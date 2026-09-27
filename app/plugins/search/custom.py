"""Search plugin: sites you add yourself.

Each site is a name, a search URL and optionally an icon, set under Settings >
Downloads & quality. The URL takes ``{query}`` (artist and title together),
``{artist}`` and ``{album}``, filled in URL-encoded with ``+`` for spaces, so a
tracker's search reads::

    https://tracker.example/torrents.php?searchstr={query}

and opens as ``...?searchstr=bleachers+i%27m+not+joking``. A URL with no
placeholder gets the query added on the end.
"""

from ... import db
from . import SearchPlugin, parse_sites, register


class CustomSearchPlugin(SearchPlugin):
    """Search links to any site, from a list kept in settings."""

    key = "custom"
    label = "Custom search links"
    description = (
        "Your own search sites, shown as icons beside the Last.fm, MusicBrainz "
        "and YouTube Music links on every release. Each one opens that site's "
        "search for the release's artist and title."
    )

    sites_setting = "search_custom_sites"

    def sites(self):
        return parse_sites(db.get_setting(self.sites_setting))


register(CustomSearchPlugin())
