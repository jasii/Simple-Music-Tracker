"""Suggested artists: where they come from, and what we know about them.

Two jobs live here. :func:`collect` asks every configured source who sounds
like a given artist and records the answers; the rest of the module fills in
what the Discover rows show about them.

Sources are the metadata plugins that answer "artist_similar" -- Last.fm's,
which needs only the API key the app already uses for bios -- asked in the
order set in Settings > Metadata.
All of them write into the same table through db.record_similar_artists, so the
ranking, the artist-page chips and the bulk scan work with any of them, and
merge when several are present.

Details for suggested artists -- the ones on Discover -> Similar Artists.

A suggestion is an artist a source considers close to one of yours, so
nothing about them is in the library yet: image, genre tags and bio have to be
looked up. One lookup asks the metadata sources in order (Last.fm's
``artist.getinfo`` for the bio, tags and image, by default), so the merged result is
cached for a week and shared by everything that needs it -- the per-row fetch on
Discover, the bulk genre enrichment, and artist creation from a suggestion.
"""

from . import db

# How long a merged lookup stays good for.


def collect(name, record=True):
    """Similar artists for *name*, from each source in the user's order.

    Returns ``(entries, sources)``: the merged suggestions, best score first,
    and the labels of the sources that answered. Every source's answers are
    recorded, so the "worth checking out" ranking builds up as pages are
    browsed whichever source produced them -- but where two sources name the
    same artist, the one higher up the Similar artists list in Settings >
    Metadata keeps its entry (its score and its link).
    """
    if not name:
        return [], []
    from .plugins import metadata as registry

    merged = {}
    sources = []
    for plugin in registry.sources("artist_similar"):
        try:
            found = plugin.similar_artists(name) or []
        except Exception:  # noqa: BLE001 - a flaky source can't break the page
            found = []
        if not found:
            continue
        label = plugin.display_label()
        sources.append(label)
        if record:
            db.record_similar_artists(name, found, label)
        for entry in found:
            merged.setdefault((entry.get("name") or "").lower(), dict(entry))

    entries = sorted(merged.values(), key=lambda e: -(e.get("score") or 0))
    return entries, sources


LASTFM_LABEL = "Last.fm"


def sources_available():
    """True when anything can answer "who sounds like this artist?"."""
    from .plugins import metadata as registry

    return bool(registry.sources("artist_similar"))


def cached_info(name, max_age=db.FROM_SETTINGS):
    """The stored lookup for *name*, or None when it's missing or stale."""
    if not name:
        return None
    return db.get_json_cache(f"simartinfo:{name.lower()}",
                             max_age=db.resolve_max_age(max_age, "hit"))


def artist_info(name, max_age=db.FROM_SETTINGS):
    """Best-effort {image_url, genres, bio, lastfm_url} for one artist name.

    Served from the cache when possible; otherwise gathered from the metadata
    sources in the order the user put them in (see app/metadata.py), each field
    taken from the first source that has one.
    """
    cached = cached_info(name, max_age=max_age)
    if cached is not None:
        return cached
    # Local import: the metadata plugins import the plugin registry, which
    # imports half the app.
    from . import metadata

    found = metadata.artist_info(name)
    out = {
        "image_url": found.get("image_url"),
        "genres": found.get("genres") or [],
        "bio": found.get("bio"),
        "lastfm_url": found.get("url"),
    }
    db.set_json_cache(f"simartinfo:{name.lower()}", out)
    # Suggested artists that already have a library row (followed from here, or
    # added by name) keep the tags on the row, so they survive this cache.
    db.fill_artist_genres(name, out["genres"])
    return out
