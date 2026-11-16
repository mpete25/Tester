"""What was chat saying? Top reactions, frequent words, notable messages, titles.

Everything here is rule based and runs locally – no AI service is needed.
"""
from __future__ import annotations

import re
from collections import Counter

import numpy as np
import pandas as pd

from .analysis import Highlight
from .reactions import FAIL, FUNNY, GENERAL, HYPE, SHOCK, content_words, message_reactions
from .vod import VodInfo

_WS = re.compile(r"\s+")

TITLE_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "da": {
        FUNNY: ["Chatten flækker af grin over {streamer} 😂", "{streamer}: stream­ens sjoveste øjeblik? ({reaction} x{count})",
                "Det her fik hele chatten til at skrive {reaction}"],
        HYPE: ["{streamer} leverer – chatten går amok 🔥", "Vildt øjeblik i {game}! ({reaction} x{count})",
               "Det øjeblik hvor chatten eksploderede: {reaction}"],
        SHOCK: ["Chatten er i chok: {reaction} 😱", "Hvad skete der lige hos {streamer}?!",
                "Ingen havde set det komme ({reaction} x{count})"],
        FAIL: ["Av! {streamer} fejler stort 💀", "F i chatten – det gik helt galt ({reaction} x{count})",
               "Uheldigste øjeblik i streamen?"],
        GENERAL: ["Chatten gik amok hos {streamer}", "Streamens mest aktive øjeblik (+{increase}% chat)",
                  "Det øjeblik alle skrev i chatten"],
    },
    "en": {
        FUNNY: ["{streamer} has chat crying laughing 😂", "Funniest moment of the stream? ({reaction} x{count})",
                "The moment all of chat typed {reaction}"],
        HYPE: ["{streamer} pops off and chat goes wild 🔥", "Insane {game} moment! ({reaction} x{count})",
               "Chat explodes: {reaction}"],
        SHOCK: ["Chat is in shock: {reaction} 😱", "What just happened to {streamer}?!",
                "Nobody saw this coming ({reaction} x{count})"],
        FAIL: ["Ouch! {streamer} with the biggest fail 💀", "F in chat – it all went wrong ({reaction} x{count})",
               "Unluckiest moment of the stream?"],
        GENERAL: ["Chat goes crazy for {streamer}", "Most active moment of the stream (+{increase}% chat)",
                  "The moment everyone was typing"],
    },
}


def _normalize(body: str) -> str:
    """Lower-case and collapse 'KEKW KEKW KEKW' to 'kekw' so repeats group together."""
    tokens = _WS.sub(" ", body.strip().lower()).split(" ")
    out: list[str] = []
    for token in tokens:
        if not out or out[-1] != token:
            out.append(token)
    return " ".join(out)


def event_messages(messages: pd.DataFrame, start: float, end: float) -> pd.DataFrame:
    """Non-bot messages with ``start <= offset < end`` (``messages`` sorted by offset)."""
    offsets = messages["offset"].to_numpy()
    lo, hi = np.searchsorted(offsets, [start, end], side="left")
    chunk = messages.iloc[lo:hi]
    if "bot" in chunk.columns:
        chunk = chunk.loc[~chunk["bot"]]
    return chunk


def top_reactions(chunk: pd.DataFrame, limit: int = 5) -> list[list]:
    counter: Counter = Counter()
    for body in chunk["body"]:
        for display, _cat in message_reactions(body):
            counter[display] += 1
    return [[text, int(n)] for text, n in counter.most_common(limit)]


def top_words(chunk: pd.DataFrame, background: pd.DataFrame | None = None, limit: int = 6) -> list[list]:
    """Words typical for this moment.

    A word counts once per chatter (one person cannot dominate) and must be at
    least three times as common as in ``background`` – the chat just before
    the moment – so everyday chatter ("stream", "game") is left out.
    """
    per_word_users: dict[str, set] = {}
    for user, body in zip(chunk["user"], chunk["body"]):
        for word in set(content_words(body)):
            per_word_users.setdefault(word, set()).add(user)
    usual: Counter = Counter()
    n_background = 0
    if background is not None and len(background):
        n_background = len(background)
        for body in background["body"]:
            usual.update(set(content_words(body)))
    n_event = max(1, len(chunk))
    ranked = []
    for word, users in per_word_users.items():
        if len(users) < 3:
            continue
        lift = (len(users) / n_event) / ((usual[word] + 1) / (n_background + 20))
        if lift >= 3.0:
            ranked.append((word, len(users)))
    ranked.sort(key=lambda x: (-x[1], x[0]))
    return [[w, int(n)] for w, n in ranked[:limit]]


def repeated_messages(chunk: pd.DataFrame, limit: int = 3) -> list[list]:
    """Messages many different people sent (what chat was spamming)."""
    users_per_msg: dict[str, set] = {}
    display: dict[str, str] = {}
    for user, body in zip(chunk["user"], chunk["body"]):
        key = _normalize(body)
        if not key or len(key) > 80:
            continue
        users_per_msg.setdefault(key, set()).add(user)
        display.setdefault(key, body if len(body) <= 60 else key)
    ranked = sorted(((k, len(u)) for k, u in users_per_msg.items() if len(u) >= 3), key=lambda x: (-x[1], x[0]))
    return [[display[k], int(n)] for k, n in ranked[:limit]]


def notable_messages(chunk: pd.DataFrame, words: list[list], limit: int = 5) -> list[dict]:
    """Longer messages that describe the moment, one per user, in time order.

    A message is interesting when it uses the words many chatters use right
    now (so it is about the moment) and is more than a bare reaction.
    """
    hot = {w: n for w, n in words}
    if len(chunk) == 0:
        return []
    start = float(chunk["offset"].iloc[0])
    span = max(1.0, float(chunk["offset"].iloc[-1]) - start)
    seen_text: set[str] = set()
    seen_user: set[str] = set()
    scored: list[tuple[float, float, str, str]] = []
    for offset, user, name, body in zip(chunk["offset"], chunk["user"], chunk["name"], chunk["body"]):
        if not 12 <= len(body) <= 160 or "http" in body.lower():
            continue
        cw = content_words(body)
        if len(cw) < 2:
            continue
        key = _normalize(body)
        if key in seen_text or user in seen_user:
            continue
        relevance = sum(min(hot.get(w, 0), 20) for w in set(cw)) / 10.0
        if hot and relevance == 0:
            continue  # ordinary chatter that happens to fall inside the moment
        has_reaction = 0.5 if message_reactions(body) else 0.0
        early = 0.5 * (1.0 - (offset - start) / span)
        mention_penalty = 1.0 if "@" in body else 0.0
        seen_text.add(key)
        seen_user.add(user)
        scored.append((relevance + has_reaction + early - mention_penalty + min(len(cw), 6) * 0.1, float(offset), name or user, body))
    best = sorted(scored, key=lambda x: -x[0])[:limit]
    return [{"time": round(t, 1), "user": u, "text": b} for _s, t, u, b in sorted(best, key=lambda x: x[1])]


def suggest_titles(h: Highlight, info: VodInfo | None, language: str = "da") -> list[str]:
    templates = TITLE_TEMPLATES.get(language, TITLE_TEMPLATES["en"])
    kind = h.kind if h.kind in templates else GENERAL
    reaction, count = (h.top_reactions[0] if h.top_reactions else ("", 0))
    streamer = (info.streamer if info else "") or ("Streameren" if language == "da" else "The streamer")
    game = (info.game if info else "") or "stream"
    if not reaction:
        kind = GENERAL
    titles = []
    for template in templates[kind]:
        text = template.format(streamer=streamer, game=game, reaction=reaction, count=count, increase=int(h.increase_pct))
        titles.append(text.replace("­", ""))
    # A title built from what chat was actually talking about.
    words = [w for w, _n in h.top_words[:2]]
    if words:
        subject = " ".join(words).upper()
        extra = f"{streamer}: {subject} {reaction}".strip()
        titles.insert(1, extra)
    seen: set[str] = set()
    return [t for t in titles if not (t in seen or seen.add(t))]


def enrich_highlights(highlights: list[Highlight], messages: pd.DataFrame, info: VodInfo | None, language: str = "da") -> None:
    """Fill reactions, words, messages and titles on each highlight (in place)."""
    for h in highlights:
        chunk = event_messages(messages, h.reaction_start, h.reaction_end)
        h.top_reactions = top_reactions(chunk)
        h.top_words = top_words(chunk, event_messages(messages, h.reaction_start - 300, h.reaction_start))
        h.repeated_messages = repeated_messages(chunk)
        h.notable_messages = notable_messages(chunk, h.top_words)
        h.titles = suggest_titles(h, info, language)
