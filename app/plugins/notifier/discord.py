"""Notifier: Discord, through a channel webhook.

Channel Settings -> Integrations -> Webhooks gives a URL that accepts a JSON
post. Messages go out as an embed so the title, body and link render as one
block rather than a wall of text.
"""

import requests

from ... import db
from . import NotifierPlugin, register

_TIMEOUT = 10
# Discord rejects embeds over these lengths outright.
_TITLE_MAX = 256
_BODY_MAX = 4096


class DiscordNotifier(NotifierPlugin):
    """Post notifications to a Discord channel webhook."""

    key = "discord"
    label = "Discord"
    icon = "discord"
    description = "Post to a Discord channel using a channel webhook URL."
    has_test = True

    url_setting = "notifier_discord_webhook"
    username_setting = "notifier_discord_username"
    enabled_setting = "notifier_discord_enabled"
    event_prefix = "notifier_discord_on"

    config_fields = [
        {"key": url_setting, "label": "Webhook URL", "type": "textarea",
         "secret": True,
         "placeholder": "https://discord.com/api/webhooks/...",
         "help": "Discord channel settings -> Integrations -> Webhooks -> "
                 "Copy Webhook URL."},
        {"key": username_setting, "label": "Post as",
         "placeholder": "Simple Music Tracker",
         "help": "Optional. Overrides the webhook's own name on each message."},
    ]

    def webhook(self):
        return (db.get_setting(self.url_setting) or "").strip()

    def configured(self):
        return self.enabled() and bool(self.webhook())

    def send(self, title, message, url=None, event=None):
        if not self.configured():
            raise RuntimeError("Discord is not configured")
        embed = {"title": title[:_TITLE_MAX], "description": message[:_BODY_MAX]}
        if url and url.startswith("http"):
            embed["url"] = url
        payload = {"embeds": [embed]}
        name = (db.get_setting(self.username_setting) or "").strip()
        if name:
            payload["username"] = name
        try:
            resp = requests.post(self.webhook(), json=payload, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"could not reach Discord: {exc}") from exc
        if resp.status_code == 404:
            raise RuntimeError("Discord webhook not found (was it deleted?)")
        if resp.status_code == 429:
            raise RuntimeError("Discord is rate limiting this webhook")
        if not resp.ok:
            raise RuntimeError(f"Discord returned HTTP {resp.status_code}")

    def check(self):
        if not self.webhook():
            return (False, "Set the webhook URL first.")
        try:
            self.send("Simple Music Tracker", "Test notification.")
        except RuntimeError as exc:
            return (False, str(exc))
        return (True, "Posted a test message to Discord.")


register(DiscordNotifier())
