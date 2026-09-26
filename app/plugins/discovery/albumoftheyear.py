"""Discover source: albumoftheyear.org's upcoming releases page.

Parses the ``.albumBlock`` grid on https://www.albumoftheyear.org/upcoming/ -
each block carries the artist (``.artistTitle``), album (``.albumTitle``), a
month-day date (``.type``, e.g. "Nov 14") and a lazy-loaded cover image.

Heads-up: AOTY sits behind Cloudflare bot protection, which answers 403
("Just a moment...") to server-side requests. Two ways through, both the
user's own clearance:

- paste the ``cf_clearance`` cookie from a browser that solved the challenge,
  plus that browser's exact User-Agent (Cloudflare binds the cookie to both,
  and to the IP) -- short-lived, so it needs re-pasting when it expires;
- or turn on a challenge solver (app/plugins/solver, e.g. FlareSolverr), which
  solves the challenge in a real browser and returns a fresh cookie that gets
  stored here.

A block is reported as the source's error in the Plugins tab / Discover status
line -- with the stored cookie's age, since an expired cookie is the usual
cause -- rather than failing silently.
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

# Cloudflare fingerprints the TLS handshake as well as headers: a perfectly
# valid cf_clearance cookie is still rejected when it arrives over a
# python-requests/curl handshake. curl_cffi replays a real browser's TLS
# fingerprint, which (with the cookie) passes. Optional: without it we fall
# back to plain requests and surface the block in the source status.
try:
    from curl_cffi import requests as cffi_requests
except ImportError:  # pragma: no cover - optional dependency
    cffi_requests = None

# Impersonation targets, newest first (availability depends on the installed
# curl_cffi version). Cloudflare ties a clearance cookie to the browser that
# earned it, so the order follows the configured User-Agent.
_FIREFOX_TARGETS = ("firefox135", "firefox133", "firefox")
_CHROME_TARGETS = ("chrome136", "chrome133a", "chrome131", "chrome")

from ... import db
from .. import solver
from . import DiscoveryPlugin, normalize_items, register
# Shared enrichment pipeline (cached Last.fm / MusicBrainz lookups).
from .metacritic import _apply_enrichment, _cache_ttl, _enrich_workers, _headers

BASE = "https://www.albumoftheyear.org"
LIST_PATH = "/upcoming/"
SOURCE = "aoty"

_lock = threading.Lock()


def _absolute(href):
    if not href:
        return None
    return href if href.startswith("http") else BASE + href


def _parse_date(text):
    """AOTY dates are month-day ("Nov 14"): the year is inferred as the next
    occurrence (upcoming page shows the coming months, wrapping past NYE)."""
    if not text:
        return None
    try:
        parsed = datetime.strptime(text.strip(), "%b %d")
    except ValueError:
        return None
    today = date.today()
    year = today.year
    candidate = date(year, parsed.month, parsed.day)
    # More than a month in the past -> it's next year's date.
    if (today - candidate).days > 31:
        candidate = date(year + 1, parsed.month, parsed.day)
    return candidate.isoformat()


def parse_releases(html):
    """Parse the albumBlock grid into discover items."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for block in soup.select("div.albumBlock"):
        artist_div = block.select_one(".artistTitle")
        album_div = block.select_one(".albumTitle")
        if artist_div is None or album_div is None:
            continue
        artist = artist_div.get_text(" ", strip=True)
        album = album_div.get_text(" ", strip=True)
        if not artist or not album:
            continue

        # ".type" packs date and format together: "Jul 9 • LP" / "Nov 14 • EP".
        date_div = block.select_one(".type")
        type_text = date_div.get_text(" ", strip=True) if date_div else ""
        parts = [p.strip() for p in type_text.split("•")]
        raw_date = parts[0] if parts and parts[0] else None
        kind = parts[1].lower() if len(parts) > 1 else ""
        if kind == "ep":
            primary_type = "EP"
        elif kind == "single":
            primary_type = "Single"
        else:  # LP, Mixtape, Remix, Compilation, ... all land under Album
            primary_type = "Album"

        img = block.select_one("img")
        image = None
        if img is not None:
            image = img.get("data-src") or img.get("src")
            if image and image.startswith("data:"):
                image = None

        album_a = block.select_one("a[href*='/album/']")
        items.append({
            "artist": artist,
            "artist_url": None,
            "album": album,
            "album_url": _absolute(album_a.get("href")) if album_a else None,
            "release_date": raw_date,
            "normalized_date": _parse_date(raw_date),
            "context": None,
            "primary_type": primary_type,
            "image": image,
        })
    return items


def _cookie():
    return (db.get_setting("aoty_cookie") or "").strip()


def _user_agent():
    return (db.get_setting("aoty_user_agent") or "").strip()


def _clearance_issued_at(cookie=None):
    """Epoch seconds the stored cf_clearance was issued, or None.

    The cookie's value is ``<token>-<issued>-<version>-...``, so its own age is
    readable without asking Cloudflare -- which is the difference between "your
    cookie went stale" and "something else is wrong".
    """
    cookie = _cookie() if cookie is None else cookie
    match = re.search(r"cf_clearance=[^;]*?-(\d{10})-", cookie)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _clearance_age_text():
    """Human age of the stored clearance cookie ('4 hours old'), or None."""
    issued = _clearance_issued_at()
    if not issued:
        return None
    seconds = max(0, int(time.time() - issued))
    if seconds < 3600:
        return f"{seconds // 60} minutes old"
    if seconds < 86400:
        return f"{seconds // 3600} hours old"
    return f"{seconds // 86400} days old"


def _aoty_headers():
    """Headers carrying the user's Cloudflare cookie and matching User-Agent.

    A browser on the same network passes because it solved Cloudflare's JS
    challenge and holds a ``cf_clearance`` cookie. Pasting that cookie (plus
    the exact User-Agent of the browser that earned it -- Cloudflare binds the
    cookie to it) lets server-side fetches ride the same clearance. Everything
    else (Accept, Sec-Fetch-*, ...) is left to curl_cffi's browser profile: a
    hand-written Accept header is exactly the kind of mismatch Cloudflare
    scores against.
    """
    headers = {} if cffi_requests is not None else _headers()
    cookie = _cookie()
    ua = _user_agent()
    if cookie:
        headers["Cookie"] = cookie
    if ua:
        headers["User-Agent"] = ua
    return headers


def _impersonate_targets():
    """Fingerprints to try, matching the configured User-Agent's browser."""
    ua = _user_agent().lower()
    if "chrome/" in ua or "edg/" in ua:
        return _CHROME_TARGETS + _FIREFOX_TARGETS
    return _FIREFOX_TARGETS + _CHROME_TARGETS


def _get(url, headers):
    """Fetch with a browser TLS fingerprint when curl_cffi is available."""
    if cffi_requests is not None:
        for target in _impersonate_targets():
            try:
                return cffi_requests.get(url, headers=headers,
                                         impersonate=target, timeout=20)
            except (ValueError, TypeError):
                continue  # this curl_cffi doesn't know the target; try older
    return requests.get(url, headers=headers, timeout=20)


# --- getting past the challenge ---------------------------------------------

def _solve(url):
    """Fetch *url* through the configured solver and keep its clearance.

    A solver drives a real browser, so it answers the challenge the way the
    user's own browser does. The cookie and User-Agent it comes back with are
    stored as if they had been pasted by hand, so the cheap direct fetches keep
    working until Cloudflare challenges again. Returns the HTML, or None when
    no solver is set up.
    """
    result = solver.fetch_html(url)
    if not result:
        return None
    if result.get("cookie"):
        db.set_setting("aoty_cookie", result["cookie"])
    if result.get("user_agent"):
        db.set_setting("aoty_user_agent", result["user_agent"])
    return result.get("html") or None


def _blocked_message():
    """Why the fetch was blocked, and what the user can do about it."""
    if solver.active() is not None:
        return ("Blocked by AOTY's Cloudflare protection (HTTP 403) and the "
                "configured solver could not clear it either")
    age = _clearance_age_text()
    if _cookie():
        return ("Blocked by AOTY's Cloudflare protection (HTTP 403) - the "
                "cf_clearance cookie" + (f" is {age} and" if age else "")
                + " is no longer accepted. Paste a fresh one (with the exact "
                  "User-Agent of the same browser), or set up a challenge "
                  "solver under Settings > Discovery to refresh it automatically.")
    return ("Blocked by AOTY's Cloudflare protection (HTTP 403) - paste your "
            "browser's cf_clearance cookie below, or set up a challenge solver "
            "under Settings > Discovery.")


def _fetch_page():
    url = BASE + LIST_PATH
    resp = _get(url, _aoty_headers())
    if resp.status_code == 403:
        # A stored cookie that stopped working is the normal case: let the
        # solver earn a new one and carry on rather than failing the scrape.
        html = _solve(url)
        if html and "albumBlock" in html:
            return html
        if html is not None:
            retry = _get(url, _aoty_headers())
            if retry.status_code == 200:
                return retry.text
        raise RuntimeError(_blocked_message())
    resp.raise_for_status()
    return resp.text


def fetch_upcoming(force=False):
    """Return (items, cached), persisted like the other Discover sources."""
    if not force:
        fetched_at, items = db.get_discover_cache(SOURCE)
        if items and fetched_at and (time.time() - fetched_at) < _cache_ttl():
            return items, True

    with _lock:
        items = parse_releases(_fetch_page())
        # Blocks usually include the cover already; enrichment fills genres and
        # any covers the lazy-loader hid, all from the shared cache.
        if items:
            with ThreadPoolExecutor(max_workers=_enrich_workers()) as pool:
                list(pool.map(_apply_enrichment_keep_image, items))

    items = normalize_items(items)
    db.set_discover_cache(SOURCE, items)
    return items, False


def _apply_enrichment_keep_image(it):
    """Enrich genres/image but never clobber a cover scraped from the page."""
    own = it.get("image")
    _apply_enrichment(it)
    if own:
        it["image"] = own


class AlbumOfTheYearDiscovery(DiscoveryPlugin):
    """albumoftheyear.org upcoming grid. Public page, but Cloudflare-guarded."""

    key = SOURCE
    label = "Album of the Year"
    description = ("Upcoming releases from albumoftheyear.org. Cloudflare "
                   "blocks server-side fetches unless you lend it your "
                   "browser's clearance cookie below.")
    enabled_setting = "discover_aoty_enabled"
    has_test = True
    config_fields = [
        {"key": "aoty_cookie", "label": "Cookie header", "type": "textarea",
         "secret": True,
         "placeholder": "cf_clearance=...",
         "help": "On albumoftheyear.org, open DevTools > Network, click any "
                 "request, copy the whole Cookie request header (at minimum "
                 "cf_clearance=...). Expires when Cloudflare re-challenges."},
        {"key": "aoty_user_agent", "label": "Browser User-Agent", "type": "text",
         "placeholder": "Mozilla/5.0 ...",
         "help": "Paste your browser's exact User-Agent (find it at "
                 "about:version or in the same DevTools request headers) - "
                 "Cloudflare ties the cookie to it."},
    ]

    def fetch(self, force=False):
        return fetch_upcoming(force=force)

    def check(self):
        """Try a live fetch and report how many releases came back."""
        try:
            items = parse_releases(_fetch_page())
        except (RuntimeError, requests.RequestException) as exc:
            return (False, str(exc))
        if not items:
            return (False, "Fetched the page but found no releases - "
                           "the page layout may have changed.")
        age = _clearance_age_text()
        detail = f" (clearance cookie {age})" if age else ""
        return (True, f"OK - {len(items)} upcoming releases visible{detail}.")


register(AlbumOfTheYearDiscovery())
