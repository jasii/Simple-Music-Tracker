"""What the newer Discover sources share: fetching, caching, enrichment, RSS.

Every source ends the same way -- normalise the rows, fill in cover art and
genre tags where the source gave none, store them as that source's cache --
and several read RSS. That lives here so each source's own file is only what
is particular to it: where to look, and how to read what it finds.
"""

import html
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

from ... import db, lastfm, musicbrainz, ratelimit
from . import normalize_items
from .metacritic import _cache_ttl, _enrich_workers

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_locks = {}
_locks_guard = threading.Lock()


def get(url, params=None, timeout=20, **kw):
    """GET with a browser User-Agent, within the site's request budget.

    Raises for an HTTP error, and ratelimit.Throttled when the site is
    rate limiting us.
    """
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    headers.update(kw.pop("headers", {}) or {})
    resp = ratelimit.get(url, params=params, headers=headers, timeout=timeout, **kw)
    resp.raise_for_status()
    return resp


def _lock(source):
    with _locks_guard:
        return _locks.setdefault(source, threading.Lock())


def item_key(it):
    return ((it.get("artist") or "").strip().lower(), (it.get("album") or "").strip().lower())


def _fill(it):
    """Cover and tags from Last.fm; MusicBrainz only when there's no cover.

    Lighter than the older sources' enrichment on purpose: MusicBrainz allows
    a request a second, and a radio station's week or a blog feed is dozens
    of records -- asking it for tags alone would take minutes. (The artist's
    own genres fill the row in anyway.) Both answers are cached.
    """
    artist, album = it.get("artist"), it.get("album")
    try:
        info = lastfm.get_album_info(artist, album) or {}
    except Exception:  # noqa: BLE001 - enrichment is best-effort
        info = {}
    it["image"] = it.get("image") or info.get("image_url")
    it["genres"] = it.get("genres") or (info.get("genres") or [])[:3]
    if not it["image"]:
        try:
            it["image"] = (musicbrainz.find_release_art(artist, album) or {}).get("image_url")
        except Exception:  # noqa: BLE001
            pass


def enrich(items):
    """Cover art and genre tags for rows that came without them."""
    todo = [it for it in items if not it.get("image") or not it.get("genres")]
    if todo:
        with ThreadPoolExecutor(max_workers=_enrich_workers()) as pool:
            list(pool.map(_fill, todo))


def cached_scrape(source, force, scrape, *, enrich_items=True, keep_days=None):
    """(items, cached) for a source, the way every Discover source answers.

    Served from the stored scrape while it's fresh. Otherwise *scrape()* runs
    (one at a time per source), its rows are enriched, normalised and stored.
    With *keep_days*, rows from earlier scrapes stay while their ``posted``
    date is that recent: a feed that only shows its last twenty posts, or a
    radio station's last few days, still adds up to a few weeks of finds.
    """
    if not force:
        fetched_at, items = db.get_discover_cache(source)
        if items and fetched_at and (time.time() - fetched_at) < _cache_ttl():
            return items, True

    with _lock(source):
        fresh = scrape() or []
        # One row per release, the first (usually newest) wins.
        seen = set()
        items = []
        for it in fresh:
            k = item_key(it)
            if not k[0] or k in seen:
                continue
            seen.add(k)
            items.append(it)
        if keep_days:
            cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
            _fetched, older = db.get_discover_cache(source)
            for it in older or []:
                k = item_key(it)
                if k[0] and k not in seen and (it.get("posted") or "") >= cutoff:
                    seen.add(k)
                    items.append(it)
        if enrich_items and items:
            enrich(items)

    items = normalize_items(items)
    db.set_discover_cache(source, items)
    return items, False


# --- dates ------------------------------------------------------------------

def iso_date(value):
    """An ISO date from an RSS pubDate, an ISO timestamp or a date, else None."""
    if not value:
        return None
    text = str(value).strip()
    # "Sept" (DIY) is not a month the RFC parser knows.
    text = re.sub(r"\bSept\b", "Sep", text)
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DATE_IN_TEXT = re.compile(
    r"\b(?:out|due(?: out)?|arrives?|arriving|lands?|released?|releasing|drops?|coming)"
    r"(?:\s+\w+){0,4}?\s+(?:on\s+)?"
    r"(?:(?P<d1>\d{1,2})(?:st|nd|rd|th)?\s+(?P<m1>[A-Za-z]{3,9})\.?|(?P<m2>[A-Za-z]{3,9})\.?\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?)"
    r"(?:,?\s+(?P<y>\d{4}))?",
    re.I,
)


def release_date_in(text, published=None):
    """A release date mentioned in *text* ("out November 7", "due 7th Nov").

    Without a year it's the next such date on or after *published* (an ISO
    date, default today). None when there's nothing like one.
    """
    if not text:
        return None
    base = date.fromisoformat(published) if published else date.today()
    for m in _DATE_IN_TEXT.finditer(text):
        month_name = (m.group("m1") or m.group("m2") or "")[:3].lower()
        month = _MONTHS.get(month_name)
        day = int(m.group("d1") or m.group("d2") or 0)
        if not month or not 1 <= day <= 31:
            continue
        year = int(m.group("y")) if m.group("y") else base.year
        try:
            when = date(year, month, day)
        except ValueError:
            continue
        if not m.group("y") and when < base - timedelta(days=7):
            when = date(year + 1, month, day)
        return when.isoformat()
    return None


# --- RSS ----------------------------------------------------------------------

def _text(value):
    """Tag contents as plain text: CDATA unwrapped, entities and tags gone."""
    value = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", value or "", flags=re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _tag(block, name):
    m = re.search(rf"<{name}(?:\s[^>]*)?>(.*?)</{name}>", block, re.S | re.I)
    return m.group(1) if m else ""


def parse_rss(text):
    """The items of an RSS feed: [{title, link, published, categories, summary, image}].

    Read with patterns rather than an XML parser on purpose: feeds in the wild
    declare namespaces they don't use, or use ones they don't declare, and a
    strict parser refuses the lot over one stray prefix.
    """
    out = []
    for block in re.findall(r"<item[\s>](.*?)</item>", text or "", re.S | re.I):
        image = None
        for pattern in (r"<media:thumbnail[^>]*url=\"([^\"]+)\"",
                        r"<media:content[^>]*url=\"([^\"]+)\"[^>]*(?:medium=\"image\"|type=\"image)",
                        r"<enclosure[^>]*url=\"([^\"]+)\"[^>]*type=\"image"):
            m = re.search(pattern, block, re.I)
            if m:
                image = html.unescape(m.group(1))
                break
        link = _text(_tag(block, "link")) or _text(_tag(block, "guid"))
        out.append({
            "title": _text(_tag(block, "title")),
            "link": html.unescape(link.split("?utm_")[0]),
            "published": iso_date(_text(_tag(block, "pubDate"))) or iso_date(_text(_tag(block, "dc:date"))),
            "categories": [_text(c) for c in re.findall(r"<category[^>]*>(.*?)</category>", block, re.S | re.I)],
            "summary": _text(_tag(block, "description"))[:1500],
            "content": _text(_tag(block, "content:encoded"))[:3000],
            "image": image,
        })
    return out


def norm_type(value):
    """Album / EP / Single from whatever a source calls it (Album otherwise)."""
    low = (value or "").strip().lower()
    if low == "ep":
        return "EP"
    if low == "single":
        return "Single"
    return "Album"
