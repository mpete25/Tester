"""Drives the Streamlit dashboard headlessly (no browser) against the fake CLI."""
import shutil

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from tests.conftest import ROOT  # noqa: E402


def new_app():
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)


def button(at, label):
    return next(b for b in at.button if label in b.label)


def test_start_page_shows_import_form_and_no_errors():
    at = new_app().run()
    assert not at.exception
    assert at.title[0].value == "🎬 Twitch Highlight Finder"
    assert [b.label for b in at.button if b.label == "Analyze Stream"]
    assert "Analyze Stream" in at.info[0].value


def test_invalid_link_gives_a_clear_message():
    at = new_app().run()
    at.text_area[0].set_value("https://www.youtube.com/watch?v=abc")
    button(at, "Analyze Stream").click().run()
    assert not at.exception
    assert "ikke et Twitch-link" in at.error[0].value


def test_missing_vod_is_reported_and_other_links_still_run():
    at = new_app().run()
    at.text_area[0].set_value("https://www.twitch.tv/videos/4040000001\nhttps://www.twitch.tv/videos/2000000042")
    button(at, "Analyze Stream").click().run()
    assert not at.exception
    assert any("ikke længere tilgængelig" in e.value for e in at.error)
    labels = [m.label for m in at.metric]
    assert {"Varighed", "Chatbeskeder", "Unikke chattere", "Highlights"} <= set(labels)
    assert int(next(m for m in at.metric if m.label == "Highlights").value) >= 8
    assert at.dataframe, "highlight table must be shown"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_analyze_adjust_and_export_from_the_ui(isolated_dirs):
    at = new_app().run()
    at.text_area[0].set_value("https://www.twitch.tv/videos/2000000042")
    button(at, "Analyze Stream").click().run()
    assert not at.exception and not at.error

    # Sensitivity is adjustable and changes the result without re-downloading.
    before = int(next(m for m in at.metric if m.label == "Highlights").value)
    next(s for s in at.slider if s.label == "Følsomhed").set_value(1).run()
    after = int(next(m for m in at.metric if m.label == "Highlights").value)
    assert after <= before
    next(s for s in at.slider if s.label == "Følsomhed").set_value(5).run()

    # Adjust the selected highlight to a short custom range and export it.
    start = next(t for t in at.text_input if t.label.startswith("Start"))
    end = next(t for t in at.text_input if t.label.startswith("Slut"))
    start.set_value("0:10:00")
    end.set_value("0:10:06")
    at.run()
    assert any("6s" in c.value and "justeret" in c.value for c in at.caption)
    button(at, "Eksportér klip").click().run()
    assert not at.exception and not at.error
    clips = list((isolated_dirs / "clips").rglob("*.mp4"))
    assert len(clips) == 1 and clips[0].name.startswith("00h10m00s_score") and clips[0].name.endswith("_16x9.mp4")
    assert any("Eksporterede klip (1)" in s.value for s in at.subheader)

    # Invalid times block the export with an explanation.
    next(t for t in at.text_input if t.label.startswith("Slut")).set_value("0:09:00")
    at.run()
    assert any("efter starttidspunktet" in e.value for e in at.error)
    assert button(at, "Eksportér klip").disabled


def test_demo_data_works_without_any_tools(monkeypatch):
    monkeypatch.setenv("THF_TWITCHDOWNLOADER_CLI", "/nonexistent/TwitchDownloaderCLI")
    at = new_app().run()
    button(at, "Prøv demo-data").click().run()
    assert not at.exception
    assert int(next(m for m in at.metric if m.label == "Highlights").value) == 11
    assert button(at, "Eksportér klip").disabled
