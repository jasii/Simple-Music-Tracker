"""The weekly Discover digest: this week's best picks, sent to your notifiers.

Once a week, at the day and time set under Settings > Notifications, the
Discover releases dated from six days back to a week ahead are ranked the way
the page's "For you" sort ranks them (app/foryou.py), and the top few go out
as one message on the "Weekly Discover picks" event -- to every notifier that
has that event ticked. Records you own or have already played are left out.
"""

from datetime import date, datetime, timedelta

from . import db
from .plugins import notifier

ENABLED_SETTING = "discover_digest_enabled"
DAY_SETTING = "discover_digest_day"
TIME_SETTING = "discover_digest_time"
COUNT_SETTING = "discover_digest_count"
LAST_RUN_SETTING = "discover_digest_last_run"
EVENT = "discover_digest"
# Discord refuses messages over 2000 characters; ntfy truncates at 4 KB.
_MAX_CHARS = 1800


def enabled():
    return (db.get_setting(ENABLED_SETTING) or "false").strip().lower() == "true"


def count():
    try:
        return max(1, min(int(db.get_setting(COUNT_SETTING) or 10), 25))
    except (TypeError, ValueError):
        return 10


def base_url():
    """The app's public address for links in messages, or '' when unset."""
    return (db.get_setting("app_base_url") or "").strip().rstrip("/")


def _slot():
    """(weekday, hour, minute) the digest goes out at. Monday = 0."""
    try:
        day = int(db.get_setting(DAY_SETTING) or 4) % 7  # Friday: release day
    except (TypeError, ValueError):
        day = 4
    try:
        hour, minute = (int(x) for x in (db.get_setting(TIME_SETTING) or "09:00").split(":", 1))
    except (TypeError, ValueError):
        hour, minute = 9, 0
    return day, max(0, min(hour, 23)), max(0, min(minute, 59))


def _previous_slot(now):
    day, hour, minute = _slot()
    wanted = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    wanted -= timedelta(days=(wanted.weekday() - day) % 7)
    if wanted > now:
        wanted -= timedelta(days=7)
    return wanted


def next_run(now=None):
    if not enabled():
        return None
    now = now or datetime.now()
    return _previous_slot(now) + timedelta(days=7)


def _last_run():
    try:
        return float(db.get_setting(LAST_RUN_SETTING) or 0)
    except (TypeError, ValueError):
        return 0.0


def due(now=None):
    if not enabled():
        return False
    now = now or datetime.now()
    return _last_run() < _previous_slot(now).timestamp()


def picks(limit=None):
    """This week's top Discover releases, best first."""
    # Local: main imports this module.
    from .main import discover_feed

    _sources, items = discover_feed(kick=False)
    today = date.today()
    lo, hi = today - timedelta(days=6), today + timedelta(days=7)
    rows = []
    for it in items:
        when = it.get("normalized_date")
        if not when or not it.get("artist") or it.get("owned") or it.get("heard"):
            continue
        try:
            day = date.fromisoformat(when)
        except ValueError:
            continue
        if lo <= day <= hi:
            rows.append(it)
    rows.sort(key=lambda it: (-(it.get("for_you") or 0), it["normalized_date"]))
    return rows[: limit or count()]


def compose(rows):
    """(title, message) for a list of picks."""
    title = f"Discover: {len(rows)} pick{'s' if len(rows) != 1 else ''} for this week"
    lines = []
    for it in rows:
        day = date.fromisoformat(it["normalized_date"]).strftime("%a %d %b")
        name = f"{it['artist']} - {it['album']}" if it.get("album") else it["artist"]
        line = f"• {name} ({day})"
        if it.get("reasons"):
            line += f" · {it['reasons'][0]}"
        if len("\n".join(lines + [line])) > _MAX_CHARS:
            break
        lines.append(line)
    return title, "\n".join(lines)


def send():
    """Build and send the digest now. Returns {"sent", "picks", "message"}."""
    rows = picks()
    if not rows:
        return {"sent": 0, "picks": 0, "message": "Nothing on Discover for this week."}
    title, message = compose(rows)
    link = base_url()
    sent = notifier.notify(EVENT, title, message, url=(link + "/discover") if link else None)
    if not sent:
        return {"sent": 0, "picks": len(rows),
                "message": "No notifier has \"Weekly Discover picks\" ticked."}
    return {"sent": sent, "picks": len(rows),
            "message": f"Sent {len(rows)} picks to {sent} notifier{'s' if sent != 1 else ''}."}


def run_if_due(now=None):
    """Send when this week's slot has passed. Returns the result, or None."""
    if not due(now):
        return None
    # Marked before the work: a notifier that's down costs one try a week.
    db.set_setting(LAST_RUN_SETTING, str(int((now or datetime.now()).timestamp())))
    return send()
