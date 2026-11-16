import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAKE_CLI = ROOT / "tests" / "fake_cli" / "TwitchDownloaderCLI"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Every test gets its own data / clips / log folders and the fake CLI."""
    monkeypatch.setenv("THF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("THF_CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("THF_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("THF_TWITCHDOWNLOADER_CLI", str(FAKE_CLI))
    monkeypatch.setenv("FAKE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FAKE_DURATION", "3600")
    monkeypatch.delenv("THF_TWITCH_OAUTH", raising=False)
    return tmp_path


@pytest.fixture(scope="session")
def six_hour_chat(tmp_path_factory):
    """A realistic six hour chat file plus the planted ground-truth events."""
    from highlight_finder import chat_parser, sample_data

    path = tmp_path_factory.mktemp("chat") / "chat.json"
    events = sample_data.write_chat_json(path, duration=6 * 3600)
    return chat_parser.parse_chat_file(path), events, path
