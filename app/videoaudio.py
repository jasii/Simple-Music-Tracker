"""The audio track of a video, so a video-only preview plays like everything else.

Some records exist nowhere but a video: a remix, a bootleg, a one-off single
no catalogue ever sold. Last.fm's own player handles those with a YouTube
embed, which the app copied -- and an embed is a second player with its own
volume, its own play button and no place in the queue.

So the audio stream is pulled out instead and served like any other track: the
docked player gets an ordinary audio URL, the volume is the volume, and pause
is pause. Only the audio stream is touched, nothing is written to disk, and it
is proxied rather than linked because the URLs are short-lived and tied to the
address that asked for them.

yt-dlp does the extraction and is an optional dependency: without it, the app
falls back to the embed exactly as before.
"""

import os
import threading
import time

from flask import send_file

from . import db

try:  # optional: previews fall back to the embed without it
    import yt_dlp
except ImportError:  # pragma: no cover - depends on the install
    yt_dlp = None

# Audio-only, AAC for preference: every browser plays it in a bare <audio>
# element, and no re-encoding (so no ffmpeg) is involved.
_FORMAT = "bestaudio[acodec^=mp4a]/bestaudio[ext=m4a]/bestaudio"

# The extracted URL carries its own expiry -- a few hours -- so it is cached
# for less than that and re-resolved after.
_URL_TTL = 2 * 3600

_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# One extraction per video at a time: an album page can ask for several tracks
# at once and each extraction is a second of work.
_locks = {}
_locks_guard = threading.Lock()


def available():
    """True when the extractor is installed."""
    return yt_dlp is not None


def _lock_for(video_id):
    with _locks_guard:
        lock = _locks.get(video_id)
        if lock is None:
            lock = _locks[video_id] = threading.Lock()
        return lock


def _key(video_id):
    return f"ytaudio:{video_id}"


def audio_url(video_id):
    """A direct, playable audio URL for one video, or None."""
    if not video_id or yt_dlp is None:
        return None
    cached = db.get_json_cache(_key(video_id), max_age=_URL_TTL)
    if cached and cached.get("url"):
        return cached["url"]

    with _lock_for(video_id):
        # Someone else may have resolved it while we waited.
        cached = db.get_json_cache(_key(video_id), max_age=_URL_TTL)
        if cached and cached.get("url"):
            return cached["url"]
        options = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "format": _FORMAT, "noplaylist": True,
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}", download=False)
        except Exception:  # noqa: BLE001 - a video that won't resolve is a miss
            db.set_json_cache(_key(video_id), {"url": None, "at": time.time()})
            return None
        url = (info or {}).get("url")
        db.set_json_cache(_key(video_id), {"url": url, "at": time.time()})
        return url


def descriptor(video_id):
    """Where the audio is and how to ask for it, or None.

    Proxied through the app rather than linked: the URL is tied to this
    machine's address and expires, so handing it to a browser would play for
    one person on one network until it didn't.
    """
    url = audio_url(video_id)
    if not url:
        return None
    return {"kind": "url", "url": url, "suffix": "m4a",
            "headers": {"User-Agent": _BROWSER_UA}}


# Where fetched audio is kept, next to the database and the artwork cache.
_DIR_NAME = "videoaudio"
# Roughly how much of it to keep. These are single tracks nobody owns, a few
# megabytes each; the oldest go when the cap is passed.
_CACHE_CAP = 512 * 1024 * 1024


def cache_dir():
    base = os.path.dirname(os.path.abspath(db.DB_PATH))
    path = os.path.join(base, _DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _path_for(video_id):
    return os.path.join(cache_dir(), f"{video_id}.m4a")


def _prune():
    """Drop the least recently played files once the cache is over its cap."""
    try:
        files = [(os.path.getmtime(p), os.path.getsize(p), p) for p in
                 (os.path.join(cache_dir(), n) for n in os.listdir(cache_dir()))
                 if os.path.isfile(p)]
    except OSError:
        return
    total = sum(size for _mtime, size, _p in files)
    for _mtime, size, path in sorted(files):
        if total <= _CACHE_CAP:
            return
        try:
            os.remove(path)
            total -= size
        except OSError:
            return


def fetch_file(video_id):
    """The audio as a file on disk, fetched once, or None.

    Fetched rather than proxied: the CDN serves a bounded range and then
    throttles, refusing anything but tiny reads part-way through a file, so
    piping it through live is unreliable by the second minute. yt-dlp already
    knows how to deal with that, so it downloads the one audio track (no
    re-encoding, no ffmpeg) and the browser is served an ordinary file --
    which also means seeking works and a second play is instant.
    """
    if not video_id or yt_dlp is None:
        return None
    path = _path_for(video_id)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        os.utime(path, None)  # most recently played
        return path
    with _lock_for(video_id):
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        options = {
            "quiet": True, "no_warnings": True, "noprogress": True,
            "format": _FORMAT,
            "noplaylist": True, "outtmpl": path, "overwrites": True,
            # No merging, no conversion: one stream, saved as it arrives.
            "postprocessors": [],
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        except Exception:  # noqa: BLE001 - a video that won't fetch is a miss
            return None
    if not (os.path.isfile(path) and os.path.getsize(path) > 0):
        return None
    _prune()
    return path


def respond(video_id, range_header=None):
    """Serve a video's audio to the browser, or None if it can't be had.

    An ordinary file response, so Range, seeking and caching all behave.
    """
    path = fetch_file(video_id)
    if not path:
        return None
    return send_file(path, mimetype="audio/mp4", conditional=True)
