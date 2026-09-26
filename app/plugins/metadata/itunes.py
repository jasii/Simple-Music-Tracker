"""Track previews from the iTunes Search API.

The widest catalogue of the keyless ones, which is why it is asked first out
of the box: if a record was ever sold digitally, iTunes has a 30-second sample
of it. It has nothing to say about artists, so previews are all it claims.
"""

from ... import preview
from . import MetadataPlugin, register


class ItunesMetadata(MetadataPlugin):
    key = "itunes"
    icon = "apple-music"
    label = "iTunes"
    description = (
        "Thirty-second track samples from Apple's public search API. No key "
        "or account needed."
    )
    enabled_setting = "metadata_itunes_enabled"
    provides = ("track_preview",)

    def track_preview(self, artist, title, page_url=None, cached_only=False):
        url = preview.from_source("itunes", artist, title, cached_only=cached_only)
        if not url:
            return None
        return {"kind": "sample", "label": "iTunes", "stream_url": url,
                "source_url": preview.page_from_source("itunes", artist, title)}


register(ItunesMetadata())
