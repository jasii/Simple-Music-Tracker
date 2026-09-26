"""Last.fm artist information lookups.

Used for artist bios, official URLs, and images. Last.fm has largely stopped
serving real artist images through the API (they return a placeholder star),
so callers should treat the image as best-effort and fall back to album art.
"""

import re
import time

import requests

from . import db

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"

# Last.fm appends a trailing '<a ...>Read more on Last.fm</a>' to every bio
# and album summary; drop it (with the full stop album summaries put after it)
# but keep the description.
_READMORE_RE = re.compile(
    r"\s*<a\b[^>]*>\s*Read more on Last\.fm\s*</a>\s*\.?\s*$", re.IGNORECASE
)
# Anything else Last.fm marks up inside a summary: keep the words, drop the tag.
_ANY_TAG = re.compile(r"<[^>]+>")


def clean_bio(bio):
    """A bio or album summary as plain text, without the "Read more" link."""
    if not bio:
        return bio
    return _ANY_TAG.sub("", _READMORE_RE.sub("", bio)).strip()


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


def _album_cache_key(artist, album):
    return f"lfalbum3:{(artist or '').strip().lower()}|{(album or '').strip().lower()}"


def get_album_info(artist, album, max_age=db.FROM_SETTINGS):
    """Return {image_url, genres, description, tracks} for a release, or {}.

    One call to album.getInfo gives the cover image, the album's top tags (used
    as genres), the sleeve-notes summary and the tracklist. Needs a configured
    Last.fm API key. Cached, since the album page, the previews and the
    metadata sources all want the same answer.
    """
    api_key = db.get_setting("lastfm_api_key")
    if not api_key or not artist or not album:
        return {}
    cache_key = _album_cache_key(artist, album)
    cached = db.get_json_cache(cache_key, max_age=db.resolve_max_age(max_age, "hit"))
    if cached:
        return cached
    # An empty payload is a remembered miss, honoured only inside the miss TTL.
    if cached == {} and db.get_json_cache(
            cache_key, max_age=db.cache_max_age("miss")) == {}:
        return {}
    info = _fetch_album_info(api_key, artist, album)
    db.set_json_cache(cache_key, info)
    return info


def cached_album_info(artist, album):
    """A stored album.getInfo answer, without asking Last.fm for a new one."""
    return db.get_json_cache(_album_cache_key(artist, album),
                             max_age=db.cache_max_age("hit")) or None


def _fetch_album_info(api_key, artist, album):
    """Uncached album.getInfo lookup. Returns {} when there's nothing usable."""
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

    # Last.fm's sleeve notes: prose about the record, where it has any.
    wiki = info.get("wiki") or {}
    description = clean_bio(wiki.get("summary") or "") or None

    return {
        "image_url": _best_image(info.get("image")),
        "genres": genres,
        "description": description,
        "lastfm_url": info.get("url"),
        "tracks": _album_tracks(info),
    }


# Artist bios/urls barely change, and the refresh worker asks for every
# followed artist on every cycle -- thousands of identical calls a day without
# a cache. A miss (no match, or Last.fm down) is remembered for a day only, so
# artists that show up later still get picked up.



def cached_artist_info(name):
    """A stored artist.getinfo answer, without asking Last.fm for a new one.

    Used where a lookup would be too expensive to do per artist (the artwork
    warmer walks the whole library), so a cache miss simply means no answer.
    """
    return db.get_json_cache(_artist_cache_key(name),
                             max_age=db.cache_max_age("hit")) or None


def _artist_cache_key(name):
    return "lfartist:" + (name or "").strip().lower()


def get_artist_info(name, max_age=db.FROM_SETTINGS):
    """Return {bio, lastfm_url, image_url} for an artist, or {} on failure.

    Cached in the DB; pass *max_age* to require something fresher.
    """
    api_key = db.get_setting("lastfm_api_key")
    if not api_key:
        return {}
    cache_key = _artist_cache_key(name)
    cached = db.get_json_cache(cache_key, max_age=db.resolve_max_age(max_age, "hit"))
    if cached:
        return cached
    # An empty payload is a remembered miss, which expires much sooner than a
    # real hit: only serve it while it's inside the miss TTL.
    if cached == {} and db.get_json_cache(
            cache_key, max_age=db.cache_max_age("miss")) == {}:
        return {}
    info = _fetch_artist_info(api_key, name)
    db.set_json_cache(cache_key, info)
    return info


# --- the user's own listening ----------------------------------------------

# Scrobble history moves slowly enough that a few hours of staleness costs
# nothing, and these calls page through hundreds of artists.

_TOP_PERIODS = ("overall", "7day", "1month", "3month", "6month", "12month")


def username():
    return (db.get_setting("lastfm_username") or "").strip()


def top_artists(period="overall", limit=200, max_age=db.FROM_SETTINGS):
    """The user's most-scrobbled artists: [{name, playcount, url, image_url}].

    Empty when no username or API key is configured -- this is opt-in, and the
    rest of the app works without it.
    """
    api_key = db.get_setting("lastfm_api_key")
    user = username()
    if not api_key or not user:
        return []
    if period not in _TOP_PERIODS:
        period = "overall"
    limit = max(1, min(int(limit or 200), 1000))
    cache_key = f"lfuser:top:{user.lower()}:{period}:{limit}"
    cached = db.get_json_cache(cache_key, max_age=db.resolve_max_age(max_age, "scrobble"))
    if cached is not None:
        return cached
    data = _lastfm_get({
        "method": "user.gettopartists",
        "user": user,
        "period": period,
        "limit": limit,
        "api_key": api_key,
        "format": "json",
    }) or {}
    artists = ((data.get("topartists") or {}).get("artist")) or []
    out = []
    for item in artists:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        try:
            plays = int(item.get("playcount") or 0)
        except (TypeError, ValueError):
            plays = 0
        image = None
        for img in item.get("image") or []:
            if img.get("size") in ("extralarge", "large") and img.get("#text"):
                if not _placeholder(img["#text"]):
                    image = img["#text"]
                    break
        out.append({"name": name, "playcount": plays,
                    "url": item.get("url"), "image_url": image})
    db.set_json_cache(cache_key, out)
    return out


def check_user():
    """Validate the configured username. Returns (ok, message)."""
    user = username()
    if not user:
        return False, "No Last.fm username set."
    if not (db.get_setting("lastfm_api_key") or "").strip():
        return False, "Set the Last.fm API key first."
    artists = top_artists(period="overall", limit=5, max_age=0)
    if not artists:
        return False, f"No scrobbles found for '{user}' (is the name right?)."
    return True, f"OK - {user} scrobbles {artists[0]['name']} most."


# Similar artists and tags move about as slowly as a bio does, and both are
# one call each, so they keep the same long cache.

# Last.fm reports similarity as a 0-1 "match"; the score column holds whole
# numbers, so it's scaled up to keep the ranking's tie-breaks meaningful.
_MATCH_SCALE = 100


def similar_artists(name, limit=30, max_age=db.FROM_SETTINGS):
    """Artists Last.fm considers close to *name*.

    Returns [{name, url, score}] -- the shape db.record_similar_artists wants
    -- or [] when there's no API key, no match, or the call failed.
    """
    api_key = db.get_setting("lastfm_api_key")
    if not api_key or not name:
        return []
    cache_key = f"lfsimilar:{name.strip().lower()}:{limit}"
    cached = db.get_json_cache(cache_key, max_age=db.resolve_max_age(max_age, "hit"))
    if cached is not None:
        return cached
    data = _lastfm_get({
        "method": "artist.getsimilar",
        "artist": name,
        "limit": limit,
        "api_key": api_key,
        "format": "json",
        "autocorrect": 1,
    }) or {}
    entries = ((data.get("similarartists") or {}).get("artist")) or []
    out = []
    for entry in entries:
        similar_name = (entry.get("name") or "").strip()
        if not similar_name:
            continue
        try:
            match = float(entry.get("match") or 0)
        except (TypeError, ValueError):
            match = 0.0
        out.append({
            "name": similar_name,
            "url": entry.get("url"),
            "score": round(match * _MATCH_SCALE, 2),
        })
    db.set_json_cache(cache_key, out)
    return out


# An artist's best-known tracks change slowly; two weeks is plenty.

# How many are fetched and stored, whatever the caller asked for.
_TRACKS_FETCHED = 50


def top_tracks(name, limit=5, max_age=db.FROM_SETTINGS):
    """The artist's most-played tracks: [{name, url, playcount, mbid}].

    Empty when there's no API key, no match, or the call failed.
    """
    api_key = db.get_setting("lastfm_api_key")
    if not api_key or not name:
        return []
    # Always fetched (and cached) in one size, then sliced: the artist page asks
    # for five and the album page's hot-track marking for fifty, and two cache
    # entries meant two calls about the same artist.
    limit = max(1, min(int(limit or 5), _TRACKS_FETCHED))
    cache_key = f"lftracks:{name.strip().lower()}:{_TRACKS_FETCHED}"
    cached = db.get_json_cache(cache_key, max_age=db.resolve_max_age(max_age, "hit"))
    if cached is not None:
        return cached[:limit]
    data = _lastfm_get({
        "method": "artist.gettoptracks",
        "artist": name,
        "limit": _TRACKS_FETCHED,
        "api_key": api_key,
        "format": "json",
        "autocorrect": 1,
    }) or {}
    entries = ((data.get("toptracks") or {}).get("track")) or []
    # One-track artists come back as a bare object rather than a list.
    if isinstance(entries, dict):
        entries = [entries]
    out = []
    for entry in entries:
        title = (entry.get("name") or "").strip()
        if not title:
            continue
        try:
            plays = int(entry.get("playcount") or 0)
        except (TypeError, ValueError):
            plays = 0
        out.append({
            "name": title,
            "url": entry.get("url"),
            "playcount": plays,
            "mbid": entry.get("mbid") or None,
        })
    db.set_json_cache(cache_key, out)
    return out[:limit]


def top_tags(name, limit=5, max_age=db.FROM_SETTINGS):
    """Last.fm's top tags for an artist, as genre labels."""
    api_key = db.get_setting("lastfm_api_key")
    if not api_key or not name:
        return []
    cache_key = f"lftags:{name.strip().lower()}"
    cached = db.get_json_cache(cache_key, max_age=db.resolve_max_age(max_age, "hit"))
    if cached is not None:
        return cached[:limit]
    data = _lastfm_get({
        "method": "artist.gettoptags",
        "artist": name,
        "api_key": api_key,
        "format": "json",
        "autocorrect": 1,
    }) or {}
    tags = ((data.get("toptags") or {}).get("tag")) or []
    out = []
    for tag in tags:
        label = (tag.get("name") or "").strip()
        # Last.fm's tag cloud is half genres, half opinions ("seen live",
        # "favourites"); a count floor keeps the noise down.
        if label and int(tag.get("count") or 0) >= 10:
            out.append(label)
    db.set_json_cache(cache_key, out)
    return out[:limit]


def _fetch_artist_info(api_key, name):
    """Uncached artist.getinfo lookup. Returns {} when there's nothing usable."""
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
        bio = clean_bio(bio_block.get("summary") or "")

    return {
        "bio": bio,
        "lastfm_url": artist.get("url"),
        "image_url": image_url,
    }
