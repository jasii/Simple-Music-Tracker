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

# Tag keys grouped so the album artist is preferred over the track artist.
# (_first_tag_value also tries lower/upper-case variants of each key.)
_ALBUM_ARTIST_TAGS = ["albumartist", "album artist", "aART", "TPE2"]
_TRACK_ARTIST_TAGS = ["artist", "TPE1", "\xa9ART"]
_MBID_TAGS = [
    "musicbrainz_albumartistid",
    "musicbrainz_artistid",
    "MusicBrainz Album Artist Id",
    "MusicBrainz Artist Id",
]
_ALBUM_TAGS = ["album", "TALB", "\xa9alb"]
# Release-group id ties an owned album to a row in the artist's MusicBrainz
# discography; Picard-tagged libraries usually have it.
_RG_MBID_TAGS = ["musicbrainz_releasegroupid", "MusicBrainz Release Group Id"]

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


def _extract(path, prefer_album=True):
    """Return (artist_name, mbid, album, rg_mbid) for an audio file, or all None.

    When *prefer_album* the album-artist tag is used and the track artist is
    only a fallback (so e.g. compilations stay under one album artist); set it
    False to prefer the per-track artist instead.
    """
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        audio = None
    if audio is None:
        try:
            audio = MutagenFile(path)
        except Exception:
            return None, None, None, None
    if audio is None:
        return None, None, None, None

    tags = getattr(audio, "tags", None) or audio
    album_artist = _first_tag_value(tags, _ALBUM_ARTIST_TAGS)
    track_artist = _first_tag_value(tags, _TRACK_ARTIST_TAGS)
    if prefer_album:
        name = album_artist or track_artist
    else:
        name = track_artist or album_artist
    mbid = _first_tag_value(tags, _MBID_TAGS)
    album = _first_tag_value(tags, _ALBUM_TAGS)
    rg_mbid = _first_tag_value(tags, _RG_MBID_TAGS)
    return name, mbid, album, rg_mbid


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
                artist_id = existing["id"]
                if increment:
                    conn.execute(
                        "UPDATE artists SET name = ?, "
                        "track_count = track_count + ?, "
                        "mbid = COALESCE(mbid, ?) WHERE id = ?",
                        (entry["name"], entry["count"], entry["mbid"], artist_id),
                    )
                else:
                    conn.execute(
                        "UPDATE artists SET name = ?, track_count = ?, "
                        "mbid = COALESCE(mbid, ?) WHERE id = ?",
                        (entry["name"], entry["count"], entry["mbid"], artist_id),
                    )
            else:
                cur = conn.execute(
                    "INSERT INTO artists (name, sort_name, mbid, track_count) "
                    "VALUES (?, ?, ?, ?)",
                    (entry["name"], sort_name, entry["mbid"], entry["count"]),
                )
                artist_id = cur.lastrowid
            # Record the albums seen for this artist as owned (scan source).
            for alb in entry.get("albums", {}).values():
                db.mark_owned(conn, artist_id, alb["title"], alb["rg_mbid"], source="scan")
            # Remember the folders so a per-artist rescan can target them.
            db.record_artist_folders(conn, artist_id, entry.get("folders", ()))
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

        prefer_album = (db.get_setting("prefer_album_artist") or "true") != "false"

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

                name, mbid, album, rg_mbid = _extract(path, prefer_album)
                processed += 1
                if not name:
                    continue

                key = name.lower()
                seen_artists.add(key)
                entry = running.setdefault(key, {"name": name, "count": 0, "mbid": None, "albums": {}, "folders": set()})
                entry["count"] += 1
                entry["folders"].add(root)
                if mbid and not entry["mbid"]:
                    entry["mbid"] = mbid
                if album:
                    ak = album.strip().lower()
                    if ak:
                        alb = entry["albums"].setdefault(ak, {"title": album.strip(), "rg_mbid": None})
                        if rg_mbid and not alb["rg_mbid"]:
                            alb["rg_mbid"] = rg_mbid

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


# --- per-artist rescan ------------------------------------------------------

def _norm(s):
    """Lowercase, alphanumerics only -- robust folder-name matching."""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _top_dirs(paths):
    """Drop any path nested under another in the set (avoid double-walking)."""
    out = []
    for p in sorted(os.path.abspath(p) for p in paths):
        if not any(p == q or p.startswith(q + os.sep) for q in out):
            out.append(p)
    return out


def _matching_dirs(music_dir, artist_norm, max_depth=2):
    """Folders within *max_depth* of the music root whose name contains the
    artist (handles 'Artist - Year - Album' and 'Artist/Album' layouts)."""
    base = os.path.abspath(music_dir)
    out = []
    for root, dirs, _files in os.walk(base):
        depth = root[len(base):].count(os.sep)
        if depth >= max_depth:
            dirs[:] = []  # don't descend further; names only, no tag reads
        for d in dirs:
            if artist_norm and artist_norm in _norm(d):
                out.append(os.path.join(root, d))
    return out


def scan_artist(artist_id):
    """Rescan just one artist: walk their remembered folders plus any folders in
    the music root whose name matches the artist. Updates track_count, owned
    albums and the folder list. Returns a summary dict.
    """
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, sort_name FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"error": "artist not found"}

    sort_name = row["sort_name"]
    prefer_album = (db.get_setting("prefer_album_artist") or "true") != "false"
    artist_norm = _norm(row["name"])

    dirs = {f for f in db.get_artist_folders(artist_id) if os.path.isdir(f)}
    music_dir = db.get_setting("music_directory") or ""
    if music_dir and os.path.isdir(music_dir):
        dirs.update(_matching_dirs(music_dir, artist_norm))

    count = 0
    albums = {}
    folders = set()
    for d in _top_dirs(dirs):
        for root, _sub, files in os.walk(d):
            for fname in files:
                if os.path.splitext(fname)[1].lower() not in AUDIO_EXTENSIONS:
                    continue
                name, _mbid, album, rg_mbid = _extract(os.path.join(root, fname), prefer_album)
                # Only count files that actually belong to this artist.
                if not name or name.lower() != sort_name:
                    continue
                count += 1
                folders.add(root)
                if album:
                    ak = album.strip().lower()
                    if ak:
                        alb = albums.setdefault(ak, {"title": album.strip(), "rg_mbid": None})
                        if rg_mbid and not alb["rg_mbid"]:
                            alb["rg_mbid"] = rg_mbid

    with db._write_lock:
        conn = db.get_connection()
        try:
            conn.execute(
                "UPDATE artists SET track_count = ? WHERE id = ?", (count, artist_id)
            )
            for alb in albums.values():
                db.mark_owned(conn, artist_id, alb["title"], alb["rg_mbid"], source="scan")
            db.record_artist_folders(conn, artist_id, folders)
            conn.commit()
        finally:
            conn.close()

    return {"artist_id": artist_id, "files": count, "albums": len(albums), "folders": len(folders)}
