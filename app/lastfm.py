"""Last.fm artist information lookups.

Used for artist bios, official URLs, and images. Last.fm has largely stopped
serving real artist images through the API (they return a placeholder star),
so callers should treat the image as best-effort and fall back to album art.
"""

import requests

from . import db

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"


def _placeholder(url):
    # Last.fm's deprecated image placeholder -- treat as "no image".
    return url and "2a96cbd8b46e442fc41c2b86b821562f" in url


def get_artist_info(name):
    """Return {bio, lastfm_url, image_url} for an artist, or {} on failure."""
    api_key = db.get_setting("lastfm_api_key")
    if not api_key:
        return {}
    try:
        resp = requests.get(
            LASTFM_BASE,
            params={
                "method": "artist.getinfo",
                "artist": name,
                "api_key": api_key,
                "format": "json",
                "autocorrect": 1,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
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
