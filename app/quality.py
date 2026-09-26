"""Quality profile: what to grab, and in what order.

The user orders the qualities they accept (best first); anything not on the
list is never grabbed. A Soulseek result carries only a file extension and a
bitrate, and a library reports a codec and a bitrate, so both are normalised to
the same labels here and compared with one ranking.
"""

from . import db

# Every quality the app can recognise, best first. This is the menu the user
# orders and ticks; it is not itself the profile.
QUALITIES = [
    "FLAC 24bit",
    "FLAC",
    "ALAC",
    "WAV",
    "APE",
    "MP3 320",
    "MP3 V0",
    "MP3 V2",
    "AAC",
    "Opus",
    "Vorbis",
    "MP3",
]

DEFAULT_QUALITY_ORDER = ["FLAC 24bit", "FLAC", "MP3 320", "MP3 V0"]

# File extensions -> label, for results that only give us a filename.
_EXTENSIONS = {
    "flac": "FLAC", "wav": "WAV", "aiff": "WAV", "aif": "WAV", "ape": "APE",
    "alac": "ALAC", "m4a": "AAC", "aac": "AAC", "opus": "Opus", "ogg": "Vorbis",
    "mp3": "MP3",
}


def _csv(setting, default):
    raw = (db.get_setting(setting) or "").strip()
    items = [p.strip() for p in raw.split(",") if p.strip()]
    return items or list(default)


def quality_order():
    """The accepted qualities, best first. Anything absent is not grabbable."""
    allowed = [q for q in _csv("quality_order", DEFAULT_QUALITY_ORDER) if q in QUALITIES]
    return allowed or list(DEFAULT_QUALITY_ORDER)


def file_label(extension, bitrate=None, variable=None):
    """Canonical label for a loose file (Soulseek), from extension + bitrate."""
    ext = (extension or "").lower().lstrip(".")
    label = _EXTENSIONS.get(ext)
    if label != "MP3":
        return label
    try:
        kbps = int(bitrate or 0)
    except (TypeError, ValueError):
        kbps = 0
    if variable:
        if kbps >= 220:
            return "MP3 V0"
        if kbps >= 170:
            return "MP3 V2"
        return "MP3"
    if kbps >= 320:
        return "MP3 320"
    if kbps >= 220:
        return "MP3 V0"
    if kbps >= 170:
        return "MP3 V2"
    return "MP3"


def rank(label, order=None):
    """Position of *label* in the profile; None when it isn't accepted."""
    order = order if order is not None else quality_order()
    try:
        return order.index(label)
    except (ValueError, AttributeError):
        return None


def better_of(a, b, order=None):
    """The better of two quality labels under the profile (None-safe)."""
    if not a:
        return b
    if not b:
        return a
    order = order if order is not None else quality_order()
    ra, rb = rank(a, order), rank(b, order)
    if ra is None:
        return b if rb is not None else a
    if rb is None:
        return a
    return a if ra <= rb else b


# Which file extensions a quality can arrive as, for clients that search by
# filename.
_LABEL_EXTENSIONS = {
    "FLAC 24bit": "flac", "FLAC": "flac", "ALAC": "m4a", "WAV": "wav",
    "APE": "ape", "MP3 320": "mp3", "MP3 V0": "mp3", "MP3 V2": "mp3",
    "MP3": "mp3", "AAC": "m4a", "Opus": "opus", "Vorbis": "ogg",
}


def profile_extensions(order=None):
    """Accepted file extensions, in profile order, without repeats.

    Lets a searching client (slskd) apply the same profile: a FLAC-only ladder
    shouldn't come back with MP3s just because the search found some.
    """
    order = order if order is not None else quality_order()
    out = []
    for label in order:
        ext = _LABEL_EXTENSIONS.get(label)
        if ext and ext not in out:
            out.append(ext)
    return out


def describe():
    """The whole profile, for the API and the settings UI."""
    return {
        "qualities": QUALITIES,
        "quality_order": quality_order(),
    }
