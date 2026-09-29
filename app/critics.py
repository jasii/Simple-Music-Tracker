"""Critic scores for new releases, from Metacritic's new-releases list.

The Discover sources list records before they're out, so none of them carry a
score. Metacritic's "new releases" browse page does: every recent album with
its Metascore. Its first few pages (a hundred albums each, newest first --
two or three months) are read on the Discover schedule and stored, and the
Discover feed looks each row up by artist and title.

Only the stored answer is ever read inside a request; a stale or missing one
starts a refresh in the background.
"""

import re
import threading
import time

import requests
from bs4 import BeautifulSoup

from . import db, ratelimit

BASE = "https://www.metacritic.com"
LIST_PATH = "/browse/albums/release-date/new-releases/date"
PAGES = 3
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_CACHE_KEY = "critics:metacritic"
_lock = threading.Lock()
_running = threading.Event()


def key(artist, album):
    """Loose match key: case, punctuation and spacing don't count."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())  # noqa: E731
    return norm(artist) + "|" + norm(album)


def parse_scores(html):
    """{key: {"score", "url"}} from one page of the new-releases list."""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for cell in soup.select("td.clamp-summary-wrap"):
        title = cell.select_one("a.title h3")
        artist = cell.select_one(".clamp-details .artist")
        score = cell.select_one(".clamp-score-wrap .metascore_w")
        link = cell.select_one("a.title")
        if not title or not artist or not score:
            continue
        text = score.get_text(strip=True)
        if not text.isdigit():
            continue  # "tbd": not enough reviews yet
        name = re.sub(r"^by\s+", "", artist.get_text(strip=True))
        href = link.get("href") if link else None
        out[key(name, title.get_text(strip=True))] = {
            "score": int(text),
            "url": (BASE + href) if href and href.startswith("/") else href,
        }
    return out


def _ttl():
    try:
        hours = float(db.get_setting("discover_refresh_hours") or 24)
    except (TypeError, ValueError):
        hours = 24
    return max(hours, 1) * 3600


def refresh():
    """Read the list again and store it. Returns how many scores were found."""
    with _lock:
        scores = {}
        for page in range(PAGES):
            try:
                resp = ratelimit.get(
                    BASE + LIST_PATH, params={"page": page} if page else None,
                    headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
                    timeout=(5, 20),
                )
            except requests.RequestException:
                break
            if not resp.ok:
                break
            found = parse_scores(resp.text)
            if not found:
                break
            scores.update(found)
            time.sleep(1)
        if scores:
            db.set_json_cache(_CACHE_KEY, {"fetched_at": time.time(), "scores": scores})
        return len(scores)


def refresh_async():
    """Refresh in a daemon thread unless one is running already."""
    if _running.is_set():
        return

    def run():
        try:
            refresh()
        except Exception:  # noqa: BLE001 - scores are decoration
            pass
        finally:
            _running.clear()

    _running.set()
    threading.Thread(target=run, daemon=True).start()


def scores():
    """The stored {key: {"score", "url"}}; kicks a refresh when it's stale."""
    entry = db.get_json_cache(_CACHE_KEY) or {}
    if time.time() - (entry.get("fetched_at") or 0) > _ttl():
        refresh_async()
    return entry.get("scores") or {}
