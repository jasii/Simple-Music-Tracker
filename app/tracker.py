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

from . import db, lastfm, musicbrainz, webhooks

# Work queue of artist ids plus a dedupe set so the same artist isn't queued
# many times while already pending.
_queue = queue.Queue()
_pending = set()
_pending_lock = threading.Lock()
_worker_started = False

# Progress for the UI: cumulative processed count and current/last artist.
_progress_lock = threading.Lock()
_progress = {"processed": 0, "queued": 0, "current": "", "message": ""}


def get_refresh_state():
    with _progress_lock:
        state = dict(_progress)
    state["queued"] = _queue.qsize()
    state["running"] = state["queued"] > 0 or bool(state["current"])
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

        # Fire webhooks for genuinely new releases if the user wants notifications.
        notified = 0
        if fire_webhooks and artist["subscription"] == "notify":
            for rel in new_releases:
                ok, _msg = webhooks.fire(artist, rel)
                if ok:
                    with db._write_lock:
                        conn.execute(
                            "UPDATE releases SET notified = 1 "
                            "WHERE artist_id = ? AND mbid = ?",
                            (artist_id, rel["mbid"]),
                        )
                        conn.commit()
                    notified += 1

        return {
            "artist": artist["name"],
            "releases": len(releases),
            "new": len(new_releases),
            "notified": notified,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    finally:
        conn.close()


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


def enqueue_all_subscribed():
    """Queue every followed artist for refresh. Returns how many were queued."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT id FROM artists WHERE subscription IN ('subscribed', 'notify') "
            "ORDER BY sort_name"
        ).fetchall()
    finally:
        conn.close()
    queued = 0
    for row in rows:
        if enqueue_artist(row["id"]):
            queued += 1
    return queued
