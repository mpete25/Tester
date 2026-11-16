"""Synthetic but realistic chat in TwitchDownloader's JSON format.

Used by the automated tests and by the demo mode in the app. The stream has:

* slowly drifting background chatter with a long "generally busy" stretch,
* planted reaction bursts of different types (the ground truth),
* a timer bot, ``!command`` spam and one user flooding a single interval.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DEMO_VOD_ID = "9000000001"

_FILLER = [
    "what game is this", "first time here, love the stream", "anyone know the song name?",
    "he should rotate earlier", "that skin looks sick", "hello from Denmark", "chat is this ranked?",
    "how long has he been live", "this map is so bad", "go left side", "he needs to buy armor",
    "back from dinner what did I miss", "the audio is a bit low", "yo", "hi chat", "true",
    "good morning everyone", "is the tournament tomorrow?", "I think he can win this one",
    "who is he playing against", "drop the settings please", "nice crosshair", "ok", "yeah",
    "that was a good round", "gl", "kom så", "hvad sker der her", "god aften chat",
    "can we get a drops timer", "mods are asleep", "he always does this", "interesting strat",
    "the new patch changed everything", "why not use the other weapon", "lol", "nice", "KEKW",
]
_BURSTS = {
    "funny": (["KEKW", "LMAO", "LUL", "OMEGALUL", "LOL", "ICANT", "hahaha", "😂", "LMAOOO", "KEKW KEKW KEKW"],
              ["HE FELL OFF THE MAP KEKW", "bro walked straight off the edge LMAO", "that fall was hilarious",
               "clip that fall please", "he really fell off the map again", "not the map edge again 💀"]),
    "hype": (["POG", "PogChamp", "POGGERS", "LETSGO", "W", "CLUTCH", "GG", "EZ Clap", "INSANE", "LETS GOOO"],
             ["1v4 CLUTCH LETSGO", "that clutch was insane", "best clutch of the tournament", "HOW DID HE WIN THAT",
              "the ace clutch omg", "clip the clutch right now"]),
    "shock": (["WTF", "OMG", "WHAT", "???", "monkaS", "NO WAY", "D:", "HOLY", "WHAT WAS THAT", "?????"],
              ["WTF was that sound", "did the lights just go out", "NO WAY that just happened", "the power went out??",
               "what was that noise monkaS", "omg the whole room went dark"]),
    "fail": (["F", "RIP", "Sadge", "NotLikeThis", "L", "NOOOO", "unlucky", "OOF", "FFFF", "BibleThump"],
             ["he lost the run at the last boss", "RIP the world record run", "the run is dead Sadge",
              "one hit from the record NOOOO", "that run was so close", "F for the run"]),
}


@dataclass(frozen=True)
class PlantedEvent:
    time: float        # seconds into the stream where the moment happens
    kind: str
    strength: float    # peak messages/second added on top of the background
    length: float      # seconds the reaction lasts


def default_events(duration: float) -> list[PlantedEvent]:
    """Events spread over the stream; the two close ones test overlap handling."""
    marks = [
        (0.06, "funny", 9.0, 35), (0.14, "hype", 14.0, 45), (0.22, "shock", 6.0, 25),
        (0.31, "funny", 4.0, 25), (0.42, "hype", 18.0, 50), (0.425, "funny", 7.0, 30),
        (0.55, "fail", 8.0, 35), (0.63, "shock", 12.0, 40), (0.74, "funny", 16.0, 45),
        (0.86, "hype", 5.0, 30), (0.95, "shock", 9.0, 35),
    ]
    return [PlantedEvent(round(duration * f), kind, strength, length) for f, kind, strength, length in marks]


def _background_rate(t: np.ndarray, duration: float, base: float) -> np.ndarray:
    """Messages per second: slow drift plus a busy stretch (55%-70% of the stream)."""
    drift = 1.0 + 0.35 * np.sin(2 * np.pi * t / duration * 1.5) + 0.15 * np.sin(2 * np.pi * t / 900.0)
    busy = np.where((t > 0.55 * duration) & (t < 0.70 * duration), 2.5, 1.0)
    return base * drift * busy


def generate_messages(
    duration: float = 6 * 3600, base_rate: float = 1.5, n_users: int = 4000, seed: int = 7,
    events: list[PlantedEvent] | None = None,
) -> tuple[list[tuple[float, str, str]], list[PlantedEvent]]:
    """Return ``([(offset, user, body), ...] sorted by time, planted_events)``."""
    rng = np.random.default_rng(seed)
    events = default_events(duration) if events is None else events
    user_weights = 1.0 / np.arange(1, n_users + 1) ** 0.8
    user_weights /= user_weights.sum()
    rows: list[tuple[float, str, str]] = []

    # Background chatter, generated second by second (Poisson with gamma noise = bursty).
    seconds = np.arange(int(duration))
    rate = _background_rate(seconds.astype(float), duration, base_rate)
    counts = rng.poisson(rate * rng.gamma(4.0, 0.25, size=len(seconds)))
    times = np.repeat(seconds, counts) + rng.random(int(counts.sum()))
    users = rng.choice(n_users, size=len(times), p=user_weights)
    texts = rng.choice(len(_FILLER), size=len(times))
    rows.extend((float(t), f"user{u}", _FILLER[x]) for t, u, x in zip(times, users, texts))

    # Reaction bursts: chat reacts ~4 s after the moment, ramps up fast, decays slowly.
    for ev in events:
        short, long_msgs = _BURSTS[ev.kind]
        n = int(ev.strength * ev.length * 0.55)
        delay = 4.0 + rng.gamma(2.0, ev.length / 6.0, size=n)
        who = rng.choice(n_users, size=n, p=user_weights)
        for d, u in zip(delay, who):
            body = rng.choice(long_msgs) if rng.random() < 0.18 else rng.choice(short)
            rows.append((float(ev.time + d), f"user{u}", str(body)))

    # Noise that must NOT become highlights.
    for t in np.arange(120, duration, 300):          # timer bot
        rows.append((float(t), "Nightbot", "Follow the socials at example.com/socials and join the discord!"))
    for t in np.arange(200, duration, 45):           # promo account repeating itself
        rows.append((float(t), "promo_guy", "Best cheap viewers and followers at bestviewers dot example"))
    spam_at = duration * 0.36                         # one user floods a single interval
    rows.extend((float(spam_at + i * 0.1), "spammer99", "KEKW KEKW KEKW KEKW") for i in range(90))
    cmd_at = duration * 0.48                          # everyone types a command at once
    rows.extend((float(cmd_at + rng.random() * 8), f"user{u}", "!drop") for u in rng.choice(n_users, size=120))

    rows = [r for r in rows if 0 <= r[0] < duration]
    rows.sort(key=lambda r: r[0])
    return rows, events


def write_chat_json(
    path: Path, duration: float = 6 * 3600, vod_id: str = DEMO_VOD_ID, title: str = "Demo stream – tournament finals",
    streamer: str = "DemoStreamer", game: str = "Demo Game", **kwargs,
) -> list[PlantedEvent]:
    """Write a chat file with the same structure TwitchDownloaderCLI produces."""
    rows, events = generate_messages(duration=duration, **kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        head = {
            "FileInfo": {"Version": {"Major": 1, "Minor": 4, "Patch": 0}, "CreatedAt": "2026-01-01T00:00:00Z"},
            "streamer": {"name": streamer, "login": streamer.lower(), "id": 123456},
            "video": {"title": title, "description": None, "id": vod_id, "created_at": "2026-01-01T18:00:00Z",
                      "start": 0, "end": duration, "length": duration, "viewCount": 12345, "game": game, "chapters": []},
        }
        fh.write(json.dumps(head, ensure_ascii=False)[:-1] + ', "comments": [\n')
        for i, (offset, user, body) in enumerate(rows):
            comment = {
                "_id": f"c{i}",
                "created_at": "2026-01-01T18:00:00Z",
                "channel_id": "123456",
                "content_type": "video",
                "content_id": vod_id,
                "content_offset_seconds": round(offset, 3),
                "commenter": {"display_name": user, "_id": f"id-{user.lower()}", "name": user.lower(), "bio": None,
                              "created_at": "2020-01-01T00:00:00Z", "updated_at": "2020-01-01T00:00:00Z", "logo": None},
                "message": {"body": body, "bits_spent": 0, "fragments": [{"text": body, "emoticon": None}],
                            "user_badges": [], "user_color": None, "emoticons": []},
            }
            fh.write(("" if i == 0 else ",\n") + json.dumps(comment, ensure_ascii=False))
        fh.write('\n], "embeddedData": null}\n')
    return events
