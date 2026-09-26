"""Following a Soulseek download until every file has landed or been given up on.

slskd doesn't hand over a release as one thing: an album is a handful of separate requests to someone's home
connection, and any of them can fail on its own -- the peer goes offline, a
file errors half way, or it sits in their upload queue for a day. Without this
module the app queues the files and forgets, and "7 of 9 tracks" is something
the user finds out when the album stops early.

So every search-client grab becomes a job (``download_jobs``), polled once a
minute by the scheduler. A file that fails is handled in this order:

1. the same peer again, a couple of times -- most failures are transient;
2. another peer's copy of just that track, from the other folders the search
   turned up, same format only (``downloader_slskd_mix_peers``);
3. when nothing has landed yet, another peer's whole folder instead;
4. when a job still ends short, one fresh search for what's missing a few hours
   later, since new peers come online all day.

A file waiting in a peer's queue longer than ``downloader_slskd_stall_hours``
counts as failed and goes straight to step 2.

The same machinery fetches the missing tracks of an album the library holds in
part (:func:`start_tracks`), which is the other half of the problem: the album
that ended 7/9 last month, before any of this existed.
"""

import json
import threading
import time

from . import db

# A failed file is asked for again from the same peer this many times.
SAME_PEER_RETRIES = 2
# A file queued less than this long ago may not be in slskd's list yet.
_LIST_GRACE = 120
# How long after a job ends short before a fresh search for what's missing,
# and how many of those one job gets.
RESEARCH_AFTER = 6 * 3600
MAX_RESEARCHES = 2
# Folders kept per job to take missing tracks from.
_MAX_ALTERNATES = 8

ACTIVE = ("searching", "downloading")
FINISHED = ("complete", "partial", "failed", "cancelled")
# File states that are still waiting on slskd.
_LIVE = ("pending", "queued", "downloading")

_lock = threading.RLock()


# --- storage ------------------------------------------------------------------

def _row_to_job(row):
    job = dict(row)
    job["files"] = json.loads(job.get("files") or "[]")
    job["alternates"] = json.loads(job.get("alternates") or "[]")
    return job


def get(job_id):
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT * FROM download_jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_job(row) if row else None


def _save(job):
    job["updated_at"] = time.time()
    with db._write_lock:
        conn = db.get_connection()
        try:
            if job.get("id"):
                conn.execute(
                    "UPDATE download_jobs SET files = ?, alternates = ?, status = ?, "
                    "message = ?, format = ?, researches = ?, updated_at = ?, "
                    "finished_at = ? WHERE id = ?",
                    (json.dumps(job["files"]), json.dumps(job["alternates"]),
                     job["status"], job.get("message"), job.get("format"),
                     job.get("researches") or 0, job["updated_at"],
                     job.get("finished_at"), job["id"]),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO download_jobs (artist_id, artist, title, client, kind, "
                    "format, files, alternates, status, message, researches, "
                    "created_at, updated_at, finished_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job.get("artist_id"), job["artist"], job["title"], job["client"],
                     job.get("kind") or "album", job.get("format"),
                     json.dumps(job["files"]), json.dumps(job["alternates"]),
                     job["status"], job.get("message"), job.get("researches") or 0,
                     job["updated_at"], job["updated_at"], job.get("finished_at")),
                )
                job["id"] = cur.lastrowid
                job["created_at"] = job["updated_at"]
            conn.commit()
        finally:
            conn.close()
    return job


def list_jobs(limit=100, active_only=False):
    """Recent jobs, newest first, each with a summary for the UI."""
    where = f"WHERE status IN ({','.join('?' for _ in ACTIVE)})" if active_only else ""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM download_jobs {where} ORDER BY created_at DESC LIMIT ?",
            (*ACTIVE, limit) if active_only else (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [describe(_row_to_job(r)) for r in rows]


def describe(job):
    """A job as the API returns it: counts up front, files without internals."""
    files = job["files"]
    counts = {"done": 0, "failed": 0, "active": 0}
    for f in files:
        if f["state"] == "done":
            counts["done"] += 1
        elif f["state"] in _LIVE:
            counts["active"] += 1
        else:
            counts["failed"] += 1
    return {
        "id": job["id"],
        "artist_id": job.get("artist_id"),
        "artist": job["artist"],
        "title": job["title"],
        "client": job["client"],
        "kind": job.get("kind") or "album",
        "format": job.get("format"),
        "status": job["status"],
        "message": job.get("message"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "finished_at": job.get("finished_at"),
        "total": len(files),
        **counts,
        "alternates": len(job["alternates"]),
        "files": [{
            "name": _basename(f["filename"]),
            "username": f["username"],
            "state": f["state"],
            "percent": round(f.get("percent") or 0),
            "attempts": f.get("attempts") or 0,
            "error": f.get("error"),
        } for f in files],
    }


def delete(job_id):
    with db._write_lock:
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM download_jobs WHERE id = ?", (job_id,))
            conn.commit()
        finally:
            conn.close()


def clear_finished():
    """Forget every job that ended complete or cancelled. Returns how many."""
    with db._write_lock:
        conn = db.get_connection()
        try:
            cur = conn.execute(
                "DELETE FROM download_jobs WHERE status IN ('complete', 'cancelled')")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def latest_for(artist, title):
    """The newest job for this release under any spelling of the artist, or None."""
    want = (db.match_key(artist), db.match_key(title))
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM download_jobs ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        if (db.match_key(row["artist"]), db.match_key(row["title"])) == want:
            return _row_to_job(row)
    return None


def holds(artist, title):
    """Does a job already cover this release? True / False / None (no job).

    A job that ended short or failed doesn't: that's exactly the release a
    second attempt is for, even though slskd still lists its folder.
    """
    job = latest_for(artist, title)
    if job is None:
        return None
    return job["status"] in ACTIVE or job["status"] == "complete"


# --- helpers ------------------------------------------------------------------

def _basename(filename):
    return (filename or "").replace("/", "\\").rsplit("\\", 1)[-1]


def _client(key):
    from .plugins import get_plugin  # local: the plugins package imports half the app
    plugin = get_plugin("downloader", key)
    if plugin is None or not plugin.configured():
        return None
    return plugin


def _artist_id(artist):
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM artists WHERE sort_name = ?", ((artist or "").strip().lower(),)
        ).fetchone()
    finally:
        conn.close()
    return row["id"] if row else None


def _file_entry(client, artist, username, f):
    from .plugins.downloader.slskd import track_key
    return {
        "username": username,
        "filename": f["filename"],
        "size": f.get("size") or 0,
        "track": list(track_key(f["filename"], artist)),
        "state": "pending",
        "attempts": 0,
        "peers": [username],
        "error": None,
        "percent": 0,
        "queued_at": time.time(),
        "transfer_id": None,
    }


def _alternate(candidate):
    """What's kept of a search result for later: the peer, the folder, its files."""
    return {"username": candidate["username"], "path": candidate["path"],
            "format": candidate["format"], "files": candidate["files"],
            "used": False}


def _enqueue(client, job, entries):
    """Send *entries* (file dicts of *job*) to their peers, one request per peer."""
    by_peer = {}
    for f in entries:
        by_peer.setdefault(f["username"], []).append(f)
    for username, files in by_peer.items():
        try:
            client.enqueue(username, files)
        except RuntimeError as exc:
            for f in files:
                f["state"] = "failed"
                f["error"] = str(exc)
        else:
            now = time.time()
            for f in files:
                f["state"] = "pending"
                f["queued_at"] = now
                f["percent"] = 0
                f["error"] = None


def _status_of(job):
    files = job["files"]
    if not files:
        return "failed"
    if any(f["state"] in _LIVE for f in files):
        return "downloading"
    done = sum(1 for f in files if f["state"] == "done")
    if done == len(files):
        return "complete"
    return "partial" if done else "failed"


# --- starting -----------------------------------------------------------------

def start_album(client, artist, title, formats=None):
    """Search for a release, queue the best folder, and start following it.

    Synchronous (the search takes the client's search time). Returns the job's
    description; raises RuntimeError when the search finds nothing usable.
    """
    candidates = client.search_release(artist, title, formats)
    if not candidates:
        raise RuntimeError(
            f"nothing on Soulseek for {artist} - {title} in "
            f"{client.formats_label(formats)}")
    best = candidates[0]
    job = {
        "artist_id": _artist_id(artist),
        "artist": artist, "title": title, "client": client.key, "kind": "album",
        "format": best["format"],
        "files": [_file_entry(client, artist, best["username"], f) for f in best["files"]],
        "alternates": [_alternate(c) for c in candidates[1:_MAX_ALTERNATES + 1]],
        "status": "downloading",
        "message": f"{len(best['files'])} {best['format'].upper()} files from {best['username']}",
    }
    with _lock:
        _enqueue(client, job, job["files"])
        if _status_of(job) not in ACTIVE:
            # The peer refused every file outright: try the others now rather
            # than a minute from now.
            _advance(client, job, {})
        job["status"] = _status_of(job)
        _save(job)
        if job["status"] in FINISHED:
            _finish(job)
            _save(job)
    return describe(job)


def start_tracks(client, artist, title, tracks, formats=None, prefer=None):
    """Fetch just *tracks* (titles) of a release the library holds in part.

    Returns the job's description at once, in the ``searching`` state; the
    searches run on a thread, since there may be one per track.
    """
    job = {
        "artist_id": _artist_id(artist),
        "artist": artist, "title": title, "client": client.key, "kind": "tracks",
        "format": None, "files": [], "alternates": [],
        "status": "searching",
        "message": f"looking for {len(tracks)} missing track"
                   f"{'' if len(tracks) == 1 else 's'}",
    }
    _save(job)
    wanted = [t for t in tracks if t]

    def work():
        try:
            _fill_tracks(client, job, wanted, formats, prefer)
        except Exception as exc:  # noqa: BLE001 - the job records why, the thread lives
            with _lock:
                job["status"] = "failed"
                job["message"] = str(exc)
                job["finished_at"] = time.time()
                _save(job)

    threading.Thread(target=work, daemon=True, name=f"slskd-tracks-{job['id']}").start()
    return describe(job)


def _pick_tracks(artist, wanted, candidates, prefer=None, skip_peers=()):
    """For each wanted title, a (candidate, file) holding it -- or None.

    Folders holding more of the missing tracks win, so the gaps come from as
    few peers as possible; the album's own format (*prefer*) wins over another.
    """
    from .plugins.downloader.slskd import same_track, track_key
    keys = {t: (None, db.match_key(t)) for t in wanted}
    scored = []
    for cand in candidates:
        if cand["username"] in skip_peers:
            continue
        hits = {}
        for f in cand["files"]:
            fk = track_key(f["filename"], artist)
            for t, tk in keys.items():
                if t not in hits and same_track(fk, tk):
                    hits[t] = f
        if hits:
            same_format = 0 if prefer and cand["format"] == prefer else 1
            scored.append((same_format, -len(hits), cand, hits))
    scored.sort(key=lambda row: (row[0], row[1]))
    out = {}
    for _fmt, _n, cand, hits in scored:
        for t, f in hits.items():
            out.setdefault(t, (cand, f))
    return out


def _fill_tracks(client, job, wanted, formats, prefer):
    artist = job["artist"]
    candidates = client.search_release(artist, job["title"], formats, minimum=1)
    picks = _pick_tracks(artist, wanted, candidates, prefer)
    for t in wanted:
        if t in picks:
            continue
        # Not in any folder the album search found: look for the song itself.
        for cand in client.search_track(artist, t, formats):
            picks[t] = (cand, cand["files"][0])
            break
    files = []
    for t in wanted:
        if t not in picks:
            continue
        cand, f = picks[t]
        entry = _file_entry(client, artist, cand["username"], f)
        entry["want"] = t
        entry["format"] = cand["format"]
        files.append(entry)
    with _lock:
        used = {c["username"] for c, _f in picks.values()}
        job["alternates"] = [_alternate(c) for c in candidates
                             if c["username"] not in used][:_MAX_ALTERNATES]
        missing = [t for t in wanted if t not in picks]
        if not files:
            job["status"] = "failed"
            job["message"] = "none of the missing tracks are on Soulseek right now"
            job["finished_at"] = time.time()
            _save(job)
            return
        job["files"] = files
        job["format"] = files[0].get("format") or prefer
        _enqueue(client, job, files)
        job["status"] = _status_of(job)
        job["message"] = (f"{len(files)} of {len(wanted)} missing tracks found"
                          + (f"; not found: {', '.join(missing[:5])}" if missing else ""))
        _save(job)


# --- following ------------------------------------------------------------------

def _replacement(job, f, skip_peers):
    """Another peer's copy of *f*'s track, same format, from the job's alternates."""
    from .plugins.downloader.slskd import same_track, track_key
    want = tuple(f["track"])
    fmt = f.get("format") or job.get("format")
    for alt in job["alternates"]:
        if alt["username"] in skip_peers:
            continue
        if fmt and alt.get("format") != fmt:
            continue
        for candidate in alt["files"]:
            if same_track(track_key(candidate["filename"], job["artist"]), want):
                return alt, candidate
    return None


def _switch_folder(client, job):
    """Nothing landed from this peer: take the next folder whole instead."""
    tried = set()
    for f in job["files"]:
        tried.update(f.get("peers") or [f["username"]])
    for alt in job["alternates"]:
        if alt["used"] or alt["username"] in tried:
            continue
        alt["used"] = True
        for f in job["files"]:
            if f["state"] in _LIVE:
                client.cancel(f["username"], f.get("transfer_id"))
        job["files"] = [_file_entry(client, job["artist"], alt["username"], f)
                        for f in alt["files"]]
        job["format"] = alt["format"]
        job["message"] = (f"switched to {len(alt['files'])} {alt['format'].upper()} "
                          f"files from {alt['username']}")
        _enqueue(client, job, job["files"])
        return True
    return False


def _advance(client, job, transfers):
    """Fold slskd's view into the job and deal with anything that failed."""
    now = time.time()
    stall = client.stall_seconds()
    for f in job["files"]:
        if f["state"] in ("done", "gave_up"):
            continue
        seen = transfers.get((f["username"], f["filename"]))
        if seen is None:
            if f["state"] in _LIVE and now - (f.get("queued_at") or now) < _LIST_GRACE:
                continue
            if f["state"] in _LIVE and transfers:
                # Queued once, now absent: removed in slskd's own UI.
                f["state"] = "failed"
                f["error"] = f.get("error") or "no longer in slskd"
            continue
        f["transfer_id"] = seen.get("id")
        f["percent"] = seen.get("percent") or 0
        state = seen["state"]
        if state == "failed":
            if f["state"] != "failed":
                f["error"] = seen.get("error") or seen.get("raw_state")
            f["state"] = "failed"
        elif state == "queued" and now - (f.get("queued_at") or now) > stall:
            # Stuck in their upload queue: not worth asking the same peer again.
            client.cancel(f["username"], f.get("transfer_id"))
            f["state"] = "failed"
            f["error"] = "waited too long in the peer's queue"
            f["attempts"] = SAME_PEER_RETRIES
        else:
            f["state"] = state

    failed = [f for f in job["files"] if f["state"] == "failed"]
    if not failed:
        return
    nothing_landed = not any(f["state"] == "done" for f in job["files"])

    retry_same = []
    for f in failed:
        if (f.get("attempts") or 0) < SAME_PEER_RETRIES:
            f["attempts"] = (f.get("attempts") or 0) + 1
            client.cancel(f["username"], f.get("transfer_id"))
            retry_same.append(f)
    if retry_same:
        _enqueue(client, job, retry_same)

    out_of_retries = [f for f in failed if f not in retry_same]
    if not out_of_retries:
        return
    # A whole album that never started from this peer is better fetched whole
    # from someone else than stitched together one track at a time.
    if (job.get("kind") == "album" and nothing_landed
            and all(f["state"] not in _LIVE for f in job["files"])
            and _switch_folder(client, job)):
        return

    moved = []
    for f in out_of_retries:
        found = None
        if client.mix_peers() or job.get("kind") == "tracks":
            found = _replacement(job, f, set(f.get("peers") or []))
        if found is None:
            f["state"] = "gave_up"
            continue
        alt, candidate = found
        client.cancel(f["username"], f.get("transfer_id"))
        f["peers"] = (f.get("peers") or []) + [alt["username"]]
        f.update(username=alt["username"], filename=candidate["filename"],
                 size=candidate.get("size") or 0, attempts=0, transfer_id=None,
                 error=None)
        moved.append(f)
    if moved:
        _enqueue(client, job, moved)


def _finish(job):
    """A job has stopped: say so, record it, and let the library catch up."""
    from . import grabber
    from .plugins import notifier
    job["finished_at"] = time.time()
    done = sum(1 for f in job["files"] if f["state"] == "done")
    total = len(job["files"])
    label = f"{job['artist']} - {job['title']}"
    if job["status"] == "complete":
        job["message"] = f"all {total} files downloaded"
        notifier.notify("download_done", f"Downloaded {label}",
                        f"{total} {(job.get('format') or '').upper()} files from Soulseek.")
    else:
        job["message"] = f"{done} of {total} files downloaded"
        notifier.notify("download_partial", f"Incomplete: {label}",
                        f"{done} of {total} files downloaded; the rest failed "
                        "on every peer tried. It will search again later.")
    if job.get("artist_id") and job.get("kind") == "album":
        status = {"complete": "sent", "partial": "partial"}.get(job["status"], "failed")
        grabber.record(job["artist_id"], job["title"], status, job["message"])
    if done and job.get("artist_id"):
        # Whatever landed is worth seeing in the library now, not at the next
        # scheduled scan. Best effort: slskd's folder may not be in the library.
        threading.Thread(target=_rescan, args=(job["artist_id"],), daemon=True).start()


def _rescan(artist_id):
    from . import gaps, grabber, scanner
    try:
        scanner.scan_artist(artist_id)
    except Exception:  # noqa: BLE001 - a rescan is a courtesy
        return
    grabber.invalidate_library_index()
    gaps.invalidate()


def _research(client, job):
    """One fresh search for what a finished job is still missing."""
    missing = [f for f in job["files"] if f["state"] != "done"]
    if not missing:
        return
    tried = set()
    for f in job["files"]:
        tried.update(f.get("peers") or [f["username"]])
    formats = [job["format"]] if job.get("format") else None
    candidates = client.search_release(job["artist"], job["title"], formats, minimum=1)
    fresh = [c for c in candidates if c["username"] not in tried]
    job["alternates"] = [_alternate(c) for c in fresh[:_MAX_ALTERNATES]]
    if not any(f["state"] == "done" for f in job["files"]) and job.get("kind") == "album":
        if _switch_folder(client, job):
            job["status"] = _status_of(job)
            job["finished_at"] = None
            return
    moved = []
    for f in missing:
        found = _replacement(job, f, tried)
        if found is None:
            continue
        alt, candidate = found
        f["peers"] = (f.get("peers") or []) + [alt["username"]]
        f.update(username=alt["username"], filename=candidate["filename"],
                 size=candidate.get("size") or 0, attempts=0, transfer_id=None,
                 error=None, state="pending")
        moved.append(f)
    if moved:
        _enqueue(client, job, moved)
        job["status"] = _status_of(job)
        job["finished_at"] = None
        job["message"] = f"found {len(moved)} missing file{'' if len(moved) == 1 else 's'} on new peers"


def poll():
    """One pass over every job: fold in slskd's state, retry, finish. Returns counts."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM download_jobs WHERE status = 'downloading' "
            "OR (status IN ('partial', 'failed') AND researches < ? "
            "    AND finished_at IS NOT NULL AND finished_at < ?)",
            (MAX_RESEARCHES, time.time() - RESEARCH_AFTER),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return {"jobs": 0}
    transfers_by_client = {}
    changed = 0
    for row in rows:
        job = _row_to_job(row)
        client = _client(job["client"])
        if client is None or not hasattr(client, "transfers"):
            continue
        with _lock:
            job = get(job["id"]) or job  # an API action may have moved it on
            try:
                if job["status"] == "downloading":
                    if job["client"] not in transfers_by_client:
                        transfers_by_client[job["client"]] = client.transfers()
                    _advance(client, job, transfers_by_client[job["client"]])
                    job["status"] = _status_of(job)
                    if job["status"] in FINISHED:
                        _finish(job)
                elif job["status"] in ("partial", "failed"):
                    job["researches"] = (job.get("researches") or 0) + 1
                    _research(client, job)
                    if job["status"] not in ACTIVE:
                        # Still nothing: this search counts, try again later.
                        job["finished_at"] = time.time()
            except RuntimeError as exc:
                # slskd unreachable: leave the job as it is for the next pass.
                job["message"] = f"slskd: {exc}"
            _save(job)
            changed += 1
    return {"jobs": changed}


# --- actions from the UI ----------------------------------------------------------

def retry(job_id):
    """Try every file that hasn't landed again, starting with its current peer."""
    with _lock:
        job = get(job_id)
        if job is None:
            return None
        client = _client(job["client"])
        if client is None:
            raise RuntimeError(f"{job['client']} is not configured")
        pending = [f for f in job["files"] if f["state"] in ("failed", "gave_up")]
        if not pending:
            _research(client, job)
        else:
            for f in pending:
                client.cancel(f["username"], f.get("transfer_id"))
                f["attempts"] = 1
            _enqueue(client, job, pending)
            job["status"] = _status_of(job)
            job["finished_at"] = None if job["status"] in ACTIVE else job.get("finished_at")
            job["message"] = f"retrying {len(pending)} file{'' if len(pending) == 1 else 's'}"
        _save(job)
        return describe(job)


def another_peer(job_id):
    """Move every file that hasn't landed to someone else, searching if need be."""
    with _lock:
        job = get(job_id)
        if job is None:
            return None
        client = _client(job["client"])
        if client is None:
            raise RuntimeError(f"{job['client']} is not configured")
        waiting = [f for f in job["files"] if f["state"] != "done"]
        for f in waiting:
            if f["state"] in _LIVE:
                client.cancel(f["username"], f.get("transfer_id"))
            f["state"] = "failed"
            f["attempts"] = SAME_PEER_RETRIES
        _advance(client, job, {})
        if not any(f["state"] in _LIVE for f in job["files"]):
            # The alternates ran out: look again.
            _research(client, job)
        job["status"] = _status_of(job)
        if job["status"] in ACTIVE:
            job["finished_at"] = None
        elif not job.get("finished_at"):
            _finish(job)
        _save(job)
        return describe(job)


def cancel(job_id):
    """Stop a job: cancel its transfers in slskd and mark it cancelled."""
    with _lock:
        job = get(job_id)
        if job is None:
            return None
        client = _client(job["client"])
        for f in job["files"]:
            if f["state"] in _LIVE and client is not None:
                client.cancel(f["username"], f.get("transfer_id"))
                f["state"] = "gave_up"
                f["error"] = "cancelled"
        job["status"] = "cancelled"
        job["finished_at"] = time.time()
        job["message"] = "cancelled"
        _save(job)
        return describe(job)
