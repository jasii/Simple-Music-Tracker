"""Last.fm artist information lookups.

Used for artist bios, official URLs, and images. Last.fm has largely stopped
serving real artist images through the API (they return a placeholder star),
so callers should treat the image as best-effort and fall back to album art.
"""

import time

import requests

from . import db

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"

# Aurral's Last.fm tuning: short timeout with a couple of retries and a small
# exponential backoff. We serialise Last.fm calls through the refresh worker, so
# no separate concurrency limiter is needed.
_TIMEOUT_S = 6
_MAX_RETRIES = 2


def _lastfm_get(params):
    """GET the Last.fm API with retries/backoff. Returns parsed JSON or None."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.get(LASTFM_BASE, params=params, timeout=_TIMEOUT_S)
            # Retry transient server errors; otherwise use what we got.
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(response=resp)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError):
            if attempt >= _MAX_RETRIES:
                return None
            time.sleep(0.3 * (2 ** attempt) + attempt * 0.2)
    return None


def _placeholder(url):
    # Last.fm's deprecated image placeholder -- treat as "no image".
    return url and "2a96cbd8b46e442fc41c2b86b821562f" in url


def get_artist_info(name):
    """Return {bio, lastfm_url, image_url} for an artist, or {} on failure."""
    api_key = db.get_setting("lastfm_api_key")
    if not api_key:
        return {}
    data = _lastfm_get(
        {
            "method": "artist.getinfo",
            "artist": name,
            "api_key": api_key,
            "format": "json",
            "autocorrect": 1,
        }
    )
    if not data:
        return {}

    artist = data.get("artist")
    if not artist:
        return {}

    image_url = None
    for image in artist.get("image", []):
        if image.get("size") in ("extralarge", "mega", "large") and image.get("#text"):
            if not _placeholder(image["#text"]):
                image_url = image["#text"]
                break

    bio = ""
    bio_block = artist.get("bio", {})
    if bio_block:
        bio = (bio_block.get("summary") or "").strip()

    return {
        "bio": bio,
        "lastfm_url": artist.get("url"),
        "image_url": image_url,
    }
