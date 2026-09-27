"""The "For you" ranking of the Discover feed, and what each row says about you.

Every Discover row gets a score and the reasons behind it, from what the app
already knows -- nothing here goes to the network:

- you follow the artist, or have them in the library;
- how many of your artists they're similar to (the similar-artist table);
- how often you've played them (your Last.fm top artists, as last fetched);
- the genres your library leans to, and whether the row shares them;
- the critic score (Metacritic, see app/critics.py);
- how many sources list the release, and whether Last.fm picked it for you.

The same pass flags what the page shows alongside: heard, saved for later,
when a source first listed it.
"""

import math

from . import critics, db, lastfm

# How many of the library's genres count as "yours".
_TOP_GENRES = 40


def _genre_key(g):
    return "".join(ch for ch in (g or "").lower() if ch.isalnum())


def _library_genres():
    """{genre key: (weight 0..1, label)} over the artists the library holds."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT genres FROM artists WHERE track_count > 0 AND COALESCE(genres, '') <> ''"
        ).fetchall()
    finally:
        conn.close()
    counts = {}
    labels = {}
    for row in rows:
        for g in db.parse_genres(row["genres"])[:6]:
            k = _genre_key(g)
            if k:
                counts[k] = counts.get(k, 0) + 1
                labels.setdefault(k, g.replace(".", " ").replace("_", " "))
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:_TOP_GENRES]
    if not top:
        return {}
    most = top[0][1]
    return {k: (n / most, labels[k]) for k, n in top}


def _artist_facts(names):
    """({lower name: (track_count, subscription)}, {lower name: similar count})."""
    if not names:
        return {}, {}
    conn = db.get_connection()
    try:
        marks = ",".join("?" * len(names))
        rows = conn.execute(
            f"SELECT sort_name, track_count, subscription FROM artists "
            f"WHERE sort_name IN ({marks})", list(names),
        ).fetchall()
        similar = conn.execute(
            f"SELECT lower(name) AS n, COUNT(DISTINCT lower(source_artist)) AS c "
            f"FROM similar_artists WHERE lower(name) IN ({marks}) GROUP BY lower(name)",
            list(names),
        ).fetchall()
    finally:
        conn.close()
    facts = {r["sort_name"]: (r["track_count"] or 0, r["subscription"]) for r in rows}
    return facts, {r["n"]: r["c"] for r in similar}


def _plays():
    """lower name -> your Last.fm playcount (all time), as last fetched."""
    out = {}
    for entry in lastfm.cached_top_artists("overall", 1000):
        name = (entry.get("name") or "").lower()
        if name:
            out[name] = int(entry.get("playcount") or 0)
    return out


def annotate(items):
    """Give each feed row for_you, reasons, score, heard, saved and first_seen."""
    names = {(it.get("artist") or "").strip().lower() for it in items if it.get("artist")}
    facts, similar = _artist_facts(names)
    plays = _plays()
    genres = _library_genres()
    scores = critics.scores()
    heard = db.heard_set()
    saved = db.wishlist_keys()
    first_seen = db.discover_first_seen()

    for it in items:
        artist = (it.get("artist") or "").strip()
        album = (it.get("album") or "").strip()
        a, b = artist.lower(), album.lower()
        it["heard"] = (a, b) in heard
        it["saved"] = bool(album) and (a, b) in saved
        it["first_seen"] = first_seen.get((a, b))
        critic = scores.get(critics.key(artist, album)) if album else None
        it["score"] = critic["score"] if critic else None
        it["score_url"] = critic["url"] if critic else None

        # (points, reason) pairs; the reasons are shown biggest first.
        parts = []
        tracks, sub = facts.get(a, (0, None))
        if sub in ("subscribed", "notify"):
            parts.append((10, "You follow them"))
        if tracks:
            parts.append((12, "In your library"))
        count = similar.get(a, 0)
        if count:
            parts.append((min(30, 6 * count),
                          f"Similar to {count} of your artists" if count > 1
                          else "Similar to one of your artists"))
        played = plays.get(a, 0)
        if played:
            parts.append((min(25, round(6 * math.log2(1 + played / 10))),
                          f"{played:,} plays on Last.fm"))
        tags = list(it.get("artist_genres") or []) + list(it.get("genres") or [])
        matched = []
        for g in tags:
            hit = genres.get(_genre_key(g))
            if hit and hit not in matched:
                matched.append(hit)
        if matched:
            matched.sort(key=lambda h: -h[0])
            points = min(15, round(10 * matched[0][0]) + 2 * (len(matched) - 1))
            if points:
                parts.append((points, "Your kind of " + matched[0][1].lower()))
        if critic and critic["score"] >= 60:
            parts.append((min(25, round((critic["score"] - 60) * 0.75)),
                          f"Metascore {critic['score']}"))
        sources = it.get("sources") or []
        if len(sources) > 1:
            parts.append((5 * (len(sources) - 1), f"On {len(sources)} sources"))
        if any(s.get("key") == "lastfm" for s in sources):
            parts.append((10, "Recommended by Last.fm"))

        total = sum(p for p, _r in parts)
        # New music is the point: a record you already own sinks.
        if it.get("owned"):
            total -= 30
        it["for_you"] = total
        it["reasons"] = [r for p, r in sorted(parts, key=lambda pr: -pr[0]) if p > 0]
    return items
