"""Metadata from Deezer: artist photos and album covers.

The most reliable source of a photo for a small artist, and it needs no key or
account. It has no biography and no usable genre tags, so it only claims what
it can actually answer.
"""

from ... import deezer, preview
from . import MetadataPlugin, register


class DeezerMetadata(MetadataPlugin):
    key = "deezer"
    icon = "deezer"
    label = "Deezer"
    description = (
        "Artist photos (1000x1000) and album covers from Deezer's public "
        "catalogue. No API key or account needed."
    )
    enabled_setting = "metadata_deezer_enabled"
    provides = ("artist_image", "album_art", "track_preview")

    def artist_images(self, name, artist_id=None, cached_only=False):
        return deezer.artist_images(name, cached_only=cached_only)

    def artist_info(self, name, artist_id=None, cached_only=False):
        images = deezer.artist_images(name, cached_only=cached_only)
        return {"image_url": images[0] if images else None}

    def album_art(self, artist, title, mbid=None):
        return deezer.album_cover(artist, title)

    def track_preview(self, artist, title, page_url=None, cached_only=False):
        url = preview.from_source("deezer", artist, title, cached_only=cached_only)
        if not url:
            return None
        return {"kind": "sample", "label": "Deezer", "stream_url": url,
                "source_url": preview.page_from_source("deezer", artist, title)}


register(DeezerMetadata())
