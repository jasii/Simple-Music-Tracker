"""Library source: a Subsonic-compatible server (e.g. Navidrome).

Talks the Subsonic API (v1.16.1). Authentication is salt+token: we store a fixed
salt ``s`` and ``t = md5(password + salt)`` (computed once at connect time, see
main.api_settings) and send them on every call -- the password itself is never
persisted.

Owned albums come from paging ``getAlbumList2``; each album carries its artist,
song count and (on OpenSubsonic servers) a MusicBrainz id. Results are written
via db.replace_library_owned("subsonic", ...) as a full replace.
"""

import threading
import time

import requests

from ... import db, gaps, names, quality, scans
from . import LibraryPlugin, merge_albums, register

API_VERSION = "1.16.1"
CLIENT = "SimpleMusicTracker"
PAGE_SIZE = 500
# Songs per bulk page when reading every track's codec (see _album_formats).
SONG_PAGE_SIZE = 500
# Sanity stop for that sweep, in case a server keeps answering with full pages.
_MAX_SONGS = 500_000
SOURCE = "subsonic"

# What the server told us it runs. Every OpenSubsonic response carries `type`
# and `serverVersion`, so this is learned for free on the first call and then
# names the source everywhere instead of the generic "Subsonic / Navidrome".
TYPE_SETTING = "subsonic_server_type"
VERSION_SETTING = "subsonic_server_version"
DEFAULT_LABEL = "Subsonic / Navidrome"

# Reported ids whose proper spelling isn't simply a capitalised word.
_SERVER_NAMES = {
    "navidrome": "Navidrome",
    "subsonic": "Subsonic",
    "airsonic": "Airsonic",
    "airsonic-advanced": "Airsonic",
    "gonic": "gonic",
    "ampache": "Ampache",
    "lms": "LMS",
    "supysonic": "Supysonic",
    "funkwhale": "Funkwhale",
    "astiga": "Astiga",
    "nextcloud": "Nextcloud Music",
}

# Last values written, so a hot path (one track lookup per click) doesn't go
# near the settings table on every response.
_seen = {}


def _remember_server(data):
    """Record the server's self-reported software, when it reports any."""
    kind = (data.get("type") or "").strip().lower()
    version = (data.get("serverVersion") or "").strip()
    if kind and _seen.get("type") != kind:
        _seen["type"] = kind
        if (db.get_setting(TYPE_SETTING) or "") != kind:
            db.set_setting(TYPE_SETTING, kind)
    if version and _seen.get("version") != version:
        _seen["version"] = version
        if (db.get_setting(VERSION_SETTING) or "") != version:
            db.set_setting(VERSION_SETTING, version)


def server_name():
    """"Navidrome", "Airsonic", ... or None until a server has answered."""
    kind = (db.get_setting(TYPE_SETTING) or "").strip().lower()
    if not kind:
        return None
    return _SERVER_NAMES.get(kind, kind.replace("-", " ").replace("_", " ").title())


def server_version():
    return (db.get_setting(VERSION_SETTING) or "").strip() or None


# Live scan progress for the settings UI to poll.
_progress = {"running": False, "message": "", "albums": 0, "artists": 0}
_progress_lock = threading.Lock()
_scan_lock = threading.Lock()


def _set_progress(**kw):
    with _progress_lock:
        _progress.update(kw)


def get_progress():
    with _progress_lock:
        return dict(_progress)


def _config():
    return {
        "url": (db.get_setting("subsonic_url") or "").strip().rstrip("/"),
        "user": (db.get_setting("subsonic_username") or "").strip(),
        "salt": (db.get_setting("subsonic_salt") or "").strip(),
        "token": (db.get_setting("subsonic_token") or "").strip(),
    }


def _params(cfg):
    return {
        "u": cfg["user"],
        "t": cfg["token"],
        "s": cfg["salt"],
        "v": API_VERSION,
        "c": CLIENT,
        "f": "json",
    }


def _get(cfg, endpoint, **extra):
    """Call a Subsonic endpoint, returning the 'subsonic-response' object.

    Raises RuntimeError on transport errors or a 'failed' API status.
    """
    if not (cfg["url"] and cfg["user"] and cfg["token"] and cfg["salt"]):
        raise RuntimeError("Subsonic server not configured")
    params = _params(cfg)
    params.update(extra)
    try:
        resp = requests.get(f"{cfg['url']}/rest/{endpoint}", params=params, timeout=20)
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not reach Subsonic server: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("Subsonic server returned a non-JSON response") from exc
    data = body.get("subsonic-response", {})
    _remember_server(data)
    if data.get("status") != "ok":
        err = (data.get("error") or {}).get("message") or "request failed"
        raise RuntimeError(f"Subsonic: {err}")
    return data


def ping():
    """Validate the stored credentials. Returns (ok, message)."""
    cfg = _config()
    if not cfg["url"]:
        return False, "No server URL set."
    if not cfg["token"]:
        return False, "Not connected - enter your password to connect."
    try:
        _get(cfg, "ping")
    except RuntimeError as exc:
        return False, str(exc)
    # The ping told us what it runs (see _remember_server), so say so.
    running = " ".join(x for x in (server_name(), server_version()) if x)
    where = f"{running} at {cfg['url']}" if running else cfg["url"]
    return True, f"Connected to {where} as {cfg['user']}."


def _fetch_albums(cfg):
    """Page through getAlbumList2, yielding every album dict."""
    offset = 0
    while True:
        data = _get(cfg, "getAlbumList2", type="alphabeticalByArtist",
                    size=PAGE_SIZE, offset=offset)
        albums = (data.get("albumList2") or {}).get("album") or []
        if not albums:
            break
        for alb in albums:
            yield alb
        if len(albums) < PAGE_SIZE:
            break
        offset += PAGE_SIZE


def _song_quality(song):
    """Quality label for one Subsonic song row, or None if it says nothing."""
    suffix = (song.get("suffix") or "").lower()
    try:
        bits = int(song.get("bitDepth") or 0)
    except (TypeError, ValueError):
        bits = 0
    if suffix == "flac":
        # Subsonic reports bit depth outright, so no guessing from bitrate.
        return "FLAC 24bit" if bits > 16 else "FLAC"
    return quality.file_label(suffix, song.get("bitRate"))


def _album_quality(songs):
    """The best quality among an album's songs ("what you've got")."""
    best = None
    for song in songs or []:
        best = gaps.better_quality(best, _song_quality(song))
    return best


def _album_formats(cfg):
    """{album id: best quality label} from one bulk pass over every song.

    search3 with an empty query pages the whole song list (500 rows per call,
    and each row carries its album id, codec and bitrate), which is far cheaper
    than asking for each album's tracks one album at a time.
    """
    out = {}
    offset = 0
    while offset < _MAX_SONGS:
        data = _get(cfg, "search3", query="", artistCount=0, albumCount=0,
                    songCount=SONG_PAGE_SIZE, songOffset=offset)
        songs = (data.get("searchResult3") or {}).get("song") or []
        for song in songs:
            album_id = song.get("albumId")
            if not album_id:
                continue
            out[album_id] = gaps.better_quality(out.get(album_id), _song_quality(song))
        scans.raise_if_stopped()
        if len(songs) < SONG_PAGE_SIZE:
            break
        offset += SONG_PAGE_SIZE
    return out


def albums_for_artist(name):
    """Owned albums for one artist via search3, filtered to exact-name matches."""
    cfg = _config()
    want = (name or "").strip().lower()
    data = _get(cfg, "search3", query=name, artistCount=0, albumCount=500, songCount=0)
    rows = (data.get("searchResult3") or {}).get("album") or []
    albums = []
    tracks = 0
    for alb in rows:
        if (alb.get("artist") or "").strip().lower() != want:
            continue
        title = (alb.get("name") or alb.get("album") or "").strip()
        if title:
            # One artist is a handful of albums, so their songs can be read
            # per album rather than sweeping the whole server.
            songs = []
            if alb.get("id"):
                one = _get(cfg, "getAlbum", id=alb["id"])
                songs = (one.get("album") or {}).get("song") or []
            albums.append({
                "title": title,
                "rg_mbid": alb.get("musicBrainzId") or None,
                "format": _album_quality(songs),
                "tracks": int(alb.get("songCount") or 0) or None,
            })
            tracks += int(alb.get("songCount") or 0)
    return {"albums": merge_albums(albums), "track_count": tracks}


def find_track(artist, title):
    """The user's own copy of one track, as a stream descriptor, or None.

    Searched by "artist title" and then checked, because search3 matches
    loosely enough to return another artist's cover of the same song.
    """
    cfg = _config()
    if not (cfg["url"] and cfg["user"] and cfg["token"]):
        return None
    data = _get(cfg, "search3", query=f"{artist} {title}",
                artistCount=0, albumCount=0, songCount=20)
    for song in (data.get("searchResult3") or {}).get("song") or []:
        # Word-wise, so a search for "Alex G" can't answer with Alex Gaudino
        # (see app/names.py).
        if not names.same_name(artist, song.get("artist")):
            continue
        if not names.same_name(title, song.get("title")):
            continue
        params = _params(cfg)
        params["id"] = song["id"]
        return {
            "kind": "url",
            "url": f"{cfg['url']}/rest/stream",
            "params": params,
            "title": song.get("title"),
            "album": song.get("album"),
            "duration": song.get("duration"),
            "suffix": song.get("suffix"),
            # The server's own id for the song, which is what a playlist is
            # made of (see playlist_replace).
            "song_id": song.get("id"),
            # Where a person can go and look at this record themselves.
            "page_url": _web_url(cfg, song),
        }


def _web_url(cfg, song):
    """The server's own page for this song, as far as one can be built.

    Navidrome's web UI is a known route, so an album deep link is possible.
    Other Subsonic servers have no agreed URL scheme, so the best that can be
    offered is the server itself.
    """
    base = (cfg.get("url") or "").rstrip("/")
    if not base:
        return None
    album_id = song.get("albumId")
    if album_id and "navidrome" in (server_name() or "").lower():
        return f"{base}/app/#/album/{album_id}/show"
    return base
    return None


def playlist_replace(name, song_ids):
    """Put *song_ids* in the playlist called *name*, creating it if needed.

    Subsonic's createPlaylist doubles as an update: given a playlistId it
    replaces that playlist's contents outright, which is what this wants --
    running it again should leave one playlist, not a longer one.

    Returns {"id", "name", "count"}.
    """
    cfg = _config()
    song_ids = [str(i) for i in song_ids if i]
    if not song_ids:
        raise RuntimeError("no songs to put in the playlist")
    existing = None
    data = _get(cfg, "getPlaylists")
    for row in ((data.get("playlists") or {}).get("playlist") or []):
        if (row.get("name") or "").strip().lower() == name.strip().lower():
            existing = str(row.get("id"))
            break
    if existing:
        _get(cfg, "createPlaylist", playlistId=existing, songId=song_ids)
        # The name is left alone: the playlist was found by it.
        return {"id": existing, "name": name, "count": len(song_ids)}
    created = _get(cfg, "createPlaylist", name=name, songId=song_ids)
    playlist = created.get("playlist") or {}
    return {"id": str(playlist.get("id") or ""), "name": playlist.get("name") or name,
            "count": len(song_ids)}


def playlist_tracks(name):
    """What's in the playlist called *name*: [{artist, title, album, duration}].

    Read back from the server rather than remembered here, so the answer is
    whatever the playlist holds now -- including anything added to it by hand.
    """
    cfg = _config()
    data = _get(cfg, "getPlaylists")
    found = None
    for row in ((data.get("playlists") or {}).get("playlist") or []):
        if (row.get("name") or "").strip().lower() == name.strip().lower():
            found = str(row.get("id"))
            break
    if not found:
        return []
    full = _get(cfg, "getPlaylist", id=found)
    out = []
    for entry in ((full.get("playlist") or {}).get("entry") or []):
        if not entry.get("title"):
            continue
        out.append({
            "artist": entry.get("artist"),
            "title": entry.get("title"),
            "album": entry.get("album"),
            "duration": entry.get("duration"),
        })
    return out


def scan():
    """Scan the server and replace this source's owned data. Returns a summary."""
    cfg = _config()
    with _scan_lock:
        _set_progress(running=True, message="connecting", albums=0, artists=0)
        try:
            _set_progress(message="reading track formats")
            formats = _album_formats(cfg)
            by_artist = {}
            seen = 0
            for alb in _fetch_albums(cfg):
                artist = (alb.get("artist") or "").strip()
                title = (alb.get("name") or alb.get("album") or "").strip()
                if not artist or not title:
                    continue
                rec = by_artist.setdefault(
                    artist.lower(), {"name": artist, "track_count": 0, "albums": []}
                )
                rec["albums"].append({
                    "title": title,
                    "rg_mbid": alb.get("musicBrainzId"),
                    "format": formats.get(alb.get("id")),
                    "tracks": int(alb.get("songCount") or 0) or None,
                })
                rec["track_count"] += int(alb.get("songCount") or 0)
                seen += 1
                scans.raise_if_stopped()
                if seen % 200 == 0:
                    _set_progress(albums=seen, artists=len(by_artist),
                                  message="reading albums")
            _set_progress(albums=seen, artists=len(by_artist), message="saving")
            for rec in by_artist.values():
                rec["albums"] = merge_albums(rec["albums"])
            artists = db.replace_library_owned(SOURCE, list(by_artist.values()), full=True)
            _set_progress(running=False, message="done", albums=seen, artists=artists)
            return {"source": SOURCE, "albums": seen, "artists": artists}
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            _set_progress(running=False, message=f"error: {exc}")
            raise


class SubsonicLibrary(LibraryPlugin):
    key = SOURCE
    label = DEFAULT_LABEL
    description = "Read owned albums from a Subsonic-compatible server's API."
    enabled_setting = "library_subsonic_enabled"
    has_test = True
    plays_tracks = True
    supports_quick = False
    config_fields = [
        {
            "key": "subsonic_url",
            "label": "Server URL",
            "type": "text",
            "placeholder": "https://navidrome.example.com",
            "help": "Base URL of your Navidrome / Subsonic server (no /rest).",
        },
        {
            "key": "subsonic_username",
            "label": "Username",
            "type": "text",
            "placeholder": "alice",
        },
        {
            "key": "subsonic_password",
            "label": "Password",
            "type": "password",
            "placeholder": "(enter to connect; not stored)",
            "help": "Used once to derive a login token. The password itself is "
                    "never saved -- only a salted token. Leave blank to keep the "
                    "existing connection.",
        },
    ]

    def configured(self):
        cfg = _config()
        return self.enabled() and bool(cfg["url"] and cfg["user"] and cfg["token"])

    supports_playlists = True

    def playlist_replace(self, name, song_ids):
        return playlist_replace(name, song_ids)

    def playlist_tracks(self, name):
        return playlist_tracks(name)

    def icon_name(self):
        """Navidrome's mark when that's what answered, else none.

        Other Subsonic servers have no icon in the set, and showing
        Navidrome's for an Airsonic box would be a small lie.
        """
        return "navidrome" if "navidrome" in (server_name() or "").lower() else None

    def display_label(self):
        """Whatever the server runs, once it has said so."""
        return server_name() or DEFAULT_LABEL

    def check(self):
        return ping()

    def scan(self, quick=False):  # noqa: ARG002 - no quick mode for Subsonic
        return scan()

    def albums_for_artist(self, name):
        return albums_for_artist(name)

    def find_track(self, artist, title):
        return find_track(artist, title)

    def progress(self):
        return get_progress()


register(SubsonicLibrary())
