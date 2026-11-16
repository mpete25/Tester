"""The analysis must find the planted moments and nothing else."""
import time

import numpy as np
import pandas as pd
import pytest

from highlight_finder import analysis, chat_parser, insights, sample_data
from highlight_finder.config import AnalysisSettings
from highlight_finder.errors import HighlightFinderError, NoChatDataError
from highlight_finder.vod import VodInfo

DURATION = 6 * 3600


@pytest.fixture(scope="module")
def result(six_hour_chat):
    df, events, _path = six_hour_chat
    return analysis.analyze(df, DURATION, AnalysisSettings()), events, analysis.annotate_messages(df)


def _match(highlights, event, tolerance=25):
    return [h for h in highlights if event.time - 5 <= h.peak_time <= event.time + tolerance]


def test_chat_parser_reads_header_and_messages(six_hour_chat):
    df, _events, path = six_hour_chat
    header = chat_parser.read_chat_header(path)
    assert header["video"]["length"] == DURATION and header["streamer"]["name"] == "DemoStreamer"
    assert list(df.columns) == chat_parser.MESSAGE_COLUMNS
    assert len(df) > 30_000 and df["offset"].is_monotonic_increasing
    assert df["offset"].between(0, DURATION).all()


def test_every_planted_moment_is_found_with_the_right_type(result):
    res, events, _ = result
    for event in events:
        hits = _match(res.highlights, event)
        assert len(hits) == 1, f"{event} matched {len(hits)} highlights"
        assert hits[0].kind == event.kind


def test_no_false_highlights(result):
    res, events, _ = result
    assert len(res.highlights) == len(events)


def test_single_user_flood_bots_and_commands_do_not_create_highlights(result):
    res, _events, annotated = result
    spam_at, cmd_at = DURATION * 0.36, DURATION * 0.48
    for h in res.highlights:
        assert not (h.clip_start <= spam_at <= h.clip_end), "one user's flood became a highlight"
        assert not (h.clip_start <= cmd_at <= h.clip_end), "!command spam became a highlight"
    bots = annotated.loc[annotated["bot"], "name"].str.lower().unique()
    assert {"nightbot", "promo_guy"} <= set(bots)
    assert annotated.loc[annotated["body"].str.startswith("!"), "bot"].all()
    # The flood is still there as raw messages but capped per user in 'eff'.
    rows = res.bins.iloc[int(spam_at // 10): int(spam_at // 10) + 2]
    assert rows["raw"].sum() > 110 and rows["eff"].sum() < rows["raw"].sum() / 2


def test_generally_busy_stretch_is_not_a_peak(result):
    res, events, _ = result
    busy = res.bins[(res.bins["t"] > DURATION * 0.56) & (res.bins["t"] < DURATION * 0.69)]
    calm = res.bins[(res.bins["t"] > DURATION * 0.75) & (res.bins["t"] < DURATION * 0.85)]
    assert busy["eff"].median() > 1.4 * calm["eff"].median()        # it really is busier …
    assert busy["baseline"].median() > 1.4 * calm["baseline"].median()  # … and the baseline follows
    assert (busy["z"] < res.settings.z_threshold).mean() > 0.97     # … so being busy is not "a peak"
    planted = {e.time for e in events}
    inside = [h for h in res.highlights if DURATION * 0.56 < h.peak_time < DURATION * 0.69]
    assert all(any(abs(h.peak_time - t) < 40 for t in planted) for h in inside)


def test_scores_are_0_to_100_and_follow_strength(result):
    res, events, _ = result
    assert all(0 <= h.score <= 100 for h in res.highlights)
    assert [h.rank for h in sorted(res.highlights, key=lambda h: -h.score)] == sorted(h.rank for h in res.highlights)
    strongest = max(events, key=lambda e: e.strength)
    weakest = min(events, key=lambda e: e.strength)
    assert _match(res.highlights, strongest)[0].rank <= 2
    assert _match(res.highlights, strongest)[0].score > _match(res.highlights, weakest)[0].score + 15


def test_clip_starts_before_the_moment_and_respects_lengths(result):
    res, events, _ = result
    s = res.settings
    for event in events:
        h = _match(res.highlights, event)[0]
        assert h.clip_start <= event.time - 5, "clip must start before the moment itself"
        assert h.clip_start <= h.reaction_start <= h.peak_time < h.clip_end
        assert h.reaction_start - h.clip_start <= s.pre_roll
        assert s.min_clip_seconds <= h.duration <= s.max_clip_seconds
        assert 0 <= h.clip_start and h.clip_end <= DURATION


def test_clips_never_overlap(result):
    res, _events, _ = result
    ordered = sorted(res.highlights, key=lambda h: h.clip_start)
    for a, b in zip(ordered, ordered[1:]):
        assert a.clip_end <= b.clip_start, f"{a.id} overlaps {b.id}"


def test_highlight_numbers_are_consistent(result):
    res, _events, _ = result
    for h in res.highlights:
        assert h.messages >= h.peak_messages * 0.5 and h.unique_users <= h.messages
        assert h.increase_pct > 50 and h.baseline >= 1 and h.z_score >= res.settings.z_threshold


def test_sensitivity_and_limits_are_adjustable(six_hour_chat):
    df, _events, _path = six_hour_chat
    annotated = analysis.annotate_messages(df)
    strict = analysis.analyze(annotated, DURATION, AnalysisSettings(sensitivity=1))
    loose = analysis.analyze(annotated, DURATION, AnalysisSettings(sensitivity=10, max_highlights=100))
    assert len(strict.highlights) <= 11 <= len(loose.highlights)
    top3 = analysis.analyze(annotated, DURATION, AnalysisSettings(max_highlights=3))
    assert [h.rank for h in top3.highlights] == [1, 2, 3]
    custom = analysis.analyze(annotated, DURATION, AnalysisSettings(pre_roll=40, min_clip_seconds=45, max_clip_seconds=60))
    assert all(45 <= h.duration <= 60 for h in custom.highlights)
    default = analysis.analyze(annotated, DURATION, AnalysisSettings())
    by_id = {h.id: h for h in default.highlights}
    earlier = [h for h in custom.highlights if h.id in by_id and h.clip_start < by_id[h.id].clip_start]
    assert len(earlier) >= 8, "a longer pre-roll must move clip starts earlier"


def test_insights_reactions_words_messages_and_titles(result):
    res, events, annotated = result
    info = VodInfo(vod_id="1", streamer="DemoStreamer", game="Demo Game")
    insights.enrich_highlights(res.highlights, annotated, info, "da")
    fall = _match(res.highlights, events[0])[0]      # funny: "he fell off the map"
    assert {t for t, _n in fall.top_reactions} & {"KEKW", "LMAO", "LUL", "LOL", "OMEGALUL"}
    assert {"map", "fell"} & {w for w, _n in fall.top_words}
    assert fall.notable_messages and all(len(m["text"]) >= 12 for m in fall.notable_messages)
    assert len({m["user"] for m in fall.notable_messages}) == len(fall.notable_messages)
    assert fall.repeated_messages and fall.repeated_messages[0][1] >= 3
    assert fall.titles and all("{" not in t for t in fall.titles)
    insights.enrich_highlights(res.highlights, annotated, info, "en")
    assert fall.titles[0] == "DemoStreamer has chat crying laughing 😂"


def test_quiet_or_tiny_chats_give_no_highlights_but_do_not_crash():
    rng = np.random.default_rng(1)
    flat = pd.DataFrame({"offset": np.sort(rng.random(4000) * 3600), "user": [f"u{i % 300}" for i in range(4000)],
                         "name": "x", "body": "hello there"})
    assert analysis.analyze(flat, 3600).highlights == []
    tiny = flat.head(10)
    res = analysis.analyze(tiny, 3600)
    assert res.highlights == [] and "note" in res.stats


def test_empty_and_broken_chat_files(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text('{"video": {"length": 10}, "comments": []}')
    with pytest.raises(NoChatDataError):
        chat_parser.parse_chat_file(empty)
    broken = tmp_path / "broken.json"
    broken.write_text('{"video": {"length": 10}, "comments": [{"content_offs')
    with pytest.raises(HighlightFinderError):
        chat_parser.parse_chat_file(broken)


def test_large_chat_is_fast_enough():
    """~half a million messages (a very busy six hour stream) in a few seconds."""
    scale = 20 / 1.5   # a chat this busy also reacts proportionally harder
    events = [sample_data.PlantedEvent(e.time, e.kind, e.strength * scale, e.length) for e in sample_data.default_events(DURATION)]
    rows, _events = sample_data.generate_messages(duration=DURATION, base_rate=20, n_users=30000, events=events)
    df = pd.DataFrame(rows, columns=["offset", "user", "body"])
    df["name"] = df["user"]
    assert len(df) > 400_000
    started = time.perf_counter()
    res = analysis.analyze(df, DURATION)
    elapsed = time.perf_counter() - started
    assert len(res.highlights) == 11
    assert elapsed < 30, f"analysis took {elapsed:.1f}s"
