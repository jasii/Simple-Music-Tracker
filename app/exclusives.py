"""How much of an EP or single isn't in the library yet.

A single is usually an album track with a new cover, and an EP is often half
old material -- so the interesting number on those rows is how many of their
songs the library hasn't got, wherever it keeps them. Working that out means a
tracklist (MusicBrainz, with the catalogues covering what it has no id for)
and a library search per track, one release at a time, so it runs in a
background thread per artist and is cached against a fingerprint of the
discography: it recomputes when that changes, not before.
"""

import hashlib
import re
import threading
import time

from . import album as album_detail, db, lastfm, librarytrack, musicbrainz

# A computed map outlives the tracklists it was built from; the fingerprint is
# what actually decides when to redo it, and store_keep_days how long it may be
# read for at all.

# Politeness gap between tracklist lookups, and a ceiling on how many releases
# one artist's pass will read (a 400-release discography shouldn't hold a thread
# for ten minutes).
_GAP_S = 0.1
_MAX_RELEASES = 250

_ALBUM_TYPES = ("Album",)
_EXTRA_TYPES = ("EP", "Single")

# artist_id -> Thread, so one artist isn't computed twice at once.
_jobs = {}
_jobs_lock = threading.Lock()


# Suffixes that label a release rather than a different recording: the album
# cut and its remaster are the same song. A remix, a dub, a live take or an
# edit is NOT in here -- those are their own songs, and a single carrying one
# counts as something the albums don't have.
_NOISE_SUFFIX = re.compile(
    r"""\s*[\(\[\-]\s*
        (?:\d{4}\s+)?(?:digital\s+|\d{4}\s+)?
        (?:re-?master(?:ed)?(?:\s+version)?(?:\s+\d{4})?
          |explicit(?:\s+version)?|clean(?:\s+version)?
          |bonus(?:\s+track)?|album\s+version|original\s+version
          |mono|stereo|deluxe(?:\s+edition)?|anniversary(?:\s+edition)?|reissue)
        \s*[\)\]]?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# "(feat. Someone)" moves around between releases without changing the song.
_FEATURING = re.compile(r"[\(\[]?\s*(?:feat|ft)\.?\s[^\)\]]*[\)\]]?", re.IGNORECASE)


def _track_key(name):
    """Comparison key for a track title.

    Case, punctuation, featured-artist credits and release labels ("-
    Remastered", "(Bonus Track)") are dropped; anything that marks a different
    recording -- a remix, a dub, an edit, a live version -- is kept, so a remix
    single counts as its own song rather than as the album track it came from.
    """
    text = (name or "").strip()
    while True:
        trimmed = _NOISE_SUFFIX.sub("", text).strip()
        if trimmed == text or not trimmed:
            break
        text = trimmed
    text = _FEATURING.sub(" ", text)
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _tracks_key(artist, title):
    # Versioned alongside the album cache: lists stored before MusicBrainz
    # became the source came from a catalogue instead.
    return f"trklist3:{(artist or '').strip().lower()}|{(title or '').strip().lower()}"


def _tracklist(artist, title, mbid=None):
    """Track titles for one release: MusicBrainz, else the catalogues.

    MusicBrainz named the release in the first place, so its tracklist is the
    one these counts are measured against; Last.fm and iTunes answer only for a
    release with no release-group id to ask about.
    """
    cache_key = _tracks_key(artist, title)
    cached = db.get_json_cache(cache_key, max_age=db.cache_max_age("hit"))
    if cached:
        return cached
    # An empty list is a remembered miss: honour it only briefly, since the
    # release may simply not have been indexed yet.
    if cached is not None and db.get_json_cache(
            cache_key, max_age=db.cache_max_age("miss")) is not None:
        return []

    names = []
    if mbid:
        try:
            names = [t["name"] for t in musicbrainz.release_tracks(mbid)]
        except Exception:  # noqa: BLE001 - a failed lookup is just an empty list
            names = []
    if not names:
        try:
            info = lastfm.get_album_info(artist, title) or {}
            names = [t.get("name") for t in info.get("tracks") or [] if t.get("name")]
        except Exception:  # noqa: BLE001
            names = []
    if not names:
        try:
            names = [t["name"] for t in album_detail._itunes_tracks(artist, title)]
        except Exception:  # noqa: BLE001
            names = []
    db.set_json_cache(cache_key, names)
    return names


def _fingerprint(items):
    """Identifies the discography this result was built from.

    sha1, not hash(): the built-in is salted per process, so a stored
    fingerprint would never match again after a restart.
    """
    parts = sorted(
        f"{it.get('mbid') or it.get('title')}:{it.get('primary_type')}" for it in items
    )
    # Versioned whenever the maths or the inputs change, so stored results are
    # rebuilt rather than trusted: v3 dropped counts built on loose catalogue
    # matching, v5 stopped folding remixes onto the track they remix, v6 reads
    # tracklists from MusicBrainz first, v7 counts against the library rather
    # than against the artist's albums, v8 keeps the album songs as well.
    return "v8:" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _result_key(artist_id):
    return f"exclusives:{artist_id}"


def release_key(item):
    """How one release is addressed in the counts map."""
    return item.get("mbid") or ("t:" + _track_key(item.get("title")))


def _compute(artist_id, name, items):
    """How many of each EP's and single's songs aren't in the library.

    Measured against the library itself -- the same per-track search that
    streams a song -- not against the artist's albums: a single whose songs
    play from your server is not something you're missing, whichever record
    they happen to sit on there.
    """
    # Albums first: their songs are what makes a single "just the album track
    # again", which the album page marks per track.
    album_keys = set()
    for item in items:
        if item.get("primary_type") not in _ALBUM_TYPES:
            continue
        names = _tracklist(name, item.get("title"), item.get("mbid"))
        time.sleep(_GAP_S)
        album_keys.update(_track_key(n) for n in names if _track_key(n))

    owned_by_key, owned_by_mbid = db.get_owned_sources(artist_id)
    if not owned_by_key and not owned_by_mbid:
        # Nothing by this artist in the library, so "you haven't got this"
        # would be true of every song: noise, not news.
        return {"counts": {}, "releases_read": 0, "partial": False,
                "covered": [], "no_library": True,
                "album_keys": sorted(album_keys)}

    counts = {}
    covered = []
    extras = [it for it in items if it.get("primary_type") in _EXTRA_TYPES]
    read = 0
    todo = extras[:_MAX_RELEASES]
    _set_progress(artist_id, 0, len(todo))
    for done, item in enumerate(todo, start=1):
        names = _tracklist(name, item.get("title"), item.get("mbid"))
        time.sleep(_GAP_S)
        _set_progress(artist_id, done, len(todo))
        if not names:
            continue
        read += 1
        marks = librarytrack.markers(name, names)
        missing = [n for n in names if not (marks.get(n) or {}).get("key")]
        counts[release_key(item)] = {"unique": len(missing), "total": len(names)}
        if not missing:
            # Every song on it is already in the library.
            covered.append(release_key(item))
    return {
        "counts": counts,
        "releases_read": read,
        "partial": len(extras) > _MAX_RELEASES,
        "covered": covered,
        # Every song the artist's albums carry, so one release's page can say
        # which of its tracks appear nowhere else.
        "album_keys": sorted(album_keys),
    }


def _progress_key(artist_id):
    return f"exclusives:progress:{artist_id}"


def _set_progress(artist_id, done, total):
    """Publish how far the pass has got, for the page's progress bar."""
    db.set_json_cache(_progress_key(artist_id), {"done": done, "total": total})


def _run(artist_id, name, items, fingerprint):
    try:
        result = _compute(artist_id, name, items)
        result["fingerprint"] = fingerprint
        result["built_at"] = time.time()
        db.set_json_cache(_result_key(artist_id), result)
    except Exception:  # noqa: BLE001 - reported as "not ready", not a crash
        pass
    finally:
        with _jobs_lock:
            _jobs.pop(artist_id, None)


def track_key(name):
    """Public form of the title key, for callers marking one release's tracks."""
    return _track_key(name)


# Setting that turns "you already have every song on this" into an owned mark.
COVERED_SETTING = "own_covered_releases"


def covered_enabled():
    return (db.get_setting(COVERED_SETTING) or "false").strip().lower() not in (
        "", "false", "0", "off", "no"
    )


def album_track_keys(artist_id):
    """Every song on this artist's albums, as comparison keys. Cache only."""
    cached = db.get_json_cache(_result_key(artist_id),
                               max_age=db.cache_max_age("hit")) or {}
    return set(cached.get("album_keys") or [])


def covered_keys(artist_id):
    """Release keys whose every song is on an album you own. Cache only."""
    cached = db.get_json_cache(_result_key(artist_id), max_age=db.cache_max_age("hit")) or {}
    return set(cached.get("covered") or [])


def status(artist_id, *, build=True, fetch=True):
    """Counts for one artist's EPs and singles, building them if needed.

    Returns {ready, running, counts, ...}. Never blocks: a cold artist comes
    back running=True with no counts, and the caller polls. *fetch* False
    limits it to the stored discography, for a caller that must not wait out a
    rate-limited walk of MusicBrainz (the album page asks that way).
    """
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT name, mbid FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    if not row["mbid"]:
        return {"ready": False, "running": False, "counts": {},
                "message": "no MusicBrainz match for this artist yet"}

    if fetch:
        items = musicbrainz.fetch_discography(row["mbid"]) or []
    else:
        items = musicbrainz.cached_discography(row["mbid"]) or []
    if not items:
        return {"ready": False, "running": False, "counts": {},
                "message": "discography not read yet"}
    fingerprint = _fingerprint(items)
    cached = db.get_json_cache(_result_key(artist_id), max_age=db.cache_max_age("hit")) or {}
    fresh = cached.get("fingerprint") == fingerprint

    with _jobs_lock:
        running = artist_id in _jobs
        if build and not fresh and not running:
            job = threading.Thread(
                target=_run, args=(artist_id, row["name"], items, fingerprint),
                daemon=True,
            )
            _jobs[artist_id] = job
            running = True
            job.start()

    return {
        "ready": fresh,
        "running": running,
        # How far the running pass has got, for the page's progress bar.
        "progress": (db.get_json_cache(_progress_key(artist_id)) or {}) if running else {},
        # Stale counts are still worth showing while the new pass runs.
        "counts": cached.get("counts") or {},
        "releases_read": cached.get("releases_read", 0),
        "partial": bool(cached.get("partial")),
    }
