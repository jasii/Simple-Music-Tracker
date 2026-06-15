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

from . import db, lastfm, musicbrainz, webhooks

# Work queue of artist ids plus a dedupe set so the same artist isn't queued
# many times while already pending.
_queue = queue.Queue()
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

        monitor_types = set(
            (artist["monitor_types"] or "album,ep").split(",")
        )

        # Last.fm enrichment (bio / image / url) -- best effort.
        info = lastfm.get_artist_info(artist["name"])
        if info:
            with db._write_lock:
                conn.execute(
                    "UPDATE artists SET bio = COALESCE(?, bio), "
                    "lastfm_url = COALESCE(?, lastfm_url), "
                    "image_url = COALESCE(?, image_url) WHERE id = ?",
                    (info.get("bio"), info.get("lastfm_url"),
                     info.get("image_url"), artist_id),
                )
                conn.commit()

        # MusicBrainz upcoming/recent releases for the monitored types.
        mbid, releases = musicbrainz.find_upcoming(
            artist["name"], artist["mbid"], monitor_types
        )

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

        # New releases may have landed; drop the cached discography so the artist
        # page re-pulls a fresh list next time it's opened.
        if mbid and new_releases:
            musicbrainz.invalidate_discography(mbid)

        # Fire any webhooks now due for this artist (honours the trigger timing).
        notified = process_pending_webhooks(artist_id) if fire_webhooks else 0

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
    if not (db.get_setting("webhook_url") or "").strip():
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
        ok, _msg = webhooks.fire(artist, release)
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

def _worker():
    while True:
        artist_id = _queue.get()
        try:
            with _pending_lock:
                _pending.discard(artist_id)
            result = refresh_artist(artist_id)
            with _progress_lock:
                _progress["processed"] += 1
                _progress["current"] = ""
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


def enqueue_artist(artist_id):
    """Queue one artist for refresh (deduped). Starts the worker if needed."""
    start_worker()
    with _pending_lock:
        if artist_id in _pending:
            return False
        _pending.add(artist_id)
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
