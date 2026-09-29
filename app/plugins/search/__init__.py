"""Search plugins: your own sites, one click from any release.

Every release in the app carries links to look it up elsewhere (Last.fm,
MusicBrainz, YouTube Music). A search plugin adds more of them: each one names
sites and a URL to search them with, and the frontend shows each as an icon
next to those links, filled in with the release's artist and title.

A site's icon is its own favicon, found once and remembered (see
:func:`site_icon`), unless the user gave one. Importing this package registers
every bundled search plugin.
"""

import hashlib
import json
from urllib.parse import urljoin, urlsplit

import requests

from .. import Plugin, register, get_plugins, get_plugin  # noqa: F401 - re-exported

from ... import db, ratelimit

# Placeholders a search URL may carry. One without any has the query added
# on the end, so "https://example.com/search?q=" works as typed.
PLACEHOLDERS = ("{query}", "{artist}", "{album}")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# A site's favicon hardly ever changes; a site with none is asked again sooner.
_ICON_TTL = 30 * 86400
_ICON_MISS_TTL = 86400


class SearchPlugin(Plugin):
    """A source of search links. Subclasses implement :meth:`sites`."""

    kind = "search"
    enabled_setting = None
    config_fields = []

    def sites(self):
        """[{"name", "url", "icon"}]: each site and how to search it."""
        return []

    def configured(self):
        return bool(self.sites())

    def describe(self):
        data = super().describe()
        data.update({
            "enabled": True,
            "enabled_setting": self.enabled_setting,
            "configured": self.configured(),
            "config_fields": self.config_fields,
            "has_test": False,
        })
        return data


def parse_sites(raw):
    """The stored site list, tidied: every entry has a name and an http(s) URL."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        name = str(item.get("name") or "").strip() or urlsplit(url).hostname or url
        icon = str(item.get("icon") or "").strip()
        out.append({
            "name": name,
            "url": url,
            "icon": icon if icon.lower().startswith(("http://", "https://")) else "",
        })
    return out


def all_sites():
    """Every configured site, across the search plugins, in order."""
    out = []
    for plugin in get_plugins("search"):
        try:
            out.extend(plugin.sites())
        except Exception:  # noqa: BLE001 - one bad plugin can't hide the rest
            continue
    return out


def version(site):
    """A short tag that changes when the site's URL or icon does (cache busting)."""
    return hashlib.sha1((site["url"] + "|" + site["icon"]).encode()).hexdigest()[:10]


def _origin(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _looks_like_image(resp):
    """Would the image cache take this? (It refuses anything not typed image/*.)"""
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    return resp.ok and bool(resp.content) and (
        ctype.startswith("image/") or not ctype
    ) and ctype != "image/svg+xml"


def _get(url):
    return ratelimit.get(url, timeout=(5, 10), headers={"User-Agent": _USER_AGENT},
                         allow_redirects=True, max_wait=20)


def _find_icon(origin):
    """The site's icon URL: what its front page declares, else /favicon.ico."""
    from bs4 import BeautifulSoup

    candidates = []
    try:
        page = _get(origin + "/")
        if page.ok and "html" in (page.headers.get("Content-Type") or ""):
            soup = BeautifulSoup(page.text[:200_000], "html.parser")
            for link in soup.find_all("link", href=True):
                rels = [r.lower() for r in (link.get("rel") or [])]
                if "icon" not in rels and "apple-touch-icon" not in rels:
                    continue
                href = link["href"].strip()
                # An SVG can't go through the image cache (it's served as a
                # raster), so it's left for the .ico.
                if href.lower().split("?")[0].endswith(".svg") or \
                        (link.get("type") or "").lower() == "image/svg+xml":
                    continue
                candidates.append(urljoin(page.url, href))
    except requests.RequestException:
        pass
    candidates.append(origin + "/favicon.ico")
    for url in candidates:
        try:
            if _looks_like_image(_get(url)):
                return url
        except requests.RequestException:
            continue
    return None


def site_icon(site):
    """The image URL to show for *site*: the one given, else its favicon.

    Looked up once per site (its front page, then /favicon.ico) and remembered;
    None when the site has neither.
    """
    if site.get("icon"):
        return site["icon"]
    origin = _origin(site["url"])
    key = "favicon:" + origin.lower()
    cached = db.get_json_cache(key, max_age=_ICON_TTL)
    if cached is not None:
        if cached.get("url"):
            return cached["url"]
        # A site with no icon is asked again after a day, not a month.
        if db.get_json_cache(key, max_age=_ICON_MISS_TTL) is not None:
            return None
    url = _find_icon(origin)
    db.set_json_cache(key, {"url": url})
    return url


# Register the bundled search plugins (import side effect calls register()).
from . import custom  # noqa: E402,F401
