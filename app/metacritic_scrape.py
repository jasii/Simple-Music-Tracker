"""Scrape Metacritic's upcoming album release calendar.

The page (https://www.metacritic.com/browse/albums/release-date/coming-soon/date)
is public - no login or cookie needed - so we just fetch the HTML with a
browser-like User-Agent and parse the dated release table.

Results are cached in memory (same TTL as the other Discover sources) so the
page doesn't re-scrape on every open; a manual refresh bypasses the cache.
"""

import re
import threading
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from . import db, lastfm, musicbrainz

BASE = "https://www.metacritic.com"
LIST_PATH = "/browse/albums/release-date/coming-soon/date"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

SOURCE = "metacritic"
# Serialise scrapes so two callers can't fetch + enrich at once.
_lock = threading.Lock()

# Metacritic gives only artist/album/date. We enrich each release with cover art
# and genre tags looked up elsewhere. The lookups are slow (MusicBrainz is paced
# at 1 req/sec) so results are cached per (artist, album) for a good while -
# image and genre are stable, and a nightly re-scrape reuses this cache.
_MAX_GENRES = 3
_ENRICH_TTL = 7 * 24 * 3600  # 7 days
_enrich_cache = {}
_enrich_lock = threading.Lock()


def _enrich(artist, album):
    """Return {image, genres} for a release, cached. Best-effort, never raises."""
    key = ((artist or "").lower(), (album or "").lower())
    with _enrich_lock:
        hit = _enrich_cache.get(key)
        if hit and (time.time() - hit["at"]) < _ENRICH_TTL:
            return hit

    image = None
    genres = []

    # Last.fm album.getInfo: image + tags in one call (needs an API key).
    try:
        info = lastfm.get_album_info(artist, album)
        image = info.get("image_url")
        genres = info.get("genres") or []
    except Exception:  # noqa: BLE001
        pass

    # Fallback to MusicBrainz cover art + genres if Last.fm came up short.
    if not image or not genres:
        try:
            mb = musicbrainz.find_release_art(artist, album)
            image = image or mb.get("image_url")
            if not genres:
                genres = mb.get("genres") or []
        except Exception:  # noqa: BLE001
            pass

    # Last resort for the image: the artist's own Last.fm photo.
    if not image:
        try:
            image = (lastfm.get_artist_info(artist) or {}).get("image_url")
        except Exception:  # noqa: BLE001
            pass

    # Tidy genres: dedupe case-insensitively, keep order, cap the count.
    seen = set()
    clean = []
    for g in genres:
        gl = g.strip().lower()
        if g.strip() and gl not in seen:
            seen.add(gl)
            clean.append(g.strip())
        if len(clean) >= _MAX_GENRES:
            break

    result = {"image": image, "genres": clean, "at": time.time()}
    with _enrich_lock:
        _enrich_cache[key] = result
    return result


def _cache_ttl():
    """Cache lifetime in seconds, from the discover_refresh_hours setting."""
    try:
        hours = float(db.get_setting("discover_refresh_hours") or 24)
    except (TypeError, ValueError):
        hours = 24
    return max(hours, 1) * 3600


def _headers():
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _absolute(href):
    if not href:
        return None
    return href if href.startswith("http") else BASE + href


def _parse_date(text):
    """Parse Metacritic's '19 June 2026' into ISO 'YYYY-MM-DD', else None."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def parse_releases(html):
    """Return items parsed from the dated 'release calendar' table.

    The calendar is the first ``table.musicTable``: ``tr.module`` header rows
    carry a date that applies to every release row beneath it until the next
    header. The later 'Anticipated Future Releases' table has no firm dates and
    is intentionally skipped.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("div.releaseCalendar table.musicTable") \
        or soup.select_one("table.musicTable")
    if table is None:
        return []

    items = []
    current_date = None
    for tr in table.select("tr"):
        if "module" in (tr.get("class") or []):
            head = tr.select_one("th")
            current_date = head.get_text(strip=True) if head else None
            continue

        artist_td = tr.select_one("td.artistName")
        album_td = tr.select_one("td.albumTitle")
        if artist_td is None or album_td is None:
            continue

        artist_a = artist_td.select_one("a")
        artist = artist_td.get_text(strip=True)
        album = album_td.get_text(strip=True)
        if not artist and not album:
            continue

        comment_td = tr.select_one("td.dataComment")
        context = comment_td.get_text(strip=True) if comment_td else None
        context = context or None

        # Metacritic is an album calendar; its note sometimes flags EP/single.
        low = (context or "").lower()
        if "[ep]" in low or " ep" in low:
            primary_type = "EP"
        elif "single" in low:
            primary_type = "Single"
        else:
            primary_type = "Album"

        items.append({
            "album": album,
            "album_url": None,
            "artist": artist,
            "artist_url": _absolute(artist_a["href"])
            if artist_a and artist_a.has_attr("href") else None,
            "release_date": current_date,
            "normalized_date": _parse_date(current_date),
            "context": context,
            "primary_type": primary_type,
            "image": None,
        })

    return items


def _fetch_page():
    resp = requests.get(BASE + LIST_PATH, headers=_headers(), timeout=20)
    resp.raise_for_status()
    return resp.text


def fetch_coming_soon(force=False):
    """Return (items, cached).

    Results are persisted in the DB and reused until older than
    discover_refresh_hours (or *force* re-scrapes now).
    """
    if not force:
        fetched_at, items = db.get_discover_cache(SOURCE)
        if items and fetched_at and (time.time() - fetched_at) < _cache_ttl():
            return items, True

    with _lock:
        items = parse_releases(_fetch_page())
        for it in items:
            extra = _enrich(it.get("artist"), it.get("album"))
            it["image"] = extra["image"]
            it["genres"] = extra["genres"]

    db.set_discover_cache(SOURCE, items)
    return items, False


def cache_age():
    """Seconds since the cache was last filled, or None if empty."""
    fetched_at, items = db.get_discover_cache(SOURCE)
    if not items or not fetched_at:
        return None
    return int(time.time() - fetched_at)
