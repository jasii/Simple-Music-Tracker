"""Background scheduler for periodic refreshes.

A single daemon thread wakes once a minute and runs two independent timers,
re-read from settings each tick so changes take effect without a restart:
- refreshing all subscribed artists (check_interval_hours), and
- re-scraping the Discover sources (discover_refresh_hours, e.g. once a day).
"""

import threading
import time

from . import db, lastfm_scrape, tracker

_thread = None
_started = False


def _hours(setting, default, floor):
    try:
        return max(float(db.get_setting(setting) or default), floor)
    except (TypeError, ValueError):
        return default


def _loop():
    # Small initial delay so the web server is up before the first heavy cycle.
    time.sleep(30)
    last_artist = 0.0
    # Don't scrape Discover the instant we boot; the page fills it on demand and
    # the scheduler keeps it fresh on the configured cadence after that.
    last_discover = time.time()

    while True:
        now = time.time()

        if now - last_artist >= _hours("check_interval_hours", 12, 0.25) * 3600:
            last_artist = now
            try:
                tracker.enqueue_all_subscribed()
            except Exception:  # noqa: BLE001 - never let the scheduler thread die
                pass

        if (db.get_setting("lastfm_cookie") or "").strip():
            if now - last_discover >= _hours("discover_refresh_hours", 24, 1) * 3600:
                last_discover = now
                try:
                    lastfm_scrape.fetch_coming_soon(force=True)
                except Exception:  # noqa: BLE001
                    pass

        time.sleep(60)


def start():
    global _thread, _started
    if _started:
        return
    _started = True
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
