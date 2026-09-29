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
from . import critics, db, lastfm, playable, ratelimit, savedaudio, similar

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
          "playable": 0, "fallback": 0, "silent": 0, "saved": 0, "message": ""}
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
    state["disk"] = savedaudio.stats()
    # Services that asked us to slow down and are being left alone for now.
    state["backing_off"] = ratelimit.status()
    return state


def enabled():
    return (db.get_setting("prewarm_audio_enabled") or "true").strip().lower() == "true"


def running():
    """Is a pre-load going right now?

    While it is, the pages don't look audio up themselves (see
    audio_lookups_paused in app/main.py): the pre-load is already spending
    the catalogues' request budgets, and it's finding the same things.
    """
    return _run_lock.locked()


def _week_start(today):
    """The Sunday this week began on, as the agenda buckets weeks."""
    return today - timedelta(days=(today.weekday() + 1) % 7)


def releases():
    """[(artist, title, mbid, songs)] to pre-load, the ones nearest today first.

    A release counts when its date falls in the window, or when a source
    listed it in the window: a record KEXP is playing this week or a blog
    posted about came out months ago, and it's this week's find all the same.
    *songs* are the ones the source named (see the discovery item schema).
    """
    # Local: main imports this module.
    from .main import discover_feed, upcoming_between

    today = date.today()
    start, end = _week_start(today), today + timedelta(days=WINDOW_DAYS)
    found = {}

    def add(artist, title, mbid, dates, songs=None):
        artist, title = (artist or "").strip(), (title or "").strip()
        if not artist or not title:
            return
        days = []
        for when in dates:
            try:
                day = date.fromisoformat((when or "")[:10])
            except ValueError:
                continue
            if start <= day <= end:
                days.append(day)
        if not days:
            return
        key = (artist.lower(), title.lower())
        if key not in found:
            distance = min(abs((day - today).days) for day in days)
            found[key] = (distance, artist, title, mbid or None, songs or [])

    try:
        _sources, items = discover_feed(kick=False)
    except Exception:  # noqa: BLE001 - Upcoming is still worth doing
        items = []
    for it in items:
        add(it.get("artist"), it.get("album"), it.get("mbid"),
            (it.get("normalized_date"), it.get("posted")), it.get("songs"))
    for it in upcoming_between(start, end):
        add(it.get("artist_name"), it.get("title"), it.get("mbid"),
            (it.get("normalized_date"),))
    return [(a, t, m, s) for _d, a, t, m, s in sorted(found.values(), key=lambda f: f[:3])]


def _per_release():
    """How many songs of each release to save; 0 means all of them."""
    try:
        return max(0, int(db.get_setting("saved_audio_per_release") or 0))
    except (TypeError, ValueError):
        return 0


def _worth_saving(artist, titles, songs, room):
    """The *room* songs of a release worth keeping, best first.

    The songs the source named (what KEXP aired, most played first) lead,
    then Last.fm's most played, then tracklist order. *room* None is all.
    """
    # Local: main imports this module.
    from .main import _mark_hot_tracks

    if room is None:
        return list(titles)
    if room <= 0 or not titles:
        return []
    named = [db.match_key(s.get("title")) for s in songs or []]
    plays = {t["name"]: t.get("playcount") or 0
             for t in _mark_hot_tracks(artist, [{"name": t} for t in titles])}

    def rank(item):
        at, title = item
        key = db.match_key(title)
        return (named.index(key) if key in named else len(named), -plays.get(title, 0), at)

    return [title for _at, title in sorted(enumerate(titles), key=rank)][:room]


def _save(fn, *args):
    """Keep one sample on disk (see app/savedaudio.py), counting new files."""
    try:
        if fn(*args):
            _bump("saved")
    except Exception:  # noqa: BLE001 - a file that won't save isn't the release
        pass


def warm_release(artist, title, mbid=None, songs=None):
    """Pre-load one release. Returns "playable", "fallback" or "silent"."""
    # Local: main imports this module.
    from .main import top_tracks_playable

    # The songs a blog posted, which Hype Machine streams whole. They count
    # towards the songs saved per release, and their previews aren't saved too.
    limit = _per_release()
    posted = [s for s in songs or [] if s.get("hypem")][:limit or None]
    for song in posted:
        _save(savedaudio.save_hypem, song["hypem"], artist, song["title"], title)
    whole = {db.match_key(s["title"]) for s in posted}
    room = max(limit - len(posted), 0) if limit else None

    detail = album_detail.get_album_detail(artist, title, mbid=mbid)
    tracks = [(t.get("name"), t.get("url")) for t in detail.get("tracks") or []]
    answers = playable.build(artist, title, tracks) if tracks else {}
    samples = [name for name, answer in answers.items()
               if (answer or {}).get("kind") == "sample"
               and db.match_key(name) not in whole]
    for name in _worth_saving(artist, samples, songs, room):
        _save(savedaudio.save, artist, name, title)
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
    # Already most played first.
    keep = [t for t in top if t.get("stream") and not t.get("full")
            and db.match_key(t["name"]) not in whole]
    for track in keep[:room]:
        _save(savedaudio.save, artist, track["name"], track.get("album"))
    return "fallback" if any(t.get("stream") for t in top) else "silent"


def _run():
    # What the For you ranking reads: your Last.fm plays, and critic scores.
    try:
        lastfm.top_artists("overall", 1000)
        critics.scores()
    except Exception:  # noqa: BLE001 - the ranking works without them
        pass
    todo = releases()
    # Files deleted from the folder by hand stop counting.
    savedaudio.reconcile()
    _set(running=True, done=0, total=len(todo), current="", playable=0,
         fallback=0, silent=0, saved=0, message="")
    stopped = False
    try:
        for artist, title, mbid, songs in todo:
            if not _snapshot()["running"]:  # stopped from the UI
                stopped = True
                break
            _set(current=f"{artist} - {title}")
            began = time.time()
            try:
                _bump(warm_release(artist, title, mbid, songs))
            except Exception:  # noqa: BLE001 - one bad release isn't the run
                _bump("silent")
            _bump("done")
            # Answered from the cache in no time: nothing was asked of anyone.
            if time.time() - began > _CACHED_S:
                time.sleep(_RELEASE_GAP)
        state = _snapshot()
        summary = (f"{state['playable']} releases play from their own tracks, "
                   f"{state['fallback']} from the artist's top tracks, "
                   f"{state['silent']} have nothing to play, "
                   f"{state['saved']} new previews saved")
        _set(running=False, current="",
             message=("Stopped" if stopped else "Done") +
                     f" ({state['done']} of {state['total']}): {summary}.")
        if not stopped:
            db.set_json_cache(_LAST_KEY, {
                "finished_at": time.time(), "total": state["total"],
                "playable": state["playable"], "fallback": state["fallback"],
                "silent": state["silent"], "saved": state["saved"],
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
