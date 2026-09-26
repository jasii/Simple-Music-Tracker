"""Spotting artists that are probably the same person.

A library built from file tags collects the same artist several times: "Bonnie
'Prince' Billy", "Bonnie “Prince” Billy" and "Bonnie Prince Billy" are three
rows, each with its own owned albums and its own place in the release feed.
Everything downstream now tolerates that (see db.match_key), but tolerating is
not fixing -- only a merge is.

Merging is destructive, so nothing here does it: this finds the candidates,
picks which row the others would fold into, and explains why. The user merges
or says "not duplicates", and a dismissal sticks until the group changes --
if a fourth spelling shows up, the suggestion comes back.

Two things count as a candidate:

- the same name once punctuation and case are removed;
- the same MusicBrainz id under two different names, which is the tagger
  having spelled one of them differently.
"""

import hashlib

from . import db


def _signature(ids):
    """Short digest of a group's membership, so a changed group resurfaces."""
    joined = ",".join(str(i) for i in sorted(ids))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def _score(row):
    """How good a merge target a row is: the one that knows the most wins."""
    return (
        row["track_count"] or 0,
        1 if row["mbid"] else 0,
        1 if (row["subscription"] or "none") != "none" else 0,
        len(row["name"] or ""),
    )


def _member(row, owned):
    return {
        "id": row["id"],
        "name": row["name"],
        "mbid": row["mbid"],
        "subscription": row["subscription"],
        "ignored": bool(row["ignored"]),
        "track_count": row["track_count"] or 0,
        "owned_albums": owned.get(row["id"], 0),
        "last_checked": row["last_checked"],
    }


def suggestions(include_dismissed=False):
    """Groups of rows that look like one artist, best merge target first."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, mbid, subscription, ignored, track_count, last_checked "
            "FROM artists"
        ).fetchall()
        owned = {
            r["artist_id"]: r["c"]
            for r in conn.execute(
                "SELECT artist_id, COUNT(*) c FROM owned_albums GROUP BY artist_id"
            )
        }
        dismissed = {
            r["group_key"]: r["signature"]
            for r in conn.execute("SELECT group_key, signature FROM merge_dismissals")
        }
    finally:
        conn.close()

    groups = {}
    for row in rows:
        if db.is_non_artist(row["name"]):
            continue
        key = db.match_key(row["name"])
        if key:
            groups.setdefault(("name", key), []).append(row)
        if row["mbid"]:
            groups.setdefault(("mbid", row["mbid"]), []).append(row)

    out = []
    seen_ids = set()
    for (kind, key), members in groups.items():
        if len(members) < 2:
            continue
        ids = {m["id"] for m in members}
        # A group already covered by a stronger suggestion (same rows, matched
        # by name) doesn't need saying twice.
        if ids <= seen_ids:
            continue
        group_key = f"{kind}:{key}"
        signature = _signature(ids)
        if not include_dismissed and dismissed.get(group_key) == signature:
            continue
        ordered = sorted(members, key=_score, reverse=True)
        seen_ids |= ids
        out.append({
            "key": group_key,
            "signature": signature,
            "reason": ("Same name apart from punctuation"
                       if kind == "name" else
                       "Same MusicBrainz artist under different names"),
            "target": _member(ordered[0], owned),
            "members": [_member(m, owned) for m in ordered[1:]],
            "dismissed": dismissed.get(group_key) == signature,
        })
    # Most consequential first: the groups holding the most music.
    out.sort(key=lambda g: -(g["target"]["track_count"]
                             + sum(m["track_count"] for m in g["members"])))
    return out


def dismiss(group_key, signature):
    """Remember that these rows are not the same artist."""
    with db._write_lock:
        conn = db.get_connection()
        try:
            conn.execute(
                "INSERT INTO merge_dismissals (group_key, signature, dismissed_at) "
                "VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(group_key) DO UPDATE SET signature = excluded.signature, "
                "dismissed_at = excluded.dismissed_at",
                (group_key, signature),
            )
            conn.commit()
        finally:
            conn.close()


def restore(group_key=None):
    """Forget a dismissal (or all of them), so the suggestion comes back."""
    with db._write_lock:
        conn = db.get_connection()
        try:
            if group_key:
                conn.execute("DELETE FROM merge_dismissals WHERE group_key = ?",
                             (group_key,))
            else:
                conn.execute("DELETE FROM merge_dismissals")
            conn.commit()
        finally:
            conn.close()


def dismissed_count():
    conn = db.get_connection()
    try:
        return conn.execute("SELECT COUNT(*) c FROM merge_dismissals").fetchone()["c"]
    finally:
        conn.close()
