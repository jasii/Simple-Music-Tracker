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
