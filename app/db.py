"""SQLite helpers and schema for Simple Music Tracker.

No ORM, just the stdlib sqlite3 module. A single connection per request is
created on demand and stored on Flask's application context.
"""

import os
import sqlite3
import threading

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
    "webhook_url": "",
    "webhook_method": "POST",
    "webhook_headers": "",          # JSON object, one per line "Key: Value" also accepted
    "webhook_template": "",         # JSON body template, blank = built-in default
    "check_interval_hours": "12",
    "default_theme": "dark",        # 'dark' (amoled) | 'light'
    "musicbrainz_contact": "",      # email/url used in the MusicBrainz User-Agent
    "default_monitor_types": "album,ep",   # applied to newly followed artists
    "musicbrainz_rate_limit_ms": "1000",   # min gap between MusicBrainz requests (matches aurral)
    "discography_autohide": "",             # categories collapsed by default on artist pages
    "home_page": "upcoming",                # which page '/' opens (Upcoming on first run)
    "nav_order": "artists,following,upcoming,ignored,settings",
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


def import_data(data):
    """Replace all data with a previously exported snapshot.

    Returns counts. Raises ValueError if the payload is not a valid backup.
    """
    if not isinstance(data, dict) or "artists" not in data or "settings" not in data:
        raise ValueError("not a valid backup file")

    settings = data.get("settings") or {}
    artists = data.get("artists") or []
    releases = data.get("releases") or []

    def _insert(conn, table, columns, row):
        cols = [c for c in columns if c in row]
        placeholders = ",".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
            [row[c] for c in cols],
        )

    with _write_lock:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM releases")
            conn.execute("DELETE FROM artists")
            for key, value in settings.items():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(key), None if value is None else str(value)),
                )
            for artist in artists:
                _insert(conn, "artists", _ARTIST_COLUMNS, artist)
            for release in releases:
                _insert(conn, "releases", _RELEASE_COLUMNS, release)
            conn.commit()
        finally:
            conn.close()

    return {
        "settings": len(settings),
        "artists": len(artists),
        "releases": len(releases),
    }
