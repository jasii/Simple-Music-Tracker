"""Discover source: The Quietus (RSS).

Posts in its Reviews category are titled "Artist – Album"; its news announces
records with the title in quotes.
"""

from . import register
from .reviews import ANNOUNCE_QUOTED, REVIEW_DASH, ReviewFeedPlugin


class QuietusDiscovery(ReviewFeedPlugin):
    key = "thequietus"
    label = "The Quietus"
    description = "Album reviews and announcements from The Quietus."
    enabled_setting = "discover_thequietus_enabled"
    feed_url = "https://thequietus.com/feed/"
    review_patterns = (REVIEW_DASH,)
    review_categories = ("reviews",)
    announce_patterns = (ANNOUNCE_QUOTED,)


register(QuietusDiscovery())
