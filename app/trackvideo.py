"""The video a track can be played from, for songs no catalogue will sample.

Last.fm's own player is a YouTube embed: every track page carries the video id
it plays in ``data-youtube-id``. For a remix or a one-off single -- the records
iTunes and Deezer have never heard of -- that id is the only way to hear the
thing, so it's read off the page here and played in an embed of our own rather
than sending anyone to Last.fm.

The page sits behind bot protection, so it's fetched with a browser TLS
fingerprint (curl_cffi) and, failing that, through the configured challenge
solver (app/plugins/solver). Ids and misses are both stored.
"""

import re
from urllib.parse import quote

import requests

from . import db
from .plugins import solver

try:  # optional: the same impersonating client the AOTY scraper uses
    from curl_cffi import requests as cffi_requests
except ImportError:  # pragma: no cover - fall back to plain requests
    cffi_requests = None

LASTFM_BASE = "https://www.last.fm"

# How long a found id and a remembered miss stay usable comes from settings
# (store_keep_days / store_miss_days): a found id keeps working, while a miss
# is worth retrying -- the track page may not have had a video attached yet.

_IMPERSONATE = ("chrome", "chrome120", "chrome110")

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_ID_PATTERNS = (
    re.compile(r'data-youtube-id="([\w-]{8,15})"'),
    re.compile(r'youtube\.com/watch\?v=([\w-]{8,15})'),
    re.compile(r'youtu\.be/([\w-]{8,15})'),
)


def _key(artist, title):
    return f"ytid:{(artist or '').strip().lower()}|{(title or '').strip().lower()}"


def track_page(artist, title):
    """Last.fm's canonical page for one track."""
    return (
        f"{LASTFM_BASE}/music/{quote((artist or '').strip(), safe='')}"
        f"/_/{quote((title or '').strip(), safe='')}"
    )


def _fetch(url):
    """The page's HTML, or None. Browser fingerprint first, solver second."""
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    if cffi_requests is not None:
        for target in _IMPERSONATE:
            try:
                resp = cffi_requests.get(url, headers=headers,
                                         impersonate=target, timeout=20)
            except (ValueError, TypeError):
                continue  # this curl_cffi build doesn't know that profile
            except Exception:  # noqa: BLE001 - transport failure, try the solver
                break
            if resp.status_code == 200 and len(resp.text) > 20_000:
                return resp.text
            break
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200 and len(resp.text) > 20_000:
            return resp.text
    except requests.RequestException:
        pass
    # Still challenged: hand it to FlareSolverr, if the user set one up.
    try:
        solved = solver.fetch_html(url)
    except Exception:  # noqa: BLE001 - a solver failure is just a miss
        solved = None
    return (solved or {}).get("html")


def _first_id(html):
    for pattern in _ID_PATTERNS:
        found = pattern.search(html or "")
        if found:
            return found.group(1)
    return None


def for_track(artist, title, page_url=None):
    """The YouTube id Last.fm plays for this track, or None.

    *page_url* is the track's own Last.fm page when a tracklist gave us one;
    otherwise the canonical artist/title URL is used.
    """
    if not artist or not title:
        return None
    cache_key = _key(artist, title)
    cached = db.get_json_cache(cache_key, max_age=db.cache_max_age("hit"))
    if cached:
        return cached.get("id")
    # Stored {"id": null} is a remembered miss: honour it inside the miss TTL.
    if cached is not None and db.get_json_cache(
            cache_key, max_age=db.cache_max_age("miss")) is not None:
        return None

    video_id = None
    for url in [u for u in (page_url, track_page(artist, title)) if u]:
        video_id = _first_id(_fetch(url))
        if video_id:
            break
    db.set_json_cache(cache_key, {"id": video_id})
    return video_id
