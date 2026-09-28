"""Thirty-second audio samples for a track, from the keyless public catalogues.

Last.fm hands out track names and playcounts but no audio, so the sample
players on an artist page need a preview URL from somewhere else: iTunes Search
first (the widest catalogue), Deezer second. Every answer -- misses included --
is stored in json_cache, so an artist page that has been opened once never
looks its samples up again.

Neither service needs an API key, and both ask to be treated gently, so all
lookups here are serialised and paced.
"""

import re
import threading
import time
from urllib.parse import urlparse

import requests

from . import db, names

ITUNES_SEARCH = "https://itunes.apple.com/search"
DEEZER_SEARCH = "https://api.deezer.com/search"
DEEZER_TRACK = "https://api.deezer.com/track/{}"

# How long a found preview and a remembered miss stay usable comes from
# settings (store_keep_days / store_miss_days): a preview URL keeps working
# (Deezer's are re-signed as they lapse, see _resign), while a miss is worth
# retrying -- the track may land on a service later.

_GAP_S = 0.25
_pace_lock = threading.Lock()
_last_call = [0.0]

# A catalogue that has said "slow down" is left alone for a while, doubling
# each time it says it again. iTunes allows about twenty searches a minute and
# answers 403 past that; Deezer answers 200 with an error body. Neither is
# "this track doesn't exist", so neither may be stored as a miss.
_COOLDOWN_S = 60
_COOLDOWN_MAX_S = 600
_cooldown = {}  # host -> (until, current length)


def _cooling(host):
    until, _length = _cooldown.get(host, (0, 0))
    return time.time() < until


def throttled():
    """Is any catalogue sitting out a rate limit right now?

    A track that found nothing while one was can't be called a miss.
    """
    return any(_cooling(host) for host in list(_cooldown))


def _back_off(host):
    with _pace_lock:
        _until, length = _cooldown.get(host, (0, 0))
        length = min(length * 2, _COOLDOWN_MAX_S) if length else _COOLDOWN_S
        _cooldown[host] = (time.time() + length, length)


def _paced_get(url, params=None):
    """GET one catalogue, no faster than one request every _GAP_S.

    None when the catalogue couldn't answer (network, rate limit, an error
    body) -- as opposed to answering that it has nothing, which is a result.
    Callers must not remember a None as a miss.
    """
    host = urlparse(url).netloc
    if _cooling(host):
        return None
    with _pace_lock:
        wait = _last_call[0] + _GAP_S - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
    try:
        resp = requests.get(url, params=params, timeout=(5, 10))
        if resp.status_code in (403, 429):
            _back_off(host)
            return None
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if isinstance(data, dict) and data.get("error"):
        # Deezer's quota error (code 4) is a 200 with this body.
        if (data["error"] or {}).get("code") == 4:
            _back_off(host)
        return None
    _cooldown.pop(host, None)
    return data


# Stamped on a stored miss: a miss is only as good as the matching that failed
# to find anything, and misses from before the rules below (or stored when a
# rate limit was mistaken for "not found") deserve one more look.
_MISS_RULES = 2


def _key(artist, title, source="itunes"):
    # Versioned: answers found by substring matching can be another artist's.
    # Keyed per source, because which one is asked first is the user's choice
    # (Settings > Metadata) and a merged answer would outlive that choice.
    # Versioned again now that the catalogue's own page is stored beside the
    # audio: an entry without one can't credit its source.
    return (f"preview3:{source}:{(artist or '').strip().lower()}"
            f":{(title or '').strip().lower()}")


# A guest credit on a title: "Sunday Bed (feat. Valerie Green)". One
# catalogue writes it into the title and another doesn't.
_FEAT_TITLE = re.compile(
    r"\s*(?:[(\[]\s*(?:feat|ft|featuring|with)\b[^)\]]*[)\]]"
    r"|\s-?\s*\b(?:feat|ft|featuring)\b\.?\s.*$)", re.I)
# Bracketed edition noise: "(2006 Remastered Version)", "[Explicit]".
_BRACKETS = re.compile(r"\s*[(\[]([^)\]]*)[)\]]")
# What joins a lead act to the rest of a credit: "Bonobo feat. Joy Crookes",
# "Erykah Badu & The Alchemist".
_LEAD_ACT = re.compile(
    r"\s+(?:(?:feat|ft|featuring|vs)\b\.?\s*|(?:with|&|and|x)\s+)\S.*$", re.I)


def _plain_title(title):
    """A title without its guest credit or bracketed edition words."""
    title = _FEAT_TITLE.sub("", title or "")

    def edition_only(m):
        return "" if names.edition_noise(m.group(1)) else m.group(0)

    return _BRACKETS.sub(edition_only, title).strip() or (title or "")


def _lead_act(artist):
    """The first act named in a credit, or the credit itself."""
    lead = _LEAD_ACT.sub("", artist or "").strip()
    return lead if names.tokens(lead) and lead != (artist or "").strip() else None


def _same_title(title, got_title):
    if names.same_name(title, got_title):
        return True
    mine, theirs = _plain_title(title), _plain_title(got_title)
    if names.same_name(mine, theirs):
        return True
    # "Paper Chains" / "Paperchains": the same words, spaced differently.
    joined = "".join(names.tokens(mine))
    return len(joined) >= 4 and joined == "".join(names.tokens(theirs))


def _same_artist(artist, got_artist):
    if names.same_name(artist, got_artist):
        return True
    # "The Avalanches feat. Prince Paul" is filed under The Avalanches.
    mine, theirs = _lead_act(artist), _lead_act(got_artist)
    return any(names.same_name(a, b)
               for a, b in ((mine, got_artist), (artist, theirs), (mine, theirs))
               if a and b)


def _same_track(artist, title, got_artist, got_title):
    """Is this search result actually the track we asked for?

    Compared word-wise (see app/names.py): a search for a short name used to
    accept anything containing it, which is how "Alex G" matched a track by
    "Alex Gaudino" and played it as the preview for an Alex G record. The
    loosening on top is only for what one catalogue spells and another
    doesn't -- a guest credit, a remaster tag, a space -- never extra words.
    """
    return _same_artist(artist, got_artist) and _same_title(title, got_title)


def _from_itunes(artist, title):
    """{url, page} for a sample, {} for none, None when iTunes couldn't answer."""
    data = _paced_get(ITUNES_SEARCH, {
        "term": f"{artist} {title}",
        "media": "music",
        "entity": "song",
        "limit": 10,
    })
    if data is None:
        return None
    for row in data.get("results") or []:
        if row.get("previewUrl") and _same_track(
            artist, title, row.get("artistName"), row.get("trackName")
        ):
            return {"url": row["previewUrl"], "page": row.get("trackViewUrl")}
    return {}


def _from_deezer(artist, title):
    """{url, page, id} for a sample, {} for none, None when Deezer couldn't answer.

    Its field query is exact to the point of uselessness -- searching
    artist:"Bonobo" track:"Kiara" finds nothing while Deezer plainly has the
    track -- so a plain search follows it, and for a credit with a guest in
    it, one for the lead act alone. All still have to agree on artist *and*
    title, which is what keeps a remix or a different song of the same name
    out (see _same_track).
    """
    queries = [f'artist:"{artist}" track:"{title}"', f"{artist} {title}"]
    lead = _lead_act(artist)
    if lead:
        queries.append(f"{lead} {_plain_title(title)}")
    failed = False
    for query in queries:
        data = _paced_get(DEEZER_SEARCH, {"q": query, "limit": 10})
        if data is None:
            failed = True
            continue
        for row in data.get("data") or []:
            if row.get("preview") and _same_track(
                artist, title, (row.get("artist") or {}).get("name"), row.get("title")
            ):
                return {"url": row["preview"], "page": row.get("link"),
                        "id": row.get("id")}
    # Nothing found, but a search that never got an answer might have: that's
    # not a miss worth remembering.
    return None if failed else {}


# Deezer signs each sample URL for fifteen minutes ("hdnea=exp=<epoch>"), so a
# stored one goes dead long before the entry does. It's re-signed from the
# track id when it's this close to expiring.
_EXPIRY = re.compile(r"\bexp=(\d+)")
_EXPIRY_MARGIN_S = 120


def _expired(url):
    found = _EXPIRY.search(url or "")
    return bool(found) and int(found.group(1)) - time.time() < _EXPIRY_MARGIN_S


def _resign(stored):
    """A freshly signed URL for a stored Deezer sample, or None."""
    if not stored.get("id"):
        return None
    data = _paced_get(DEEZER_TRACK.format(int(stored["id"])))
    return (data or {}).get("preview") or None


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
    if cached and cached.get("url"):
        if not _expired(cached["url"]):
            return cached["url"]
        if cached_only:
            return None
        url = _resign(cached)
        if url:
            db.set_json_cache(key, {**cached, "url": url})
            return url
        # No id stored (an older entry), or the track has gone: search again.
        cached = None
    # A stored {"url": null} is a remembered miss: honour it only while it's
    # inside the (much shorter) miss TTL, then look again.
    if (cached is not None and cached.get("rules") == _MISS_RULES
            and db.get_json_cache(key, max_age=db.cache_max_age("miss")) is not None):
        return None
    if cached_only:
        return None
    lookup = _from_itunes if source == "itunes" else _from_deezer
    found = lookup(artist, title)
    if found is None:
        # The catalogue didn't answer: ask again next time, don't remember it.
        return None
    db.set_json_cache(key, {"url": found.get("url"), "page": found.get("page"),
                            "id": found.get("id"), "rules": _MISS_RULES})
    return found.get("url")


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
