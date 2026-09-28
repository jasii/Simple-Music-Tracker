"""Discover source: BrooklynVegan (RSS).

A news feed; kept are the album announcements ("Fontaines D.C. announce new
album 'Dopamine Chamber,' share 'Marianne'").
"""

from . import register
from .reviews import ANNOUNCE_QUOTED, ReviewFeedPlugin


class BrooklynVeganDiscovery(ReviewFeedPlugin):
    key = "brooklynvegan"
    label = "BrooklynVegan"
    description = "Album announcements from BrooklynVegan."
    enabled_setting = "discover_brooklynvegan_enabled"
    feed_url = "https://www.brooklynvegan.com/feed/"
    review_patterns = ()
    announce_patterns = (ANNOUNCE_QUOTED,)


register(BrooklynVeganDiscovery())
