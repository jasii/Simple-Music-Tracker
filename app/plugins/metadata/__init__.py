"""Metadata plugins: where artist and album information is scraped from.

MusicBrainz says what a release *is* (that stays the backbone). Everything
else -- an artist's photo, their biography, the genre tags, a cover for a
release the archive has nothing for -- comes from whichever service happens to
have it, and services come and go: Last.fm has no photo for half the artists in
a personal library, the Cover Art Archive only has sleeves, Deezer has a press
photo for nearly everyone.

So each source is a plugin declaring what it can answer (``provides``), and the
user puts them in the order they should be asked (``metadata_priority``). The
facade in :mod:`app.metadata` walks that order and takes the first answer for
each field, which means adding a better source later is one new file plus a
drag in the settings list -- no call sites change.

Importing this package registers every bundled metadata plugin.
"""

from .. import Plugin, register, get_plugins, get_plugin  # noqa: F401 - re-exported

from ... import db

# Values that mean "off" for an enable toggle stored as a string setting.
_OFF = ("false", "0", "off", "no", "")

# Setting holding the comma-separated plugin keys, best source first. One list
# per capability, because the right order differs by field: Last.fm is the only
# source with a biography, while the Cover Art Archive has the release's own
# sleeve at 1200px and Last.fm only a thumbnail of it.
PRIORITY_SETTING = "metadata_priority"


def priority_setting(capability=None):
    """Which setting holds the order for *capability* (None = the shared one)."""
    return f"{PRIORITY_SETTING}_{capability}" if capability else PRIORITY_SETTING

# What a metadata source can answer. A plugin lists the ones it implements so
# the settings page can show it, and so a lookup only asks sources that can help.
CAPABILITIES = (
    "artist_image", "artist_bio", "artist_genres", "artist_similar",
    "album_art", "album_description", "album_tags", "track_preview",
)

# Which block of the settings page a field belongs in, so eleven lists read as
# three groups rather than one wall.
CAPABILITY_GROUPS = {
    "artist_image": "artist", "artist_bio": "artist",
    "artist_genres": "artist", "artist_similar": "artist",
    "album_art": "release", "album_description": "release",
    "album_tags": "release",
    "track_preview": "playback",
}

GROUP_LABELS = {
    "artist": ("About an artist", "Their picture, their story, what they sound like."),
    "release": ("About a release", "Sleeves, write-ups and tags."),
    "playback": ("Playing a track", "Where the audio comes from when you press play."),
}

# What each field is, for the settings UI: one ordered list is shown per entry.
CAPABILITY_LABELS = {
    "artist_image": (
        "Artist photos",
        "The picture on the artist page and in every artist list.",
    ),
    "artist_bio": (
        "Biographies",
        "The write-up on the artist page.",
    ),
    "artist_genres": (
        "Genre tags",
        "The tags on the artist page, which the Discover genre filter runs on.",
    ),
    "artist_similar": (
        "Similar artists",
        "Who sounds like an artist: the Discover suggestions, the chips on an "
        "artist page, and the \"worth checking out\" ranking.",
    ),
    "album_art": (
        "Album covers",
        "Sleeves on album pages, discographies and the missing list.",
    ),
    "album_description": (
        "Album write-ups",
        "The description shown under a release on its album page.",
    ),
    "album_tags": (
        "Album genre tags",
        "Genre badges on the album page.",
    ),
    "track_preview": (
        "Track previews",
        "What a track plays when you don't have it: a 30-second sample, or the "
        "video a page carries. Your own library always wins over these -- which "
        "of your libraries is the next list along.",
    ),
}


class MetadataPlugin(Plugin):
    """A source of artist / album metadata.

    Subclasses set ``key`` / ``label`` / ``provides`` and implement whichever of
    :meth:`artist_info`, :meth:`artist_images` and :meth:`album_art` they can
    answer. Every method is optional and returns nothing when it has no answer:
    the facade simply moves on to the next source.
    """

    kind = "metadata"
    enabled_setting = None
    config_fields = []
    has_test = False
    provides = ()

    def enabled(self):
        """True unless the enable toggle is explicitly off (sources default on)."""
        if not self.enabled_setting:
            return True
        value = (db.get_setting(self.enabled_setting) or "true").strip().lower()
        return value not in _OFF

    def configured(self):
        """Ready to answer? Default: just enabled. Override to also need config."""
        return self.enabled()

    def artist_info(self, name, artist_id=None, cached_only=False):
        """{image_url?, bio?, genres?, url?} for one artist, or None.

        Partial answers are the norm and are expected: a source that only has a
        photo returns just ``image_url`` and the rest is filled from further
        down the order. *artist_id* is the library row, for sources that read
        what the app already stores; *cached_only* means answer from cache or
        not at all, which is how the background artwork warmer asks (it must
        not turn a 2000-artist walk into 2000 lookups).
        """
        return None

    def artist_images(self, name, artist_id=None, cached_only=False):
        """Several candidate photos for one artist, best first (for the picker)."""
        info = self.artist_info(name, artist_id=artist_id,
                                cached_only=cached_only) or {}
        return [info["image_url"]] if info.get("image_url") else []

    def similar_artists(self, name):
        """Artists this source thinks sound like *name*.

        [{"name", "score"?, "url"?, ...}] as app.db.record_similar_artists
        expects, or [] when this source has no opinion.
        """
        return []

    def album_art(self, artist, title, mbid=None):
        """A cover URL for one release, or None."""
        return None

    def track_preview(self, artist, title, page_url=None, cached_only=False):
        """Something playable for one track, or None.

        Either ``{"kind": "sample", "stream_url": <audio url>, "label": ...}``
        for a catalogue with audio, or ``{"kind": "youtube", "youtube_id": ...,
        "label": ...}`` for a page that only carries an embed. *page_url* is
        the track's own page on that service when a tracklist gave us one.
        """
        return None

    def album_info(self, artist, title, mbid=None, cached_only=False):
        """What this source knows about one release, or None.

        Any of {image_url, description, tags}; partial answers are normal and
        the rest is filled from further down each field's own order. *cached_only* answers
        from storage or not at all, for callers that can't wait on a lookup.
        """
        return None

    def check(self):
        """Optional health check. Return ``(ok, message)`` or ``None``."""
        return None

    def describe(self):
        data = super().describe()
        data.update({
            "enabled": self.enabled(),
            "enabled_setting": self.enabled_setting,
            "configured": self.configured(),
            "config_fields": self.config_fields,
            "has_test": self.has_test,
            "provides": list(self.provides),
        })
        return data


def default_order(capability=None):
    """Registration order, which is also the out-of-the-box priority."""
    return [p.key for p in get_plugins("metadata")
            if not capability or capability in p.provides]


def priority(capability=None):
    """Plugin keys in the order the user wants them asked for *capability*.

    Falls back to the shared order, then to registration order. Keys that no
    longer exist are dropped and newly added plugins fall in at the end, so an
    installed plugin is always reachable even if the stored order predates it.
    """
    known = default_order(capability)
    stored = db.get_setting(priority_setting(capability))
    if not stored and capability:
        stored = db.get_setting(PRIORITY_SETTING)
    order = []
    for key in (stored or "").split(","):
        key = key.strip()
        if key in known and key not in order:
            order.append(key)
    return order + [k for k in known if k not in order]


def set_priority(keys, capability=None):
    """Store a new order for *capability* (unknown keys ignored). Returns it."""
    known = default_order(capability)
    order = []
    for key in keys or []:
        key = (key or "").strip()
        if key in known and key not in order:
            order.append(key)
    order += [k for k in known if k not in order]
    db.set_setting(priority_setting(capability), ",".join(order))
    return order


def orders():
    """{capability: [plugin keys]} for every capability, for the settings UI."""
    return {cap: priority(cap) for cap in CAPABILITIES}


def sources(capability=None):
    """Usable plugins in priority order, optionally only those answering *capability*."""
    by_key = {p.key: p for p in get_plugins("metadata")}
    out = []
    for key in priority(capability):
        plugin = by_key.get(key)
        if not plugin or not plugin.configured():
            continue
        if capability and capability not in plugin.provides:
            continue
        out.append(plugin)
    return out


# Register the bundled sources (import side effect calls register()). The order
# here is the out-of-the-box priority: the services that describe an artist
# first, then the ones that only have a picture, then the local fallback.
from . import lastfm, itunes, deezer, coverart, covers  # noqa: E402,F401
