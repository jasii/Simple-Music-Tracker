"""What metadata the library is missing, and filling it in from the sources.

The app collects artist information as a side effect of browsing: open an
artist page and it gets a photo, a biography and genre tags. Anything never
opened stays blank, and a source that had nothing the first time is never asked
again on its own. So this answers two questions on demand -- what's missing,
and fill it -- over every followed artist at once, using the metadata sources
in the user's order (see app/metadata.py).

The fill runs in the background with progress and a cancel, because it's one
lookup per artist per missing field and a library of a few thousand artists
takes minutes, not seconds.
"""

import threading
import time

from . import artreview, artwork, db, metadata

# What can be missing, in the order the UI lists it: (key, column, label).
KINDS = (
    ("images", "image_url", "Artist photos"),
    ("bios", "bio", "Biographies"),
    ("genres", "genres", "Genre tags"),
)

# Only followed artists are worth filling: the rest are names the library has
# seen, and there are thousands of them.
_SCOPE = "subscription IN ('subscribed', 'notify') AND ignored = 0"

_state = {"running": False, "done": 0, "total": 0, "images": 0, "bios": 0,
          "genres": 0, "message": "", "cancelling": False}
_lock = threading.Lock()
_run_lock = threading.Lock()


def _set(**kw):
    with _lock:
        _state.update(kw)


def get_state():
    with _lock:
        return dict(_state)


def _suspect_images(conn):
    """Followed artists whose stored photo has never reached the image cache.

    A URL with no bytes behind it reads as "has artwork" everywhere else, so
    nothing counts it as missing and nothing replaces it -- while the page
    shows an empty square. Two ways that happens: the host answered a
    confirmed miss (remembered), or it has never successfully served the file
    at all (an image host that goes down takes hundreds at once). Deciding
    from what is on disk costs no requests; the fill then tries each one once
    before replacing it.
    """
    rows = conn.execute(
        f"SELECT id, name, image_url FROM artists WHERE {_SCOPE} "
        "AND COALESCE(image_url, '') <> '' AND image_locked = 0 ORDER BY name"
    ).fetchall()
    return [r for r in rows if not artwork.cached_path(r["image_url"])]


def gaps():
    """Counts of what's missing, with a few example names for each."""
    conn = db.get_connection()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM artists WHERE {_SCOPE}").fetchone()["c"]
        out = {"followed": total, "kinds": []}
        suspect = _suspect_images(conn)
        for key, column, label in KINDS:
            rows = conn.execute(
                f"SELECT name FROM artists WHERE {_SCOPE} "
                f"AND COALESCE({column}, '') = '' ORDER BY name LIMIT 5"
            ).fetchall()
            missing = conn.execute(
                f"SELECT COUNT(*) AS c FROM artists WHERE {_SCOPE} "
                f"AND COALESCE({column}, '') = ''").fetchone()["c"]
            examples = [r["name"] for r in rows]
            broken = 0
            if key == "images":
                broken = len(suspect)
                examples = (examples + [r["name"] for r in suspect])[:5]
            out["kinds"].append({
                "key": key, "label": label, "missing": missing,
                "broken": broken, "examples": examples,
            })
        # A hand-picked photo is never touched by a fill; worth saying so.
        out["locked_images"] = conn.execute(
            f"SELECT COUNT(*) AS c FROM artists WHERE {_SCOPE} "
            "AND image_locked = 1").fetchone()["c"]
    finally:
        conn.close()
    out["state"] = get_state()
    return out


def _pending(conn, wanted):
    """Followed artists missing at least one of the *wanted* kinds.

    Includes the ones whose photo is a dead link, which is a gap the columns
    can't show: the row has a URL, it just doesn't serve an image any more.
    """
    columns = [c for key, c, _label in KINDS if key in wanted]
    if not columns:
        return []
    clause = " OR ".join(f"COALESCE({c}, '') = ''" for c in columns)
    rows = conn.execute(
        f"SELECT id, name, image_url, bio, genres, lastfm_url, image_locked "
        f"FROM artists WHERE {_SCOPE} AND ({clause}) ORDER BY name"
    ).fetchall()
    if "images" not in wanted:
        return rows
    seen = {r["id"] for r in rows}
    dead = [r["id"] for r in _suspect_images(conn) if r["id"] not in seen]
    if not dead:
        return rows
    placeholders = ",".join("?" for _ in dead)
    extra = conn.execute(
        f"SELECT id, name, image_url, bio, genres, lastfm_url, image_locked "
        f"FROM artists WHERE id IN ({placeholders})", dead
    ).fetchall()
    return sorted([*rows, *extra], key=lambda r: (r["name"] or "").lower())


def _write(artist, found, wanted, needs_image):
    """Store whichever wanted fields were blank and came back. Returns them."""
    filled = []
    sets, values = [], []
    if ("images" in wanted and needs_image
            and not artist["image_locked"] and found.get("image_url")):
        sets.append("image_url = ?")
        values.append(found["image_url"])
        filled.append("images")
    if "bios" in wanted and not artist["bio"] and found.get("bio"):
        sets.append("bio = ?")
        values.append(found["bio"])
        filled.append("bios")
    if "genres" in wanted and not artist["genres"] and found.get("genres"):
        sets.append("genres = ?")
        values.append(",".join(found["genres"]))
        filled.append("genres")
    if not artist["lastfm_url"] and found.get("url"):
        sets.append("lastfm_url = ?")
        values.append(found["url"])
    if not sets:
        return filled
    with db._write_lock:
        conn = db.get_connection()
        try:
            conn.execute(f"UPDATE artists SET {', '.join(sets)} WHERE id = ?",
                         [*values, artist["id"]])
            conn.commit()
        finally:
            conn.close()
    return filled


def _run(wanted):
    conn = db.get_connection()
    try:
        artists = _pending(conn, wanted)
    finally:
        conn.close()
    _set(running=True, cancelling=False, done=0, total=len(artists),
         images=0, bios=0, genres=0, message="")
    try:
        for artist in artists:
            if get_state()["cancelling"]:
                break
            # One download attempt on the stored photo settles whether it is
            # artwork or just a URL; it lands in the image cache either way.
            needs_image = "images" in wanted and not artwork.usable(
                artist["image_url"])
            other_gaps = any(
                not artist[column] for key, column, _l in KINDS
                if key in wanted and key != "images"
            )
            if not needs_image and not other_gaps:
                _set(done=get_state()["done"] + 1)
                continue
            try:
                found = metadata.artist_info(artist["name"], artist_id=artist["id"])
                # The replacement is downloaded before it's stored: the whole
                # point of this pass is not to leave another URL serving nothing.
                if needs_image:
                    usable = metadata.usable_artist_image(
                        artist["name"], artist_id=artist["id"])
                    found["image_url"] = usable["url"] if usable else None
            except Exception:  # noqa: BLE001 - one bad artist isn't the run
                found = {}
            for kind in _write(artist, found, wanted, needs_image):
                _set(**{kind: get_state()[kind] + 1})
            _set(done=get_state()["done"] + 1)
    finally:
        if "images" in wanted:
            artreview.mark_pass_done(time.time())
        state = get_state()
        _set(running=False, cancelling=False, message=(
            f"Filled {state['images']} photos, {state['bios']} biographies, "
            f"{state['genres']} genre tags across {state['done']} artists"
            + (" (cancelled)" if state["cancelling"] else "")))
    # Newly found photos are worth having on disk before a page asks for them.
    if state["images"]:
        from . import artcache
        artcache.start()


def start(kinds=None):
    """Begin a fill in the background. Returns False if one is already going."""
    wanted = {k for k, _c, _l in KINDS if not kinds or k in kinds}
    with _run_lock:
        if get_state()["running"]:
            return False
        _set(running=True, message="starting")
        threading.Thread(target=_run, args=(wanted,), daemon=True,
                         name="metadata-fill").start()
    return True


def stop():
    """Ask a running fill to stop after the artist it's on."""
    if get_state()["running"]:
        _set(cancelling=True)
        return True
    return False
