"""Discover source: DIY Magazine (RSS).

Reviews are titled "Artist — Album"; news announces records as "Artist
announce(s) new album 'Title'".
"""

from . import register
from .reviews import ANNOUNCE_QUOTED, REVIEW_DASH, ReviewFeedPlugin


class DIYDiscovery(ReviewFeedPlugin):
    key = "diy"
    label = "DIY"
    description = "Album reviews and announcements from DIY Magazine."
    enabled_setting = "discover_diy_enabled"
    feed_url = "https://diymag.com/feeds/all"
    review_patterns = (REVIEW_DASH,)
    announce_patterns = (ANNOUNCE_QUOTED,)


register(DIYDiscovery())
