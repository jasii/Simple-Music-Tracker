"""Download-client plugins: fetch a release from somewhere and download it.

A :class:`DownloaderPlugin` is handed an artist and an album title and finds
the files itself -- Soulseek via slskd, today. The Missing page and the album
page grab through whichever client comes first in the user's order.

Like the other kinds, clients are opt-in and configured from the Plugins tab via
``enabled_setting`` / ``config_fields``.

Importing this package registers every bundled downloader.
"""

from .. import Plugin, register, get_plugins, get_plugin  # noqa: F401 - re-exported

from ... import db

# Values that mean "off" for an enable toggle stored as a string setting.
_OFF = ("false", "0", "off", "no", "")


def setting_on(key):
    """True when the string setting *key* holds something that means "on"."""
    return (db.get_setting(key) or "").strip().lower() not in _OFF


class DownloaderPlugin(Plugin):
    """A download client.

    Subclasses set ``key`` / ``label`` and implement :meth:`download_release`:
    given an artist and an album title, find the files on the client's own
    network and fetch them.
    """

    kind = "downloader"
    enabled_setting = None
    config_fields = []
    has_test = False

    def enabled(self):
        """Clients are opt-in: off unless the toggle is explicitly on."""
        if not self.enabled_setting:
            return True
        return setting_on(self.enabled_setting)

    def configured(self):
        """Ready to take work? Default: just enabled."""
        return self.enabled()

    def download_release(self, artist, title, formats=None):
        """Find *title* by *artist* on the client's own network and fetch it.

        *formats* is an ordered list of acceptable file extensions (the quality profile's), or
        None to use the client's own preference. Returns a short message for
        the UI; raises RuntimeError with a user-facing reason when nothing
        suitable is found.
        """
        raise NotImplementedError

    def has_release(self, artist, title):
        """Is this release already in the client? Optional; default: unknown.

        Used to stop a second copy being sent when a first one is still
        downloading (or finished but not yet scanned). Returning False -- the
        default -- means "can't say", never "definitely not".
        """
        return False

    def check(self):
        """Optional health check. Return ``(ok, message)`` or ``None``."""
        return None

    def describe(self):
        data = super().describe()
        data.update({
            "enabled": self.enabled(),
            "enabled_setting": self.enabled_setting,
            "configured": self.configured(),
            "config_fields": self.config_fields,
            "has_test": self.has_test,
        })
        return data


def clients():
    """Configured download clients."""
    return [p for p in get_plugins("downloader") if p.configured()]


# Register the bundled clients (import side effect calls register()).
from . import slskd  # noqa: E402,F401
