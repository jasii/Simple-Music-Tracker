"""Background bulk lookup: genre tags for the suggested artists on Discover.

The Similar Artists ranking can hold thousands of names, and each one's tags
live behind a metadata lookup (Last.fm, by default). Fetching them row-by-row as the page is scrolled means the
genre filter only ever knows about what has been looked at, so this walks the
ranking top-down -- the artists similar to the most of your library first --
and fills the same week-long cache the page reads.

Politeness mirrors similar_scan: one artist at a time, a fixed pause between
lookups that actually hit the network, cached artists skipped for free, one run
at a time, manual start only.
"""

import threading
import time

from . import db, similar

# Pause between artists that had to be fetched (cache misses). Last.fm is
# forgiving; a second apart is plenty.
_ARTIST_GAP = 1.0
# json_cache key remembering an in-flight run (and its limit), so a restarted
# backend picks it back up instead of silently dropping it.
_ACTIVE_KEY = "simenrich:active"

_state = {"running": False, "done": 0, "total": 0, "fetched": 0,
          "skipped_cached": 0, "current": "", "message": ""}
_state_lock = threading.Lock()
_run_lock = threading.Lock()


def _set(**kw):
    with _state_lock:
        _state.update(kw)


def get_state():
    with _state_lock:
        return dict(_state)


def _configured():
    """True when some metadata source can answer artist lookups."""
    from .plugins import metadata as registry

    return bool(registry.sources("artist_genres"))


def _run(limit):
    if not _configured():
        _set(running=False, message="No metadata source can look up genre tags.")
        return
    names = db.similar_artists_missing_genres(limit or None)
    _set(running=True, done=0, total=len(names), fetched=0, skipped_cached=0,
         current="", message="")
    stopped = False
    try:
        for name in names:
            if not get_state()["running"]:  # stopped from the UI
                stopped = True
                break
            # Someone may have loaded this artist's row (or created them) since
            # the list was built -- then the lookup is free.
            was_cached = similar.cached_info(name) is not None
            _set(current=name)
            try:
                similar.artist_info(name)
                if not was_cached:
                    _set(fetched=get_state()["fetched"] + 1)
            except Exception:  # noqa: BLE001 - one bad artist must not kill the
                pass           # run; they're simply left without tags
            _set(done=get_state()["done"] + 1,
                 skipped_cached=get_state()["skipped_cached"] + (1 if was_cached else 0))
            if not was_cached:
                time.sleep(_ARTIST_GAP)
        state = get_state()
        _set(running=False, current="",
             message=("Stopped" if stopped else "Done") +
                     f": genres looked up for {state['fetched']} artists"
                     f" ({state['done']} of {state['total']} checked).")
    except Exception as exc:  # noqa: BLE001
        _set(running=False, current="", message=f"Failed: {exc}")
    finally:
        db.set_json_cache(_ACTIVE_KEY, {"running": False})


def start(limit=0):
    """Start the lookup in a daemon thread. Returns False if already running.

    *limit* caps how many artists this run covers (0 = the whole ranking).
    """
    if not _run_lock.acquire(blocking=False):
        return False
    def runner():
        try:
            _run(limit)
        finally:
            _run_lock.release()
    _set(running=True, message="")
    db.set_json_cache(_ACTIVE_KEY, {"running": True, "limit": limit})
    threading.Thread(target=runner, daemon=True).start()
    return True


def stop():
    """Ask a running lookup to stop after the current artist."""
    _set(running=False)
    db.set_json_cache(_ACTIVE_KEY, {"running": False})


def maybe_resume():
    """Resume a run that a backend restart killed mid-way.

    Called once at app startup. Artists already looked up are skipped from the
    cache, so the resumed run picks up where the old one stopped.
    """
    entry = db.get_json_cache(_ACTIVE_KEY)
    if entry and entry.get("running") and _configured():
        start(entry.get("limit") or 0)
