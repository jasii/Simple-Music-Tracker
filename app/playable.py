"""Where every track on one release can be played from.

Resolving a single track is quick when the answer is cached and slow when it
isn't -- a library search, then each preview source in turn (a catalogue
lookup, and for the records nobody sells, reading a track page for the video it
plays). An album page wants all of that answered at once so it can show a play
button only on the rows that will actually play, so it runs as one background
pass per release, writing each answer as it lands, and the page polls.
"""

import hashlib
import threading
import time

from . import db, librarytrack, videoaudio

# The map itself is cheap to rebuild from the per-track caches underneath it,
# so how long it's read for is just store_keep_days like everything else.

# release key -> Thread, so one release isn't resolved twice at once.
_jobs = {}
_jobs_lock = threading.Lock()


def _key(artist, album):
    return f"playable:{(artist or '').strip().lower()}|{(album or '').strip().lower()}"


def _fingerprint(titles):
    """What a stored map was built from: the tracklist and the source order.

    Reordering the preview sources, or which library plays a track, changes
    the answers -- so a map built under the old order is stale, not a hit.
    """
    from . import librarytrack
    from .plugins import metadata as registry

    joined = "|".join([
        # Bumped when the name matching changes: an answer of "nothing has
        # this" is only as good as the rules that failed to find it.
        "rules3",
        *titles,
        ",".join(p.key for p in registry.sources("track_preview")),
        ",".join(librarytrack.playback_order()),
    ])
    return hashlib.sha1(joined.encode()).hexdigest()[:16]


def resolve_one(artist, title, page_url=None):
    """What one track can be played from.

    Your own library first, always: that's the whole song rather than a
    fragment of it. Failing that, the preview sources in the order set in
    Settings > Metadata -- a catalogue sample streams through the app, while a
    video source hands back an embed id instead.

    Returns {"kind": "library"|"sample"|"youtube"|"none", ...}.
    """
    from .main import _track_stream_url  # local: main imports this module
    from .plugins import metadata as registry

    try:
        plugin, found = librarytrack.locate(artist, title)
    except Exception:  # noqa: BLE001 - a sick library can't block playback
        plugin, found = None, None
    if found:
        return {"kind": "library", "label": plugin.display_label(),
                # So the player can credit the source, mark and all.
                "source_url": found.get("page_url"),
                "icon": plugin.icon_name(),
                "stream": _track_stream_url(artist, title)}

    for source in registry.sources("track_preview"):
        try:
            answer = source.track_preview(artist, title, page_url=page_url)
        except Exception:  # noqa: BLE001 - one bad source isn't the track
            continue
        if not answer:
            continue
        if answer.get("stream_url"):
            # Proxied rather than linked, so the player only ever talks to us.
            return {"kind": "sample", "label": answer.get("label") or source.label,
                    "source": source.key,
                    "source_url": answer.get("source_url"),
                    "icon": source.icon_name(),
                    "stream": _track_stream_url(artist, title)}
        if answer.get("youtube_id"):
            found = {"kind": "youtube", "label": answer.get("label") or "video",
                     "source": source.key, "youtube_id": answer["youtube_id"],
                     "source_url": answer.get("source_url"),
                     # The audio is a video's, whoever found it.
                     "icon": "youtube"}
            # With the extractor installed the audio plays through the app's
            # own player; without it the page falls back to the embed.
            if videoaudio.available():
                found["stream"] = _track_stream_url(artist, title)
            return found
    return {"kind": "none"}


def _store(artist, album, payload):
    db.set_json_cache(_key(artist, album), payload)


def _run(artist, album, tracks, fingerprint):
    """Resolve each track in turn, publishing the map as it fills in."""
    payload = {"fingerprint": fingerprint, "tracks": {}, "built_at": time.time()}
    try:
        for title, page_url in tracks:
            if not title:
                continue
            try:
                payload["tracks"][title] = resolve_one(artist, title, page_url)
            except Exception:  # noqa: BLE001 - one bad track isn't the album
                payload["tracks"][title] = {"kind": "none"}
            # Written as it goes, so the page fills in row by row rather than
            # sitting empty until the last (slow) lookup finishes.
            _store(artist, album, payload)
    finally:
        payload["done"] = True
        _store(artist, album, payload)
        with _jobs_lock:
            _jobs.pop(_key(artist, album), None)


def _has_misses(payload):
    """Did anything in this map come back with nothing to play?"""
    return any((info or {}).get("kind") == "none"
               for info in (payload.get("tracks") or {}).values())


def _stale(payload):
    """Is this map older than the window a remembered miss is trusted for?"""
    built = payload.get("built_at") or 0
    window = db.cache_max_age("miss")
    if not window:
        return False
    return (time.time() - built) > window


def status(artist, album, tracks, *, build=True):
    """{ready, running, tracks} for one release; starts the pass if needed.

    *tracks* is a list of (title, page_url) pairs, in tracklist order.
    """
    titles = [t for t, _u in tracks if t]
    if not artist or not album or not titles:
        return {"ready": False, "running": False, "tracks": {},
                "progress": {"done": 0, "total": 0}}
    cache_key = _key(artist, album)
    fingerprint = _fingerprint(titles)
    cached = db.get_json_cache(cache_key, max_age=db.cache_max_age("hit")) or {}
    same = cached.get("fingerprint") == fingerprint
    ready = bool(same and cached.get("done"))
    if ready and _has_misses(cached) and _stale(cached):
        # Some track had nothing behind it last time. That's the one answer
        # worth asking again -- a video gets attached to a Last.fm page, a
        # catalogue lists a record it hadn't, the file lands in the library --
        # so a map with gaps in it expires like any other remembered miss.
        ready = False

    with _jobs_lock:
        running = cache_key in _jobs
        if build and not ready and not running:
            job = threading.Thread(
                target=_run, args=(artist, album, tracks, fingerprint), daemon=True
            )
            _jobs[cache_key] = job
            running = True
            job.start()

    resolved = (cached.get("tracks") or {}) if same else {}
    return {
        "ready": ready,
        "running": running,
        # What the page's progress bar needs: how many tracks have an answer.
        "progress": {"done": len(resolved), "total": len(titles)},
        # Partial answers are worth showing: those rows can play already.
        "tracks": resolved,
    }
