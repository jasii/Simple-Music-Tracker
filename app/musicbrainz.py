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

# Release-group types we care about for "new albums".
WANTED_PRIMARY_TYPES = {"Album", "EP"}

# How far back a release still counts as "new" when surfacing it.
RECENT_WINDOW_DAYS = 30

_rate_lock = threading.Lock()
_last_request = [0.0]


def _user_agent():
    contact = db.get_setting("musicbrainz_contact") or "https://github.com/jasii/simple-music-tracker"
    return f"SimpleMusicTracker/1.0 ( {contact} )"


def _rate_limited_get(url, params=None):
    """GET with a global >=1.1s gap between MusicBrainz requests."""
    with _rate_lock:
        elapsed = time.time() - _last_request[0]
        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)
        try:
            resp = requests.get(
                url,
                params=params,
                headers={"User-Agent": _user_agent(), "Accept": "application/json"},
                timeout=20,
            )
        finally:
            _last_request[0] = time.time()
    if resp.status_code == 503:
        # Service busy -- back off once and retry.
        time.sleep(2)
        return _rate_limited_get(url, params)
    resp.raise_for_status()
    return resp.json()


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


def fetch_release_groups(mbid):
    """Return release-groups for an artist MBID, paging through results."""
    groups = []
    offset = 0
    while True:
        data = _rate_limited_get(
            f"{MB_BASE}/release-group",
            {
                "artist": mbid,
                "type": "album|ep",
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


def find_upcoming(name, mbid=None):
    """Resolve an artist and return a list of upcoming/recent release dicts.

    Each dict: {mbid, title, release_date, primary_type, image_url}.
    """
    if not mbid:
        mbid = resolve_mbid(name)
    if not mbid:
        return mbid, []

    cutoff = date.today() - timedelta(days=RECENT_WINDOW_DAYS)
    results = []
    for rg in fetch_release_groups(mbid):
        primary = rg.get("primary-type")
        if primary not in WANTED_PRIMARY_TYPES:
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
