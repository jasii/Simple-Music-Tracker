"""Artists with no usable artwork, offered for review one by one.

The automatic passes get most of it: they ask each metadata source in turn and
keep the first picture that downloads. What's left over is the awkward tail --
a joint credit no service has photographed, a band whose only image was a
hotlink to a site that has since closed, a name spelled differently everywhere.
Those need a person to look, which is what this feeds.

Modelled on the merge-suggestion tool: it only ever suggests, an artist can be
waved away (and brought back), and it stays out of sight until the first
artwork pass has run -- before that, "no artwork" describes the whole library
and the list would be noise.
"""

from . import artwork, db

# Set when an artwork pass finishes, which is what unlocks the tool.
PASS_SETTING = "artwork_pass_at"

# Rows worth reviewing: in the library proper, not hidden away.
_SCOPE = "ignored = 0"


def mark_pass_done(when):
    """Record that an artwork pass has completed (epoch seconds)."""
    db.set_setting(PASS_SETTING, str(int(when)))


def last_pass():
    """When an artwork pass last finished, or 0.0 if one never has."""
    try:
        return float(db.get_setting(PASS_SETTING) or 0)
    except (TypeError, ValueError):
        return 0.0


def _dismissed(conn):
    return {r["artist_id"] for r in
            conn.execute("SELECT artist_id FROM artwork_dismissals")}


def needs_artwork(limit=25, offset=0, include_dismissed=False, ids=None):
    """Artists whose artwork is missing or won't load, worst first.

    *limit* 0 returns the counts with no rows; *ids* narrows the answer to
    those artists, which is what the selection on the Artists page asks for.

    "Won't load" means no bytes ever reached the image cache for the stored
    URL, which is the state a dead hotlink leaves behind: the row looks filled
    in and the page shows an empty square. Followed artists come first, then
    the ones with the most tracks, since those are the pages actually opened.
    """
    conn = db.get_connection()
    try:
        rows = conn.execute(
            f"SELECT id, name, image_url, image_locked, subscription, "
            f"track_count FROM artists WHERE {_SCOPE} AND image_locked = 0 "
            "ORDER BY CASE WHEN subscription IN ('subscribed', 'notify') "
            "THEN 0 ELSE 1 END, track_count DESC, name"
        ).fetchall()
        skipped = _dismissed(conn)
    finally:
        conn.close()

    wanted = set(ids) if ids else None
    out = []
    for row in rows:
        if wanted is not None and row["id"] not in wanted:
            continue
        if not artwork.usable(row["image_url"], download=False):
            if row["id"] in skipped and not include_dismissed:
                continue
            out.append({
                "id": row["id"],
                "name": row["name"],
                # The URL that isn't working, so the card can say what it tried.
                "image_url": row["image_url"] or None,
                "followed": row["subscription"] in ("subscribed", "notify"),
                "track_count": row["track_count"],
                "dismissed": row["id"] in skipped,
            })
    total = len([a for a in out if not a["dismissed"]])
    # limit 0 asks for the counts alone -- what the Tools menu needs to decide
    # whether to offer the review at all.
    page = out[offset:offset + limit] if limit else []
    return {
        # A selection is an explicit request, so it doesn't wait on a pass
        # having run -- that gate is about the unasked-for list.
        "ready": last_pass() > 0 or bool(ids),
        "scanned_at": last_pass(),
        "total": total,
        "dismissed": len(skipped),
        "artists": page,
    }


def dismiss(artist_id):
    """Stop offering this artist. Returns True if it wasn't already set aside."""
    with db._write_lock:
        conn = db.get_connection()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO artwork_dismissals (artist_id) VALUES (?)",
                (artist_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def restore(artist_id=None):
    """Bring one artist back into the list, or all of them."""
    with db._write_lock:
        conn = db.get_connection()
        try:
            if artist_id is None:
                cur = conn.execute("DELETE FROM artwork_dismissals")
            else:
                cur = conn.execute(
                    "DELETE FROM artwork_dismissals WHERE artist_id = ?",
                    (artist_id,),
                )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
