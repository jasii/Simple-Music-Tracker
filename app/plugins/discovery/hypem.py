"""Discover source: Hype Machine's popular tracks.

Hype Machine follows hundreds of music blogs; its popular list
(https://api.hypem.com/v2/popular) is what they're posting and its listeners
are loving right now, with the blog that posted each. It names songs, not
records, and blogs write about old favourites too -- so each song is dated
before it's kept: by iTunes, which knows the record it's on and when that
came out; failing that, by the album Last.fm puts it on, looked up on iTunes.
Only what's recent stays. A song neither knows is dated by its post, unless
Last.fm shows it's long established (a big listener count).
"""

import re

from datetime import date, datetime, timedelta, timezone

from ... import db, lastfm, names, preview
from . import DiscoveryPlugin, register
from .common import cached_scrape, get

API = "https://api.hypem.com/v2/popular"
SOURCE = "hypem"
# Past this many Last.fm listeners a song isn't new, whatever its post says.
_ESTABLISHED = 250_000
# "(Explicit)", "(ext remix)", "[feat. X]": what a catalogue won't have in the title.
_SUFFIX = re.compile(r"\s*[(\[][^)\]]*\b(?:explicit|clean|remaster\w*|remix|mix|edit|version|feat\.?|ft\.?|with)\b[^)\]]*[)\]]", re.I)


def _months():
    try:
        return max(1, min(int(db.get_setting("discover_hypem_months") or 6), 60))
    except (TypeError, ValueError):
        return 6


def _bare(name):
    """A title without its bracketed extras and edition tags, for matching."""
    name = re.sub(r"\s*[(\[][^)\]]*[)\]]", "", name or "")
    return re.sub(r"\s+-\s+(?:single|ep)$", "", name, flags=re.I).strip()


def _itunes(kind, artist, title):
    """{album, date, image} for a song or album from iTunes' catalogue, or {} (cached)."""
    key = f"hypemit:{kind}:{artist.lower()}|{title.lower()}"
    cached = db.get_json_cache(key, max_age=db.cache_max_age("hit"))
    if cached is not None:
        return cached
    data = preview._paced_get(preview.ITUNES_SEARCH, {
        "term": f"{artist} {title}", "media": "music", "limit": 10,
        "entity": "song" if kind == "song" else "album",
    })
    if data is None:
        return {}  # a failed call isn't worth remembering
    field = "trackName" if kind == "song" else "collectionName"
    out = {}
    for r in data.get("results") or []:
        # "Pink Flag (2018 Remaster)", "m.A.A.d city (feat. MC Eiht)": the
        # same record, whatever the catalogue calls this edition.
        if names.same_name(artist, r.get("artistName")) and (
                names.same_name(title, r.get(field))
                or names.same_name(_bare(title), _bare(r.get(field)))):
            art = r.get("artworkUrl100") or ""
            out = {
                "album": r.get("collectionName"),
                "date": (r.get("releaseDate") or "")[:10] or None,
                # The same artwork at a size worth showing.
                "image": art.replace("100x100bb", "600x600bb") or None,
            }
            break
    db.set_json_cache(key, out)
    return out


def date_song(artist, song):
    """({album, date, image}, established) for a song: when its record came out.

    *established* is True when nothing gives a date but Last.fm shows half
    the world has heard it.
    """
    plain = _SUFFIX.sub("", song).strip() or song
    for title in dict.fromkeys((song, plain)):
        found = _itunes("song", artist, title)
        if found.get("date"):
            return found, False
    fallback = lastfm.track_album(artist, plain)
    if not fallback.get("album") and plain != song:
        fallback = lastfm.track_album(artist, song) or fallback
    if (fallback.get("listeners") or 0) > _ESTABLISHED:
        return {}, True
    album = fallback.get("album")
    if album:
        record = _itunes("album", artist, album)
        if record.get("date"):
            return {"album": album, "date": record["date"],
                    "image": record.get("image") or fallback.get("image_url")}, False
    return {"album": album, "date": None, "image": fallback.get("image_url")}, False


def scrape():
    data = get(API, params={"count": 50}, timeout=30).json()
    tracks = data if isinstance(data, list) else (data or {}).get("tracks") or []
    recent = (date.today() - timedelta(days=30 * _months())).isoformat()
    items = []
    for t in tracks:
        artist = (t.get("artist") or "").strip()
        song = (t.get("title") or "").strip()
        if not artist or not song:
            continue
        posted = None
        if t.get("dateposted"):
            posted = datetime.fromtimestamp(int(t["dateposted"]), timezone.utc).date().isoformat()
        found, established = date_song(artist, song)
        if established or (found.get("date") and found["date"] < recent):
            continue  # a blog remembering an old record, not a new one
        album = found.get("album")
        image = found.get("image")
        # iTunes files singles as "Song - Single".
        single = not album or album.lower().endswith(" - single") or album.lower() == song.lower()
        if album:
            album = album.removesuffix(" - Single").removesuffix(" - EP")
        when = found.get("date") or posted
        why = f"“{song}” posted by {t.get('sitename') or 'a blog'}"
        if t.get("loved_count"):
            why += f" · loved {t['loved_count']} times"
        items.append({
            "artist": artist,
            "album": album or song,
            "album_url": t.get("posturl"),
            "primary_type": "Single" if single else "Album",
            "release_date": when,
            "normalized_date": when,
            "posted": posted,
            "image": image or t.get("thumb_url_large"),
            "context": why,
        })
    return items


class HypeMachineDiscovery(DiscoveryPlugin):
    key = SOURCE
    label = "Hype Machine"
    group = "Radio and blogs"
    description = (
        "New music blogs are posting right now (Hype Machine's popular list), each "
        "with the blog that posted it. Each song is matched to its record and release "
        "date; blog posts about old records are left out."
    )
    enabled_setting = "discover_hypem_enabled"
    config_fields = [
        {"key": "discover_hypem_months", "label": "Released within (months)",
         "placeholder": "6", "help": "Older records blogs write about are left out."},
    ]

    def fetch(self, force=False):
        return cached_scrape(self.key, force, scrape, keep_days=14)


register(HypeMachineDiscovery())
