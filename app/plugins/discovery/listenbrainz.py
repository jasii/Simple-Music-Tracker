"""Discover source: ListenBrainz Fresh Releases.

ListenBrainz publishes every new and upcoming release MusicBrainz knows about
(https://api.listenbrainz.org/1/explore/fresh-releases/), each with its
release-group id -- so album pages, tracklists and playback work exactly as
they do for followed artists. It's thousands of records a fortnight, nearly
all of them nobody's business, so this source keeps only:

- releases by artists on your Similar Artists ranking (similar to what you own);
- releases carrying one of the tags you list;
- with a ListenBrainz username, the fresh releases ListenBrainz picks for that
  account from its listening history (it can import a Last.fm history).

The same feed also drives "New from your library" (librarynew.py).
"""

import time
from datetime import date

from ... import db
from . import DiscoveryPlugin, register
from .common import cached_scrape, get, norm_type

API = "https://api.listenbrainz.org/1"
SOURCE = "listenbrainz"
# The feed is big (a couple of MB) and both sources read it: fetched once
# per this long and shared.
_FEED_TTL = 6 * 3600
# ListenBrainz caps the window at 90 days either side.
_MAX_DAYS = 90
_TYPES = ("Album", "EP", "Single")


def _days(setting, default):
    try:
        return max(1, min(int(db.get_setting(setting) or default), _MAX_DAYS))
    except (TypeError, ValueError):
        return default


def fresh_releases(days):
    """Every fresh release *days* back and ahead (shared, cached a few hours)."""
    key = f"lbfresh:{days}"
    cached = db.get_json_cache(key, max_age=_FEED_TTL)
    if cached is not None:
        return cached
    data = get(f"{API}/explore/fresh-releases/",
               params={"days": days, "past": "true", "future": "true", "sort": "release_date"},
               timeout=60).json()
    releases = ((data or {}).get("payload") or {}).get("releases") or []
    db.set_json_cache(key, releases)
    return releases


def user_releases(user, days):
    """The fresh releases ListenBrainz recommends to *user*."""
    data = get(f"{API}/user/{user}/fresh_releases",
               params={"days": days, "past": "true", "future": "true"}, timeout=60).json()
    return ((data or {}).get("payload") or {}).get("releases") or []


def to_item(rel, context):
    """A Discover item from one ListenBrainz release."""
    rg = rel.get("release_group_mbid")
    artists = rel.get("artist_mbids") or []
    image = None
    if rel.get("caa_id") and rel.get("caa_release_mbid"):
        image = (f"https://coverartarchive.org/release/{rel['caa_release_mbid']}/"
                 f"{rel['caa_id']}-500.jpg")
    return {
        "artist": rel.get("artist_credit_name"),
        "artist_url": f"https://musicbrainz.org/artist/{artists[0]}" if artists else None,
        "album": rel.get("release_name"),
        "album_url": f"https://musicbrainz.org/release-group/{rg}" if rg else None,
        "release_date": rel.get("release_date"),
        "normalized_date": (rel.get("release_date") or "")[:10] or None,
        "primary_type": norm_type(rel.get("release_group_primary_type")),
        "image": image,
        "genres": [t for t in (rel.get("release_tags") or [])][:3],
        "context": context,
        "mbid": rg,
    }


def keep(rel):
    """A record worth a row: an album, EP or single, not a compilation."""
    return (rel.get("release_group_primary_type") in _TYPES
            and not rel.get("release_group_secondary_type"))


def _similar_sources():
    """lower name -> the owned artists they're similar to, most matches first."""
    return {r["name"].lower(): r["sources"] for r in db.similar_artist_rankings()}


def _tags():
    raw = db.get_setting("discover_listenbrainz_tags") or ""
    return {t.strip().lower() for t in raw.split(",") if t.strip()}


def scrape():
    days = _days("discover_listenbrainz_days", 14)
    user = (db.get_setting("discover_listenbrainz_user") or "").strip()
    similar = _similar_sources()
    tags = _tags()
    items = []
    seen = set()

    def add(rel, context):
        rg = rel.get("release_group_mbid")
        if not keep(rel) or not rg or rg in seen:
            return
        seen.add(rg)
        items.append(to_item(rel, context))

    if user:
        # Most confident first: it's ListenBrainz's own ranking.
        for rel in sorted(user_releases(user, days), key=lambda r: -(r.get("confidence") or 0)):
            add(rel, "Picked for you by ListenBrainz")
    for rel in fresh_releases(days):
        name = (rel.get("artist_credit_name") or "").lower()
        if name in similar:
            names = similar[name]
            more = f" and {len(names) - 2} more" if len(names) > 2 else ""
            add(rel, "Similar to " + ", ".join(names[:2]) + more)
            continue
        hits = tags & {t.lower() for t in rel.get("release_tags") or []}
        if hits:
            add(rel, "Tagged " + ", ".join(sorted(hits)))
    return items


class ListenBrainzDiscovery(DiscoveryPlugin):
    key = SOURCE
    label = "ListenBrainz"
    icon = "listenbrainz"
    group = "Release calendars"
    description = (
        "New and upcoming releases from MusicBrainz, via ListenBrainz: the ones by "
        "artists similar to yours (your Similar Artists list), tagged with genres you "
        "name, or picked for your ListenBrainz account."
    )
    enabled_setting = "discover_listenbrainz_enabled"
    config_fields = [
        {"key": "discover_listenbrainz_user", "label": "ListenBrainz username",
         "placeholder": "optional",
         "help": "Adds the fresh releases ListenBrainz picks from your listening. "
                 "ListenBrainz can import your Last.fm history."},
        {"key": "discover_listenbrainz_tags", "label": "Tags",
         "placeholder": "post-punk, shoegaze, ambient",
         "help": "Also include releases tagged with any of these (comma separated)."},
        {"key": "discover_listenbrainz_days", "label": "Days back and ahead",
         "placeholder": "14", "help": "How wide a window to read (up to 90)."},
    ]

    def fetch(self, force=False):
        # Covers, types and tags come with the feed: nothing to look up.
        return cached_scrape(self.key, force, scrape, enrich_items=False)


register(ListenBrainzDiscovery())
