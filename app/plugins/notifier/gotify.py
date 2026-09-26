"""Notifier: Gotify (https://gotify.net), a self-hosted push server.

One POST to ``{server}/message?token=<app token>`` with a title, message and
priority. The token is an *application* token from the Gotify UI, not a client
one -- clients read, applications write.
"""

import requests

from ... import db
from . import NotifierPlugin, register

_TIMEOUT = 10


class GotifyNotifier(NotifierPlugin):
    """Push notifications through a Gotify server."""

    key = "gotify"
    label = "Gotify"
    icon = "gotify"
    description = ("Push to a self-hosted Gotify server using an application "
                   "token.")
    has_test = True

    server_setting = "notifier_gotify_server"
    token_setting = "notifier_gotify_token"
    priority_setting = "notifier_gotify_priority"
    enabled_setting = "notifier_gotify_enabled"
    event_prefix = "notifier_gotify_on"

    config_fields = [
        {"key": server_setting, "label": "Server", "placeholder": "http://gotify:80",
         "help": "Where Gotify listens. http:// is assumed if you leave the "
                 "scheme off."},
        {"key": token_setting, "label": "Application token", "type": "password",
         "help": "Created under Apps in the Gotify UI (not a client token)."},
        {"key": priority_setting, "label": "Priority", "placeholder": "5",
         "help": "0-10. Gotify's clients decide how loudly to announce each "
                 "level; 5 is a normal notification."},
    ]

    def server(self):
        value = (db.get_setting(self.server_setting) or "").strip().rstrip("/")
        if value and "://" not in value:
            value = "http://" + value
        return value

    def token(self):
        return (db.get_setting(self.token_setting) or "").strip()

    def priority(self):
        try:
            return max(0, min(int(db.get_setting(self.priority_setting) or 5), 10))
        except (TypeError, ValueError):
            return 5

    def configured(self):
        return self.enabled() and bool(self.server() and self.token())

    def send(self, title, message, url=None, event=None):
        if not self.configured():
            raise RuntimeError("Gotify is not configured")
        payload = {"title": title, "message": message, "priority": self.priority()}
        if url:
            # Gotify renders a click action from these extras.
            payload["extras"] = {
                "client::notification": {"click": {"url": url}},
            }
        try:
            resp = requests.post(f"{self.server()}/message",
                                 params={"token": self.token()},
                                 json=payload, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"could not reach Gotify: {exc}") from exc
        if resp.status_code in (401, 403):
            raise RuntimeError("Gotify rejected the application token")
        if not resp.ok:
            raise RuntimeError(f"Gotify returned HTTP {resp.status_code}")

    def check(self):
        if not self.server() or not self.token():
            return (False, "Set the server and application token first.")
        try:
            self.send("Simple Music Tracker", "Test notification.")
        except RuntimeError as exc:
            return (False, str(exc))
        return (True, "Sent a test message to Gotify.")


register(GotifyNotifier())
