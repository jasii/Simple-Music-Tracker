"""Discover source: Pitchfork's album reviews (RSS).

The feed titles each review with the album alone ("I Wrote You a Letter"); the
artist comes from the cover image's file name ("M83 I Wrote You A Letter.jpg"),
or failing that from the review's address (/reviews/albums/m83-i-wrote-you-a-letter/).
"""

import re
from urllib.parse import unquote

from . import register
from .reviews import ReviewFeedPlugin


def _words(text):
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def artist_from(post, album):
    """The reviewed artist, from the cover's file name or the review's URL."""
    target = _words(album)
    if not target:
        return None
    name = unquote((post.get("image") or "").rsplit("/", 1)[-1]).rsplit(".", 1)[0]
    tokens = re.split(r"[\s_]+", name.strip())
    if len(tokens) > len(target) and _words(" ".join(tokens[-len(target):])) == target:
        artist = " ".join(tokens[:-len(target)]).strip(" -–_")
        if artist:
            return artist
    m = re.search(r"/reviews/albums/([^/?#]+)", post.get("link") or "")
    if m:
        slug = m.group(1).split("-")
        if len(slug) > len(target) and slug[-len(target):] == target:
            return " ".join(w.capitalize() for w in slug[:-len(target)])
    return None


class PitchforkDiscovery(ReviewFeedPlugin):
    key = "pitchfork"
    label = "Pitchfork"
    description = "Albums Pitchfork has just reviewed."
    enabled_setting = "discover_pitchfork_enabled"
    feed_url = "https://pitchfork.com/feed/feed-album-reviews/rss"
    image_is_cover = True

    def parse_post(self, post):
        album = (post.get("title") or "").strip()
        artist = artist_from(post, album)
        if not album or not artist:
            return None
        return self.review_item(post, artist, album)


register(PitchforkDiscovery())
