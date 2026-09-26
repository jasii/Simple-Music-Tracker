"""Metadata from the library itself: a record sleeve when no photo exists.

Not a photo of the band, and deliberately last in the default order -- but an
artist nobody has ever photographed is better represented by one of their own
covers than by an empty square. Everything here is a local read: the releases
already tracked, the albums the library owns, and the cached discography.
"""

from ... import artwork, db, musicbrainz
from . import MetadataPlugin, register


class CoverMetadata(MetadataPlugin):
    key = "own_covers"
    label = "Own release covers"
    description = (
        "Falls back to a cover from one of the artist's own releases when no "
        "service has a photo of them. Reads what's already stored, so it never "
        "makes a request."
    )
    enabled_setting = "metadata_own_covers_enabled"
    provides = ("artist_image",)

    def artist_images(self, name, artist_id=None, cached_only=False):
        if not artist_id:
            return []
        conn = db.get_connection()
        try:
            found = (
                self._release_cover(conn, artist_id),
                self._owned_cover(conn, artist_id),
                self._discography_cover(conn, artist_id),
            )
        finally:
            conn.close()
        # Two of these are archive URLs derived from a release-group id, and
        # plenty of release groups have no front image. Offering one the
        # archive has already refused would crowd out the sources that do have
        # a picture -- and it costs nothing to know, because the refusal is
        # remembered in the image cache.
        return [url for url in found if url and not artwork.broken(url)]

    def artist_info(self, name, artist_id=None, cached_only=False):
        found = self.artist_images(name, artist_id=artist_id)
        return {"image_url": found[0]} if found else None

    @staticmethod
    def _release_cover(conn, artist_id):
        """The cover of this artist's most recent tracked release."""
        row = conn.execute(
            "SELECT image_url FROM releases WHERE artist_id = ? "
            "AND COALESCE(image_url, '') <> '' ORDER BY release_date DESC LIMIT 1",
            (artist_id,),
        ).fetchone()
        return row["image_url"] if row else None

    @staticmethod
    def _owned_cover(conn, artist_id):
        """A sleeve from something the library owns by this artist.

        Owned albums tagged by Picard carry their release-group id, and a cover
        URL is a pure function of that -- so this costs nothing but the download
        the warmer was going to do anyway.
        """
        row = conn.execute(
            "SELECT rg_mbid FROM owned_albums WHERE artist_id = ? "
            "AND COALESCE(rg_mbid, '') <> '' LIMIT 1", (artist_id,),
        ).fetchone()
        return musicbrainz.cover_art_url(row["rg_mbid"]) if row else None

    @staticmethod
    def _discography_cover(conn, artist_id):
        """Failing everything else, the newest sleeve in the cached discography."""
        import json

        row = conn.execute(
            "SELECT j.payload FROM artists a "
            "JOIN json_cache j ON j.cache_key = 'disco:' || a.mbid WHERE a.id = ?",
            (artist_id,),
        ).fetchone()
        if not row:
            return None
        try:
            items = json.loads(row["payload"])
        except (TypeError, ValueError):
            return None
        dated = [i for i in items if i.get("mbid") and i.get("release_date")]
        if not dated:
            return None
        newest = max(dated, key=lambda i: i["release_date"])
        return musicbrainz.cover_art_url(newest["mbid"])


register(CoverMetadata())
