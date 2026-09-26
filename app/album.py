"""Album detail for the upcoming/discover pages: tracklist, cover, previews.

MusicBrainz is the source of record: the tracklist is its release's, and the
cover is the Cover Art Archive's front image. Last.fm and iTunes are
enrichment -- a page link per track, a thirty-second preview, a cover when the
archive has none -- and only supply the tracklist themselves for a release
MusicBrainz was never asked about (a Discover row with no release-group id).

Results are cached in the DB (json_cache) so these often-read pages don't hit
any of the three on every view, and survive a restart.
"""

import re
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from . import db, exclusives, gaps, lastfm, metadata, musicbrainz, names
from .plugins import metadata as metadata_plugins

ITUNES_SEARCH = "https://itunes.apple.com/search"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Tracklists/previews/cover change rarely once a record exists; cache in the DB
# (survives restarts) and only re-fetch when older than this.
# A released album's tracklist and cover don't change, so how long this is
# kept is store_keep_days (0 = forever); a caller that needs it fresh anyway
# passes force=True (the page's refresh button).


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

    tracks = []
    for r in results:
        name = r.get("trackName")
        if not name:
            continue
        # iTunes search is a free-text search, so a release it doesn't carry
        # comes back as a pile of other people's songs that happen to share a
        # word. Both the artist and the album have to match, or it isn't this
        # album's tracklist.
        # Word-wise, not substring: "Alex G" is not "Alex Gaudino", and
        # "High" is not "High Times" (see app/names.py).
        if not names.same_name(artist, r.get("artistName")):
            continue
        millis = r.get("trackTimeMillis")
        tracks.append({
            "name": name,
            "preview_url": r.get("previewUrl"),
            "duration": int(millis / 1000) if millis else None,
            "url": r.get("trackViewUrl"),
            "_album_match": names.same_name(album, r.get("collectionName")),
        })

    # No result from this album: iTunes hasn't got it, and the artist's other
    # songs are not a tracklist for it.
    chosen = [t for t in tracks if t["_album_match"]]
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


# Permanent per-release-group art override. Once a working cover is resolved for
# a release-group, the discography reuses it directly (no CAA/Last.fm round-trip
# on later views, and it survives discography re-fetches since it's keyed by the
# release-group, not the artist's cached list).
def _art_override_key(rg_mbid):
    return "artrg:" + rg_mbid


def remember_release_art(rg_mbid, image_url):
    """Persist a resolved cover for a release-group so the discography reuses it."""
    if rg_mbid and image_url:
        db.set_json_cache(_art_override_key(rg_mbid), {"image_url": image_url})


def release_art_overrides(rg_mbids):
    """Batch get_release_art_override: mbid -> image_url for remembered covers.

    Lets list endpoints (upcoming, calendar) swap the seeded CAA URLs -- which
    404 for release-groups without a front image -- for covers the resolver
    already found, in one DB query instead of one per row.
    """
    keyed = {_art_override_key(m): m for m in rg_mbids if m}
    out = {}
    for key, entry in db.get_json_cache_many(list(keyed)).items():
        img = (entry or {}).get("image_url")
        if img:
            out[keyed[key]] = img
    return out


def get_release_art_override(rg_mbid):
    """Return a previously-resolved cover URL for a release-group, or None."""
    if not rg_mbid:
        return None
    entry = db.get_json_cache(_art_override_key(rg_mbid))  # no max_age: permanent
    return entry.get("image_url") if entry else None


_LABEL_TO_KEY = {"Album": "album", "EP": "ep", "Single": "single"}


def group_discography(artist_id, items, src_labels=None):
    """Group MusicBrainz discography *items* by release type and flag ownership.

    Shared by the discography endpoint (which renders the groups) and the
    background refresh (which only keeps the counts). Mutates each item in place
    to set ``owned``/``owned_sources`` and a cached art override, then returns
    ``(groups, owned_counts)`` where both are keyed album/ep/single.

    *src_labels* maps a source key to a human label for ``owned_sources``; when
    omitted the raw key is used (the background path discards owned_sources).
    """
    src_labels = src_labels or {}
    # One read of what the library holds, plus the manual matches, and every
    # release group is answered from it.
    owned_rows, links = db.owned_index(artist_id)
    alt_index = db.alt_key_index(owned_rows)
    # Opt-in: an EP or single whose every song is already on an album you own
    # counts as owned too, rather than nagging as a gap forever.
    covered = exclusives.covered_keys(artist_id) if exclusives.covered_enabled() else set()
    # One query for every remembered cover, not one per release-group: a big
    # discography is 1000+ items, and this also runs for every artist in a
    # background refresh.
    overrides = release_art_overrides([it.get("mbid") for it in items])
    groups = {"album": [], "ep": [], "single": []}
    owned_counts = {"album": 0, "ep": 0, "single": 0}
    for item in items:
        # Reuse a previously-resolved cover (Last.fm) instead of the seeded CAA
        # URL that 404s, so warmed art loads straight from cache with no lookup.
        override = overrides.get(item.get("mbid"))
        if override:
            item["image_url"] = override
        matched, sources, formats = db.match_owned(
            owned_rows, links, item.get("mbid"), item.get("title"), alt_index
        )
        item["owned"] = bool(sources)
        # Which library albums answered for this release, so the page can show
        # (and undo) what it matched to.
        item["matched_albums"] = sorted(matched)
        item["owned_covered"] = False
        if not item["owned"] and covered and exclusives.release_key(item) in covered:
            item["owned"] = True
            item["owned_covered"] = True
        item["owned_sources"] = [
            {"key": s, "label": src_labels.get(s, s)} for s in sorted(sources)
        ]
        # Every quality you hold it in, best first, for the badges on the row.
        held = gaps.ranked_formats(formats)
        item["owned_formats"] = held
        item["owned_format"] = held[0] if held else None
        key = _LABEL_TO_KEY.get(item["primary_type"])
        if key:
            groups[key].append(item)
            if item["owned"]:
                owned_counts[key] += 1
    for key in groups:
        groups[key].sort(key=lambda r: r["release_date"] or "", reverse=True)
    return groups, owned_counts


def resolve_album_art(artist, title, mbid=None):
    """Best cover-art URL for a release: Last.fm first, Cover Art Archive fallback.

    The discography list seeds each item with an *unverified* CAA URL
    (musicbrainz.cover_art_url), which 404s for the many release-groups that have
    no front image. This resolves the same Last.fm image the album page uses, so
    a failed CAA cover can fall back to a working one. Cached in the DB, and the
    resolved URL is remembered per release-group so later discography views skip
    the lookup entirely.
    """
    key = "albumart:" + (artist or "").lower() + "|" + (title or "").lower()
    cached = db.get_json_cache(key, max_age=db.cache_max_age("hit"))
    if cached is not None:
        # Backfill the per-release-group override for albums resolved before the
        # override existed (skip a CAA fallback, which is the URL that failed).
        img = cached.get("image_url")
        if img and mbid and img != musicbrainz.cover_art_url(mbid) \
                and not get_release_art_override(mbid):
            remember_release_art(mbid, img)
        return cached

    # The metadata sources in order (Settings > Metadata); the archive answers
    # with a confirmed cover or nothing.
    image = metadata.album_art(artist, title, mbid)
    if image:
        remember_release_art(mbid, image)
    elif mbid:
        # Nothing confirmed: fall back to the archive's derived URL, which is
        # the one that 404s -- so it's offered but never remembered.
        image = musicbrainz.cover_art_url(mbid)

    result = {"image_url": image}
    if image:
        db.set_json_cache(key, result)
    return result


def _caa_cover(mbid):
    """The Cover Art Archive's confirmed cover for a release group, or None.

    Asked through its metadata plugin (so the HEAD check lives in one place)
    but called directly here: the album page runs it alongside the MusicBrainz
    and catalogue lookups, and hands the answer to the facade afterwards.
    """
    plugin = metadata_plugins.get_plugin("metadata", "coverartarchive")
    if plugin is None or not plugin.configured():
        return None
    return plugin.album_art(None, None, mbid)


def get_album_detail(artist, title, mbid=None, force=False):
    """Return {artist, title, image, lastfm_url, tracks, source}.

    MusicBrainz is the source: its tracklist is the tracklist, and the Cover
    Art Archive's front image is the cover. Last.fm and iTunes only enrich what
    it says -- a page link per track, a thirty-second preview, a cover when the
    archive hasn't got one. They become the tracklist only when MusicBrainz
    can't be asked (nothing passed a release-group id) or has no tracks for the
    release, which is how a Discover row with no MBID still reads properly.

    Each track: {name, duration, url, preview_url}.
    """
    # Versioned: entries stored before MusicBrainz became the source hold a
    # catalogue's tracklist, and ones from before names were compared word-wise
    # can hold another artist's previews. Both are left behind, not served.
    key = "album4:" + (artist or "").lower() + "|" + (title or "").lower()
    if not force:
        cached = db.get_json_cache(key, max_age=db.cache_max_age("hit"))
        if cached:
            return cached

    # One resolution per release, shared. Opening an album page asks for the
    # detail and for its playability at once and both need the tracklist, so
    # without this a first view does the whole MusicBrainz, Last.fm and iTunes
    # round twice -- at one MusicBrainz request per second. The second caller
    # waits for the first and gets its answer, including when the answer turns
    # out to be too empty to cache.
    with _detail_guard:
        flight = _detail_flights.get(key)
        leader = flight is None
        if leader:
            flight = _detail_flights[key] = {"done": threading.Event(), "data": None}
    if not leader:
        if flight["done"].wait(timeout=_FLIGHT_WAIT_S) and flight["data"] is not None:
            return flight["data"]
        # The leader failed or is taking too long: ask for ourselves rather
        # than hand the page an empty tracklist.
        return _resolve_album_detail(key, artist, title, mbid)
    try:
        flight["data"] = _resolve_album_detail(key, artist, title, mbid)
        return flight["data"]
    finally:
        flight["done"].set()
        with _detail_guard:
            _detail_flights.pop(key, None)


# Resolutions in flight, so two callers asking for the same release share one.
_detail_flights = {}
_detail_guard = threading.Lock()
# Long enough for a cold release (MusicBrainz, two catalogues, a HEAD), short
# enough that a wedged lookup doesn't hold a page open.
_FLIGHT_WAIT_S = 25


def _resolve_album_detail(key, artist, title, mbid):
    """The uncached half of get_album_detail: ask everyone, store, return."""
    # Three independent lookups -- MusicBrainz for the truth, the two
    # catalogues for what they can add -- run together, so a first view waits
    # for the slowest rather than the sum.
    with ThreadPoolExecutor(max_workers=4) as pool:
        mb_job = pool.submit(musicbrainz.release_tracks, mbid) if mbid else None
        cover_job = pool.submit(_caa_cover, mbid) if mbid else None
        lf_job = pool.submit(lambda: lastfm.get_album_info(artist, title) or {})
        itunes_job = pool.submit(_itunes_tracks, artist, title)
        try:
            mb_tracks = mb_job.result() if mb_job else []
        except Exception:  # noqa: BLE001 - fall back to the catalogues
            mb_tracks = []
        try:
            archive_cover = cover_job.result() if cover_job else None
        except Exception:  # noqa: BLE001 - no cover is not an error
            archive_cover = None
        try:
            lf = lf_job.result() or {}
        except Exception:  # noqa: BLE001
            lf = {}
        try:
            itunes = itunes_job.result()
        except Exception:  # noqa: BLE001
            itunes = []
    previews = {_norm(t["name"]): t for t in itunes if t.get("preview_url")}
    lf_tracks = lf.get("tracks") or []
    lf_by_name = {_norm(t["name"]): t for t in lf_tracks if t.get("name")}

    if mb_tracks:
        source = "musicbrainz"
        tracks = []
        for t in mb_tracks:
            norm = _norm(t["name"])
            sample = previews.get(norm) or {}
            page = lf_by_name.get(norm) or {}
            tracks.append({
                "name": t["name"],
                "duration": t.get("duration") or page.get("duration"),
                "url": page.get("url") or (sample or {}).get("url"),
                "preview_url": sample.get("preview_url"),
            })
    elif lf_tracks:
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
    elif itunes:
        source = "itunes"
        tracks = [
            {"name": t["name"], "duration": t.get("duration"),
             "url": t.get("url"), "preview_url": t.get("preview_url")}
            for t in itunes
        ]
    else:
        source = None
        tracks = []

    # Which cover wins follows the album-art order in Settings > Metadata
    # (the archive first out of the box: it's the release's own sleeve and it
    # serves sizes up to 1200px). The two answers already in hand are passed
    # in, so a source is never asked twice for the same page.
    image = metadata.album_art(artist, title, mbid, known={
        "coverartarchive": archive_cover,
        "lastfm": lf.get("image_url"),
    })
    # Anything but the archive's derived URL is worth remembering per release
    # group, so the discography stops asking.
    if image and image != archive_cover:
        remember_release_art(mbid, image)

    data = {
        "artist": artist,
        "title": title,
        "image": image,
        "lastfm_url": lf.get("lastfm_url"),
        "tracks": tracks,
        "source": source,
    }
    # Only cache once there's something worth keeping, so a transient API failure
    # doesn't pin an empty tracklist for two weeks.
    if tracks or image:
        db.set_json_cache(key, data)
    return data
