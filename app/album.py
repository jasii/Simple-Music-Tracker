"""Album detail for the upcoming/discover pages: tracklist + audio previews.

Tracklist comes from Last.fm (album.getInfo). Last.fm serves no audio, so 30s
previews are pulled from the iTunes Search API (free, no key) and matched to the
tracklist by name. If Last.fm has nothing, the iTunes tracklist is used directly.

Results are cached in memory for a while - these pages are read often and the
data barely changes.
"""

import re
import threading
import time

import requests

from . import lastfm, musicbrainz

ITUNES_SEARCH = "https://itunes.apple.com/search"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_CACHE_TTL = 6 * 3600
_cache = {}
_lock = threading.Lock()


def _norm(name):
    """Loose track-name key for matching across sources."""
    name = (name or "").lower()
    name = re.sub(r"\(feat[^)]*\)|\bfeat\.?\b.*", "", name)  # drop "(feat. ...)"
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return name.strip()


def _itunes_tracks(artist, album):
    """Return iTunes song results for an album: list of {name, preview_url, duration, url}."""
    try:
        resp = requests.get(
            ITUNES_SEARCH,
            params={
                "term": f"{artist} {album}",
                "media": "music",
                "entity": "song",
                "limit": 50,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except (requests.RequestException, ValueError):
        return []

    album_norm = _norm(album)
    tracks = []
    for r in results:
        name = r.get("trackName")
        if not name:
            continue
        # Keep tracks from the matching album when we can tell; iTunes search is
        # fuzzy, so fall back to all song results if nothing matches the album.
        collection = _norm(r.get("collectionName"))
        millis = r.get("trackTimeMillis")
        tracks.append({
            "name": name,
            "preview_url": r.get("previewUrl"),
            "duration": int(millis / 1000) if millis else None,
            "url": r.get("trackViewUrl"),
            "_album_match": bool(album_norm and album_norm in collection),
        })

    matched = [t for t in tracks if t["_album_match"]]
    chosen = matched or tracks
    # Drop duplicates (deluxe/multi-disc editions repeat titles); keep first seen.
    seen = set()
    deduped = []
    for t in chosen:
        k = _norm(t["name"])
        if k in seen:
            continue
        seen.add(k)
        t.pop("_album_match", None)
        deduped.append(t)
    return deduped


def get_album_detail(artist, title, mbid=None, force=False):
    """Return {artist, title, image, lastfm_url, tracks, source}.

    Each track: {name, duration, url, preview_url}. Best-effort; tracks may be
    empty for not-yet-released albums.
    """
    key = ((artist or "").lower(), (title or "").lower())
    if not force:
        with _lock:
            hit = _cache.get(key)
            if hit and (time.time() - hit["at"]) < _CACHE_TTL:
                return hit["data"]

    lf = {}
    try:
        lf = lastfm.get_album_info(artist, title) or {}
    except Exception:  # noqa: BLE001
        pass

    itunes = _itunes_tracks(artist, title)
    previews = {_norm(t["name"]): t for t in itunes if t.get("preview_url")}

    lf_tracks = lf.get("tracks") or []
    if lf_tracks:
        source = "lastfm"
        tracks = []
        for t in lf_tracks:
            match = previews.get(_norm(t["name"]))
            tracks.append({
                "name": t["name"],
                "duration": t.get("duration"),
                "url": t.get("url"),
                "preview_url": match.get("preview_url") if match else None,
            })
    else:
        source = "itunes" if itunes else None
        tracks = [
            {"name": t["name"], "duration": t.get("duration"),
             "url": t.get("url"), "preview_url": t.get("preview_url")}
            for t in itunes
        ]

    image = lf.get("image_url")
    if not image and mbid:
        image = musicbrainz.cover_art_url(mbid)

    data = {
        "artist": artist,
        "title": title,
        "image": image,
        "lastfm_url": lf.get("lastfm_url"),
        "tracks": tracks,
        "source": source,
    }
    with _lock:
        _cache[key] = {"at": time.time(), "data": data}
    return data
