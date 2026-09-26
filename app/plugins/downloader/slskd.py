"""Download client: slskd (https://github.com/slskd/slskd), a Soulseek daemon.

You search the Soulseek network, get back a pile of per-file results from
other users, and enqueue the files you want from one of them. So this client
is handed an artist and an album title and does the searching itself.

Choosing what to download is the interesting part. Results arrive as loose
files, so they're regrouped by the folder they live in (a folder is usually
one release), folders are filtered to the formats the user wants, and the best
is picked: preferred format first, then most files, then a peer who can start
now -- a free upload slot and a fast line beat a long queue.

API: REST under /api/v0, authenticated with an API key in ``X-API-Key`` or a
JWT from ``POST /session`` with the web UI credentials.
"""

import os
import re
import threading
import time
import uuid
from urllib.parse import quote

import requests

from ... import db
from . import DownloaderPlugin, register, setting_on

_TIMEOUT = (5, 30)
# Audio files worth grabbing; anything else in the folder (art, logs, cues) is
# left behind -- the scanner only cares about audio, and peers' queues are a
# shared resource.
_AUDIO_EXTENSIONS = (".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma",
                     ".wav", ".aiff", ".aif", ".ape")
# How long to let a search run before picking from what came back.
_DEFAULT_SEARCH_SECONDS = 20
_POLL_SECONDS = 1.5


def _folder_of(filename):
    """The folder part of a Soulseek path (peers use Windows separators)."""
    return (filename or "").replace("/", "\\").rsplit("\\", 1)[0]


# "01 - ", "1-01. ", "A1 ", "101 " -- the numbering peers put before a title.
_NUMBER_PREFIX = re.compile(r"^\s*(?:(?:cd|disc)\s*\d+\s*[-_. ]\s*)?"
                            r"([a-d]?\d{1,3})(?:[-_.](\d{1,3}))?[\s._)\]-]+",
                            re.IGNORECASE)


def track_key(filename, artist=None):
    """(track number or None, match key of the title) for one peer's file.

    Peers name files every way there is -- "01 - Title.flac", "Artist - 01 -
    Title.flac", "1-03 Title.mp3" -- so the number and any leading artist name
    are peeled off before the title is compared.
    """
    base = (filename or "").replace("/", "\\").rsplit("\\", 1)[-1]
    base = os.path.splitext(base)[0]
    artist_key = db.match_key(artist) if artist else ""
    if artist_key and " - " in base:
        head, rest = base.split(" - ", 1)
        if db.match_key(head) == artist_key:
            base = rest
    number = None
    match = _NUMBER_PREFIX.match(base)
    if match:
        # "1-04": disc one, track four.
        digits = re.sub(r"\D", "", match.group(2) or match.group(1))
        number = int(digits) if digits else None
        base = base[match.end():]
    if artist_key and " - " in base:
        head, rest = base.split(" - ", 1)
        if db.match_key(head) == artist_key:
            base = rest
    return number, db.match_key(base)


def same_track(a, b):
    """Do two track keys (see track_key) name the same song?"""
    num_a, title_a = a
    num_b, title_b = b
    if title_a and title_a == title_b:
        return True
    # Tags that differ only by a "(Remastered)" tail, or a title the peer cut
    # short: the numbers have to agree before containment counts.
    if num_a is not None and num_a == num_b and title_a and title_b:
        return title_a in title_b or title_b in title_a
    return False


def transfer_state(state):
    """slskd's flag string ("Completed, Errored") reduced to one word.

    done | failed | queued (waiting on the peer) | downloading | pending.
    """
    s = (state or "").lower()
    if "succeeded" in s:
        return "done"
    if "completed" in s or any(w in s for w in ("errored", "rejected", "timedout",
                                                 "cancelled", "aborted")):
        return "failed"
    if "inprogress" in s or "initializing" in s:
        return "downloading"
    if "queued" in s and "remotely" in s:
        return "queued"
    return "pending"


def _extension(f):
    ext = (f.get("extension") or "").lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    return ext or os.path.splitext((f.get("filename") or "").lower())[1]


class SlskdPlugin(DownloaderPlugin):
    """Search Soulseek through slskd and enqueue a release's files."""

    key = "slskd"
    label = "slskd (Soulseek)"
    icon = "slskd"
    description = (
        "Fetch releases from Soulseek through a slskd instance. It searches "
        "the network itself: give it an album and it finds a copy, downloads "
        "it, and retries or switches peer for any file that fails."
    )
    has_test = True

    url_setting = "downloader_slskd_url"
    api_key_setting = "downloader_slskd_api_key"
    username_setting = "downloader_slskd_username"
    password_setting = "downloader_slskd_password"
    formats_setting = "downloader_slskd_formats"
    min_files_setting = "downloader_slskd_min_files"
    timeout_setting = "downloader_slskd_search_seconds"
    enabled_setting = "downloader_slskd_enabled"
    mix_setting = "downloader_slskd_mix_peers"
    stall_setting = "downloader_slskd_stall_hours"

    config_fields = [
        {"key": url_setting, "label": "slskd address",
         "placeholder": "http://localhost:5030",
         "help": "Where slskd's web UI listens. http:// is assumed if you "
                 "leave the scheme off."},
        {"key": api_key_setting, "label": "API key", "type": "password",
         "help": "From slskd's config (web.authentication.api_keys). Simplest "
                 "option; leave blank to log in with the web UI credentials "
                 "below instead."},
        {"key": username_setting, "label": "Web UI username",
         "placeholder": "slskd",
         "help": "Only used when no API key is set."},
        {"key": password_setting, "label": "Web UI password", "type": "password",
         "help": "Only used when no API key is set."},
        {"key": formats_setting, "label": "Preferred formats",
         "placeholder": "flac,mp3",
         "help": "Comma-separated, best first. A folder in the first format "
                 "wins over one in the second, whatever else it has going for it."},
        {"key": min_files_setting, "label": "Minimum files",
         "placeholder": "2",
         "help": "Skip folders with fewer audio files than this -- keeps a "
                 "single stray track from being mistaken for the album. Use 1 "
                 "when grabbing singles."},
        {"key": timeout_setting, "label": "Search seconds",
         "placeholder": str(_DEFAULT_SEARCH_SECONDS),
         "help": "How long to let results come in before choosing. Soulseek "
                 "peers answer at their own pace, so shorter means fewer options."},
        {"key": mix_setting, "label": "Fill gaps from other peers",
         "type": "checkbox",
         "help": "When some of an album's files fail, fetch just those tracks "
                 "from another peer's copy in the same format. Off: retry the "
                 "same peer, then fetch someone else's whole folder instead."},
        {"key": stall_setting, "label": "Give up on a queue after (hours)",
         "placeholder": "12",
         "help": "A file still waiting in a peer's upload queue after this long "
                 "is fetched from someone else."},
    ]

    def __init__(self):
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._jwt = None
        self._jwt_expires = 0.0

    # --- config ---------------------------------------------------------

    def _url(self):
        url = (db.get_setting(self.url_setting) or "").strip().rstrip("/")
        if url and "://" not in url:
            url = f"http://{url}"
        return url

    def _api_key(self):
        return (db.get_setting(self.api_key_setting) or "").strip()

    def _formats(self, override=None):
        """Acceptable extensions, best first: the caller's list or the setting."""
        raw = override if override else (db.get_setting(self.formats_setting) or "flac,mp3")
        if isinstance(raw, str):
            raw = raw.lower().split(",")
        out = []
        for part in raw:
            ext = str(part).strip().lower().lstrip(".")
            if ext and "." + ext not in out:
                out.append("." + ext)
        return out or [".flac", ".mp3"]

    def _min_files(self):
        try:
            return max(1, int(db.get_setting(self.min_files_setting) or 2))
        except (TypeError, ValueError):
            return 2

    def _search_seconds(self):
        try:
            return max(5, int(db.get_setting(self.timeout_setting) or _DEFAULT_SEARCH_SECONDS))
        except (TypeError, ValueError):
            return _DEFAULT_SEARCH_SECONDS

    def configured(self):
        if not (self.enabled() and self._url()):
            return False
        return bool(self._api_key() or (db.get_setting(self.username_setting) or "").strip())

    # --- API ------------------------------------------------------------

    def _auth_headers(self):
        """An API key if there is one, otherwise a cached JWT from /session."""
        key = self._api_key()
        if key:
            return {"X-API-Key": key}
        if self._jwt and time.time() < self._jwt_expires - 60:
            return {"Authorization": f"Bearer {self._jwt}"}
        username = (db.get_setting(self.username_setting) or "").strip()
        password = db.get_setting(self.password_setting) or ""
        try:
            resp = self._session.post(
                f"{self._url()}/api/v0/session",
                json={"username": username, "password": password},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"could not reach slskd: {exc}") from exc
        if resp.status_code in (401, 403):
            raise RuntimeError("slskd rejected the web UI username/password")
        if not resp.ok:
            raise RuntimeError(f"slskd returned HTTP {resp.status_code} logging in")
        data = resp.json() or {}
        self._jwt = data.get("token")
        self._jwt_expires = float(data.get("expires") or 0)
        if not self._jwt:
            raise RuntimeError("slskd did not return a session token")
        return {"Authorization": f"Bearer {self._jwt}"}

    def _api(self, method, path, **kwargs):
        url = self._url()
        if not url:
            raise RuntimeError("slskd address not configured")
        try:
            resp = self._session.request(
                method, f"{url}/api/v0{path}",
                headers=self._auth_headers(), timeout=_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError(f"could not reach slskd: {exc}") from exc
        if resp.status_code in (401, 403):
            # A JWT may simply have aged out; drop it so the next call re-logs in.
            self._jwt = None
            raise RuntimeError("slskd rejected the credentials")
        if not resp.ok:
            raise RuntimeError(f"slskd returned HTTP {resp.status_code}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    # --- searching ------------------------------------------------------

    def _search(self, text):
        """Run one search to completion (or timeout) and return its responses."""
        seconds = self._search_seconds()
        search_id = str(uuid.uuid4())
        self._api("POST", "/searches", json={
            "id": search_id,
            "searchText": text,
            "searchTimeout": seconds * 1000,
        })
        deadline = time.time() + seconds + 10
        search = None
        while time.time() < deadline:
            time.sleep(_POLL_SECONDS)
            search = self._api("GET", f"/searches/{search_id}?includeResponses=true")
            state = ((search or {}).get("state") or "").lower()
            if "completed" in state or "cancelled" in state or "errored" in state:
                break
        return (search or {}).get("responses") or []

    def _candidates(self, responses, formats=None, minimum=None):
        """Group every response's files into per-folder candidates."""
        wanted = self._formats(formats)
        minimum = self._min_files() if minimum is None else minimum
        out = []
        for response in responses:
            username = response.get("username")
            if not username:
                continue
            folders = {}
            for f in response.get("files") or []:
                if f.get("isLocked"):
                    continue
                ext = _extension(f)
                if ext not in wanted:
                    continue
                folder = folders.setdefault(_folder_of(f.get("filename")),
                                            {"files": [], "exts": set()})
                folder["files"].append({"filename": f.get("filename"),
                                        "size": f.get("size") or 0})
                folder["exts"].add(ext)
            for path, folder in folders.items():
                if len(folder["files"]) < minimum:
                    continue
                # Rank by the best format present in the folder.
                rank = min(wanted.index(e) for e in folder["exts"])
                out.append({
                    "username": username,
                    "path": path,
                    "files": folder["files"],
                    "format": sorted(folder["exts"], key=wanted.index)[0].lstrip("."),
                    "rank": rank,
                    "free_slot": bool(response.get("hasFreeUploadSlot")),
                    "speed": response.get("uploadSpeed") or 0,
                    "queue": response.get("queueLength") or 0,
                })
        # Preferred format, then a fuller folder, then whoever can start now.
        out.sort(key=lambda c: (c["rank"], -len(c["files"]), not c["free_slot"],
                                c["queue"], -c["speed"]))
        return out

    def search_release(self, artist, title, formats=None, minimum=None):
        """Every usable folder of *title* by *artist* on Soulseek, best first.

        Each: {username, path, files: [{filename, size}], format, ...}. The
        search is serialised: slskd copes with several, but peers answering
        one search at a time is kinder to the network.
        """
        if not self.configured():
            raise RuntimeError("slskd is not configured")
        with self._lock:
            responses = self._search(f"{artist} {title}")
        return self._candidates(responses, formats, minimum)

    def search_track(self, artist, track, formats=None):
        """Single files matching one song, best first (as one-file candidates)."""
        if not self.configured():
            raise RuntimeError("slskd is not configured")
        with self._lock:
            responses = self._search(f"{artist} {track}")
        want = (None, db.match_key(track))
        out = []
        for cand in self._candidates(responses, formats, minimum=1):
            for f in cand["files"]:
                if same_track(track_key(f["filename"], artist), want):
                    out.append({**cand, "files": [f]})
                    break
        return out

    def enqueue(self, username, files):
        """Ask *username* for *files* ([{filename, size}])."""
        self._api("POST", f"/transfers/downloads/{quote(username, safe='')}",
                  json=[{"filename": f["filename"], "size": f.get("size") or 0}
                        for f in files])

    def transfers(self):
        """{(username, filename): {id, state, percent, error}} for every download."""
        out = {}
        for user in self._api("GET", "/transfers/downloads") or []:
            username = user.get("username")
            for directory in user.get("directories") or []:
                for f in directory.get("files") or []:
                    out[(username or f.get("username"), f.get("filename"))] = {
                        "id": f.get("id"),
                        "state": transfer_state(f.get("state")),
                        "raw_state": f.get("state"),
                        "percent": f.get("percentComplete") or 0,
                        "error": f.get("exception"),
                    }
        return out

    def cancel(self, username, transfer_id, remove=True):
        """Cancel one transfer (and drop it from slskd's list with *remove*)."""
        if not transfer_id:
            return
        suffix = "?remove=true" if remove else ""
        try:
            self._api("DELETE", f"/transfers/downloads/{quote(username, safe='')}/"
                                f"{quote(str(transfer_id), safe='')}{suffix}")
        except RuntimeError:
            pass  # already gone is as good as cancelled

    def download_release(self, artist, title, formats=None):
        """Search Soulseek for *title* by *artist* and enqueue the best folder.

        *formats* (from the quality profile) overrides the plugin's own format
        preference, so one ladder governs every client. The grabber goes
        through app/downloads instead, which follows the files afterwards;
        this is the plain fire-and-forget version.
        """
        candidates = self.search_release(artist, title, formats)
        if not candidates:
            raise RuntimeError(
                f"nothing on Soulseek for {artist} - {title} in "
                f"{', '.join(e.lstrip('.') for e in self._formats(formats))}")
        best = candidates[0]
        self.enqueue(best["username"], best["files"])
        return (f"Queued {len(best['files'])} {best['format'].upper()} files "
                f"from {best['username']}.")

    def formats_label(self, formats=None):
        return ", ".join(e.lstrip(".") for e in self._formats(formats))

    def mix_peers(self):
        return setting_on(self.mix_setting)

    def stall_seconds(self):
        try:
            hours = float(db.get_setting(self.stall_setting) or 12)
        except (TypeError, ValueError):
            hours = 12
        return max(0.25, hours) * 3600

    def has_release(self, artist, title):
        """True when this release is already downloading or queued in slskd.

        A followed download (app/downloads) answers first: one that ended short
        still has its folder in slskd's list, but it's exactly the release a
        second attempt is for.
        """
        from ... import downloads  # local: downloads imports this module's helpers
        held = downloads.holds(artist, title)
        if held is not None:
            return held
        want_artist, want_title = db.match_key(artist), db.match_key(title)
        if not want_title:
            return False
        try:
            users = self._api("GET", "/transfers/downloads")
        except RuntimeError:
            return False
        for user in users or []:
            for directory in user.get("directories") or []:
                name = db.match_key(directory.get("directory"))
                if want_title in name and (not want_artist or want_artist in name):
                    return True
        return False

    def check(self):
        """Confirm the address and credentials by reading slskd's version."""
        if not self._url():
            return (False, "slskd address not configured.")
        try:
            version = self._api("GET", "/application/version")
        except RuntimeError as exc:
            return (False, str(exc))
        if isinstance(version, dict):
            version = version.get("full") or version.get("version")
        return (True, f"Connected to slskd {version}." if version
                else "Connected to slskd.")


register(SlskdPlugin())
