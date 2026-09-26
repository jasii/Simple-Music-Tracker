"""Library source: a local music directory scanned with the existing scanner.

Thin wrapper around :mod:`app.scanner` -- the file walk and tag reading live
there; this just presents the scanner as a :class:`LibraryPlugin` so it sits
alongside other library sources (Subsonic, ...) in the registry and settings UI.
"""

import os

from ... import db, scanner
from . import LibraryPlugin, register

AUDIO_EXTENSIONS = scanner.AUDIO_EXTENSIONS


def _music_dir():
    return (db.get_setting("music_directory") or "").strip()


class FilesystemLibrary(LibraryPlugin):
    key = "filesystem"
    label = "Filesystem"
    description = "Scan a local music folder (inside the container) for owned albums."
    enabled_setting = "library_filesystem_enabled"
    has_test = True
    plays_tracks = True
    supports_quick = True
    config_fields = [
        {
            "key": "music_directory",
            "label": "Music directory (inside the container)",
            "type": "text",
            "placeholder": "/music",
            "help": "Mount your library here, e.g. -v /path/to/music:/music.",
        },
    ]

    def configured(self):
        return self.enabled() and os.path.isdir(_music_dir())

    def check(self):
        directory = _music_dir()
        if not directory:
            return False, "No music directory set."
        if not os.path.isdir(directory):
            return False, f"Not a directory: {directory}"
        # Cheap top-level peek so we don't walk a huge tree just to validate.
        try:
            files = 0
            for _root, _dirs, names in os.walk(directory):
                files += sum(1 for n in names
                             if os.path.splitext(n)[1].lower() in AUDIO_EXTENSIONS)
                if files >= 1:
                    break
        except OSError as exc:
            return False, f"Could not read {directory}: {exc}"
        if files:
            return True, f"Found audio files under {directory}."
        return False, f"No audio files found under {directory}."

    def scan(self, quick=False):
        directory = _music_dir()
        return scanner.scan_directory(directory, quick=quick)

    def find_track(self, artist, title):
        path = scanner.find_track_file(artist, title)
        if not path:
            return None
        return {
            "kind": "file",
            "path": path,
            "title": os.path.splitext(os.path.basename(path))[0],
            "suffix": os.path.splitext(path)[1].lstrip(".").lower() or None,
        }

    def progress(self):
        return scanner.get_scan_state()


register(FilesystemLibrary())
