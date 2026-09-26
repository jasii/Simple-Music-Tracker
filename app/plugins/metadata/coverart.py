"""Album covers from the Cover Art Archive (MusicBrainz's own artwork store).

The URL is a pure function of the release-group id, so no lookup is needed to
build one -- but plenty of release groups have no front image and that URL
404s, which is why a cover is only offered once the archive confirms it with a
HEAD. Answers already on disk (or already known to be missing) cost nothing.
"""

import requests

from ... import artwork, musicbrainz
from . import MetadataPlugin, register

USER_AGENT = "SimpleMusicTracker/1.0 (+https://github.com/jasii/Simple-Music-Tracker)"


class CoverArtArchiveMetadata(MetadataPlugin):
    key = "coverartarchive"
    icon = "musicbrainz"
    label = "Cover Art Archive"
    description = (
        "Album covers from MusicBrainz's artwork archive, matched by release "
        "group. Only offers a cover the archive confirms it has."
    )
    enabled_setting = "metadata_coverartarchive_enabled"
    provides = ("album_art",)

    def album_art(self, artist, title, mbid=None):
        if not mbid:
            return None
        url = musicbrainz.cover_art_url(mbid)
        if not url:
            return None
        # Already on disk (or already known to be missing): no request at all.
        if artwork.cached_path(url):
            return url
        if artwork.negative_cached(url):
            return None
        try:
            # HEAD, not the bytes: the browser asks /art for those, which caches
            # them then. This only has to answer "is there a front cover".
            resp = requests.head(url, timeout=(5, 10), allow_redirects=True,
                                 headers={"User-Agent": USER_AGENT})
            return url if resp.status_code == 200 else None
        except requests.RequestException:
            return None


register(CoverArtArchiveMetadata())
