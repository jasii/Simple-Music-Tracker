"""Upcoming-album discovery via MusicBrainz.

This mirrors the logic aurral uses to find upcoming releases: resolve an
artist to a MusicBrainz artist id (MBID), then list that artist's
release-groups and keep the ones whose first-release-date is in the future
(or very recent). Cover art is pulled from the Cover Art Archive.

MusicBrainz asks for at most one request per second and a descriptive
User-Agent, both of which are enforced here.
"""

import re
import threading
import time
from datetime import date, datetime, timedelta

import requests

from . import db

# Matches a MusicBrainz artist id (UUID), whether pasted raw or inside a URL
# like https://musicbrainz.org/artist/<mbid>.
_MBID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

MB_BASE = "https://musicbrainz.org/ws/2"
CAA_BASE = "https://coverartarchive.org"

# Default release-group types to watch when an artist has no explicit selection.
DEFAULT_TYPES = {"album", "ep"}

# Map our lowercase type keys to MusicBrainz primary-type values.
_TYPE_LABELS = {"album": "Album", "ep": "EP", "single": "Single"}

# How far back a release still counts as "new" when surfacing it.
RECENT_WINDOW_DAYS = 30

# Serialise and pace all MusicBrainz requests so a large library can't trip
# their rate limiting (which would get the instance temporarily blocked).
_rate_lock = threading.Lock()
_last_request = [0.0]


def _min_interval():
    """Minimum seconds between MusicBrainz requests, from settings."""
    try:
        ms = float(db.get_setting("musicbrainz_rate_limit_ms") or 1100)
    except (TypeError, ValueError):
        ms = 1100
    # Never go below MusicBrainz's documented 1 req/sec ceiling.
    return max(ms / 1000.0, 1.0)


def _user_agent():
    contact = db.get_setting("musicbrainz_contact") or "https://github.com/jasii/simple-music-tracker"
    return f"SimpleMusicTracker/1.0 ( {contact} )"


# Status codes worth retrying: rate limiting (429) and transient server errors.
# Mirrors aurral's retry set; 404 (not found) is never retried.
_RETRY_STATUSES = {429, 500, 502, 503, 504}

# Total retries after the first attempt (aurral uses 3).
_MAX_RETRIES = 3


def _backoff_seconds(attempt):
    """Exponential backoff base, matching aurral's 300ms * 2^n schedule."""
    return 0.3 * (2 ** attempt)


def _rate_limited_get(url, params=None, _attempt=1):
    """GET MusicBrainz with global pacing and retries on transient failures.

    All callers funnel through here and are spaced by at least the configured
    interval. The lock only reserves the next time slot; the HTTP call happens
    *outside* it, so one slow/hung request can't block every other MusicBrainz
    lookup behind the lock. ``timeout`` is a (connect, read) pair so a server
    that trickles bytes can't hold a request open indefinitely.
    """
    with _rate_lock:
        interval = _min_interval()
        scheduled = max(time.time(), _last_request[0] + interval)
        _last_request[0] = scheduled
    wait = scheduled - time.time()
    if wait > 0:
        time.sleep(wait)

    error = None
    resp = None
    try:
        resp = requests.get(
            url,
            params=params,
            headers={"User-Agent": _user_agent(), "Accept": "application/json"},
            timeout=(10, 30),
        )
    except (requests.ConnectionError, requests.Timeout) as exc:
        # Transient connection errors (ECONNRESET/ETIMEDOUT equivalents).
        error = exc

    if error is not None:
        if _attempt <= _MAX_RETRIES:
            time.sleep(min(_backoff_seconds(_attempt), 10))
            return _rate_limited_get(url, params, _attempt + 1)
        raise error

    # 429 = rate limited, 5xx = transient server error. Back off and retry.
    if resp.status_code in _RETRY_STATUSES and _attempt <= _MAX_RETRIES:
        retry_after = resp.headers.get("Retry-After")
        try:
            wait = float(retry_after) if retry_after else _backoff_seconds(_attempt)
        except ValueError:
            wait = _backoff_seconds(_attempt)
        time.sleep(min(wait, 60))
        return _rate_limited_get(url, params, _attempt + 1)

    resp.raise_for_status()
    return resp.json()


def _type_filter(types):
    """Build the MusicBrainz 'type' query value from our type keys."""
    keys = [t for t in ("album", "ep", "single") if t in (types or DEFAULT_TYPES)]
    return "|".join(keys) if keys else "album|ep"


# Everything fetched from MusicBrainz is kept: the API is rate limited to one
# request a second, so a lookup repeated on the next refresh is a second nobody
# gets back. Misses are kept too -- an artist MusicBrainz doesn't know is the
# most likely thing to be looked up again and again -- but for less time, since
# they're the ones that might appear later. Both windows are settings
# (store_keep_days / store_miss_days).


def _cached_lookup(key, fetch, ttl=db.FROM_SETTINGS, miss_ttl=db.FROM_SETTINGS):
    """Run *fetch* unless its answer -- including "nothing" -- is already stored."""
    ttl = db.resolve_max_age(ttl, "hit")
    miss_ttl = db.resolve_max_age(miss_ttl, "miss")
    entry = db.get_json_cache(key, max_age=ttl)
    if entry is not None:
        value = entry.get("value")
        if value:
            return value
        # A remembered miss is only trusted inside the shorter window.
        fresh = db.get_json_cache(key, max_age=miss_ttl)
        if fresh is not None:
            return fresh.get("value")
    value = fetch()
    db.set_json_cache(key, {"value": value})
    return value


def resolve_mbid(name):
    """Look up an artist MBID by name. Returns the best match or None."""
    if not name:
        return None
    return _cached_lookup(f"mbsearch:{name.strip().lower()}",
                          lambda: _resolve_mbid_uncached(name))


def _resolve_mbid_uncached(name):
    data = _rate_limited_get(
        f"{MB_BASE}/artist",
        {"query": f'artist:"{name}"', "fmt": "json", "limit": 5},
    )
    artists = data.get("artists", [])
    if not artists:
        return None
    # MusicBrainz returns a score; prefer an exact case-insensitive name match.
    lowered = name.lower()
    for artist in artists:
        if artist.get("name", "").lower() == lowered:
            return artist["id"]
    return artists[0]["id"]


def extract_mbid(text):
    """Pull an artist MBID out of a pasted URL or raw id. Returns None if absent."""
    if not text:
        return None
    match = _MBID_RE.search(text)
    return match.group(0).lower() if match else None


def lookup_artist(mbid):
    """Fetch an artist by MBID. Returns {mbid, name, sort_name} or None."""
    if not mbid:
        return None
    return _cached_lookup(f"mbartist:{mbid}", lambda: _lookup_artist_uncached(mbid))


def _lookup_artist_uncached(mbid):
    try:
        data = _rate_limited_get(f"{MB_BASE}/artist/{mbid}", {"fmt": "json"})
    except requests.HTTPError:
        return None
    name = data.get("name")
    if not name:
        return None
    return {
        "mbid": data.get("id", mbid),
        "name": name,
        "sort_name": data.get("sort-name") or name,
    }


def release_tracks(rg_mbid):
    """Track titles for a release group: [{name, duration}], or [].

    The fallback tracklist for a release the streaming catalogues don't carry
    (a remix EP, a bootleg-ish single): MusicBrainz knows it, since that's
    where the release came from. One browse call, cached.
    """
    if not rg_mbid:
        return []
    # Versioned: lists picked by track count alone could be a bootleg's.
    return _cached_lookup(f"mbrgtracks2:{rg_mbid}",
                          lambda: _release_tracks_uncached(rg_mbid)) or []


def _release_tracks_uncached(rg_mbid):
    try:
        data = _rate_limited_get(f"{MB_BASE}/release", {
            "release-group": rg_mbid,
            "inc": "recordings",
            "limit": 5,
            "fmt": "json",
        })
    except requests.HTTPError:
        return []
    # Several releases (editions) hang off one group, so pick the one whose
    # tracklist is the record as published: an official release first, then the
    # earliest, and only then the longest. Without that a 2018 bootleg wins on
    # track count alone and its extra songs read as the album's.
    best = None
    best_rank = None
    # The shortest official edition, for the incomplete-album check: a copy
    # of the standard edition isn't missing the Japanese bonus track.
    shortest = {}
    for release in data.get("releases") or []:
        tracks = []
        for medium in release.get("media") or []:
            for track in medium.get("tracks") or []:
                name = (track.get("title") or "").strip()
                if not name:
                    continue
                length = track.get("length")
                tracks.append({
                    "name": name,
                    "duration": int(length) // 1000 if length else None,
                })
        if not tracks:
            continue
        official = (release.get("status") or "").lower() == "official"
        rank = (0 if official else 1, release.get("date") or "9999", -len(tracks))
        if best_rank is None or rank < best_rank:
            best, best_rank = tracks, rank
        kind = "official" if official else "any"
        shortest[kind] = min(shortest.get(kind, len(tracks)), len(tracks))
    if data.get("releases") is not None:
        db.set_json_cache(_MIN_PREFIX + rg_mbid,
                          {"value": shortest.get("official") or shortest.get("any") or 0})
    return best or []


# Written alongside a tracklist by _release_tracks_uncached.
_MIN_PREFIX = "mbrgmin:"


def min_track_count(rg_mbid, fetch=True):
    """Tracks on the shortest official edition of a release group (0 = unknown).

    What "complete" means for an album in the library: editions add bonus
    tracks, never take them away, so owning fewer than the shortest edition is
    the only sure sign of a copy that stopped short. With *fetch* False only a
    stored answer is returned (None when there isn't one).
    """
    if not rg_mbid:
        return 0
    cached = db.get_json_cache(_MIN_PREFIX + rg_mbid)
    if cached is not None:
        return cached.get("value") or 0
    if not fetch:
        return None
    _release_tracks_uncached(rg_mbid)
    cached = db.get_json_cache(_MIN_PREFIX + rg_mbid)
    return (cached or {}).get("value") or 0


def _parse_date(value):
    """Parse a possibly-partial MusicBrainz date into a date object."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def fetch_release_groups(mbid, types=None):
    """Return release-groups for an artist MBID, paging through results."""
    groups = []
    offset = 0
    type_filter = _type_filter(types)
    while True:
        data = _rate_limited_get(
            f"{MB_BASE}/release-group",
            {
                "artist": mbid,
                "type": type_filter,
                "fmt": "json",
                "limit": 100,
                "offset": offset,
            },
        )
        batch = data.get("release-groups", [])
        groups.extend(batch)
        total = data.get("release-group-count", len(groups))
        offset += len(batch)
        if not batch or offset >= total:
            break
    return groups


def cover_art_url(release_group_mbid):
    """Return a Cover Art Archive front-image URL (not verified to exist)."""
    return f"{CAA_BASE}/release-group/{release_group_mbid}/front-250"


# Persisted in the DB (json_cache) so an artist's full discography survives
# restarts and isn't re-fetched from MusicBrainz on every page view. Refreshed
# when older than this, or on demand via fetch_discography(force=True).
# A page serves whatever was stored, as old as store_keep_days allows (0 =
# forever). The refresh cycle passes its own max_age (check_interval_hours),
# which is what actually goes looking for releases added since.


def _disco_key(mbid):
    return "disco:" + mbid


def cached_discography(mbid):
    """The stored discography for an artist, or None -- never a live fetch.

    For callers that want the release types if they're already known but must
    not wait out a rate-limited walk of MusicBrainz to find out (an album page
    opened cold, say).
    """
    if not mbid:
        return None
    cached = db.get_json_cache(_disco_key(mbid))
    if cached is None:
        return None
    return [_with_cover(item) for item in cached]


def invalidate_discography(mbid):
    """Drop an artist's cached discography so the next view re-fetches it."""
    if mbid:
        db.delete_json_cache(_disco_key(mbid))


def fetch_discography(mbid, use_cache=True, force=False, max_age=None):
    """Return every album/EP/single release-group for an artist MBID.

    Each item: {mbid, title, primary_type, release_date, image_url}. Results are
    cached in the DB so revisiting an artist page (even after a restart) doesn't
    re-hit MusicBrainz until the cache goes stale. *max_age* (seconds) lets a
    caller that needs fresher data than the default TTL say so.
    """
    if use_cache and not force:
        cached = db.get_json_cache(
            _disco_key(mbid),
            max_age=db.cache_max_age("hit") if max_age is None else max_age
        )
        if cached is not None:
            return [_with_cover(item) for item in cached]

    items = []
    for rg in fetch_release_groups(mbid, {"album", "ep", "single"}):
        primary = rg.get("primary-type")
        if primary not in ("Album", "EP", "Single"):
            continue
        items.append(
            {
                "mbid": rg["id"],
                "title": rg.get("title", "Untitled"),
                "primary_type": primary,
                # Compilation / Live / Remix / ... -- what separates a new
                # record from the fortieth reissue of an old one.
                "secondary_types": rg.get("secondary-types") or [],
                "release_date": rg.get("first-release-date"),
                # The cover URL is a pure function of the id (see
                # cover_art_url), so it's rebuilt on read rather than stored:
                # at ~350k cached release-groups it was 29MB of duplication.
            }
        )

    db.set_json_cache(_disco_key(mbid), items)
    return [_with_cover(item) for item in items]


def _with_cover(item):
    """Add the derived cover-art URL back onto a cached discography item."""
    if item.get("image_url") or not item.get("mbid"):
        return item
    return {**item, "image_url": cover_art_url(item["mbid"])}


def find_release_art(name, title, mbid=None):
    """Best-effort cover art + genres for a named release.

    Resolves the artist, finds the release-group whose title matches *title*
    (case-insensitive), and returns {image_url, genres}. Genres come from the
    release-group's MusicBrainz genres (community-voted). Returns {} if nothing
    matches. Used as a fallback when Last.fm has no album data.
    """
    if not name or not title:
        return {}
    key = f"mbrelart:{name.strip().lower()}|{title.strip().lower()}"
    return _cached_lookup(key, lambda: _find_release_art_uncached(name, title, mbid)) or {}


def _find_release_art_uncached(name, title, mbid=None):
    try:
        if not mbid:
            mbid = resolve_mbid(name)
        if not mbid:
            return {}
        wanted = title.strip().lower()
        match = None
        for rg in fetch_release_groups(mbid, {"album", "ep"}):
            if (rg.get("title") or "").strip().lower() == wanted:
                match = rg
                break
        if match is None:
            return {}
        # Re-fetch the release-group with genres included (browse omits them).
        detail = _rate_limited_get(
            f"{MB_BASE}/release-group/{match['id']}", {"fmt": "json", "inc": "genres"}
        )
        ranked = sorted(
            (g for g in (detail.get("genres") or []) if g.get("name")),
            key=lambda g: g.get("count", 0),
            reverse=True,
        )
        genres = [g["name"] for g in ranked]
        return {"image_url": cover_art_url(match["id"]), "genres": genres}
    except requests.RequestException:
        return {}


def find_upcoming(name, mbid=None, types=None, max_age=None):
    """Resolve an artist and return a list of upcoming/recent release dicts.

    *types* is a set of lowercase type keys ('album', 'ep', 'single'); only
    matching release-groups are returned. Each dict:
    {mbid, title, release_date, primary_type, image_url}.

    Reads the artist's cached discography rather than paging MusicBrainz again:
    it is the same browse request, so a refresh (which also syncs discography
    counts) and an artist-page view now share one fetch instead of walking the
    release-group list twice. *max_age* is how stale that cache may be before
    it's re-fetched -- a refresh passes its own check interval.
    """
    types = {t.lower() for t in (types or DEFAULT_TYPES)}
    wanted_labels = {_TYPE_LABELS[t] for t in types if t in _TYPE_LABELS}

    if not mbid:
        mbid = resolve_mbid(name)
    if not mbid:
        return mbid, []

    cutoff = date.today() - timedelta(days=RECENT_WINDOW_DAYS)
    results = []
    for item in fetch_discography(mbid, max_age=max_age):
        if item.get("primary_type") not in wanted_labels:
            continue
        released = _parse_date(item.get("release_date"))
        if released is None or released < cutoff:
            continue
        results.append(dict(item))
    results.sort(key=lambda r: r["release_date"] or "9999")
    return mbid, results
