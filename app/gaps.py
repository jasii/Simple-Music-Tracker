"""What the library is missing, and which albums it holds only in part.

Both answers come from data the app already holds, so neither costs a network
call: an artist's MusicBrainz discography (cached under ``disco:<mbid>``) and
the albums the scanners recorded in ``owned_albums``.

Missing    = a release group of a type the artist is monitored for that no
             library source owns.
Incomplete = an album owned with fewer tracks than the shortest official
             edition MusicBrainz lists for it (``musicbrainz.min_track_count``),
             so a standard-edition copy isn't "missing" a bonus track. Counts
             are read from cache only; the ones not fetched yet are queued for
             :func:`warm_tracklists`, which the scheduler runs a few at a time.

The whole sweep is ~300ms over a 2500-artist library, so it runs on demand and
is memoised briefly rather than kept in its own table.
"""

import json
import threading
import time

from . import db, exclusives, musicbrainz

# Quality labels the scanner records, best first. Anything unrecognised sorts
# last.
QUALITY_RANK = [
    "FLAC 24bit", "WAV", "FLAC", "ALAC", "APE",
    "MP3 320", "MP3 V0", "AAC", "Opus", "MP3 V2", "Vorbis", "MP3",
]
_RANK = {name: i for i, name in enumerate(QUALITY_RANK)}

# How long a built table is trusted before a request rebuilds it. The inputs
# (cached discographies, owned albums) only move when a scan
# or a refresh writes something, and both invalidate it explicitly.
_TTL = 900.0
_cache = {"at": 0.0, "rebuilding": False}
_lock = threading.Lock()


def invalidate():
    """Mark the gaps table stale (call after a scan or an ownership change)."""
    db.set_setting("library_gaps_built_at", "0")
    with _lock:
        _cache["at"] = 0.0


def quality_rank(label):
    """Sort key for a quality label: lower is better, unknown last."""
    return _RANK.get(label, len(QUALITY_RANK))


def ranked_formats(labels):
    """Every distinct quality among *labels*, best first.

    A record held by two libraries (or twice by one) is often held in two
    qualities -- a FLAC rip on the server and a 320 on the laptop -- and both
    are worth showing, so this keeps them all rather than picking a winner.
    """
    seen = []
    for label in labels or ():
        if label and label not in seen:
            seen.append(label)
    return sorted(seen, key=quality_rank)


def better_quality(a, b):
    """The better of two quality labels (None-safe)."""
    if not a:
        return b
    if not b:
        return a
    return a if quality_rank(a) <= quality_rank(b) else b


def _like_escape(text):
    """Escape a user's search text so LIKE treats it as characters."""
    return (text.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_"))


_TYPE_KEYS = {"Album": "album", "EP": "ep", "Single": "single"}


def _owned_maps(conn):
    """artist_id -> ({album_key: quality}, {rg_mbid: quality}), plus the same
    two maps holding the most tracks any source has of each album."""
    by_key, by_mbid = {}, {}
    tracks_by_key, tracks_by_mbid = {}, {}
    for r in conn.execute(
        "SELECT artist_id, album_key, rg_mbid, format, track_count FROM owned_albums"
    ):
        aid = r["artist_id"]
        tracks = r["track_count"] or 0
        keys = by_key.setdefault(aid, {})
        counts = tracks_by_key.setdefault(aid, {})
        # Also under the looser key, so "Young Heartache" in a library answers
        # for "Young Heartache EP" in MusicBrainz.
        alt = db.owned_album_alt_key(r["album_key"])
        for key in (r["album_key"], alt):
            if key:
                keys[key] = better_quality(keys.get(key), r["format"])
                counts[key] = max(counts.get(key, 0), tracks)
        if r["rg_mbid"]:
            mbids = by_mbid.setdefault(aid, {})
            mbids[r["rg_mbid"]] = better_quality(mbids.get(r["rg_mbid"]), r["format"])
            mcounts = tracks_by_mbid.setdefault(aid, {})
            mcounts[r["rg_mbid"]] = max(mcounts.get(r["rg_mbid"], 0), tracks)
    return by_key, by_mbid, tracks_by_key, tracks_by_mbid


# Shortest-edition track counts are cached by musicbrainz under this prefix.
_TRACKLIST_PREFIX = musicbrainz._MIN_PREFIX
# Release groups whose tracklist the incomplete check wanted and didn't have.
_WANTED_KEY = "gaps:tracklists_wanted"


def _tracklist_sizes():
    """{rg_mbid: shortest edition's track count} for every one cached (0 = unknown)."""
    out = {}
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT cache_key, payload FROM json_cache WHERE cache_key LIKE ?",
            (_TRACKLIST_PREFIX + "%",),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            value = int(json.loads(row["payload"]).get("value") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        out[row["cache_key"][len(_TRACKLIST_PREFIX):]] = value
    return out


def warm_tracklists(limit=10):
    """Fetch a few of the tracklists the last rebuild wanted. Returns how many.

    MusicBrainz allows a request a second, so this is paced by its caller (the
    scheduler, a handful per minute) and the gaps table is marked stale once
    something new arrives, so the next rebuild can use it.
    """
    wanted = (db.get_json_cache(_WANTED_KEY) or {}).get("mbids") or []
    if not wanted:
        return 0
    have = _tracklist_sizes()
    todo = [m for m in wanted if m not in have][:limit]
    fetched = 0
    for mbid in todo:
        try:
            musicbrainz.min_track_count(mbid)
            fetched += 1
        except Exception:  # noqa: BLE001 - one bad lookup mustn't stop the rest
            continue
    remaining = [m for m in wanted if m not in todo and m not in have]
    db.set_json_cache(_WANTED_KEY, {"mbids": remaining})
    if fetched and not remaining:
        invalidate()
    return fetched


def _covered_maps():
    """{artist_id: {release keys whose songs are all in the library}}.

    Read in one query rather than per artist: the rebuild streams thousands of
    artists, and these are the results the EP/single pass already stored.
    """
    out = {}
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT cache_key, payload FROM json_cache WHERE cache_key LIKE 'exclusives:%'"
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            aid = int(row["cache_key"].split(":", 1)[1])
            payload = json.loads(row["payload"])
        except (ValueError, TypeError, IndexError):
            continue
        keys = set(payload.get("covered") or [])
        if keys:
            out[aid] = keys
    return out


def _rows_for(artist, items, owned_by_key, owned_by_mbid,
              covered_by_artist=None, links_by_artist=None, track_maps=None,
              tracklists=None, wanted=None):
    """Every gap for one artist: yields (kind, row-tuple) pairs.

    *track_maps* is (tracks by key, tracks by mbid) from _owned_maps and
    *tracklists* {rg_mbid: size}; release groups owned with a known track count
    but no cached tracklist are added to the *wanted* set.
    """
    aid = artist["id"]
    keys = owned_by_key.get(aid, {})
    mbids = owned_by_mbid.get(aid, {})
    covered = (covered_by_artist or {}).get(aid) or set()
    links = (links_by_artist or {}).get(aid) or {}
    tracks_by_key, tracks_by_mbid = track_maps or ({}, {})
    key_tracks = tracks_by_key.get(aid, {})
    mbid_tracks = tracks_by_mbid.get(aid, {})
    tracklists = tracklists if tracklists is not None else {}
    alt_index = db.alt_key_index(keys)
    monitored = db.monitored_types(artist["monitor_types"])

    for item in items:
        type_key = _TYPE_KEYS.get(item.get("primary_type"))
        if not type_key or type_key not in monitored:
            continue
        title = item.get("title") or ""
        # The same matching the artist page shows, manual links included.
        matched = db.owned_album_keys(links, set(keys), item.get("mbid"), title,
                                      alt_index)
        have_tracks = max([key_tracks.get(k, 0) for k in matched] or [0])
        if item.get("mbid") in mbids:
            owned = True
            quality_label = mbids.get(item.get("mbid"))
            have_tracks = max(have_tracks, mbid_tracks.get(item.get("mbid"), 0))
        else:
            owned = bool(matched)
            quality_label = None
            for key in matched:
                quality_label = better_quality(quality_label, keys.get(key))
        if not owned and covered and exclusives.release_key(item) in covered:
            # Every song on this EP/single is on a record already in the
            # library: not a gap (see the own_covered_releases setting).
            owned = True
        date = item.get("release_date") or ""
        secondary_list = [str(x) for x in (item.get("secondary_types") or [])]
        secondary = ",".join(secondary_list)
        noisy = 1 if any(x.lower() in SECONDARY_NOISE for x in secondary_list) else 0
        if not owned:
            yield ("missing", (
                "missing", db.match_key(artist["name"]), db.match_key(title),
                aid, artist["name"], title, type_key, date, item.get("mbid"),
                None, secondary, noisy, None, None,
            ))
        if owned and have_tracks and item.get("mbid"):
            # Owned, and the library says how many tracks: short of the
            # tracklist is incomplete.
            total = tracklists.get(item["mbid"])
            if total is None:
                if wanted is not None:
                    wanted.add(item["mbid"])
            elif total and have_tracks < total:
                yield ("incomplete", (
                    "incomplete", db.match_key(artist["name"]), db.match_key(title),
                    aid, artist["name"], title, type_key, date, item.get("mbid"),
                    quality_label, secondary, noisy, have_tracks, total,
                ))


_INSERT = (
    "INSERT INTO library_gaps (kind, artist_key, title_key, artist_id, artist, "
    "title, type, release_date, mbid, owned_format, "
    "secondary, is_secondary, have_tracks, total_tracks) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    # Duplicate artist spellings collapse onto one row; the fullest copy of an
    # album is the one that says how incomplete it is.
    "ON CONFLICT(kind, artist_key, title_key) DO UPDATE SET "
    "have_tracks = MAX(COALESCE(library_gaps.have_tracks, 0), "
    "                  COALESCE(excluded.have_tracks, 0))"
)


def rebuild():
    """Recompute the gaps table from the cached discographies.

    Streams one artist at a time: the whole answer never exists in memory at
    once, which is the difference between this and the list it replaced.
    """
    started = time.time()
    read = db.get_connection()
    try:
        owned_by_key, owned_by_mbid, *track_maps = _owned_maps(read)
        tracklists = _tracklist_sizes()
        wanted = set()
        links_by_artist = db.all_album_links()
        covered_by_artist = _covered_maps() if exclusives.covered_enabled() else {}
        counts = {"missing": 0, "incomplete": 0}
        # Iterated, not fetchall()'d: the discographies are ~85MB of JSON and
        # only one artist's worth needs to be in memory at a time.
        cursor = read.execute(
            "SELECT a.id, a.name, a.monitor_types, j.payload "
            "FROM artists a JOIN json_cache j ON j.cache_key = 'disco:' || a.mbid "
            "WHERE a.ignored = 0 AND a.track_count > 0"
        )
        with db._write_lock:
            conn = db.get_connection()
            try:
                conn.execute("DELETE FROM library_gaps")
                batch = []
                for artist in cursor:
                    # A compilation credit has no discography worth chasing.
                    if db.is_non_artist(artist["name"]):
                        continue
                    try:
                        items = json.loads(artist["payload"])
                    except (TypeError, ValueError):
                        continue
                    for kind, row in _rows_for(artist, items, owned_by_key,
                                               owned_by_mbid,
                                               covered_by_artist, links_by_artist,
                                               track_maps, tracklists, wanted):
                        counts[kind] += 1
                        batch.append(row)
                    if len(batch) >= 5000:
                        conn.executemany(_INSERT, batch)
                        batch.clear()
                if batch:
                    conn.executemany(_INSERT, batch)
                conn.commit()
            finally:
                conn.close()
    finally:
        read.close()
    db.set_json_cache(_WANTED_KEY, {"mbids": sorted(wanted)})
    db.set_setting("library_gaps_built_at", repr(time.time()))
    with _lock:
        _cache["at"] = time.time()
    return {"missing": counts["missing"], "incomplete": counts["incomplete"],
            "seconds": round(time.time() - started, 1)}


def built_at():
    try:
        return float(db.get_setting("library_gaps_built_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def rebuilding():
    """True while a rebuild is running."""
    with _lock:
        return _cache["rebuilding"]


def rebuild_async(force=False):
    """Kick a rebuild on a thread unless one is already going.

    A full rebuild takes several seconds, which is far too long to hold a page
    request open: callers get whatever the table holds now plus a "rebuilding"
    flag, and the fresh numbers appear on their next poll.
    """
    with _lock:
        if _cache["rebuilding"]:
            return False
        if not force and built_at() and (time.time() - built_at()) <= _TTL:
            return False
        _cache["rebuilding"] = True

    def worker():
        try:
            rebuild()
        except Exception:  # noqa: BLE001 - a bad rebuild must not kill the thread
            pass
        finally:
            with _lock:
                _cache["rebuilding"] = False

    threading.Thread(target=worker, daemon=True, name="library-gaps").start()
    return True


def ensure_fresh(refresh=False):
    """Start a rebuild when asked, or when the table is stale/never built.

    The very first build is synchronous: there is nothing to show yet, and an
    empty page would read as "nothing is missing".
    """
    if built_at() == 0 and not rebuilding():
        return rebuild()
    rebuild_async(force=refresh)
    return None


def counts():
    """How many gaps of each kind are on hand."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT kind, COUNT(*) c FROM library_gaps GROUP BY kind"
        ).fetchall()
    finally:
        conn.close()
    by_kind = {r["kind"]: r["c"] for r in rows}
    return {"missing": by_kind.get("missing", 0),
            "incomplete": by_kind.get("incomplete", 0),
            "computed_at": built_at()}


_SORTS = {
    "artist": "artist COLLATE NOCASE, release_date",
    "newest": "release_date DESC, artist COLLATE NOCASE",
    "oldest": "CASE WHEN release_date = '' THEN '9999' ELSE release_date END, "
              "artist COLLATE NOCASE",
}

# MusicBrainz types that mean "not a new record": these are what turn a
# discography of 40 albums into 300 rows of reissues and live sets.
SECONDARY_NOISE = ("compilation", "live", "remix", "dj-mix", "mixtape/street",
                   "demo", "interview", "audiobook", "spokenword")


def filtered(kind, q="", types=(), sort="artist",
             limit=100, offset=0, refresh=False, include_secondary=False):
    """One page of the gaps table, with the filters the UI offers applied."""
    ensure_fresh(refresh)
    where = ["kind = ?"]
    params = ["incomplete" if kind == "incomplete" else "missing"]
    if q and q.strip():
        # % and _ are wildcards to SQLite; someone searching for "100%" means
        # the characters, so they're escaped rather than honoured.
        where.append("(artist LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\')")
        like = "%" + _like_escape(q.strip()) + "%"
        params += [like, like]
    if types:
        where.append("type IN (" + ",".join("?" for _ in types) + ")")
        params += list(types)
    if not include_secondary:
        # Anything tagged compilation / live / remix is an old record wearing a
        # new release-group, so it's hidden unless asked for.
        where.append("is_secondary = 0")
    clause = " AND ".join(where)
    order = _SORTS.get(sort, _SORTS["artist"])
    conn = db.get_connection()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) c FROM library_gaps WHERE {clause}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM library_gaps WHERE {clause} ORDER BY {order} "
            "LIMIT ? OFFSET ?", params + [limit, offset]
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        date = r["release_date"] or ""
        out.append({
            "artist_id": r["artist_id"],
            "artist": r["artist"],
            "title": r["title"],
            "type": r["type"],
            "release_date": date or None,
            "year": date[:4] or None,
            "mbid": r["mbid"],
            # Derived, not stored -- the URL is a pure function of the id.
            "image_url": musicbrainz.cover_art_url(r["mbid"]) if r["mbid"] else None,
            "secondary": [x for x in (r["secondary"] or "").split(",") if x],
            **({"owned_format": r["owned_format"]} if r["owned_format"] else {}),
            # Incomplete: how short the copy on disk is.
            **({"have_tracks": r["have_tracks"], "total_tracks": r["total_tracks"]}
               if r["total_tracks"] else {}),
        })
    return out, total
