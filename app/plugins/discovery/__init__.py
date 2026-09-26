"""Discovery plugins: new-release sources shown on the Discover page.

Each source is a :class:`DiscoveryPlugin` that knows how to scrape a site for
upcoming releases, whether it's switched on, and what (if anything) it needs the
user to configure (e.g. a session cookie). The Discover page and the background
scheduler iterate the registered plugins instead of hard-coding each source.

Importing this package registers every bundled discovery plugin.
"""

from .. import Plugin, register, get_plugins, get_plugin  # noqa: F401 - re-exported

from ... import db

# Values that mean "off" for an enable toggle stored as a string setting.
_OFF = ("false", "0", "off", "no", "")


class DiscoveryPlugin(Plugin):
    """A Discover source.

    Subclasses set ``key`` / ``label`` and implement :meth:`fetch`. They may also
    declare ``enabled_setting`` (the on/off toggle), ``config_fields`` (inputs the
    Plugins settings tab renders) and override :meth:`configured` / :meth:`check`.
    """

    kind = "discovery"
    # Settings key holding this source's on/off toggle (default-on string flag).
    enabled_setting = None
    # Field descriptors the settings UI renders when the plugin is enabled. Each:
    #   {key, label, type: text|textarea, placeholder?, help?}
    # where ``key`` is an existing settings key.
    config_fields = []
    # Set True (and implement :meth:`check`) to get a Test button in the Plugins
    # settings tab. The button hits /api/plugins/<kind>/<key>/test -> check().
    has_test = False

    def enabled(self):
        """True unless the enable toggle is explicitly off (sources default on)."""
        if not self.enabled_setting:
            return True
        value = (db.get_setting(self.enabled_setting) or "true").strip().lower()
        return value not in _OFF

    def configured(self):
        """Ready to scrape? Default: just enabled. Override to also require config."""
        return self.enabled()

    def fetch(self, force=False):
        """Return ``(items, cached)``. Persists to the discover cache."""
        raise NotImplementedError

    def check(self):
        """Optional health check. Return ``(ok, message)`` or ``None``."""
        return None

    def describe(self):
        data = super().describe()
        fetched_at, items = db.get_discover_cache(self.key)
        data.update({
            "enabled": self.enabled(),
            "enabled_setting": self.enabled_setting,
            "configured": self.configured(),
            "config_fields": self.config_fields,
            "has_test": self.has_test,
            "refreshable": True,
            "last_scraped": fetched_at,   # epoch seconds, or None if never
            "item_count": len(items),
        })
        return data


# --- canonical item schema --------------------------------------------------

# Every discovery plugin emits a list of release dicts. They're coerced to this
# schema (missing keys -> default, unknown keys dropped) so the merge layer, the
# discover cache and the frontend can rely on one stable shape no matter which
# source produced the row. New plugins add fields here, not ad-hoc per source.
ITEM_DEFAULTS = {
    "artist": None,
    "artist_url": None,
    "album": None,
    "album_url": None,
    "release_date": None,      # raw source string, e.g. "17 Jun 2026"
    "normalized_date": None,   # ISO "YYYY-MM-DD", or None if unparseable
    "primary_type": "Album",   # "Album" | "EP" | "Single"
    "image": None,             # cover-art URL
    "genres": [],              # list[str]
    "context": None,           # source flavor text (recommendation reason / note)
    "context_artists": [],     # artist names named in that flavor text
    "mbid": None,              # release-group MBID, if the source knows it
}


def normalize_item(raw):
    """Coerce one scraped row to :data:`ITEM_DEFAULTS` (drops unknown keys)."""
    item = {}
    for key, default in ITEM_DEFAULTS.items():
        value = raw.get(key, default)
        item[key] = default if value is None else value
    # The merge layer iterates these, so guarantee they're lists.
    if not isinstance(item["genres"], list):
        item["genres"] = []
    if not isinstance(item["context_artists"], list):
        item["context_artists"] = []
    return item


def normalize_items(items):
    """Coerce a list of scraped rows to the canonical schema."""
    return [normalize_item(it) for it in items or []]


# Register the bundled sources (import side effect calls register()).
from . import lastfm, metacritic  # noqa: E402,F401
from . import albumoftheyear, indieisnotagenre  # noqa: E402,F401
