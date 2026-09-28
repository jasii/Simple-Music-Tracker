"""Discover source: Paste Magazine's music section (RSS).

Paste's headlines are sentences ("Blondshell goes deeper on Violins"), so the
reviews are picked out by their address (/music/<artist>/<...>-review) and the
album read out of the sentence around the artist's name.
"""

import re

from . import register
from .reviews import ANNOUNCE_QUOTED, ReviewFeedPlugin

_REVIEW_URL = re.compile(r"/music/(?P<artist>[a-z0-9-]+)/(?P<slug>[a-z0-9-]+)-review/?$")


def parse_review(title, link):
    """(artist, album) from a Paste review's headline and address, or None."""
    m = _REVIEW_URL.search(link or "")
    if not m:
        return None
    words = m.group("artist").split("-")
    # The artist as the headline writes them.
    found = re.search(r"\b" + r"[\W_]+".join(map(re.escape, words)) + r"\b", title, re.I)
    if not found:
        return None
    artist = found.group(0)
    if found.start() == 0:
        # "Blondshell goes deeper on Violins": the record comes after the last "on".
        tail = re.split(r"\s+(?:on|with)\s+", title[found.end():], flags=re.I)
        album = tail[-1] if len(tail) > 1 else None
    else:
        # "I Wrote You A Letter is just another M83 record": it comes first.
        head = re.split(r"\s+(?:is|are|was|finds|sees|shows|proves)\s+", title[:found.start()], flags=re.I)
        album = head[0] if len(head) > 1 else None
    album = (album or "").strip(" ‘’“”\"',.:")
    if not album:
        return None
    # The address names the album too (sometimes shortened): one word of it
    # has to be in what was read out, or it's something else.
    slug = set(m.group("slug").split("-")) - set(words)
    if slug and not slug & set(re.findall(r"[a-z0-9]+", album.lower())):
        return None
    return artist, album


class PasteDiscovery(ReviewFeedPlugin):
    key = "paste"
    label = "Paste"
    description = "Album reviews and announcements from Paste Magazine."
    enabled_setting = "discover_paste_enabled"
    feed_url = "https://www.pastemagazine.com/music/feed"
    announce_patterns = (ANNOUNCE_QUOTED,)

    def parse_post(self, post):
        found = parse_review(post.get("title") or "", post.get("link") or "")
        if found:
            return self.review_item(post, *found)
        return super().parse_post(post)


register(PasteDiscovery())
