"""Artwork candidates for one artist, and the choice the user makes.

No single source can be trusted for an artist photo: Last.fm has none for
plenty of bands, a stored image is sometimes a hotlink to a blog that has since
moved (the URL still looks fine and serves nothing), and for an artist with no
photo anywhere a record sleeve is a reasonable stand-in. So instead of guessing
once during a refresh and living with it, this asks every metadata source in
the user's order and lets the page choose.

A chosen image is *locked*: the artist refresh in app/tracker and the artwork
backfill in app/artcache both leave a locked row alone, so the pick survives
every later refresh until it's cleared again.
"""

import time

from . import db, metadata


def _key(artist_id):
    return f"artistart:{artist_id}"


def _add(out, seen, url, source, label):
    """Append one candidate, skipping anything already offered."""
    url = (url or "").strip()
    if not url or not url.lower().startswith(("http://", "https://")) or url in seen:
        return
    seen.add(url)
    out.append({"url": url, "source": source, "label": label})


def options(artist_id, *, refresh=False):
    """{current, locked, options} for one artist: every image we can offer.

    The candidates come from the metadata sources in the user's order (see
    app/metadata.py), so whichever service they trust most is listed first.
    Cached, because most of those sources are network lookups; *refresh* goes
    and asks again.
    """
    conn = db.get_connection()
    try:
        artist = conn.execute(
            "SELECT id, name, image_url, image_locked FROM artists WHERE id = ?",
            (artist_id,),
        ).fetchone()
        if not artist:
            return None
    finally:
        conn.close()

    current = artist["image_url"] or None
    head = {"current": current, "locked": bool(artist["image_locked"])}
    if not refresh:
        cached = db.get_json_cache(_key(artist_id), max_age=db.cache_max_age("hit"))
        if cached:
            return {**head, "options": cached.get("options") or []}

    out, seen = [], set()
    _add(out, seen, current, "current", "In use now")
    for found in metadata.artist_images(artist["name"], artist_id=artist_id):
        _add(out, seen, found["url"], found["source"], found["label"])
    db.set_json_cache(_key(artist_id), {"options": out, "built_at": time.time()})
    return {**head, "options": out}


def pick(artist_id, url):
    """Set (and lock) an artist's image, or clear the lock when *url* is None.

    Returns the stored URL, or None when it was cleared.
    """
    chosen = (url or "").strip() or None
    if chosen and not chosen.lower().startswith(("http://", "https://")):
        raise ValueError("artwork must be an http(s) URL")
    with db._write_lock:
        conn = db.get_connection()
        try:
            if chosen:
                conn.execute(
                    "UPDATE artists SET image_url = ?, image_locked = 1 WHERE id = ?",
                    (chosen, artist_id),
                )
            else:
                # Back to automatic: the artwork warmer refills an empty image,
                # so clearing the pick means clearing the URL with it.
                conn.execute(
                    "UPDATE artists SET image_url = NULL, image_locked = 0 "
                    "WHERE id = ?", (artist_id,),
                )
            conn.commit()
        finally:
            conn.close()
    return chosen
