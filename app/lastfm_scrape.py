"""Scrape Last.fm's personalised "coming soon" releases page.

This page (https://www.last.fm/music/+releases/coming-soon/recommended) is
login-only and not exposed through the Last.fm API, so we fetch it with the
user's session cookie (configured in Settings) and parse the HTML.

Results are cached in memory for a while so opening the Discover page doesn't
re-scrape every time; a manual refresh bypasses the cache.
"""

import re
import threading
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from . import db

BASE = "https://www.last.fm"
LIST_PATH = "/music/+releases/coming-soon/recommended"
# A browser-like User-Agent; Last.fm serves a different page to unknown agents.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

MAX_PAGES = 15

SOURCE = "lastfm"
# Serialise scrapes so two callers can't hammer Last.fm at once.
_lock = threading.Lock()


def _cache_ttl():
    """Cache lifetime in seconds, from the discover_refresh_hours setting."""
    try:
        hours = float(db.get_setting("discover_refresh_hours") or 24)
    except (TypeError, ValueError):
        hours = 24
    return max(hours, 1) * 3600


def _headers():
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    cookie = (db.get_setting("lastfm_cookie") or "").strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _absolute(href):
    if not href:
        return None
    return href if href.startswith("http") else BASE + href


def _parse_date(text):
    """Parse Last.fm's '17 Jun 2026' into ISO 'YYYY-MM-DD', else None."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%d %b %Y").date().isoformat()
    except ValueError:
        return None


def parse_releases(html):
    """Return (items, has_next) parsed from one coming-soon page."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for li in soup.select("li.resource-list--release-list-item-wrap"):
        name_a = li.select_one("h3.resource-list--release-list-item-name a")
        artist_a = li.select_one("p.resource-list--release-list-item-artist a")
        date_p = li.select_one("p.resource-list--release-list-item-date")
        ctx_p = li.select_one("p.resource-list--release-list-item-context")
        img = li.select_one("img")

        album = name_a.get_text(strip=True) if name_a else None
        artist = artist_a.get_text(strip=True) if artist_a else None
        if not album and not artist:
            continue
        date_text = date_p.get_text(strip=True) if date_p else None
        context = None
        if ctx_p:
            # Flatten the inline artist links into a single readable string,
            # collapsing whitespace and tidying spaces before punctuation.
            context = re.sub(r"\s+", " ", ctx_p.get_text(" ", strip=True)).strip()
            context = re.sub(r"\s+([,.])", r"\1", context)

        items.append({
            "album": album,
            "album_url": _absolute(name_a["href"]) if name_a and name_a.has_attr("href") else None,
            "artist": artist,
            "artist_url": _absolute(artist_a["href"]) if artist_a and artist_a.has_attr("href") else None,
            "release_date": date_text,
            "normalized_date": _parse_date(date_text),
            "context": context,
            "primary_type": "Album",  # the coming-soon list is album releases
            "image": img["src"] if img and img.has_attr("src") else None,
        })

    has_next = soup.select_one("li.pagination-next a") is not None
    return items, has_next


def _fetch_page(page):
    resp = requests.get(
        BASE + LIST_PATH,
        params={"page": page},
        headers=_headers(),
        timeout=20,
    )
    resp.raise_for_status()
    return resp.text


def fetch_coming_soon(force=False):
    """Return (items, cached). Raises RuntimeError if no cookie is configured.

    Results are persisted in the DB and reused until older than
    discover_refresh_hours (or *force* re-scrapes now).
    """
    if not force:
        fetched_at, items = db.get_discover_cache(SOURCE)
        if items and fetched_at and (time.time() - fetched_at) < _cache_ttl():
            return items, True

    if not (db.get_setting("lastfm_cookie") or "").strip():
        raise RuntimeError("Last.fm session cookie not configured in Settings")

    with _lock:
        items = []
        for page in range(1, MAX_PAGES + 1):
            html = _fetch_page(page)
            page_items, has_next = parse_releases(html)
            items.extend(page_items)
            if not page_items or not has_next:
                break
            time.sleep(1)  # be polite between page requests

    db.set_discover_cache(SOURCE, items)
    return items, False


def cache_age():
    """Seconds since the cache was last filled, or None if empty."""
    fetched_at, items = db.get_discover_cache(SOURCE)
    if not items or not fetched_at:
        return None
    return int(time.time() - fetched_at)


def check_cookie():
    """Validate the configured Last.fm cookie. Returns (ok, message)."""
    if not (db.get_setting("lastfm_cookie") or "").strip():
        return False, "No Last.fm cookie set."
    try:
        html = _fetch_page(1)
    except requests.RequestException as exc:
        return False, f"Could not reach Last.fm: {exc}"
    items, _ = parse_releases(html)
    if items:
        return True, f"Logged in - {len(items)} releases visible."
    lowered = html.lower()
    if "/login" in lowered or "log in to last.fm" in lowered or "sign in" in lowered:
        return False, "Not logged in - the cookie is missing or expired."
    return False, "No releases found - the cookie may be invalid."
