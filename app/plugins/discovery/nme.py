"""Discover source: NME's album reviews (RSS).

Every title reads "Artist – 'Album' review: ...".
"""

from . import register
from .reviews import REVIEW_QUOTED, ReviewFeedPlugin


class NMEDiscovery(ReviewFeedPlugin):
    key = "nme"
    label = "NME"
    description = "Albums NME has just reviewed."
    enabled_setting = "discover_nme_enabled"
    feed_url = "https://www.nme.com/reviews/album/feed"
    review_patterns = (REVIEW_QUOTED,)
    announce_patterns = ()


register(NMEDiscovery())
