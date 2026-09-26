"""Background scheduler for periodic refreshes.

A single daemon thread wakes once a minute and runs two independent timers,
re-read from settings each tick so changes take effect without a restart:
- refreshing all subscribed artists (check_interval_hours), and
- re-scraping the Discover sources (discover_refresh_hours, e.g. once a day).
"""

import threading
import time

from . import db, downloads, gaps, grabber, hype, plugins, scans, tracker
# Importing the discovery package registers its plugins with the registry.
from .plugins import discovery as _discovery  # noqa: F401

_thread = None
_started = False

# Last run of each periodic job, so the settings page can show what runs when
# instead of the user guessing from interval settings alone.
_tasks = {
    "artists": {"label": "Refresh followed artists", "setting": "check_interval_hours",
                "unit": "hours", "default": 12, "last": 0.0},
    "discover": {"label": "Re-scrape Discover sources", "setting": "discover_refresh_hours",
                 "unit": "hours", "default": 24, "last": 0.0},
    "autograb": {"label": "Automatic grabbing", "setting": "autograb_interval_minutes",
                 "unit": "minutes", "default": 60, "last": 0.0,
                 "enabled_setting": "autograb_enabled"},
    "compact": {"label": "Compact database", "setting": None,
                "unit": "hours", "default": 24, "last": 0.0},
    "webhooks": {"label": "Deliver due webhooks", "setting": None,
                 "unit": "minutes", "default": 1, "last": 0.0},
    "library_scan": {"label": "Scheduled library scans", "setting": None,
                     "unit": "minutes", "default": 1, "last": 0.0},
    "downloads": {"label": "Follow Soulseek downloads", "setting": None,
                  "unit": "minutes", "default": 1, "last": 0.0},
    "tracklists": {"label": "Fetch tracklists for the incomplete check",
                   "setting": None, "unit": "minutes", "default": 1, "last": 0.0},
    # Weekly rather than every N hours, so its own next-run is reported
    # separately (see hype.schedule_state).
    "hype_playlist": {"label": "Rebuild the Get Hyped playlist",
                      "setting": None, "unit": "hours", "default": 168,
                      "enabled_setting": "hype_playlist_weekly", "last": 0.0},
}
_tasks_lock = threading.Lock()


def _ran(key):
    with _tasks_lock:
        _tasks[key]["last"] = time.time()


def get_tasks():
    """Each periodic job: interval, when it last ran and when it runs next."""
    now = time.time()
    out = []
    with _tasks_lock:
        snapshot = {k: dict(v) for k, v in _tasks.items()}
    for key, task in snapshot.items():
        if task["setting"]:
            interval = _hours(task["setting"], task["default"], 0) * 3600 \
                if task["unit"] == "hours" else \
                _minutes(task["setting"], task["default"], 0) * 60
        else:
            interval = task["default"] * (3600 if task["unit"] == "hours" else 60)
        enabled_setting = task.get("enabled_setting")
        active = True
        if enabled_setting:
            active = (db.get_setting(enabled_setting) or "false").strip().lower() == "true"
        last = task["last"] or None
        out.append({
            "key": key,
            "label": task["label"],
            "active": active,
            "interval_seconds": int(interval),
            "last_run": last,
            "next_run": (last + interval) if last else (now + interval),
        })
    return out


def _minutes(setting, default, floor):
    try:
        return max(float(db.get_setting(setting) or default), floor)
    except (TypeError, ValueError):
        return default


def _hours(setting, default, floor):
    try:
        return max(float(db.get_setting(setting) or default), floor)
    except (TypeError, ValueError):
        return default


def _loop():
    # Small initial delay so the web server is up before the first heavy cycle.
    time.sleep(30)
    last_artist = 0.0
    # Checked on the first tick, not a day from now: a database left fragmented
    # by a big purge shouldn't stay that way until the process has run 24h.
    last_compact = 0.0
    last_autograb = 0.0
    # Don't scrape Discover the instant we boot; the page fills it on demand and
    # the scheduler keeps it fresh on the configured cadence after that.
    last_discover = time.time()

    while True:
        now = time.time()

        if now - last_artist >= _hours("check_interval_hours", 12, 0.25) * 3600:
            last_artist = now
            try:
                # Only the stale ones, oldest first, so a reboot resumes instead
                # of re-syncing every artist alphabetically from scratch.
                tracker.enqueue_all_subscribed(stale_only=True)
                _ran("artists")
            except Exception:  # noqa: BLE001 - never let the scheduler thread die
                pass

        if now - last_discover >= _hours("discover_refresh_hours", 24, 1) * 3600:
            last_discover = now
            # Re-scrape every discovery source that's enabled and configured
            # (Last.fm needs a cookie; Metacritic is public).
            _ran("discover")
            for plugin in plugins.get_plugins("discovery"):
                if not plugin.configured():
                    continue
                try:
                    plugin.fetch(force=True)
                except Exception:  # noqa: BLE001 - never let the scheduler thread die
                    pass

        # Libraries re-scan on their own schedules. Enqueueing rather than
        # running means several falling due together queue up instead of
        # fighting over the database.
        try:
            scans.ensure_library_runners()
            for key in scans.due_libraries():
                scans.enqueue(key)
                _ran("library_scan")
        except Exception:  # noqa: BLE001 - never let the scheduler thread die
            pass

        # Automatic grabbing: only does anything when it's switched on, and
        # the pass itself decides what (if anything) is due.
        if now - last_autograb >= _minutes("autograb_interval_minutes", 60, 5) * 60:
            last_autograb = now
            try:
                grabber.run_pass()
                _ran("autograb")
            except Exception:  # noqa: BLE001 - never let the scheduler thread die
                pass

        # Soulseek downloads: fold in what slskd says, retry what failed.
        try:
            if downloads.poll().get("jobs"):
                _ran("downloads")
        except Exception:  # noqa: BLE001 - never let the scheduler thread die
            pass

        # A few of the tracklists the incomplete-album check is waiting on.
        # MusicBrainz allows one request a second; ten keeps the tick short.
        try:
            if gaps.warm_tracklists(limit=10):
                _ran("tracklists")
        except Exception:  # noqa: BLE001 - never let the scheduler thread die
            pass

        # Once a day, truncate the WAL and reclaim free pages -- but only when
        # enough of the file is actually free (a VACUUM rewrites it, so it isn't
        # something to do on a whim).
        if now - last_compact >= 24 * 3600:
            last_compact = now
            try:
                db.compact(min_free_ratio=0.25)
                _ran("compact")
            except Exception:  # noqa: BLE001 - never let the scheduler thread die
                pass

        # The weekly "Get Hyped" playlist, when it's switched on and its slot
        # has passed. The function decides; this only asks.
        try:
            if hype.run_if_due():
                _ran("hype_playlist")
        except Exception:  # noqa: BLE001 - never let the scheduler thread die
            pass

        # Fire any 'notify' webhooks that have reached their trigger time.
        try:
            tracker.process_pending_webhooks()
            _ran("webhooks")
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
