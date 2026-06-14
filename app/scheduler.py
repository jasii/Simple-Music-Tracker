"""Background scheduler that periodically refreshes subscribed artists.

A single daemon thread sleeps for the configured interval and then refreshes
all subscribed artists. Kept deliberately simple (no external scheduler
dependency) -- the interval is re-read from settings on every cycle so changes
take effect without a restart.
"""

import threading
import time

from . import db, tracker

_thread = None
_started = False


def _loop():
    # Small initial delay so the web server is up before the first heavy cycle.
    time.sleep(30)
    while True:
        try:
            interval_hours = float(db.get_setting("check_interval_hours") or 12)
        except (TypeError, ValueError):
            interval_hours = 12
        interval_hours = max(interval_hours, 0.25)  # floor at 15 minutes

        try:
            # Hand work to the single refresh worker; it paces external calls.
            tracker.enqueue_all_subscribed()
        except Exception:  # noqa: BLE001 - never let the scheduler thread die
            pass

        # Sleep in short chunks so interval changes are picked up reasonably fast.
        remaining = interval_hours * 3600
        while remaining > 0:
            time.sleep(min(60, remaining))
            remaining -= 60


def start():
    global _thread, _started
    if _started:
        return
    _started = True
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()
