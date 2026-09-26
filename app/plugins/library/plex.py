"""Library source: a Plex Media Server.

Talks the Plex Media Server API. Authentication is a single ``X-Plex-Token``
(stored verbatim, like the Subsonic token) -- paste a server token from plex.tv
or the server's own settings. We never run the PIN/JWT flow here; the token is
all PMS needs.

Owned albums come from the music library sections (``type == "artist"``). For
each music section we page ``/library/sections/{id}/albums``; every album row
carries its artist (``parentTitle``) and, when the Plex music agent has matched
it, a MusicBrainz id in its ``Guid`` array. Track counts and qualities come from
one bulk pass over the section's tracks (see _album_formats). Results
are written via db.replace_library_owned("plex", ...) as a full replace.
"""

import threading

import requests

from ... import db, gaps, names, quality, scans
from . import LibraryPlugin, merge_albums, register

CLIENT = "SimpleMusicTracker"
PAGE_SIZE = 500
SOURCE = "plex"
# Remembered once asked for: Plex's web app addresses every item as
# /server/<machineIdentifier>/details?key=..., so a link needs it.
MACHINE_SETTING = "library_plex_machine_id"
# Plex music section type ("artist" libraries), per the metadata-types table.
MUSIC_TYPE = "artist"
# Plex metadata type number for tracks (for /library/sections/{id}/all?type=10).
TRACK_TYPE = 10
# Tracks per page when sweeping a section for codecs.
TRACK_PAGE_SIZE = 1000

# Plex metadata type number for albums (for /library/sections/{id}/all?type=9).
ALBUM_TYPE = 9

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
        "url": (db.get_setting("plex_url") or "").strip().rstrip("/"),
        "token": (db.get_setting("plex_token") or "").strip(),
        "section": (db.get_setting("plex_section") or "").strip(),
    }


def _headers(cfg):
    return {
        "X-Plex-Token": cfg["token"],
        "X-Plex-Client-Identifier": CLIENT,
        "Accept": "application/json",
    }


def _get(cfg, path, *, params=None, headers=None):
    """Call a PMS endpoint, returning the 'MediaContainer' object.

    Raises RuntimeError on transport errors or a non-JSON / error response.
    """
    if not (cfg["url"] and cfg["token"]):
        raise RuntimeError("Plex server not configured")
    hdrs = _headers(cfg)
    if headers:
        hdrs.update(headers)
    try:
        resp = requests.get(f"{cfg['url']}{path}", params=params or {},
                            headers=hdrs, timeout=20)
        if resp.status_code == 401:
            raise RuntimeError("Plex rejected the token (401 Unauthorized)")
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not reach Plex server: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("Plex server returned a non-JSON response") from exc
    return body.get("MediaContainer") or {}


def ping():
    """Validate the stored token + server. Returns (ok, message)."""
    cfg = _config()
    if not cfg["url"]:
        return False, "No server URL set."
    if not cfg["token"]:
        return False, "No token set."
    try:
        mc = _get(cfg, "/")
    except RuntimeError as exc:
        return False, str(exc)
    name = mc.get("friendlyName") or cfg["url"]
    sections = _music_sections(cfg)
    return True, f"Connected to {name} - {len(sections)} music librar" + \
        ("y." if len(sections) == 1 else "ies.")


def _music_sections(cfg):
    """Section ids of music ('artist') libraries, honouring plex_section override."""
    if cfg["section"]:
        return [cfg["section"]]
    mc = _get(cfg, "/library/sections")
    out = []
    for d in mc.get("Directory") or []:
        if (d.get("type") or "").lower() == MUSIC_TYPE and d.get("key"):
            out.append(str(d["key"]))
    return out


def _mbid(album):
    """First MusicBrainz id from an album's Guid array, or None."""
    for g in album.get("Guid") or []:
        gid = g.get("id") or ""
        if gid.startswith("mbid://"):
            return gid[len("mbid://"):]
    return None


def _fetch_albums(cfg, section):
    """Page through a section's albums, yielding every album dict."""
    start = 0
    while True:
        mc = _get(
            cfg, f"/library/sections/{section}/all",
            params={"type": ALBUM_TYPE},
            headers={"X-Plex-Container-Start": str(start),
                     "X-Plex-Container-Size": str(PAGE_SIZE)},
        )
        albums = mc.get("Metadata") or []
        if not albums:
            break
        for alb in albums:
            yield alb
        total = mc.get("totalSize")
        start += len(albums)
        if len(albums) < PAGE_SIZE or (total is not None and start >= total):
            break


def _machine_id(cfg):
    """This server's identifier, which its web app puts in every item URL."""
    stored = db.get_setting(MACHINE_SETTING)
    if stored:
        return stored
    try:
        found = (_get(cfg, "/identity") or {}).get("machineIdentifier")
    except Exception:  # noqa: BLE001 - without it we just link to the server
        found = None
    if found:
        db.set_setting(MACHINE_SETTING, found)
    return found


def _web_url(cfg, rating_key):
    """Plex's own page for one item, or the server when it can't be built."""
    base = (cfg.get("url") or "").rstrip("/")
    if not base:
        return None
    machine = _machine_id(cfg)
    if not (machine and rating_key):
        return base
    return (f"{base}/web/index.html#!/server/{machine}/details"
            f"?key=%2Flibrary%2Fmetadata%2F{rating_key}")


def find_track(artist, title):
    """The user's own copy of one track, as a stream descriptor, or None.

    Plex indexes tracks as type 10; the title filter narrows the section and
    grandparentTitle (the track's artist) confirms it's the right recording.
    """
    cfg = _config()
    if not (cfg["url"] and cfg["token"]):
        return None
    for section in _music_sections(cfg):
        mc = _get(cfg, f"/library/sections/{section}/all",
                  params={"type": TRACK_TYPE, "title": title, "limit": 30})
        for track in mc.get("Metadata") or []:
            # Word-wise (see app/names.py): a substring match would hand back
            # another artist whose name merely starts the same way.
            if not names.same_name(artist, track.get("grandparentTitle")):
                continue
            if not names.same_name(title, track.get("title")):
                continue
            part = next(
                (pt for media in track.get("Media") or []
                 for pt in media.get("Part") or [] if pt.get("key")),
                None,
            )
            if not part:
                continue
            return {
                "kind": "url",
                "url": f"{cfg['url']}{part['key']}",
                # Only the token: the JSON Accept header of _headers would ask
                # Plex for metadata rather than the audio file.
                "headers": {"X-Plex-Token": cfg["token"],
                            "X-Plex-Client-Identifier": CLIENT},
                "title": track.get("title"),
                "album": track.get("parentTitle"),
                "page_url": _web_url(cfg, track.get("ratingKey")),
                "duration": int(track["duration"]) // 1000 if track.get("duration") else None,
                "suffix": (part.get("container") or "").lower() or None,
            }
    return None


# Plex names codecs, not file extensions; these are the ones that differ.
_CODEC_EXTENSIONS = {"vorbis": "ogg", "pcm": "wav", "alac": "alac"}

# Plex doesn't report bit depth (the section listing carries no stream details,
# even with includeStreams), so a lossless file above this bitrate is taken for
# 24-bit. Measured against the same records on a Subsonic server, which does
# report depth outright: of 1,927 FLAC albums held by both, 16-bit ones peak at
# 1064 kbps for the 90th percentile while real 24-bit ones start at 1424, so
# this sits in the gap. At 1000 kbps a third of the CD rips were called 24-bit.
_HI_RES_KBPS = 1400

# Names that say hi-res outright, whatever the bitrate came out at.
_HI_RES_HINTS = ("24bit", "24-bit", "24 bit", "96khz", "96 khz", "192khz",
                 "192 khz", "hi-res", "hires")


def _track_quality(track):
    """Quality label for one Plex track row, or None if it says nothing."""
    media = (track.get("Media") or [{}])[0]
    codec = (media.get("audioCodec") or media.get("container") or "").lower()
    try:
        kbps = int(media.get("bitrate") or 0)
    except (TypeError, ValueError):
        kbps = 0
    if codec == "flac":
        path = ((media.get("Part") or [{}])[0].get("file") or "").lower()
        hi_res = kbps >= _HI_RES_KBPS or any(h in path for h in _HI_RES_HINTS)
        return "FLAC 24bit" if hi_res else "FLAC"
    return quality.file_label(_CODEC_EXTENSIONS.get(codec, codec), kbps)


def _album_formats(cfg, section, counts=None):
    """{album ratingKey: best quality label} from one pass over the tracks.

    Album rows carry no media details, so the codecs have to come from the
    tracks -- but paged in bulk (a thousand at a time, folded by their parent
    album) rather than one request per album. Nor do they carry a track count
    (no leafCount in a section listing), so *counts*, when given, is filled
    with {album ratingKey: tracks} on the same pass.
    """
    out = {}
    start = 0
    while True:
        mc = _get(cfg, f"/library/sections/{section}/all", params={
            "type": TRACK_TYPE,
            "X-Plex-Container-Start": start,
            "X-Plex-Container-Size": TRACK_PAGE_SIZE,
        })
        rows = mc.get("Metadata") or []
        for track in rows:
            album = track.get("parentRatingKey")
            if not album:
                continue
            key = str(album)
            out[key] = gaps.better_quality(out.get(key), _track_quality(track))
            if counts is not None:
                counts[key] = counts.get(key, 0) + 1
        scans.raise_if_stopped()
        if len(rows) < TRACK_PAGE_SIZE:
            return out
        start += TRACK_PAGE_SIZE


def _album_quality(cfg, rating_key):
    """(best quality, track count) of one album, read from that album directly."""
    if not rating_key:
        return None, None
    mc = _get(cfg, f"/library/metadata/{rating_key}/children")
    best = None
    tracks = mc.get("Metadata") or []
    for track in tracks:
        best = gaps.better_quality(best, _track_quality(track))
    return best, len(tracks) or None


def albums_for_artist(name):
    """Owned albums for one artist: page each music section, keep matches.

    Plex's listing has no reliable single-artist filter, so reuse the same
    paging the full scan uses and match on the album's parentTitle (the artist).
    """
    cfg = _config()
    want = (name or "").strip().lower()
    albums = []
    tracks = 0
    for section in _music_sections(cfg):
        for alb in _fetch_albums(cfg, section):
            if (alb.get("parentTitle") or "").strip().lower() != want:
                continue
            title = (alb.get("title") or "").strip()
            if title:
                # A handful of albums: read each one's tracks rather than
                # sweeping the section.
                best, count = _album_quality(cfg, alb.get("ratingKey"))
                albums.append({
                    "title": title,
                    "rg_mbid": _mbid(alb),
                    "format": best,
                    "tracks": count,
                })
                tracks += count or 0
    return {"albums": merge_albums(albums), "track_count": tracks}


def scan():
    """Scan the server and replace this source's owned data. Returns a summary."""
    cfg = _config()
    with _scan_lock:
        _set_progress(running=True, message="connecting", albums=0, artists=0)
        try:
            sections = _music_sections(cfg)
            if not sections:
                raise RuntimeError("No music libraries found on this server")
            by_artist = {}
            seen = 0
            for section in sections:
                _set_progress(message="reading track formats", albums=seen,
                              artists=len(by_artist))
                counts = {}
                formats = _album_formats(cfg, section, counts)
                for alb in _fetch_albums(cfg, section):
                    artist = (alb.get("parentTitle") or "").strip()
                    title = (alb.get("title") or "").strip()
                    if not artist or not title:
                        continue
                    rec = by_artist.setdefault(
                        artist.lower(), {"name": artist, "track_count": 0, "albums": []}
                    )
                    rec["albums"].append({
                        "title": title,
                        "rg_mbid": _mbid(alb),
                        "format": formats.get(str(alb.get("ratingKey"))),
                        "tracks": counts.get(str(alb.get("ratingKey"))),
                    })
                    rec["track_count"] += counts.get(str(alb.get("ratingKey")), 0)
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


class PlexLibrary(LibraryPlugin):
    key = SOURCE
    icon = "plex"
    label = "Plex"
    description = "Read owned albums from a Plex Media Server's music libraries."
    enabled_setting = "library_plex_enabled"
    has_test = True
    plays_tracks = True
    supports_quick = False
    config_fields = [
        {
            "key": "plex_url",
            "label": "Server URL",
            "type": "text",
            "placeholder": "http://plex.example.com:32400",
            "help": "Base URL of your Plex Media Server (include the port, "
                    "usually 32400).",
        },
        {
            "key": "plex_token",
            "label": "Token",
            "type": "password",
            "placeholder": "(X-Plex-Token; leave blank to keep current)",
            "help": "A Plex auth token (X-Plex-Token). Leave blank to keep the "
                    "existing one.",
        },
        {
            "key": "plex_section",
            "label": "Music library (optional)",
            "type": "text",
            "placeholder": "(section id; blank = all music libraries)",
            "help": "Limit to one music library by its section id. Blank scans "
                    "every music library.",
        },
    ]

    def configured(self):
        cfg = _config()
        return self.enabled() and bool(cfg["url"] and cfg["token"])

    def check(self):
        return ping()

    def scan(self, quick=False):  # noqa: ARG002 - no quick mode for Plex
        return scan()

    def albums_for_artist(self, name):
        return albums_for_artist(name)

    def find_track(self, artist, title):
        return find_track(artist, title)

    def progress(self):
        return get_progress()


register(PlexLibrary())
