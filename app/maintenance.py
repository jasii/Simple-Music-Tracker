"""Cache maintenance: measure and purge data left behind by unfollowed artists.

Unfollowing keeps an artist's cached data on purpose. Over time that adds up -
discography/album JSON in the DB plus the album-art image files on disk. This
module reports how much of that is "stale" (not tied to a currently-followed
artist) and can purge it to reclaim space.

Stale = anything keyed to an artist whose subscription is not 'subscribed' or
'notify': their discography cache, album-detail caches, tracked releases, and
any artwork files no longer referenced by a followed artist.
"""

import json
import os
import threading
import time

from . import artwork, db

_FOLLOWED = "('subscribed', 'notify')"

# Measuring the caches means reading every json_cache payload length (tens of
# MB) and stat-ing every artwork file, which took ~240ms. The numbers only
# move when something is cached or purged, so a short memo keeps the settings
# page snappy without showing anything meaningfully out of date.
_STATS_TTL = 60.0
_stats_cache = {"at": 0.0, "value": None}
_stats_lock = threading.Lock()


def invalidate_cache_stats():
    with _stats_lock:
        _stats_cache["value"] = None


def _followed_keys(conn):
    """Artists whose cached data is worth keeping.

    Followed artists, plus ignored ones: an ignored artist is one you parked
    deliberately or opened from a suggestion, and the whole point of keeping
    the row is that their page comes back instantly. Unfollowing without
    ignoring is still what marks data as stale.
    """
    rows = conn.execute(
        f"SELECT sort_name, mbid FROM artists "
        f"WHERE subscription IN {_FOLLOWED} OR ignored = 1"
    ).fetchall()
    names = {r["sort_name"] for r in rows}
    mbids = {r["mbid"] for r in rows if r["mbid"]}
    return names, mbids


def _json_is_stale(key, names, mbids):
    """Is this json_cache row tied to an artist that's no longer followed?"""
    if key.startswith("disco:"):
        return key[len("disco:"):] not in mbids
    if key.startswith("album:"):
        artist = key[len("album:"):].split("|", 1)[0]
        return artist not in names
    return False  # unknown cache types are left alone


def _kept_art_keys(conn, names, mbids):
    """sha1 keys of artwork still referenced by a followed artist."""
    urls = set()
    for r in conn.execute(
        f"SELECT image_url FROM artists WHERE subscription IN {_FOLLOWED} AND image_url IS NOT NULL"
    ):
        urls.add(r["image_url"])
    for r in conn.execute(
        "SELECT rel.image_url AS u FROM releases rel JOIN artists a ON a.id = rel.artist_id "
        f"WHERE a.subscription IN {_FOLLOWED} AND rel.image_url IS NOT NULL"
    ):
        urls.add(r["u"])
    for r in conn.execute("SELECT cache_key, payload FROM json_cache"):
        if _json_is_stale(r["cache_key"], names, mbids):
            continue
        try:
            data = json.loads(r["payload"])
        except (TypeError, ValueError):
            continue
        if isinstance(data, list):  # discography: list of release-group items
            for it in data:
                if isinstance(it, dict) and it.get("image_url"):
                    urls.add(it["image_url"])
        elif isinstance(data, dict):
            # album detail -> "image"; resolved-art overrides (artrg:/albumart:)
            # -> "image_url". Keep both so warmed covers survive a purge.
            if data.get("image"):
                urls.add(data["image"])
            if data.get("image_url"):
                urls.add(data["image_url"])
    return {artwork._key(u) for u in urls}


def _art_files():
    """Yield (key, path, size) for each cached artwork file.

    Negative-cache markers (key + '.miss') are reported under their image's
    key, so a marker survives purges exactly as long as its URL is referenced.
    """
    d = artwork.art_dir()
    for name in os.listdir(d):
        if name.endswith(".part"):
            continue
        path = os.path.join(d, name)
        try:
            if os.path.isfile(path):
                yield name.removesuffix(".miss"), path, os.path.getsize(path)
        except OSError:
            continue


def cache_stats(max_age=_STATS_TTL):
    """Return cache sizes split into kept vs. stale (bytes), memoised briefly."""
    with _stats_lock:
        value = _stats_cache["value"]
        if value is not None and (time.time() - _stats_cache["at"]) < max_age:
            return value
    value = _measure_cache()
    with _stats_lock:
        _stats_cache.update(at=time.time(), value=value)
    return value


def _measure_cache():
    conn = db.get_connection()
    try:
        names, mbids = _followed_keys(conn)
        json_total = json_stale = 0
        stale_keys = []
        for r in conn.execute("SELECT cache_key, LENGTH(payload) AS n FROM json_cache"):
            n = r["n"] or 0
            json_total += n
            if _json_is_stale(r["cache_key"], names, mbids):
                json_stale += n
                stale_keys.append(r["cache_key"])
        kept = _kept_art_keys(conn, names, mbids)
        art_total = art_stale = art_stale_files = 0
        for name, _path, size in _art_files():
            art_total += size
            if name not in kept:
                art_stale += size
                art_stale_files += 1
    finally:
        conn.close()

    return {
        "total_bytes": json_total + art_total,
        "stale_bytes": json_stale + art_stale,
        "json_total_bytes": json_total,
        "json_stale_bytes": json_stale,
        "art_total_bytes": art_total,
        "art_stale_bytes": art_stale,
        "stale_json_entries": len(stale_keys),
        "stale_art_files": art_stale_files,
    }


def purge_artist(artist_id):
    """Delete one artist's cached data + orphaned artwork. Returns bytes freed.

    Used to auto-clean right after an unfollow. The artist must already be
    unfollowed so their data counts as stale.
    """
    conn = db.get_connection()
    try:
        a = conn.execute(
            "SELECT mbid, sort_name FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
        if a is None:
            return {"freed_bytes": 0}
        mbid, sort_name = a["mbid"], a["sort_name"]
        keys = []
        json_freed = 0
        for r in conn.execute("SELECT cache_key, LENGTH(payload) AS n FROM json_cache"):
            k = r["cache_key"]
            if (mbid and k == "disco:" + mbid) or (
                k.startswith("album:") and k[len("album:"):].split("|", 1)[0] == (sort_name or "")
            ):
                keys.append(k)
                json_freed += r["n"] or 0
        names, mbids = _followed_keys(conn)
        kept = _kept_art_keys(conn, names, mbids)
        stale_files = [(path, size) for name, path, size in _art_files() if name not in kept]
    finally:
        conn.close()

    art_freed = 0
    for path, size in stale_files:
        try:
            os.remove(path)
            art_freed += size
        except OSError:
            pass

    with db._write_lock:
        conn = db.get_connection()
        try:
            for key in keys:
                conn.execute("DELETE FROM json_cache WHERE cache_key = ?", (key,))
            conn.execute("DELETE FROM releases WHERE artist_id = ?", (artist_id,))
            conn.commit()
        finally:
            conn.close()

    invalidate_cache_stats()
    return {"freed_bytes": json_freed + art_freed}


def purge_stale():
    """Delete stale caches/releases/artwork. Returns counts and bytes freed."""
    conn = db.get_connection()
    try:
        names, mbids = _followed_keys(conn)
        stale_keys = []
        json_freed = 0
        for r in conn.execute("SELECT cache_key, LENGTH(payload) AS n FROM json_cache"):
            if _json_is_stale(r["cache_key"], names, mbids):
                stale_keys.append(r["cache_key"])
                json_freed += r["n"] or 0
        kept = _kept_art_keys(conn, names, mbids)
        stale_files = [(name, path, size) for name, path, size in _art_files()
                       if name not in kept]
    finally:
        conn.close()

    art_freed = 0
    art_removed = 0
    for _name, path, size in stale_files:
        try:
            os.remove(path)
            art_freed += size
            art_removed += 1
        except OSError:
            pass

    with db._write_lock:
        conn = db.get_connection()
        try:
            for key in stale_keys:
                conn.execute("DELETE FROM json_cache WHERE cache_key = ?", (key,))
            cur = conn.execute(
                "DELETE FROM releases WHERE artist_id IN "
                f"(SELECT id FROM artists WHERE subscription NOT IN {_FOLLOWED})"
            )
            releases_removed = cur.rowcount
            conn.commit()
        finally:
            conn.close()

    invalidate_cache_stats()
    return {
        "freed_bytes": json_freed + art_freed,
        "json_entries_removed": len(stale_keys),
        "art_files_removed": art_removed,
        "releases_removed": releases_removed,
    }
