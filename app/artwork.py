"""On-disk cache for album art (and any remote image we display).

Images are fetched once from their source URL, written under the data folder,
and served locally thereafter. The original URL is only used as a fallback when
the bytes aren't on disk (and can't be fetched). Keeping the files under /data
means they're covered by the same backup as everything else.
"""

import hashlib
import os
import threading

import requests

from . import db

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB ceiling per image
_locks = {}
_locks_guard = threading.Lock()


def art_dir():
    """Directory holding cached images, alongside the SQLite DB under /data."""
    base = os.path.dirname(os.path.abspath(db.DB_PATH))
    path = os.path.join(base, "artwork")
    os.makedirs(path, exist_ok=True)
    return path


def _key(url):
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()


def path_for(url):
    return os.path.join(art_dir(), _key(url))


def cached_path(url):
    """Return the on-disk path for *url* if already saved, else None."""
    if not url:
        return None
    p = path_for(url)
    return p if os.path.isfile(p) and os.path.getsize(p) > 0 else None


def _lock_for(key):
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def fetch(url):
    """Download *url* to the cache and return its path, or None on failure."""
    if not url:
        return None
    key = _key(url)
    path = os.path.join(art_dir(), key)
    # Serialise concurrent fetches of the same image.
    with _lock_for(key):
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        try:
            resp = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=20, stream=True
            )
            resp.raise_for_status()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if ctype and not ctype.startswith("image/"):
                return None
            total = 0
            tmp = path + ".part"
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(8192):
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        fh.close()
                        os.remove(tmp)
                        return None
                    fh.write(chunk)
            if total == 0:
                os.remove(tmp)
                return None
            os.replace(tmp, path)
            return path
        except (requests.RequestException, OSError):
            return None


def content_type(path):
    """Sniff the image type from magic bytes (no extension is stored)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return "application/octet-stream"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"
