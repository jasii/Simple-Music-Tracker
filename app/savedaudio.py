"""Preview audio kept on disk, so a sample found once plays for good.

A catalogue's link doesn't last -- Deezer signs each one for a quarter of an
hour, iTunes rate-limits the searches that find them, and a blog's file can
vanish -- so the pre-load job (app/prewarm.py) saves the audio of every sample
it resolves, and a sample played in the player is saved behind it. Played again,
it comes off the disk.

Files are named for what they are -- previews/<Artist>/<Artist> - <Title>.mp3 --
so the folder is just as useful opened in a file manager or any music player.
The saved_audio table indexes them; a file deleted by hand is noticed and its
row dropped. Past the size cap in settings, the least recently played go first.
"""

import os
import re
import threading
import time

import requests
from flask import send_file

from . import db, preview, ratelimit

_DIR_NAME = "previews"
_MIME = {"mp3": "audio/mpeg", "m4a": "audio/mp4"}
_EXT_BY_TYPE = {
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mpeg3": "mp3",
    "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/m4a": "m4a", "audio/aac": "m4a",
}
# A thirty-second sample is about a megabyte and a blog's whole song a few;
# anything far past that isn't what was asked for.
_MAX_BYTES = 60 * 1024 * 1024
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
# Characters no filesystem the data folder might sit on will take.
_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')

# Keys being fetched right now, so a play and the pre-load don't both fetch.
_busy = set()
_busy_lock = threading.Lock()


def enabled():
    return (db.get_setting("save_preview_audio") or "true").strip().lower() == "true"


def _cap_bytes():
    """The folder's size cap in bytes, or None for no cap (0 in settings)."""
    try:
        mb = float(db.get_setting("saved_audio_cap_mb") or 5000)
    except (TypeError, ValueError):
        mb = 5000
    return int(mb * 1024 * 1024) if mb > 0 else None


def base_dir():
    """Where the files live: beside the database, like the artwork cache."""
    path = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), _DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _key(artist, title):
    return f"{(artist or '').strip().lower()}|{(title or '').strip().lower()}"


def _hypem_key(item_id):
    return f"hypem:{item_id}"


def _safe(name, limit=80):
    """A name as a file or folder name: unsafe characters out, not too long."""
    cleaned = _UNSAFE.sub(" ", name or "").strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)[:limit].strip()
    return cleaned or "Unknown"


def _row(key):
    """The saved file for *key* as a dict with its absolute path, or None.

    A file deleted from the folder by hand takes its row with it.
    """
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT * FROM saved_audio WHERE audio_key = ?", (key,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    found = dict(row)
    found["abs_path"] = os.path.join(base_dir(), found["path"])
    if not os.path.isfile(found["abs_path"]):
        _forget([key])
        return None
    return found


def _forget(keys):
    with db._write_lock:
        conn = db.get_connection()
        try:
            conn.executemany("DELETE FROM saved_audio WHERE audio_key = ?",
                             [(k,) for k in keys])
            conn.commit()
        finally:
            conn.close()


def find(artist, title):
    """The saved sample for one track, or None."""
    if not artist or not title:
        return None
    return _row(_key(artist, title))


def find_hypem(item_id):
    """A saved Hype Machine song, or None."""
    return _row(_hypem_key(item_id)) if item_id else None


def respond(saved):
    """Serve a saved file (Range and all), and note that it was played."""
    with db._write_lock:
        conn = db.get_connection()
        try:
            conn.execute("UPDATE saved_audio SET played_at = ? WHERE audio_key = ?",
                         (time.time(), saved["audio_key"]))
            conn.commit()
        finally:
            conn.close()
    ext = saved["path"].rsplit(".", 1)[-1].lower()
    return send_file(saved["abs_path"], mimetype=_MIME.get(ext, "audio/mpeg"),
                     conditional=True)


def save(artist, title, album=None):
    """Save one track's sample if it has one. True when a new file was written."""
    if not enabled() or not artist or not title:
        return False
    key = _key(artist, title)
    if _row(key):
        return False
    url = preview.for_track(artist, title)
    if not url:
        return False
    # Read after for_track, which re-signs a lapsed Deezer link in the cache.
    details = preview.cached_details(artist, title) or {}
    return _download(key, url, artist=artist, title=title, album=album,
                     source=details.get("source"), label=details.get("label"),
                     icon=details.get("icon"), page_url=details.get("page"))


def save_hypem(item_id, artist, title, album=None):
    """Save a song a blog posted, whole, as Hype Machine streams it.

    True when a new file was written.
    """
    if not enabled() or not item_id or not artist or not title:
        return False
    key = _hypem_key(item_id)
    if _row(key):
        return False
    return _download(key, f"https://hypem.com/serve/public/{item_id}",
                     artist=artist, title=title, album=album, source="hypem",
                     label="Hype Machine", icon=None,
                     page_url=f"https://hypem.com/track/{item_id}",
                     name_suffix=" (Hype Machine)")


def save_async(fn, *args, **kwargs):
    """Run a save in the background: what a play does after it's started."""
    if not enabled():
        return
    threading.Thread(target=lambda: _quietly(fn, *args, **kwargs), daemon=True).start()


def _quietly(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 - a file that won't save is no loss
        pass


def _target(artist, title, ext, name_suffix=""):
    """(relative path, absolute path) for a new file, never over another one."""
    folder = _safe(artist)
    stem = _safe(f"{artist} - {title}{name_suffix}", limit=150)
    os.makedirs(os.path.join(base_dir(), folder), exist_ok=True)
    for n in range(1, 100):
        name = f"{stem}.{ext}" if n == 1 else f"{stem} ({n}).{ext}"
        rel = os.path.join(folder, name)
        full = os.path.join(base_dir(), rel)
        if not os.path.exists(full):
            return rel, full
    return None, None


def _download(key, url, *, artist, title, album=None, source=None, label=None,
              icon=None, page_url=None, name_suffix=""):
    """Fetch one file into the folder and index it. True when it was saved."""
    with _busy_lock:
        if key in _busy:
            return False
        _busy.add(key)
    try:
        try:
            # The CDNs have budgets too (app/ratelimit.py): a pre-load of a
            # few thousand files needn't arrive all at once.
            resp = ratelimit.get(url, stream=True, timeout=(5, 30), max_wait=60,
                                 headers={"User-Agent": _BROWSER_UA})
        except requests.RequestException:
            return False
        with resp:
            if not resp.ok:
                return False
            kind = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = _EXT_BY_TYPE.get(kind)
            if not ext:
                # Some CDNs say octet-stream; the URL's own suffix will do.
                path_part = resp.url.split("?")[0].lower()
                ext = next((e for e in ("mp3", "m4a") if path_part.endswith("." + e)), None)
            if not ext:
                return False  # a page, not audio
            rel, full = _target(artist, title, ext, name_suffix)
            if not full:
                return False
            part = full + ".part"
            size = 0
            try:
                with open(part, "wb") as out:
                    for chunk in resp.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > _MAX_BYTES:
                            raise ValueError("too big")
                        out.write(chunk)
                if size == 0:
                    raise ValueError("empty")
                os.replace(part, full)
            except (OSError, ValueError, requests.RequestException):
                try:
                    os.remove(part)
                except OSError:
                    pass
                return False
        with db._write_lock:
            conn = db.get_connection()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO saved_audio (audio_key, artist, title, album, "
                    "source, label, icon, page_url, path, size, saved_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (key, artist, title, album, source, label, icon, page_url,
                     rel, size, time.time()),
                )
                conn.commit()
            finally:
                conn.close()
        prune()
        return True
    finally:
        with _busy_lock:
            _busy.discard(key)


def prune():
    """Drop the least recently played files once the folder is over its cap."""
    cap = _cap_bytes()
    if cap is None:
        return
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT audio_key, path, size FROM saved_audio "
            "ORDER BY COALESCE(played_at, saved_at)"
        ).fetchall()
    finally:
        conn.close()
    total = sum(r["size"] for r in rows)
    gone = []
    for r in rows:
        if total <= cap:
            break
        full = os.path.join(base_dir(), r["path"])
        try:
            os.remove(full)
        except FileNotFoundError:
            pass
        except OSError:
            continue
        _remove_empty_dir(os.path.dirname(full))
        total -= r["size"]
        gone.append(r["audio_key"])
    if gone:
        _forget(gone)


def _remove_empty_dir(path):
    if os.path.abspath(path) == os.path.abspath(base_dir()):
        return
    try:
        os.rmdir(path)  # only succeeds when empty
    except OSError:
        pass


def reconcile():
    """Drop the rows whose file has been deleted by hand."""
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT audio_key, path FROM saved_audio").fetchall()
    finally:
        conn.close()
    root = base_dir()
    missing = [r["audio_key"] for r in rows
               if not os.path.isfile(os.path.join(root, r["path"]))]
    if missing:
        _forget(missing)


def stats():
    """{count, bytes, dir, cap_bytes} for the settings page."""
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b FROM saved_audio"
        ).fetchone()
    finally:
        conn.close()
    return {"count": row["n"], "bytes": row["b"], "dir": base_dir(),
            "cap_bytes": _cap_bytes()}
