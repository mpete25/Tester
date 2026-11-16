"""Lexicon of chat reactions (LOL, KEKW, WTF, Pog …) and their highlight type."""
from __future__ import annotations

import re
from functools import lru_cache

# Highlight types -----------------------------------------------------------
FUNNY, HYPE, SHOCK, FAIL, GENERAL = "funny", "hype", "shock", "fail", "general"
CATEGORIES = (FUNNY, HYPE, SHOCK, FAIL)
CATEGORY_LABELS = {
    FUNNY: "😂 Sjov situation",
    HYPE: "🔥 Spændende øjeblik",
    SHOCK: "😱 Stor reaktion",
    FAIL: "💀 Fail / uheld",
    GENERAL: "💬 Chat-peak",
}

# token (lower-case) -> display form. Emotes keep their usual spelling.
_WORDS: dict[str, dict[str, str]] = {
    FUNNY: {
        "lul": "LUL", "lulw": "LULW", "omegalul": "OMEGALUL", "kekw": "KEKW", "kek": "KEK",
        "kekl": "KEKL", "lol": "LOL", "lmao": "LMAO", "lmfao": "LMFAO", "rofl": "ROFL",
        "xd": "XD", "xdd": "XD", "icant": "ICANT", "pepelaugh": "PepeLaugh", "lulz": "LULZ",
        "omegalaughing": "OMEGALAUGHING", "4head": "4Head", "elol": "EleGiggle",
        "elegiggle": "EleGiggle", "jajaja": "JAJAJA", "kkkk": "KKKK", "classic": "Classic",
        "😂": "😂", "🤣": "🤣", "💀": "💀", "😭": "😭", "😹": "😹",
    },
    HYPE: {
        "pog": "Pog", "pogchamp": "PogChamp", "poggers": "POGGERS", "pogu": "PogU",
        "pogcrazy": "POGCRAZY", "pagman": "PagMan", "pagchomp": "PagChomp", "letsgo": "LETSGO",
        "lfg": "LFG", "hype": "HYPE", "ez": "EZ", "ezclap": "EZ Clap", "gg": "GG", "ggs": "GG",
        "clutch": "CLUTCH", "goat": "GOAT", "goated": "GOATED", "sheesh": "SHEESH",
        "kreygasm": "Kreygasm", "banger": "BANGER", "cracked": "CRACKED", "clap": "Clap",
        "gigachad": "GIGACHAD", "ww": "W", "www": "W", "catjam": "catJAM", "pogo": "PogO",
        "🔥": "🔥", "👏": "👏", "🐐": "🐐", "🚀": "🚀",
    },
    SHOCK: {
        "wtf": "WTF", "wtff": "WTF", "omg": "OMG", "omfg": "OMFG", "monkas": "monkaS",
        "monkaw": "monkaW", "monkagiga": "monkaGIGA", "monkaomega": "monkaOMEGA", "d:": "D:",
        "wutface": "WutFace", "noway": "NO WAY", "aintnoway": "AINTNOWAY", "excuseme": "EXCUSE ME",
        "aware": "Aware", "yikes": "YIKES", "holy": "HOLY", "uhm": "UHM", "sus": "SUS",
        "hä": "HÄ", "hvad": "HVAD", "😱": "😱", "😳": "😳", "😨": "😨", "🤯": "🤯",
    },
    FAIL: {
        "rip": "RIP", "sadge": "Sadge", "notlikethis": "NotLikeThis", "biblethump": "BibleThump",
        "unlucky": "UNLUCKY", "oof": "OOF", "fail": "FAIL", "pepehands": "PepeHands",
        "sadkek": "SadKEK", "choke": "CHOKE", "choked": "CHOKE", "despair": "Despair",
        "throw": "THROW", "throwing": "THROW", "ff": "FF", "failfish": "FailFish",
        "copium": "COPIUM", "💔": "💔", "😢": "😢", "🪦": "🪦",
    },
}

# Ordinary words that are only a "reaction" when shouted or sent on their own.
_AMBIGUOUS: dict[str, tuple[str, str]] = {
    "what": ("WHAT", SHOCK), "wow": ("WOW", SHOCK), "bruh": ("BRUH", SHOCK), "huh": ("HUH", SHOCK),
    "how": ("HOW", SHOCK), "nah": ("NAH", SHOCK), "insane": ("INSANE", HYPE), "nice": ("NICE", HYPE),
    "crazy": ("CRAZY", HYPE), "dead": ("DEAD", FUNNY), "no": ("NO", FAIL), "why": ("WHY", FAIL),
    "win": ("WIN", HYPE), "yes": ("YES", HYPE),
}
# Single letters count only in upper case ("W", "L", "F").
_CASE_SENSITIVE = {"W": ("W", HYPE), "L": ("L", FAIL), "F": ("F", FAIL)}

_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^(?:l+o+)+l+z*$"), "LOL", FUNNY),
    (re.compile(r"^lm+f?a+o+$"), "LMAO", FUNNY),
    (re.compile(r"^(?:a?(?:ha){2,}h?|(?:he){2,}h?|(?:ja){3,}|(?:hi){3,})$"), "HAHA", FUNNY),
    (re.compile(r"^x+d+$"), "XD", FUNNY),
    (re.compile(r"^k+e+k+w+$"), "KEKW", FUNNY),
    (re.compile(r"^omegalul\w*$"), "OMEGALUL", FUNNY),
    (re.compile(r"^lul\w{0,3}$"), "LUL", FUNNY),
    (re.compile(r"^o+m+f?g+$"), "OMG", SHOCK),
    (re.compile(r"^w+t+f+$"), "WTF", SHOCK),
    (re.compile(r"^[?!]*\?[?!]+$"), "???", SHOCK),
    (re.compile(r"^wh+a+t+$"), "WHAT", SHOCK),
    (re.compile(r"^monka\w+$"), "monkaS", SHOCK),
    (re.compile(r"^po+g+(?:gers|champ|u|g+)?$"), "Pog", HYPE),
    (re.compile(r"^le+t+s+g+o+$"), "LETSGO", HYPE),
    (re.compile(r"^g+g+s?$"), "GG", HYPE),
    (re.compile(r"^!{3,}$"), "!!!", HYPE),
    (re.compile(r"^no{3,}$"), "NOOO", FAIL),
    (re.compile(r"^f{3,}$"), "F", FAIL),
    (re.compile(r"^sadge\w*$"), "Sadge", FAIL),
]

# Multi-word reactions, matched on the whole message before tokenising.
_PHRASES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\bno+ +(?:fucking +|f+ing +)?wa+y+\b", re.I), "NO WAY", SHOCK),
    (re.compile(r"\blet'?s +(?:f+ing +)?go+\b", re.I), "LETSGO", HYPE),
    (re.compile(r"\boh +my +(?:god|gosh)\b", re.I), "OMG", SHOCK),
    (re.compile(r"\bholy +(?:shit|sh\*t|moly|crap|fuck)\b", re.I), "HOLY", SHOCK),
    (re.compile(r"\bi'?m +dead\b", re.I), "DEAD", FUNNY),
]

_LOOKUP: dict[str, tuple[str, str]] = {}
for _cat, _words in _WORDS.items():
    for _tok, _display in _words.items():
        _LOOKUP[_tok] = (_display, _cat)

_STRIP = ".,;:!?\"'()[]{}*~_-…"

STOPWORDS = frozenset(
    """a an the and or but if then so to of in on at for with from by is are was were be been am im i
    you he she it we they me my your his her its our their this that these those there here what who
    how why when where not no yes do does did dont cant can will would should could just like get got
    have has had all some any one out up down as too very really more than now him them u ur he's it's
    that's i'm you're was let lets go going gonna did hes shes thats whats bro chat guys man dude one
    og i at det er en et den til på de med han af for ikke der var mig sig men har om vi min havde ham
    hun nu over da fra du ud sin dem os op jeg kan hvad så her skal bare også din nej ja lige
    off again right still back into about only even ever never always already much many most
    him her did its been being after before because while through same other another""".split()
)


@lru_cache(maxsize=200_000)
def classify_token(token: str) -> tuple[str, str, bool] | None:
    """Return ``(display, category, ambiguous)`` if the token is a reaction."""
    if token in _CASE_SENSITIVE:
        display, cat = _CASE_SENSITIVE[token]
        return display, cat, False
    stripped = token.strip(_STRIP) or token
    low = stripped.lower()
    hit = _LOOKUP.get(low)
    if hit:
        return hit[0], hit[1], False
    if low in _AMBIGUOUS:
        display, cat = _AMBIGUOUS[low]
        # Shouted ("WHAT") counts everywhere; lower-case only in very short messages.
        return display, cat, not (stripped.isupper() and len(stripped) > 1)
    raw_low = token.lower()
    for pattern, display, cat in _PATTERNS:
        if pattern.match(low) or pattern.match(raw_low):
            return display, cat, False
    return None


def message_reactions(body: str) -> list[tuple[str, str]]:
    """Distinct ``(display, category)`` reactions in one chat message."""
    seen: dict[str, str] = {}
    if " " in body:
        for pattern, display, cat in _PHRASES:
            if pattern.search(body):
                seen.setdefault(display, cat)
                body = pattern.sub(" ", body)
    tokens = body.split()
    if not tokens:
        return list(seen.items())
    short = len(tokens) <= 3
    for token in tokens[:40]:
        hit = classify_token(token)
        if hit is None:
            continue
        display, cat, ambiguous = hit
        if ambiguous and not short:
            continue
        seen.setdefault(display, cat)
    return list(seen.items())


def content_words(body: str) -> list[str]:
    """Lower-cased non-reaction, non-stopword words (for 'frequent words')."""
    words = []
    if " " in body:
        for pattern, _display, _cat in _PHRASES:
            body = pattern.sub(" ", body)
    for token in body.split()[:40]:
        if classify_token(token) is not None:
            continue
        word = token.strip(_STRIP).lower()
        if len(word) < 3 or len(word) > 24 or word in STOPWORDS:
            continue
        if word.startswith(("http", "@", "!")) or not any(c.isalpha() for c in word):
            continue
        words.append(word)
    return words
