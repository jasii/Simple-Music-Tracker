"""A playlist for the week's upcoming releases, from records you already have.

A release that isn't out yet can't be played, which is the trouble with a page
full of them. What *can* be played is the rest of the artist's work -- so this
gathers the artists with something due this week, takes each one's best-known
songs, keeps the ones your own server actually holds, and writes them to a
playlist there.

Named "Get Hyped" by default, replaced rather than appended each time it runs,
so pressing the button twice leaves one playlist rather than a longer one.
"""

from datetime import date, datetime, timedelta

from . import db, lastfm, plugins

PLAYLIST_NAME = "Get Hyped"

# Rebuilding it on a schedule: on/off, which day (0 = Monday), and the local
# time of day. The last run is remembered so a restart doesn't rebuild it and
# a missed week isn't built twice.
WEEKLY_SETTING = "hype_playlist_weekly"
DAY_SETTING = "hype_playlist_day"
TIME_SETTING = "hype_playlist_time"
LAST_RUN_SETTING = "hype_playlist_last_run"
# How far ahead to look for releases. A week is the Friday-to-Friday habit; a
# quarter suits someone who wants a longer playlist and fewer rebuilds.
DAYS_SETTING = "hype_playlist_days"
# How many of each artist's best-known songs go in.
PER_ARTIST_SETTING = "hype_playlist_per_artist"
WINDOWS = (
    (7, "The week ahead"),
    (30, "The month ahead"),
    (60, "The next two months"),
    (90, "The next three months"),
)
DEFAULT_DAYS = 7

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")

# Defaults, both settable: enough of each artist to recognise them, and a
# ceiling so a wide window gives a longer playlist rather than an unusable one.
DEFAULT_PER_ARTIST = 3
PER_ARTIST_CHOICES = (1, 2, 3, 5, 10)
_MAX_TRACKS = 200


def _targets():
    """Any library that can be written to, in the playback order."""
    from . import librarytrack

    by_key = {p.key: p for p in plugins.get_plugins("library")}
    return [by_key[k] for k in librarytrack.playback_order()
            if k in by_key and by_key[k].configured()
            and getattr(by_key[k], "supports_playlists", False)]


def available():
    """Is there anywhere to put a playlist?"""
    return bool(_targets())


def per_artist_count():
    """How many songs per artist, from settings."""
    try:
        count = int(db.get_setting(PER_ARTIST_SETTING) or DEFAULT_PER_ARTIST)
    except (TypeError, ValueError):
        return DEFAULT_PER_ARTIST
    return max(1, min(count, 20))


def window_days():
    """How far ahead the playlist looks, from settings."""
    try:
        days = int(db.get_setting(DAYS_SETTING) or DEFAULT_DAYS)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(1, min(days, 365))


def _weekly_enabled():
    return (db.get_setting(WEEKLY_SETTING) or "false").strip().lower() == "true"


def _slot():
    """(weekday, hour, minute) the rebuild is wanted at."""
    try:
        day = int(db.get_setting(DAY_SETTING) or 4)  # Friday: release day
    except (TypeError, ValueError):
        day = 4
    day = day % 7
    raw = (db.get_setting(TIME_SETTING) or "08:00").strip()
    try:
        hour, minute = (int(x) for x in raw.split(":", 1))
    except (TypeError, ValueError):
        hour, minute = 8, 0
    return day, max(0, min(hour, 23)), max(0, min(minute, 59))


def _last_run():
    try:
        return float(db.get_setting(LAST_RUN_SETTING) or 0)
    except (TypeError, ValueError):
        return 0.0


def _previous_slot(now):
    """The most recent moment the rebuild was wanted, at or before *now*."""
    day, hour, minute = _slot()
    wanted = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # Walk back to the wanted weekday, then back a week if that lands ahead.
    wanted -= timedelta(days=(wanted.weekday() - day) % 7)
    if wanted > now:
        wanted -= timedelta(days=7)
    return wanted


def next_run(now=None):
    """When the rebuild is next due, or None when it isn't scheduled."""
    if not _weekly_enabled():
        return None
    now = now or datetime.now()
    return _previous_slot(now) + timedelta(days=7)


def due(now=None):
    """Is a scheduled rebuild overdue?"""
    if not _weekly_enabled():
        return False
    now = now or datetime.now()
    return _last_run() < _previous_slot(now).timestamp()


def run_if_due(now=None):
    """Rebuild when the week's slot has passed. Returns the result, or None.

    Called once a minute by the scheduler, so the cheap checks come first and
    the run is marked before the work: a server that can't be reached should
    cost one attempt a week, not one a minute.
    """
    if not due(now):
        return None
    db.set_setting(LAST_RUN_SETTING, str(int((now or datetime.now()).timestamp())))
    return build()


def schedule_state():
    """What the settings page shows about the weekly rebuild."""
    when = next_run()
    day, hour, minute = _slot()
    return {
        "enabled": _weekly_enabled(),
        "day": day,
        "day_name": DAY_NAMES[day],
        "time": f"{hour:02d}:{minute:02d}",
        "days": window_days(),
        "per_artist": per_artist_count(),
        "per_artist_choices": list(PER_ARTIST_CHOICES),
        "windows": [{"days": d, "label": label} for d, label in WINDOWS],
        "last_run": _last_run() or None,
        "next_run": when.isoformat(timespec="minutes") if when else None,
    }


def _artists_with_releases(start, end):
    """[(artist id, name)] for followed artists with a release in the window."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT a.id, a.name, MIN(r.release_date) AS due "
            "FROM releases r JOIN artists a ON a.id = r.artist_id "
            "WHERE a.subscription IN ('subscribed', 'notify') AND a.ignored = 0 "
            "AND r.release_date >= ? AND r.release_date <= ? "
            "GROUP BY a.id ORDER BY due, a.name",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    return [(r["id"], r["name"]) for r in rows]


def build(days=None, per_artist=None, name=PLAYLIST_NAME):
    """Write the playlist and report what went into it.

    Returns {"playlist", "tracks", "artists", "considered", "missing"} or
    raises RuntimeError when there's nowhere to write it.
    """
    plugin = next(iter(_targets()), None)
    if plugin is None:
        raise RuntimeError("No library that can hold a playlist is set up.")

    days = window_days() if days is None else max(1, min(int(days), 365))
    per_artist = (per_artist_count() if per_artist is None
                  else max(1, min(int(per_artist), 20)))
    today = date.today()
    artists = _artists_with_releases(today, today + timedelta(days=days))
    if not artists:
        return {"playlist": None, "tracks": 0, "artists": 0, "days": days,
                "considered": 0, "missing": [],
                "message": f"Nothing due in the next {days} days."}

    chosen, used, missing = [], [], []
    considered = 0
    for _artist_id, artist in artists:
        if len(chosen) >= _MAX_TRACKS:
            break
        try:
            top = lastfm.top_tracks(artist, limit=per_artist * 3)
        except Exception:  # noqa: BLE001 - an artist without scrobbles is fine
            top = []
        kept = 0
        for track in top:
            if kept >= per_artist or len(chosen) >= _MAX_TRACKS:
                break
            considered += 1
            try:
                found = plugin.find_track(artist, track["name"])
            except Exception:  # noqa: BLE001 - a sick server ends the run below
                found = None
            song_id = (found or {}).get("song_id")
            if not song_id:
                continue
            if song_id in chosen:
                continue
            chosen.append(song_id)
            kept += 1
        if kept:
            used.append(artist)
        else:
            missing.append(artist)

    if not chosen:
        return {"playlist": None, "tracks": 0, "artists": 0, "days": days,
                "considered": considered, "missing": missing,
                "message": ("None of this week's artists have anything on "
                            f"{plugin.display_label()} yet.")}

    written = plugin.playlist_replace(name, chosen)
    # Stamped whoever asked for it, so the button can say when it last ran.
    db.set_setting(LAST_RUN_SETTING, str(int(datetime.now().timestamp())))
    return {
        "playlist": {**written, "label": plugin.display_label(),
                     "icon": plugin.icon_name()},
        "tracks": len(chosen),
        "artists": len(used),
        "days": days,
        "per_artist": per_artist,
        "considered": considered,
        "missing": missing,
        "message": (f"{name}: {len(chosen)} tracks from {len(used)} "
                    f"artist{'s' if len(used) != 1 else ''} on "
                    f"{plugin.display_label()}."),
    }
