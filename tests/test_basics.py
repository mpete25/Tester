import pytest

from highlight_finder import reactions, timeutil, vod
from highlight_finder.config import AnalysisSettings
from highlight_finder.errors import InvalidVodUrlError


@pytest.mark.parametrize("text,expected", [
    ("https://www.twitch.tv/videos/2345678901", "2345678901"),
    ("https://twitch.tv/videos/2345678901?t=1h2m3s", "2345678901"),
    ("twitch.tv/videos/2345678901/", "2345678901"),
    ("https://m.twitch.tv/videos/2345678901", "2345678901"),
    ("https://www.twitch.tv/somechannel/v/2345678901", "2345678901"),
    ("https://www.twitch.tv/somechannel/video/2345678901?filter=archives", "2345678901"),
    ("  2345678901  ", "2345678901"),
])
def test_parse_vod_id_accepts_vod_links(text, expected):
    assert vod.parse_vod_id(text) == expected


@pytest.mark.parametrize("text", [
    "", "hello", "https://www.youtube.com/watch?v=abc", "https://www.twitch.tv/somechannel",
    "https://clips.twitch.tv/FunnyClipName", "https://www.twitch.tv/somechannel/clip/FunnyClipName",
    "https://www.twitch.tv/videos/abc", "123",
])
def test_parse_vod_id_rejects_everything_else(text):
    with pytest.raises(InvalidVodUrlError):
        vod.parse_vod_id(text)


def test_parse_vod_list_dedupes_and_reports_errors():
    ids, errors = vod.parse_vod_list(
        "https://www.twitch.tv/videos/1111111111\nnot-a-link, 2222222222\nhttps://www.twitch.tv/videos/1111111111"
    )
    assert ids == ["1111111111", "2222222222"]
    assert [bad for bad, _msg in errors] == ["not-a-link"]


def test_time_roundtrip_and_formats():
    assert timeutil.format_time(3723.4) == "1:02:03"
    assert timeutil.parse_time("1:02:03") == 3723
    assert timeutil.parse_time("02:03") == 123
    assert timeutil.parse_time("1h2m3s") == 3723
    assert timeutil.parse_time("90") == 90
    assert timeutil.parse_time("0:00:10,5") == 10.5
    assert timeutil.twitch_timestamp(3723) == "1h02m03s"
    assert vod.vod_url("123456789", 3723).endswith("/videos/123456789?t=1h02m03s")
    for bad in ("", "abc", "1:99:00", "12:75"):
        with pytest.raises(ValueError):
            timeutil.parse_time(bad)


def test_settings_are_clamped_and_sensitivity_lowers_thresholds():
    s = AnalysisSettings(sensitivity=99, min_clip_seconds=60, max_clip_seconds=10, pre_roll=-5).clamped()
    assert s.sensitivity == 10 and s.max_clip_seconds == 60 and s.pre_roll == 0
    low, high = AnalysisSettings(sensitivity=1), AnalysisSettings(sensitivity=10)
    assert low.z_threshold > high.z_threshold and low.ratio_threshold > high.ratio_threshold
    assert AnalysisSettings().fingerprint() != high.fingerprint()


@pytest.mark.parametrize("body,expected", [
    ("LMAOOO that was insane", {"LMAO": "funny"}),
    ("lol", {"LOL": "funny"}),
    ("KEKW KEKW KEKW", {"KEKW": "funny"}),
    ("WTF", {"WTF": "shock"}),
    ("omgggg", {"OMG": "shock"}),
    ("?????", {"???": "shock"}),
    ("NO WAY", {"NO WAY": "shock"}),
    ("Pogggg LETSGOOO", {"Pog": "hype", "LETSGO": "hype"}),
    ("W", {"W": "hype"}),
    ("F", {"F": "fail"}),
    ("what game is this", {}),                 # ordinary sentence, not a reaction
    ("we won the last one", {}),               # lower-case 'w' words are not 'W'
    ("WHAT", {"WHAT": "shock"}),
])
def test_reaction_lexicon(body, expected):
    assert dict(reactions.message_reactions(body)) == expected
