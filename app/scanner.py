"""Scan a music directory and build the list of artists.

Reads tags from audio files using mutagen. Only local filesystem work happens
here -- no network calls -- so scanning a large library stays fast.

Two modes:
- full  : read every audio file and (re)compute artist track counts.
- quick : only read files whose modification time is newer than the previous
          scan, so adding a few albums syncs almost instantly instead of
          re-reading the whole library.

In both modes artists are written to the database in batches *during* the walk
(not only at the end), so they appear in the UI -- and can be subscribed to --
while the scan is still running.
"""

import os
import threading
import time

from mutagen import File as MutagenFile

from . import db

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus",
    ".wma", ".wav", ".aiff", ".aif", ".ape", ".mpc",
}

# Tag keys that may hold the artist name across the various container formats.
_ARTIST_TAGS = ["albumartist", "album artist", "artist", "TPE2", "TPE1", "\xa9ART", "aART"]
_MBID_TAGS = [
    "musicbrainz_albumartistid",
    "musicbrainz_artistid",
    "MusicBrainz Album Artist Id",
    "MusicBrainz Artist Id",
]

# Flush newly discovered artists to the database this often (in audio files
# processed) so they show up in the UI promptly during a long scan.
FLUSH_EVERY_FILES = 300

# Module level state so the UI can poll progress of an in-flight scan.
_scan_lock = threading.Lock()
_scan_state = {
    "running": False,
    "mode": "full",
    "files_seen": 0,
    "artists_found": 0,
    "message": "",
}


def get_scan_state():
    with _scan_lock:
        return dict(_scan_state)


def _set_scan_state(**kwargs):
    with _scan_lock:
        _scan_state.update(kwargs)


def _first_tag_value(tags, keys):
    if not tags:
        return None
    for key in keys:
        # mutagen tag objects behave like dicts but key casing differs by format.
        for candidate in (key, key.lower(), key.upper()):
            try:
                if candidate in tags:
                    value = tags[candidate]
                    if isinstance(value, list):
                        value = value[0] if value else None
                    if value:
                        return str(value).strip()
            except (KeyError, TypeError):
                continue
    return None


def _extract(path):
    """Return (artist_name, mbid) for an audio file, or (None, None)."""
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        audio = None
    if audio is None:
        try:
            audio = MutagenFile(path)
        except Exception:
            return None, None
    if audio is None:
        return None, None

    tags = getattr(audio, "tags", None) or audio
    name = _first_tag_value(tags, _ARTIST_TAGS)
    mbid = _first_tag_value(tags, _MBID_TAGS)
    return name, mbid


def _flush(conn, batch, increment):
    """Write a batch of {sort_name: {name, count, mbid}} to the database.

    *increment* True adds to existing track counts (quick scan, where we only
    saw the newly added files); False sets the count to the absolute running
    total (full scan). Existing subscription/ignore/mbid state is preserved.
    The batch is cleared once written.
    """
    if not batch:
        return
    with db._write_lock:
        for entry in batch.values():
            sort_name = entry["name"].lower()
            existing = conn.execute(
                "SELECT id FROM artists WHERE sort_name = ?", (sort_name,)
            ).fetchone()
            if existing:
                if increment:
                    conn.execute(
                        "UPDATE artists SET name = ?, "
                        "track_count = track_count + ?, "
                        "mbid = COALESCE(mbid, ?) WHERE id = ?",
                        (entry["name"], entry["count"], entry["mbid"], existing["id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE artists SET name = ?, track_count = ?, "
                        "mbid = COALESCE(mbid, ?) WHERE id = ?",
                        (entry["name"], entry["count"], entry["mbid"], existing["id"]),
                    )
            else:
                conn.execute(
                    "INSERT INTO artists (name, sort_name, mbid, track_count) "
                    "VALUES (?, ?, ?, ?)",
                    (entry["name"], sort_name, entry["mbid"], entry["count"]),
                )
        conn.commit()
    if increment:
        # Deltas are now persisted; start accumulating fresh ones.
        batch.clear()


def scan_directory(directory, quick=False):
    """Walk *directory*, collect artists, and upsert them into the database.

    Returns a summary dict. Designed to be called from a background thread.
    """
    if get_scan_state().get("running"):
        return {"error": "scan already running"}

    mode = "quick" if quick else "full"
    _set_scan_state(running=True, mode=mode, files_seen=0, artists_found=0,
                    message="starting")

    conn = db.get_connection()
    log_id = None
    start_time = time.time()
    try:
        cur = conn.execute(
            "INSERT INTO scan_log (status, message) VALUES ('running', ?)",
            (f"{mode} scan {directory}",),
        )
        log_id = cur.lastrowid
        conn.commit()

        if not directory or not os.path.isdir(directory):
            raise FileNotFoundError(f"music directory not found: {directory!r}")

        last_scan = 0.0
        if quick:
            try:
                last_scan = float(db.get_setting("last_scan_time") or 0)
            except (TypeError, ValueError):
                last_scan = 0.0

        # full: absolute running totals; quick: unflushed deltas + seen set.
        running = {}
        seen_artists = set()
        files_seen = 0
        processed = 0
        since_flush = 0

        for root, _dirs, files in os.walk(directory):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue
                files_seen += 1
                path = os.path.join(root, fname)

                if quick:
                    # Skip the expensive tag read for files unchanged since the
                    # last scan -- this is what makes a quick scan quick.
                    try:
                        if os.path.getmtime(path) <= last_scan:
                            continue
                    except OSError:
                        continue

                name, mbid = _extract(path)
                processed += 1
                if not name:
                    continue

                key = name.lower()
                seen_artists.add(key)
                entry = running.setdefault(key, {"name": name, "count": 0, "mbid": None})
                entry["count"] += 1
                if mbid and not entry["mbid"]:
                    entry["mbid"] = mbid

                since_flush += 1
                if since_flush >= FLUSH_EVERY_FILES:
                    _flush(conn, running, increment=quick)
                    since_flush = 0
                    _set_scan_state(files_seen=files_seen,
                                    artists_found=len(seen_artists),
                                    message=f"scanning {root}")
                elif files_seen % 200 == 0:
                    _set_scan_state(files_seen=files_seen,
                                    artists_found=len(seen_artists),
                                    message=f"scanning {root}")

        # Final flush of whatever is left.
        _flush(conn, running, increment=quick)

        # Record when this scan started so the next quick scan only looks at
        # files added/changed afterwards.
        db.set_setting("last_scan_time", repr(start_time))

        with db._write_lock:
            conn.execute(
                "UPDATE scan_log SET finished_at = datetime('now'), status = 'done', "
                "files_seen = ?, artists_found = ?, message = ? WHERE id = ?",
                (files_seen, len(seen_artists), f"{mode} scan complete", log_id),
            )
            conn.commit()

        summary = {
            "mode": mode,
            "files_seen": files_seen,
            "processed": processed,
            "artists_found": len(seen_artists),
        }
        _set_scan_state(running=False, files_seen=files_seen,
                        artists_found=len(seen_artists), message="done")
        return summary

    except Exception as exc:  # noqa: BLE001 - report any failure to the UI
        if log_id is not None:
            try:
                conn.execute(
                    "UPDATE scan_log SET finished_at = datetime('now'), "
                    "status = 'error', message = ? WHERE id = ?",
                    (str(exc), log_id),
                )
                conn.commit()
            except Exception:
                pass
        _set_scan_state(running=False, message=f"error: {exc}")
        return {"error": str(exc)}
    finally:
        conn.close()


def scan_in_background(directory, quick=False):
    thread = threading.Thread(
        target=scan_directory, args=(directory,), kwargs={"quick": quick}, daemon=True
    )
    thread.start()
    return thread
