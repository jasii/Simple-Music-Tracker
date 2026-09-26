"""iCalendar feed of upcoming releases.

Subscribing to a URL is how release dates get somewhere they'll actually be
seen -- a phone's calendar app rather than a tab nobody opens. Each tracked
release becomes an all-day event on its release date; calendar clients re-fetch
the feed on their own schedule and reconcile by UID, so an event that moves is
updated rather than duplicated.

The feed is read-only and unauthenticated by design (calendar apps can't log
in), so its URL carries a token that stands in for a password. Anyone with the
link can see which artists you follow and nothing else.
"""

import secrets
from datetime import date, datetime, timedelta, timezone

from . import db

PRODID = "-//Simple Music Tracker//Upcoming Releases//EN"
# Calendar clients poll on their own schedule; this is a hint, not a promise.
REFRESH_HINT = "PT6H"


def token(create=True):
    """The feed's secret path token, generated on first use."""
    value = (db.get_setting("calendar_token") or "").strip()
    if not value and create:
        value = secrets.token_urlsafe(18)
        db.set_setting("calendar_token", value)
    return value


def reset_token():
    """Issue a new token, invalidating every existing subscription."""
    value = secrets.token_urlsafe(18)
    db.set_setting("calendar_token", value)
    return value


def _escape(text):
    """Escape a value for an iCalendar text field."""
    return (str(text or "")
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\n", "\\n"))


def _fold(line):
    """Wrap a content line at 75 octets, as RFC 5545 requires.

    Continuation lines are written with a leading space that counts toward the
    limit, so they carry one octet less than the first.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks = []
    limit = 75
    while len(raw) > limit:
        cut = limit
        # Don't split a multi-byte character across the fold.
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        chunks.append(raw[:cut].decode("utf-8"))
        raw = raw[cut:]
        limit = 74  # the continuation's leading space takes the other octet
    chunks.append(raw.decode("utf-8"))
    return "\r\n ".join(chunks)


def _parse_date(value):
    """'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' -> date, else None."""
    if not value:
        return None
    parts = str(value).split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


def _releases(past_days=180):
    """Tracked releases for followed artists, recent past included."""
    cutoff = date.today() - timedelta(days=past_days)
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT r.mbid, r.title, r.release_date, r.primary_type, "
            "a.name AS artist, a.id AS artist_id "
            "FROM releases r JOIN artists a ON a.id = r.artist_id "
            "WHERE a.subscription IN ('subscribed', 'notify') "
            "ORDER BY r.release_date"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        when = _parse_date(row["release_date"])
        if when is None or when < cutoff:
            continue
        out.append((when, row))
    return out


def build(base_url=""):
    """Render the whole feed as an iCalendar document."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Upcoming releases",
        f"X-PUBLISHED-TTL:{REFRESH_HINT}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH_HINT}",
    ]
    for when, row in _releases():
        uid = row["mbid"] or f"{row['artist_id']}-{row['title']}"
        kind = row["primary_type"] or "Release"
        summary = f"{row['artist']} - {row['title']}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_escape(uid)}@simple-music-tracker",
            f"DTSTAMP:{stamp}",
            # All-day event: DTEND is exclusive, so it's the following day.
            f"DTSTART;VALUE=DATE:{when.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(when + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_escape(summary)}",
            f"DESCRIPTION:{_escape(kind + ' released ' + when.isoformat())}",
            "TRANSP:TRANSPARENT",
        ]
        if base_url:
            lines.append(f"URL:{_escape(base_url)}/artist/{row['artist_id']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
