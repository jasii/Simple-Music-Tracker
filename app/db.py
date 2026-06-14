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
}


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
