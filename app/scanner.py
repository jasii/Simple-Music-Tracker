"""Scan a music directory and build the list of artists.

Reads tags from audio files using mutagen. Only local filesystem work happens
here -- no network calls -- so scanning a large library stays fast.
"""

import os
import threading

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

# Module level state so the UI can poll progress of an in-flight scan.
_scan_lock = threading.Lock()
_scan_state = {"running": False, "files_seen": 0, "artists_found": 0, "message": ""}


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


def scan_directory(directory):
    """Walk *directory*, collect artists, and upsert them into the database.

    Returns a summary dict. Designed to be called from a background thread.
    """
    if get_scan_state().get("running"):
        return {"error": "scan already running"}

    _set_scan_state(running=True, files_seen=0, artists_found=0, message="starting")

    conn = db.get_connection()
    log_id = None
    try:
        cur = conn.execute(
            "INSERT INTO scan_log (status, message) VALUES ('running', ?)",
            (f"scanning {directory}",),
        )
        log_id = cur.lastrowid
        conn.commit()

        if not directory or not os.path.isdir(directory):
            raise FileNotFoundError(f"music directory not found: {directory!r}")

        # name(lowercased) -> {"name": display, "count": n, "mbid": mbid}
        artists = {}
        files_seen = 0

        for root, _dirs, files in os.walk(directory):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue
                files_seen += 1
                if files_seen % 200 == 0:
                    _set_scan_state(
                        files_seen=files_seen,
                        artists_found=len(artists),
                        message=f"scanning {root}",
                    )
                name, mbid = _extract(os.path.join(root, fname))
                if not name:
                    continue
                key = name.lower()
                entry = artists.setdefault(key, {"name": name, "count": 0, "mbid": None})
                entry["count"] += 1
                if mbid and not entry["mbid"]:
                    entry["mbid"] = mbid

        _set_scan_state(
            files_seen=files_seen,
            artists_found=len(artists),
            message="writing database",
        )

        # Upsert artists. Preserve existing subscription state and metadata.
        with db._write_lock:
            for entry in artists.values():
                sort_name = entry["name"].lower()
                existing = conn.execute(
                    "SELECT id, mbid FROM artists WHERE sort_name = ?", (sort_name,)
                ).fetchone()
                if existing:
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
            conn.execute(
                "UPDATE scan_log SET finished_at = datetime('now'), status = 'done', "
                "files_seen = ?, artists_found = ?, message = ? WHERE id = ?",
                (files_seen, len(artists), "scan complete", log_id),
            )
            conn.commit()

        summary = {"files_seen": files_seen, "artists_found": len(artists)}
        _set_scan_state(running=False, message="done")
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


def scan_in_background(directory):
    thread = threading.Thread(target=scan_directory, args=(directory,), daemon=True)
    thread.start()
    return thread
