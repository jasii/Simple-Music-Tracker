"""Discover source: AllMusic's featured new releases.

The weekly list at https://www.allmusic.com/newreleases: each record with its
cover, genre, label, AllMusic's star rating and whether it's an Editors'
Choice. The page is dated ("Featured New Releases for September 25, 2026").
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from . import DiscoveryPlugin, register
from .common import cached_scrape, get

URL = "https://www.allmusic.com/newreleases"
SOURCE = "allmusic"


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    when = None
    heading = soup.select_one("h1")
    m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", heading.get_text(" ", strip=True) if heading else "")
    if m:
        try:
            when = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
        except ValueError:
            when = None
    items = []
    for card in soup.select("article.newReleaseItem"):
        artist = card.select_one(".artist")
        title = card.select_one(".title a") or card.select_one(".title")
        if not artist or not title:
            continue
        cover = card.select_one("img.nrCoverImg")
        genres = [g.get_text(strip=True) for g in card.select(".genres a")]
        label = card.select_one(".labels")
        rating = card.select_one(".allmusicRating")
        stars = None
        if rating:
            r = re.search(r"ratingAllmusic(\d+)", " ".join(rating.get("class") or []))
            stars = int(r.group(1)) / 2 if r else None
        why = []
        if card.select_one(".edChoiceBanner"):
            why.append("Editors' Choice")
        if stars:
            why.append(f"{stars:g} stars on AllMusic")
        if label and label.get_text(strip=True):
            why.append(label.get_text(" ", strip=True))
        items.append({
            "artist": artist.get_text(" ", strip=True),
            "album": title.get_text(" ", strip=True),
            "album_url": title.get("href") if title.name == "a" else None,
            "release_date": when,
            "normalized_date": when,
            "posted": when,
            "image": cover.get("src") if cover else None,
            "genres": genres[:3],
            "context": " · ".join(why) or None,
        })
    return items


class AllMusicDiscovery(DiscoveryPlugin):
    key = SOURCE
    label = "AllMusic"
    group = "Release calendars"
    description = "AllMusic's featured new releases each week, with its ratings and Editors' Choices."
    enabled_setting = "discover_allmusic_enabled"

    def fetch(self, force=False):
        return cached_scrape(self.key, force, lambda: parse(get(URL).text))


register(AllMusicDiscovery())
