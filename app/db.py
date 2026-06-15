"""SQLite helpers and schema for Simple Music Tracker.

No ORM, just the stdlib sqlite3 module. A single connection per request is
created on demand and stored on Flask's application context.
"""

import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("SMT_DB_PATH", os.path.join("data", "tracker.db"))

# A module level lock keeps writes from different background threads from
# stepping on each other. SQLite handles concurrency at the file level, but
# serialising writes avoids "database is locked" errors during scans.
_write_lock = threading.Lock()


SCHEMA = """
CREATE TABLE IF NOT EXISTS artists (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    sort_name     TEXT NOT NULL,
    mbid          TEXT,
    lastfm_url    TEXT,
    image_url     TEXT,
    bio           TEXT,
    -- 'none' | 'subscribed' | 'notify'
    subscription  TEXT NOT NULL DEFAULT 'none',
    -- comma separated subset of 'album,ep,single' to watch for this artist
    monitor_types TEXT NOT NULL DEFAULT 'album,ep',
    -- 1 = hidden from the main library list (artist parked in the Ignored area)
    ignored       INTEGER NOT NULL DEFAULT 0,
    track_count   INTEGER NOT NULL DEFAULT 0,
    last_checked  TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_artists_sort_name ON artists (sort_name);
CREATE INDEX IF NOT EXISTS idx_artists_subscription ON artists (subscription);

CREATE TABLE IF NOT EXISTS releases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id     INTEGER NOT NULL REFERENCES artists (id) ON DELETE CASCADE,
    mbid          TEXT,
    title         TEXT NOT NULL,
    release_date  TEXT,            -- ISO date 'YYYY-MM-DD' (may be partial)
    primary_type  TEXT,            -- Album | EP | Single | ...
    image_url     TEXT,
    notified      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_releases_artist_mbid
    ON releases (artist_id, mbid);
CREATE INDEX IF NOT EXISTS idx_releases_date ON releases (release_date);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Persisted Discover scrape results, one row per source ('lastfm', 'metacritic'),
-- so releases survive a restart and only re-scrape when stale or forced.
CREATE TABLE IF NOT EXISTS discover_cache (
    source      TEXT PRIMARY KEY,
    fetched_at  REAL NOT NULL,      -- epoch seconds of the last successful scrape
    payload     TEXT NOT NULL       -- JSON array of release items
);

-- Generic JSON cache for expensive external lookups (MusicBrainz discographies,
-- album detail / tracklists) so they survive restarts and aren't re-fetched on
-- every page view. Keyed by an arbitrary string; callers own the TTL.
CREATE TABLE IF NOT EXISTS json_cache (
    cache_key   TEXT PRIMARY KEY,
    fetched_at  REAL NOT NULL,      -- epoch seconds of the last write
    payload     TEXT NOT NULL       -- arbitrary JSON value
);

CREATE TABLE IF NOT EXISTS scan_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status      TEXT,             -- running | done | error
    files_seen  INTEGER DEFAULT 0,
    artists_found INTEGER DEFAULT 0,
    message     TEXT
);
"""


DEFAULT_SETTINGS = {
    "music_directory": "/music",
    "lastfm_api_key": "",
    "lastfm_cookie": "",            # session cookie for scraping login-only Last.fm pages
    "discover_refresh_hours": "24", # how often the Discover scrape is refreshed
    "discover_lastfm_enabled": "true",      # show the Last.fm source on Discover
    "discover_metacritic_enabled": "true",  # show the Metacritic source on Discover
    "webhook_url": "",
    "webhook_method": "POST",
    "webhook_headers": "",          # JSON object, one per line "Key: Value" also accepted
    "webhook_template": "",         # JSON body template, blank = built-in default
    # When a 'notify' webhook fires: 'discovery' = as soon as a release is found;
    # 'before_release' = webhook_lead_value/unit before the release date.
    "webhook_trigger": "discovery",
    "webhook_lead_value": "0",
    "webhook_lead_unit": "days",     # hours | days | weeks
    "check_interval_hours": "12",
    "artist_refresh_timeout": "180",  # max seconds to spend on one artist refresh
    "default_theme": "dark",        # 'dark' (amoled) | 'light'
    "musicbrainz_contact": "",      # email/url used in the MusicBrainz User-Agent
    "default_monitor_types": "album,ep",   # applied to newly followed artists
    "musicbrainz_rate_limit_ms": "1000",   # min gap between MusicBrainz requests (matches aurral)
    "discography_autohide": "",             # categories collapsed by default on artist pages
    "home_page": "upcoming",                # which page '/' opens (Upcoming on first run)
    "nav_order": "artists,following,upcoming,discover,ignored,settings",
    "nav_hidden": "",                       # comma separated list of hidden nav pages
    "prefer_album_artist": "true",          # use the album-artist tag before the track artist
}

# Release types that may be monitored. Order is the display order.
MONITOR_TYPE_OPTIONS = ["album", "ep", "single"]


def _migrate(conn):
    """Apply lightweight, idempotent schema migrations for existing databases."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(artists)")}
    if "monitor_types" not in cols:
        conn.execute(
            "ALTER TABLE artists ADD COLUMN monitor_types TEXT NOT NULL "
            "DEFAULT 'album,ep'"
        )
    if "ignored" not in cols:
        conn.execute(
            "ALTER TABLE artists ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0"
        )
    # Index created here (not in SCHEMA) so it runs after the column exists on
    # databases created before the column was added.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_artists_ignored ON artists (ignored)"
    )


def clean_types(value):
    """Return an ordered, validated subset of the type options (may be empty)."""
    if isinstance(value, str):
        items = [v.strip().lower() for v in value.split(",")]
    else:
        items = [str(v).strip().lower() for v in (value or [])]
    return [t for t in MONITOR_TYPE_OPTIONS if t in items]


def normalize_monitor_types(value):
    """Return a clean, ordered comma string from a list or comma string."""
    chosen = clean_types(value)
    # Never allow an empty selection -- fall back to the global default.
    if not chosen:
        chosen = [t for t in MONITOR_TYPE_OPTIONS
                  if t in DEFAULT_SETTINGS["default_monitor_types"].split(",")]
    return ",".join(chosen) or "album,ep"


def get_connection():
    """Return a new connection with sensible pragmas and row factory."""
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        # Seed any missing default settings without clobbering existing values.
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


# --- settings helpers -------------------------------------------------------

def get_setting(key, default=None):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return DEFAULT_SETTINGS.get(key, default)
    return row["value"]


def get_all_settings():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()
    settings = dict(DEFAULT_SETTINGS)
    settings.update({r["key"]: r["value"] for r in rows})
    return settings


def set_setting(key, value):
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()


# --- discover cache ---------------------------------------------------------

def get_discover_cache(source):
    """Return (fetched_at, items) for a source, or (None, []) if absent/corrupt."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT fetched_at, payload FROM discover_cache WHERE source = ?",
            (source,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None, []
    try:
        items = json.loads(row["payload"])
    except (TypeError, ValueError):
        return None, []
    return row["fetched_at"], items


def set_discover_cache(source, items):
    """Store a source's scrape result, stamped with the current time."""
    payload = json.dumps(items)
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO discover_cache (source, fetched_at, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(source) DO UPDATE SET "
                "fetched_at = excluded.fetched_at, payload = excluded.payload",
                (source, time.time(), payload),
            )
            conn.commit()
        finally:
            conn.close()


# --- generic JSON cache -----------------------------------------------------

def get_json_cache(key, max_age=None):
    """Return the cached value for *key*, or None if absent/stale/corrupt.

    *max_age* (seconds) lets the caller treat anything older as a miss.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT fetched_at, payload FROM json_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    if max_age is not None and (time.time() - row["fetched_at"]) > max_age:
        return None
    try:
        return json.loads(row["payload"])
    except (TypeError, ValueError):
        return None


def set_json_cache(key, value):
    """Store *value* (JSON-serialisable) under *key*, stamped now."""
    payload = json.dumps(value)
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO json_cache (cache_key, fetched_at, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET "
                "fetched_at = excluded.fetched_at, payload = excluded.payload",
                (key, time.time(), payload),
            )
            conn.commit()
        finally:
            conn.close()


def delete_json_cache(key):
    """Drop a cached entry so the next read re-fetches."""
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM json_cache WHERE cache_key = ?", (key,))
            conn.commit()
        finally:
            conn.close()


# --- backup / restore -------------------------------------------------------

BACKUP_VERSION = 1

_ARTIST_COLUMNS = [
    "id", "name", "sort_name", "mbid", "lastfm_url", "image_url", "bio",
    "subscription", "monitor_types", "ignored", "track_count", "last_checked",
    "created_at",
]
_RELEASE_COLUMNS = [
    "id", "artist_id", "mbid", "title", "release_date", "primary_type",
    "image_url", "notified", "created_at",
]


def _rows(conn, sql):
    return [{k: r[k] for k in r.keys()} for r in conn.execute(sql)]


def export_data():
    """Return a JSON-serialisable snapshot of settings, artists and releases."""
    from datetime import datetime, timezone
    conn = get_connection()
    try:
        settings = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
        return {
            "version": BACKUP_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "settings": settings,
            "artists": _rows(conn, "SELECT * FROM artists"),
            "releases": _rows(conn, "SELECT * FROM releases"),
        }
    finally:
        conn.close()


def _insert_row(conn, table, columns, row):
    cols = [c for c in columns if c in row]
    placeholders = ",".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
        [row[c] for c in cols],
    )


def export_settings():
    """Raw stored settings (the 'settings' backup section)."""
    conn = get_connection()
    try:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    finally:
        conn.close()


def export_artists():
    """Artists + their tracked releases (the 'artist information' section)."""
    conn = get_connection()
    try:
        return {
            "artists": _rows(conn, "SELECT * FROM artists"),
            "releases": _rows(conn, "SELECT * FROM releases"),
        }
    finally:
        conn.close()


def import_settings(settings):
    """Upsert settings from a backup. Returns how many keys were written."""
    settings = settings or {}
    with _write_lock:
        conn = get_connection()
        try:
            for key, value in settings.items():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(key), None if value is None else str(value)),
                )
            conn.commit()
        finally:
            conn.close()
    return len(settings)


def import_artists(artists, releases):
    """Replace artists + releases from a backup. Returns counts."""
    artists = artists or []
    releases = releases or []
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM releases")
            conn.execute("DELETE FROM artists")
            for artist in artists:
                _insert_row(conn, "artists", _ARTIST_COLUMNS, artist)
            for release in releases:
                _insert_row(conn, "releases", _RELEASE_COLUMNS, release)
            conn.commit()
        finally:
            conn.close()
    return {"artists": len(artists), "releases": len(releases)}


def import_data(data):
    """Replace all data with a previously exported (legacy JSON) snapshot.

    Returns counts. Raises ValueError if the payload is not a valid backup.
    """
    if not isinstance(data, dict) or "artists" not in data or "settings" not in data:
        raise ValueError("not a valid backup file")
    n_settings = import_settings(data.get("settings") or {})
    counts = import_artists(data.get("artists") or [], data.get("releases") or [])
    return {"settings": n_settings, **counts}
