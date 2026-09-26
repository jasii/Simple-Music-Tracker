"""Discover source: indieisnotagenre.com's new-indie-albums release list.

The page (https://www.indieisnotagenre.com/new-indie-albums/) is a public
WordPress list: ``div.month_divider`` headings ("January 2026") with
``div.container`` rows beneath, each holding ``div.release_date`` ("9 Jan"),
``div.album_artist``, ``div.album_name`` and an often-empty ``div.album_label``.

Rows carry no cover art or genres, so releases are enriched through the same
cached Last.fm / MusicBrainz pipeline the Metacritic source uses.
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from ... import db
from . import DiscoveryPlugin, normalize_items, register
# Shared enrichment (image + genres, cached 7 days) so all scraped sources
# resolve a release's art the same way and share one cache.
from .metacritic import _apply_enrichment, _cache_ttl, _enrich_workers, _headers

BASE = "https://www.indieisnotagenre.com"
LIST_PATH = "/new-indie-albums/"
SOURCE = "iing"

_lock = threading.Lock()


def _absolute(href):
    if not href:
        return None
    return href if href.startswith("http") else BASE + href


def parse_releases(html):
    """Parse the month-divided release list into discover items."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    month_year = None  # e.g. "January 2026", from the last divider seen
    for node in soup.select("div.month_divider, div.container"):
        if "month_divider" in (node.get("class") or []):
            month_year = node.get_text(" ", strip=True)
            continue
        if node.select_one("div.head"):  # the column-header row
            continue
        artist_div = node.select_one("div.album_artist")
        album_div = node.select_one("div.album_name")
        if artist_div is None or album_div is None:
            continue
        artist = artist_div.get_text(" ", strip=True)
        album = album_div.get_text(" ", strip=True)
        if not artist or not album:
            continue

        day_text = ""
        date_div = node.select_one("div.release_date")
        if date_div is not None:
            day_text = date_div.get_text(" ", strip=True)

        # "9 Jan" + the divider's year -> ISO date. The divider is authoritative
        # for the year; rows only carry day + abbreviated month.
        raw_date = None
        normalized = None
        year_m = re.search(r"(\d{4})", month_year or "")
        if day_text and year_m:
            raw_date = f"{day_text} {year_m.group(1)}"
            try:
                normalized = datetime.strptime(raw_date, "%d %b %Y").date().isoformat()
            except ValueError:
                normalized = None

        label_div = node.select_one("div.album_label")
        label = label_div.get_text(" ", strip=True) if label_div else ""

        artist_a = artist_div.select_one("a")
        album_a = album_div.select_one("a")
        items.append({
            "artist": artist,
            "artist_url": _absolute(artist_a.get("href")) if artist_a else None,
            "album": album,
            "album_url": _absolute(album_a.get("href")) if album_a else None,
            "release_date": raw_date or (month_year or None),
            "normalized_date": normalized,
            "context": f"Label: {label}" if label else None,
            "primary_type": "Album",
            "image": None,
        })
    return items


def _fetch_page():
    resp = requests.get(BASE + LIST_PATH, headers=_headers(), timeout=20)
    resp.raise_for_status()
    return resp.text


def fetch_new_albums(force=False):
    """Return (items, cached), persisted like the other Discover sources."""
    if not force:
        fetched_at, items = db.get_discover_cache(SOURCE)
        if items and fetched_at and (time.time() - fetched_at) < _cache_ttl():
            return items, True

    with _lock:
        items = parse_releases(_fetch_page())
        if items:
            with ThreadPoolExecutor(max_workers=_enrich_workers()) as pool:
                list(pool.map(_apply_enrichment, items))

    items = normalize_items(items)
    db.set_discover_cache(SOURCE, items)
    return items, False


class IndieIsNotAGenreDiscovery(DiscoveryPlugin):
    """indieisnotagenre.com release list. Public - just an on/off toggle."""

    key = SOURCE
    label = "Indie is not a genre"
    description = "New and upcoming indie albums from indieisnotagenre.com."
    enabled_setting = "discover_iing_enabled"

    def fetch(self, force=False):
        return fetch_new_albums(force=force)


register(IndieIsNotAGenreDiscovery())
