"""Deciding whether two catalogues mean the same artist, album or track.

Every source spells things its own way -- "Black Market Music" against "Black
Market Music (Deluxe Edition)", "Atom Bomb" against "Atom Bomb - Remastered" --
so a comparison has to be loose. Substring matching is *too* loose, though:
"Alex G" sits inside "Alex Gaudino" and "High" inside "High Times", which is
how a stranger's song ends up playing as the preview for your EP.

So names are compared as word lists instead: equal, or one a leading run of the
other with nothing left over but the words that label a release.
"""

import re
import unicodedata

_SPLIT = re.compile(r"[^0-9a-z]+")

# Apostrophes vanish rather than split a word: "Don't" is one word, and the
# catalogues disagree about whether to write it at all ("Dont Save Us").
_APOSTROPHES = re.compile(r"[\u0027\u2018\u2019\u02bc\u0060\u00b4]")

# Words that only ever describe an edition or a remaster, never the record
# itself, so a title may carry them and still be the same thing.
_EDITION_WORDS = frozenset({
    "ep", "lp", "single", "album", "deluxe", "edition", "remaster",
    "remastered", "remasters", "reissue", "expanded", "anniversary", "special",
    "bonus", "track", "tracks", "version", "explicit", "clean", "mono",
    "stereo", "digital", "instrumental",
})


# Letters that stand in for Latin ones in a stylised name, and letters that
# don't decompose to Latin on their own. Bands write themselves however they
# like -- CHVRCHΞS, MØ, Sigur Rós -- and each service copies a different part
# of it: Navidrome files the track artist as "CHVRCHΞS" while its own album
# artist is "CHVRCHES", so without this the app can see it owns a record and
# still refuse to play the file.
#
# Only shapes that read as the Latin letter are mapped. Greek and Cyrillic
# capitals that look identical to a Latin letter go to it; Ξ is the one
# deliberate stylisation (it is written for an E). Letters whose sound is the
# pair they're written as (Æ, Œ, ß) expand.
_CONFUSABLE = str.maketrans({
    # Greek, upper and lower where the lowercase is also unmistakable.
    "α": "a", "β": "b", "ε": "e", "ζ": "z", "η": "h", "ι": "i", "κ": "k",
    "μ": "m", "ν": "n", "ο": "o", "ρ": "p", "τ": "t", "υ": "y", "χ": "x",
    "ξ": "e", "σ": "s", "ς": "s",
    # Cyrillic lookalikes.
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
    # Latin letters NFKD leaves alone.
    "ø": "o", "đ": "d", "ð": "d", "ł": "l", "ħ": "h", "ŧ": "t", "ı": "i",
    "æ": "ae", "œ": "oe", "ß": "ss",
})


def tokens(text):
    """A name as lowercase words: accents folded, apostrophes and punctuation gone.

    Stylised letters fold to the Latin ones they stand for, so the same record
    matches however each service spells the act (see _CONFUSABLE).
    """
    folded = unicodedata.normalize("NFKD", (text or "").casefold())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return [t for t in _SPLIT.split(_APOSTROPHES.sub("", plain).translate(_CONFUSABLE)) if t]


# Separators that join two acts into one credit: "Aesop Rock and Blockhead",
# "Augustus Pablo/King Tubby", "Dday One | Glen Porter". Captured, so a part
# that turns out to belong to the name before it can be put back verbatim.
_CREDIT_SPLIT = re.compile(
    r"(\s*(?:/|\||\band\b|&|\+|\u00d7|\bx\b|\bvs\.?(?=\s)|\bwith\b|"
    r"\bfeaturing\b|\bfeat\.?(?=\s)|\bft\.?(?=\s))\s*)",
    re.IGNORECASE,
)

# After these, whatever follows is another act, never the tail of a band name:
# "Breakbot feat. The Ruckazoid" is two acts, where "Nick Cave and the Bad
# Seeds" is one.
_GUEST_JOINER = re.compile(r"\b(?:feat|ft|featuring|with|vs)\b", re.IGNORECASE)

# "Nick Cave and the Bad Seeds" is one act: a part starting with "the" is the
# tail of the name before it, so it gets joined back on rather than standing as
# an act of its own.
_BAND_TAIL = re.compile(r"^the\b", re.IGNORECASE)


def _joined_parts(text):
    """Split on the joiners, putting "and the ..." tails back where they belong."""
    chunks = _CREDIT_SPLIT.split(text)
    parts, seps = chunks[0::2], chunks[1::2]
    out = [parts[0]]
    for sep, part in zip(seps, parts[1:]):
        if _BAND_TAIL.match(part.strip()) and not _GUEST_JOINER.search(sep):
            out[-1] = out[-1] + sep + part
        else:
            out.append(part)
    return [p.strip(" -") for p in out if p.strip(" -")]


def _comma_parts(text):
    """Acts listed with commas: "DJ Shadow, Little Dragon", "E.VAX, Ratatat".

    Only reached when nothing else joined the name, which is what keeps the
    band names safe: "Earth, Wind & Fire" and "Blood, Sweat & Tears" are split
    by their ampersand first and then thrown out for having a comma left inside
    a part. "Tyler, The Creator" survives because a "The ..." piece is treated
    as the tail of the name before it, exactly as elsewhere.
    """
    pieces = [p.strip() for p in text.split(",") if p.strip()]
    if len(pieces) < 2:
        return []
    parts = [pieces[0]]
    for piece in pieces[1:]:
        if _BAND_TAIL.match(piece):
            parts[-1] = f"{parts[-1]}, {piece}"
        else:
            parts.append(piece)
    return parts if len(parts) >= 2 else []


def split_credit(name):
    """The acts named in a joint credit, in the order they're credited.

    Returns [] for an ordinary artist name -- including the "X and the Y"
    pattern, which reads like a collaboration and isn't. Used when no source
    has a picture of the pairing: the first-named act's photo is better than an
    empty square (see app/metadata.py).
    """
    text = (name or "").strip()
    if not text:
        return []
    parts = _joined_parts(text)
    if len(parts) < 2:
        # Nothing joined them, so try a plain list: "Dee Gees, Foo Fighters".
        parts = _comma_parts(text)
    if len(parts) < 2:
        return []
    for part in parts:
        # Too short to search for, or a comma the joiners split through the
        # middle of ("Earth, Wind" out of "Earth, Wind & Fire").
        if len(part) < 2 or "," in part:
            return []
    return parts


# "Simon & Garfunkel" and "Simon and Garfunkel" are the same duo, and the
# ampersand is already dropped by tokenising, so the spelled-out word goes too.
_JOINERS = frozenset({"and"})


def _bare(text):
    """Casefolded text with whitespace and punctuation spacing removed."""
    folded = unicodedata.normalize("NFKC", (text or "").casefold())
    return "".join(folded.split())


def _compared(text):
    """Tokens as compared: spelling-only differences removed.

    Joining words go ("Simon & Garfunkel" is "Simon and Garfunkel"), and so
    does a leading article -- every catalogue disagrees about whether the band
    is "The White Panda" or "White Panda", which is the same disagreement a
    library's sort name settles by dropping it.
    """
    words = tokens(text)
    stripped = [w for w in words if w not in _JOINERS]
    if len(stripped) > 1 and stripped[0] == "the":
        stripped = stripped[1:]
    return stripped or words


def same_name(left, right):
    """Do these two names refer to the same artist, release or track?

    Equal word lists match. So does one being a leading run of the other, but
    only when everything after that run is release-label noise -- which keeps
    "High" / "High - EP" together while keeping "High" / "High Times" apart.
    """
    a, b = _compared(left), _compared(right)
    if not a or not b:
        # Nothing Latin to compare: "!!!", "宇山寛人", "⣎⡇ꉺლ༽இ•̛)ྀ◞". Those are
        # real acts, so fall back to the raw text rather than refusing every
        # comparison -- which is what kept the app from playing its own copy.
        return _bare(left) == _bare(right) and bool(_bare(left))
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if long_[: len(short)] != short:
        return False
    return all(word in _EDITION_WORDS for word in long_[len(short):])
