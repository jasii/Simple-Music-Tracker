"""Background bulk scan: similar artists for every owned artist in the library.

For each artist with tracks in the library, asks the similar-artist sources
(see similar.collect) who sounds like them and records the suggestions (see
db.record_similar_artists). The Discover page's "worth checking out" ranking
then covers the whole collection, not just pages that happen to have been
browsed.

Politeness: one artist at a time, a pause between artists, a durable marker so
an artist done once isn't asked again for a month, one scan at a time, manual
start only.
"""

import threading
import time

from . import db, similar

# Pause between artists.
_GAP = 1.0
# A scanned artist isn't re-scanned for this long (similar lists move slowly).
_MARK_TTL = 30 * 86400
# json_cache key remembering that a scan was mid-run, so a restarted backend
# (dev reloader, redeploy) picks it back up instead of silently dying.
_ACTIVE_KEY = "simscan:active"

_state = {"running": False, "done": 0, "total": 0, "recorded": 0,
          "skipped_cached": 0, "current": "", "message": ""}
_state_lock = threading.Lock()
_run_lock = threading.Lock()


def _set(**kw):
    with _state_lock:
        _state.update(kw)


def get_state():
    with _state_lock:
        return dict(_state)


def _owned_artist_names():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM artists WHERE track_count > 0 "
            "ORDER BY sort_name"
        ).fetchall()
    finally:
        conn.close()
    return [r["name"] for r in rows]


def _mark_key(scope, name):
    return f"simscanned:{scope}:{name.lower()}"


def _already_scanned(scope, name):
    """Durable skip: this artist completed a scan recently (survives restarts)."""
    return db.get_json_cache(_mark_key(scope, name), max_age=_MARK_TTL) is not None


def _run():
    if not similar.sources_available():
        _set(running=False, message=(
            "Nothing can suggest similar artists yet: add a Last.fm API key, "
            "and check the Similar artists list in Settings > Metadata has a "
            "source switched on."))
        return
    marker_scope = "lastfm"
    names = _owned_artist_names()
    _set(running=True, done=0, total=len(names), recorded=0, skipped_cached=0,
         current="", message="")
    stopped = False
    sources_seen = set()
    try:
        for name in names:
            if not get_state()["running"]:  # stopped from the UI
                stopped = True
                break
            # Durable skip: completed in an earlier run (marker survives restarts).
            if _already_scanned(marker_scope, name):
                _set(done=get_state()["done"] + 1,
                     skipped_cached=get_state()["skipped_cached"] + 1)
                continue
            _set(current=name)
            try:
                suggestions, sources = similar.collect(name)
                sources_seen.update(sources)
                if suggestions:
                    _set(recorded=get_state()["recorded"] + len(suggestions))
                db.set_json_cache(_mark_key(marker_scope, name), {"at": time.time()})
            except Exception:  # noqa: BLE001 - one bad artist must not kill the
                pass           # scan; unmarked, so the next run retries it
            _set(done=get_state()["done"] + 1)
            time.sleep(_GAP)
        state = get_state()
        via = ", ".join(sorted(sources_seen)) or "no source"
        _set(running=False, current="",
             message=("Stopped" if stopped else "Done") +
                     f": {state['done']} artists scanned via {via}, "
                     f"{state['recorded']} suggestions recorded.")
    except Exception as exc:  # noqa: BLE001
        _set(running=False, current="", message=f"Failed: {exc}")
    finally:
        db.set_json_cache(_ACTIVE_KEY, {"running": False})


def start():
    """Start the scan in a daemon thread. Returns False if already running."""
    if not _run_lock.acquire(blocking=False):
        return False
    def runner():
        try:
            _run()
        finally:
            _run_lock.release()
    _set(running=True, message="")
    db.set_json_cache(_ACTIVE_KEY, {"running": True})
    threading.Thread(target=runner, daemon=True).start()
    return True


def stop():
    """Ask a running scan to stop after the current artist."""
    _set(running=False)
    db.set_json_cache(_ACTIVE_KEY, {"running": False})


def maybe_resume():
    """Resume a scan that a backend restart killed mid-run.

    Called once at app startup. The durable per-artist markers mean the
    resumed scan flies through everything already done without API calls.
    """
    entry = db.get_json_cache(_ACTIVE_KEY)
    if entry and entry.get("running") and similar.sources_available():
        start()
