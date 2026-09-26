"""Deezer lookups: artist photos and album covers, no API key needed.

Deezer is the one free catalogue with a press photo for nearly every artist,
including the small ones Last.fm has nothing for, and its covers are served at
1000x1000. Its search matches loosely, though -- a search for one band finds
tribute acts and soundalikes -- so every result has to agree on the name (see
app/names.py) before it's offered.

Answers and misses are both cached; the pacing lives in app/preview.py, which
talks to the same host for track samples, so the whole app keeps one queue.
"""

from . import db, names
from .preview import _paced_get

SEARCH_ARTIST = "https://api.deezer.com/search/artist"
SEARCH_ALBUM = "https://api.deezer.com/search/album"

# How many photos to keep for the artwork picker.
_MAX_IMAGES = 4

# Deezer answers for an artist it has no picture of with the same URL minus the
# id -- ".../images/artist//1000x1000-...jpg" -- which serves a grey square.
_NO_PICTURE = "/artist//"


# Versioned: a remembered miss is only as good as the name matching that
# produced it, and the rules in app/names.py have since learned that a leading
# "The" and a spelled-out "and" are spelling, not identity.
def _artist_key(name):
    return f"dzartist2:{(name or '').strip().lower()}"


def _album_key(artist, title):
    return f"dzalbum2:{(artist or '').strip().lower()}|{(title or '').strip().lower()}"


def _cached(key):
    """(hit, value): a stored answer, honouring the shorter TTL for a miss."""
    stored = db.get_json_cache(key, max_age=db.cache_max_age("hit"))
    if stored:
        return True, stored
    if stored is not None and db.get_json_cache(
            key, max_age=db.cache_max_age("miss")) is not None:
        return True, stored
    return False, None


def artist_images(name, cached_only=False):
    """Every photo Deezer has for this artist, biggest size first.

    *cached_only* answers from the cache or not at all, for callers that walk
    the whole library and can't afford a lookup per artist.
    """
    if not name:
        return []
    key = _artist_key(name)
    hit, stored = _cached(key)
    if hit:
        return (stored or {}).get("images") or []
    if cached_only:
        return []

    data = _paced_get(SEARCH_ARTIST, {"q": name, "limit": 10}) or {}
    images = []
    for row in data.get("data") or []:
        if not names.same_name(name, row.get("name")):
            continue
        url = row.get("picture_xl") or row.get("picture_big") or row.get("picture")
        if url and _NO_PICTURE in url:
            continue
        if url and url not in images:
            images.append(url)
        if len(images) >= _MAX_IMAGES:
            break
    db.set_json_cache(key, {"images": images})
    return images


def album_cover(artist, title):
    """Deezer's cover for one release, or None."""
    if not artist or not title:
        return None
    key = _album_key(artist, title)
    hit, stored = _cached(key)
    if hit:
        return (stored or {}).get("cover")

    # The field query is exact and misses records Deezer does have under a
    # slightly different spelling, so a plain search is tried after it. Both
    # still have to agree on artist *and* title -- a loose search for one album
    # happily returns a different artist's record with a similar name.
    cover = None
    for query in (f'artist:"{artist}" album:"{title}"', f"{artist} {title}"):
        data = _paced_get(SEARCH_ALBUM, {"q": query, "limit": 5}) or {}
        for row in data.get("data") or []:
            if not names.same_name(artist, (row.get("artist") or {}).get("name")):
                continue
            if not names.same_name(title, row.get("title")):
                continue
            cover = row.get("cover_xl") or row.get("cover_big") or row.get("cover")
            if cover:
                break
        if cover:
            break
    db.set_json_cache(key, {"cover": cover})
    return cover
