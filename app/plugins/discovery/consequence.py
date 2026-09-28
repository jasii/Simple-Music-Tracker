"""Discover source: Consequence (RSS).

Kept from the news feed: album announcements ("Artist Announces New Album
'Title'") and album reviews ("Artist's 'Title' ...").
"""

import re

from . import register
from .reviews import _QUOTED, ANNOUNCE_QUOTED, ReviewFeedPlugin

REVIEW_POSSESSIVE = re.compile(rf"^(?P<artist>.+?)['’]s\s+(?:new\s+(?:album|lp|ep)\s+)?{_QUOTED}", re.I)


class ConsequenceDiscovery(ReviewFeedPlugin):
    key = "consequence"
    label = "Consequence"
    description = "Album announcements and reviews from Consequence."
    enabled_setting = "discover_consequence_enabled"
    feed_url = "https://consequence.net/feed/"
    review_patterns = (REVIEW_POSSESSIVE,)
    review_categories = ("album reviews", "reviews")
    announce_patterns = (ANNOUNCE_QUOTED,)


register(ConsequenceDiscovery())
