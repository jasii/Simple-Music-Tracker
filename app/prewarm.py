"""Background pre-load: what this month's releases play from, looked up ahead.

Pressing play on a Discover row (or an album page) for a release nobody has
opened yet means a tracklist lookup, then a library search and a catalogue
lookup per track -- ten or twenty seconds before the first note. This walks
every release on Discover and Upcoming dated from the start of this week to a
month out and does that work in advance, storing the same answers the album
page reads (see app/playable.py), so playing them is instant.

For a record that isn't out yet there's usually nothing on it to play, so the
artist's top tracks are looked up too: that's what the Discover play button
falls back to. The artist's genre tags and most played songs come along, for
the Discover rows and the sampler, and the For you ranking's inputs (your
Last.fm plays, critic scores) are refreshed first.

Politeness: one release at a time, a short pause after each one that had to
go to the network, cached releases passed over for free, one run at a time.
"""

import threading
import time
from datetime import date, timedelta

from . import album as album_detail
from . import critics, db, lastfm, playable, similar

# How far past today releases count as "this month".
WINDOW_DAYS = 30
# Pause after a release that needed lookups. The modules underneath pace their
# own services; this keeps the job from hogging them.
_RELEASE_GAP = 0.5
# Faster than this and a release came out of the cache.
_CACHED_S = 0.3
# How many top tracks to have ready for the fallback (the Discover button
# asks for the same number).
TOP_TRACKS = 10
# The last finished run, for the settings page.
_LAST_KEY = "prewarm:last"

_state = {"running": False, "done": 0, "total": 0, "current": "",
          "playable": 0, "fallback": 0, "silent": 0, "message": ""}
_state_lock = threading.Lock()
_run_lock = threading.Lock()


def _set(**kw):
    with _state_lock:
        _state.update(kw)


def _bump(key):
    with _state_lock:
        _state[key] += 1


def _snapshot():
    with _state_lock:
        return dict(_state)


def get_state():
    state = _snapshot()
    state["enabled"] = enabled()
    state["last"] = db.get_json_cache(_LAST_KEY)
    return state


def enabled():
    return (db.get_setting("prewarm_audio_enabled") or "true").strip().lower() == "true"


def _week_start(today):
    """The Sunday this week began on, as the agenda buckets weeks."""
    return today - timedelta(days=(today.weekday() + 1) % 7)


def releases():
    """[(artist, title, mbid)] to pre-load, the ones nearest today first."""
    # Local: main imports this module.
    from .main import discover_feed, upcoming_between

    today = date.today()
    start, end = _week_start(today), today + timedelta(days=WINDOW_DAYS)
    found = {}

    def add(artist, title, mbid, when):
        artist, title = (artist or "").strip(), (title or "").strip()
        if not artist or not title or not when:
            return
        try:
            day = date.fromisoformat(when[:10])
        except ValueError:
            return
        if not start <= day <= end:
            return
        key = (artist.lower(), title.lower())
        if key not in found:
            found[key] = (abs((day - today).days), artist, title, mbid or None)

    try:
        _sources, items = discover_feed(kick=False)
    except Exception:  # noqa: BLE001 - Upcoming is still worth doing
        items = []
    for it in items:
        add(it.get("artist"), it.get("album"), it.get("mbid"), it.get("normalized_date"))
    for it in upcoming_between(start, end):
        add(it.get("artist_name"), it.get("title"), it.get("mbid"), it.get("normalized_date"))
    return [(a, t, m) for _d, a, t, m in sorted(found.values())]


def warm_release(artist, title, mbid=None):
    """Pre-load one release. Returns "playable", "fallback" or "silent"."""
    # Local: main imports this module.
    from .main import top_tracks_playable

    detail = album_detail.get_album_detail(artist, title, mbid=mbid)
    tracks = [(t.get("name"), t.get("url")) for t in detail.get("tracks") or []]
    answers = playable.build(artist, title, tracks) if tracks else {}
    # Genre tags for the Discover row, and the artist's most played songs
    # (the sampler starts a release with those), whatever else happens.
    try:
        similar.artist_info(artist)
        lastfm.top_tracks(artist, limit=50)
    except Exception:  # noqa: BLE001 - tags are decoration
        pass
    if any((a or {}).get("kind") not in (None, "none") for a in answers.values()):
        return "playable"
    # Nothing on the record plays (it isn't out yet): have the artist's best
    # known songs ready instead, which is what the play button falls back to.
    top = top_tracks_playable(artist, TOP_TRACKS)
    return "fallback" if any(t.get("stream") for t in top) else "silent"


def _run():
    # What the For you ranking reads: your Last.fm plays, and critic scores.
    try:
        lastfm.top_artists("overall", 1000)
        critics.scores()
    except Exception:  # noqa: BLE001 - the ranking works without them
        pass
    todo = releases()
    _set(running=True, done=0, total=len(todo), current="", playable=0,
         fallback=0, silent=0, message="")
    stopped = False
    try:
        for artist, title, mbid in todo:
            if not _snapshot()["running"]:  # stopped from the UI
                stopped = True
                break
            _set(current=f"{artist} - {title}")
            began = time.time()
            try:
                _bump(warm_release(artist, title, mbid))
            except Exception:  # noqa: BLE001 - one bad release isn't the run
                _bump("silent")
            _bump("done")
            # Answered from the cache in no time: nothing was asked of anyone.
            if time.time() - began > _CACHED_S:
                time.sleep(_RELEASE_GAP)
        state = _snapshot()
        summary = (f"{state['playable']} releases play from their own tracks, "
                   f"{state['fallback']} from the artist's top tracks, "
                   f"{state['silent']} have nothing to play")
        _set(running=False, current="",
             message=("Stopped" if stopped else "Done") +
                     f" ({state['done']} of {state['total']}): {summary}.")
        if not stopped:
            db.set_json_cache(_LAST_KEY, {
                "finished_at": time.time(), "total": state["total"],
                "playable": state["playable"], "fallback": state["fallback"],
                "silent": state["silent"],
            })
    except Exception as exc:  # noqa: BLE001
        _set(running=False, current="", message=f"Failed: {exc}")


def start():
    """Start a run in a daemon thread. Returns False if one is running."""
    if not _run_lock.acquire(blocking=False):
        return False

    def runner():
        try:
            _run()
        finally:
            _run_lock.release()

    _set(running=True, message="")
    threading.Thread(target=runner, daemon=True).start()
    return True


def stop():
    """Ask a running pre-load to stop after the current release."""
    _set(running=False)
