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

    All callers funnel through here, so only one request is ever in flight and
    consecutive requests are spaced by at least the configured interval. The
    network call happens under the lock; retry sleeps happen *outside* it (the
    lock is not reentrant) so a backoff never blocks other work needlessly.
    """
    error = None
    resp = None
    with _rate_lock:
        elapsed = time.time() - _last_request[0]
        interval = _min_interval()
        if elapsed < interval:
            time.sleep(interval - elapsed)
        try:
            resp = requests.get(
                url,
                params=params,
                headers={"User-Agent": _user_agent(), "Accept": "application/json"},
                timeout=20,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            # Transient connection errors (ECONNRESET/ETIMEDOUT equivalents).
            error = exc
        finally:
            _last_request[0] = time.time()

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


def resolve_mbid(name):
    """Look up an artist MBID by name. Returns the best match or None."""
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


# Short-lived cache of full discographies so revisiting an artist page (or a
# quick reload) doesn't hit MusicBrainz again. Keyed by MBID.
_DISCO_TTL = 900  # seconds (15 minutes)
_disco_cache = {}
_disco_lock = threading.Lock()


def fetch_discography(mbid, use_cache=True):
    """Return every album/EP/single release-group for an artist MBID.

    Each item: {mbid, title, primary_type, release_date, image_url}. Results are
    cached briefly to avoid repeat API calls when navigating around.
    """
    if use_cache:
        with _disco_lock:
            cached = _disco_cache.get(mbid)
            if cached and (time.time() - cached[0]) < _DISCO_TTL:
                return cached[1]

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
                "release_date": rg.get("first-release-date"),
                "image_url": cover_art_url(rg["id"]),
            }
        )

    with _disco_lock:
        _disco_cache[mbid] = (time.time(), items)
    return items


def find_upcoming(name, mbid=None, types=None):
    """Resolve an artist and return a list of upcoming/recent release dicts.

    *types* is a set of lowercase type keys ('album', 'ep', 'single'); only
    matching release-groups are returned. Each dict:
    {mbid, title, release_date, primary_type, image_url}.
    """
    types = {t.lower() for t in (types or DEFAULT_TYPES)}
    wanted_labels = {_TYPE_LABELS[t] for t in types if t in _TYPE_LABELS}

    if not mbid:
        mbid = resolve_mbid(name)
    if not mbid:
        return mbid, []

    cutoff = date.today() - timedelta(days=RECENT_WINDOW_DAYS)
    results = []
    for rg in fetch_release_groups(mbid, types):
        primary = rg.get("primary-type")
        if primary not in wanted_labels:
            continue
        released = _parse_date(rg.get("first-release-date"))
        if released is None or released < cutoff:
            continue
        results.append(
            {
                "mbid": rg["id"],
                "title": rg.get("title", "Untitled"),
                "release_date": rg.get("first-release-date"),
                "primary_type": primary,
                "image_url": cover_art_url(rg["id"]),
            }
        )
    results.sort(key=lambda r: r["release_date"] or "9999")
    return mbid, results
