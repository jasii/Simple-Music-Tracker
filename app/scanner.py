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

from . import db, gaps, scans

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


def _quality(path, audio):
    """A quality label for one audio file ('FLAC', 'MP3 320', ...), or None.

    Shown wherever an owned album's quality is: what matters is lossless vs
    lossy and, for MP3, roughly which encoder preset -- VBR bitrates map to the
    V-preset they land nearest.
    """
    ext = os.path.splitext(path)[1].lower()
    info = getattr(audio, "info", None)
    bitrate = int(getattr(info, "bitrate", 0) or 0) // 1000
    if ext == ".flac":
        return "FLAC 24bit" if getattr(info, "bits_per_sample", 16) > 16 else "FLAC"
    if ext in (".wav", ".aiff", ".aif"):
        return "WAV"
    if ext == ".ape":
        return "APE"
    if ext == ".mp3":
        mode = getattr(info, "bitrate_mode", None)
        vbr = mode is not None and getattr(mode, "name", "") in ("VBR", "ABR")
        if not vbr:
            return f"MP3 {bitrate}" if bitrate else "MP3"
        if bitrate >= 220:
            return "MP3 V0"
        if bitrate >= 170:
            return "MP3 V2"
        return "MP3"
    if ext in (".m4a", ".aac"):
        # Apple Lossless rides in the same container as AAC.
        codec = (getattr(info, "codec", "") or "").lower()
        return "ALAC" if "alac" in codec else "AAC"
    if ext == ".opus":
        return "Opus"
    if ext == ".ogg":
        return "Vorbis"
    return None


def _extract(path, prefer_album=True):
    """Return (artist_name, mbid, album, rg_mbid, quality) for an audio file.

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
            return None, None, None, None, None
    if audio is None:
        return None, None, None, None, None

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
    return name, mbid, album, rg_mbid, _quality(path, audio)


def _audio_files_in(folders):
    """How many audio files sit directly in *folders* (0 when unreadable)."""
    total = 0
    for folder in folders or ():
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        total += sum(1 for n in names
                     if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS)
    return total


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
                conn.execute(
                    "UPDATE artists SET name = ?, mbid = COALESCE(mbid, ?) WHERE id = ?",
                    (entry["name"], entry["mbid"], artist_id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO artists (name, sort_name, mbid, track_count) "
                    "VALUES (?, ?, ?, 0)",
                    (entry["name"], sort_name, entry["mbid"]),
                )
                artist_id = cur.lastrowid
            # Track count goes to this library's per-source stat; the artist's
            # total is the sum across sources. quick scan increments (deltas),
            # full sets the absolute running total.
            db.set_library_stat(conn, artist_id, "filesystem", entry["count"], increment=increment)
            db.recompute_track_count(conn, artist_id)
            # Record the albums seen for this artist as owned (filesystem source).
            for alb in entry.get("albums", {}).values():
                if increment:
                    # A quick scan saw only the new files, so the tag count is
                    # a fraction of the album; its folders hold the rest.
                    tracks = _audio_files_in(alb.get("folders", ()))
                else:
                    tracks = alb.get("tracks")
                db.mark_owned(conn, artist_id, alb["title"], alb["rg_mbid"],
                              source="filesystem", fmt=alb.get("format"),
                              tracks=tracks, tracks_at_least=increment)
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

                # Cancellation point: between files is the only safe place
                # to stop a walk that may be hundreds of thousands of them.
                scans.raise_if_stopped()

                if quick:
                    # Skip the expensive tag read for files unchanged since the
                    # last scan -- this is what makes a quick scan quick.
                    try:
                        if os.path.getmtime(path) <= last_scan:
                            continue
                    except OSError:
                        continue

                name, mbid, album, rg_mbid, quality = _extract(path, prefer_album)
                processed += 1
                # "Various Artists" and friends are a compilation credit, not
                # someone to track: skipping them here is what keeps them out
                # of the library, the release feed and everything downstream.
                if not name or db.is_non_artist(name):
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
                        alb = entry["albums"].setdefault(
                            ak, {"title": album.strip(), "rg_mbid": None, "format": None,
                                 "tracks": 0, "folders": set()})
                        if rg_mbid and not alb["rg_mbid"]:
                            alb["rg_mbid"] = rg_mbid
                        alb["format"] = gaps.better_quality(alb["format"], quality)
                        alb["tracks"] += 1
                        alb["folders"].add(root)

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
        # Quality labels and ownership just changed under it.
        gaps.invalidate()
        from . import grabber  # local: grabber imports plugins, which import us
        grabber.invalidate_library_index()
        from .plugins import notifier  # local: avoids an import cycle
        notifier.notify(
            "scan_done", f"{mode.title()} scan finished",
            f"{files_seen} files seen, {len(seen_artists)} artists.",
        )
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


# Short-lived memo of the folder shortlist per artist: resolving five tracks
# for one artist page would otherwise walk the music root five times.
_DIRS_TTL = 120
_dirs_cache = {}
_dirs_lock = threading.Lock()


def _artist_dirs(music_dir, artist_norm):
    """_matching_dirs, remembered for a couple of minutes."""
    key = (music_dir, artist_norm)
    now = time.time()
    with _dirs_lock:
        hit = _dirs_cache.get(key)
        if hit and now - hit[0] < _DIRS_TTL:
            return hit[1]
    dirs = _matching_dirs(music_dir, artist_norm)
    with _dirs_lock:
        _dirs_cache[key] = (now, dirs)
        # The app is long-lived and artists come and go; don't grow forever.
        if len(_dirs_cache) > 256:
            for stale in [k for k, v in _dirs_cache.items() if now - v[0] > _DIRS_TTL]:
                _dirs_cache.pop(stale, None)
    return dirs


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


def find_track_file(artist, title):
    """Path to the user's own file for one track, or None.

    Looks only inside the folders already associated with that artist (plus any
    folder in the music root whose name matches them) and matches on the file
    name with case, punctuation and leading track numbers ignored. Of several
    matches the tightest name wins, so "Zion" doesn't answer with
    "Zion Dub (Extended)" when the plain track sits next to it.
    """
    title_key = _norm(title)
    artist_norm = _norm(artist)
    if not title_key or not artist_norm:
        return None

    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM artists WHERE sort_name = ?",
            ((artist or "").strip().lower(),),
        ).fetchone()
    finally:
        conn.close()

    dirs = set()
    if row:
        dirs.update(f for f in db.get_artist_folders(row["id"]) if os.path.isdir(f))
    music_dir = db.get_setting("music_directory") or ""
    if music_dir and os.path.isdir(music_dir):
        dirs.update(_artist_dirs(music_dir, artist_norm))

    best = None
    best_len = None
    for directory in _top_dirs(dirs):
        for root, _sub, files in os.walk(directory):
            for fname in files:
                base, ext = os.path.splitext(fname)
                if ext.lower() not in AUDIO_EXTENSIONS:
                    continue
                key = _norm(base)
                if title_key not in key:
                    continue
                if best_len is None or len(key) < best_len:
                    best, best_len = os.path.join(root, fname), len(key)
                    if key == title_key:
                        return best
    return best


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
                name, _mbid, album, rg_mbid, quality = _extract(
                    os.path.join(root, fname), prefer_album)
                # Only count files that actually belong to this artist.
                if not name or name.lower() != sort_name or db.is_non_artist(name):
                    continue
                count += 1
                folders.add(root)
                if album:
                    ak = album.strip().lower()
                    if ak:
                        alb = albums.setdefault(
                            ak, {"title": album.strip(), "rg_mbid": None, "format": None,
                                 "tracks": 0})
                        if rg_mbid and not alb["rg_mbid"]:
                            alb["rg_mbid"] = rg_mbid
                        alb["format"] = gaps.better_quality(alb["format"], quality)
                        alb["tracks"] += 1

    # Remote library sources (Plex/Subsonic/...): look this artist up on each
    # configured source. Network calls happen here, outside the DB write lock.
    from . import plugins  # local import avoids an import cycle at module load
    remote = []  # (source_key, label, {"albums": [...], "track_count": int})
    for plug in plugins.get_plugins("library"):
        if plug.key == "filesystem":
            continue
        try:
            if not plug.configured():
                continue
            res = plug.albums_for_artist(row["name"])
        except Exception:  # noqa: BLE001 - a flaky source must not fail the scan
            res = None
        if res is not None:
            remote.append((plug.key, plug.label, res))

    with db._write_lock:
        conn = db.get_connection()
        try:
            db.set_library_stat(conn, artist_id, "filesystem", count, increment=False)
            for alb in albums.values():
                db.mark_owned(conn, artist_id, alb["title"], alb["rg_mbid"],
                              source="filesystem", fmt=alb.get("format"),
                              tracks=alb.get("tracks"))
            db.record_artist_folders(conn, artist_id, folders)
            # Replace just this artist's rows for each remote source, leaving
            # other artists' owned data on that source untouched.
            for src_key, _label, res in remote:
                conn.execute(
                    "DELETE FROM owned_albums WHERE artist_id = ? AND source = ?",
                    (artist_id, src_key),
                )
                for alb in res["albums"]:
                    db.mark_owned(conn, artist_id, alb["title"], alb.get("rg_mbid"),
                                  source=src_key, fmt=alb.get("format"),
                                  tracks=alb.get("tracks"))
                if res.get("track_count"):
                    db.set_library_stat(conn, artist_id, src_key, res["track_count"], increment=False)
            db.recompute_track_count(conn, artist_id)
            conn.commit()
        finally:
            conn.close()

    return {
        "artist_id": artist_id,
        "files": count,
        "albums": len(albums),
        "folders": len(folders),
        "sources": [
            {"key": k, "label": lbl, "albums": len(res["albums"])}
            for k, lbl, res in remote
        ],
    }
