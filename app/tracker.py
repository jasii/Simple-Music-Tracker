"""Refresh artist metadata and upcoming releases for subscribed artists.

Ties together MusicBrainz (upcoming albums), Last.fm (artist info) and the
webhook delivery. Only artists with subscription in ('subscribed', 'notify')
are refreshed, keeping API usage proportional to what the user follows.
"""

import threading
import time

from . import db, lastfm, musicbrainz, webhooks

# Tracks the state of an in-flight refresh so the UI can show progress.
_refresh_lock = threading.Lock()
_refresh_state = {"running": False, "done": 0, "total": 0, "message": ""}


def get_refresh_state():
    with _refresh_lock:
        return dict(_refresh_state)


def _set_refresh_state(**kwargs):
    with _refresh_lock:
        _refresh_state.update(kwargs)


def refresh_artist(artist_id, fire_webhooks=True):
    """Refresh a single artist. Returns dict with counts and any error."""
    conn = db.get_connection()
    try:
        artist = conn.execute(
            "SELECT * FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
        if artist is None:
            return {"error": "artist not found"}

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

        # MusicBrainz upcoming/recent releases.
        mbid, releases = musicbrainz.find_upcoming(artist["name"], artist["mbid"])

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


def refresh_all_subscribed():
    """Refresh every subscribed artist. Long running; run in a thread."""
    if get_refresh_state().get("running"):
        return {"error": "refresh already running"}

    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT id FROM artists WHERE subscription IN ('subscribed', 'notify') "
            "ORDER BY sort_name"
        ).fetchall()
    finally:
        conn.close()

    ids = [r["id"] for r in rows]
    _set_refresh_state(running=True, done=0, total=len(ids), message="starting")

    summary = {"checked": 0, "new": 0, "notified": 0, "errors": 0}
    for i, artist_id in enumerate(ids, start=1):
        result = refresh_artist(artist_id)
        if "error" in result:
            summary["errors"] += 1
        else:
            summary["checked"] += 1
            summary["new"] += result.get("new", 0)
            summary["notified"] += result.get("notified", 0)
        _set_refresh_state(done=i, total=len(ids),
                           message=result.get("artist", result.get("error", "")))

    _set_refresh_state(running=False, message="done")
    return summary


def refresh_artist_in_background(artist_id):
    thread = threading.Thread(
        target=refresh_artist, args=(artist_id,), daemon=True
    )
    thread.start()
    return thread


def refresh_all_in_background():
    thread = threading.Thread(target=refresh_all_subscribed, daemon=True)
    thread.start()
    return thread
