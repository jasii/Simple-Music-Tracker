"""Notifier: ntfy (https://ntfy.sh, or a self-hosted server).

A message is a plain POST to ``{server}/{topic}`` with the body as the text and
a few ``X-`` headers for the title, priority and a click-through link. Public
topics need no credentials; protected ones take a token.
"""

import requests

from ... import db
from . import NotifierPlugin, register, setting_on  # noqa: F401 - setting_on re-exported

_TIMEOUT = 10


class NtfyNotifier(NotifierPlugin):
    """Push notifications through an ntfy topic."""

    key = "ntfy"
    label = "ntfy"
    icon = "ntfy"
    description = ("Push to an ntfy topic -- ntfy.sh or your own server. "
                   "Subscribe to the topic on your phone and releases land there.")
    has_test = True

    server_setting = "notifier_ntfy_server"
    topic_setting = "notifier_ntfy_topic"
    token_setting = "notifier_ntfy_token"
    enabled_setting = "notifier_ntfy_enabled"
    event_prefix = "notifier_ntfy_on"

    config_fields = [
        {"key": server_setting, "label": "Server", "placeholder": "https://ntfy.sh",
         "help": "Leave as ntfy.sh or point at your own instance."},
        {"key": topic_setting, "label": "Topic", "placeholder": "music-tracker",
         "help": "The topic you subscribe to in the ntfy app. Anyone who knows "
                 "a public topic name can read it, so pick something unguessable."},
        {"key": token_setting, "label": "Access token", "type": "password",
         "help": "Only needed for a protected topic (tk_... from your server)."},
    ]

    def server(self):
        value = (db.get_setting(self.server_setting) or "https://ntfy.sh").strip().rstrip("/")
        if value and "://" not in value:
            value = "https://" + value
        return value

    def topic(self):
        return (db.get_setting(self.topic_setting) or "").strip().lstrip("/")

    def configured(self):
        return self.enabled() and bool(self.server() and self.topic())

    def send(self, title, message, url=None, event=None):
        if not self.configured():
            raise RuntimeError("ntfy is not configured")
        headers = {"Title": title}
        token = (db.get_setting(self.token_setting) or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if url:
            headers["Click"] = url
        try:
            resp = requests.post(f"{self.server()}/{self.topic()}",
                                 data=message.encode("utf-8"),
                                 headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"could not reach ntfy: {exc}") from exc
        if not resp.ok:
            raise RuntimeError(f"ntfy returned HTTP {resp.status_code}")

    def check(self):
        if not self.topic():
            return (False, "Set the topic first.")
        try:
            self.send("Simple Music Tracker", "Test notification.")
        except RuntimeError as exc:
            return (False, str(exc))
        return (True, f"Sent a test message to {self.topic()}.")


register(NtfyNotifier())
