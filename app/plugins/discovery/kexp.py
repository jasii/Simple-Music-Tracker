"""Discover source: KEXP radio plays.

KEXP (Seattle) publishes every song it airs (https://api.kexp.org/v2/plays/)
with the album, its release date, its MusicBrainz release group, a cover, and
the station's rotation for it (Heavy / Medium / Light / R/N for new music).
What's kept is what the station is really behind: an album in heavy
rotation, or one it has played several times this week (in any rotation, or
out in the last few months). A week of KEXP is well over a thousand records,
most of them played once.
"""

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from ... import db
from . import DiscoveryPlugin, register
from .common import cached_scrape, get

API = "https://api.kexp.org/v2/plays/"
SOURCE = "kexp"
# Enough pages for about a week of airplay at 200 plays each.
_MAX_PAGES = 15
_ROTATIONS = ("Heavy", "Medium", "Light", "R/N", "R")
# A guest on the song, not an author of the record: "Bonobo feat. Joy Crookes"
# is a Bonobo album, and no catalogue files it under the full credit.
_GUEST = re.compile(r"\s+(?:feat|ft|featuring)\b\.?\s*\S.*$", re.I)


def _album_artist(credit):
    return _GUEST.sub("", credit or "").strip() or credit


def _int_setting(key, default, low, high):
    try:
        return max(low, min(int(db.get_setting(key) or default), high))
    except (TypeError, ValueError):
        return default


def scrape():
    days = _int_setting("discover_kexp_days", 7, 1, 14)
    fresh_months = _int_setting("discover_kexp_months", 3, 1, 24)
    min_plays = _int_setting("discover_kexp_min_plays", 3, 1, 20)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent = (date.today() - timedelta(days=30 * fresh_months)).isoformat()

    plays = []
    url, params = API, {"limit": 200, "ordering": "-airdate", "airdate_after": since}
    for _page in range(_MAX_PAGES):
        data = get(url, params=params, timeout=30).json()
        plays.extend(data.get("results") or [])
        url, params = data.get("next"), None
        if not url:
            break

    counts = Counter()
    first = {}
    # Which songs off each record the station played, and how often: what the
    # play button starts with.
    songs = defaultdict(Counter)
    for p in plays:
        if p.get("play_type") != "trackplay" or not p.get("artist") or not p.get("album"):
            continue
        released = (p.get("release_date") or "")[:10]
        rotation = p.get("rotation_status")
        if not (released and released >= recent) and rotation not in _ROTATIONS:
            continue
        key = (_album_artist(p["artist"]).lower(), p["album"].lower())
        counts[key] += 1
        first.setdefault(key, p)
        if p.get("song"):
            songs[key][p["song"]] += 1

    items = []
    for key, p in first.items():
        n = counts[key]
        rotation = p.get("rotation_status")
        if rotation != "Heavy" and n < min_plays:
            continue
        why = f"Played {n} time{'s' if n != 1 else ''} on KEXP this week"
        if rotation in _ROTATIONS:
            why += f" · {'new music' if rotation in ('R/N', 'R') else rotation.lower() + ' rotation'}"
        if p.get("is_local"):
            why += " · local artist"
        released = (p.get("release_date") or "")[:10] or None
        items.append({
            "artist": _album_artist(p["artist"]),
            "album": p["album"],
            "release_date": released,
            "normalized_date": released,
            "posted": (p.get("airdate") or "")[:10] or None,
            "image": p.get("image_uri") or p.get("thumbnail_uri"),
            "context": why,
            "mbid": p.get("release_group_id"),
            "songs": [{"title": t} for t, _n in songs[key].most_common()],
        })
    # Most played first, so the cache holds the station's favourites up top.
    items.sort(key=lambda it: -counts[(it["artist"].lower(), it["album"].lower())])
    return items


class KEXPDiscovery(DiscoveryPlugin):
    key = SOURCE
    label = "KEXP"
    group = "Radio and blogs"
    description = (
        "New records KEXP (Seattle) is playing most: heavy rotation, and whatever "
        "it has aired several times this week."
    )
    enabled_setting = "discover_kexp_enabled"
    config_fields = [
        {"key": "discover_kexp_days", "label": "Days of airplay",
         "placeholder": "7", "help": "How far back to read the playlist (up to 14)."},
        {"key": "discover_kexp_months", "label": "Released within (months)",
         "placeholder": "3", "help": "What counts as new, unless it's in rotation."},
        {"key": "discover_kexp_min_plays", "label": "Played at least (times)",
         "placeholder": "3", "help": "How often the station has to have played a "
                                     "record this week (heavy rotation always counts)."},
    ]

    def fetch(self, force=False):
        return cached_scrape(self.key, force, scrape, keep_days=21)


register(KEXPDiscovery())
