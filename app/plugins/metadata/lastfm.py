"""Metadata from Last.fm: photos, biographies and genre tags.

The oldest source here and still the only one with a written biography, so it
sits at the top of the default order. Its artist photos are patchier than they
look -- Last.fm serves a placeholder star for anyone nobody uploaded a picture
for, which app/lastfm.py filters out rather than pass on as a real image.
"""

from ... import db, lastfm, trackvideo
from . import MetadataPlugin, register


class LastfmMetadata(MetadataPlugin):
    key = "lastfm"
    icon = "last-fm"
    label = "Last.fm"
    description = (
        "Artist photos, biographies and genre tags, plus album covers, "
        "sleeve-notes write-ups and album tags. Needs the Last.fm API key "
        "from the External APIs tab."
    )
    enabled_setting = "metadata_lastfm_enabled"
    provides = ("artist_image", "artist_bio", "artist_genres", "artist_similar",
                "album_art", "album_description", "album_tags", "track_preview")
    has_test = True

    def configured(self):
        return self.enabled() and bool(db.get_setting("lastfm_api_key"))

    def check(self):
        return lastfm.check_api_key()

    def artist_info(self, name, artist_id=None, cached_only=False):
        if cached_only:
            info = lastfm.cached_artist_info(name)
            if not info:
                return None
            return {"image_url": info.get("image_url"),
                    "bio": info.get("bio") or None,
                    "genres": [],
                    "url": info.get("lastfm_url")}
        info = lastfm.get_artist_info(name) or {}
        genres = info.get("genres") or lastfm.top_tags(name)
        return {
            "image_url": info.get("image_url"),
            "bio": info.get("bio") or None,
            "genres": genres or [],
            "url": info.get("lastfm_url"),
        }

    def similar_artists(self, name):
        return lastfm.similar_artists(name)

    def track_preview(self, artist, title, page_url=None, cached_only=False):
        """The video Last.fm's own player uses, for tracks no catalogue sells.

        Not audio the app can stream -- an embed id -- so it only helps a
        remix or a one-off single that iTunes and Deezer have never heard of,
        which is exactly what it's for.
        """
        if cached_only:
            return None
        video_id = trackvideo.for_track(artist, title, page_url)
        if not video_id:
            return None
        return {"kind": "youtube", "label": "video", "youtube_id": video_id,
                # Where the audio is really coming from, for the credit line.
                "source_url": f"https://www.youtube.com/watch?v={video_id}"}

    def album_art(self, artist, title, mbid=None):
        return (lastfm.get_album_info(artist, title) or {}).get("image_url")

    def album_info(self, artist, title, mbid=None, cached_only=False):
        if cached_only and not lastfm.cached_album_info(artist, title):
            return None
        found = lastfm.get_album_info(artist, title) or {}
        return {
            "image_url": found.get("image_url"),
            "description": found.get("description"),
            "tags": (found.get("genres") or [])[:10],
        }


register(LastfmMetadata())
