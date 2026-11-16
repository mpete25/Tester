"""CLI wrapper, caching, clip export and the complete link -> clips workflow.

TwitchDownloaderCLI is replaced by tests/fake_cli (same arguments and output
format); FFmpeg is the real thing.
"""
import json
import shutil
import subprocess

import pytest

from highlight_finder import cache, clipper, pipeline, twitch_cli
from highlight_finder.config import AnalysisSettings
from highlight_finder.errors import (
    ClipExportError, InvalidVodUrlError, NetworkError, NoChatDataError, ToolNotFoundError, VodUnavailableError,
)
from highlight_finder.vod import VodInfo

VOD = "https://www.twitch.tv/videos/2000000042"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height:format=duration",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    audio = [s for s in data["streams"] if s["codec_type"] == "audio"]
    return video, audio, float(data["format"]["duration"])


def test_vod_info_is_parsed_from_raw_cli_output():
    info = twitch_cli.fetch_vod_info("2000000042")
    assert (info.streamer, info.streamer_login, info.game) == ("TestStreamer", "teststreamer", "Test Game")
    assert info.title == "Test stream 2000000042" and info.length_seconds == 3600
    assert info.qualities == ["1080p60", "720p60"]
    assert info.chapters == [{"start": 0.0, "length": 3600.0, "name": "Test Game"}]
    assert twitch_cli.cli_version() == "1.56.5"


def test_full_analysis_reports_progress_and_caches(isolated_dirs):
    stages = []
    first = pipeline.analyze_vod(VOD, progress=lambda stage, frac, msg: stages.append((stage, frac, msg)))
    assert not first.from_cache and first.info.streamer == "TestStreamer"
    assert len(first.result.highlights) >= 8
    assert [s for s, _f, _m in stages if s != "chat"][:2] == ["info", "info"]
    chat_fracs = [f for s, f, _m in stages if s == "chat"]
    assert chat_fracs[0] == 0.0 and max(chat_fracs) == 1.0, "chat download progress must reach 100%"
    assert {"info", "chat", "parse", "analyze"} <= {s for s, _f, _m in stages}

    folder = cache.vod_dir("2000000042")
    assert sorted(p.name for p in folder.iterdir()) == ["highlights.csv", "highlights.json", "info.json", "messages.parquet"]
    saved = json.loads((folder / "highlights.json").read_text())
    assert len(saved["highlights"]) == len(first.result.highlights) and saved["highlights"][0]["titles"]

    # Second run must not call the CLI at all.
    import os
    os.environ["THF_TWITCHDOWNLOADER_CLI"] = "/nonexistent/TwitchDownloaderCLI"
    second = pipeline.analyze_vod("2000000042", settings=AnalysisSettings(sensitivity=8))
    assert second.from_cache and len(second.messages) == len(first.messages)
    assert [v.vod_id for v in cache.list_cached_vods()] == ["2000000042"]
    cache.delete_vod("2000000042")
    assert cache.list_cached_vods() == []


def test_several_vods_in_one_session():
    a = pipeline.analyze_vod("2000000001")
    b = pipeline.analyze_vod("2000000002")
    assert a.info.vod_id != b.info.vod_id
    assert {v.vod_id for v in cache.list_cached_vods()} == {"2000000001", "2000000002"}
    assert [h.peak_time for h in a.result.highlights] != [h.peak_time for h in b.result.highlights]


def test_error_handling_for_bad_links_missing_vods_and_empty_chat(monkeypatch):
    with pytest.raises(InvalidVodUrlError):
        pipeline.analyze_vod("https://www.twitch.tv/somechannel")
    with pytest.raises(VodUnavailableError):
        pipeline.analyze_vod("4040000001")
    with pytest.raises(NoChatDataError):
        pipeline.analyze_vod("7770000001")
    assert not cache.has_messages("7770000001") and not cache.raw_chat_path("7770000001").exists()
    monkeypatch.setenv("THF_TWITCHDOWNLOADER_CLI", "/nonexistent/TwitchDownloaderCLI")
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.setattr("highlight_finder.config.EXTRA_BIN_DIRS", ())
    monkeypatch.setattr("highlight_finder.config.bin_dir", lambda: __import__("pathlib").Path("/nonexistent"))
    with pytest.raises(ToolNotFoundError) as exc:
        pipeline.analyze_vod("2000000042")
    assert "setup_macos.sh" in exc.value.hint


def test_network_errors_are_retried_then_reported(monkeypatch, isolated_dirs):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(NetworkError):                      # info fails -> clear error
        pipeline.analyze_vod("5000000001")
    monkeypatch.setenv("FAKE_NET_FAILS", "2")              # chat download fails twice, then works
    out = isolated_dirs / "chat.json"
    twitch_cli.download_chat("5000000002", out, retries=2)
    assert out.stat().st_size > 1000
    monkeypatch.setenv("FAKE_NET_FAILS", "99")
    with pytest.raises(NetworkError):
        twitch_cli.download_chat("5000000003", isolated_dirs / "x.json", retries=1)


def test_demo_mode_needs_no_cli(monkeypatch):
    monkeypatch.setenv("THF_TWITCHDOWNLOADER_CLI", "/nonexistent/TwitchDownloaderCLI")
    demo = pipeline.load_demo(duration=3600)
    assert demo.info.is_demo and len(demo.result.highlights) >= 8
    with pytest.raises(ClipExportError):
        clipper.export_clip(demo.info, 10, 40)


@needs_ffmpeg
def test_export_wide_and_vertical_clip(isolated_dirs):
    vod = VodInfo(vod_id="2000000042", title="Finale: æøå / test!", streamer="Test Streamer", length_seconds=3600)
    seen = []
    clips = clipper.export_clip(vod, 100.4, 112.2, title="Sjovt øjeblik: KEKW?!", formats=("16:9", "9:16"),
                                score=87, progress=lambda f, m: seen.append(f))
    assert [c.format for c in clips] == ["16:9", "9:16"]
    wide, tall = clips
    assert wide.path.parent == isolated_dirs / "clips" / "Test-Streamer" / "2000000042_Finale-æøå-test"
    assert wide.path.name == "00h01m40s_score87_Sjovt-øjeblik-KEKW_16x9.mp4"
    assert tall.path.name == "00h01m40s_score87_Sjovt-øjeblik-KEKW_9x16.mp4"

    v, a, dur = probe(wide.path)            # 100 -> 113 s = 13 s, untouched download
    assert (v["width"], v["height"]) == (1280, 720) and len(a) == 1 and abs(dur - 13) < 0.5
    v, a, dur = probe(tall.path)
    assert (v["width"], v["height"]) == (1080, 1920) and v["codec_name"] == "h264"
    assert len(a) == 1 and a[0]["codec_name"] == "aac" and abs(dur - 13) < 0.5

    index = (wide.path.parent / "clips.csv").read_text(encoding="utf-8").splitlines()
    assert len(index) == 3 and "Sjovt øjeblik: KEKW?!" in index[1] and "0:01:40" in index[1]
    assert seen and seen[-1] == 1.0 and seen == sorted(seen)
    assert list((isolated_dirs / "data" / "tmp").glob("*.mp4")) == [], "temporary download must be removed"


@needs_ffmpeg
def test_vertical_blur_mode_and_export_errors(isolated_dirs):
    vod = VodInfo(vod_id="2000000042", title="T", streamer="S", length_seconds=3600)
    (clip,) = clipper.export_clip(vod, 3590, 3700, formats=("9:16",), vertical_mode="blur")
    v, _a, dur = probe(clip.path)
    assert (v["width"], v["height"]) == (1080, 1920) and abs(dur - 10) < 0.5   # clamped to VOD length
    assert not list(clip.path.parent.glob("*16x9*"))
    with pytest.raises(ClipExportError):
        clipper.export_clip(vod, 50, 50)
    with pytest.raises(ClipExportError):
        clipper.export_clip(vod, 0, 3000)
    with pytest.raises(VodUnavailableError) as exc:
        clipper.export_clip(VodInfo(vod_id="4030000001", title="T", streamer="S"), 10, 20)
    assert "THF_TWITCH_OAUTH" in exc.value.hint


@needs_ffmpeg
def test_complete_workflow_link_to_exported_clips():
    """Paste link -> analyse -> pick the two best highlights -> export both formats."""
    analysed = pipeline.analyze_vod(VOD, settings=AnalysisSettings(min_clip_seconds=15, max_clip_seconds=25))
    best = sorted(analysed.result.highlights, key=lambda h: h.rank)[:2]
    exported = []
    for h in best:
        exported += clipper.export_clip(analysed.info, h.clip_start, h.clip_end, title=h.titles[0],
                                        formats=("16:9", "9:16"), quality="720p60", score=h.score)
    assert len(exported) == 4 and all(c.path.stat().st_size > 10_000 for c in exported)
    for h, clip in zip(best, exported[::2]):
        _v, _a, dur = probe(clip.path)
        assert abs(dur - h.duration) < 1.0
    assert len({c.path for c in exported}) == 4


def test_optional_ai_titles_fail_soft_and_work_with_a_local_server(monkeypatch):
    """Ollama is optional: no server -> None; a (mock) local server -> its title."""
    import http.server
    import threading

    from highlight_finder import ai

    highlight = pipeline.load_demo(duration=3600).result.highlights[0]
    info = VodInfo(vod_id="1", streamer="DemoStreamer", game="Demo Game")
    monkeypatch.setenv("THF_OLLAMA_URL", "http://127.0.0.1:9")          # nothing listens here
    assert ai.available_models() == [] and ai.generate_title(highlight, info, timeout=2) is None

    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"models": [{"name": "llama3.2:latest"}]})

        def do_POST(self):
            seen["request"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self._send({"response": '"Chatten eksploderer over vildt clutch"\nekstra linje'})

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("THF_OLLAMA_URL", f"http://127.0.0.1:{server.server_port}")
        assert ai.available_models() == ["llama3.2:latest"]
        assert ai.generate_title(highlight, info, "llama3.2:latest") == "Chatten eksploderer over vildt clutch"
        assert seen["request"]["stream"] is False and "DemoStreamer" in seen["request"]["prompt"]
    finally:
        server.shutdown()
