"""Grabbing releases: send each one to the first download client that can fetch it.

One place decides how a release is fetched, so a button press and the
automatic pass behave identically. Two orderings drive it:

- the quality profile (app/quality): which qualities are acceptable, best
  first, handed to the client as the file types to search for;
- the download-client priority: clients are tried in the user's order, and the
  first that finds the release wins.

Every attempt is written to ``grab_log`` -- which is what keeps the automatic
pass from grabbing the same album every half hour, and what lets a failure be
retried later instead of never.

Not downloading something twice takes more than that, because the same release
reaches us under several identities: MusicBrainz and the file tags spell an
artist differently, and a library with "Bonnie 'Prince' Billy" and
"Bonnie “Prince” Billy" as separate rows would otherwise queue the same album
once per spelling. So every comparison here runs through ``db.match_key``, and
before anything is sent the release is checked against what the library
already owns and what the download client is already holding.
"""

import threading
import time

from . import db, downloads, gaps, quality
from .plugins import downloader
from .plugins import notifier

# How long before a failed attempt is worth retrying.
RETRY_AFTER = 6 * 3600
# Releases older than this are ignored by the automatic pass, so turning it on
# doesn't try to fetch a decade of back catalogue at once.
DEFAULT_MAX_AGE_DAYS = 45
# Ceiling on how many releases one automatic pass will grab.
DEFAULT_BATCH = 5


def _skip_in_client():
    return (db.get_setting("skip_in_client") or "true").strip().lower() == "true"


# Reading 14k owned albums takes ~100ms, and a grab pass asks for the same
# answer once per release. It only changes when a scan or a grab writes, and
# both call invalidate_library_index().
_index_cache = {"at": 0.0, "value": None}
_index_lock = threading.Lock()
_INDEX_TTL = 60.0


def invalidate_library_index():
    with _index_lock:
        _index_cache["value"] = None


def library_index():
    """(artist key, album key) -> best owned quality, across duplicate artists.

    Ownership recorded against one spelling of an artist counts for every other
    spelling, which is what stops the same album being grabbed once per
    duplicate artist row. Memoised briefly -- see above.
    """
    with _index_lock:
        value = _index_cache["value"]
        if value is not None and (time.time() - _index_cache["at"]) < _INDEX_TTL:
            return value
    value = _build_library_index()
    with _index_lock:
        _index_cache.update(at=time.time(), value=value)
    return value


def _build_library_index():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT a.name AS artist, o.title, o.album_key, o.format "
            "FROM owned_albums o JOIN artists a ON a.id = o.artist_id"
        ).fetchall()
    finally:
        conn.close()
    index = {}
    for row in rows:
        key = (db.match_key(row["artist"]),
               db.match_key(row["title"] or row["album_key"]))
        index[key] = quality.better_of(index.get(key), row["format"])
    return index


def owned_quality(artist, title, index=None):
    """The best quality the library holds for this release, or None."""
    index = library_index() if index is None else index
    return index.get((db.match_key(artist), db.match_key(title)))


def client_priority():
    """Download clients in the user's order, configured ones only."""
    configured = {p.key: p for p in downloader.clients()}
    order = [k.strip() for k in (db.get_setting("downloader_priority") or "").split(",")
             if k.strip()]
    ordered = [configured.pop(key) for key in order if key in configured]
    # Anything not mentioned keeps registration order, after the ranked ones.
    return ordered + list(configured.values())


def _client_holds(client, artist, title):
    """True when the client already has this release (best effort)."""
    check = getattr(client, "has_release", None)
    if not callable(check):
        return False
    try:
        return bool(check(artist, title))
    except Exception:  # noqa: BLE001 - a client that can't answer isn't a veto
        return False


def grab(artist, title, *, downloader_key=None, reason="manual", refetch=False):
    """Fetch one release. Returns a result dict; never raises for a miss.

    *refetch* fetches a release the library holds in part all over again: the
    library and the client's own list both say "you have this", and for an
    album missing two tracks both are wrong.
    """
    # The library is the first authority: a copy recorded under any spelling of
    # the artist counts.
    if not refetch:
        have = owned_quality(artist, title)
        if have:
            return {"skipped": True, "reason": f"already in the library ({have})"}

    clients = client_priority()
    if downloader_key:
        clients = [c for c in clients if c.key == downloader_key]
    if not clients:
        return {"error": "no download client configured"}

    # Hand over the profile's extensions so the search obeys the profile.
    formats = quality.profile_extensions(quality.quality_order())
    tried = []
    for client in clients:
        if _skip_in_client() and not refetch and _client_holds(client, artist, title):
            tried.append(f"{client.label}: already has it")
            continue
        job = None
        try:
            if hasattr(client, "search_release"):
                # Followed afterwards (app/downloads), so a file that fails is
                # retried or fetched elsewhere instead of forgotten.
                job = downloads.start_album(client, artist, title, formats=formats)
                message = f"Queued {job['message']}."
            else:
                message = client.download_release(artist, title, formats=formats)
        except (RuntimeError, NotImplementedError) as exc:
            tried.append(f"{client.label}: {exc}")
            continue
        result = {"sent": True, "client": client.label, "message": message}
        if job:
            result["job_id"] = job["id"]
        _announce(artist, title, result, reason)
        return result

    detail = "; ".join(tried) if tried else "nothing available"
    return {"error": f"no copy of {artist} - {title} could be grabbed ({detail})"}


def grab_missing(artist, title, tracks, *, downloader_key=None, owned_format=None):
    """Fetch the tracks a partly-owned album is missing.

    A client that can follow a download (slskd) is asked for just those
    songs; any other gets the whole album again (see *refetch* on :func:`grab`).
    """
    tracks = [t for t in (tracks or []) if t]
    if not tracks:
        return {"error": "no missing tracks to fetch"}
    clients = client_priority()
    if downloader_key:
        clients = [c for c in clients if c.key == downloader_key]
    searchers = [c for c in clients if hasattr(c, "search_release")]
    if searchers:
        client = searchers[0]
        prefer = _extension_of(owned_format)
        formats = quality.profile_extensions(quality.quality_order())
        job = downloads.start_tracks(client, artist, title, tracks,
                                     formats=formats, prefer=prefer)
        return {"sent": True, "client": client.label,
                "job_id": job["id"], "message": job["message"]}
    if not clients:
        return {"error": "no download client configured"}
    result = grab(artist, title, downloader_key=downloader_key, refetch=True)
    if result.get("sent"):
        result["message"] = (f"{result.get('client')} has the whole album again; "
                             "it will fetch what's missing")
    return result


def missing_tracks(artist, title, mbid=None):
    """Titles on the release's tracklist that no library can find right now.

    Asked fresh rather than from the cached per-track markers: the point is
    usually to check again after a download, and a marker is trusted for weeks.
    """
    from concurrent.futures import ThreadPoolExecutor

    from . import album, librarytrack  # local: both import the plugin registry

    detail = album.get_album_detail(artist, title, mbid=mbid)
    names = [t.get("name") for t in detail.get("tracks") or [] if t.get("name")]
    if not names:
        return [], 0

    def owned(name):
        try:
            return librarytrack.locate(artist, name)[1] is not None
        except Exception:  # noqa: BLE001 - an unreachable library can't say "missing"
            return True

    with ThreadPoolExecutor(max_workers=4) as pool:
        have = list(pool.map(owned, names))
    return [n for n, h in zip(names, have) if not h], len(names)


def _extension_of(label):
    """'FLAC 24bit' -> 'flac', 'MP3 320' -> 'mp3': a quality label's file type."""
    return quality._LABEL_EXTENSIONS.get(label) if label else None


def _announce(artist, title, result, reason):
    what = result.get("message") or "release"
    notifier.notify(
        "grab_sent",
        f"{'Auto-grabbed' if reason == 'auto' else 'Grabbed'} {artist} - {title}",
        f"{what} sent to {result.get('client')}.",
    )


# --- the automatic pass -----------------------------------------------------

def _enabled():
    return (db.get_setting("autograb_enabled") or "false").strip().lower() == "true"


def _int_setting(key, default):
    try:
        return int(db.get_setting(key) or default)
    except (TypeError, ValueError):
        return default


def _scope_clause():
    """Which artists the automatic pass is allowed to act on."""
    scope = (db.get_setting("autograb_scope") or "notify").strip().lower()
    if scope == "following":
        return "a.subscription IN ('subscribed', 'notify')"
    return "a.subscription = 'notify'"


def pending(limit=None):
    """Releases the automatic pass would grab right now, best candidates first.

    A release qualifies when its artist is in scope, it is out (not a future
    date), it is recent enough, the library doesn't already own it, and it
    hasn't been attempted lately.
    """
    max_age = _int_setting("autograb_max_age_days", DEFAULT_MAX_AGE_DAYS)
    limit = limit or _int_setting("autograb_batch", DEFAULT_BATCH)
    today = time.strftime("%Y-%m-%d")
    oldest = time.strftime("%Y-%m-%d", time.gmtime(time.time() - max_age * 86400))

    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT r.id AS release_id, r.title, r.release_date, r.primary_type, "
            "a.id AS artist_id, a.name AS artist, a.monitor_types "
            "FROM releases r JOIN artists a ON a.id = r.artist_id "
            f"WHERE {_scope_clause()} AND a.ignored = 0 "
            "  AND r.release_date IS NOT NULL AND r.release_date <> '' "
            "  AND r.release_date <= ? AND r.release_date >= ? "
            "ORDER BY r.release_date DESC",
            (today, oldest),
        ).fetchall()
        attempts = {}
        for r in conn.execute(
            "SELECT g.album_key, g.status, g.attempted_at, a.name AS artist "
            "FROM grab_log g JOIN artists a ON a.id = g.artist_id"
        ):
            # Keyed by name, not id: an attempt against one spelling of an
            # artist counts for their duplicates too.
            key = (db.match_key(r["artist"]), db.match_key(r["album_key"]))
            previous = attempts.get(key)
            if previous is None or r["attempted_at"] > previous[1]:
                attempts[key] = (r["status"], r["attempted_at"])
    finally:
        conn.close()
    owned = library_index()

    now = time.time()
    out = []
    seen = set()
    for row in rows:
        types = db.monitored_types(row["monitor_types"])
        kind = (row["primary_type"] or "Album").lower()
        if kind not in types:
            continue
        key = (db.match_key(row["artist"]), db.match_key(row["title"]))
        if key in seen:
            continue  # the same release under another spelling of the artist
        seen.add(key)
        if owned.get(key):
            continue  # already in the library
        status, attempted_at = attempts.get(key, (None, None))
        if status in ("sent", "partial"):
            # 'partial': a download job is still chasing the missing files
            # (app/downloads), which beats grabbing the whole album again.
            continue
        if attempted_at and (now - attempted_at) < RETRY_AFTER:
            continue
        out.append({
            "artist_id": row["artist_id"],
            "artist": row["artist"],
            "title": row["title"],
            "release_date": row["release_date"],
            "type": row["primary_type"],
        })
        if len(out) >= limit:
            break
    return out


def record(artist_id, title, status, detail=""):
    """Remember an attempt so the pass doesn't repeat it every half hour."""
    with db._write_lock:
        conn = db.get_connection()
        try:
            conn.execute(
                "INSERT INTO grab_log (artist_id, album_key, title, status, detail, "
                "attempted_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(artist_id, album_key) DO UPDATE SET "
                "status = excluded.status, detail = excluded.detail, "
                "attempted_at = excluded.attempted_at",
                (artist_id, db.owned_album_key(title), title, status, detail[:500],
                 time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def run_pass(force=False):
    """One automatic round. Returns {checked, sent, failed, results}."""
    if not force and not _enabled():
        return {"checked": 0, "sent": 0, "skipped": 0, "failed": 0, "results": []}
    items = pending()
    results = []
    sent = failed = skipped = 0
    for item in items:
        outcome = grab(item["artist"], item["title"], reason="auto")
        if outcome.get("sent"):
            sent += 1
            record(item["artist_id"], item["title"], "sent",
                   f"via {outcome.get('client') or ''}")
        elif outcome.get("skipped"):
            skipped += 1
            # Recorded as sent: it is already had, so nothing should ask again.
            record(item["artist_id"], item["title"], "sent", outcome.get("reason", ""))
        else:
            failed += 1
            record(item["artist_id"], item["title"], "failed", outcome.get("error", ""))
        results.append({**item, **outcome})
    if sent or skipped:
        gaps.invalidate()
    return {"checked": len(items), "sent": sent, "skipped": skipped,
            "failed": failed, "results": results}
