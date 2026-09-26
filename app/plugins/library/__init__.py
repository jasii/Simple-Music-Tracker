"""Library plugins: sources of the user's owned music collection.

A library tells the app which artists/albums the user already has, which drives
``track_count`` and the "owned" badges on discographies. Each library is a
:class:`LibraryPlugin` -- a filesystem scanner, a Subsonic/Navidrome server, etc.
Several can be enabled at once: their owned albums form a union, tagged per
source in ``owned_albums.source`` so a re-scan of one never disturbs another
(see :func:`app.db.replace_library_owned`).

Importing this package registers every bundled library plugin.
"""

from .. import Plugin, register, get_plugins, get_plugin  # noqa: F401 - re-exported

from ... import db, gaps

# Values that mean "off" for an enable toggle stored as a string setting.
_OFF = ("false", "0", "off", "no", "")


def merge_albums(albums):
    """Collapse albums that share a title, keeping the best quality of them.

    A server can hold the same record twice (a FLAC rip beside a 320), while
    owned_albums keeps one row per album per source -- so the better copy is
    the one worth remembering, not whichever was listed last.
    """
    out = {}
    for alb in albums or []:
        key = db.owned_album_key(alb.get("title"))
        if not key:
            continue
        current = out.get(key)
        if current is None:
            out[key] = dict(alb)
            continue
        current["format"] = gaps.better_quality(current.get("format"), alb.get("format"))
        # Two copies of one album: the fuller one says how complete it is.
        if (alb.get("tracks") or 0) > (current.get("tracks") or 0):
            current["tracks"] = alb["tracks"]
        if not current.get("rg_mbid") and alb.get("rg_mbid"):
            current["rg_mbid"] = alb["rg_mbid"]
    return list(out.values())


class LibraryPlugin(Plugin):
    """A source of owned music.

    Subclasses set ``key`` / ``label`` and implement :meth:`scan`. ``key`` is the
    value written to ``owned_albums.source`` / ``artist_library_stats.source``.
    """

    kind = "library"
    enabled_setting = None
    config_fields = []
    has_test = False
    # Filesystem supports a fast incremental ("quick") sync; most don't.
    supports_quick = False
    # Can this source hand back audio for one track (see find_track)?
    plays_tracks = False
    # Can playlists be written to it? (see playlist_replace)
    supports_playlists = False

    def enabled(self):
        """True unless the enable toggle is explicitly off."""
        if not self.enabled_setting:
            return True
        value = (db.get_setting(self.enabled_setting) or "false").strip().lower()
        return value not in _OFF

    def configured(self):
        """Ready to scan? Default: just enabled. Override to also require config."""
        return self.enabled()

    def scan(self, quick=False):
        """Run a scan, writing owned albums + per-source counts. Returns a summary."""
        raise NotImplementedError

    def check(self):
        """Optional health check. Return ``(ok, message)`` or ``None``."""
        return None

    def albums_for_artist(self, name):
        """Owned albums for a single artist, for a scoped per-artist rescan.

        Return ``{"albums": [{"title", "rg_mbid"}], "track_count": int}`` or
        ``None`` when the source can't look an artist up on its own (e.g. the
        filesystem source, which the per-artist scan walks directly).
        """
        return None

    def playlist_replace(self, name, song_ids):
        """Put these songs in the playlist called *name*, creating it if needed.

        Only sources that declare ``supports_playlists`` implement it.
        """
        raise NotImplementedError

    def playlist_tracks(self, name):
        """What's in that playlist now: [{artist, title, album, duration}]."""
        return []

    def find_track(self, artist, title):
        """Locate one track in this library, for playback.

        Return a stream descriptor plus whatever is worth showing:

        * ``{"kind": "file", "path": "/music/..."}`` -- served directly
        * ``{"kind": "url", "url": ..., "params": {...}, "headers": {...}}`` --
          proxied, so no credentials reach the browser

        with optional ``title`` / ``album`` / ``duration`` / ``suffix``. Return
        None when this source hasn't got the track, or can't look one up.
        """
        return None

    def progress(self):
        """Live scan progress for polling. Override if the source tracks it."""
        return {"running": False}

    def interval_setting(self):
        """Settings key holding how often this library re-scans itself."""
        return f"library_{self.key}_scan_hours"

    def scan_interval_hours(self):
        """Hours between automatic scans; 0 means manual only."""
        try:
            return max(0.0, float(db.get_setting(self.interval_setting()) or 0))
        except (TypeError, ValueError):
            return 0.0

    def schedule_field(self):
        """The interval picker, appended to every library's own settings."""
        return {
            "key": self.interval_setting(),
            "label": "How often to re-scan this library",
            "type": "select",
            "options": [
                {"value": "0", "label": "Never - only when I press Scan now"},
                {"value": "3", "label": "Every 3 hours"},
                {"value": "6", "label": "Every 6 hours"},
                {"value": "12", "label": "Every 12 hours"},
                {"value": "24", "label": "Every 24 hours"},
                {"value": "72", "label": "Every 3 days"},
                {"value": "168", "label": "Every 7 days"},
            ],
            "help": "An interval, not a time of day: the next scan is due this "
                    "long after the last one finished. Scans run one at a time "
                    "and queue behind each other, so several libraries on the "
                    "same interval won't collide.",
        }

    def describe(self):
        data = super().describe()
        stats = db.library_stats(self.key)
        data.update({
            "enabled": self.enabled(),
            "enabled_setting": self.enabled_setting,
            "configured": self.configured(),
            "config_fields": list(self.config_fields) + [self.schedule_field()],
            "has_test": self.has_test,
            "supports_quick": self.supports_quick,
            "plays_tracks": self.plays_tracks,
            "supports_playlists": self.supports_playlists,
            "scan_interval_hours": self.scan_interval_hours(),
            "scannable": True,
            "last_scanned": stats["last_scanned"],
            "artist_total": stats["artists"],
            "album_total": stats["albums"],
            "track_total": stats["tracks"],
        })
        return data


# Register the bundled libraries (import side effect calls register()).
from . import filesystem, subsonic, plex  # noqa: E402,F401
