"""Flask application: pages and JSON API for Simple Music Tracker."""

import io
import json
import os
import sqlite3
import threading
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from . import (
    album as album_detail,
    artwork,
    db,
    lastfm,
    maintenance,
    lastfm_scrape,
    metacritic_scrape,
    musicbrainz,
    scanner,
    scheduler,
    tracker,
    webhooks,
)

app = Flask(__name__)

# Initialise database and start the background scheduler at import time so it
# works under any WSGI server as well as the built-in dev server.
db.init_db()
tracker.start_worker()
scheduler.start()

VALID_STATES = {"none", "subscribed", "notify"}


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
    return results


# --- navigation -------------------------------------------------------------

# key -> (endpoint, label). The key is also the value stored in settings.
PAGE_DEFS = {
    "artists": ("artists_page", "Artists"),
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


def _home_key():
    # The home page is simply whichever tab is first in the navigation order.
    order = normalize_nav_order(db.get_setting("nav_order"))
    return order[0] if order else DEFAULT_HOME


@app.context_processor
def inject_nav():
    """Make the (ordered) nav items and labels available to every template."""
    order = normalize_nav_order(db.get_setting("nav_order"))
    hidden_keys = set((db.get_setting("nav_hidden") or "").split(","))
    # The home page (first tab) and Settings can never be hidden.
    home_key = order[0] if order else None
    items = [
        {"key": k, "endpoint": PAGE_DEFS[k][0], "label": PAGE_DEFS[k][1],
         "hidden": k in hidden_keys and k != "settings" and k != home_key}
        for k in order
    ]
    return {"nav_items": items}


# --- pages ------------------------------------------------------------------

@app.route("/")
def home():
    # The home page is configurable; send the user to their chosen page.
    return redirect(url_for(PAGE_DEFS[_home_key()][0]))


@app.route("/artists")
def artists_page():
    return render_template("index.html", **_base_context(active="artists"))


@app.route("/subscriptions")
def subscriptions_page():
    return render_template("following.html", **_base_context(active="following"))


@app.route("/upcoming")
def upcoming_page():
    return render_template("upcoming.html", **_base_context(active="upcoming"))


@app.route("/discover")
def discover_page():
    return render_template("discover.html", **_base_context(active="discover"))


@app.route("/ignored")
def ignored_page():
    return render_template("ignored.html", **_base_context(active="ignored"))


@app.route("/artist/<int:artist_id>")
def artist_page(artist_id):
    conn = db.get_connection()
    try:
        artist = conn.execute(
            "SELECT * FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
    finally:
        conn.close()
    if artist is None:
        abort(404)
    ctx = _base_context(active="artists")
    ctx["artist"] = _row_to_dict(artist)
    # Strip Last.fm's trailing "Read more" link from bios stored before this fix.
    ctx["artist"]["bio"] = lastfm.clean_bio(ctx["artist"].get("bio"))
    ctx["autohide"] = db.get_setting("discography_autohide") or ""
    return render_template("artist.html", **ctx)


@app.route("/art")
def art_proxy():
    """Serve cached album art from disk, fetching + saving it on first request.

    Falls back to redirecting to the source URL only when the bytes aren't on
    disk and can't be downloaded.
    """
    url = request.args.get("u")
    if not url:
        abort(404)
    path = artwork.cached_path(url)
    # Only download + save new images when caching is enabled; otherwise serve
    # anything already cached and fall back to the remote URL for the rest.
    if not path and (db.get_setting("cache_images") or "true") != "false":
        path = artwork.fetch(url)
    if path:
        resp = send_file(path, mimetype=artwork.content_type(path))
        resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp
    return redirect(url)


@app.route("/album")
def album_page():
    """Detail page for one release: title, tracks, and audio previews.

    Keyed by query params (artist, title, optional release-group mbid) since
    discovered releases aren't always in the library. The shell renders fast;
    album.js fetches the tracklist from /api/album.
    """
    artist = (request.args.get("artist") or "").strip()
    title = (request.args.get("title") or "").strip()
    if not artist or not title:
        abort(404)
    # Back link returns to wherever the album was opened from (upcoming default).
    origin = request.args.get("from")
    artist_id = request.args.get("artist_id")
    if origin == "discover":
        back_url, back_label, active = url_for("discover_page"), "Discover", "discover"
    elif origin == "artist" and (artist_id or "").isdigit():
        back_url, back_label, active = url_for("artist_page", artist_id=int(artist_id)), artist, "artists"
    else:
        back_url, back_label, active = url_for("upcoming_page"), "Upcoming", "upcoming"
    ctx = _base_context(active=active)
    ctx["album_artist"] = artist
    ctx["album_title"] = title
    ctx["album_mbid"] = (request.args.get("mbid") or "").strip()
    ctx["back_url"] = back_url
    ctx["back_label"] = back_label
    return render_template("album.html", **ctx)


@app.route("/api/album")
def api_album():
    """Tracklist + previews for a release. Params: artist, title, mbid?, refresh?."""
    artist = (request.args.get("artist") or "").strip()
    title = (request.args.get("title") or "").strip()
    if not artist or not title:
        return jsonify({"error": "artist and title are required"}), 400
    data = dict(album_detail.get_album_detail(
        artist, title,
        mbid=(request.args.get("mbid") or "").strip() or None,
        force=request.args.get("refresh") == "1",
    ))
    # Live (uncached) library status for this artist, so the page can show a
    # follow toggle and link the name to the artist page when it's tracked.
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT id, subscription FROM artists WHERE sort_name = ?",
            (artist.lower(),),
        ).fetchone()
    finally:
        conn.close()
    data["artist_id"] = row["id"] if row else None
    data["following"] = bool(row and row["subscription"] in ("subscribed", "notify"))
    return jsonify(data)


@app.route("/settings")
def settings_page():
    ctx = _base_context(active="settings")
    ctx["settings"] = db.get_all_settings()
    ctx["default_webhook_template"] = webhooks.DEFAULT_TEMPLATE
    return render_template("settings.html", **ctx)


def _base_context(active=""):
    return {
        "active": active,
        "default_theme": db.get_setting("default_theme") or "dark",
        "hide_page_descriptions": (db.get_setting("hide_page_descriptions") or "false") == "true",
    }


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

    sql = "SELECT * FROM artists"
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
            cur = conn.execute(
                "UPDATE artists SET subscription = ? WHERE id = ?",
                (state, artist_id),
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

    Body: {"types": ["album", "ep", "single"]} (any non-empty subset).
    Releases of types no longer monitored are dropped, then a refresh is queued.
    """
    payload = request.get_json(silent=True) or {}
    types = payload.get("types")
    if types is None:
        types = request.form.getlist("types")
    monitor_types = db.normalize_monitor_types(types)
    kept = monitor_types.split(",")

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
                placeholders = ",".join("?" for _ in labels)
                conn.execute(
                    f"DELETE FROM releases WHERE artist_id = ? "
                    f"AND primary_type NOT IN ({placeholders})",
                    [artist_id, *labels],
                )
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
                conn.execute(
                    "UPDATE artists SET track_count = track_count + ? WHERE id = ?",
                    (source["track_count"] or 0, artist_id),
                )
                if not target_mbid and source["mbid"]:
                    target_mbid = source["mbid"]
                conn.execute("DELETE FROM artists WHERE id = ?", (sid,))
                merged += 1

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

    groups = {"album": [], "ep": [], "single": []}
    label_to_key = {"Album": "album", "EP": "ep", "Single": "single"}
    for item in items:
        key = label_to_key.get(item["primary_type"])
        if key:
            groups[key].append(item)
    for key in groups:
        groups[key].sort(key=lambda r: r["release_date"] or "", reverse=True)

    return jsonify({"mbid": mbid, "groups": groups,
                    "counts": {k: len(v) for k, v in groups.items()}})


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


# Registry of new-release discovery sources. Each has a label, a check for
# whether it's set up, and a fetch returning (items, cached). More can be added
# here later (other sites/feeds) and they'll appear on the Discover page.
def _enabled(setting):
    return (db.get_setting(setting) or "true").lower() not in ("false", "0", "off", "no", "")


DISCOVER_SOURCES = {
    "lastfm": {
        "label": "Last.fm",
        # Toggled on in Settings and a session cookie is configured.
        "configured": lambda: _enabled("discover_lastfm_enabled")
        and bool((db.get_setting("lastfm_cookie") or "").strip()),
        "fetch": lastfm_scrape.fetch_coming_soon,
    },
    "metacritic": {
        "label": "Metacritic",
        # Public page - no auth needed; just the Settings toggle.
        "configured": lambda: _enabled("discover_metacritic_enabled"),
        "fetch": metacritic_scrape.fetch_coming_soon,
    },
}


def _flag_known_artists(items):
    """Annotate each item with in_library / following flags."""
    names = {(it.get("artist") or "").lower() for it in items if it.get("artist")}
    if not names:
        return
    conn = db.get_connection()
    try:
        placeholders = ",".join("?" for _ in names)
        rows = conn.execute(
            f"SELECT sort_name, subscription FROM artists "
            f"WHERE sort_name IN ({placeholders})",
            list(names),
        ).fetchall()
        known = {r["sort_name"]: r["subscription"] for r in rows}
    finally:
        conn.close()
    for it in items:
        sub = known.get((it.get("artist") or "").lower())
        it["in_library"] = sub is not None
        it["following"] = sub in ("subscribed", "notify")


# Background Discover refreshes. Scraping (especially Metacritic, which enriches
# every release via MusicBrainz at 1 req/sec) takes minutes, so it must never run
# inside a request - that would hang the page. Instead we serve whatever is in the
# DB cache immediately and refresh in a daemon thread; the page polls until done.
_discover_jobs = {}     # source key -> running Thread
_discover_errors = {}   # source key -> last error string (or None)
_discover_jobs_lock = threading.Lock()


def _discover_ttl_seconds():
    try:
        return max(float(db.get_setting("discover_refresh_hours") or 24), 1) * 3600
    except (TypeError, ValueError):
        return 24 * 3600


def _is_refreshing(key):
    job = _discover_jobs.get(key)
    return job is not None and job.is_alive()


def _kick_refresh(key, src):
    """Start a background scrape for *src* unless one is already running."""
    with _discover_jobs_lock:
        if _is_refreshing(key):
            return

        def worker():
            try:
                src["fetch"](force=True)
                _discover_errors[key] = None
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI instead
                _discover_errors[key] = str(exc)

        thread = threading.Thread(target=worker, daemon=True)
        _discover_jobs[key] = thread
        thread.start()


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
        for field in ("image", "context", "mbid", "artist_url", "album_url"):
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
        {"key": key, "label": src["label"], "configured": src["configured"]()}
        for key, src in DISCOVER_SOURCES.items()
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
    for key, src in DISCOVER_SOURCES.items():
        entry = {"key": key, "label": src["label"], "configured": src["configured"](),
                 "count": 0, "error": _discover_errors.get(key)}
        if entry["configured"]:
            fetched_at, cached_items = db.get_discover_cache(key)
            stale = (not fetched_at) or (not cached_items) or (time.time() - fetched_at > ttl)
            if refresh_all or refresh == key or stale:
                _kick_refresh(key, src)
            entry["count"] = len(cached_items)
            entry["fetched_at"] = fetched_at
            entry["stale"] = stale
            entry["refreshing"] = _is_refreshing(key)
            for it in cached_items:
                tagged = dict(it)
                tagged["source"] = key
                tagged["source_label"] = src["label"]
                items.append(tagged)
        sources.append(entry)

    items = _merge_discover_items(items)
    _flag_known_artists(items)
    items.sort(key=lambda r: r.get("normalized_date") or "9999")
    return jsonify({
        "sources": sources,
        "count": len(items),
        "items": items,
        "refreshing": any(s.get("refreshing") for s in sources),
    })


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
                f"UPDATE artists SET subscription = ? WHERE id IN ({placeholders})",
                [state, *ids],
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
            "SELECT * FROM artists WHERE ignored = 1 ORDER BY sort_name"
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"artists": [_row_to_dict(r) for r in rows]})


@app.route("/api/subscriptions")
def api_subscriptions():
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM artists WHERE subscription IN ('subscribed', 'notify') "
            "ORDER BY sort_name"
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
    return jsonify({
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(items),
        "releases": items,
    })


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Start a library scan. Body: {"quick": true} for an incremental sync."""
    directory = db.get_setting("music_directory")
    payload = request.get_json(silent=True) or {}
    quick = bool(payload.get("quick"))
    state = scanner.get_scan_state()
    if state.get("running"):
        return jsonify({"error": "scan already running", "state": state}), 409
    scanner.scan_in_background(directory, quick=quick)
    return jsonify({"started": True, "directory": directory, "quick": quick})


@app.route("/api/scan/status")
def api_scan_status():
    return jsonify(scanner.get_scan_state())


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


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(db.get_all_settings())

    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form.to_dict()
    allowed = set(db.DEFAULT_SETTINGS.keys())
    updated = {}
    for key, value in payload.items():
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
                     "discover_metacritic_enabled", "hide_page_descriptions", "cache_images"):
            value = "true" if str(value).lower() in ("true", "1", "on", "yes") else "false"
        elif key == "discover_refresh_hours":
            try:
                value = str(max(int(float(value)), 1))
            except (TypeError, ValueError):
                value = "24"
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
    ok, message = lastfm_scrape.check_cookie()
    return jsonify({"ok": ok, "message": message})


@app.route("/api/cache/stats")
def api_cache_stats():
    """Cache sizes split into kept (followed) vs. stale (unfollowed) bytes."""
    return jsonify(maintenance.cache_stats())


@app.route("/api/cache/purge", methods=["POST"])
def api_cache_purge():
    """Delete cached data for artists no longer followed. Returns bytes freed."""
    return jsonify(maintenance.purge_stale())


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


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
