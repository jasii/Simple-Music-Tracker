"""Releases saved for later: the release-day reminder, and the optional grab.

Saving a release on Discover (or from the player) puts it on the wishlist
without following the artist. On the day it comes out, every notifier with the
"Saved release out today" event ticked hears about it, and -- when switched on
under Settings > Discovery -- it's sent to the download client like a grab from
the album page.
"""

import threading
from datetime import date
from urllib.parse import urlencode

from . import db, digest
from .plugins import notifier

AUTOGRAB_SETTING = "wishlist_autograb"
EVENT = "wishlist_release_day"

_running = threading.Lock()


def autograb():
    return (db.get_setting(AUTOGRAB_SETTING) or "false").strip().lower() == "true"


def _album_link(item):
    base = digest.base_url()
    if not base:
        return None
    return base + "/album?" + urlencode({"artist": item["artist"], "title": item["album"]})


def run_due():
    """Announce (and maybe grab) every saved release that's out. Returns how many."""
    if not _running.acquire(blocking=False):
        return 0
    try:
        due = db.wishlist_due(date.today().isoformat())
        for item in due:
            notifier.notify(
                EVENT,
                f"Out today: {item['artist']} - {item['album']}",
                "A release you saved for later is out.",
                url=_album_link(item),
            )
            db.mark_wishlist(item["id"], notified_at=True)
            if autograb() and not item.get("grabbed_at"):
                # Local: grabber pulls in the download machinery.
                from . import grabber

                try:
                    result = grabber.grab(item["artist"], item["album"], reason="auto")
                except Exception:  # noqa: BLE001 - one failed grab isn't the list
                    continue
                if result.get("sent") or result.get("skipped"):
                    db.mark_wishlist(item["id"], grabbed_at=True)
        return len(due)
    finally:
        _running.release()


def run_due_async():
    """run_due in a daemon thread: a grab can take a Soulseek search's time."""
    threading.Thread(target=run_due, daemon=True).start()
