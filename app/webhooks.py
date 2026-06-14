"""Webhook delivery for notify-subscribed artists.

When a new upcoming release is detected for an artist the user subscribed to
with "notify", a webhook is fired. The payload is a JSON body the user can
customise from the settings page using simple {placeholders}.
"""

import json

import requests

from . import db

DEFAULT_TEMPLATE = json.dumps(
    {
        "event": "new_release",
        "artist": "{artist}",
        "title": "{title}",
        "release_date": "{release_date}",
        "type": "{type}",
        "image": "{image_url}",
    },
    indent=2,
)


def _parse_headers(raw):
    """Accept either a JSON object or simple 'Key: Value' lines."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except ValueError:
        pass
    headers = {}
    for line in raw.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip()] = value.strip()
    return headers


def _render(template, context):
    """Replace {placeholders} without choking on stray braces in JSON."""
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace("{" + key + "}", str(value if value is not None else ""))
    return rendered


def fire(artist, release):
    """Send a webhook for *artist* / *release*. Returns (ok, message)."""
    url = db.get_setting("webhook_url")
    if not url:
        return False, "no webhook configured"

    method = (db.get_setting("webhook_method") or "POST").upper()
    template = db.get_setting("webhook_template") or DEFAULT_TEMPLATE
    headers = _parse_headers(db.get_setting("webhook_headers"))

    context = {
        "artist": artist["name"],
        "title": release["title"],
        "release_date": release.get("release_date") or "",
        "type": release.get("primary_type") or "",
        "image_url": release.get("image_url") or "",
    }
    body = _render(template, context)

    # Send JSON if the body parses as JSON, otherwise as raw text.
    send_kwargs = {"timeout": 15, "headers": headers}
    try:
        json.loads(body)
        headers.setdefault("Content-Type", "application/json")
        send_kwargs["data"] = body.encode("utf-8")
    except ValueError:
        send_kwargs["data"] = body.encode("utf-8")

    try:
        resp = requests.request(method, url, **send_kwargs)
        ok = resp.status_code < 400
        return ok, f"{resp.status_code}"
    except requests.RequestException as exc:
        return False, str(exc)


def send_test():
    """Fire a sample webhook so users can verify their configuration."""
    sample_artist = {"name": "Test Artist"}
    sample_release = {
        "title": "Test Album",
        "release_date": "2099-01-01",
        "primary_type": "Album",
        "image_url": "",
    }
    return fire(sample_artist, sample_release)
