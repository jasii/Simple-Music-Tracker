"""Discover source: AnyDecentMusic?

A review aggregator, mostly of the UK press: every new album gets the average
of its critics' scores. Its front page (http://www.anydecentmusic.com/) holds
the chart of the best-rated new albums and the ones just in, each with its
ADM rating and the date it was added.
"""

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import DiscoveryPlugin, register
from .common import cached_scrape, get

URL = "http://www.anydecentmusic.com/"
SOURCE = "anydecentmusic"


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.select("li.top_album, div.album_detail"):
        info = card.select_one(".album_info")
        if not info:
            continue
        artist = info.select_one("h4")
        album = info.select_one("h5")
        if not artist or not album:
            continue
        score = card.select_one(".score")
        cover = card.select_one("img")
        link = album.select_one("a")
        added = re.search(r"Added:\s*(\d{1,2}/\d{1,2}/\d{4})", info.get_text(" ", strip=True))
        when = None
        if added:
            try:
                when = datetime.strptime(added.group(1), "%d/%m/%Y").date().isoformat()
            except ValueError:
                when = None
        blurb = next((p.get_text(" ", strip=True) for p in info.select("p")
                      if not p.select_one("small")), "")
        why = f"ADM rating {score.get_text(strip=True)}" if score else "On AnyDecentMusic?"
        items.append({
            "artist": artist.get_text(" ", strip=True),
            "album": album.get_text(" ", strip=True),
            "album_url": urljoin(URL, link.get("href")) if link else None,
            "release_date": when,
            "normalized_date": when,
            "posted": when,
            "image": urljoin(URL, cover.get("src")) if cover and cover.get("src") else None,
            "context": why + (f" · {blurb}" if blurb else ""),
        })
    return items


class AnyDecentMusicDiscovery(DiscoveryPlugin):
    key = SOURCE
    label = "AnyDecentMusic?"
    group = "Critics and reviews"
    description = (
        "The best-rated new albums from AnyDecentMusic?, which averages the critics' "
        "scores (mostly the UK press) for every new record."
    )
    enabled_setting = "discover_anydecentmusic_enabled"

    def fetch(self, force=False):
        return cached_scrape(self.key, force, lambda: parse(get(URL).text), keep_days=60)


register(AnyDecentMusicDiscovery())
