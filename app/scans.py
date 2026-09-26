"""One library scan at a time, with a queue and a cancel button.

Every source used to scan on its own thread, so pressing "Scan now" on Plex
while Navidrome was still going ran both at once: two passes writing owned
albums, competing for the same SQLite write lock and the same network. Worse,
a second press on the *same* source started a second scan of it.

So scans go through here instead. One worker runs one job; anything else asked
for while it runs is queued in order, and a source already running or queued is
never queued twice. Cancelling is cooperative: the coordinator raises a flag
and the scan loops check it (:func:`should_stop`) between items, which is the
only way to stop work that is mid-request against someone else's server.
"""

import threading
import time


class Cancelled(RuntimeError):
    """Raised inside a scan when the user asks for it to stop."""


_lock = threading.Condition()
_queue = []          # pending jobs, in order: {"key", "label", "quick", "queued_at"}
_current = None      # the running job, same shape plus "started_at"
_stop = False        # cancellation flag for the running job
_results = {}        # key -> {"status", "message", "finished_at"}
_worker = None
# key -> callable(quick) that actually performs the scan. Registered by main.
_runners = {}


def register_runner(key, label, run):
    """Teach the coordinator how to scan one source."""
    _runners[key] = {"label": label, "run": run}


def ensure_library_runners():
    """Register every configured library, once. Safe to call repeatedly."""
    from .plugins import get_plugins  # local: plugins import db, which imports us

    for plugin in get_plugins("library"):
        if plugin.key in _runners:
            continue

        def run(quick, plugin=plugin):
            return plugin.scan(quick=quick and plugin.supports_quick)

        register_runner(plugin.key, plugin.label, run)


def due_libraries():
    """Libraries whose own schedule says they're due for a scan."""
    from . import db
    from .plugins import get_plugins

    due = []
    for plugin in get_plugins("library"):
        if not plugin.configured():
            continue
        hours = plugin.scan_interval_hours()
        if not hours:
            continue
        last = db.library_stats(plugin.key)["last_scanned"] or 0
        if (time.time() - float(last)) >= hours * 3600:
            due.append(plugin.key)
    return due


def should_stop():
    """True when the running scan has been asked to stop (call this often)."""
    with _lock:
        return _stop


def raise_if_stopped():
    """Cooperative cancellation point for a scan loop."""
    if should_stop():
        raise Cancelled("cancelled")


def _job(key, quick):
    return {"key": key, "label": _runners.get(key, {}).get("label", key),
            "quick": bool(quick), "queued_at": time.time()}


def enqueue(key, quick=False):
    """Run *key* now, or queue it behind whatever is running.

    Returns {"state": "started" | "queued" | "duplicate", "position": int}.
    """
    if key not in _runners:
        return {"state": "unknown"}
    with _lock:
        if _current and _current["key"] == key:
            return {"state": "duplicate", "position": 0}
        for i, job in enumerate(_queue):
            if job["key"] == key:
                return {"state": "duplicate", "position": i + 1}
        _queue.append(_job(key, quick))
        position = len(_queue)
        starting = _current is None
        _lock.notify_all()
    _ensure_worker()
    return {"state": "started" if starting else "queued", "position": position}


def cancel(key=None):
    """Stop the running scan and/or drop queued ones.

    With no *key*, everything is cancelled. Returns what was affected.
    """
    global _stop
    removed = []
    stopping = None
    with _lock:
        keep = []
        for job in _queue:
            if key is None or job["key"] == key:
                removed.append(job["key"])
            else:
                keep.append(job)
        _queue[:] = keep
        if _current and (key is None or _current["key"] == key):
            _stop = True
            stopping = _current["key"]
        _lock.notify_all()
    return {"stopping": stopping, "dequeued": removed}


def status():
    """What's running, what's waiting, and how the last run of each went."""
    with _lock:
        current = dict(_current) if _current else None
        if current:
            current["elapsed"] = int(time.time() - current["started_at"])
            current["cancelling"] = _stop
        return {
            "running": current is not None,
            "current": current,
            "queue": [dict(job) for job in _queue],
            "results": {k: dict(v) for k, v in _results.items()},
        }


def state_for(key):
    """This source's place in the world: running, queued (with position), or idle."""
    with _lock:
        if _current and _current["key"] == key:
            return {"state": "running", "position": 0, "cancelling": _stop}
        for i, job in enumerate(_queue):
            if job["key"] == key:
                return {"state": "queued", "position": i + 1}
    return {"state": "idle", "position": None}


def _record(key, status_name, message):
    with _lock:
        _results[key] = {"status": status_name, "message": message,
                         "finished_at": time.time()}


def _run_next():
    """Pull one job and run it. Returns False when the queue is empty."""
    global _current, _stop
    with _lock:
        if not _queue:
            return False
        job = _queue.pop(0)
        job["started_at"] = time.time()
        _current = job
        _stop = False
    runner = _runners.get(job["key"])
    try:
        summary = runner["run"](job["quick"]) if runner else None
        if isinstance(summary, dict) and summary.get("error"):
            _record(job["key"], "error", str(summary["error"]))
        else:
            _record(job["key"], "done", _summarise(summary))
            # A finished scan has just rewritten what's owned and in what
            # quality, so the missing/incomplete table is stale. Imported here,
            # not at the top: gaps reaches the library plugins, which import
            # this module.
            from . import gaps
            gaps.invalidate()
    except Cancelled:
        _record(job["key"], "cancelled", "Cancelled.")
    except Exception as exc:  # noqa: BLE001 - one bad scan must not kill the worker
        _record(job["key"], "error", str(exc))
    finally:
        with _lock:
            _current = None
            _stop = False
            _lock.notify_all()
    return True


def _summarise(summary):
    """One line describing a finished scan, from whatever it returned.

    Sources report different fields (a filesystem walk counts files, an API
    scan counts albums), so this takes whichever are present.
    """
    if not isinstance(summary, dict):
        return "Done."
    parts = []
    artists = summary.get("artists", summary.get("artists_found"))
    if artists is not None:
        parts.append(f"{artists} artists")
    if summary.get("albums") is not None:
        parts.append(f"{summary['albums']} albums")
    if summary.get("files_seen") is not None:
        parts.append(f"{summary['files_seen']} files")
    return "Done: " + ", ".join(parts) if parts else "Done."


def _loop():
    while True:
        if _run_next():
            continue
        with _lock:
            if not _queue:
                _lock.wait(timeout=60)


def _ensure_worker():
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_loop, daemon=True, name="library-scans")
        _worker.start()
