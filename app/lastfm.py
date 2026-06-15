"""Last.fm artist information lookups.

Used for artist bios, official URLs, and images. Last.fm has largely stopped
serving real artist images through the API (they return a placeholder star),
so callers should treat the image as best-effort and fall back to album art.
"""

import time

import requests

from . import db

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"


def check_api_key():
    """Validate the configured Last.fm API key. Returns (ok, message)."""
    key = (db.get_setting("lastfm_api_key") or "").strip()
    if not key:
        return False, "No API key set."
    try:
        resp = requests.get(
            LASTFM_BASE,
            params={"method": "auth.getToken", "api_key": key, "format": "json"},
            timeout=6,
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return False, f"Could not reach Last.fm: {exc}"
    if isinstance(data, dict) and data.get("error"):
        return False, f"Last.fm error {data['error']}: {data.get('message', '')}"
    if isinstance(data, dict) and data.get("token"):
        return True, "API key is valid."
    return False, "Unexpected response from Last.fm."

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


def _best_image(images):
    """Pick the largest non-placeholder image URL from a Last.fm image list."""
    by_size = {img.get("size"): img.get("#text") for img in (images or [])}
    for size in ("mega", "extralarge", "large", "medium"):
        url = by_size.get(size)
        if url and not _placeholder(url):
            return url
    return None


def _album_tracks(info):
    """Pull an ordered tracklist out of an album.getInfo payload."""
    block = info.get("tracks") or {}
    raw = block.get("track") if isinstance(block, dict) else None
    if isinstance(raw, dict):  # single-track albums come back as one object
        raw = [raw]
    tracks = []
    for t in raw or []:
        name = t.get("name")
        if not name:
            continue
        duration = t.get("duration")
        try:
            duration = int(duration) if duration else None
        except (TypeError, ValueError):
            duration = None
        tracks.append({"name": name, "duration": duration, "url": t.get("url")})
    return tracks


def get_album_info(artist, album):
    """Return {image_url, genres, tracks} for a release, or {} on failure.

    One call to album.getInfo gives the cover image, the album's top tags
    (used as genres), and the tracklist. Needs a configured Last.fm API key.
    """
    api_key = db.get_setting("lastfm_api_key")
    if not api_key or not artist or not album:
        return {}
    data = _lastfm_get(
        {
            "method": "album.getinfo",
            "artist": artist,
            "album": album,
            "api_key": api_key,
            "format": "json",
            "autocorrect": 1,
        }
    )
    if not data:
        return {}
    info = data.get("album")
    if not info:
        return {}

    tags = info.get("tags") or {}
    tag_list = tags.get("tag") if isinstance(tags, dict) else None
    genres = [t.get("name") for t in (tag_list or []) if t.get("name")]

    return {
        "image_url": _best_image(info.get("image")),
        "genres": genres,
        "lastfm_url": info.get("url"),
        "tracks": _album_tracks(info),
    }


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
