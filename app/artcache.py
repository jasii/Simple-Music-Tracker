"""Get every followed artist's picture onto local disk, ahead of being asked.

The Following and Artists pages show a thumbnail per row. Each one is a
``/art?u=...`` request, which serves the file from disk when it's there and
otherwise downloads it while the browser waits -- so a page of fifty artists
whose images have never been fetched is fifty upstream round-trips happening
in front of the user. On a 2400-artist library that is most of them.

This walks the library instead, in the background, and does two jobs:

- *warm*: download the artist's image into the on-disk cache, so the page only
  ever reads local files;
- *backfill*: find an image for artists that have none, from the metadata
  sources in the user's order (app/metadata.py) -- asked cache-only, so this
  never becomes one lookup per artist. Last.fm largely stopped serving artist
  photos, which is why the order ends with a cover from one of the artist's own
  releases: a record sleeve beats an empty square.

Nothing here fetches anything a page view wouldn't have fetched eventually; it
just does it early, in one place, at a polite pace.
"""

import threading
import time

from . import artreview, artwork, db, metadata

# Parallel downloads. Small on purpose: this is background work sharing the
# line with whatever the user is actually doing.
_WORKERS = 4
# Pause between batches, so a long warm-up doesn't monopolise the network.
_BATCH_PAUSE = 0.2
# Fresh metadata lookups one pass may spend on artists that have no picture at
# all. Enough to clear the backlog of a personal library in a run or two,
# bounded so a first run on a big library doesn't sit there looking things up.
_LOOKUP_BUDGET = 250

_state = {"running": False, "done": 0, "total": 0, "warmed": 0,
          "filled": 0, "repaired": 0, "message": ""}
_lock = threading.Lock()
_run_lock = threading.Lock()


def _set(**kw):
    with _lock:
        _state.update(kw)


def get_state():
    with _lock:
        return dict(_state)


def _followed(conn):
    return conn.execute(
        "SELECT id, name, sort_name, image_url FROM artists "
        "WHERE subscription IN ('subscribed', 'notify') AND ignored = 0 "
        "ORDER BY sort_name"
    ).fetchall()


def _backfill(conn, artist, budget=None, replace=False):
    """Find and store a working image for an artist. Returns the URL, or None.

    Cached answers first, for every artist. Only then, and only while *budget*
    allows, does it look one up for real: a pass over a 2400-artist library
    can't turn into 2400 lookups, but the artists with no usable picture are
    exactly the ones worth asking about, and the sources pace themselves (see
    app/deezer.py). Each artist is asked once; the answer is cached either way,
    so the next pass continues where this one stopped.

    Whatever is chosen is downloaded first: a URL that 404s is worse than an
    empty column, because nothing then looks for a replacement. *replace* also
    overwrites a stored image (used when the stored one is dead).
    """
    found = metadata.usable_artist_image(artist["name"], artist_id=artist["id"],
                                         cached_only=True)
    if not found and budget is not None and budget.get("left", 0) > 0:
        budget["left"] -= 1
        found = metadata.usable_artist_image(artist["name"], artist_id=artist["id"])
    image = found["url"] if found else None
    if not image:
        return None
    condition = "" if replace else "AND COALESCE(image_url, '') = '' "
    with db._write_lock:
        write = db.get_connection()
        try:
            write.execute(
                "UPDATE artists SET image_url = ? WHERE id = ? "
                f"{condition}AND image_locked = 0",
                (image, artist["id"]),
            )
            write.commit()
        finally:
            write.close()
    return image


def _repair(conn, artist, budget):
    """Replace an artist's unusable image with one that downloads."""
    replacement = _backfill(conn, artist, budget, replace=True)
    if replacement:
        _set(repaired=get_state()["repaired"] + 1)
    return replacement


def _repair_failures(conn, artists, budget):
    """Find new images for the artists whose stored one wouldn't download.

    A host that is down (rather than answering a clean 404) is the common case
    and leaves no miss marker, so the failed download is the only evidence.
    """
    for artist in artists:
        if not get_state()["running"]:
            return
        _repair(conn, artist, budget)


def _run(force):
    conn = db.get_connection()
    try:
        artists = _followed(conn)
        _set(running=True, done=0, total=len(artists), warmed=0, filled=0,
             repaired=0, message="")
        pending = []
        # Fresh lookups allowed this pass, shared across every artist missing a
        # picture. Whatever is left over gets asked on the next run.
        budget = {"left": _LOOKUP_BUDGET}
        for artist in artists:
            if not get_state()["running"]:
                break
            image = artist["image_url"]
            # A URL the host has already refused counts as no image at all:
            # look for another rather than leaving the row reading as filled in
            # while the page shows an empty square.
            if image and artwork.broken(image):
                image = _repair(conn, artist, budget)
            elif not image:
                image = _backfill(conn, artist, budget)
                if image:
                    _set(filled=get_state()["filled"] + 1)
            if image and (force or not artwork.cached_path(image)):
                pending.append((artist, image))
            _set(done=get_state()["done"] + 1)
            if len(pending) >= _WORKERS * 4:
                _repair_failures(conn, _warm(pending), budget)
                pending = []
        if pending:
            _repair_failures(conn, _warm(pending), budget)
    finally:
        conn.close()
    state = get_state()
    _set(running=False,
         message=f"Warmed {state['warmed']} images, found {state['filled']} "
                 f"that were missing, replaced {state['repaired']} dead links.")
    # The review tool stays hidden until a pass has actually run: before that
    # "no artwork" describes the whole library.
    artreview.mark_pass_done(time.time())


def _warm(batch):
    """Download a batch into the cache, a few at a time.

    *batch* is a list of (artist row, url). Returns the artists whose image
    would not download: their row says they have artwork and the bytes say
    otherwise, which is what the repair below acts on.
    """
    from concurrent.futures import ThreadPoolExecutor

    def one(item):
        _artist, url = item
        if artwork.fetch(url):
            _set(warmed=get_state()["warmed"] + 1)
            return None
        return item[0]

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        failed = [row for row in pool.map(one, batch) if row is not None]
    time.sleep(_BATCH_PAUSE)
    return failed


def start(force=False):
    """Warm every followed artist's image in the background."""
    if not _run_lock.acquire(blocking=False):
        return False

    def runner():
        try:
            _run(force)
        except Exception as exc:  # noqa: BLE001 - never kill the thread
            _set(running=False, message=f"Failed: {exc}")
        finally:
            _run_lock.release()

    _set(running=True, message="Starting...")
    threading.Thread(target=runner, daemon=True, name="art-warm").start()
    return True


def stop():
    _set(running=False)


def coverage():
    """How many followed artists have a picture, and how many are on disk."""
    conn = db.get_connection()
    try:
        artists = _followed(conn)
    finally:
        conn.close()
    with_url = [a["image_url"] for a in artists if a["image_url"]]
    on_disk = sum(1 for url in with_url if artwork.cached_path(url))
    return {"followed": len(artists), "with_image": len(with_url),
            "on_disk": on_disk, "missing_image": len(artists) - len(with_url)}
