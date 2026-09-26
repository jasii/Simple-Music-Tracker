"""Thirty-second audio samples for a track, from the keyless public catalogues.

Last.fm hands out track names and playcounts but no audio, so the sample
players on an artist page need a preview URL from somewhere else: iTunes Search
first (the widest catalogue), Deezer second. Every answer -- misses included --
is stored in json_cache, so an artist page that has been opened once never
looks its samples up again.

Neither service needs an API key, and both ask to be treated gently, so all
lookups here are serialised and paced.
"""

import threading
import time

import requests

from . import db, names

ITUNES_SEARCH = "https://itunes.apple.com/search"
DEEZER_SEARCH = "https://api.deezer.com/search"

# How long a found preview and a remembered miss stay usable comes from
# settings (store_keep_days / store_miss_days): a preview URL keeps working,
# while a miss is worth retrying -- the track may land on a service later.

_GAP_S = 0.25
_pace_lock = threading.Lock()
_last_call = [0.0]


def _paced_get(url, params):
    """GET one catalogue, no faster than one request every _GAP_S."""
    with _pace_lock:
        wait = _last_call[0] + _GAP_S - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
    try:
        resp = requests.get(url, params=params, timeout=(5, 10))
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def _key(artist, title, source="itunes"):
    # Versioned: answers found by substring matching can be another artist's.
    # Keyed per source, because which one is asked first is the user's choice
    # (Settings > Metadata) and a merged answer would outlive that choice.
    # Versioned again now that the catalogue's own page is stored beside the
    # audio: an entry without one can't credit its source.
    return (f"preview3:{source}:{(artist or '').strip().lower()}"
            f":{(title or '').strip().lower()}")


def _same_track(artist, title, got_artist, got_title):
    """Is this search result actually the track we asked for?

    Compared word-wise (see app/names.py): a search for a short name used to
    accept anything containing it, which is how "Alex G" matched a track by
    "Alex Gaudino" and played it as the preview for an Alex G record.
    """
    return (names.same_name(artist, got_artist)
            and names.same_name(title, got_title))


def _from_itunes(artist, title):
    """(preview url, the store page it came from) or (None, None)."""
    data = _paced_get(ITUNES_SEARCH, {
        "term": f"{artist} {title}",
        "media": "music",
        "entity": "song",
        "limit": 5,
    }) or {}
    for row in data.get("results") or []:
        if row.get("previewUrl") and _same_track(
            artist, title, row.get("artistName"), row.get("trackName")
        ):
            return row["previewUrl"], row.get("trackViewUrl")
    return None, None


def _from_deezer(artist, title):
    """Deezer's sample for one track, or None.

    Its field query is exact to the point of uselessness -- searching
    artist:"Bonobo" track:"Kiara" finds nothing while Deezer plainly has the
    track -- so a plain search follows it. Both still have to agree on artist
    *and* title, which is what keeps a remix or a different song of the same
    name out (see _same_track).
    """
    for query in (f'artist:"{artist}" track:"{title}"', f"{artist} {title}"):
        data = _paced_get(DEEZER_SEARCH, {"q": query, "limit": 5}) or {}
        for row in data.get("data") or []:
            if row.get("preview") and _same_track(
                artist, title, (row.get("artist") or {}).get("name"), row.get("title")
            ):
                return row["preview"], row.get("link")
    return None, None


def from_source(source, artist, title, cached_only=False):
    """A 30-second sample from one catalogue, cached per catalogue.

    *source* is "itunes" or "deezer" -- the two keyless catalogues with audio.
    Which of them is asked first is decided by the metadata order, so each
    keeps its own answer rather than sharing one merged result.
    """
    if not artist or not title:
        return None
    key = _key(artist, title, source)
    cached = db.get_json_cache(key, max_age=db.cache_max_age("hit"))
    if cached:
        return cached.get("url")
    # A stored {"url": null} is a remembered miss: honour it only while it's
    # inside the (much shorter) miss TTL, then look again.
    if cached is not None and db.get_json_cache(
            key, max_age=db.cache_max_age("miss")) is not None:
        return None
    if cached_only:
        return None
    lookup = _from_itunes if source == "itunes" else _from_deezer
    url, page = lookup(artist, title)
    db.set_json_cache(key, {"url": url, "page": page})
    return url


def cached_details(artist, title):
    """{source, label, url, page} for a sample already stored, or None.

    Reads the per-source caches in the order the user put them in, so the
    answer matches whichever source actually supplied the audio. No lookups:
    this runs while a page is being built.
    """
    from .plugins import metadata as registry

    for plugin in registry.sources("track_preview"):
        stored = db.get_json_cache(_key(artist, title, plugin.key),
                                   max_age=db.cache_max_age("hit"))
        if stored and stored.get("url"):
            return {"source": plugin.key, "label": plugin.display_label(),
                    "icon": plugin.icon_name(),
                    "url": stored["url"], "page": stored.get("page")}
    return None


def page_from_source(source, artist, title):
    """The catalogue's own page for a sample it gave us, if it named one."""
    if not artist or not title:
        return None
    cached = db.get_json_cache(_key(artist, title, source),
                              max_age=db.cache_max_age("hit")) or {}
    return cached.get("page")


def for_track(artist, title, cached_only=False):
    """A preview URL for one track, from the first source that has one.

    Asks the catalogues in the order set in Settings > Metadata; the video
    sources are not consulted here (they have no audio URL to hand back, only
    an embed -- see app/playable.py).
    """
    if not artist or not title:
        return None
    from .plugins import metadata as registry

    for plugin in registry.sources("track_preview"):
        found = plugin.track_preview(artist, title, cached_only=cached_only)
        if found and found.get("stream_url"):
            return found["stream_url"]
    return None


def for_tracks(artist, titles):
    """Preview URLs for several tracks by one artist: {title: url or None}."""
    return {title: for_track(artist, title) for title in titles or []}
