"""Refresh artist metadata and upcoming releases for subscribed artists.

Ties together MusicBrainz (upcoming albums), Last.fm (artist info) and the
webhook delivery. Only artists with subscription in ('subscribed', 'notify')
are refreshed.

All refresh work flows through a single background worker draining a queue.
This guarantees only one artist is processed at a time -- so no matter how many
artists you bulk-subscribe, external APIs are hit serially and stay within the
rate limits enforced in the musicbrainz module. (Spawning a thread per artist
would otherwise let thousands of requests fan out at once and get blocked.)
"""

import queue
import threading
import time

from . import album as album_detail, artwork, db, metadata, musicbrainz, webhooks


class _RefreshQueue(queue.Queue):
    """FIFO refresh queue with a jump-the-line put for clicked artists.

    A name someone just clicked is waited on by a page, so it goes to the front
    instead of behind however many artists a bulk pass has queued. Mirrors
    Queue.put's bookkeeping (the stdlib's own LifoQueue/PriorityQueue extend the
    class the same way).
    """

    def put_front(self, item):
        with self.not_full:
            self.queue.appendleft(item)
            self.unfinished_tasks += 1
            self.not_empty.notify()


# Work queue of artist ids plus a dedupe set so the same artist isn't queued
# many times while already pending.
_queue = _RefreshQueue()
_pending = set()
_pending_lock = threading.Lock()
_worker_started = False

# Progress for the UI: cumulative processed count and current/last artist.
# `current_started` stamps when the in-flight artist began, so the UI can show
# how long the worker has been on it - the tell for "stuck" vs merely "slow".
_progress_lock = threading.Lock()
_progress = {"processed": 0, "queued": 0, "current": "", "current_started": 0.0,
             "message": ""}


def get_refresh_state():
    with _progress_lock:
        state = dict(_progress)
    state["queued"] = _queue.qsize()
    state["running"] = state["queued"] > 0 or bool(state["current"])
    started = state.pop("current_started", 0.0)
    state["elapsed"] = int(time.time() - started) if state["current"] and started else 0
    return state


def _set_progress(**kwargs):
    with _progress_lock:
        _progress.update(kwargs)


# Bounds how many background art warmers run at once so a bulk refresh can't
# fan out hundreds of image downloads.
_warm_slots = threading.Semaphore(4)


def _warm_art(urls):
    """Fetch images into the on-disk cache so pages render them instantly.

    Fire-and-forget from a refresh: already-cached and negative-cached URLs
    return immediately, so repeat warming is nearly free.
    """
    if (db.get_setting("cache_images") or "true") == "false":
        return
    with _warm_slots:
        for url in urls:
            if url:
                artwork.fetch(url)


def warm_art_async(urls):
    urls = [u for u in urls if u]
    if urls:
        threading.Thread(target=_warm_art, args=(urls,), daemon=True).start()


def _discography_max_age():
    """How stale a cached discography may be for a refresh.

    A refresh looks for releases the user hasn't been told about yet, so its
    data has to be at least as fresh as the refresh cadence itself -- but no
    fresher, otherwise every cycle re-walks MusicBrainz for artists that put
    nothing out.
    """
    try:
        hours = max(float(db.get_setting("check_interval_hours") or 12), 0.25)
    except (TypeError, ValueError):
        hours = 12
    return hours * 3600


def refresh_artist(artist_id, fire_webhooks=True):
    """Refresh a single artist. Returns dict with counts and any error."""
    conn = db.get_connection()
    try:
        artist = conn.execute(
            "SELECT * FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
        if artist is None:
            return {"error": "artist not found"}

        # Mark this artist in-flight so the status shows who we're on and for how
        # long (a job that never clears is the stuck one).
        _set_progress(current=artist["name"], current_started=time.time())

        monitor_types = db.monitored_types(artist["monitor_types"])

        # Enrichment (bio / image / url) from the metadata sources, in the
        # user's order -- best effort.
        info = metadata.artist_info(artist["name"], artist_id=artist_id)
        if info:
            with db._write_lock:
                conn.execute(
                    "UPDATE artists SET bio = COALESCE(?, bio), "
                    "lastfm_url = COALESCE(?, lastfm_url), "
                    # A hand-picked image outranks whatever Last.fm has.
                    "image_url = CASE WHEN image_locked = 1 THEN image_url "
                    "ELSE COALESCE(?, image_url) END WHERE id = ?",
                    (info.get("bio"), info.get("url"),
                     info.get("image_url"), artist_id),
                )
                conn.commit()

        # MusicBrainz upcoming/recent releases for the monitored types. The
        # lookup caches the artist's full release-group list, which the
        # discography sync below then reads for free.
        disco_max_age = _discography_max_age()
        if monitor_types:
            mbid, releases = musicbrainz.find_upcoming(
                artist["name"], artist["mbid"], monitor_types, max_age=disco_max_age
            )
        else:
            # Monitoring nothing: keep the artist's own details fresh, but
            # don't go looking for releases they asked not to hear about.
            mbid, releases = artist["mbid"], []

        new_releases = []
        with db._write_lock:
            if mbid and mbid != artist["mbid"]:
                conn.execute(
                    "UPDATE artists SET mbid = ? WHERE id = ?", (mbid, artist_id)
                )
            for rel in releases:
                existing = conn.execute(
                    "SELECT id FROM releases WHERE artist_id = ? AND mbid = ?",
                    (artist_id, rel["mbid"]),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE releases SET title = ?, release_date = ?, "
                        "primary_type = ?, image_url = ? WHERE id = ?",
                        (rel["title"], rel["release_date"], rel["primary_type"],
                         rel["image_url"], existing["id"]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO releases (artist_id, mbid, title, release_date, "
                        "primary_type, image_url) VALUES (?, ?, ?, ?, ?, ?)",
                        (artist_id, rel["mbid"], rel["title"], rel["release_date"],
                         rel["primary_type"], rel["image_url"]),
                    )
                    new_releases.append(rel)
            conn.execute(
                "UPDATE artists SET last_checked = datetime('now') WHERE id = ?",
                (artist_id,),
            )
            conn.commit()

        # Warm the art cache in the background: the artist image plus every
        # release cover just saved. Covers that don't exist upstream get a
        # negative-cache marker now, so pages show the placeholder instantly
        # instead of waiting out a slow Cover Art Archive 404 on first view.
        warm_art_async(
            [(info or {}).get("image_url") or artist["image_url"]]
            + [rel["image_url"] for rel in releases]
        )

        # Sync the per-type discography totals + owned subset so the Artists list
        # shows owned/total ("missing") without anyone opening the artist page.
        # Best effort: a discography fetch failure must not fail the refresh.
        if mbid:
            try:
                # Already cached by find_upcoming above (same browse request),
                # so this costs nothing and includes any new release.
                items = musicbrainz.fetch_discography(mbid, max_age=disco_max_age)
                groups, owned = album_detail.group_discography(artist_id, items)
                db.set_discography_counts(
                    artist_id, len(groups["album"]), len(groups["ep"]), len(groups["single"])
                )
                db.set_owned_counts(
                    artist_id, owned["album"], owned["ep"], owned["single"]
                )
            except Exception:  # noqa: BLE001 - counts are non-critical
                pass

        # Fire any webhooks now due for this artist (honours the trigger timing).
        notified = process_pending_webhooks(artist_id) if fire_webhooks else 0

        # A release that just landed is exactly what automatic grabbing is for,
        # so don't wait for the next scheduled pass to notice it.
        if new_releases:
            from . import grabber  # local: grabber imports plugins, which import us
            try:
                grabber.run_pass()
            except Exception:  # noqa: BLE001 - a grab failure can't fail a refresh
                pass

        return {
            "artist": artist["name"],
            "releases": len(releases),
            "new": len(new_releases),
            "notified": notified,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    finally:
        _set_progress(current="", current_started=0.0)
        conn.close()


# --- webhook delivery timing ------------------------------------------------

_UNIT_SECONDS = {"hours": 3600, "days": 86400, "weeks": 604800}


def _normalize_date(value):
    """Expand a partial 'YYYY'/'YYYY-MM' date to a date object, else None."""
    if not value:
        return None
    parts = value.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        from datetime import date
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


def _lead_seconds():
    try:
        value = float(db.get_setting("webhook_lead_value") or 0)
    except (TypeError, ValueError):
        value = 0
    unit = db.get_setting("webhook_lead_unit") or "days"
    return value * _UNIT_SECONDS.get(unit, 86400)


def process_pending_webhooks(artist_id=None):
    """Fire due 'notify' webhooks for un-notified releases. Returns count sent.

    'discovery' mode fires as soon as a release is stored; 'before_release'
    mode waits until the configured lead time before the release date.
    """
    from .plugins import notifier  # local: avoids an import cycle at module load

    webhook_url = (db.get_setting("webhook_url") or "").strip()
    # The webhook isn't the only channel any more: notifier plugins subscribe to
    # the same releases, so the pass still runs when only they are configured.
    channels = bool(webhook_url) or bool(
        notifier.subscribers("release_found") or notifier.subscribers("release_day")
    )
    if not channels:
        return 0
    mode = db.get_setting("webhook_trigger") or "discovery"
    lead = _lead_seconds()
    import time as _time
    now = _time.time()

    conn = db.get_connection()
    try:
        sql = (
            "SELECT r.id AS rid, r.mbid, r.title, r.release_date, r.primary_type, "
            "r.image_url, a.id AS artist_id, a.name AS name "
            "FROM releases r JOIN artists a ON a.id = r.artist_id "
            "WHERE a.subscription = 'notify' AND r.notified = 0"
        )
        params = []
        if artist_id is not None:
            sql += " AND a.id = ?"
            params.append(artist_id)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    sent = 0
    for row in rows:
        if mode == "before_release":
            rd = _normalize_date(row["release_date"])
            if rd is None:
                continue  # can't time it without a date; wait until one is known
            from datetime import datetime
            release_ts = datetime(rd.year, rd.month, rd.day).timestamp()
            if now < release_ts - lead:
                continue  # not within the lead window yet

        artist = {"name": row["name"]}
        release = {
            "title": row["title"],
            "release_date": row["release_date"],
            "primary_type": row["primary_type"],
            "image_url": row["image_url"],
        }
        ok = True
        if webhook_url:
            ok, _msg = webhooks.fire(artist, release)
        # 'before_release' mode only gets this far inside the lead window, so
        # that is the release-day event; otherwise it's a fresh discovery.
        event = "release_day" if mode == "before_release" else "release_found"
        when = row["release_date"] or "date TBA"
        sent_to = notifier.notify(
            event,
            f"{row['name']} - {row['title']}",
            f"{row['primary_type'] or 'Release'} out {when}.",
        )
        ok = ok or bool(sent_to)
        if ok:
            with db._write_lock:
                wconn = db.get_connection()
                try:
                    wconn.execute(
                        "UPDATE releases SET notified = 1 WHERE id = ?", (row["rid"],)
                    )
                    wconn.commit()
                finally:
                    wconn.close()
            sent += 1
    return sent


# --- single-worker queue ----------------------------------------------------

# Hard ceiling for one artist's refresh. A hung external request (MusicBrainz
# occasionally trickles a response that defeats the socket timeout) would
# otherwise freeze the whole serial queue. Past this we abandon that artist
# (the stuck thread is left to die on its own) and move to the next one.
def _job_timeout():
    try:
        return max(float(db.get_setting("artist_refresh_timeout") or 180), 30)
    except (TypeError, ValueError):
        return 180


def _worker():
    while True:
        artist_id = _queue.get()
        try:
            with _pending_lock:
                _pending.discard(artist_id)

            # Run the refresh in a helper thread so we can bound how long it may
            # take; join() returns early if it finishes, or on timeout.
            box = {}
            job = threading.Thread(
                target=lambda: box.__setitem__("result", refresh_artist(artist_id)),
                daemon=True,
            )
            job.start()
            job.join(_job_timeout())

            with _progress_lock:
                _progress["processed"] += 1
                _progress["current"] = ""
                _progress["current_started"] = 0.0
                if job.is_alive():
                    _progress["message"] = "timed out (skipped)"
                else:
                    result = box.get("result", {})
                    _progress["message"] = result.get("artist", result.get("error", ""))
        except Exception:  # noqa: BLE001 - keep the worker alive no matter what
            pass
        finally:
            _queue.task_done()


def start_worker():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=_worker, daemon=True).start()


def enqueue_artist(artist_id, front=False):
    """Queue one artist for refresh (deduped). Starts the worker if needed.

    *front* puts them next in line: used when someone is sitting on that
    artist's page waiting for it to fill in.
    """
    start_worker()
    with _pending_lock:
        if artist_id in _pending:
            return False
        _pending.add(artist_id)
    if front:
        _queue.put_front(artist_id)
    else:
        _queue.put(artist_id)
    return True


def enqueue_all_subscribed(stale_only=False):
    """Queue followed artists for refresh, oldest-checked first.

    Ordering by `last_checked` (never-checked first, then most stale) means a
    restart resumes where it left off instead of re-syncing the same artists in
    alphabetical order every boot. When *stale_only* is set (the scheduled/boot
    pass), artists checked within `check_interval_hours` are skipped, so a reboot
    doesn't redo work that was just done. Returns how many were queued.
    """
    sql = (
        "SELECT id FROM artists WHERE subscription IN ('subscribed', 'notify')"
    )
    params = []
    if stale_only:
        try:
            hours = max(float(db.get_setting("check_interval_hours") or 12), 0)
        except (TypeError, ValueError):
            hours = 12
        sql += " AND (last_checked IS NULL OR last_checked <= datetime('now', ?))"
        params.append(f"-{hours} hours")
    # NULLs (never checked) first, then the oldest last_checked.
    sql += " ORDER BY last_checked IS NULL DESC, last_checked ASC"

    conn = db.get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    queued = 0
    for row in rows:
        if enqueue_artist(row["id"]):
            queued += 1
    return queued
