"""Discover source: Stereogum (RSS).

A news feed, so most posts aren't about a record. What's kept: album
announcements ("Danz CM Announces New Album The Interdimensional ...: Hear Two
Tracks", quoted or not) and the Album Of The Week / Premature Evaluation
reviews, whose artist is found among the post's tags.
"""

import re

from . import register
from .reviews import ANNOUNCE_BARE, ANNOUNCE_QUOTED, ReviewFeedPlugin

_COLUMN = re.compile(r"^(?P<column>Album Of The Week|Premature Evaluation):\s*(?P<rest>.+)$", re.I)


class StereogumDiscovery(ReviewFeedPlugin):
    key = "stereogum"
    label = "Stereogum"
    description = "Album announcements and the Album Of The Week from Stereogum."
    enabled_setting = "discover_stereogum_enabled"
    feed_url = "https://stereogum.com/feed"
    review_patterns = ()
    announce_patterns = (ANNOUNCE_QUOTED, ANNOUNCE_BARE)

    def parse_post(self, post):
        m = _COLUMN.match(post.get("title") or "")
        if m:
            rest = m.group("rest").strip()
            # "Protomartyr Hotel Usona": the artist is one of the post's tags.
            for tag in sorted(post.get("categories") or [], key=len, reverse=True):
                if rest.lower().startswith(tag.lower() + " "):
                    album = rest[len(tag):].strip(" ‘’“”\"'")
                    if album:
                        return self.review_item(post, tag, album, context=f"Stereogum {m.group('column')}")
            return None
        return super().parse_post(post)


register(StereogumDiscovery())
