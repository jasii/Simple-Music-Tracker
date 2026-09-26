"""Playing a track the user already owns, instead of a thirty-second sample.

Given an artist and a track title, each configured library source is asked
whether it holds that recording; the first that does streams it in full
*through this server*, so no library credential -- a Subsonic token, a Plex
token, a path on disk -- ever reaches the browser. When nothing owns it the
caller falls back to a preview (see app/preview.py).
"""

import mimetypes
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Response, send_file

from . import db, plugins

# How long a "this library has that track" answer is trusted comes from
# settings (store_marker_days): the stream route looks again for real when
# someone presses play, so a stale yes costs nothing but a fallback.

_CHUNK = 64 * 1024

# Worth passing back from the upstream server: everything the browser needs to
# seek and to know what it's playing.
_PROXY_HEADERS = ("Content-Type", "Content-Length", "Content-Range",
                  "Accept-Ranges", "Last-Modified", "ETag")

# mimetypes doesn't know every music container, and a wrong type stops playback.
_AUDIO_MIME = {
    ".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".wav": "audio/wav", ".aiff": "audio/aiff", ".aif": "audio/aiff",
    ".wma": "audio/x-ms-wma", ".ape": "audio/x-ape", ".mpc": "audio/x-musepack",
}


# Which library is asked for a track first. Several can hold the same song --
# a FLAC on the server, a 320 on the laptop -- and which one streams it is a
# matter of taste (and of which machine is awake), so it's the user's order.
PLAYBACK_SETTING = "library_playback_priority"
# What the settings UI calls this list, alongside the metadata capabilities.
PLAYBACK_FIELD = "library_playback"


def playable_keys():
    """Libraries that can actually serve audio, in registration order."""
    return [p.key for p in plugins.get_plugins("library")
            if getattr(p, "plays_tracks", False)]


def playback_order():
    """Library plugin keys in the order a track is looked for, usable first.

    Keys that no longer exist are dropped and new libraries fall in at the end,
    so a stored order never hides a library the user has just set up.
    """
    known = playable_keys()
    stored = db.get_setting(PLAYBACK_SETTING) or ""
    order = []
    for key in stored.split(","):
        key = key.strip()
        if key in known and key not in order:
            order.append(key)
    return order + [k for k in known if k not in order]


def set_playback_order(keys):
    """Store a new order (unknown keys ignored). Returns the stored order."""
    known = playable_keys()
    order = []
    for key in keys or []:
        key = (key or "").strip()
        if key in known and key not in order:
            order.append(key)
    order += [k for k in known if k not in order]
    db.set_setting(PLAYBACK_SETTING, ",".join(order))
    return order


def _sources():
    """Configured libraries, in the order the user wants them asked."""
    by_key = {p.key: p for p in plugins.get_plugins("library")}
    return [by_key[k] for k in playback_order()
            if k in by_key and by_key[k].configured()]


def locate(artist, title):
    """(plugin, descriptor) from the first configured library that has it."""
    for plugin in _sources():
        try:
            found = plugin.find_track(artist, title)
        except Exception:  # noqa: BLE001 - one unreachable source can't stop the rest
            found = None
        if found:
            return plugin, found
    return None, None


def _marker_key(artist, title):
    # Versioned: answers found by substring matching can be another artist's.
    # The playback order is part of the key, so moving a library up doesn't
    # leave every page still naming the one that used to answer first.
    order = "".join(k[:2] for k in playback_order())
    return (f"libtrack5:{order}:{(artist or '').strip().lower()}"
            f"|{(title or '').strip().lower()}")


def _named(info):
    """Put the source's current name on a marker.

    The label is resolved on the way out, never trusted from the cache: a
    server that reports itself as Navidrome renames the source, and a stored
    row shouldn't keep showing what it was called yesterday.
    """
    if not info or not info.get("key"):
        return info
    plugin = plugins.get_plugin("library", info["key"])
    if plugin:
        info["label"] = plugin.display_label()
        info["icon"] = plugin.icon_name()
    return info


def marker(artist, title, max_age=db.FROM_SETTINGS):
    """Which library has this track: {key, label, url, duration, suffix}, or {}."""
    cache_key = _marker_key(artist, title)
    cached = db.get_json_cache(cache_key, max_age=db.resolve_max_age(max_age, "marker"))
    if cached is not None:
        return _named(cached)
    plugin, found = locate(artist, title)
    info = {}
    if plugin and found:
        info = {
            "key": plugin.key,
            "label": plugin.display_label(),
            # The server's own page for it, so a credit can link there.
            "url": found.get("page_url"),
            "icon": plugin.icon_name(),
            "album": found.get("album"),
            "duration": found.get("duration"),
            "suffix": found.get("suffix"),
        }
    db.set_json_cache(cache_key, info)
    return info


def markers(artist, titles):
    """{title: marker} for several tracks by one artist.

    Resolved in parallel: each title asks every library in turn, so a handful
    of tracks done one after another would keep an artist page waiting several
    seconds. Cached answers make later visits free either way.
    """
    titles = list(titles or [])
    if not titles:
        return {}
    if len(titles) == 1:
        return {titles[0]: marker(artist, titles[0])}
    with ThreadPoolExecutor(max_workers=min(4, len(titles))) as pool:
        found = list(pool.map(lambda t: marker(artist, t), titles))
    return dict(zip(titles, found))


def respond(descriptor, range_header=None):
    """Stream the located track back to the browser, honouring Range."""
    if descriptor.get("kind") == "file":
        path = descriptor["path"]
        ext = "." + (descriptor.get("suffix") or path.rsplit(".", 1)[-1]).lower()
        mime = _AUDIO_MIME.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        # conditional=True gives Range/If-Modified-Since, so seeking works.
        return send_file(path, mimetype=mime, conditional=True)

    headers = dict(descriptor.get("headers") or {})
    if range_header:
        headers["Range"] = range_header
    upstream = requests.get(
        descriptor["url"],
        params=descriptor.get("params"),
        headers=headers,
        stream=True,
        timeout=(5, 30),
    )

    def body():
        try:
            for chunk in upstream.iter_content(_CHUNK):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    resp = Response(body(), status=upstream.status_code)
    for name in _PROXY_HEADERS:
        value = upstream.headers.get(name)
        if value:
            resp.headers[name] = value
    resp.headers.setdefault("Accept-Ranges", "bytes")
    return resp
