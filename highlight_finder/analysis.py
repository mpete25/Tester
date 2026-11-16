"""Chat activity analysis: binning, baseline, peak detection and scoring.

Pipeline (all vectorised, a 6 hour / 500k message chat takes a few seconds):

1. **Clean** – drop known bots, ``!commands`` and accounts that post the same
   long message over and over.
2. **Bin** – count messages per 10 s interval. Each user counts at most
   ``per_user_cap`` times per interval, so one spammer cannot create a peak.
3. **Baseline** – a centred rolling median (default 10 min) is the "normal"
   activity at that point of the stream; a rolling MAD gives the normal spread.
   A long stretch of generally busy chat raises the baseline and therefore
   does *not* count as a peak – only bursts above the local level do.
4. **Detect** – intervals whose robust z-score and relative increase exceed the
   sensitivity thresholds become events; neighbouring intervals are grouped.
5. **Score** 0–100 from z-score, relative increase, unique chatters, volume,
   share of reaction messages and how long the reaction lasted.
6. **Clip windows** – start ``pre_roll`` seconds before the reaction begins,
   end after it has died down; overlapping windows are merged or dropped.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd

from .config import AnalysisSettings
from .logging_setup import get_logger
from .reactions import CATEGORIES, GENERAL, message_reactions

log = get_logger("analysis")

KNOWN_BOTS = frozenset(
    """nightbot streamelements streamlabs moobot fossabot wizebot soundalerts sery_bot botrix
    deepbot phantombot stay_hydrated_bot commanderroot pokemoncommunitygame creatisbot
    streamholics own3d kofistreambot songlistbot lolrankbot buttsbot restreambot tangiabot
    blerp frostytoolsdotcom pretzelrocks""".split()
)

MIN_PEAK_MESSAGES = 6     # counted messages in the peak interval
MIN_PEAK_USERS = 4        # distinct chatters in the peak interval
MIN_TOTAL_MESSAGES = 30   # below this the chat is too thin to analyse

SCORE_WEIGHTS = {
    "intensity": 0.25,    # robust z-score: how unusual is the burst
    "increase": 0.20,     # relative increase over normal activity
    "users": 0.20,        # number / breadth of unique chatters
    "volume": 0.10,       # absolute size compared with the stream's biggest moment
    "reactions": 0.15,    # share of messages that are reactions (LOL, WTF, Pog …)
    "sustain": 0.10,      # the reaction lasts longer than a single interval
}


@dataclass
class Highlight:
    id: str
    rank: int
    peak_time: float            # start of the busiest interval (seconds into the VOD)
    reaction_start: float       # where chat starts to take off
    reaction_end: float         # where chat is back to normal
    clip_start: float
    clip_end: float
    score: int
    kind: str                   # funny / hype / shock / fail / general
    messages: int               # chat messages during the reaction
    peak_messages: int          # messages in the busiest interval
    baseline: float             # normal messages per interval at this point
    increase_pct: float         # increase of the peak interval over normal
    unique_users: int           # distinct chatters during the reaction
    z_score: float
    reaction_share: float       # 0..1 share of messages containing a reaction
    components: dict = field(default_factory=dict)
    top_reactions: list = field(default_factory=list)       # [[text, count], ...]
    top_words: list = field(default_factory=list)
    notable_messages: list = field(default_factory=list)    # [{time, user, text}, ...]
    repeated_messages: list = field(default_factory=list)   # [[text, users], ...]
    titles: list = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.clip_end - self.clip_start

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Highlight":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class AnalysisResult:
    bins: pd.DataFrame
    highlights: list[Highlight]
    stats: dict
    settings: AnalysisSettings


# --------------------------------------------------------------------------
# 1. Cleaning
# --------------------------------------------------------------------------
def flag_bots(df: pd.DataFrame) -> np.ndarray:
    """Boolean mask of messages that should not count as chat activity."""
    names = df["name"].astype(str).str.lower()
    body = df["body"].astype(str)
    bot = names.isin(KNOWN_BOTS).to_numpy() | body.str.startswith("!").to_numpy()

    # Accounts that mostly repeat one long message (ads, timers, copy-paste bots).
    counts = df["user"].value_counts()
    heavy = counts[counts >= 15].index
    if len(heavy):
        sub = df.loc[df["user"].isin(heavy) & ~bot, ["user", "body"]]
        if len(sub):
            norm = sub["body"].str.lower().str.replace(r"\s+", " ", regex=True)
            grouped = sub.assign(norm=norm).groupby(["user", "norm"], sort=False).size().rename("n").reset_index()
            top = grouped.loc[grouped.groupby("user")["n"].idxmax()]
            totals = sub["user"].value_counts()
            share = top["n"].to_numpy() / totals.reindex(top["user"]).to_numpy()
            long_msg = top["norm"].str.len().to_numpy() >= 20
            spammers = top["user"].to_numpy()[(share >= 0.5) & long_msg]
            if len(spammers):
                log.info("Ignorerer %s konti med gentagne beskeder (bot-lignende)", len(spammers))
                bot |= df["user"].isin(spammers).to_numpy()
    return bot


@lru_cache(maxsize=300_000)
def _reaction_mask(body: str) -> int:
    mask = 0
    for _display, cat in message_reactions(body):
        mask |= 1 << CATEGORIES.index(cat)
    return mask


def annotate_messages(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``bot`` (bool) and ``react`` (category bitmask) columns."""
    out = df.copy()
    out["bot"] = flag_bots(out)
    out["react"] = np.fromiter((_reaction_mask(b) for b in out["body"]), dtype=np.int8, count=len(out))
    return out


# --------------------------------------------------------------------------
# 2 + 3. Binning and baseline
# --------------------------------------------------------------------------
def bin_activity(df: pd.DataFrame, duration: float, settings: AnalysisSettings) -> pd.DataFrame:
    """Per-interval activity table. ``df`` must come from :func:`annotate_messages`."""
    size = settings.bin_seconds
    valid = df.loc[~df["bot"]]
    max_offset = float(df["offset"].max()) if len(df) else 0.0
    n_bins = max(1, int(math.ceil(max(duration, max_offset + 0.001) / size)))
    idx = np.minimum((valid["offset"].to_numpy() // size).astype(np.int64), n_bins - 1)

    raw = np.bincount(idx, minlength=n_bins).astype(float)
    user_codes = pd.factorize(valid["user"])[0]
    per_user = pd.DataFrame({"b": idx, "u": user_codes}).groupby(["b", "u"], sort=False).size()
    eff = np.zeros(n_bins)
    unique = np.zeros(n_bins)
    if len(per_user):
        capped = per_user.clip(upper=settings.per_user_cap).groupby(level=0).sum()
        eff[capped.index.to_numpy()] = capped.to_numpy()
        uniq = per_user.groupby(level=0).size()
        unique[uniq.index.to_numpy()] = uniq.to_numpy()

    react = valid["react"].to_numpy()
    bins = pd.DataFrame({"t": np.arange(n_bins) * size, "raw": raw, "eff": eff, "unique": unique})
    bins["react"] = np.bincount(idx, weights=(react != 0), minlength=n_bins)
    for i, cat in enumerate(CATEGORIES):
        bins[cat] = np.bincount(idx, weights=((react >> i) & 1), minlength=n_bins)

    window = max(5, int(round(settings.baseline_window_seconds / size)) | 1)  # odd number of bins
    min_periods = max(3, window // 4)
    series = bins["eff"]
    median = series.rolling(window, center=True, min_periods=min_periods).median()
    median = median.bfill().ffill().fillna(0.0)
    global_median = float(series.median()) if n_bins else 0.0
    floor = max(1.0, 0.25 * global_median)
    baseline = np.maximum(median.to_numpy(), floor)
    deviation = (series - median).abs()
    mad = deviation.rolling(window, center=True, min_periods=min_periods).median().bfill().ffill().fillna(0.0)
    spread = np.maximum.reduce([1.4826 * mad.to_numpy(), np.sqrt(baseline), np.ones(n_bins)])

    bins["baseline"] = baseline
    bins["spread"] = spread
    bins["z"] = (eff - baseline) / spread
    bins["ratio"] = eff / baseline
    return bins


# --------------------------------------------------------------------------
# 4. Event detection
# --------------------------------------------------------------------------
def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive (start, end) index pairs of consecutive True values."""
    if not mask.any():
        return []
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(a), int(b) - 1) for a, b in zip(edges[::2], edges[1::2])]


def _clip_window(peak: int, run: tuple[int, int], duration: float, s: AnalysisSettings) -> tuple[float, float, float, float]:
    """Return (reaction_start, reaction_end, clip_start, clip_end) in seconds."""
    size = s.bin_seconds
    lead_bins = max(0, s.max_onset_lead_seconds // size)
    onset = max(run[0], peak - lead_bins)
    reaction_start = onset * size
    peak_end = (peak + 1) * size
    clip_start = max(0.0, reaction_start - s.pre_roll)
    reaction_end = (run[1] + 1) * size
    clip_end = reaction_end + s.post_roll
    if clip_end - clip_start > s.max_clip_seconds:
        clip_end = clip_start + s.max_clip_seconds
        if clip_end < peak_end:  # very short max length: keep the peak inside the clip
            clip_end = peak_end
            clip_start = max(0.0, clip_end - s.max_clip_seconds)
    if clip_end - clip_start < s.min_clip_seconds:
        clip_end = clip_start + s.min_clip_seconds
    if duration > 0 and clip_end > duration:
        clip_end = float(duration)
        clip_start = max(0.0, min(clip_start, clip_end - s.min_clip_seconds))
    reaction_end = max(peak_end, min(reaction_end, clip_end))
    return float(reaction_start), float(reaction_end), float(clip_start), float(clip_end)


def _find_events(bins: pd.DataFrame, duration: float, s: AnalysisSettings) -> list[dict]:
    eff = bins["eff"].to_numpy()
    z = bins["z"].to_numpy()
    ratio = bins["ratio"].to_numpy()
    unique = bins["unique"].to_numpy()
    baseline = bins["baseline"].to_numpy()
    size = s.bin_seconds

    hot = (z >= s.z_threshold) & (ratio >= s.ratio_threshold) & (eff >= MIN_PEAK_MESSAGES) & (unique >= MIN_PEAK_USERS)
    warm = hot | ((z >= max(1.5, 0.4 * s.z_threshold)) & (eff > baseline))
    # Close single-interval dips inside a reaction.
    gap = ~warm
    gap[1:-1] &= warm[:-2] & warm[2:]
    if len(gap) >= 2:
        gap[0] = gap[-1] = False
    warm = warm | gap

    events: list[dict] = []
    pending = [run for run in _runs(warm) if hot[run[0]: run[1] + 1].any()]
    while pending:
        a, b = pending.pop()
        hot_idx = np.flatnonzero(hot[a: b + 1]) + a
        if not len(hot_idx):
            continue
        peak = int(hot_idx[np.argmax(eff[hot_idx])])
        r_start, r_end, c_start, c_end = _clip_window(peak, (a, b), duration, s)
        first = int(r_start // size)
        last = max(peak, int(math.ceil(r_end / size)) - 1)
        events.append({"peak": peak, "first": first, "last": last, "reaction_start": r_start,
                       "reaction_end": r_end, "clip_start": c_start, "clip_end": c_end})
        # A long run can hold several separate moments: look at what is left.
        before_end = int(c_start // size) - 1
        after_start = int(math.ceil(c_end / size))
        if before_end >= a:
            pending.append((a, before_end))
        if after_start <= b:
            pending.append((after_start, b))
    return events


# --------------------------------------------------------------------------
# 5. Scoring
# --------------------------------------------------------------------------
def _score(event: dict, bins: pd.DataFrame, ref: dict) -> tuple[int, dict]:
    row = bins.iloc[event["peak"]]
    z = max(0.0, float(row["z"]))
    ratio = max(1.0, float(row["ratio"]))
    n_bins = event["last"] - event["first"] + 1
    intensity = 1.0 - math.exp(-z / 6.0)
    increase = min(1.0, math.log2(ratio) / 3.0)                      # 8x normal -> 1.0
    user_jump = min(1.0, math.log2(max(1.0, float(row["unique"]) / ref["unique_typical"])) / 3.0)
    breadth = min(1.0, float(row["unique"]) / ref["unique_max"])
    users = 0.5 * user_jump + 0.5 * math.sqrt(breadth)
    volume = math.sqrt(min(1.0, float(row["eff"]) / ref["eff_max"]))
    reactions = min(1.0, event["reaction_share"] / 0.4)              # 40% reactions -> 1.0
    sustain = min(1.0, (n_bins - 1) / 3.0)                           # 40 s reaction -> 1.0
    components = {
        "intensity": intensity, "increase": increase, "users": users,
        "volume": volume, "reactions": reactions, "sustain": sustain,
    }
    total = sum(SCORE_WEIGHTS[k] * v for k, v in components.items())
    # A burst carried by very few people (heavy repetition) is worth less.
    diversity = event["unique_users"] / max(1, event["messages"])
    if diversity < 0.35:
        total *= 0.6 + diversity / 0.35 * 0.4
    score = int(round(100 * min(1.0, max(0.0, total))))
    return score, {k: round(v, 3) for k, v in components.items()}


def _classify(cat_counts: dict[str, float], messages: int) -> str:
    if not messages:
        return GENERAL
    best = max(cat_counts, key=cat_counts.get)
    if cat_counts[best] / messages < 0.12 or cat_counts[best] < 3:
        return GENERAL
    return best


# --------------------------------------------------------------------------
# 6. Overlap handling
# --------------------------------------------------------------------------
def _resolve_overlaps(events: list[dict], s: AnalysisSettings) -> list[dict]:
    """Greedy by score: merge windows that fit in one clip, trim or drop the rest."""
    accepted: list[dict] = []
    for ev in sorted(events, key=lambda e: (-e["score"], e["peak"])):
        ok = True
        for other in accepted:
            gap = s.min_gap_seconds
            if ev["clip_start"] >= other["clip_end"] + gap or ev["clip_end"] <= other["clip_start"] - gap:
                continue
            union_start = min(ev["clip_start"], other["clip_start"])
            union_end = max(ev["clip_end"], other["clip_end"])
            if union_end - union_start <= s.max_clip_seconds:
                other["clip_start"], other["clip_end"] = union_start, union_end
                other["reaction_start"] = min(other["reaction_start"], ev["reaction_start"])
                other["reaction_end"] = max(other["reaction_end"], ev["reaction_end"])
                other["merged"] = other.get("merged", 0) + 1
                ok = False
                break
            # The weaker moment comes right after a stronger one: start it later
            # as long as a few seconds of lead-in remain.
            new_start = other["clip_end"] + gap
            if ev["clip_start"] > other["clip_start"] and new_start <= ev["reaction_start"] - min(5, s.pre_roll):
                ev["clip_start"] = new_start
                if ev["clip_end"] - ev["clip_start"] < s.min_clip_seconds:
                    ev["clip_end"] = ev["clip_start"] + s.min_clip_seconds
                continue
            ok = False
            break
        if ok:
            # Trimming may have pushed the window into another accepted clip.
            if any(ev["clip_start"] < o["clip_end"] and ev["clip_end"] > o["clip_start"] for o in accepted):
                continue
            accepted.append(ev)
    return accepted


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def analyze(messages: pd.DataFrame, duration: float, settings: AnalysisSettings | None = None) -> AnalysisResult:
    """Find highlights in a chat. ``messages`` needs columns offset/user/name/body."""
    s = (settings or AnalysisSettings()).clamped()
    df = messages if {"bot", "react"} <= set(messages.columns) else annotate_messages(messages)
    bins = bin_activity(df, duration, s)
    valid = df.loc[~df["bot"]]
    duration = float(max(duration, bins["t"].iloc[-1] + s.bin_seconds if len(bins) else 0))

    stats = {
        "total_messages": int(len(df)),
        "counted_messages": int(len(valid)),
        "ignored_messages": int(df["bot"].sum()),
        "unique_chatters": int(valid["user"].nunique()),
        "duration": duration,
        "bin_seconds": s.bin_seconds,
        "median_per_bin": float(bins["eff"].median()),
        "max_per_bin": float(bins["raw"].max()),
        "messages_per_minute": float(len(valid) / max(1.0, duration / 60)),
    }
    if len(valid) < MIN_TOTAL_MESSAGES or len(bins) < 6:
        stats["note"] = "For få chatbeskeder til at finde highlights."
        return AnalysisResult(bins, [], stats, s)

    ref = {
        "eff_max": max(1.0, float(bins["eff"].max())),
        "unique_max": max(1.0, float(bins["unique"].max())),
        "unique_typical": max(1.0, float(bins["unique"].median())),
    }
    offsets = valid["offset"].to_numpy()
    react = valid["react"].to_numpy()
    users = valid["user"].to_numpy()

    events = _find_events(bins, duration, s)
    for ev in events:
        lo = np.searchsorted(offsets, ev["reaction_start"], side="left")
        hi = np.searchsorted(offsets, ev["reaction_end"], side="left")
        ev_react = react[lo:hi]
        ev["messages"] = int(hi - lo)
        ev["unique_users"] = int(len(set(users[lo:hi])))
        ev["reaction_share"] = float((ev_react != 0).mean()) if hi > lo else 0.0
        cat_counts = {cat: float(((ev_react >> i) & 1).sum()) for i, cat in enumerate(CATEGORIES)}
        ev["kind"] = _classify(cat_counts, ev["messages"])
        ev["score"], ev["components"] = _score(ev, bins, ref)

    selected = _resolve_overlaps(events, s)
    selected = sorted(selected, key=lambda e: (-e["score"], e["peak"]))[: s.max_highlights]

    highlights: list[Highlight] = []
    for rank, ev in enumerate(selected, start=1):
        row = bins.iloc[ev["peak"]]
        highlights.append(
            Highlight(
                id=f"t{int(row['t'])}",
                rank=rank,
                peak_time=float(row["t"]),
                reaction_start=ev["reaction_start"],
                reaction_end=ev["reaction_end"],
                clip_start=ev["clip_start"],
                clip_end=ev["clip_end"],
                score=ev["score"],
                kind=ev["kind"],
                messages=ev["messages"],
                peak_messages=int(row["raw"]),
                baseline=round(float(row["baseline"]), 1),
                increase_pct=round((float(row["ratio"]) - 1.0) * 100.0, 0),
                unique_users=ev["unique_users"],
                z_score=round(float(row["z"]), 1),
                reaction_share=round(ev["reaction_share"], 3),
                components=ev["components"],
            )
        )
    stats["candidates"] = len(events)
    log.info(
        "Analyse: %s beskeder (%s ignoreret), %s intervaller, %s kandidater -> %s highlights",
        stats["total_messages"], stats["ignored_messages"], len(bins), len(events), len(highlights),
    )
    return AnalysisResult(bins, highlights, stats, s)
