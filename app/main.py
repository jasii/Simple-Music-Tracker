"""Flask application: pages and JSON API for Simple Music Tracker."""

import os
from datetime import date, datetime, timedelta

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from . import db, musicbrainz, scanner, scheduler, tracker, webhooks

app = Flask(__name__)

# Initialise database and start the background scheduler at import time so it
# works under any WSGI server as well as the built-in dev server.
db.init_db()
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


# --- pages ------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", **_base_context(active="artists"))


@app.route("/subscriptions")
def subscriptions_page():
    return render_template("subscriptions.html", **_base_context(active="subscriptions"))


@app.route("/upcoming")
def upcoming_page():
    return render_template("upcoming.html", **_base_context(active="upcoming"))


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
    return render_template("artist.html", **ctx)


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
    }


# --- JSON API ---------------------------------------------------------------

@app.route("/api/stats")
def api_stats():
    conn = db.get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) c FROM artists").fetchone()["c"]
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
        tracker.refresh_artist_in_background(artist_id)

    return jsonify({"id": artist_id, "subscription": state})


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
                    "UPDATE artists SET subscription = ?, "
                    "mbid = COALESCE(mbid, ?) WHERE id = ?",
                    (state, mbid, artist_id),
                )
                created = False
            else:
                cur = conn.execute(
                    "INSERT INTO artists (name, sort_name, mbid, subscription, track_count) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (info["name"], info["name"].lower(), mbid, state),
                )
                artist_id = cur.lastrowid
                created = True
            conn.commit()
        finally:
            conn.close()

    tracker.refresh_artist_in_background(artist_id)
    return jsonify(
        {
            "id": artist_id,
            "name": info["name"],
            "mbid": mbid,
            "subscription": state,
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
            tracker.refresh_artist_in_background(artist_id)

    return jsonify({"updated": len(ids), "state": state})


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


@app.route("/api/scan", methods=["POST"])
def api_scan():
    directory = db.get_setting("music_directory")
    state = scanner.get_scan_state()
    if state.get("running"):
        return jsonify({"error": "scan already running", "state": state}), 409
    scanner.scan_in_background(directory)
    return jsonify({"started": True, "directory": directory})


@app.route("/api/scan/status")
def api_scan_status():
    return jsonify(scanner.get_scan_state())


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    state = tracker.get_refresh_state()
    if state.get("running"):
        return jsonify({"error": "refresh already running", "state": state}), 409
    tracker.refresh_all_in_background()
    return jsonify({"started": True})


@app.route("/api/artists/<int:artist_id>/refresh", methods=["POST"])
def api_refresh_artist(artist_id):
    tracker.refresh_artist_in_background(artist_id)
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
        if key in allowed:
            db.set_setting(key, str(value))
            updated[key] = value
    return jsonify({"updated": updated})


@app.route("/api/webhook/test", methods=["POST"])
def api_webhook_test():
    ok, message = webhooks.send_test()
    return jsonify({"ok": ok, "message": message})


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
