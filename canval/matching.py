"""Finding the vehicle when nobody spells it the same way twice.

A hand-written alias table breaks the first time someone types شكمان
instead of شاكمان, and it will always be one spelling behind. Sales type
fast, on a call, on a phone. The tool has to meet that.

Three layers, cheapest first:

  1. NORMALISE. Strip the differences that carry no meaning in Arabic:
     hamza forms (أ إ آ -> ا), taa marbuta (ة -> ه), alef maqsura,
     tatweel, diacritics. شاحنه and شاحنة become one word before anything
     else runs.

  2. TRANSLITERATE. Arabic script to latin, so شاكمان becomes shakman and
     can be compared with SHACMAN from the catalogue. Arabic writes short
     vowels rarely, so the comparison drops vowels on both sides: shkmn
     against shcmn.

  3. MEASURE. Whatever is left is compared by edit distance against the
     makes we know, with a threshold that scales with word length. Short
     words get almost no slack -- two edits on a four-letter word is a
     different word.

WHY NOT JUST FUZZY-MATCH EVERYTHING
-----------------------------------
Because a loose match that silently picks the wrong truck is worse than no
match: sales quote the wrong capability and nobody finds out until the
install. So a fuzzy hit is returned as a *suggestion* with its confidence,
and anything below the bar comes back as "did you mean" rather than an
answer.
"""

from __future__ import annotations

import re
import unicodedata

# ------------------------------------------------------------- normalise

_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670\u0640]")   # harakat + tatweel
_TRANSLATE = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ة": "ه", "ى": "ي", "ئ": "ي", "ؤ": "و", "ء": "",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
})


def fold(text: str) -> str:
    """Collapse the spelling differences that carry no meaning."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    t = _DIACRITICS.sub("", t).translate(_TRANSLATE).lower()
    t = re.sub(r"[^\w\u0600-\u06FF]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------- transliterate

_AR2LAT = {
    "ا": "a", "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h", "خ": "kh",
    "د": "d", "ذ": "z", "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s",
    "ض": "d", "ط": "t", "ظ": "z", "ع": "a", "غ": "gh", "ف": "f", "ق": "q",
    "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h", "و": "w", "ي": "y",
    "پ": "p", "چ": "ch", "ژ": "zh", "ڤ": "v", "گ": "g",
}


def translit(text: str) -> str:
    return "".join(_AR2LAT.get(ch, ch) for ch in fold(text))


_VOWELS = re.compile(r"[aeiou]")

# Sounds Arabic writes with one letter but latin spells several ways. Arabic
# has no V, so فولفو uses the F letter and must still reach VOLVO.
_EQUIV = str.maketrans({"v": "f", "p": "b", "q": "k", "g": "j", "z": "s"})

# Letters that genuinely have two readings. C is the awkward one: HIACE ends
# in an S sound, SCUDO starts with an S, but ACTROS and SCANIA take a K. One
# fixed choice gets half of them wrong, so both readings are kept and a match
# on either counts.
_AMBIGUOUS = {"c": ("k", "s"), "x": ("ks",), "j": ("j", "y")}


def _strip(latin: str) -> str:
    """Reduce a transliteration to its consonant skeleton.

    Order matters. Removing vowels first promotes an internal y to the
    front of the word, where the "keep the leading semivowel" rule then
    protects it -- which is why ايفيكو came out as yfk and never met
    IVECO's fk. Semivowels are judged on their original position.
    """
    latin = re.sub(r"(.)\1+", r"\1", latin)         # shhh -> sh
    latin = re.sub(r"(?<=.)[wy]", "", latin)       # vowel inside, consonant at the start
    return _VOWELS.sub("", latin)


def skeletons(text: str) -> set[str]:
    """Every consonant skeleton this spelling could stand for."""
    if not text:
        return set()
    latin = translit(text) if re.search(r"[\u0600-\u06FF]", text) else fold(text)
    latin = latin.translate(_EQUIV)

    forms = {latin}
    for ch, options in _AMBIGUOUS.items():
        if ch in latin:
            forms = {f.replace(ch, o) for f in forms for o in options}
    return {_strip(f) for f in forms if _strip(f)}


def skeleton(text: str) -> str:
    """The single most likely skeleton, for display and debugging."""
    forms = skeletons(text)
    return sorted(forms)[0] if forms else ""


# ------------------------------------------------------------- distance

def edits(a: str, b: str, cap: int = 4) -> int:
    """Levenshtein, abandoned once it passes `cap` -- we only care if it's close."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(v)
            best = min(best, v)
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def tolerance(word: str) -> int:
    """How wrong a word may be before it is a different word.

    Scaled by length on purpose: two edits turn "faw" into something else
    entirely, but leave "shakman" recognisable.
    """
    n = len(word)
    if n <= 2:
        return 0
    if n <= 4:
        return 1
    if n <= 6:
        return 1
    if n <= 9:
        return 2
    return 3


def _length_compatible(a: str, b: str) -> bool:
    """Guard against short skeletons colliding.

    Stripping vowels turns both toyota and tata into "tt". The written
    words look nothing alike and their lengths say so, so check those
    before trusting a short skeleton. The shorter the skeleton, the less
    slack it gets, because there is less of it to be right about.
    """
    la, lb = len(fold(a)), len(fold(b))
    if not la or not lb:
        return False
    shortest = min(len(s) for s in (skeletons(a) | skeletons(b))) if (
        skeletons(a) and skeletons(b)) else 0
    slack = 1 if shortest <= 2 else 2 if shortest <= 3 else 3
    return abs(la - lb) <= slack


def similar(a: str, b: str) -> bool:
    """Are these two words plausibly the same word, spelt differently?"""
    A, B = skeletons(a), skeletons(b)
    if not A or not B:
        return False
    if A & B:
        return _length_compatible(a, b)
    for sa in A:
        for sb in B:
            if len(sa) >= 4 and len(sb) >= 4 and (
                    sa.startswith(sb) or sb.startswith(sa)):
                return True
            if len(sa) >= 3 and edits(sa, sb, cap=3) <= tolerance(sa):
                return True
    return False


# ------------------------------------------------------------ suggesting

def rank(query: str, candidates: list[str], limit: int = 5) -> list[tuple[str, float]]:
    """Score catalogue names against a query, best first.

    Confidence is deliberately blunt: 1.0 for an exact skeleton match on
    every word, dropping as edits pile up. It is used to decide whether to
    answer or to ask, so it should not pretend to more precision than it has.
    """
    q_words = [w for w in fold(query).split() if w]
    if not q_words:
        return []

    scored = []
    for cand in candidates:
        c_words = [w for w in fold(cand).split() if w]
        if not c_words:
            continue
        total = 0.0
        for qw in q_words:
            best = 0.0
            Q = skeletons(qw)
            for cw in c_words:
                C = skeletons(cw)
                if not Q or not C:
                    continue
                if Q & C and _length_compatible(qw, cw):
                    best = max(best, 1.0)
                    continue
                for qs in Q:
                    for cs in C:
                        if len(qs) >= 4 and len(cs) >= 4 and (
                                cs.startswith(qs) or qs.startswith(cs)):
                            best = max(best, 0.85)
                        elif len(qs) >= 3:
                            d = edits(qs, cs, cap=3)
                            tol = tolerance(qs)
                            if tol and d <= tol:
                                best = max(best, 1.0 - (d / (tol + 1)) * 0.5)
            total += best
        score = total / len(q_words)
        if score > 0:
            scored.append((cand, round(score, 3)))

    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


# Confidence below which the tool asks instead of answering. Chosen so a
# single mistyped word still answers, but two unrecognised words do not.
ANSWER_BAR = 0.6


# ------------------------------------------------------- partial typing

def prefix_score(typed: str, candidate: str) -> float:
    """How well a half-typed query matches a catalogue name.

    Type-ahead cannot use the same rules as a finished search. "مر" is not
    a misspelling of MERCEDES, it is the first two letters of it, and the
    strict matcher rightly rejects it. Here a query word counts if it
    *begins* one of the candidate's words, by letters or by sound, and the
    last word is treated as unfinished while earlier ones must match
    properly -- that is what the person is actually doing.
    """
    q_words = [w for w in fold(typed).split() if w]
    if not q_words:
        return 0.0
    c_words = [w for w in fold(candidate).split() if w]
    if not c_words:
        return 0.0

    total = 0.0
    for i, qw in enumerate(q_words):
        partial = (i == len(q_words) - 1)     # only the last word is being typed
        best = 0.0
        Q = skeletons(qw)

        for pos, cw in enumerate(c_words):
            # The first word of a catalogue name is the make, which is what
            # people type. Later words are model codes, and letting TGX
            # compete with TOYOTA for "تو" puts the wrong truck on top.
            weight = 1.0 if pos == 0 else 0.72

            if qw == cw:
                best = max(best, 1.0 * weight)
                continue
            if cw.startswith(qw):
                best = max(best, (0.95 if partial else 0.8) * weight)
                continue

            C = skeletons(cw)
            if Q & C and _length_compatible(qw, cw):
                # A two-letter skeleton agreeing is weak evidence: ACK and
                # سك both reduce to "sk", which is true and useless.
                shortest = min(len(x) for x in (Q | C) if x) if (Q | C) else 0
                confidence = 1.0 if shortest >= 4 else 0.85 if shortest == 3 else 0.6
                best = max(best, confidence * weight)
                continue

            for qs in Q:
                if not qs:
                    continue
                for cs in C:
                    if cs.startswith(qs):
                        best = max(best, (0.9 if partial else 0.75) * weight)
                    elif len(qs) >= 3 and edits(qs, cs, cap=2) <= tolerance(qs):
                        best = max(best, 0.7 * weight)
        total += best

    return round(total / len(q_words), 3)


# A suggestion is cheap to ignore, so the bar is lower than for an answer.
SUGGEST_BAR = 0.55
