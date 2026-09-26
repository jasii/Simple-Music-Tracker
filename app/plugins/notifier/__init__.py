"""Notifier plugins: tell the user something happened, somewhere they'll see it.

The built-in webhook already posts a JSON body wherever you point it, which is
flexible and fiddly: every service wants a different shape, so using it means
hand-writing a template. A notifier plugin knows one service's shape already --
you give it a server and a topic and it sends a readable message.

Each notifier subscribes to the events it wants (a checkbox per event in the
Plugins tab), so one instance can carry release announcements while another
only reports grabs. Callers fire an event with :func:`notify` and never learn
which services are configured.

Importing this package registers every bundled notifier.
"""

import threading

from .. import Plugin, register, get_plugins, get_plugin  # noqa: F401 - re-exported

from ... import db

# Values that mean "off" for an enable toggle stored as a string setting.
_OFF = ("false", "0", "off", "no", "")

# The events a notifier can subscribe to: key -> label shown in settings.
EVENTS = {
    "release_found": "New release found",
    "release_day": "Release out today",
    "grab_sent": "Release sent to a download client",
    "download_done": "Soulseek download finished",
    "download_partial": "Soulseek download ended incomplete",
    "scan_done": "Library scan finished",
}


def setting_on(key):
    """True when the string setting *key* holds something that means "on"."""
    return (db.get_setting(key) or "").strip().lower() not in _OFF


class NotifierPlugin(Plugin):
    """A place to send messages.

    Subclasses set ``key`` / ``label`` and implement :meth:`send`.
    """

    kind = "notifier"
    enabled_setting = None
    config_fields = []
    has_test = False
    # Settings key prefix for the per-event checkboxes ("<prefix>_<event>").
    event_prefix = None

    def enabled(self):
        """Notifiers are opt-in: off unless the toggle is explicitly on."""
        if not self.enabled_setting:
            return True
        return setting_on(self.enabled_setting)

    def configured(self):
        """Ready to send? Default: just enabled."""
        return self.enabled()

    def wants(self, event):
        """Is this notifier subscribed to *event*?"""
        if not self.event_prefix:
            return True
        return setting_on(f"{self.event_prefix}_{event}")

    def send(self, title, message, url=None, event=None):
        """Deliver one message. Raise RuntimeError with a user-facing reason."""
        raise NotImplementedError

    def check(self):
        """Optional health check. Return ``(ok, message)`` or ``None``."""
        return None

    def event_fields(self):
        """Config-field descriptors for this notifier's event checkboxes."""
        if not self.event_prefix:
            return []
        return [
            {"key": f"{self.event_prefix}_{event}", "label": label,
             "type": "checkbox"}
            for event, label in EVENTS.items()
        ]

    def describe(self):
        data = super().describe()
        data.update({
            "enabled": self.enabled(),
            "enabled_setting": self.enabled_setting,
            "configured": self.configured(),
            "config_fields": list(self.config_fields) + self.event_fields(),
            "has_test": self.has_test,
        })
        return data


def subscribers(event):
    """Configured notifiers subscribed to *event*."""
    return [p for p in get_plugins("notifier") if p.configured() and p.wants(event)]


def notify(event, title, message, url=None, background=True):
    """Send one event to every notifier subscribed to it. Returns how many.

    Delivery is fire-and-forget by default: a slow or broken notification
    service must never hold up a scan, a refresh or a request.
    """
    targets = subscribers(event)
    if not targets:
        return 0

    def deliver():
        for plugin in targets:
            try:
                plugin.send(title, message, url=url, event=event)
            except Exception:  # noqa: BLE001 - one bad service can't break the rest
                pass

    if background:
        threading.Thread(target=deliver, daemon=True).start()
    else:
        deliver()
    return len(targets)


# Register the bundled notifiers (import side effect calls register()).
from . import ntfy  # noqa: E402,F401
from . import gotify  # noqa: E402,F401
from . import discord  # noqa: E402,F401
