"""SQLite helpers and schema for Simple Music Tracker.

No ORM, just the stdlib sqlite3 module. A single connection per request is
created on demand and stored on Flask's application context.
"""

import json
import os
import sqlite3
import threading
import time
import unicodedata

DB_PATH = os.environ.get("SMT_DB_PATH", os.path.join("data", "tracker.db"))

# A module level lock keeps writes from different background threads from
# stepping on each other. SQLite handles concurrency at the file level, but
# serialising writes avoids "database is locked" errors during scans.
_write_lock = threading.Lock()


LIBRARY_GAPS_DDL = """
-- Materialised answer to "what's missing / incomplete", rebuilt from the
-- cached discographies. It lives in SQLite rather than in the process because
-- a 2500-artist library produces ~300k rows: filtering and paging them in SQL
-- costs nothing, while holding them as Python objects cost ~200MB per worker.
CREATE TABLE IF NOT EXISTS library_gaps (
    kind         TEXT NOT NULL,     -- 'missing' | 'incomplete'
    artist_key   TEXT NOT NULL,     -- db.match_key(artist), collapses spellings
    title_key    TEXT NOT NULL,
    artist_id    INTEGER,
    artist       TEXT,
    title        TEXT,
    type         TEXT,              -- album | ep | single
    release_date TEXT,
    mbid         TEXT,
    -- No cover URL: it's derived from the mbid on read (see gaps.filtered),
    -- which at ~300k rows is 28MB of file this table doesn't have to carry.
    owned_format TEXT,              -- incomplete only: the quality on disk
    secondary    TEXT,              -- MusicBrainz secondary types, comma list
    -- 1 when those secondary types mean "not a new record" (compilation, live,
    -- remix...). Decided once at build time so the list can filter on an index
    -- instead of parsing the string per row.
    is_secondary INTEGER NOT NULL DEFAULT 0,
    -- Incomplete only: tracks the library holds, and how many the release has.
    have_tracks  INTEGER,
    total_tracks INTEGER,
    PRIMARY KEY (kind, artist_key, title_key)
);
CREATE INDEX IF NOT EXISTS idx_library_gaps_date
    ON library_gaps (kind, is_secondary, release_date);
-- Covers the default listing: one kind, real releases only, artist order with
-- date as the tiebreak. Without the date in the index SQLite sorts 300k rows
-- through a temp B-tree for every page (200ms -> 0.1ms).
CREATE INDEX IF NOT EXISTS idx_library_gaps_artist
    ON library_gaps (kind, is_secondary, artist COLLATE NOCASE, release_date);
"""

SCHEMA = LIBRARY_GAPS_DDL + """
CREATE TABLE IF NOT EXISTS artists (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    sort_name     TEXT NOT NULL,
    mbid          TEXT,
    lastfm_url    TEXT,
    image_url     TEXT,
    bio           TEXT,
    -- comma separated genre tags kept with the artist (seeded when the artist
    -- is created from a Discover suggestion, so they outlive that cache)
    genres        TEXT,
    -- 1 = image_url was chosen by hand; refreshes and the artwork backfill
    -- leave it alone (see app/artistart.py)
    image_locked  INTEGER NOT NULL DEFAULT 0,
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

-- Accumulated history of every Discover release ever scraped, so past releases
-- stay visible for a configurable window after they drop out of a source's live
-- scrape. One row per (source, item identity); served by get_discover_history.
CREATE TABLE IF NOT EXISTS discover_history (
    source       TEXT NOT NULL,
    item_key     TEXT NOT NULL,     -- mbid, else 'artist|album' lowercased
    release_date TEXT,              -- normalized ISO date (YYYY-MM-DD) when known
    first_seen   REAL NOT NULL,
    last_seen    REAL NOT NULL,
    payload      TEXT NOT NULL,     -- JSON of the normalized item
    PRIMARY KEY (source, item_key)
);

-- Artists/albums the user never wants on the Discover page. album = '' hides
-- the whole artist; otherwise just that one release. Matched case-insensitively
-- against incoming discover items.
CREATE TABLE IF NOT EXISTS discover_ignores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    artist     TEXT NOT NULL,
    album      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_discover_ignores
    ON discover_ignores (artist COLLATE NOCASE, album COLLATE NOCASE);

-- Similar-artist suggestions seen while browsing artist pages (from Last.fm).
-- Aggregated to rank artists that are "similar to many of yours but
-- not owned" -- the ones most worth checking out.
CREATE TABLE IF NOT EXISTS similar_artists (
    source_artist TEXT NOT NULL,   -- local artist whose page produced the suggestion
    name          TEXT NOT NULL,   -- the suggested artist
    url           TEXT,            -- their page on the site that suggested them
    site          TEXT,            -- label of that site
    score         REAL,            -- the site's similarity score (higher = closer)
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source_artist COLLATE NOCASE, name COLLATE NOCASE)
);

-- Generic JSON cache for expensive external lookups (MusicBrainz discographies,
-- album detail / tracklists) so they survive restarts and aren't re-fetched on
-- every page view. Keyed by an arbitrary string; callers own the TTL.
CREATE TABLE IF NOT EXISTS json_cache (
    cache_key   TEXT PRIMARY KEY,
    fetched_at  REAL NOT NULL,      -- epoch seconds of the last write
    payload     TEXT NOT NULL       -- arbitrary JSON value
);

-- Manual matches between a library album and a MusicBrainz release group, for
-- the pairs no amount of title normalising will agree on: linked = 1 says
-- "these are the same record", linked = 0 says "they are not" and blocks the
-- loose title match that would otherwise pair them.
CREATE TABLE IF NOT EXISTS album_links (
    artist_id  INTEGER NOT NULL REFERENCES artists (id) ON DELETE CASCADE,
    album_key  TEXT NOT NULL,     -- owned_albums.album_key (the library's title)
    rg_mbid    TEXT NOT NULL,     -- the MusicBrainz release group
    linked     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (artist_id, album_key, rg_mbid)
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

-- Albums the user owns, marked by the library scan or manually on the artist
-- page. Matched against an artist's MusicBrainz discography by release-group
-- mbid first, then by normalized title. One row per (artist, album_key).
CREATE TABLE IF NOT EXISTS owned_albums (
    artist_id   INTEGER NOT NULL,
    album_key   TEXT NOT NULL,    -- lower(title), the fallback match key
    rg_mbid     TEXT,             -- MusicBrainz release-group id, when tagged
    title       TEXT,
    source      TEXT NOT NULL DEFAULT 'filesystem',  -- library key | 'manual'
    -- Best quality seen for this album ('FLAC', 'MP3 320', ...); drives the
    -- incomplete list and playback labels. NULL for sources that can't tell us.
    format      TEXT,
    -- Tracks of it this source holds, to spot a short album. NULL = unknown.
    track_count INTEGER,
    PRIMARY KEY (artist_id, album_key, source)
);
CREATE INDEX IF NOT EXISTS idx_owned_rg_mbid ON owned_albums (rg_mbid);

-- Merge suggestions the user has waved away. Keyed by the group, with a
-- signature of its members: if a fourth spelling of the same artist turns up,
-- the signature changes and the suggestion comes back.
CREATE TABLE IF NOT EXISTS merge_dismissals (
    group_key    TEXT PRIMARY KEY,
    signature    TEXT NOT NULL,
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Artists whose missing artwork the user has waved away, so the review tool
-- stops offering them. Cleared per artist or all at once.
CREATE TABLE IF NOT EXISTS artwork_dismissals (
    artist_id    INTEGER PRIMARY KEY,
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per release the grabber has tried, so the automatic pass doesn't
-- re-send what it already sent and doesn't retry a failure immediately.
CREATE TABLE IF NOT EXISTS grab_log (
    artist_id    INTEGER NOT NULL,
    album_key    TEXT NOT NULL,     -- db.owned_album_key(title)
    title        TEXT,
    status       TEXT NOT NULL,     -- sent | partial | failed
    detail       TEXT,
    attempted_at REAL NOT NULL,
    PRIMARY KEY (artist_id, album_key)
);
CREATE INDEX IF NOT EXISTS idx_grab_log_attempted ON grab_log (attempted_at);

-- Per-library track counts so a multi-source 'track_count' is a sum that never
-- double-counts on re-scan. artists.track_count is the SUM across sources.
CREATE TABLE IF NOT EXISTS artist_library_stats (
    artist_id    INTEGER NOT NULL,
    source       TEXT NOT NULL,            -- library key, e.g. 'filesystem' | 'subsonic'
    track_count  INTEGER NOT NULL DEFAULT 0,
    last_scanned REAL,                     -- epoch seconds of last write
    PRIMARY KEY (artist_id, source)
);

-- Folders where an artist's tracks were found during a scan, so a per-artist
-- rescan can walk just those directories instead of the whole library.
CREATE TABLE IF NOT EXISTS artist_folders (
    artist_id   INTEGER NOT NULL,
    folder      TEXT NOT NULL,
    PRIMARY KEY (artist_id, folder)
);

-- Downloads a searching client (slskd) was handed, followed until every file
-- has either landed or been given up on. Soulseek hands over files one by one from someone's home
-- connection, so an album can end 7/9 and nobody would know without this.
CREATE TABLE IF NOT EXISTS download_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id    INTEGER,
    artist       TEXT NOT NULL,
    title        TEXT NOT NULL,
    client       TEXT NOT NULL,     -- downloader plugin key
    kind         TEXT NOT NULL DEFAULT 'album',  -- album | tracks
    format       TEXT,              -- extension the job is fetching, e.g. 'flac'
    -- JSON list: {username, filename, size, track, state, attempts, error,
    -- percent, queued_at}. Files can come from different peers after a retry.
    files        TEXT NOT NULL DEFAULT '[]',
    -- JSON list of the other folders the search turned up, best first, kept
    -- so a failed track can be fetched from someone else without searching.
    alternates   TEXT NOT NULL DEFAULT '[]',
    -- searching | downloading | complete | partial | failed | cancelled
    status       TEXT NOT NULL,
    message      TEXT,
    -- Fresh searches spent on this job after its first one ran out of peers.
    researches   INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    finished_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_download_jobs_status ON download_jobs (status);
"""


DEFAULT_SETTINGS = {
    "music_directory": "/music",
    # Library sources (app/plugins/library). Filesystem on by default to preserve
    # existing behaviour; Subsonic/Navidrome off until configured.
    "library_filesystem_enabled": "true",
    "library_subsonic_enabled": "false",
    "subsonic_url": "",
    "subsonic_username": "",
    "subsonic_salt": "",       # random salt; token = md5(password + salt)
    "subsonic_token": "",      # stored instead of the password (never persisted)
    "library_plex_enabled": "false",
    "plex_url": "",
    "plex_token": "",          # X-Plex-Token, stored verbatim
    "plex_section": "",        # optional music section id; blank = all music libraries
    "lastfm_api_key": "",
    "lastfm_username": "",          # whose scrobbles feed the Discover tab
    "lastfm_cookie": "",            # session cookie for scraping login-only Last.fm pages
    "discover_refresh_hours": "24", # how often the Discover scrape is refreshed
    "discover_enrich_workers": "8", # parallel threads enriching Discover releases (art/genres)
    "discover_lastfm_enabled": "true",      # show the Last.fm source on Discover
    "discover_metacritic_enabled": "true",  # show the Metacritic source on Discover
    "discover_aoty_enabled": "true",        # albumoftheyear.org upcoming grid
    "aoty_cookie": "",                      # browser Cookie header (cf_clearance) for AOTY
    "aoty_user_agent": "",                  # UA of the browser that earned that cookie
    # Challenge solvers (app/plugins/solver): fetch pages bot protection blocks.
    "solver_flaresolverr_enabled": "false",
    "solver_flaresolverr_url": "",          # e.g. http://flaresolverr:8191
    "solver_flaresolverr_timeout": "60",    # seconds the browser may spend per page
    "discover_iing_enabled": "true",        # indieisnotagenre.com release list
    "discover_history_months": "3",         # keep past Discover releases for this many months
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
    # Tag EPs/singles with how many of their songs no album carries. Off means
    # the tracklists behind that count are never read.
    "show_unique_tags": "true",
    # Treat an EP or single as owned when every song on it is already in the
    # library on a record you own (usually the album it was pulled from).
    "own_covered_releases": "false",
    # The "Get Hyped" playlist: how far ahead it looks, and whether it rebuilds
    # itself weekly (day 0 = Monday, 4 = Friday, which is release day).
    "hype_playlist_days": "7",
    "hype_playlist_per_artist": "3",
    "hype_playlist_weekly": "false",
    "hype_playlist_day": "4",
    "hype_playlist_time": "08:00",
    "hype_playlist_last_run": "0",
    # Metadata sources (artist photos, bios, genre tags, album covers), in the
    # order they're asked -- one list per field, because the best source differs:
    # the Cover Art Archive has the release's own sleeve at 1200px while Last.fm
    # is the only one with a written biography. Empty = the order the plugins
    # registered in. The Metadata settings section writes these lists (see
    # app/plugins/metadata).
    "metadata_priority": "",
    "metadata_priority_artist_image": "",
    "metadata_priority_artist_bio": "",
    "metadata_priority_artist_genres": "",
    "metadata_priority_artist_similar": "lastfm",
    "metadata_priority_album_art": "coverartarchive,lastfm,deezer",
    "metadata_priority_album_description": "lastfm",
    "metadata_priority_album_tags": "lastfm",
    # iTunes has the widest catalogue of samples, Deezer fills its gaps, and
    # the Last.fm video is the last resort for a track nobody sells.
    "metadata_priority_track_preview": "itunes,deezer,lastfm",
    # Which of your own libraries streams a track when several hold it. Empty =
    # the order the library plugins registered in (see app/librarytrack.py).
    "library_playback_priority": "",
    "metadata_itunes_enabled": "true",
    "metadata_lastfm_enabled": "true",
    "metadata_deezer_enabled": "true",
    "metadata_coverartarchive_enabled": "true",
    "metadata_own_covers_enabled": "true",
    # How long scraped answers stay usable. 0 days = forever, which is the
    # point: browsing fills the database and pages stop going online at all.
    # Misses expire sooner (something MusicBrainz or Last.fm hasn't got yet may
    # appear later), library track matches sooner still (music gets deleted),
    # and your own scrobbles soonest (they change by the hour).
    "store_keep_days": "0",
    "store_miss_days": "7",
    "store_marker_days": "30",
    "store_scrobble_hours": "6",
    "home_page": "upcoming",                # which page '/' opens (Upcoming on first run)
    "nav_order": "artists,following,upcoming,discover,ignored,settings",
    "nav_hidden": "",                       # comma separated list of hidden nav pages
    "prefer_album_artist": "true",          # use the album-artist tag before the track artist
    "hide_page_descriptions": "false",      # hide the muted intro text on list pages
    "cache_images": "true",                 # save album art / artist images to disk
    "purge_cache_on_unfollow": "true",      # auto-delete an artist's cache when unfollowed
    # Notifiers (app/plugins/notifier). Each also stores one flag per event,
    # "<prefix>_<event>", written by the settings UI as it's toggled.
    "notifier_ntfy_enabled": "false",
    "notifier_ntfy_server": "https://ntfy.sh",
    "notifier_ntfy_topic": "",
    "notifier_ntfy_token": "",
    "notifier_gotify_enabled": "false",
    "notifier_gotify_server": "",
    "notifier_gotify_token": "",
    "notifier_gotify_priority": "5",
    "notifier_discord_enabled": "false",
    "notifier_discord_webhook": "",
    "notifier_discord_username": "",
    "downloader_slskd_enabled": "false",
    "downloader_slskd_url": "",             # e.g. http://localhost:5030
    "downloader_slskd_api_key": "",
    "downloader_slskd_username": "",        # only when no API key is set
    "downloader_slskd_password": "",
    "downloader_slskd_formats": "flac,mp3",
    "downloader_slskd_min_files": "2",
    "downloader_slskd_search_seconds": "20",
    # What happens when some of an album's files fail: take the missing tracks
    # from another peer's copy (same format only), and how long a file may sit
    # in someone's upload queue before another peer is tried instead.
    "downloader_slskd_mix_peers": "true",
    "downloader_slskd_stall_hours": "12",
    # Quality profile (app/quality): accepted qualities, best first.
    "quality_order": "FLAC 24bit,FLAC,MP3 320,MP3 V0",
    # Download clients in the order the grabber should try them.
    "downloader_priority": "",
    # Automatic grabbing of new releases (app/grabber).
    "autograb_enabled": "false",
    "autograb_scope": "notify",     # 'notify' artists only, or all 'following'
    "autograb_max_age_days": "45",  # ignore anything older, so enabling is safe
    "autograb_batch": "5",          # most releases one pass will grab
    "autograb_interval_minutes": "60",
    # Duplicate protection: don't send a release a download client already holds.
    "skip_in_client": "true",
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
    # Cached MusicBrainz discography counts per release type. NULL = not yet
    # fetched (filled in when an artist's discography is loaded; see
    # api_discography). Shown on the Artists table.
    for dcol in ("disc_albums", "disc_eps", "disc_singles"):
        if dcol not in cols:
            conn.execute(f"ALTER TABLE artists ADD COLUMN {dcol} INTEGER")
    # Owned subset of the discography per type (how many of disc_* the user has),
    # so the Artists list can show owned/total ("missing") without re-fetching.
    # NULL = not yet synced; filled alongside disc_* (see set_owned_counts).
    for ocol in ("owned_albums", "owned_eps", "owned_singles"):
        if ocol not in cols:
            conn.execute(f"ALTER TABLE artists ADD COLUMN {ocol} INTEGER")
    # The gaps table gained its precomputed "this is a reissue/live/comp" flag
    # after first shipping; rebuild fills it in.
    gap_cols = {row["name"] for row in conn.execute("PRAGMA table_info(library_gaps)")}
    if gap_cols and "image_url" in gap_cols:
        # Cheaper to drop and rebuild than to migrate: the table is a cache,
        # and gaps.rebuild() refills it from the discographies.
        conn.execute("DROP TABLE library_gaps")
        conn.executescript(LIBRARY_GAPS_DDL)
        conn.execute("DELETE FROM settings WHERE key = 'library_gaps_built_at'")
        gap_cols = {row["name"] for row in conn.execute("PRAGMA table_info(library_gaps)")}
    if gap_cols and "is_secondary" not in gap_cols:
        conn.execute(
            "ALTER TABLE library_gaps ADD COLUMN is_secondary INTEGER NOT NULL DEFAULT 0"
        )
    if gap_cols:
        # The artist index gained release_date; recreate it when it predates that.
        index_cols = [r["name"]
                      for r in conn.execute("PRAGMA index_info(idx_library_gaps_artist)")]
        if index_cols and "release_date" not in index_cols:
            conn.execute("DROP INDEX idx_library_gaps_artist")
    # Compilation credits that earlier scans turned into artists: unfollow and
    # park them once, so they stop filling Upcoming with records nobody made.
    # Guarded by a flag, so deliberately un-ignoring one afterwards sticks.
    parked = conn.execute(
        "SELECT value FROM settings WHERE key = 'non_artists_parked'"
    ).fetchone()
    if not parked:
        # Matched in Python: normalising a name in SQL would be a nest of
        # replace() calls, and this runs once.
        for row in conn.execute("SELECT id, name FROM artists").fetchall():
            if is_non_artist(row["name"]):
                conn.execute(
                    "UPDATE artists SET ignored = 1, subscription = 'none' "
                    "WHERE id = ?", (row["id"],),
                )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES "
            "('non_artists_parked', '1')"
        )
    # Quality of the owned copy, filled in by the filesystem scanner.
    owned_cols_now = {row["name"] for row in conn.execute("PRAGMA table_info(owned_albums)")}
    if owned_cols_now and "format" not in owned_cols_now:
        conn.execute("ALTER TABLE owned_albums ADD COLUMN format TEXT")
    # How many tracks of it the source holds, so a short album can be spotted.
    if owned_cols_now and "track_count" not in owned_cols_now:
        conn.execute("ALTER TABLE owned_albums ADD COLUMN track_count INTEGER")
    # Genre tags stored on the artist, seeded from a Discover suggestion.
    if "genres" not in cols:
        conn.execute("ALTER TABLE artists ADD COLUMN genres TEXT")
    gap_cols = {r["name"] for r in conn.execute("PRAGMA table_info(library_gaps)")}
    if gap_cols and "have_tracks" not in gap_cols:
        conn.execute("ALTER TABLE library_gaps ADD COLUMN have_tracks INTEGER")
        conn.execute("ALTER TABLE library_gaps ADD COLUMN total_tracks INTEGER")
    if gap_cols and "score" in gap_cols:
        # The upgrade list and its tracker formats are gone. The table is a
        # cache, so it's rebuilt in its new shape rather than altered.
        conn.execute("DROP TABLE library_gaps")
        conn.executescript(LIBRARY_GAPS_DDL)
        conn.execute("DELETE FROM settings WHERE key = 'library_gaps_built_at'")
    _remove_trackers(conn)
    # Hand-picked artwork, which no automatic source may overwrite.
    if "image_locked" not in cols:
        conn.execute(
            "ALTER TABLE artists ADD COLUMN image_locked INTEGER NOT NULL DEFAULT 0"
        )
    # Similarity score from the suggesting site (tables created before it existed).
    sim_cols = {row["name"] for row in conn.execute("PRAGMA table_info(similar_artists)")}
    if sim_cols and "score" not in sim_cols:
        conn.execute("ALTER TABLE similar_artists ADD COLUMN score REAL")
    # Index created here (not in SCHEMA) so it runs after the column exists on
    # databases created before the column was added.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_artists_ignored ON artists (ignored)"
    )
    # The suggestion ranking groups by lower(name); without this it sorts 20k+
    # rows through a temp B-tree on every Discover load.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_similar_name_lower "
        "ON similar_artists (lower(name))"
    )

    # owned_albums gained 'source' in its primary key (so multiple libraries can
    # each own an album). Rebuild the table when it still has the old 2-col PK,
    # mapping the legacy 'scan' source to the new 'filesystem' library key.
    owned_cols = conn.execute("PRAGMA table_info(owned_albums)").fetchall()
    src_col = next((c for c in owned_cols if c["name"] == "source"), None)
    if src_col is not None and src_col["pk"] == 0:
        conn.executescript(
            """
            CREATE TABLE owned_albums_new (
                artist_id   INTEGER NOT NULL,
                album_key   TEXT NOT NULL,
                rg_mbid     TEXT,
                title       TEXT,
                source      TEXT NOT NULL DEFAULT 'filesystem',
                PRIMARY KEY (artist_id, album_key, source)
            );
            INSERT OR IGNORE INTO owned_albums_new
                (artist_id, album_key, rg_mbid, title, source)
                SELECT artist_id, album_key, rg_mbid, title,
                       CASE WHEN source = 'scan' THEN 'filesystem' ELSE source END
                FROM owned_albums;
            DROP TABLE owned_albums;
            ALTER TABLE owned_albums_new RENAME TO owned_albums;
            CREATE INDEX IF NOT EXISTS idx_owned_rg_mbid ON owned_albums (rg_mbid);
            """
        )
        # Seed per-source stats from the existing aggregate so track counts
        # survive the migration until the next scan recomputes them.
        conn.execute(
            "INSERT OR IGNORE INTO artist_library_stats (artist_id, source, track_count) "
            "SELECT id, 'filesystem', track_count FROM artists WHERE track_count > 0"
        )


# The discovery sources that exist, so cached rows from any other can go.
_DISCOVERY_SOURCES = ("lastfm", "metacritic", "aoty", "iing")


def _remove_trackers(conn):
    """Clear out what the torrent trackers and torrent clients left behind.

    Once, guarded by a flag: their settings (API keys and passwords among
    them), the albums a tracker's snatch list marked as owned, their cached
    answers and the similar artists they suggested. Settings are matched by
    what still exists rather than by name: a plugin setting that isn't in
    DEFAULT_SETTINGS belonged to a plugin that's gone.
    """
    done = conn.execute(
        "SELECT value FROM settings WHERE key = 'trackers_removed'").fetchone()
    if done:
        return
    for (key,) in conn.execute("SELECT key FROM settings").fetchall():
        gone_plugin = key.startswith(("torrent_", "downloader_")) or (
            key.startswith(("metadata_", "discover_")) and key.endswith("_enabled"))
        if (gone_plugin or "snatched" in key) and key not in DEFAULT_SETTINGS:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    # The profile lost its cutoff and media ladder, grabbing its upgrade mode
    # and duplicate protection its snatch check.
    for key in ("quality_cutoff", "media_order", "media_strict", "autograb_upgrade",
                "skip_snatched", "collapse_snatched_torrents",
                "metadata_priority_album_details", "metadata_priority_album_credits"):
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    # The client order named torrent clients; with one client left there's
    # nothing to order, and an empty list means "as registered".
    conn.execute("UPDATE settings SET value = '' WHERE key = 'downloader_priority'")
    # The torrent-sent notification is now "grab sent"; keep each service's choice.
    for row in conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE '%\\_torrent\\_sent' "
            "ESCAPE '\\'").fetchall():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                     (row["key"][:-len("torrent_sent")] + "grab_sent", row["value"]))
        conn.execute("DELETE FROM settings WHERE key = ?", (row["key"],))
    # Albums owned only because a tracker said they'd been snatched.
    conn.execute("DELETE FROM owned_albums WHERE source LIKE '%\\_snatched' ESCAPE '\\'")
    conn.execute(
        "DELETE FROM artist_library_stats WHERE source LIKE '%\\_snatched' ESCAPE '\\'")
    conn.execute(
        "UPDATE artists SET track_count = (SELECT COALESCE(SUM(track_count), 0) "
        "FROM artist_library_stats s WHERE s.artist_id = artists.id)")
    # Cached tracker answers (all under the "gz" prefix) and any Discover
    # source that no longer exists.
    conn.execute("DELETE FROM json_cache WHERE cache_key LIKE 'gz%'")
    marks = ",".join("?" for _ in _DISCOVERY_SOURCES)
    for table in ("discover_cache", "discover_history"):
        conn.execute(f"DELETE FROM {table} WHERE source NOT IN ({marks})",
                     _DISCOVERY_SOURCES)
    # Similar artists only Last.fm suggested stay; the trackers' go.
    conn.execute("DELETE FROM similar_artists WHERE COALESCE(site, '') <> 'Last.fm'")
    conn.execute("DELETE FROM settings WHERE key = 'library_gaps_built_at'")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('trackers_removed', '1')")


def clean_types(value):
    """Return an ordered, validated subset of the type options (may be empty)."""
    if isinstance(value, str):
        items = [v.strip().lower() for v in value.split(",")]
    else:
        items = [str(v).strip().lower() for v in (value or [])]
    return [t for t in MONITOR_TYPE_OPTIONS if t in items]


def monitored_types(stored):
    """Release types to watch, from an artist row's ``monitor_types``.

    NULL means the column was never set (an old row, an import) and takes the
    default. An empty string is a deliberate "monitor nothing" -- the artist
    page lets you untick everything -- and has to stay empty, or the next
    refresh quietly puts the releases back.
    """
    if stored is None:
        return set(clean_types(DEFAULT_SETTINGS["default_monitor_types"]))
    return {t for t in str(stored).split(",") if t}


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
    # WAL already gives durability across crashes; NORMAL skips the fsync per
    # commit, which is what makes a scan's thousands of small writes slow.
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def db_size_bytes():
    """Size of the database file plus its WAL, in bytes."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(DB_PATH + suffix)
        except OSError:
            pass
    return total


def free_page_ratio():
    """Share of the database file that is unused pages (0.0 - 1.0)."""
    conn = get_connection()
    try:
        pages = conn.execute("PRAGMA page_count").fetchone()[0] or 0
        free = conn.execute("PRAGMA freelist_count").fetchone()[0] or 0
    finally:
        conn.close()
    return (free / pages) if pages else 0.0


def compact(min_free_ratio=0.0):
    """Checkpoint the WAL and VACUUM. Returns bytes freed (0 when skipped).

    Deleting cached discographies and artwork rows leaves the space inside the
    file: after a big purge most of a 100MB database can be free pages, which
    every backup and page-cache read then carries around. *min_free_ratio*
    skips the VACUUM unless at least that share of the file is free.
    """
    before = db_size_bytes()
    if min_free_ratio and free_page_ratio() < min_free_ratio:
        return {"freed_bytes": 0, "size_bytes": before, "vacuumed": False}
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            # VACUUM rewrites the whole file through the WAL, so without a
            # second checkpoint the space shows up as a ~50MB WAL instead.
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    after = db_size_bytes()
    return {"freed_bytes": max(0, before - after), "size_bytes": after, "vacuumed": True}


def init_db():
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        _seed_history_from_cache(conn)
        # Seed any missing default settings without clobbering existing values.
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()
    invalidate_settings_cache()


# --- settings helpers -------------------------------------------------------

# Settings are read constantly (a single request can ask for a dozen of them,
# each of which used to mean opening a connection), change rarely, and the app
# runs as one process -- so the table is mirrored in memory and dropped whenever
# something writes to it.
_settings_cache = None
_settings_lock = threading.Lock()


def _load_settings():
    global _settings_cache
    with _settings_lock:
        if _settings_cache is not None:
            return _settings_cache
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    finally:
        conn.close()
    values = dict(DEFAULT_SETTINGS)
    values.update({r["key"]: r["value"] for r in rows})
    with _settings_lock:
        _settings_cache = values
        return _settings_cache


def invalidate_settings_cache():
    """Forget the cached settings (call after any write to the table)."""
    global _settings_cache
    with _settings_lock:
        _settings_cache = None


def get_setting(key, default=None):
    values = _load_settings()
    if key in values:
        return values[key]
    return DEFAULT_SETTINGS.get(key, default)


def get_all_settings():
    return dict(_load_settings())


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
    invalidate_settings_cache()


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


def _history_key(item):
    """Stable identity for a discover item: its release-group MBID, else artist|album."""
    mbid = (item.get("mbid") or "").strip()
    if mbid:
        return mbid
    artist = (item.get("artist") or "").strip().lower()
    album = (item.get("album") or "").strip().lower()
    return f"{artist}|{album}" if (artist or album) else None


def set_discover_cache(source, items):
    """Store a source's scrape result, stamped with the current time.

    Also folds every item into discover_history (keeping first_seen) so past
    releases survive after they fall out of the live scrape -- see
    get_discover_history.
    """
    payload = json.dumps(items)
    now = time.time()
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO discover_cache (source, fetched_at, payload) "
                "VALUES (?, ?, ?) ON CONFLICT(source) DO UPDATE SET "
                "fetched_at = excluded.fetched_at, payload = excluded.payload",
                (source, now, payload),
            )
            for it in items or []:
                key = _history_key(it)
                if not key:
                    continue
                conn.execute(
                    "INSERT INTO discover_history "
                    "(source, item_key, release_date, first_seen, last_seen, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source, item_key) DO UPDATE SET "
                    "release_date = excluded.release_date, "
                    "last_seen = excluded.last_seen, payload = excluded.payload",
                    (source, key, it.get("normalized_date"), now, now, json.dumps(it)),
                )
            conn.commit()
        finally:
            conn.close()


def _seed_history_from_cache(conn):
    """Fold the current discover_cache into discover_history (idempotent).

    Runs at startup so releases already cached before this feature existed are
    retained going forward, instead of only items seen by future scrapes.
    """
    rows = conn.execute("SELECT source, fetched_at, payload FROM discover_cache").fetchall()
    for row in rows:
        try:
            items = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        ts = row["fetched_at"]
        for it in items or []:
            key = _history_key(it)
            if not key:
                continue
            conn.execute(
                "INSERT INTO discover_history "
                "(source, item_key, release_date, first_seen, last_seen, payload) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, item_key) DO UPDATE SET "
                "release_date = excluded.release_date, payload = excluded.payload",
                (row["source"], key, it.get("normalized_date"), ts, ts, json.dumps(it)),
            )


def record_similar_artists(source_artist, entries, site):
    """Remember that *source_artist*'s page suggested *entries* (upsert)."""
    source_artist = (source_artist or "").strip()
    if not source_artist or not entries:
        return
    with _write_lock:
        conn = get_connection()
        try:
            for e in entries:
                name = (e.get("name") or "").strip()
                if (not name or name.lower() == source_artist.lower()
                        or is_non_artist(name)):
                    continue
                conn.execute(
                    "INSERT INTO similar_artists (source_artist, name, url, site, score) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(source_artist, name) DO UPDATE SET "
                    "url = excluded.url, site = excluded.site, "
                    "score = excluded.score, updated_at = datetime('now')",
                    (source_artist, name, e.get("url"), site, e.get("score")),
                )
            conn.commit()
        finally:
            conn.close()


# SQL fragment: genre tags for a suggested artist, preferring the ones stored on
# their library row (permanent) over the weekly 'simartinfo:' lookup cache.
_SIMILAR_GENRES_SQL = (
    "COALESCE(NULLIF(a.genres, ''), "
    "(SELECT json_extract(jc.payload, '$.genres') FROM json_cache jc "
    " WHERE jc.cache_key = 'simartinfo:' || lower(s.name)))"
)


def _parse_genres(value):
    """Genre list from either storage shape: a JSON array or a comma string."""
    if not value:
        return []
    text = value.strip()
    if text.startswith("["):
        try:
            items = json.loads(text)
        except ValueError:
            items = []
    else:
        items = text.split(",")
    return [str(g).strip() for g in items if str(g).strip()]


def similar_artist_rankings():
    """Suggested artists ranked by how many of your artists they're similar to.

    Owned artists (any tracks in the library) are excluded -- the point of the
    list is "similar to a lot of what you have, but you have nothing by them".
    Each row carries whatever genre tags are known for that artist, so the
    Discover list can filter on them without a request per row.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT s.name, COUNT(*) AS n, MAX(s.url) AS url, MAX(s.site) AS site, "
            "GROUP_CONCAT(s.source_artist, '') AS sources, "
            "COALESCE(SUM(s.score), 0) AS total_score, "
            "a.id AS artist_id, COALESCE(a.track_count, 0) AS track_count, "
            "COALESCE(a.subscription, 'none') AS subscription, "
            f"{_SIMILAR_GENRES_SQL} AS genres "
            "FROM similar_artists s "
            "LEFT JOIN artists a ON a.sort_name = lower(s.name) "
            "GROUP BY lower(s.name) "
            "HAVING COALESCE(a.track_count, 0) = 0 "
            "ORDER BY n DESC, total_score DESC, s.name COLLATE NOCASE"
        ).fetchall()
    finally:
        conn.close()
    return [{
        "name": r["name"],
        "count": r["n"],
        "url": r["url"],
        "site": r["site"],
        "artist_id": r["artist_id"],
        "subscription": r["subscription"],
        "genres": _parse_genres(r["genres"]),
        "sources": sorted((r["sources"] or "").split("")),
    } for r in rows]


def similar_artists_missing_genres(limit=None):
    """Suggested artists with no genre tags yet, best-ranked first.

    Drives the bulk genre lookup: same ranking and exclusions as
    similar_artist_rankings, minus everyone whose tags are already known.
    """
    conn = get_connection()
    try:
        sql = (
            "SELECT s.name AS name, COUNT(*) AS n "
            "FROM similar_artists s "
            "LEFT JOIN artists a ON a.sort_name = lower(s.name) "
            "GROUP BY lower(s.name) "
            "HAVING COALESCE(a.track_count, 0) = 0 "
            f"   AND COALESCE({_SIMILAR_GENRES_SQL}, '[]') IN ('', '[]') "
            "ORDER BY n DESC, COALESCE(SUM(s.score), 0) DESC, s.name COLLATE NOCASE"
        )
        params = []
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [r["name"] for r in rows]


def similar_genre_coverage():
    """(with_genres, total) counts over the suggestion ranking."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN g IS NULL OR g IN ('', '[]') THEN 0 ELSE 1 END) AS known "
            "FROM (SELECT " + _SIMILAR_GENRES_SQL + " AS g "
            "      FROM similar_artists s "
            "      LEFT JOIN artists a ON a.sort_name = lower(s.name) "
            "      GROUP BY lower(s.name) "
            "      HAVING COALESCE(a.track_count, 0) = 0)"
        ).fetchone()
    finally:
        conn.close()
    return int(row["known"] or 0), int(row["total"] or 0)


def fill_artist_genres(name, genres):
    """Store *genres* on an existing artist row that has none (no-op otherwise)."""
    if not name or not genres:
        return
    value = ",".join(g for g in genres if g)
    if not value:
        return
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE artists SET genres = ? WHERE sort_name = ? "
                "AND COALESCE(genres, '') = ''",
                (value, name.lower()),
            )
            conn.commit()
        finally:
            conn.close()


def add_discover_ignore(artist, album=None):
    """Hide *artist* (or just *artist*'s *album*) from the Discover page."""
    artist = (artist or "").strip()
    album = (album or "").strip()
    if not artist:
        return None
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO discover_ignores (artist, album) VALUES (?, ?)",
                (artist, album),
            )
            conn.commit()
        finally:
            conn.close()
    return {"artist": artist, "album": album or None}


def remove_discover_ignore(ignore_id):
    with _write_lock:
        conn = get_connection()
        try:
            cur = conn.execute("DELETE FROM discover_ignores WHERE id = ?", (ignore_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def list_discover_ignores():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, artist, album, created_at FROM discover_ignores "
            "ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE"
        ).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "artist": r["artist"], "album": r["album"] or None,
             "created_at": r["created_at"]} for r in rows]


def discover_ignore_sets():
    """Lowercased match sets: (ignored artist names, ignored (artist, album) pairs)."""
    artists = set()
    albums = set()
    for row in list_discover_ignores():
        a = row["artist"].lower()
        if row["album"]:
            albums.add((a, row["album"].lower()))
        else:
            artists.add(a)
    return artists, albums


def get_discover_history(sources, since_iso):
    """Stored history items for *sources* with a known release_date >= since_iso.

    Each returned item carries its `source` key. Undated rows are skipped (they
    can't be placed on the timeline; the live scrape still surfaces current ones).
    """
    if not sources:
        return []
    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in sources)
        rows = conn.execute(
            f"SELECT source, payload FROM discover_history "
            f"WHERE source IN ({placeholders}) "
            f"AND release_date IS NOT NULL AND release_date >= ?",
            [*sources, since_iso],
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            item = json.loads(r["payload"])
        except (TypeError, ValueError):
            continue
        item["source"] = r["source"]
        out.append(item)
    return out


# --- how long stored answers stay usable ------------------------------------

class _FromSettings:
    """Sentinel for "whatever the settings say", resolved when the call runs."""

    def __repr__(self):  # pragma: no cover - debugging aid
        return "FROM_SETTINGS"


FROM_SETTINGS = _FromSettings()

# kind -> (setting, unit in seconds, default in those units). A value of 0
# means no expiry at all: the stored answer is served however old it is.
_CACHE_AGE_SETTINGS = {
    "hit": ("store_keep_days", 86400, 0),
    "miss": ("store_miss_days", 86400, 7),
    "marker": ("store_marker_days", 86400, 30),
    "scrobble": ("store_scrobble_hours", 3600, 6),
}


def cache_max_age(kind):
    """Seconds a stored answer of this kind stays usable; None = forever."""
    key, unit, default = _CACHE_AGE_SETTINGS[kind]
    raw = get_setting(key)
    try:
        value = float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    return None if value <= 0 else value * unit


def resolve_max_age(max_age, kind):
    """Turn the FROM_SETTINGS sentinel into seconds (None means forever)."""
    return cache_max_age(kind) if max_age is FROM_SETTINGS else max_age


# --- owned albums -----------------------------------------------------------

def owned_album_key(title):
    """Normalized match key for an album title."""
    return (title or "").strip().lower()


# Words one side of a match carries and the other doesn't: MusicBrainz names a
# release group "Young Heartache EP" while a library tags the same record
# "Young Heartache", and neither is wrong.
_RELEASE_SUFFIXES = (
    "ep", "lp", "single", "deluxe", "deluxeedition", "deluxeversion",
    "remaster", "remastered", "remasteredversion", "reissue", "edition",
    "expandededition", "anniversaryedition", "specialedition", "bonustracks",
    "explicit", "originalmotionpicturesoundtrack",
)

# Never strip a title down past this: "EP" is a suffix on "Young Heartache EP"
# and the whole name of the record called "EP".
_MIN_ALT_KEY = 4


def owned_album_alt_key(title):
    """Looser album key: punctuation, spacing and trailing release words gone.

    Used only to widen an ownership match, never to write a row, so the worst a
    collision does is call a release owned when a near-identically named one is.
    """
    key = match_key(title)
    trimmed = True
    while trimmed and key:
        trimmed = False
        for word in _RELEASE_SUFFIXES:
            if key.endswith(word) and len(key) - len(word) >= _MIN_ALT_KEY:
                key = key[: -len(word)]
                trimmed = True
    return key


# Curly quotes, dashes and the like differ between MusicBrainz, Last.fm and
# file tags for the same release, and the same artist shows up three ways
# ("Bonnie 'Prince' Billy" / "Bonnie “Prince” Billy" / "Bonnie Prince
# Billy"). This key ignores everything that isn't a letter or a digit, which is
# strict enough to match those and loose enough not to merge real differences.
# Names that aren't an artist: a compilation credit, or a placeholder a tagger
# wrote. They arrive from library scans and from MusicBrainz, and tracking them
# means a release feed full of records nobody made. Matched on the normalised
# key, so a real band called Various Production is untouched.
NON_ARTIST_KEYS = {
    "variousartists", "variousartist", "various", "va", "variousarists",
    "unknownartist", "unknown", "noartist", "soundtrack", "originalsoundtrack",
    "ost", "compilation", "variouscomposers",
}


def is_non_artist(name):
    """True when *name* is a compilation credit rather than an artist."""
    return match_key(name) in NON_ARTIST_KEYS


def match_key(text):
    """Aggressively normalized key for comparing artist or album names."""
    folded = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(ch for ch in folded if ch.isalnum())


def mark_owned(conn, artist_id, title, rg_mbid=None, source="filesystem",
               fmt=None, tracks=None, tracks_at_least=False):
    """Upsert an owned album on an open connection (caller holds the write lock).

    Each (library) *source* keeps its own row, so an album owned by more than one
    source -- a filesystem copy and a Subsonic server, say, or a manual mark --
    coexists. "Owned" means any row exists (see get_owned).

    *tracks* is how many of the album's tracks the source holds. A scan that
    only saw part of the album (a quick scan of the new files) passes
    *tracks_at_least*, so it can raise the count but never lower it.
    """
    key = owned_album_key(title)
    if not key:
        return
    tracks = int(tracks) if tracks else None
    if tracks_at_least:
        track_expr = ("MAX(COALESCE(owned_albums.track_count, 0), "
                      "COALESCE(excluded.track_count, 0))")
    else:
        track_expr = "COALESCE(excluded.track_count, owned_albums.track_count)"
    conn.execute(
        "INSERT INTO owned_albums (artist_id, album_key, rg_mbid, title, source, "
        "format, track_count) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(artist_id, album_key, source) DO UPDATE SET "
        "rg_mbid = COALESCE(excluded.rg_mbid, owned_albums.rg_mbid), "
        "title = excluded.title, "
        "format = COALESCE(excluded.format, owned_albums.format), "
        f"track_count = {track_expr}",
        (artist_id, key, rg_mbid, title, source, fmt, tracks),
    )


def set_library_stat(conn, artist_id, source, track_count, *, increment=False):
    """Upsert a per-source track count (open connection, caller holds the lock)."""
    expr = "artist_library_stats.track_count + excluded.track_count" if increment else "excluded.track_count"
    conn.execute(
        "INSERT INTO artist_library_stats (artist_id, source, track_count, last_scanned) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(artist_id, source) DO UPDATE SET "
        f"track_count = {expr}, last_scanned = excluded.last_scanned",
        (artist_id, source, int(track_count or 0), time.time()),
    )


def recompute_track_count(conn, artist_id):
    """Set artists.track_count to the sum of its per-source library counts."""
    row = conn.execute(
        "SELECT COALESCE(SUM(track_count), 0) AS t FROM artist_library_stats "
        "WHERE artist_id = ?",
        (artist_id,),
    ).fetchone()
    conn.execute(
        "UPDATE artists SET track_count = ? WHERE id = ?", (row["t"], artist_id)
    )


def replace_library_owned(source, records, *, full=True):
    """Apply a library scan for *source*. Returns the number of artists touched.

    *records* is a list of
    {name, mbid?, track_count?, albums: [{title, rg_mbid?, format?, tracks?}]}.
    With *full* (the default) this source's owned rows and stats are cleared first
    so removals on the remote side propagate; otherwise counts are incremented
    (used by a quick filesystem sync of just the newly added files).
    """
    touched = set()
    with _write_lock:
        conn = get_connection()
        try:
            if full:
                conn.execute("DELETE FROM owned_albums WHERE source = ?", (source,))
                conn.execute("DELETE FROM artist_library_stats WHERE source = ?", (source,))
            for rec in records:
                name = (rec.get("name") or "").strip()
                # A compilation credit is not an artist, wherever it came from.
                if not name or is_non_artist(name):
                    continue
                sort_name = name.lower()
                existing = conn.execute(
                    "SELECT id FROM artists WHERE sort_name = ?", (sort_name,)
                ).fetchone()
                if existing:
                    artist_id = existing["id"]
                    conn.execute(
                        "UPDATE artists SET name = ?, mbid = COALESCE(mbid, ?) WHERE id = ?",
                        (name, rec.get("mbid"), artist_id),
                    )
                else:
                    cur = conn.execute(
                        "INSERT INTO artists (name, sort_name, mbid, track_count) "
                        "VALUES (?, ?, ?, 0)",
                        (name, sort_name, rec.get("mbid")),
                    )
                    artist_id = cur.lastrowid
                for alb in rec.get("albums", []):
                    mark_owned(conn, artist_id, alb.get("title"), alb.get("rg_mbid"),
                               source=source, fmt=alb.get("format"),
                               tracks=alb.get("tracks"))
                set_library_stat(conn, artist_id, source, rec.get("track_count"), increment=not full)
                touched.add(artist_id)
            for artist_id in touched:
                recompute_track_count(conn, artist_id)
            conn.commit()
        finally:
            conn.close()
    return len(touched)


def library_stats(source):
    """Aggregate stats for a library source: (last_scanned, artists, tracks)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT MAX(last_scanned) AS last, COUNT(*) AS artists, "
            "COALESCE(SUM(track_count), 0) AS tracks "
            "FROM artist_library_stats WHERE source = ?",
            (source,),
        ).fetchone()
        albums = conn.execute(
            "SELECT COUNT(*) AS c FROM owned_albums WHERE source = ?", (source,)
        ).fetchone()["c"]
    finally:
        conn.close()
    return {
        "last_scanned": row["last"],
        "artists": row["artists"],
        "tracks": row["tracks"],
        "albums": albums,
    }


def set_owned(artist_id, title, owned, rg_mbid=None):
    """Manually mark/unmark an album as owned. Returns the new owned state."""
    with _write_lock:
        conn = get_connection()
        try:
            if owned:
                mark_owned(conn, artist_id, title, rg_mbid, source="manual")
            else:
                conn.execute(
                    "DELETE FROM owned_albums WHERE artist_id = ? AND album_key = ?",
                    (artist_id, owned_album_key(title)),
                )
            conn.commit()
        finally:
            conn.close()
    return bool(owned)


def set_discography_counts(artist_id, albums, eps, singles):
    """Cache an artist's MusicBrainz discography counts by release type."""
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE artists SET disc_albums = ?, disc_eps = ?, disc_singles = ? "
                "WHERE id = ?",
                (albums, eps, singles, artist_id),
            )
            conn.commit()
        finally:
            conn.close()


def set_owned_counts(artist_id, albums, eps, singles):
    """Cache how many of each type the user owns (the owned subset of disc_*)."""
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE artists SET owned_albums = ?, owned_eps = ?, owned_singles = ? "
                "WHERE id = ?",
                (albums, eps, singles, artist_id),
            )
            conn.commit()
        finally:
            conn.close()


def get_owned(artist_id):
    """Return ({album_key, ...}, {rg_mbid, ...}) of owned albums for an artist."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT album_key, rg_mbid FROM owned_albums WHERE artist_id = ?",
            (artist_id,),
        ).fetchall()
    finally:
        conn.close()
    keys = {r["album_key"] for r in rows}
    mbids = {r["rg_mbid"] for r in rows if r["rg_mbid"]}
    return keys, mbids


def get_album_links(artist_id):
    """{(album_key, rg_mbid): linked} for one artist's manual matches."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT album_key, rg_mbid, linked FROM album_links WHERE artist_id = ?",
            (artist_id,),
        ).fetchall()
    finally:
        conn.close()
    return {(r["album_key"], r["rg_mbid"]): int(r["linked"]) for r in rows}


def all_album_links():
    """{artist_id: {(album_key, rg_mbid): linked}} in one query, for the sweep."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT artist_id, album_key, rg_mbid, linked FROM album_links"
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["artist_id"], {})[(r["album_key"], r["rg_mbid"])] = int(r["linked"])
    return out


def set_album_link(artist_id, album_key, rg_mbid, linked):
    """Record (or clear, with *linked* None) a manual match for one pair."""
    with _write_lock:
        conn = get_connection()
        try:
            if linked is None:
                conn.execute(
                    "DELETE FROM album_links WHERE artist_id = ? AND album_key = ? "
                    "AND rg_mbid = ?", (artist_id, album_key, rg_mbid),
                )
            else:
                conn.execute(
                    "INSERT INTO album_links (artist_id, album_key, rg_mbid, linked) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(artist_id, album_key, rg_mbid) "
                    "DO UPDATE SET linked = excluded.linked",
                    (artist_id, album_key, rg_mbid, 1 if linked else 0),
                )
            conn.commit()
        finally:
            conn.close()


def alt_key_index(available):
    """{looser key: {library album keys}} -- built once per artist, not per release."""
    index = {}
    for key in available:
        alt = owned_album_alt_key(key)
        if alt:
            index.setdefault(alt, set()).add(key)
    return index


def owned_album_keys(links, available, item_mbid, title, alt_index=None):
    """Which library album keys count as this release group.

    *links* is one artist's {(album_key, rg_mbid): linked}, *available* the
    album keys the library actually has. A manual link wins outright; failing
    that the exact title matches, then the looser title (compared loose to
    loose, so "Young Heartache EP" finds "Young Heartache") -- and a pair the
    user has explicitly unlinked never matches at all.
    """
    if item_mbid:
        linked = {
            album_key for (album_key, rg), state in links.items()
            if rg == item_mbid and state and album_key in available
        }
        if linked:
            return linked
    candidates = set()
    exact = owned_album_key(title)
    if exact in available:
        candidates.add(exact)
    alt = owned_album_alt_key(title)
    if alt:
        index = alt_index if alt_index is not None else alt_key_index(available)
        candidates |= index.get(alt, set())
    if item_mbid:
        # The user said these two are not the same record.
        candidates = {k for k in candidates if links.get((k, item_mbid)) != 0}
    return candidates


def owned_index(artist_id):
    """What the library holds for one artist, ready for matching.

    Returns (rows, links): rows is {album_key: {sources, formats, mbids}} and
    links the artist's manual matches, so one query answers "do I own this
    release group, from where, in what quality" for a whole discography.
    """
    conn = get_connection()
    try:
        owned = conn.execute(
            "SELECT album_key, rg_mbid, format, source, track_count FROM owned_albums "
            "WHERE artist_id = ?", (artist_id,),
        ).fetchall()
    finally:
        conn.close()
    rows = {}
    for r in owned:
        entry = rows.setdefault(
            r["album_key"],
            {"sources": set(), "formats": [], "mbids": set(), "tracks": 0},
        )
        entry["sources"].add(r["source"])
        # The fullest copy any source holds: a complete album on the server
        # isn't made incomplete by a partial one on the laptop.
        entry["tracks"] = max(entry["tracks"], r["track_count"] or 0)
        if r["format"]:
            entry["formats"].append(r["format"])
        if r["rg_mbid"]:
            entry["mbids"].add(r["rg_mbid"])
    return rows, get_album_links(artist_id)


def match_owned(rows, links, item_mbid, title, alt_index=None):
    """(album_keys, sources, formats) of the library albums that are this release."""
    keys = owned_album_keys(links, set(rows), item_mbid, title, alt_index)
    if item_mbid:
        # The library tagged the release group itself, which is as good as a
        # manual link unless the user has unlinked that exact pair.
        for key, entry in rows.items():
            if item_mbid in entry["mbids"] and links.get((key, item_mbid)) != 0:
                keys.add(key)
    sources, formats = set(), []
    for key in keys:
        sources |= rows[key]["sources"]
        formats += rows[key]["formats"]
    return keys, sources, formats


def owned_track_count(rows, keys):
    """Most tracks any source holds of the library albums *keys* (0 = unknown)."""
    return max((rows[k].get("tracks") or 0 for k in keys if k in rows), default=0)


def get_owned_sources(artist_id):
    """Return (by_key, by_mbid): album_key / rg_mbid -> set of owning source keys.

    Lets the discography show *which* library (or 'manual') an owned album came
    from, including the several sources that may own the same album. Each album
    is indexed under its exact key *and* its looser one (see
    owned_album_alt_key), so a library's "Young Heartache" answers for
    MusicBrainz's "Young Heartache EP".
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT album_key, rg_mbid, source FROM owned_albums WHERE artist_id = ?",
            (artist_id,),
        ).fetchall()
    finally:
        conn.close()
    by_key, by_mbid = {}, {}
    for r in rows:
        by_key.setdefault(r["album_key"], set()).add(r["source"])
        alt = owned_album_alt_key(r["album_key"])
        if alt:
            by_key.setdefault(alt, set()).add(r["source"])
        if r["rg_mbid"]:
            by_mbid.setdefault(r["rg_mbid"], set()).add(r["source"])
    return by_key, by_mbid


def get_owned_formats(artist_id):
    """Return (by_key, by_mbid): album_key / rg_mbid -> list of quality labels.

    One album can be owned by several sources in different qualities (a FLAC on
    disk, a 320 on the server); the caller picks the best of them.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT album_key, rg_mbid, format FROM owned_albums "
            "WHERE artist_id = ? AND format IS NOT NULL AND format <> ''",
            (artist_id,),
        ).fetchall()
    finally:
        conn.close()
    by_key, by_mbid = {}, {}
    for r in rows:
        by_key.setdefault(r["album_key"], []).append(r["format"])
        alt = owned_album_alt_key(r["album_key"])
        if alt:
            by_key.setdefault(alt, []).append(r["format"])
        if r["rg_mbid"]:
            by_mbid.setdefault(r["rg_mbid"], []).append(r["format"])
    return by_key, by_mbid


def record_artist_folders(conn, artist_id, folders):
    """Remember the directories an artist's tracks live in (open connection)."""
    for folder in folders:
        if folder:
            conn.execute(
                "INSERT OR IGNORE INTO artist_folders (artist_id, folder) VALUES (?, ?)",
                (artist_id, folder),
            )


def get_artist_folders(artist_id):
    """Return the list of stored folders for an artist."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT folder FROM artist_folders WHERE artist_id = ?", (artist_id,)
        ).fetchall()
    finally:
        conn.close()
    return [r["folder"] for r in rows]


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


def get_json_cache_many(keys):
    """Batch variant of get_json_cache (no max_age): key -> decoded payload.

    Missing/corrupt entries are simply absent from the result.
    """
    keys = [k for k in keys if k]
    if not keys:
        return {}
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(keys))
        rows = conn.execute(
            "SELECT cache_key, payload FROM json_cache "
            f"WHERE cache_key IN ({placeholders})",
            keys,
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for row in rows:
        try:
            out[row["cache_key"]] = json.loads(row["payload"])
        except (TypeError, ValueError):
            pass
    return out


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
    invalidate_settings_cache()
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
