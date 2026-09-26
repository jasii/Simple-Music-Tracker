"""On-disk cache for album art (and any remote image we display).

Images are fetched once from their source URL, written under the data folder,
and served locally thereafter. The original URL is only used as a fallback when
the bytes aren't on disk (and can't be fetched). Keeping the files under /data
means they're covered by the same backup as everything else.
"""

import hashlib
import os
import threading
import time

import requests

from . import db

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB ceiling per image
# How long a confirmed miss (404 / not an image) is remembered before we retry.
# Cover Art Archive 404s take seconds each; without this every page view
# re-pays that cost for every release-group that has no front image.
_NEG_TTL = 3 * 86400
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


def _miss_path(key):
    return os.path.join(art_dir(), key + ".miss")


def negative_cached(url):
    """True if *url* recently returned a confirmed miss (404/not an image)."""
    if not url:
        return False
    try:
        return (time.time() - os.path.getmtime(_miss_path(_key(url)))) < _NEG_TTL
    except OSError:
        return False


def usable(url, *, download=True):
    """Will this image actually load? Bytes on disk, or a fetch that works.

    A stored URL is not evidence of artwork: half the hotlinks an artist page
    is seeded with are dead by the time anyone looks (an image host that has
    gone down takes thousands of them at once), and nothing noticed because the
    row still had a URL in it. With *download* False this only believes what is
    already on disk.
    """
    if not url:
        return False
    if cached_path(url):
        return True
    if negative_cached(url) or not download:
        return False
    return bool(fetch(url))


def broken(url):
    """True when this URL is known not to serve an image (no request made)."""
    return bool(url) and not cached_path(url) and negative_cached(url)


def _mark_miss(key):
    try:
        with open(_miss_path(key), "wb"):
            pass
        os.utime(_miss_path(key), None)
    except OSError:
        pass


def _lock_for(key):
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def fetch(url):
    """Download *url* to the cache and return its path, or None on failure.

    Confirmed misses (4xx, non-image, empty body) are remembered via a .miss
    marker so repeat requests fail instantly instead of re-hitting a slow
    upstream; transient failures (timeout, connection error) are not marked
    and will be retried.
    """
    if not url:
        return None
    key = _key(url)
    path = os.path.join(art_dir(), key)
    # Serialise concurrent fetches of the same image.
    with _lock_for(key):
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        if negative_cached(url):
            return None
        try:
            resp = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=(5, 15),
                stream=True,
            )
            if 400 <= resp.status_code < 500:
                _mark_miss(key)
                return None
            resp.raise_for_status()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if ctype and not ctype.startswith("image/"):
                _mark_miss(key)
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
                _mark_miss(key)
                return None
            os.replace(tmp, path)
            # Drop any stale miss marker from before the image existed upstream.
            try:
                os.remove(_miss_path(key))
            except OSError:
                pass
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
