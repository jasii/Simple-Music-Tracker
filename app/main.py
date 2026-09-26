"""Flask application: pages and JSON API for Simple Music Tracker."""

from . import __version__

import gzip
import hashlib
import io
import json
import os
import secrets
import sqlite3
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    request,
    send_file,
)

from . import (
    album as album_detail,
    artcache,
    artreview,
    artistart,
    artwork,
    calendar_feed,
    db,
    downloads,
    duplicates,
    exclusives,
    gaps,
    grabber,
    hype,
    lastfm,
    librarytrack,
    maintenance,
    metadata,
    metascan,
    musicbrainz,
    playable,
    plugins,
    preview,
    quality,
    scanner,
    scans,
    scheduler,
    similar,
    similar_enrich,
    similar_scan,
    tracker,
    videoaudio,
    webhooks,
)
# Importing the plugin packages registers their plugins with the registry above:
# discovery (Last.fm, Metacritic, ...) and library (filesystem, Subsonic, ...).
# discovery is also used directly for its shared item normaliser.
from .plugins import discovery
from .plugins import library  # noqa: F401 - registers library plugins
from .plugins import downloader  # noqa: F401 - registers download clients (and its client lookup)
from .plugins import solver  # noqa: F401 - registers challenge solvers
from .plugins import notifier  # noqa: F401 - registers notification services

app = Flask(__name__)

# Initialise database and start the background scheduler at import time so it
# works under any WSGI server as well as the built-in dev server.
db.init_db()
tracker.start_worker()
scheduler.start()
# Pick a similar-artist library scan (and genre lookup) back up if a restart
# killed one mid-run.
similar_scan.maybe_resume()
similar_enrich.maybe_resume()
# Build the missing/incomplete table in the background if it's stale, so the first
# visit to the Missing page doesn't pay for it.
gaps.rebuild_async()
# Same for artwork: a page of fifty artists shouldn't mean fifty downloads
# happening while someone waits.
artcache.start()

VALID_STATES = {"none", "subscribed", "notify"}

# When this process came up, reported on the settings page.
_STARTED_AT = time.time()


# --- response compression ---------------------------------------------------

# Nothing sits in front of the app (gunicorn serves it directly), so JSON is
# compressed here or not at all. The artist list is ~2MB uncompressed and ~190KB
# gzipped, which dominates the time to paint the Artists/Following pages.
# Level 1 is deliberate: it gets within a few percent of level 6 on this kind of
# repetitive JSON for a quarter of the CPU.
_GZIP_LEVEL = 1
_GZIP_MIN_BYTES = 1024
_GZIP_TYPES = {"application/json", "application/javascript", "text/javascript",
               "image/svg+xml"}


def _compressible(resp):
    mimetype = resp.mimetype or ""
    return mimetype.startswith("text/") or mimetype in _GZIP_TYPES


@app.after_request
def _gzip_response(resp):
    """Gzip text/JSON responses the client accepts compressed."""
    if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
        return resp
    if resp.status_code < 200 or resp.status_code >= 300 or resp.headers.get("Content-Encoding"):
        return resp
    if not _compressible(resp):
        return resp
    # send_file streams by default; reading it in is fine for the SPA bundle and
    # is what lets it be compressed at all.
    if resp.direct_passthrough:
        if (resp.content_length or 0) > 8 * 1024 * 1024:
            return resp
        resp.direct_passthrough = False
    data = resp.get_data()
    if len(data) < _GZIP_MIN_BYTES:
        return resp
    resp.set_data(gzip.compress(data, _GZIP_LEVEL))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Length"] = str(len(resp.get_data()))
    resp.headers.add("Vary", "Accept-Encoding")
    return resp


@app.after_request
def _static_cache_headers(resp):
    """Cache Vite's hashed bundles forever; never cache the SPA shell.

    Asset filenames carry a content hash, so a new build means a new URL --
    while index.html must be re-checked every load or it would keep pointing at
    the bundle from the previous build.
    """
    path = request.path
    if path.startswith("/static/spa/assets/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path == "/" or (resp.mimetype == "text/html" and not path.startswith("/api/")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# --- helpers ----------------------------------------------------------------

def _normalize_date(value):
    """Expand a partial 'YYYY' or 'YYYY-MM' date to a comparable full date."""
    if not value:
        return None
    parts = value.split("-")
    year = parts[0]
    month = parts[1] if len(parts) > 1 else "01"
    day = parts[2] if len(parts) > 2 else "01"
    try:
        return date(int(year), int(month), int(day))
    except (ValueError, IndexError):
        return None


WINDOWS = {
    "day": 1,
    "week": 7,
    "next-week": 14,
    "month": 30,
}


def _window_bounds(window):
    """Return (start, end) dates for a named window.

    'next-week' covers days 7-14 from today; the others start today.
    """
    today = date.today()
    if window == "next-week":
        return today + timedelta(days=7), today + timedelta(days=14)
    days = WINDOWS.get(window, 30)
    return today, today + timedelta(days=days)


def _row_to_dict(row):
    return {k: row[k] for k in row.keys()}


# Columns for artist *lists*. Everything except `bio`, which is only rendered on
# the artist page: bios are ~1MB of the 2MB the Artists page used to download.
ARTIST_LIST_COLUMNS = (
    "id, name, sort_name, mbid, lastfm_url, image_url, genres, subscription, "
    "monitor_types, ignored, track_count, disc_albums, disc_eps, disc_singles, "
    "owned_albums, owned_eps, owned_singles, last_checked, created_at"
)


def _apply_art_overrides(items):
    """Swap seeded CAA cover URLs for covers the art resolver already found.

    Release rows store an unverified coverartarchive URL that 404s when the
    release-group has no front image; once the resolver has found a real cover
    (Last.fm) it's remembered per release-group, and using it here saves every
    list view from replaying the 404 + resolve dance.
    """
    overrides = album_detail.release_art_overrides(
        [it.get("mbid") for it in items]
    )
    for it in items:
        img = overrides.get(it.get("mbid"))
        if img:
            it["image_url"] = img
    return items


def _query_upcoming(window="month", include_past=False):
    """Return upcoming releases joined with artist info for a window."""
    start, end = _window_bounds(window)
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT r.*, a.name AS artist_name, a.id AS artist_id, "
            "a.subscription AS subscription "
            "FROM releases r JOIN artists a ON a.id = r.artist_id "
            "WHERE a.subscription IN ('subscribed', 'notify') "
            "ORDER BY r.release_date"
        ).fetchall()
    finally:
        conn.close()

    today = date.today()
    results = []
    for row in rows:
        normalized = _normalize_date(row["release_date"])
        if normalized is None:
            continue
        if not include_past and normalized < today:
            continue
        if normalized < start or normalized > end:
            continue
        item = _row_to_dict(row)
        item["normalized_date"] = normalized.isoformat()
        item["days_until"] = (normalized - today).days
        results.append(item)
    return _apply_art_overrides(results)


# --- navigation -------------------------------------------------------------

# key -> (endpoint, label). The key is also the value stored in settings.
PAGE_DEFS = {
    "artists": ("artists_page", "Artists"),
    "missing": ("missing_page", "Missing"),
    "following": ("subscriptions_page", "Following"),
    "upcoming": ("upcoming_page", "Upcoming"),
    "discover": ("discover_page", "Discover"),
    "ignored": ("ignored_page", "Ignored"),
    "settings": ("settings_page", "Settings"),
}
PAGE_KEYS = list(PAGE_DEFS)
DEFAULT_HOME = "upcoming"


def _ordered_subset(value, keys):
    """Return *value* (a comma string) ordered to valid *keys*, all present."""
    ordered = []
    for key in (value or "").split(","):
        key = key.strip()
        if key in keys and key not in ordered:
            ordered.append(key)
    for key in keys:
        if key not in ordered:
            ordered.append(key)
    return ordered


def normalize_nav_order(value):
    """Return a valid, de-duplicated page order with every page present."""
    return _ordered_subset(value, PAGE_KEYS)


# --- pages ------------------------------------------------------------------

# Pages are served by the React single-page app (see serve_spa below); the nav
# config those tabs need is exposed at /api/nav, and the routes that used to
# render Jinja templates have moved to the client (React Router).


@app.route("/art")
def art_proxy():
    """Serve cached album art from disk, fetching + saving it on first request.

    Falls back to redirecting to the source URL only when the bytes aren't on
    disk and can't be downloaded.
    """
    url = request.args.get("u")
    # Only real http(s) images: the fallback below redirects the browser at
    # this value, and "notaurl" is not somewhere to send anyone.
    if not url or not url.lower().startswith(("http://", "https://")):
        abort(404)
    path = artwork.cached_path(url)
    # Only download + save new images when caching is enabled; otherwise serve
    # anything already cached and fall back to the remote URL for the rest.
    if not path and (db.get_setting("cache_images") or "true") != "false":
        path = artwork.fetch(url)
        # A confirmed miss (CAA has no front image for this release-group)
        # 404s immediately: redirecting the browser to the dead URL would make
        # it wait out the same slow upstream 404 a second time.
        if not path and artwork.negative_cached(url):
            return "", 404, {"Cache-Control": "public, max-age=86400"}
    if path:
        resp = send_file(path, mimetype=artwork.content_type(path))
        # Files are keyed by the URL's hash, so the bytes for a given /art?u=
        # never change -- safe to cache forever.
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp
    return redirect(url)


@app.route("/api/album-art")
def api_album_art():
    """Resolve a working cover-art URL for a release (Last.fm, CAA fallback).

    Used by the discography to recover real art when its seeded CAA URL 404s.
    Params: artist, title, mbid?.
    """
    artist = (request.args.get("artist") or "").strip()
    title = (request.args.get("title") or "").strip()
    if not artist or not title:
        return jsonify({"error": "artist and title are required"}), 400
    mbid = (request.args.get("mbid") or "").strip() or None
    return jsonify(album_detail.resolve_album_art(artist, title, mbid))


# How many of an album's tracks may be called hot, and how close to the
# album's best a track has to be to earn it. Without both, an album of evenly
# played tracks would light up end to end and say nothing.
_HOT_TRACK_LIMIT = 3
_HOT_TRACK_SHARE = 0.5


def _mark_hot_tracks(artist, tracks):
    """Flag the tracks Last.fm's listeners actually play.

    Matched against the artist's top tracks, which are already cached for a
    fortnight, so an album page costs no extra API call after the first.
    """
    plays = {
        db.match_key(t["name"]): t["playcount"]
        for t in lastfm.top_tracks(artist, limit=50)
        if t.get("playcount")
    }
    for track in tracks:
        track["playcount"] = plays.get(db.match_key(track.get("name")))
        track["hot"] = False
    if not plays:
        return tracks
    ranked = sorted(
        (t for t in tracks if t.get("playcount")),
        key=lambda t: t["playcount"],
        reverse=True,
    )
    if not ranked:
        return tracks
    best = ranked[0]["playcount"]
    for track in ranked[:_HOT_TRACK_LIMIT]:
        if track["playcount"] >= best * _HOT_TRACK_SHARE:
            track["hot"] = True
    return tracks


def _release_types(artist_mbid, album_mbid, title):
    """(primary_type, secondary_types) for one release, from what's cached.

    Read out of the artist's stored discography only: an album page shouldn't
    wait on a rate-limited walk of MusicBrainz just to print "EP".
    """
    items = musicbrainz.cached_discography(artist_mbid) or []
    if not items:
        return None, []
    want_title = db.match_key(title)
    for item in items:
        if album_mbid and item.get("mbid") == album_mbid:
            return item.get("primary_type"), item.get("secondary_types") or []
    for item in items:
        if db.match_key(item.get("title")) == want_title:
            return item.get("primary_type"), item.get("secondary_types") or []
    return None, []


def _describe_release(data, row, artist, title):
    """Add the release type, and mark tracks no album of this artist carries.

    The unique marks reuse the per-artist pass behind the EP/single tags (see
    app/exclusives); an album's own tracks are album tracks, so only EPs and
    singles get marked.
    """
    album_mbid = (request.args.get("mbid") or "").strip() or data.get("mbid")
    primary, secondary = _release_types(row["mbid"] if row else None, album_mbid, title)
    data["primary_type"] = primary
    data["secondary_types"] = secondary
    data["unique_ready"] = False
    data["owned_covered"] = False
    # Opt-in: every song here is already on a record in the library, so count
    # it as owned (see the own_covered_releases setting).
    if row and not data.get("owned") and exclusives.covered_enabled():
        key = album_mbid or ("t:" + exclusives.track_key(title))
        if key in exclusives.covered_keys(row["id"]):
            data["owned"] = True
            data["owned_covered"] = True
    if not row or primary == "Album":
        return
    if (db.get_setting("show_unique_tags") or "true") == "false":
        return
    # Asked of the library directly, one search per track (cached, and run a
    # few at a time), so the marks are right on the first render instead of
    # waiting for the per-artist pass.
    tracks = data.get("tracks") or []
    marks = librarytrack.markers(artist, [t.get("name") for t in tracks])
    # The artist's album songs, from the per-artist pass, so a track can also
    # say "this one is on no album of theirs".
    album_keys = exclusives.album_track_keys(row["id"])
    data["unique_ready"] = True
    data["album_songs_ready"] = bool(album_keys)
    for track in tracks:
        track["unique"] = not (marks.get(track.get("name")) or {}).get("key")
        if album_keys:
            track["album_exclusive"] = (
                exclusives.track_key(track.get("name")) not in album_keys
            )
    # Still kick the per-artist pass (it feeds the discography tags), on the
    # stored discography only: an album page must not wait out a MusicBrainz
    # walk to do it.
    exclusives.status(row["id"], fetch=False)


@app.route("/api/album")
def api_album():
    """Tracklist + previews for a release. Params: artist, title, mbid?, refresh?."""
    artist = (request.args.get("artist") or "").strip()
    title = (request.args.get("title") or "").strip()
    if not artist or not title:
        return jsonify({"error": "artist and title are required"}), 400
    # The tracklist lookup and the artist's top tracks (which mark the hot
    # ones below) are independent third-party calls, so the top tracks are
    # warmed alongside rather than after: a first view of an album waits for
    # the slower of the two, not the sum.
    with ThreadPoolExecutor(max_workers=2) as pool:
        hot_job = pool.submit(lastfm.top_tracks, artist, 50)
        detail_job = pool.submit(
            album_detail.get_album_detail, artist, title,
            mbid=(request.args.get("mbid") or "").strip() or None,
            force=request.args.get("refresh") == "1",
        )
        data = dict(detail_job.result())
        try:
            hot_job.result()
        except Exception:  # noqa: BLE001 - hot marks are decoration
            pass
    # Live (uncached) library status for this artist, so the page can show a
    # follow toggle and link the name to the artist page when it's tracked.
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT id, subscription, mbid FROM artists WHERE sort_name = ?",
            (artist.lower(),),
        ).fetchone()
        owned = False
        owned_format = None
        owned_formats = []
        owned_tracks = 0
        if row:
            mbid = (request.args.get("mbid") or "").strip() or data.get("mbid")
            # The same matcher the discography uses: exact title, looser
            # title, the release's own id, or a manual link.
            owned_rows, links = db.owned_index(row["id"])
            matched_keys, sources, formats = db.match_owned(owned_rows, links, mbid, title)
            owned = bool(sources)
            owned_tracks = db.owned_track_count(owned_rows, matched_keys)
            # Several sources may hold it in different qualities; show them all.
            owned_formats = gaps.ranked_formats(formats)
            owned_format = owned_formats[0] if owned_formats else None
    finally:
        conn.close()
    data["artist_id"] = row["id"] if row else None
    data["following"] = bool(row and row["subscription"] in ("subscribed", "notify"))
    data["owned"] = owned
    data["owned_format"] = owned_format
    data["owned_formats"] = owned_formats
    # How many of its tracks the fullest library copy has (0 = unknown), so
    # the page can say "7 of 9" and offer to fetch the rest.
    data["owned_tracks"] = owned_tracks
    # ...and how many a complete copy has: the shortest official edition, so
    # a standard-edition rip isn't short of the Japanese bonus track.
    rg = (request.args.get("mbid") or "").strip() or data.get("mbid")
    data["complete_tracks"] = (musicbrainz.min_track_count(rg) or None) if owned_tracks and rg else None
    job = downloads.latest_for(artist, title)
    data["download"] = downloads.describe(job) if job else None
    data["tracks"] = _mark_hot_tracks(artist, data.get("tracks") or [])
    _describe_release(data, row, artist, title)
    return jsonify(data)


# --- JSON API ---------------------------------------------------------------

@app.route("/api/stats")
def api_stats():
    conn = db.get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) c FROM artists").fetchone()["c"]
        ignored = conn.execute(
            "SELECT COUNT(*) c FROM artists WHERE ignored = 1"
        ).fetchone()["c"]
        subscribed = conn.execute(
            "SELECT COUNT(*) c FROM artists WHERE subscription = 'subscribed'"
        ).fetchone()["c"]
        notify = conn.execute(
            "SELECT COUNT(*) c FROM artists WHERE subscription = 'notify'"
        ).fetchone()["c"]
        releases = conn.execute("SELECT COUNT(*) c FROM releases").fetchone()["c"]
    finally:
        conn.close()
    return jsonify(
        {
            "artists": total,
            "visible": total - ignored,
            "ignored": ignored,
            "subscribed": subscribed,
            "notify": notify,
            "following": subscribed + notify,
            "tracked_releases": releases,
            "upcoming_week": len(_query_upcoming("week")),
            "upcoming_month": len(_query_upcoming("month")),
        }
    )


@app.route("/api/artists")
def api_artists():
    """List artists with optional search/filter/pagination.

    Query params: q, subscription (none|subscribed|notify|following),
    limit, offset, sort (name|tracks|recent).
    """
    q = (request.args.get("q") or "").strip().lower()
    subscription = request.args.get("subscription") or ""
    sort = request.args.get("sort") or "name"
    # 'ignored': "0" (default, hide ignored), "1" (only ignored), "all" (both).
    ignored = request.args.get("ignored", "0")
    try:
        limit = min(int(request.args.get("limit", 5000)), 10000)
    except ValueError:
        limit = 5000
    try:
        offset = int(request.args.get("offset", 0))
    except ValueError:
        offset = 0

    where = []
    params = []
    if q:
        where.append("LOWER(name) LIKE ?")
        params.append(f"%{q}%")
    if subscription == "following":
        where.append("subscription IN ('subscribed', 'notify')")
    elif subscription in VALID_STATES:
        where.append("subscription = ?")
        params.append(subscription)
    if ignored == "1":
        where.append("ignored = 1")
    elif ignored != "all":
        where.append("ignored = 0")

    order = {
        "tracks": "track_count DESC, sort_name",
        "recent": "last_checked DESC, sort_name",
        "name": "sort_name",
    }.get(sort, "sort_name")

    sql = f"SELECT {ARTIST_LIST_COLUMNS} FROM artists"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = db.get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        count_sql = "SELECT COUNT(*) c FROM artists"
        if where:
            count_sql += " WHERE " + " AND ".join(where)
        total = conn.execute(count_sql, params[:-2]).fetchone()["c"]
    finally:
        conn.close()

    return jsonify(
        {
            "total": total,
            "count": len(rows),
            "artists": [_row_to_dict(r) for r in rows],
        }
    )


@app.route("/api/artists/<int:artist_id>")
def api_artist(artist_id):
    conn = db.get_connection()
    try:
        artist = conn.execute(
            "SELECT * FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
        if artist is None:
            abort(404)
        releases = conn.execute(
            "SELECT * FROM releases WHERE artist_id = ? ORDER BY release_date DESC",
            (artist_id,),
        ).fetchall()
    finally:
        conn.close()
    data = _row_to_dict(artist)
    # Strip Last.fm's trailing "Read more" link from bios stored before that fix.
    data["bio"] = lastfm.clean_bio(data.get("bio"))
    data["releases"] = [_row_to_dict(r) for r in releases]
    return jsonify(data)


def _purge_on_unfollow(artist_ids):
    """When enabled, drop cached data for artists that were just unfollowed."""
    if (db.get_setting("purge_cache_on_unfollow") or "true") == "false":
        return
    for aid in artist_ids:
        try:
            maintenance.purge_artist(aid)
        except Exception:  # noqa: BLE001 - cleanup must never break the request
            pass


@app.route("/api/artists/<int:artist_id>/subscription", methods=["POST"])
def api_set_subscription(artist_id):
    payload = request.get_json(silent=True) or {}
    state = payload.get("state") or request.form.get("state")
    if state not in VALID_STATES:
        return jsonify({"error": "invalid state"}), 400

    with db._write_lock:
        conn = db.get_connection()
        try:
            # Following un-ignores: an artist can't be both in your library and
            # hidden from it, and suggestions you open arrive ignored.
            cur = conn.execute(
                "UPDATE artists SET subscription = ?, "
                "ignored = CASE WHEN ? = 'none' THEN ignored ELSE 0 END "
                "WHERE id = ?",
                (state, state, artist_id),
            )
            conn.commit()
        finally:
            conn.close()
        if cur.rowcount == 0:
            return jsonify({"error": "artist not found"}), 404

    # Newly following an artist? Kick off a metadata fetch in the background.
    if state in ("subscribed", "notify"):
        tracker.enqueue_artist(artist_id)
    elif state == "none":
        _purge_on_unfollow([artist_id])

    return jsonify({"id": artist_id, "subscription": state})


@app.route("/api/artists/<int:artist_id>/monitor-types", methods=["POST"])
def api_set_monitor_types(artist_id):
    """Set which release types to watch for an artist.

    Body: {"types": ["album", "ep", "single"]} (any subset, including none).
    Releases of types no longer monitored are dropped, then a refresh is queued.
    """
    payload = request.get_json(silent=True) or {}
    types = payload.get("types")
    if types is None:
        types = request.form.getlist("types")
    # Allow an empty selection here (unlike new-artist defaults) so the user can
    # turn off monitoring entirely for an artist.
    kept = db.clean_types(types)
    monitor_types = ",".join(kept)

    with db._write_lock:
        conn = db.get_connection()
        try:
            cur = conn.execute(
                "UPDATE artists SET monitor_types = ? WHERE id = ?",
                (monitor_types, artist_id),
            )
            if cur.rowcount:
                # Drop stored releases whose type is no longer monitored.
                labels = [t.capitalize() if t != "ep" else "EP" for t in kept]
                if labels:
                    placeholders = ",".join("?" for _ in labels)
                    conn.execute(
                        f"DELETE FROM releases WHERE artist_id = ? "
                        f"AND primary_type NOT IN ({placeholders})",
                        [artist_id, *labels],
                    )
                else:
                    conn.execute("DELETE FROM releases WHERE artist_id = ?", (artist_id,))
            conn.commit()
        finally:
            conn.close()
        if cur.rowcount == 0:
            return jsonify({"error": "artist not found"}), 404

    tracker.enqueue_artist(artist_id)
    return jsonify({"id": artist_id, "monitor_types": kept})


@app.route("/api/artists/<int:artist_id>/mbid", methods=["POST"])
def api_set_mbid(artist_id):
    """Match an existing library artist to a MusicBrainz artist URL/ID.

    Body: {"link": "https://musicbrainz.org/artist/<mbid>"}. Sets the artist's
    MBID, clears stale stored releases, and queues a refresh with the new id.
    """
    payload = request.get_json(silent=True) or {}
    link = payload.get("link") or payload.get("mbid") or ""
    mbid = musicbrainz.extract_mbid(link)
    if not mbid:
        return jsonify({"error": "no MusicBrainz artist id found in that link"}), 400

    info = musicbrainz.lookup_artist(mbid)
    if not info:
        return jsonify({"error": "artist not found on MusicBrainz"}), 404

    with db._write_lock:
        conn = db.get_connection()
        try:
            cur = conn.execute(
                "UPDATE artists SET mbid = ? WHERE id = ?", (mbid, artist_id)
            )
            # Drop releases gathered under the old identity so they re-fetch.
            conn.execute("DELETE FROM releases WHERE artist_id = ?", (artist_id,))
            conn.commit()
        finally:
            conn.close()
        if cur.rowcount == 0:
            return jsonify({"error": "artist not found"}), 404

    tracker.enqueue_artist(artist_id)
    return jsonify({"id": artist_id, "mbid": mbid, "matched_name": info["name"]})


@app.route("/api/artists/<int:artist_id>/artwork")
def api_artist_artwork(artist_id):
    """Every image the app can offer for this artist, and which one is in use.

    ``?refresh=1`` asks the sources again instead of answering from the cache.
    """
    refresh = request.args.get("refresh") in ("1", "true", "yes")
    found = artistart.options(artist_id, refresh=refresh)
    if found is None:
        return jsonify({"error": "artist not found"}), 404
    return jsonify(found)


@app.route("/api/artists/<int:artist_id>/artwork", methods=["POST"])
def api_set_artist_artwork(artist_id):
    """Use one image as this artist's artwork, or go back to automatic.

    Body: {"url": "https://..."} to pick one (it is then left alone by every
    refresh), or {"url": null} to clear the choice and let the backfill pick.
    """
    payload = request.get_json(silent=True) or {}
    try:
        chosen = artistart.pick(artist_id, payload.get("url"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    # Pull the bytes into the local art cache now, so the page shows it from
    # disk and a source that later rots doesn't take the picture with it.
    if chosen:
        tracker.warm_art_async([chosen])
    return jsonify({"id": artist_id, "image_url": chosen, "locked": bool(chosen)})


@app.route("/api/artists/duplicates")
def api_artist_duplicates():
    """Artists that look like the same person, with a suggested merge target.

    ``?dismissed=1`` includes the ones already waved away.
    """
    include = request.args.get("dismissed") == "1"
    return jsonify({
        "groups": duplicates.suggestions(include_dismissed=include),
        "dismissed": duplicates.dismissed_count(),
    })


@app.route("/api/artists/duplicates/dismiss", methods=["POST"])
def api_artist_duplicates_dismiss():
    """Remember that a suggested group is not one artist.

    Body: {"key": "...", "signature": "..."} to dismiss, plus "undo": true to
    bring it back, or {"all": true} with undo to clear every dismissal.
    """
    payload = request.get_json(silent=True) or {}
    key = (payload.get("key") or "").strip()
    if payload.get("undo"):
        duplicates.restore(None if payload.get("all") else key)
        return jsonify({"restored": key or "all"})
    signature = (payload.get("signature") or "").strip()
    if not key or not signature:
        return jsonify({"error": "key and signature are required"}), 400
    duplicates.dismiss(key, signature)
    return jsonify({"dismissed": key})


@app.route("/api/artists/<int:artist_id>/merge", methods=["POST"])
def api_merge_artists(artist_id):
    """Merge one or more source artists into this (target) artist.

    Body: {"source_ids": [..], "name": "<optional chosen name>"}. Releases and
    track counts move to the target, which keeps its subscription, monitor types
    and ignore state; the resulting name is the target's unless *name* is given.
    Source artists are then deleted.
    """
    payload = request.get_json(silent=True) or {}
    source_ids = [int(i) for i in (payload.get("source_ids") or []) if str(i).isdigit()]
    source_ids = [i for i in source_ids if i != artist_id]
    chosen_name = (payload.get("name") or "").strip()
    if not source_ids:
        return jsonify({"error": "no source artists to merge"}), 400

    with db._write_lock:
        conn = db.get_connection()
        try:
            target = conn.execute(
                "SELECT * FROM artists WHERE id = ?", (artist_id,)
            ).fetchone()
            if target is None:
                return jsonify({"error": "target artist not found"}), 404

            merged = 0
            target_mbid = target["mbid"]
            for sid in source_ids:
                source = conn.execute(
                    "SELECT * FROM artists WHERE id = ?", (sid,)
                ).fetchone()
                if source is None:
                    continue
                # Move releases; UPDATE OR IGNORE leaves duplicates (same mbid)
                # behind on the source, to be removed with it below.
                conn.execute(
                    "UPDATE OR IGNORE releases SET artist_id = ? WHERE artist_id = ?",
                    (artist_id, sid),
                )
                # Move owned albums + remembered folders to the target so the
                # merged-in records (and future rescans) follow the artist;
                # OR IGNORE drops rows the target already has, the leftover dupes
                # go with the source DELETE below.
                conn.execute(
                    "UPDATE OR IGNORE owned_albums SET artist_id = ? WHERE artist_id = ?",
                    (artist_id, sid),
                )
                conn.execute("DELETE FROM owned_albums WHERE artist_id = ?", (sid,))
                conn.execute(
                    "UPDATE OR IGNORE artist_folders SET artist_id = ? WHERE artist_id = ?",
                    (artist_id, sid),
                )
                conn.execute("DELETE FROM artist_folders WHERE artist_id = ?", (sid,))
                # Fold each source per-library track count into the target's, then
                # recompute below; clears the source's stats rows too.
                for srow in conn.execute(
                    "SELECT source, track_count FROM artist_library_stats WHERE artist_id = ?",
                    (sid,),
                ).fetchall():
                    db.set_library_stat(conn, artist_id, srow["source"], srow["track_count"], increment=True)
                conn.execute("DELETE FROM artist_library_stats WHERE artist_id = ?", (sid,))
                if not target_mbid and source["mbid"]:
                    target_mbid = source["mbid"]
                conn.execute("DELETE FROM artists WHERE id = ?", (sid,))
                merged += 1

            # Track count is the sum of the (now-merged) per-library stats.
            db.recompute_track_count(conn, artist_id)

            if target_mbid and target_mbid != target["mbid"]:
                conn.execute(
                    "UPDATE artists SET mbid = ? WHERE id = ?",
                    (target_mbid, artist_id),
                )
            # Apply the chosen display name (sources are gone, so the only
            # possible sort_name clash is a different artist -- ignore if so).
            if chosen_name and chosen_name != target["name"]:
                try:
                    conn.execute(
                        "UPDATE artists SET name = ?, sort_name = ? WHERE id = ?",
                        (chosen_name, chosen_name.lower(), artist_id),
                    )
                except sqlite3.IntegrityError:
                    pass
            conn.commit()

            final = conn.execute(
                "SELECT name FROM artists WHERE id = ?", (artist_id,)
            ).fetchone()
        finally:
            conn.close()

    return jsonify({"id": artist_id, "merged": merged, "name": final["name"]})


@app.route("/api/artists/<int:artist_id>/discography")
def api_discography(artist_id):
    """All albums/EPs/singles for an artist from MusicBrainz, fetched on demand.

    Grouped and ordered Albums -> EPs -> Singles (newest first within each).
    Only this route calls MusicBrainz for the full list, so it happens when the
    user opens the artist page -- not during scans or background refreshes.
    """
    conn = db.get_connection()
    try:
        artist = conn.execute(
            "SELECT * FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
    finally:
        conn.close()
    if artist is None:
        abort(404)

    mbid = artist["mbid"]
    try:
        if not mbid:
            mbid = musicbrainz.resolve_mbid(artist["name"])
            if mbid:
                with db._write_lock:
                    conn = db.get_connection()
                    try:
                        conn.execute(
                            "UPDATE artists SET mbid = ? WHERE id = ?",
                            (mbid, artist_id),
                        )
                        conn.commit()
                    finally:
                        conn.close()
        if not mbid:
            return jsonify({"error": "no MusicBrainz match", "mbid": None,
                            "groups": {"album": [], "ep": [], "single": []}})

        items = musicbrainz.fetch_discography(mbid, force=request.args.get("refresh") == "1")
    except Exception as exc:  # noqa: BLE001 - report fetch failures to the UI
        return jsonify({"error": str(exc), "mbid": mbid,
                        "groups": {"album": [], "ep": [], "single": []}}), 502

    # Map a library/source key to a human label ('manual' -> "Manual").
    src_labels = {p.key: p.display_label() for p in plugins.get_plugins("library")}
    src_labels.setdefault("manual", "Manual")
    groups, owned_counts = album_detail.group_discography(artist_id, items, src_labels)

    # Cache the per-type totals + owned subset on the artist so the Artists list
    # can show owned/total ("missing") without re-fetching the discography.
    db.set_discography_counts(artist_id, len(groups["album"]), len(groups["ep"]), len(groups["single"]))
    db.set_owned_counts(artist_id, owned_counts["album"], owned_counts["ep"], owned_counts["single"])

    # Warm EP/single covers in the background: those tabs aren't visible when
    # the artist page opens, so their art would otherwise only start
    # downloading when the tab is clicked. Cached/negative-cached URLs return
    # instantly, so repeat visits cost nothing.
    tracker.warm_art_async(
        [it.get("image_url") for it in groups["ep"] + groups["single"]]
    )

    return jsonify({"mbid": mbid, "groups": groups,
                    "counts": {k: len(v) for k, v in groups.items()}})


def _track_stream_url(artist, title):
    """The in-app URL that plays one track (library copy, else a sample)."""
    return "/api/track-stream?" + urlencode({"artist": artist, "title": title})


@app.route("/api/track-source")
def api_track_source():
    """What to play one track from, without starting to play it.

    Answers with the in-app stream URL when the audio can come from here (a
    library copy, or a thirty-second sample), else the YouTube id Last.fm's own
    player uses -- so a remix no catalogue carries still plays on this page
    rather than sending someone to last.fm. Params: artist, title, url?.
    """
    artist = (request.args.get("artist") or "").strip()
    title = (request.args.get("title") or "").strip()
    if not artist or not title:
        return jsonify({"error": "artist and title are required"}), 400

    return jsonify(playable.resolve_one(
        artist, title, (request.args.get("url") or "").strip() or None
    ))


@app.route("/api/album/playable")
def api_album_playable():
    """Which of a release's tracks can be played, and from where.

    Params: artist, title. The first call for a release starts a background
    pass (a library search, a catalogue lookup and, for what neither has, a
    read of Last.fm's track page) and answers running=True, publishing each
    answer as it lands so the page fills in row by row.
    """
    artist = (request.args.get("artist") or "").strip()
    title = (request.args.get("title") or "").strip()
    if not artist or not title:
        return jsonify({"error": "artist and title are required"}), 400
    detail = album_detail.get_album_detail(
        artist, title, mbid=(request.args.get("mbid") or "").strip() or None
    )
    tracks = [(t.get("name"), t.get("url")) for t in detail.get("tracks") or []]
    return jsonify(playable.status(artist, title, tracks))


@app.route("/api/track-stream")
def api_track_stream():
    """Play one track: the user's own copy if a library has it, else a sample.

    All audio is served from here, so a page never sees a Subsonic token, a
    Plex token or a path on disk, and a library that has since lost the track
    falls back to the thirty-second preview instead of failing.
    """
    artist = (request.args.get("artist") or "").strip()
    title = (request.args.get("title") or "").strip()
    if not artist or not title:
        return jsonify({"error": "artist and title are required"}), 400
    try:
        _plugin, found = librarytrack.locate(artist, title)
        if found:
            return librarytrack.respond(found, request.headers.get("Range"))
    except Exception:  # noqa: BLE001 - a sick library shouldn't kill playback
        pass
    sample = preview.for_track(artist, title)
    if sample:
        return redirect(sample)
    # Nothing sells it: play the audio out of the video a source found, so it
    # goes through the same player as everything else (see app/videoaudio.py).
    found = playable.resolve_one(artist, title)
    if found.get("youtube_id"):
        # Its own proxy rather than the library one: the CDN only serves this
        # in pieces (see app/videoaudio.py).
        streamed = videoaudio.respond(found["youtube_id"],
                                      request.headers.get("Range"))
        if streamed is not None:
            return streamed
    return jsonify({"error": "no audio for that track"}), 404


@app.route("/api/artists/<int:artist_id>/album-links")
def api_album_links(artist_id):
    """What the library holds for this artist, and what it's matched to.

    Feeds the match dialog: every library album with the release group it
    currently counts as (by title, by id, or because it was linked by hand),
    plus every release group in the discography and which album answered for
    it. Read-only, and no network: the discography comes from what's stored.
    """
    conn = db.get_connection()
    try:
        artist = conn.execute(
            "SELECT id, name, mbid FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
    finally:
        conn.close()
    if artist is None:
        return jsonify({"error": "artist not found"}), 404

    owned_rows, links = db.owned_index(artist_id)
    alt_index = db.alt_key_index(owned_rows)
    src_labels = {p.key: p.display_label() for p in plugins.get_plugins("library")}
    src_labels.setdefault("manual", "Manual")
    items = musicbrainz.cached_discography(artist["mbid"]) or []

    releases = []
    matched_by_album = {}
    for item in items:
        keys, sources, formats = db.match_owned(
            owned_rows, links, item.get("mbid"), item.get("title"), alt_index
        )
        best = None
        for label in formats:
            best = gaps.better_quality(best, label)
        for key in keys:
            matched_by_album.setdefault(key, []).append(item.get("title"))
        releases.append({
            "mbid": item.get("mbid"),
            "title": item.get("title"),
            "type": item.get("primary_type"),
            "release_date": item.get("release_date"),
            "owned": bool(sources),
            "matched_albums": sorted(keys),
            "format": best,
            "linked": any(
                links.get((key, item.get("mbid"))) == 1 for key in keys
            ),
        })

    # Named for what it is, not "library": the module of that name is imported
    # above, and shadowing it inside a route is how a later edit breaks.
    owned = []
    for key, entry in sorted(owned_rows.items()):
        best = None
        for label in entry["formats"]:
            best = gaps.better_quality(best, label)
        owned.append({
            "album_key": key,
            "sources": sorted(src_labels.get(s, s) for s in entry["sources"]),
            "format": best,
            "matched_releases": sorted(matched_by_album.get(key, [])),
            "links": [
                {"rg_mbid": rg, "linked": bool(state)}
                for (album_key, rg), state in sorted(links.items())
                if album_key == key
            ],
        })
    return jsonify({"artist": artist["name"], "library": owned,
                    "releases": releases})


@app.route("/api/artists/<int:artist_id>/album-links", methods=["POST"])
def api_set_album_link(artist_id):
    """Connect a library album to a release group, or disconnect the two.

    Body: {"album_key": ..., "rg_mbid": ..., "linked": true|false|null}.
    True says they're the same record (which beats every title rule), false
    says they aren't (which blocks the loose title match), null forgets the
    instruction and goes back to matching by name.
    """
    payload = request.get_json(silent=True) or {}
    album_key = (payload.get("album_key") or "").strip().lower()
    rg_mbid = (payload.get("rg_mbid") or "").strip()
    if not album_key or not rg_mbid:
        return jsonify({"error": "album_key and rg_mbid are required"}), 400
    linked = payload.get("linked", True)
    if linked is not None:
        linked = bool(linked)
    db.set_album_link(artist_id, album_key, rg_mbid, linked)
    # Ownership just changed, so the missing/incomplete table is stale.
    gaps.invalidate()
    return jsonify({"album_key": album_key, "rg_mbid": rg_mbid, "linked": linked})


@app.route("/api/artists/<int:artist_id>/exclusives")
def api_artist_exclusives(artist_id):
    """How many songs each of this artist's EPs/singles keeps off the albums.

    Tracklists are read one release at a time, so the first call for an artist
    starts a background pass and answers running=True; the page polls. Keyed to
    the artist's discography, so it only ever runs again when that changes.
    """
    state = exclusives.status(artist_id)
    if state is None:
        return jsonify({"error": "artist not found"}), 404
    return jsonify(state)


@app.route("/api/artists/<int:artist_id>/top-tracks")
def api_artist_top_tracks(artist_id):
    """The artist's best-known tracks, each with a 30-second sample if one exists.

    Names and playcounts come from Last.fm; the sample URLs from the keyless
    catalogues (see app/preview.py). Both are stored, so this is one round trip
    per artist ever -- the first open pays for the lookups, later ones don't.
    """
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT name FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return jsonify({"error": "artist not found"}), 404
    try:
        limit = max(1, min(int(request.args.get("limit") or 5), 10))
    except (TypeError, ValueError):
        limit = 5
    tracks = lastfm.top_tracks(row["name"], limit=limit)
    # The user's own copy beats a thirty-second sample, so ask the libraries
    # first and only look a sample up for what they haven't got.
    owned = librarytrack.markers(row["name"], [t["name"] for t in tracks])
    samples = preview.for_tracks(
        row["name"], [t["name"] for t in tracks if not owned.get(t["name"])]
    )
    for track in tracks:
        mark = owned.get(track["name"]) or {}
        track["preview"] = samples.get(track["name"])
        track["library"] = mark.get("label")
        # Where that copy lives, so the player can credit it with a link.
        track["library_url"] = mark.get("url")
        track["library_icon"] = mark.get("icon")
        if not mark and track["preview"]:
            # Not ours: say which catalogue the sample came from instead.
            sample = preview.cached_details(row["name"], track["name"]) or {}
            track["library"] = sample.get("label")
            track["library_url"] = sample.get("page")
            track["library_icon"] = sample.get("icon")
        track["album"] = mark.get("album")
        track["full"] = bool(mark)
        track["duration"] = mark.get("duration")
        track["stream"] = (
            _track_stream_url(row["name"], track["name"])
            if mark or track["preview"]
            else None
        )
    return jsonify({"artist": row["name"], "tracks": tracks})


def _artist_ids(payload):
    """The ids a bulk action was given, as ints, or None when malformed."""
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        return None
    out = []
    for value in ids:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            return None
    return out


@app.route("/api/artists/refresh", methods=["POST"])
def api_refresh_artists():
    """Queue a metadata refresh for the artists named. Body: {"ids": [...]}.

    The same work the per-artist Refresh does, for a selection: each one is
    queued and the worker gets through them at its own pace.
    """
    ids = _artist_ids(request.get_json(silent=True) or {})
    if ids is None:
        return jsonify({"error": "ids must be a non-empty list of artist ids"}), 400
    queued = [aid for aid in ids if tracker.enqueue_artist(aid) is not False]
    return jsonify({"queued": len(queued), "ids": queued})


@app.route("/api/artists/scan", methods=["POST"])
def api_scan_artists():
    """Rescan what the libraries hold for the artists named.

    Body: {"ids": [...]}. Runs inline, one after another: each is scoped to
    that artist's own folders, so a handful is quick.
    """
    ids = _artist_ids(request.get_json(silent=True) or {})
    if ids is None:
        return jsonify({"error": "ids must be a non-empty list of artist ids"}), 400
    done, failed = [], []
    for aid in ids:
        try:
            summary = scanner.scan_artist(aid)
        except Exception as exc:  # noqa: BLE001 - one bad artist isn't the run
            summary = {"error": str(exc)}
        (failed if summary.get("error") else done).append(aid)
    return jsonify({"scanned": len(done), "failed": len(failed)})


@app.route("/api/artists/<int:artist_id>/scan", methods=["POST"])
def api_scan_artist(artist_id):
    """Fast rescan of one artist: walk only their known/matching folders, then
    update track count + owned albums. Runs inline (scoped, so quick)."""
    summary = scanner.scan_artist(artist_id)
    if summary.get("error"):
        return jsonify(summary), 404
    return jsonify(summary)


@app.route("/api/artists/<int:artist_id>/albums/owned", methods=["POST"])
def api_set_album_owned(artist_id):
    """Manually mark/unmark an album as owned.

    Body: {"title": "...", "owned": true/false, "mbid": "<release-group id>"?}.
    """
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    owned = bool(payload.get("owned"))
    rg_mbid = (payload.get("mbid") or "").strip() or None
    db.set_owned(artist_id, title, owned, rg_mbid)
    gaps.invalidate()
    return jsonify({"title": title, "owned": owned})


@app.route("/api/artists/track-by-name", methods=["POST"])
def api_track_by_name():
    """Start monitoring an artist by name (used by the Discover page).

    Body: {"name": "Artist", "state": "subscribed|notify|none"}. Following states
    create the artist if needed and queue a metadata fetch; "none" unfollows an
    existing artist (and is a no-op if they aren't in the library).
    """
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    state = payload.get("state") or "subscribed"
    if not name:
        return jsonify({"error": "name is required"}), 400
    if state not in ("subscribed", "notify", "none"):
        state = "subscribed"

    monitor_types = db.normalize_monitor_types(db.get_setting("default_monitor_types"))
    with db._write_lock:
        conn = db.get_connection()
        try:
            existing = conn.execute(
                "SELECT id FROM artists WHERE sort_name = ?", (name.lower(),)
            ).fetchone()
            if existing:
                artist_id = existing["id"]
                # Unfollowing leaves the artist (and any ignored flag) alone;
                # following also un-ignores so they show in the library again.
                if state == "none":
                    conn.execute(
                        "UPDATE artists SET subscription = 'none' WHERE id = ?", (artist_id,)
                    )
                else:
                    conn.execute(
                        "UPDATE artists SET subscription = ?, ignored = 0 WHERE id = ?",
                        (state, artist_id),
                    )
                created = False
            elif state == "none":
                return jsonify({"id": None, "name": name, "subscription": "none", "created": False})
            else:
                cur = conn.execute(
                    "INSERT INTO artists (name, sort_name, subscription, "
                    "monitor_types, track_count) VALUES (?, ?, ?, ?, 0)",
                    (name, name.lower(), state, monitor_types),
                )
                artist_id = cur.lastrowid
                created = True
            conn.commit()
        finally:
            conn.close()

    if state != "none":
        tracker.enqueue_artist(artist_id)
    else:
        _purge_on_unfollow([artist_id])
    return jsonify({"id": artist_id, "name": name, "subscription": state, "created": created})


# --- what the library is missing -------------------------------------------

def _library_page(kind):
    """Shared handler for the missing / incomplete lists (same filters, same shape)."""
    args = request.args
    types = [t for t in (args.get("types") or "").split(",") if t]
    try:
        limit = min(int(args.get("limit", 100)), 500)
    except ValueError:
        limit = 100
    try:
        offset = max(int(args.get("offset", 0)), 0)
    except ValueError:
        offset = 0
    rows, total = gaps.filtered(
        kind,
        q=args.get("q", ""),
        types=types,
        sort=args.get("sort") or "artist",
        limit=limit,
        offset=offset,
        refresh=args.get("refresh") == "1",
        include_secondary=args.get("include_secondary") == "1",
    )
    totals = gaps.counts()
    return jsonify({
        "rows": rows,
        "total": total,
        "missing_total": totals["missing"],
        "incomplete_total": totals["incomplete"],
        "computed_at": totals["computed_at"],
        "rebuilding": gaps.rebuilding(),
        "grab_ready": bool(downloader.clients()),
    })


@app.route("/api/library/missing")
def api_library_missing():
    """Release groups the library doesn't own, across every scanned artist.

    Built from cached discographies + owned_albums, so it costs no network
    calls. Params: q, types (album,ep,single), sort, limit, offset, refresh=1.
    """
    return _library_page("missing")


@app.route("/api/library/incomplete")
def api_library_incomplete():
    """Albums the library holds fewer tracks of than MusicBrainz lists."""
    return _library_page("incomplete")


@app.route("/api/library/grab-missing", methods=["POST"])
def api_library_grab_missing():
    """Fetch the tracks a partly-owned album is missing.

    Body: {"artist", "title", "mbid"?, "tracks"?, "downloader"?, "owned_format"?}.
    Without *tracks* the server works out which are missing by asking each
    library for every song on the tracklist.
    """
    payload = request.get_json(silent=True) or {}
    artist = (payload.get("artist") or "").strip()
    title = (payload.get("title") or "").strip()
    if not artist or not title:
        return jsonify({"error": "artist and title are required"}), 400
    tracks = [str(t).strip() for t in payload.get("tracks") or [] if str(t).strip()]
    if not tracks:
        tracks, total = grabber.missing_tracks(
            artist, title, mbid=(payload.get("mbid") or "").strip() or None)
        if not total:
            return jsonify({"error": "no tracklist for this release"}), 404
        if not tracks:
            return jsonify({"error": "every track is in the library already"}), 409
    result = grabber.grab_missing(
        artist, title, tracks,
        downloader_key=(payload.get("downloader") or "").strip() or None,
        owned_format=(payload.get("owned_format") or "").strip() or None,
    )
    if result.get("error"):
        return jsonify(result), 404
    result["tracks"] = tracks
    return jsonify(result)


# --- followed downloads (app/downloads) ---------------------------------------

@app.route("/api/downloads")
def api_downloads():
    """Recent Soulseek download jobs, newest first. Params: active=1, limit."""
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except ValueError:
        limit = 100
    return jsonify({"jobs": downloads.list_jobs(
        limit=limit, active_only=request.args.get("active") == "1")})


def _download_action(job_id, action):
    try:
        job = action(job_id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    if job is None:
        return jsonify({"error": "no such download"}), 404
    return jsonify(job)


@app.route("/api/downloads/<int:job_id>/retry", methods=["POST"])
def api_download_retry(job_id):
    """Ask again for every file that hasn't landed."""
    return _download_action(job_id, downloads.retry)


@app.route("/api/downloads/<int:job_id>/another-peer", methods=["POST"])
def api_download_another_peer(job_id):
    """Move every file that hasn't landed to a different peer."""
    return _download_action(job_id, downloads.another_peer)


@app.route("/api/downloads/<int:job_id>/cancel", methods=["POST"])
def api_download_cancel(job_id):
    """Cancel a job's transfers in slskd."""
    return _download_action(job_id, downloads.cancel)


@app.route("/api/downloads/<int:job_id>", methods=["DELETE"])
def api_download_delete(job_id):
    """Forget a job (its files stay wherever slskd put them)."""
    downloads.delete(job_id)
    return jsonify({"ok": True})


@app.route("/api/downloads/clear", methods=["POST"])
def api_downloads_clear():
    """Forget every finished (complete or cancelled) job."""
    return jsonify({"removed": downloads.clear_finished()})


@app.route("/api/library/grab", methods=["POST"])
def api_library_grab():
    """Grab one release through the first download client that finds it.

    Body: {"artist", "title", "downloader"?}. The file types come from the
    quality profile and the client order from the configured priority, so this
    behaves exactly like the automatic pass.
    """
    payload = request.get_json(silent=True) or {}
    artist = (payload.get("artist") or "").strip()
    title = (payload.get("title") or "").strip()
    if not artist or not title:
        return jsonify({"error": "artist and title are required"}), 400
    result = grabber.grab(
        artist, title,
        downloader_key=(payload.get("downloader") or "").strip() or None,
    )
    if result.get("error"):
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/quality")
def api_quality():
    """The quality profile, plus the menus the settings UI orders from."""
    profile = quality.describe()
    profile["clients"] = [
        {"key": p.key, "label": p.display_label(), "configured": p.configured()}
        for p in plugins.get_plugins("downloader")
    ]
    profile["client_order"] = [p.key for p in grabber.client_priority()]
    return jsonify(profile)


@app.route("/api/autograb/status")
def api_autograb_status():
    """What the automatic pass would do next, without doing it."""
    items = grabber.pending()
    return jsonify({
        "enabled": (db.get_setting("autograb_enabled") or "false") == "true",
        "pending": items,
        "count": len(items),
    })


@app.route("/api/autograb/run", methods=["POST"])
def api_autograb_run():
    """Run one automatic pass now (works even while the schedule is off)."""
    return jsonify(grabber.run_pass(force=True))


@app.route("/api/artists/from-similar", methods=["POST"])
def api_add_artist_from_similar():
    """Create a library row for a suggested artist, seeded with what is known.

    Body: {"name": "Artist"}. Backs every artist name in the app that isn't in
    the library yet -- the Discover feed and calendar, the scrobble list, the
    Similar Artists rankings, the similar-artist chips on an artist page -- so a
    name always opens a page of ours rather than leaving for Last.fm. The artist
    is created *unfollowed and ignored* (browsing a suggestion should not
    silently fill the library list with artists you only looked at) and
    everything scraped for them (image, bio, Last.fm url, genre tags) is stored,
    so that information outlives the week-long suggestion cache and the page has
    content the first time it opens. The MusicBrainz id is resolved here too,
    because without it the page has no discography to show; the rest (releases,
    discography counts, art) follows on the refresh queue, which this puts the
    artist at the front of. An artist that already exists keeps their own values
    and their place in the library; only empty fields are filled in. Following
    them takes them off the Ignored page.
    """
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    if db.is_non_artist(name):
        # "Various Artists" and the other compilation credits aren't artists,
        # so they get no page and no library row.
        return jsonify({"error": "not an artist"}), 400

    info = similar.artist_info(name) or {}
    genres = ",".join(info.get("genres") or []) or None
    monitor_types = db.normalize_monitor_types(db.get_setting("default_monitor_types"))
    with db._write_lock:
        conn = db.get_connection()
        try:
            existing = conn.execute(
                "SELECT id, subscription, mbid FROM artists WHERE sort_name = ?",
                (name.lower(),),
            ).fetchone()
            if existing:
                artist_id = existing["id"]
                subscription = existing["subscription"]
                mbid = existing["mbid"]
                conn.execute(
                    "UPDATE artists SET image_url = COALESCE(image_url, ?), "
                    "bio = COALESCE(bio, ?), lastfm_url = COALESCE(lastfm_url, ?), "
                    "genres = COALESCE(genres, ?) WHERE id = ?",
                    (info.get("image_url"), info.get("bio"),
                     info.get("lastfm_url"), genres, artist_id),
                )
                created = False
            else:
                # Created ignored: opening a suggestion shouldn't pad the
                # library list with artists you merely looked at. Everything
                # fetched about them is kept, and the Ignored page brings them
                # back instantly -- following one un-ignores it.
                cur = conn.execute(
                    "INSERT INTO artists (name, sort_name, lastfm_url, image_url, "
                    "bio, genres, subscription, monitor_types, track_count, ignored) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'none', ?, 0, 1)",
                    (name, name.lower(), info.get("lastfm_url"),
                     info.get("image_url"), info.get("bio"), genres, monitor_types),
                )
                artist_id = cur.lastrowid
                subscription = "none"
                mbid = None
                created = True
            conn.commit()
        finally:
            conn.close()

    # Resolve MusicBrainz inline rather than leaving it to the queue: it is one
    # paced, cached search, and it is what turns the page from a name into a
    # discography. A MusicBrainz outage still opens the page.
    if not mbid:
        try:
            mbid = musicbrainz.resolve_mbid(name)
        except Exception:  # noqa: BLE001 - the page matters more than the id
            mbid = None
        if mbid:
            with db._write_lock:
                conn = db.get_connection()
                try:
                    conn.execute(
                        "UPDATE artists SET mbid = COALESCE(mbid, ?) WHERE id = ?",
                        (mbid, artist_id),
                    )
                    conn.commit()
                finally:
                    conn.close()

    tracker.warm_art_async([info.get("image_url")])
    # Front of the queue: someone is sitting on this page waiting for it to
    # fill in, so it shouldn't wait behind a bulk refresh pass.
    tracker.enqueue_artist(artist_id, front=True)
    return jsonify(
        {
            "id": artist_id,
            "name": name,
            "created": created,
            "ignored": created,
            "subscription": subscription,
            "mbid": mbid,
            "genres": info.get("genres") or [],
        }
    )


# New-release discovery sources are plugins (app/plugins/discovery). Each knows
# its label, whether it's configured, and how to fetch (items, cached). Adding a
# plugin module there makes it appear on the Discover page automatically.
def _discover_plugins():
    return plugins.get_plugins("discovery")


def _drop_non_artists(items):
    """Remove compilation credits ("Various Artists") from a feed."""
    return [it for it in items if not db.is_non_artist(it.get("artist"))]


def _flag_known_artists(items):
    """Annotate each item with in_library / following / owned flags and the
    artist id, so the frontend can link known artists to their own page and
    filter out releases the library already has."""
    names = {(it.get("artist") or "").lower() for it in items if it.get("artist")}
    if not names:
        return
    conn = db.get_connection()
    try:
        placeholders = ",".join("?" for _ in names)
        rows = conn.execute(
            f"SELECT id, sort_name, subscription FROM artists "
            f"WHERE sort_name IN ({placeholders})",
            list(names),
        ).fetchall()
        known = {r["sort_name"]: (r["id"], r["subscription"]) for r in rows}
        # Which of those artists' albums the library already holds, by title key
        # and by release-group mbid (set when the library source tagged one).
        owned_keys = set()
        owned_mbids = set()
        if known:
            ids = [aid for aid, _ in known.values()]
            owned_rows = conn.execute(
                f"SELECT artist_id, album_key, rg_mbid FROM owned_albums "
                f"WHERE artist_id IN ({','.join('?' for _ in ids)})",
                ids,
            ).fetchall()
            for r in owned_rows:
                owned_keys.add((r["artist_id"], r["album_key"]))
                if r["rg_mbid"]:
                    owned_mbids.add(r["rg_mbid"])
    finally:
        conn.close()
    for it in items:
        aid, sub = known.get((it.get("artist") or "").lower(), (None, None))
        it["artist_id"] = aid
        it["in_library"] = sub is not None
        it["following"] = sub in ("subscribed", "notify")
        # Distinguished from "following" so the feed can show which artists
        # actually announce themselves, rather than a bell that means nothing
        # until you press it.
        it["notify"] = sub == "notify"
        it["owned"] = bool(
            (it.get("mbid") and it["mbid"] in owned_mbids)
            or (aid and (aid, db.owned_album_key(it.get("album"))) in owned_keys)
        )


# Background Discover refreshes. Scraping (especially Metacritic, which enriches
# every release via MusicBrainz at 1 req/sec) takes minutes, so it must never run
# inside a request - that would hang the page. Instead we serve whatever is in the
# DB cache immediately and refresh in a daemon thread; the page polls until done.
_discover_jobs = {}     # source key -> running Thread
_discover_errors = {}   # source key -> last error string (or None)
_discover_jobs_lock = threading.Lock()


def _months_ago(months):
    """Date *months* whole months before today (day clamped to the month length)."""
    today = date.today()
    idx = today.year * 12 + (today.month - 1) - months
    y, m = divmod(idx, 12)
    m += 1
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    last_day = (nxt - timedelta(days=1)).day
    return date(y, m, min(today.day, last_day))


def _discover_history_months():
    try:
        return max(int(db.get_setting("discover_history_months") or 3), 0)
    except (TypeError, ValueError):
        return 3


def _discover_ttl_seconds():
    try:
        return max(float(db.get_setting("discover_refresh_hours") or 24), 1) * 3600
    except (TypeError, ValueError):
        return 24 * 3600


def _is_refreshing(key):
    job = _discover_jobs.get(key)
    return job is not None and job.is_alive()


def _kick_refresh(key, plugin):
    """Start a background scrape for *plugin* unless one is already running."""
    with _discover_jobs_lock:
        if _is_refreshing(key):
            return

        def worker():
            try:
                plugin.fetch(force=True)
                _discover_errors[key] = None
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI instead
                _discover_errors[key] = str(exc)

        thread = threading.Thread(target=worker, daemon=True)
        _discover_jobs[key] = thread
        thread.start()


# Library scans all go through one coordinator (app/scans): one at a time, the
# rest queued, any of them cancellable. Each source registers how to run it the
# first time the registry is asked, so a plugin added later needs no wiring.
def _library_scanning(key):
    """True when this source is mid-scan (queued doesn't count as scanning)."""
    if scans.state_for(key)["state"] == "running":
        return True
    # A scan started before this process (or the per-artist path) reports
    # through the plugin's own progress.
    plugin = plugins.get_plugin("library", key)
    return bool(plugin and plugin.progress().get("running"))


def _library_error(key):
    result = scans.status()["results"].get(key)
    return result["message"] if result and result["status"] == "error" else None


def _merge_discover_items(items):
    """Collapse the same album seen from multiple sources into one row.

    Keyed by (artist, album) case-insensitively. The merged row keeps every
    source in a `sources` list (so the agenda can show both tags) and fills any
    missing image/genres/date/context/mbid from whichever source has them.
    Items lacking an artist or album are never merged.
    """
    merged = []
    by_key = {}
    for it in items:
        artist = (it.get("artist") or "").strip().lower()
        album = (it.get("album") or "").strip().lower()
        src = {"key": it.get("source"), "label": it.get("source_label")}
        key = (artist, album) if artist and album else None
        existing = by_key.get(key) if key else None
        if existing is None:
            row = dict(it)
            row["sources"] = [src]
            merged.append(row)
            if key:
                by_key[key] = row
            continue
        # Same album from another source: record the tag and backfill blanks.
        if src not in existing["sources"]:
            existing["sources"].append(src)
        for field in ("image", "context", "context_artists", "mbid",
                      "artist_url", "album_url"):
            if not existing.get(field) and it.get(field):
                existing[field] = it[field]
        if not existing.get("genres") and it.get("genres"):
            existing["genres"] = it["genres"]
        # Keep the earliest known release date.
        nd = it.get("normalized_date")
        if nd and (not existing.get("normalized_date") or nd < existing["normalized_date"]):
            existing["normalized_date"] = nd
    return merged


@app.route("/api/discover/sources")
def api_discover_sources():
    """List the available discovery sources and whether each is configured."""
    return jsonify({"sources": [
        {"key": p.key, "label": p.display_label(), "configured": p.configured()}
        for p in _discover_plugins()
    ]})


@app.route("/api/discover/releases")
def api_discover_releases():
    """Merged releases from every configured discovery source. ?refresh=1 re-fetches.

    Each item is tagged with its `source` / `source_label`; the response also
    reports per-source status (configured / count / any error).

    Always returns immediately from the persisted DB cache. A source is
    refreshed in the background (never inline) when its cache is stale/empty, or
    when asked: ?refresh=<source-key> for one, ?refresh=all (or =1) for every
    source. Each source reports `refreshing` so the page can poll until done.
    """
    refresh = request.args.get("refresh")
    refresh_all = refresh in ("all", "1")
    ttl = _discover_ttl_seconds()
    items = []
    sources = []
    for plugin in _discover_plugins():
        key = plugin.key
        entry = {"key": key, "label": plugin.display_label(), "configured": plugin.configured(),
                 "count": 0, "error": _discover_errors.get(key)}
        if entry["configured"]:
            fetched_at, cached_items = db.get_discover_cache(key)
            stale = (not fetched_at) or (not cached_items) or (time.time() - fetched_at > ttl)
            if refresh_all or refresh == key or stale:
                _kick_refresh(key, plugin)
            entry["count"] = len(cached_items)
            entry["fetched_at"] = fetched_at
            entry["stale"] = stale
            entry["refreshing"] = _is_refreshing(key)
            for it in cached_items:
                # Normalise here too so rows cached before the schema existed
                # (or by a sloppy plugin) still reach the merge layer consistent.
                tagged = discovery.normalize_item(it)
                tagged["source"] = key
                tagged["source_label"] = plugin.label
                items.append(tagged)
        sources.append(entry)

    # Fold in previously-discovered releases from the configured sources, so the
    # past `discover_history_months` of releases stay on the page after they drop
    # out of a source's live scrape. The merge below dedups against the live rows.
    months = _discover_history_months()
    if months:
        cutoff = _months_ago(months).isoformat()
        labels = {p.key: p.label for p in _discover_plugins()}
        hist_sources = [s["key"] for s in sources if s["configured"]]
        for raw in db.get_discover_history(hist_sources, cutoff):
            tagged = discovery.normalize_item(raw)
            src = raw.get("source")
            tagged["source"] = src
            tagged["source_label"] = labels.get(src, src)
            items.append(tagged)

    items = _merge_discover_items(items)

    # Drop anything the user has ignored (whole artists or single releases).
    ign_artists, ign_albums = db.discover_ignore_sets()
    if ign_artists or ign_albums:
        def _ignored(it):
            a = (it.get("artist") or "").strip().lower()
            b = (it.get("album") or "").strip().lower()
            return a in ign_artists or (a, b) in ign_albums
        items = [it for it in items if not _ignored(it)]

    items = _drop_non_artists(items)
    _flag_known_artists(items)
    items.sort(key=lambda r: r.get("normalized_date") or "9999")
    return jsonify({
        "sources": sources,
        "count": len(items),
        "items": items,
        "refreshing": any(s.get("refreshing") for s in sources),
    })


@app.route("/api/discover/ignores", methods=["GET", "POST"])
def api_discover_ignores():
    """Discover-page ignore rules.

    GET lists them; POST {"artist": ..., "album"?: ...} adds one (album omitted
    or blank hides the whole artist from the feed).
    """
    if request.method == "GET":
        return jsonify({"ignores": db.list_discover_ignores()})
    payload = request.get_json(silent=True) or {}
    added = db.add_discover_ignore(payload.get("artist"), payload.get("album"))
    if added is None:
        return jsonify({"error": "artist is required"}), 400
    return jsonify({"added": added, "ignores": db.list_discover_ignores()})


@app.route("/api/discover/ignores/<int:ignore_id>", methods=["DELETE"])
def api_discover_ignore_delete(ignore_id):
    removed = db.remove_discover_ignore(ignore_id)
    return jsonify({"removed": removed})


@app.route("/api/artwork/search")
def api_artwork_search():
    """Artist photos for any name, from the metadata sources in order.

    For the artist whose name nothing matches: a stage name spelled with a
    Greek letter, a transliteration, a duo listed under one member. Searching
    the name the services use finds the picture the artist's own row can't.
    Params: q (the name to search for).
    """
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "q is required"}), 400
    return jsonify({
        "query": query,
        "options": metadata.artist_images(query),
    })


@app.route("/api/artwork/review")
def api_artwork_review():
    """Artists whose artwork is missing or won't load, for review by hand.

    ``?dismissed=1`` includes the ones set aside; ``limit`` / ``offset`` page
    through, and ``limit=0`` answers with the counts alone. ``ready`` is false
    until an artwork pass has run at least once, which is when the list means
    anything.
    """
    include = request.args.get("dismissed") == "1"
    ids = None
    if request.args.get("ids"):
        ids = [int(x) for x in request.args["ids"].split(",") if x.strip().isdigit()]
    try:
        limit = max(0, min(int(request.args.get("limit", 25)), 200))
    except ValueError:
        limit = 25
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0
    return jsonify(artreview.needs_artwork(
        limit=limit, offset=offset, include_dismissed=include, ids=ids))


@app.route("/api/artwork/review/dismiss", methods=["POST"])
def api_artwork_review_dismiss():
    """Set an artist aside, or bring them back.

    Body: {"artist_id": n} to dismiss, {"artist_id": n, "restore": true} to
    undo one, {"restore": true} to bring all of them back.
    """
    payload = request.get_json(silent=True) or {}
    artist_id = payload.get("artist_id")
    if payload.get("restore"):
        return jsonify({"restored": artreview.restore(artist_id)})
    if not isinstance(artist_id, int):
        return jsonify({"error": "artist_id is required"}), 400
    return jsonify({"dismissed": artreview.dismiss(artist_id)})


@app.route("/api/album/extras")
def api_album_extras():
    """What the metadata sources add to one release, beyond its tracklist.

    Fetched separately from /api/album so the page renders straight away: the
    first lookup for a release can involve a slow source, and after that it's
    answered from storage. Params: artist, title, mbid?.
    """
    artist = (request.args.get("artist") or "").strip()
    title = (request.args.get("title") or "").strip()
    if not artist or not title:
        return jsonify({"error": "artist and title are required"}), 400
    mbid = (request.args.get("mbid") or "").strip() or None
    return jsonify(metadata.album_info(artist, title, mbid))


@app.route("/api/metadata/gaps")
def api_metadata_gaps():
    """What metadata the followed artists are missing, with examples."""
    return jsonify(metascan.gaps())


@app.route("/api/metadata/fill", methods=["POST"])
def api_metadata_fill():
    """Fill the gaps from the metadata sources, in the background.

    Body: {"kinds": ["images", "bios", "genres"]} to limit it; omitted does all
    three. One run at a time.
    """
    payload = request.get_json(silent=True) or {}
    kinds = payload.get("kinds") or None
    if not metascan.start(kinds):
        return jsonify({"error": "a fill is already running",
                        **metascan.get_state()}), 409
    return jsonify({"started": True, **metascan.get_state()})


@app.route("/api/metadata/fill/status")
def api_metadata_fill_status():
    return jsonify(metascan.get_state())


@app.route("/api/metadata/fill/cancel", methods=["POST"])
def api_metadata_fill_cancel():
    return jsonify({"cancelling": metascan.stop(), **metascan.get_state()})


@app.route("/api/metadata/order")
def api_metadata_order():
    """Which metadata source answers which field, in the order they're asked.

    One ordered list per field (photos, biographies, genre tags, covers), each
    naming only the sources that can answer it.
    """
    from .plugins import metadata as meta_plugins

    described = {p.key: p.describe() for p in plugins.get_plugins("metadata")}
    fields = []
    for capability in meta_plugins.CAPABILITIES:
        label, description = meta_plugins.CAPABILITY_LABELS.get(
            capability, (capability, ""))
        fields.append({
            "key": capability,
            "label": label,
            "description": description,
            "group": meta_plugins.CAPABILITY_GROUPS.get(capability, "artist"),
            "order": meta_plugins.priority(capability),
        })
    # Not a metadata source, but the same kind of choice and the same UI: when
    # several of your own libraries hold a track, which one streams it.
    playable = [p for p in plugins.get_plugins("library")
                if getattr(p, "plays_tracks", False)]
    if playable:
        for plugin in playable:
            described.setdefault(plugin.key, plugin.describe())
        fields.append({
            "key": librarytrack.PLAYBACK_FIELD,
            "group": "playback",
            "label": "Plays your own copy",
            "description": ("Which of your libraries streams a track you have. "
                            "Whichever is first and holds the song wins; these "
                            "always come before the preview sources above."),
            "order": librarytrack.playback_order(),
        })
    groups = [
        {"key": key, "label": label, "description": description}
        for key, (label, description) in meta_plugins.GROUP_LABELS.items()
    ]
    return jsonify({"fields": fields, "groups": groups,
                    "plugins": list(described.values())})


@app.route("/api/metadata/order", methods=["POST"])
def api_set_metadata_order():
    """Reorder one field's sources. Body: {"capability": ..., "keys": [...]}."""
    from .plugins import metadata as meta_plugins

    payload = request.get_json(silent=True) or {}
    capability = (payload.get("capability") or "").strip()
    keys = payload.get("keys")
    if not isinstance(keys, list):
        return jsonify({"error": "keys must be a list"}), 400
    if capability == librarytrack.PLAYBACK_FIELD:
        return jsonify({"capability": capability,
                        "order": librarytrack.set_playback_order(keys)})
    if capability not in meta_plugins.CAPABILITIES:
        return jsonify({"error": "unknown metadata field"}), 400
    order = meta_plugins.set_priority(keys, capability)
    return jsonify({"capability": capability, "order": order})


@app.route("/api/plugins")
def api_plugins():
    """Plugin metadata for the Settings UI. ?kind=discovery filters to one kind.

    Each entry reports its enable toggle, whether it's fully configured, and any
    config fields (e.g. a cookie) the settings tab should render. The field
    values themselves live in /api/settings; this only describes them.
    """
    kind = request.args.get("kind")
    kinds = [kind] if kind else plugins.kinds()
    out = []
    for k in kinds:
        for p in plugins.get_plugins(k):
            data = p.describe()
            # Discovery plugins back onto the background scrape jobs; report
            # whether one is running now and the last error, so the Plugins tab
            # can show live "Scraping..." state without hitting the Discover API.
            if k == "discovery":
                data["refreshing"] = _is_refreshing(p.key)
                data["error"] = _discover_errors.get(p.key)
            elif k == "library":
                data["scanning"] = _library_scanning(p.key)
                data.update(scans.state_for(p.key))
                data["error"] = _library_error(p.key)
                data["progress"] = p.progress()
            out.append(data)
    return jsonify({"plugins": out})


@app.route("/api/similar/artist")
def api_similar_artist():
    """Who sounds like one artist, from every similar-artist source.

    Params: artist. The suggestions are recorded on the way past, which is
    what builds the "worth checking out" ranking on Discover.
    """
    name = (request.args.get("artist") or "").strip()
    suggestions, suggestion_sources = similar.collect(name) if name else ([], [])
    _attach_local_artist_ids(suggestions)
    return jsonify({
        "artists": suggestions,
        "source": ", ".join(suggestion_sources) or None,
    })


@app.route("/api/lastfm/top-artists")
def api_lastfm_top_artists():
    """The user's most-played Last.fm artists, flagged against the library.

    Params: period (overall|7day|1month|3month|6month|12month), limit,
    unowned=1 to drop the ones already in the library. Cached server-side for
    a few hours, so switching periods is cheap.
    """
    period = request.args.get("period") or "overall"
    try:
        limit = min(int(request.args.get("limit", 200)), 1000)
    except ValueError:
        limit = 200
    artists = lastfm.top_artists(period=period, limit=limit)
    _attach_local_artist_ids(artists)
    if request.args.get("unowned") == "1":
        artists = [a for a in artists if not a.get("owned")]
    return jsonify({
        "user": lastfm.username(),
        "period": period,
        "artists": artists,
        "configured": bool(lastfm.username() and db.get_setting("lastfm_api_key")),
    })


@app.route("/api/health/lastfm-user", methods=["GET", "POST"])
def api_health_lastfm_user():
    ok, message = lastfm.check_user()
    return jsonify({"ok": ok, "message": message})


@app.route("/api/similar/rankings")
def api_similar_rankings():
    """Suggested artists ranked by how many of your artists they're similar to,
    excluding ones you already own. Populated as artist pages are browsed.

    ``genres_known`` says for how many of them genre tags are on hand (the rest
    need the bulk lookup below before they can be filtered by genre).
    """
    known, total = db.similar_genre_coverage()
    return jsonify({
        "artists": db.similar_artist_rankings(),
        "genres_known": known,
        "genres_total": total,
    })


@app.route("/api/similar/artist-info")
def api_similar_artist_info():
    """Details for one suggested artist, for the Similar Artists rows on
    Discover. The frontend requests these one at a time, so a page of rows
    doesn't fire a burst of lookups at the metadata sources."""
    name = (request.args.get("artist") or "").strip()
    if not name:
        return jsonify({})
    return jsonify(similar.artist_info(name))


@app.route("/api/similar/scan", methods=["POST"])
def api_similar_scan():
    """Start (or stop, with {"stop": true}) the bulk similar-artist scan of the
    owned library. Runs in the background, rate-limited."""
    payload = request.get_json(silent=True) or {}
    if payload.get("stop"):
        similar_scan.stop()
        return jsonify(similar_scan.get_state())
    started = similar_scan.start()
    state = similar_scan.get_state()
    state["started"] = started
    return jsonify(state)


@app.route("/api/similar/scan/status")
def api_similar_scan_status():
    return jsonify(similar_scan.get_state())


@app.route("/api/similar/enrich", methods=["POST"])
def api_similar_enrich():
    """Start (or stop, with {"stop": true}) the bulk genre lookup for suggested
    artists, best-ranked first. Body: {"limit": 500} caps how many to look up in
    this run (0 or omitted = all of them). Rate-limited background work."""
    payload = request.get_json(silent=True) or {}
    if payload.get("stop"):
        similar_enrich.stop()
        return jsonify(similar_enrich.get_state())
    try:
        limit = int(payload.get("limit") or 0)
    except (TypeError, ValueError):
        limit = 0
    started = similar_enrich.start(limit)
    state = similar_enrich.get_state()
    state["started"] = started
    return jsonify(state)


@app.route("/api/similar/enrich/status")
def api_similar_enrich_status():
    return jsonify(similar_enrich.get_state())


def _attach_local_artist_ids(entries):
    """Add artist_id / owned to each {name, ...} entry matching a library artist,
    so the UI can link internally and color owned suggestions."""
    names = {(e.get("name") or "").lower() for e in entries if e.get("name")}
    if not names:
        return
    conn = db.get_connection()
    try:
        placeholders = ",".join("?" for _ in names)
        rows = conn.execute(
            f"SELECT id, sort_name, track_count, subscription FROM artists "
            f"WHERE sort_name IN ({placeholders})",
            list(names),
        ).fetchall()
        known = {r["sort_name"]: (r["id"], r["track_count"], r["subscription"])
                 for r in rows}
    finally:
        conn.close()
    for e in entries:
        aid, tracks, sub = known.get((e.get("name") or "").lower(), (None, 0, "none"))
        e["artist_id"] = aid
        e["owned"] = bool(tracks)
        e["subscription"] = sub or "none"


@app.route("/api/plugins/<kind>/<key>/test", methods=["POST"])
def api_plugin_test(kind, key):
    """Run a plugin's health check (e.g. validate the Last.fm cookie)."""
    plugin = plugins.get_plugin(kind, key)
    if plugin is None:
        return jsonify({"ok": False, "message": "unknown plugin"}), 404
    result = plugin.check()
    if result is None:
        return jsonify({"ok": True, "message": "No test available for this plugin."})
    ok, message = result
    return jsonify({"ok": ok, "message": message})


@app.route("/api/plugins/<kind>/<key>/refresh", methods=["POST"])
def api_plugin_refresh(kind, key):
    """Kick a background re-scrape for one plugin. Returns its refreshing state."""
    plugin = plugins.get_plugin(kind, key)
    if plugin is None:
        return jsonify({"error": "unknown plugin"}), 404
    if not hasattr(plugin, "fetch"):
        return jsonify({"error": "plugin is not refreshable"}), 400
    _kick_refresh(key, plugin)
    return jsonify({"refreshing": _is_refreshing(key)})


@app.route("/api/plugins/library/<key>/scan", methods=["POST"])
def api_library_scan(key):
    """Queue a library scan. ?quick=1 for the filesystem incremental sync.

    Returns where the request landed: "started" when the worker was free,
    "queued" with its position when something else is scanning, "duplicate"
    when this source is already running or waiting.
    """
    plugin = plugins.get_plugin("library", key)
    if plugin is None:
        return jsonify({"error": "unknown library"}), 404
    if not plugin.configured():
        return jsonify({"error": "library is not configured"}), 400
    payload = request.get_json(silent=True) or {}
    quick = request.args.get("quick") in ("1", "true") or bool(payload.get("quick"))
    scans.ensure_library_runners()
    # The enqueue result says what this request did ("started" / "queued" /
    # "duplicate"); state_for would flatten all three into where it sits now.
    result = scans.enqueue(key, quick=quick)
    return jsonify({"scanning": True, **result})


@app.route("/api/plugins/library/<key>/cancel", methods=["POST"])
def api_library_cancel(key):
    """Stop this source's scan, or drop it from the queue."""
    if plugins.get_plugin("library", key) is None:
        return jsonify({"error": "unknown library"}), 404
    return jsonify(scans.cancel(key))


@app.route("/api/plugins/library/<key>/status")
def api_library_status(key):
    """Live progress for one library: queue position plus its own counters."""
    plugin = plugins.get_plugin("library", key)
    if plugin is None:
        return jsonify({"error": "unknown library"}), 404
    return jsonify({
        "scanning": _library_scanning(key),
        "error": _library_error(key),
        **scans.state_for(key),
        **plugin.progress(),
    })


@app.route("/api/scans")
def api_scans():
    """The whole scan queue: what runs, what waits, how the last ones went."""
    return jsonify(scans.status())


@app.route("/api/scans/cancel", methods=["POST"])
def api_scans_cancel():
    """Cancel everything: the running scan and the whole queue."""
    return jsonify(scans.cancel())


@app.route("/api/artists/add", methods=["POST"])
def api_add_artist():
    """Start monitoring an artist from a pasted MusicBrainz link (or raw MBID).

    Body: {"link": "https://musicbrainz.org/artist/<mbid>", "state": "subscribed"}.
    Creates the artist if it isn't already in the library (track_count 0), sets
    the subscription, and kicks off a metadata fetch.
    """
    payload = request.get_json(silent=True) or {}
    link = payload.get("link") or payload.get("mbid") or payload.get("url") or ""
    state = payload.get("state") or "subscribed"
    if state not in ("subscribed", "notify"):
        return jsonify({"error": "state must be 'subscribed' or 'notify'"}), 400

    mbid = musicbrainz.extract_mbid(link)
    if not mbid:
        return jsonify({"error": "no MusicBrainz artist id found in that link"}), 400

    # Release types to monitor; default to the configured global default.
    if payload.get("types"):
        monitor_types = db.normalize_monitor_types(payload["types"])
    else:
        monitor_types = db.normalize_monitor_types(
            db.get_setting("default_monitor_types")
        )

    # Look up the canonical name from MusicBrainz.
    info = musicbrainz.lookup_artist(mbid)
    if not info:
        return jsonify({"error": "artist not found on MusicBrainz"}), 404

    with db._write_lock:
        conn = db.get_connection()
        try:
            # Match an existing row by MBID first, then by name.
            existing = conn.execute(
                "SELECT id FROM artists WHERE mbid = ? OR sort_name = ?",
                (mbid, info["name"].lower()),
            ).fetchone()
            if existing:
                artist_id = existing["id"]
                conn.execute(
                    "UPDATE artists SET subscription = ?, monitor_types = ?, "
                    "mbid = COALESCE(mbid, ?) WHERE id = ?",
                    (state, monitor_types, mbid, artist_id),
                )
                created = False
            else:
                cur = conn.execute(
                    "INSERT INTO artists (name, sort_name, mbid, subscription, "
                    "monitor_types, track_count) VALUES (?, ?, ?, ?, ?, 0)",
                    (info["name"], info["name"].lower(), mbid, state, monitor_types),
                )
                artist_id = cur.lastrowid
                created = True
            conn.commit()
        finally:
            conn.close()

    tracker.enqueue_artist(artist_id)
    return jsonify(
        {
            "id": artist_id,
            "name": info["name"],
            "mbid": mbid,
            "subscription": state,
            "monitor_types": monitor_types.split(","),
            "created": created,
        }
    )


@app.route("/api/artists/subscriptions", methods=["POST"])
def api_bulk_subscription():
    """Bulk set subscription state for many artists at once.

    Body: {"ids": [..], "state": "subscribed"}.
    """
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids") or []
    state = payload.get("state")
    if state not in VALID_STATES:
        return jsonify({"error": "invalid state"}), 400
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        return jsonify({"error": "no ids"}), 400

    placeholders = ",".join("?" for _ in ids)
    with db._write_lock:
        conn = db.get_connection()
        try:
            conn.execute(
                f"UPDATE artists SET subscription = ?, "
                f"ignored = CASE WHEN ? = 'none' THEN ignored ELSE 0 END "
                f"WHERE id IN ({placeholders})",
                [state, state, *ids],
            )
            conn.commit()
        finally:
            conn.close()

    if state in ("subscribed", "notify"):
        for artist_id in ids:
            tracker.enqueue_artist(artist_id)
    elif state == "none":
        _purge_on_unfollow(ids)

    return jsonify({"updated": len(ids), "state": state})


@app.route("/api/artists/<int:artist_id>/ignore", methods=["POST"])
def api_set_ignore(artist_id):
    """Hide or unhide an artist from the main library list.

    Body: {"ignored": true|false}. Ignored artists move to the Ignored area and
    no longer appear in the default Artists listing.
    """
    payload = request.get_json(silent=True) or {}
    ignored = 1 if payload.get("ignored", True) else 0

    with db._write_lock:
        conn = db.get_connection()
        try:
            cur = conn.execute(
                "UPDATE artists SET ignored = ? WHERE id = ?", (ignored, artist_id)
            )
            conn.commit()
        finally:
            conn.close()
        if cur.rowcount == 0:
            return jsonify({"error": "artist not found"}), 404

    return jsonify({"id": artist_id, "ignored": bool(ignored)})


@app.route("/api/artists/ignore", methods=["POST"])
def api_bulk_ignore():
    """Bulk hide/unhide artists. Body: {"ids": [..], "ignored": true|false}."""
    payload = request.get_json(silent=True) or {}
    ids = [int(i) for i in (payload.get("ids") or []) if str(i).isdigit()]
    ignored = 1 if payload.get("ignored", True) else 0
    if not ids:
        return jsonify({"error": "no ids"}), 400

    placeholders = ",".join("?" for _ in ids)
    with db._write_lock:
        conn = db.get_connection()
        try:
            conn.execute(
                f"UPDATE artists SET ignored = ? WHERE id IN ({placeholders})",
                [ignored, *ids],
            )
            conn.commit()
        finally:
            conn.close()
    return jsonify({"updated": len(ids), "ignored": bool(ignored)})


@app.route("/api/ignored")
def api_ignored():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            f"SELECT {ARTIST_LIST_COLUMNS} FROM artists WHERE ignored = 1 "
            "ORDER BY sort_name"
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"artists": [_row_to_dict(r) for r in rows]})


@app.route("/api/subscriptions")
def api_subscriptions():
    conn = db.get_connection()
    try:
        # Only what the Following table renders: it shows the artist, their
        # art, their subscription and the next release joined in below. The
        # full artist row is 254KB of JSON for a 2400-artist library.
        rows = conn.execute(
            "SELECT id, name, image_url, subscription FROM artists "
            "WHERE subscription IN ('subscribed', 'notify') ORDER BY sort_name"
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"artists": [_row_to_dict(r) for r in rows]})


@app.route("/api/upcoming")
def api_upcoming():
    """Upcoming releases for followed artists within a window.

    ?window=day|week|next-week|month|all
    """
    window = request.args.get("window", "month")
    if window == "all":
        # Everything from today forward, far out.
        items = _query_upcoming("month", include_past=False)
        # Pull anything beyond a month too by widening the query manually.
        conn = db.get_connection()
        try:
            rows = conn.execute(
                "SELECT r.*, a.name AS artist_name, a.id AS artist_id, "
                "a.subscription AS subscription "
                "FROM releases r JOIN artists a ON a.id = r.artist_id "
                "WHERE a.subscription IN ('subscribed', 'notify') "
                "ORDER BY r.release_date"
            ).fetchall()
        finally:
            conn.close()
        today = date.today()
        items = []
        for row in rows:
            nd = _normalize_date(row["release_date"])
            if nd is None or nd < today:
                continue
            item = _row_to_dict(row)
            item["normalized_date"] = nd.isoformat()
            item["days_until"] = (nd - today).days
            items.append(item)
        items.sort(key=lambda r: r["normalized_date"])
        _apply_art_overrides(items)
        return jsonify({"window": "all", "count": len(items), "releases": items})

    if window not in WINDOWS:
        return jsonify({"error": "invalid window"}), 400
    items = _query_upcoming(window)
    return jsonify({"window": window, "count": len(items), "releases": items})


@app.route("/api/upcoming/releases")
def api_upcoming_releases():
    """Releases for followed artists within an explicit date range.

    Params: from=YYYY-MM-DD (default today), to=YYYY-MM-DD (default +366 days).
    Powers the agenda (week-by-week) and calendar views. Includes past dates
    when the range asks for them (so a calendar month grid can be filled).
    """
    today = date.today()

    def _parse(arg, fallback):
        try:
            return datetime.strptime(arg, "%Y-%m-%d").date() if arg else fallback
        except ValueError:
            return fallback

    start = _parse(request.args.get("from"), today)
    end = _parse(request.args.get("to"), today + timedelta(days=366))

    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT r.*, a.name AS artist_name, a.id AS artist_id, "
            "a.subscription AS subscription "
            "FROM releases r JOIN artists a ON a.id = r.artist_id "
            "WHERE a.subscription IN ('subscribed', 'notify') "
            "ORDER BY r.release_date"
        ).fetchall()
    finally:
        conn.close()

    items = []
    for row in rows:
        nd = _normalize_date(row["release_date"])
        if nd is None or nd < start or nd > end:
            continue
        item = _row_to_dict(row)
        item["normalized_date"] = nd.isoformat()
        item["days_until"] = (nd - today).days
        items.append(item)
    items.sort(key=lambda r: r["normalized_date"])
    _apply_art_overrides(items)
    return jsonify({
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(items),
        "releases": items,
    })


@app.route("/api/upcoming/playlist", methods=["POST"])
def api_upcoming_playlist():
    """Build the "Get Hyped" playlist on the library, for the week ahead.

    Body: {"days": 7, "per_artist": 3, "name": "Get Hyped"} -- all optional;
    each defaults to the configured value. The artists have nothing playable yet
    (their records aren't out), so what goes in is their best-known songs that
    your own server already holds.
    """
    payload = request.get_json(silent=True) or {}
    days = payload.get("days")
    if days is not None:
        try:
            days = max(1, min(int(days), 365))
        except (TypeError, ValueError):
            days = None  # fall back to the configured window
    per_artist = payload.get("per_artist")
    if per_artist is not None:
        try:
            per_artist = max(1, min(int(per_artist), 20))
        except (TypeError, ValueError):
            per_artist = None
    name = (payload.get("name") or hype.PLAYLIST_NAME).strip() or hype.PLAYLIST_NAME
    try:
        return jsonify(hype.build(days=days, per_artist=per_artist, name=name))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/upcoming/playlist")
def api_upcoming_playlist_state():
    """Whether a playlist can be written, where it would go, and what's in it."""
    plugin = next(iter(hype._targets()), None)
    tracks = []
    if plugin is not None:
        try:
            tracks = plugin.playlist_tracks(hype.PLAYLIST_NAME)
        except Exception:  # noqa: BLE001 - an unreachable server has no playlist
            tracks = []
    return jsonify({
        "available": bool(plugin),
        "label": plugin.display_label() if plugin else None,
        "icon": plugin.icon_name() if plugin else None,
        "name": hype.PLAYLIST_NAME,
        # What's on the server now, so the page can offer to play it.
        "tracks": tracks,
        "schedule": hype.schedule_state(),
    })


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Queue a filesystem scan. Body: {"quick": true} for an incremental sync.

    Shares the queue with every other library source, so this can't start a
    second scan alongside one kicked off from the settings page.
    """
    directory = db.get_setting("music_directory")
    payload = request.get_json(silent=True) or {}
    quick = bool(payload.get("quick"))
    scans.ensure_library_runners()
    result = scans.enqueue("filesystem", quick=quick)
    if result.get("state") == "duplicate":
        return jsonify({"error": "that scan is already running or queued",
                        **scans.state_for("filesystem")}), 409
    return jsonify({"started": result.get("state") == "started",
                    "directory": directory, "quick": quick, **result})


@app.route("/api/scan/status")
def api_scan_status():
    state = scanner.get_scan_state()
    queue = scans.state_for("filesystem")
    state.update(queue)
    # "running" has to stay true while queued, or the Artists page stops polling
    # and never notices the scan actually starting.
    state["running"] = bool(state.get("running")) or queue["state"] in ("running", "queued")
    return jsonify(state)


@app.route("/api/scan/cancel", methods=["POST"])
def api_scan_cancel():
    """Stop the filesystem scan (or drop it from the queue)."""
    return jsonify(scans.cancel("filesystem"))


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    queued = tracker.enqueue_all_subscribed()
    return jsonify({"started": True, "queued": queued})


@app.route("/api/artists/<int:artist_id>/refresh", methods=["POST"])
def api_refresh_artist(artist_id):
    tracker.enqueue_artist(artist_id)
    return jsonify({"started": True, "id": artist_id})


@app.route("/api/refresh/status")
def api_refresh_status():
    return jsonify(tracker.get_refresh_state())


def _writable_settings():
    """Every settings key the UI may write.

    The defaults table plus whatever the plugins declare, because a plugin that
    adds a field shouldn't also have to register a default somewhere else --
    that mismatch silently dropped the value on save.
    """
    allowed = set(db.DEFAULT_SETTINGS)
    for kind in plugins.kinds():
        for plugin in plugins.get_plugins(kind):
            described = plugin.describe()
            for field in described.get("config_fields") or []:
                if field.get("key"):
                    allowed.add(field["key"])
            if described.get("enabled_setting"):
                allowed.add(described["enabled_setting"])
    return allowed


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(db.get_all_settings())

    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form.to_dict()
    allowed = _writable_settings()
    updated = {}
    for key, value in payload.items():
        # Subsonic connect: derive a salt+token from the password and store those
        # instead -- the password itself is never persisted. Blank = keep current.
        if key == "subsonic_password":
            pwd = (value or "").strip()
            if pwd:
                salt = secrets.token_hex(8)
                token = hashlib.md5((pwd + salt).encode("utf-8")).hexdigest()
                db.set_setting("subsonic_salt", salt)
                db.set_setting("subsonic_token", token)
                updated["subsonic_connected"] = True
            continue
        # Plex token is write-to-keep: a blank submit leaves the current token
        # intact instead of wiping a working connection.
        if key == "plex_token" and not (value or "").strip():
            continue
        if key not in allowed:
            continue
        if key == "default_monitor_types":
            value = db.normalize_monitor_types(value)
        elif key == "discography_autohide":
            value = ",".join(db.clean_types(value))
        elif key == "home_page":
            value = value if value in PAGE_DEFS else DEFAULT_HOME
        elif key == "nav_order":
            value = ",".join(normalize_nav_order(value))
        elif key == "nav_hidden":
            value = ",".join([k.strip() for k in str(value).split(",") if k.strip() in PAGE_DEFS])
        elif key in ("prefer_album_artist", "discover_lastfm_enabled",
                     "discover_metacritic_enabled", "hide_page_descriptions", "cache_images",
                     "library_filesystem_enabled", "library_subsonic_enabled",
                     "library_plex_enabled") or (
                         key.endswith("_enabled") and
                         key.startswith(("library_", "discover_"))):
            value = "true" if str(value).lower() in ("true", "1", "on", "yes") else "false"
        elif key.startswith("library_") and key.endswith("_scan_hours"):
            try:
                hours = max(0.0, float(value))
                # Stored whole where possible: the picker's values are "0",
                # "24", "168" and a stored "24.0" would match none of them.
                value = str(int(hours)) if hours == int(hours) else str(hours)
            except (TypeError, ValueError):
                value = "0"
        elif key == "discover_refresh_hours":
            try:
                value = str(max(int(float(value)), 1))
            except (TypeError, ValueError):
                value = "24"
        elif key == "discover_enrich_workers":
            try:
                value = str(min(max(int(float(value)), 1), 16))
            except (TypeError, ValueError):
                value = "8"
        elif key == "webhook_trigger":
            value = "before_release" if value == "before_release" else "discovery"
        elif key == "webhook_lead_value":
            try:
                value = str(max(int(float(value)), 0))
            except (TypeError, ValueError):
                value = "0"
        elif key == "webhook_lead_unit":
            value = value if value in ("hours", "days", "weeks") else "days"
        elif key == "musicbrainz_rate_limit_ms":
            # Clamp to >= 1000ms so we never undercut MusicBrainz's 1 req/sec.
            try:
                value = str(max(int(float(value)), 1000))
            except (TypeError, ValueError):
                value = "1100"
        db.set_setting(key, str(value))
        updated[key] = value
    return jsonify({"updated": updated})


@app.route("/api/webhook/test", methods=["POST"])
def api_webhook_test():
    ok, message = webhooks.send_test()
    return jsonify({"ok": ok, "message": message})


@app.route("/api/health/lastfm-key", methods=["GET", "POST"])
def api_health_lastfm_key():
    ok, message = lastfm.check_api_key()
    return jsonify({"ok": ok, "message": message})


@app.route("/api/health/lastfm-cookie", methods=["GET", "POST"])
def api_health_lastfm_cookie():
    plugin = plugins.get_plugin("discovery", "lastfm")
    result = plugin.check() if plugin else None
    ok, message = result if result else (False, "Last.fm plugin unavailable.")
    return jsonify({"ok": ok, "message": message})


@app.route("/api/cache/stats")
def api_cache_stats():
    """Cache sizes split into kept (followed) vs. stale (unfollowed) bytes."""
    return jsonify(maintenance.cache_stats())


@app.route("/api/artwork/status")
def api_artwork_status():
    """How much of the followed library's artwork is already on disk."""
    return jsonify({**artcache.coverage(), **artcache.get_state()})


@app.route("/api/artwork/warm", methods=["POST"])
def api_artwork_warm():
    """Fetch every followed artist's image into the on-disk cache.

    Body: {"force": true} re-fetches images already cached, {"stop": true}
    halts a run.
    """
    payload = request.get_json(silent=True) or {}
    if payload.get("stop"):
        artcache.stop()
        return jsonify(artcache.get_state())
    started = artcache.start(force=bool(payload.get("force")))
    return jsonify({"started": started, **artcache.get_state()})


@app.route("/api/cache/purge", methods=["POST"])
def api_cache_purge():
    """Delete cached data for artists no longer followed. Returns bytes freed."""
    result = maintenance.purge_stale()
    # A purge is what leaves the file full of free pages; reclaim them when it
    # made a real dent (cheap: seconds at most, and only after a big delete).
    result["compact"] = db.compact(min_free_ratio=0.25)
    return jsonify(result)


@app.route("/api/db/compact", methods=["POST"])
def api_db_compact():
    """Checkpoint the WAL and VACUUM the database. Returns bytes freed."""
    return jsonify(db.compact())


BACKUP_SECTIONS = ("settings", "artists", "artwork")


@app.route("/api/backup")
def api_backup():
    """Download a ZIP backup. ?sections=settings,artists,artwork (default all).

    - settings: your settings
    - artists:  artist information (artists + tracked releases)
    - artwork:  the cached album-art/artist-image files on disk
    """
    requested = (request.args.get("sections") or "").strip()
    sections = [s for s in requested.split(",") if s in BACKUP_SECTIONS] or list(BACKUP_SECTIONS)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({
            "version": db.BACKUP_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "sections": sections,
        }))
        if "settings" in sections:
            z.writestr("settings.json", json.dumps(db.export_settings()))
        if "artists" in sections:
            z.writestr("artists.json", json.dumps(db.export_artists()))
        if "artwork" in sections:
            art = artwork.art_dir()
            for name in os.listdir(art):
                path = os.path.join(art, name)
                if os.path.isfile(path) and not name.endswith(".part"):
                    z.write(path, "artwork/" + name)
    mem.seek(0)
    return send_file(
        mem, mimetype="application/zip", as_attachment=True,
        download_name=f"smt-backup-{date.today().isoformat()}.zip",
    )


@app.route("/api/import", methods=["POST"])
def api_import():
    """Restore from a backup. Accepts the new ZIP format or a legacy JSON file.

    Only the sections present in the file AND requested are restored (settings
    upserted, artist information replaced, artwork files written to disk).
    ``sections`` (form field or query, comma list) filters what's applied;
    omit it to restore every section the file contains.
    """
    requested = (request.values.get("sections") or "").strip()
    allowed = {s for s in requested.split(",") if s in BACKUP_SECTIONS} if requested else None

    def want(section):
        return allowed is None or section in allowed

    f = request.files.get("file")
    raw = f.read() if f is not None else None
    if raw is None:
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "no backup file provided"}), 400
        raw = json.dumps(payload).encode("utf-8")

    result = {}
    if zipfile.is_zipfile(io.BytesIO(raw)):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = set(z.namelist())
                if want("settings") and "settings.json" in names:
                    result["settings"] = db.import_settings(json.loads(z.read("settings.json")))
                if want("artists") and "artists.json" in names:
                    info = json.loads(z.read("artists.json"))
                    result.update(db.import_artists(info.get("artists"), info.get("releases")))
                art_names = [n for n in names if n.startswith("artwork/") and not n.endswith("/")]
                if want("artwork") and art_names:
                    art = artwork.art_dir()
                    for n in art_names:
                        base = os.path.basename(n)  # sha1 name; strips any path
                        if base:
                            with open(os.path.join(art, base), "wb") as out:
                                out.write(z.read(n))
                    result["artwork"] = len(art_names)
        except (ValueError, OSError, KeyError) as exc:
            return jsonify({"error": f"import failed: {exc}"}), 400
        return jsonify({"imported": result})

    # Legacy plain-JSON backup (settings + artist info only; no artwork).
    try:
        payload = json.loads(raw)
    except ValueError:
        return jsonify({"error": "could not parse the uploaded file"}), 400
    if not isinstance(payload, dict) or "artists" not in payload or "settings" not in payload:
        return jsonify({"error": "not a valid backup file"}), 400
    if want("settings"):
        result["settings"] = db.import_settings(payload.get("settings") or {})
    if want("artists"):
        result.update(db.import_artists(payload.get("artists") or [], payload.get("releases") or []))
    return jsonify({"imported": result})


# --- calendar feed ----------------------------------------------------------

@app.route("/api/calendar")
def api_calendar_info():
    """The subscribe URL for the release calendar (token generated on demand)."""
    return jsonify({
        "url": request.url_root.rstrip("/") + "/calendar/" + calendar_feed.token() + ".ics",
    })


@app.route("/api/calendar/reset", methods=["POST"])
def api_calendar_reset():
    """Issue a new token, breaking every existing subscription."""
    token = calendar_feed.reset_token()
    return jsonify({
        "url": request.url_root.rstrip("/") + "/calendar/" + token + ".ics",
    })


@app.route("/calendar/<token>.ics")
def calendar_ics(token):
    """iCalendar feed of upcoming releases for followed artists."""
    expected = calendar_feed.token(create=False)
    if not expected or not secrets.compare_digest(token, expected):
        abort(404)
    body = calendar_feed.build(base_url=request.url_root.rstrip("/"))
    return app.response_class(
        body,
        mimetype="text/calendar",
        headers={
            "Content-Disposition": 'inline; filename="releases.ics"',
            "Cache-Control": "public, max-age=1800",
        },
    )


@app.route("/api/system")
def api_system():
    """Version, environment and what the scheduler is going to do next.

    Feeds the settings page's Help & Info pane -- the things you want when
    something looks wrong and you're about to file a bug against yourself.
    """
    import platform
    import sqlite3 as _sqlite3
    import sys

    conn = db.get_connection()
    try:
        counts = {
            "artists": conn.execute("SELECT COUNT(*) c FROM artists").fetchone()["c"],
            "following": conn.execute(
                "SELECT COUNT(*) c FROM artists WHERE subscription IN "
                "('subscribed', 'notify')").fetchone()["c"],
            "releases": conn.execute("SELECT COUNT(*) c FROM releases").fetchone()["c"],
            "owned_albums": conn.execute("SELECT COUNT(*) c FROM owned_albums").fetchone()["c"],
        }
    finally:
        conn.close()

    # Owned albums come from several sources at once (a folder, a Plex server,
    # a Navidrome server); the totals mean more with them named.
    libraries = []
    for plugin in plugins.get_plugins("library"):
        if not plugin.configured():
            continue
        stats = db.library_stats(plugin.key)
        libraries.append({
            "key": plugin.key,
            "label": plugin.display_label(),
            "scan_interval_hours": plugin.scan_interval_hours(),
            "artists": stats["artists"],
            "albums": stats["albums"],
            "tracks": stats["tracks"],
            "last_scanned": stats["last_scanned"],
        })

    return jsonify({
        "version": __version__,
        "libraries": libraries,
        "python": sys.version.split()[0],
        "sqlite": _sqlite3.sqlite_version,
        "platform": platform.platform(),
        "database_path": os.path.abspath(db.DB_PATH),
        "database_bytes": db.db_size_bytes(),
        "artwork_path": os.path.abspath(artwork.art_dir()),
        "music_directory": db.get_setting("music_directory"),
        "timezone": time.strftime("%Z (UTC%z)"),
        "started_at": _STARTED_AT,
        "counts": counts,
        "tasks": scheduler.get_tasks(),
        "links": {
            "source": "https://github.com/jasii/Simple-Music-Tracker",
            "musicbrainz": "https://musicbrainz.org",
            "lastfm": "https://www.last.fm",
        },
    })


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


# Client-side router needs the page paths each endpoint maps to.
ENDPOINT_PATHS = {
    "artists_page": "/artists",
    "missing_page": "/missing",
    "subscriptions_page": "/subscriptions",
    "upcoming_page": "/upcoming",
    "discover_page": "/discover",
    "ignored_page": "/ignored",
    "settings_page": "/settings",
}


@app.route("/api/nav")
def api_nav():
    """Bootstrap config for the SPA: ordered nav items, home page, theme.

    Reuses normalize_nav_order so the navigation honours the same Settings
    (nav_order / nav_hidden / home) the server templates used to.
    """
    order = normalize_nav_order(db.get_setting("nav_order"))
    hidden_keys = set((db.get_setting("nav_hidden") or "").split(","))
    home_key = order[0] if order else DEFAULT_HOME
    items = [
        {
            "key": k,
            "endpoint": PAGE_DEFS[k][0],
            "label": PAGE_DEFS[k][1],
            "path": ENDPOINT_PATHS[PAGE_DEFS[k][0]],
            "hidden": k in hidden_keys and k != "settings" and k != home_key,
        }
        for k in order
    ]
    return jsonify({
        "items": items,
        "home": home_key,
        "home_path": ENDPOINT_PATHS[PAGE_DEFS[home_key][0]],
        "default_theme": db.get_setting("default_theme") or "dark",
        "hide_page_descriptions": (db.get_setting("hide_page_descriptions") or "false") == "true",
        "default_webhook_template": webhooks.DEFAULT_TEMPLATE,
    })


# --- PWA assets -------------------------------------------------------------

@app.route("/manifest.webmanifest")
def manifest():
    return app.send_static_file("manifest.webmanifest")


@app.route("/sw.js")
def service_worker():
    # Served from root scope so it can control the whole app.
    response = app.send_static_file("sw.js")
    response.headers["Cache-Control"] = "no-cache"
    return response


# --- React single-page app --------------------------------------------------

SPA_DIR = os.path.join(app.static_folder, "spa")


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_spa(path):
    """Serve the built React app for every non-API route.

    The client-side router (React Router) handles /artists, /artist/<id>,
    /album, /settings, etc. API calls (/api/*) and other explicit routes are
    matched first by Werkzeug, so they never reach this catch-all.
    """
    if path.startswith("api/"):
        abort(404)
    index = os.path.join(SPA_DIR, "index.html")
    if not os.path.exists(index):
        return (
            "React app not built. Run `npm install && npm run build` in frontend/.",
            503,
        )
    return send_file(index)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    # Every interface by default (this is a self-hosted app on a home network);
    # HOST=127.0.0.1 keeps it to this machine.
    host = os.environ.get("HOST") or "0.0.0.0"
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes", "on")
    app.run(host=host, port=port, debug=debug)
