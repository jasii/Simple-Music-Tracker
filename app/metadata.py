"""One way to ask "what do we know about this artist / release".

Callers name the fact they want, not the service that has it. The user orders
the services in Settings > Metadata, and each field is answered by the first
source in that order that has one -- so a photo can come from Deezer while the
biography comes from Last.fm, in a single pass, and a new source added later
is picked up here without a single call site changing.

See app/plugins/metadata for the plugin contract.
"""

from . import artwork, names
from .plugins import metadata as registry

# Fields an artist lookup can fill, and the capability that answers each.
_ARTIST_FIELDS = (
    ("image_url", "artist_image"),
    ("bio", "artist_bio"),
    ("genres", "artist_genres"),
    ("url", "artist_bio"),
)


def _has(field, value):
    """Is this answer worth keeping? An empty list/dict/string is not one."""
    if value is None:
        return False
    if field in ("genres", "tags", "credits"):
        return bool(value)
    return bool(str(value).strip())


def artist_info(name, artist_id=None, cached_only=False):
    """{image_url, bio, genres, url} for one artist, filled field by field.

    Each field is answered by the first source in *its* order that has one, so
    a photo can come from Deezer while the biography comes from Last.fm. Every
    source is asked at most once, whatever it ends up answering.
    """
    out = {"image_url": None, "bio": None, "genres": [], "url": None}
    if not name:
        return out
    answers = {}

    def ask(plugin):
        if plugin.key not in answers:
            try:
                answers[plugin.key] = plugin.artist_info(
                    name, artist_id=artist_id, cached_only=cached_only) or {}
            except Exception:  # noqa: BLE001 - one broken source isn't a failure
                answers[plugin.key] = {}
        return answers[plugin.key]

    for field, capability in _ARTIST_FIELDS:
        for plugin in registry.sources(capability):
            value = ask(plugin).get(field)
            if _has(field, value):
                out[field] = value
                break
    if not out["image_url"]:
        # Nothing has a picture of this credit: try the acts in it (see
        # artist_images). Only the picture falls back -- one act's biography
        # would be plain wrong for the pairing.
        found = artist_images(name, artist_id=artist_id, cached_only=cached_only)
        if found:
            out["image_url"] = found[0]["url"]
    return out


def _images_for(name, artist_id, cached_only, credit=None):
    """Photos of exactly this name, from each source in order."""
    out, seen = [], set()
    for plugin in registry.sources("artist_image"):
        try:
            found = plugin.artist_images(name, artist_id=artist_id,
                                         cached_only=cached_only) or []
        except Exception:  # noqa: BLE001 - skip a source that's misbehaving
            continue
        for url in found:
            url = (url or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            label = plugin.display_label()
            out.append({
                "url": url,
                "source": plugin.key,
                # Say whose picture it is when it isn't the credit's own.
                "label": f"{label} ({credit})" if credit else label,
                **({"credit": credit} if credit else {}),
            })
    return out


def artist_images(name, artist_id=None, cached_only=False):
    """Every photo the sources can offer, in priority order.

    Returns [{"url", "source", "label", "credit"?}] -- the artwork picker shows
    these, so duplicates are dropped but nothing else is filtered: a candidate
    that turns out to be a dead link is discarded by the page that tries it.

    A joint credit ("Aesop Rock and Blockhead") is asked about as a pair first,
    because a duo that tours together usually has a photo of the pair. Only
    when nothing has one does it fall back to the acts in it, in the order
    they're credited -- the first-named artist's photo beats an empty square,
    and the label says whose it is.
    """
    if not name:
        return []
    found = _images_for(name, artist_id, cached_only)
    if found:
        return found
    for part in names.split_credit(name):
        # The library row belongs to the credit, not to one of its acts, so the
        # local-covers source is not asked again per part.
        found += _images_for(part, None, cached_only, credit=part)
    return found


# Fields a release lookup can fill, and the capability that answers each.
_ALBUM_FIELDS = (
    ("image_url", "album_art"),
    ("description", "album_description"),
    ("tags", "album_tags"),
)


def usable_artist_image(name, artist_id=None, cached_only=False):
    """The first offered photo whose bytes actually download, or None.

    Storing a URL that 404s is how an artist ends up looking like it has
    artwork while showing an empty square, so every automatic choice is
    downloaded before it's kept. The download lands in the app's own image
    cache, which is where the page was going to read it from anyway.

    A joint credit gets a second round: its own candidates may all be dead
    (typically a derived archive URL), and the acts named in it are then worth
    asking about even though the credit did offer something.
    """
    for found in artist_images(name, artist_id=artist_id, cached_only=cached_only):
        if artwork.usable(found["url"]):
            return found
    for part in names.split_credit(name):
        for found in _images_for(part, None, cached_only, credit=part):
            if artwork.usable(found["url"]):
                return found
    return None


def album_info(artist, title, mbid=None, cached_only=False):
    """Everything the sources know about one release, field by field.

    Same shape as artist_info: each field is answered by the first source in
    its own order, every source is asked at most once, and a source that only
    has the tags doesn't stop the write-up arriving from the next.
    """
    out = {"image_url": None, "description": None, "tags": [], "sources": {}}
    if not artist or not title:
        return out
    answers = {}

    def ask(plugin):
        if plugin.key not in answers:
            try:
                answers[plugin.key] = plugin.album_info(
                    artist, title, mbid, cached_only=cached_only) or {}
            except Exception:  # noqa: BLE001 - one broken source isn't a failure
                answers[plugin.key] = {}
        return answers[plugin.key]

    for field, capability in _ALBUM_FIELDS:
        for plugin in registry.sources(capability):
            value = ask(plugin).get(field)
            if _has(field, value):
                out[field] = value
                # Which source each field came from, so the page can say.
                out["sources"][field] = plugin.display_label()
                break
    return out


def album_art(artist, title, mbid=None, known=None):
    """A cover URL for one release, from the first source that has one.

    *known* is {plugin key: url or None} for answers the caller already has --
    the album page fetches Last.fm's album info for its tracklist anyway, so
    its cover is passed in here rather than looked up a second time.
    """
    known = known or {}
    for plugin in registry.sources("album_art"):
        if plugin.key in known:
            found = known[plugin.key]
        else:
            try:
                found = plugin.album_art(artist, title, mbid)
            except Exception:  # noqa: BLE001 - try the next source
                continue
        if found:
            return found
    return None


def priority(capability=None):
    """Plugin keys in the order they're asked for *capability*."""
    return registry.priority(capability)
