"""The base for review sites read through their RSS feeds.

A feed is a list of posts, and only some posts are about a record: a review
("Blondshell – 'Violins' review"), or an announcement ("Fontaines D.C.
announce new album 'Dopamine Chamber'"). Each site's plugin names its feed and
the ways its titles are written; this reads the feed, keeps the posts it can
turn into an artist and an album, and throws away the tour dates and awards
shows.

A review is dated by when it was posted (reviews land in release week). An
announcement is dated by the release date its text mentions ("out November
7"), else left undated -- it shows under "Date TBA" until it's out.
"""

import re

from . import DiscoveryPlugin
from .common import cached_scrape, get, parse_rss, release_date_in

# How long a post stays on Discover after it drops off the feed.
KEEP_DAYS = 60

# Opening quote -> the closing one. A closing single quote only counts when
# something other than a letter follows it, so "Nobody's" isn't an ending.
_QUOTED = r"(?:‘(?P<q1>.+?)[,.!?]?’(?![A-Za-z])|“(?P<q2>.+?)[,.!?]?”|\"(?P<q3>.+?)[,.!?]?\"|'(?P<q4>.+?)[,.!?]?'(?![A-Za-z]))"

_ANNOUNCE_VERBS = (
    r"(?:announces?|announced|details?|unveils?|reveals?|shares? details (?:of|on)|confirms?|"
    r"releases?|drops?|surprise[- ]releases?|preview|previews|tease|teases|return with|returns with|"
    r"set(?:s)?|ready|readies|to release|will release|are releasing|is releasing)"
)
_RECORD_WORDS = (
    r"(?:(?:a|an|the|their|her|his|its|new|debut|second|third|fourth|fifth|sixth|seventh|eighth|"
    r"ninth|tenth|first|sophomore|next|upcoming|surprise|another|forthcoming|final|studio|solo|"
    r"full[- ]length|collaborative|joint|double|live|instrumental|covers|self[- ]titled|latest)\s+)*"
)
_KIND = r"(?P<kind>album|lp|ep|record|mixtape)"

# A clause before the announcement: "Kate Nash shares 'Spilt Milk' and ...".
_LEAD_CLAUSE = r"(?:(?:shares?|drops?|releases?|unveils?|premieres?|debuts?|returns? with)\s+.+?\s+(?:and|,)\s+)?"
# "Artist announce(s) new album 'Title'" (and the many ways of saying it).
ANNOUNCE_QUOTED = re.compile(
    rf"^(?P<artist>[^‘“\"]+?)\s+{_LEAD_CLAUSE}{_ANNOUNCE_VERBS}\s+{_RECORD_WORDS}{_KIND}\b[,:]?\s*"
    rf"(?:[^‘“\"']{{0,40}}?\s)?{_QUOTED}",
    re.I,
)
# Stereogum's way, unquoted: "Artist Announces New Album Title: Hear ..."
ANNOUNCE_BARE = re.compile(
    rf"^(?P<artist>.+?)\s+Announces?\s+{_RECORD_WORDS}{_KIND}\s+(?P<album>[^:–—(]+?)\s*(?::|–|—|\(|$)",
    re.I,
)
# "Artist – 'Title' review: ..." (NME).
REVIEW_QUOTED = re.compile(rf"^(?P<artist>.+?)\s+[–—-]\s+{_QUOTED}\s+review\b", re.I)
# "Artist – Title" / "Artist — Title" (The Quietus, DIY reviews).
REVIEW_DASH = re.compile(r"^(?P<artist>[^–—:]+?)\s+[–—]\s+(?P<album>[^–—:]+?)\s*$")


def _album_of(m):
    for group in ("album", "q1", "q2", "q3", "q4"):
        try:
            value = m.group(group)
        except IndexError:
            continue
        if value:
            return value.strip(" ,.")
    return None


def _clean_artist(value):
    value = re.sub(r"^(?:watch|listen|hear|stream)[:\s]+", "", value.strip(), flags=re.I)
    # Not dots: "Fontaines D.C." ends in one.
    return value.strip(" ,:")


class ReviewFeedPlugin(DiscoveryPlugin):
    """A review site's RSS feed as a Discover source.

    Subclasses set key/label/description/enabled_setting, ``feed_url``, and
    which of :meth:`parse_review` / :meth:`parse_announcement` apply (or
    override :meth:`parse_post` altogether).
    """

    group = "Critics and reviews"
    feed_url = None
    # Which title patterns this site's posts use.
    review_patterns = (REVIEW_QUOTED,)
    announce_patterns = (ANNOUNCE_QUOTED,)
    # Only posts in these categories count as reviews (None = any).
    review_categories = None
    # The feed's picture is the record's cover (else it's a photo, and the
    # cover is looked up like any other source's).
    image_is_cover = False

    def fetch(self, force=False):
        return cached_scrape(self.key, force, self.scrape, keep_days=KEEP_DAYS)

    def scrape(self):
        posts = parse_rss(get(self.feed_url).text)
        items = []
        for post in posts:
            try:
                item = self.parse_post(post)
            except Exception:  # noqa: BLE001 - one odd post isn't the feed
                item = None
            if item and item.get("artist") and item.get("album"):
                items.append(item)
        return items

    def is_review(self, post):
        if self.review_categories is None:
            return True
        cats = {c.strip().lower() for c in post.get("categories") or []}
        return any(c in cats for c in self.review_categories)

    def parse_post(self, post):
        """A Discover item for one post, or None when it isn't about a record."""
        title = post.get("title") or ""
        if self.is_review(post):
            for pattern in self.review_patterns:
                m = pattern.search(title)
                if m:
                    return self.review_item(post, _clean_artist(m.group("artist")), _album_of(m))
        for pattern in self.announce_patterns:
            m = pattern.search(title)
            if m:
                return self.announce_item(post, _clean_artist(m.group("artist")), _album_of(m))
        return None

    def review_item(self, post, artist, album, context=None):
        return {
            "artist": artist,
            "album": album,
            "album_url": post.get("link"),
            "release_date": post.get("published"),
            "normalized_date": post.get("published"),
            "posted": post.get("published"),
            "image": post.get("image") if self.image_is_cover else None,
            "context": context or f"Reviewed by {self.label}",
        }

    def announce_item(self, post, artist, album):
        text = " ".join([post.get("title") or "", post.get("summary") or "", post.get("content") or ""])
        when = release_date_in(text, post.get("published"))
        return {
            "artist": artist,
            "album": album,
            "album_url": post.get("link"),
            "release_date": when,
            "normalized_date": when,
            "posted": post.get("published"),
            "image": None,  # a news photo, not the cover: the lookup finds that
            "context": f"Announced on {self.label}",
        }
