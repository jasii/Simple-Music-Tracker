"""Discover source: new from your library.

Releases by artists you own but don't follow. Tracking only covers followed
artists (MusicBrainz allows a request a second, and a library can hold
thousands), so a new record by someone you've half a dozen albums of can go
unnoticed. ListenBrainz's fresh-releases feed lists everything in one request,
and this keeps the rows whose artist is in the library -- matched on the
MusicBrainz id where the library has one, else on the name.
"""

from ... import db
from . import DiscoveryPlugin, register
from .common import cached_scrape
from .listenbrainz import _days, fresh_releases, keep, to_item

SOURCE = "librarynew"


def _library():
    """(by mbid, by lower name) -> track count, for owned artists not followed."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT sort_name, mbid, track_count FROM artists "
            "WHERE track_count > 0 AND subscription = 'none' AND ignored = 0"
        ).fetchall()
    finally:
        conn.close()
    by_mbid = {r["mbid"]: r["track_count"] for r in rows if r["mbid"]}
    by_name = {r["sort_name"]: r["track_count"] for r in rows}
    return by_mbid, by_name


def scrape():
    by_mbid, by_name = _library()
    items = []
    seen = set()
    for rel in fresh_releases(_days("discover_librarynew_days", 30)):
        rg = rel.get("release_group_mbid")
        if not keep(rel) or not rg or rg in seen:
            continue
        tracks = next((by_mbid[m] for m in rel.get("artist_mbids") or [] if m in by_mbid), None)
        if tracks is None:
            tracks = by_name.get((rel.get("artist_credit_name") or "").lower())
        if not tracks:
            continue
        seen.add(rg)
        items.append(to_item(rel, f"In your library ({tracks} tracks), not followed"))
    return items


class LibraryNewDiscovery(DiscoveryPlugin):
    key = SOURCE
    label = "New from your library"
    icon = "listenbrainz"
    group = "Release calendars"
    description = (
        "New and upcoming releases by artists in your library that you don't follow, "
        "from ListenBrainz's list of everything new in MusicBrainz."
    )
    enabled_setting = "discover_librarynew_enabled"
    config_fields = [
        {"key": "discover_librarynew_days", "label": "Days back and ahead",
         "placeholder": "30", "help": "How wide a window to read (up to 90)."},
    ]

    def fetch(self, force=False):
        return cached_scrape(self.key, force, scrape, enrich_items=False)


register(LibraryNewDiscovery())
