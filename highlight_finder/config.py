"""Paths and tunable settings.

Everything lives inside the project folder by default; override with the
environment variables ``THF_DATA_DIR``, ``THF_CLIPS_DIR``, ``THF_LOG_DIR``,
``THF_TWITCHDOWNLOADER_CLI`` and ``THF_FFMPEG``.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Places where Homebrew installs binaries; Streamlit started from Finder does
# not always inherit the shell PATH.
EXTRA_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def data_dir() -> Path:
    return _env_path("THF_DATA_DIR", PROJECT_ROOT / "data")


def vods_dir() -> Path:
    return data_dir() / "vods"


def tmp_dir() -> Path:
    return data_dir() / "tmp"


def clips_dir() -> Path:
    return _env_path("THF_CLIPS_DIR", PROJECT_ROOT / "clips")


def log_dir() -> Path:
    return _env_path("THF_LOG_DIR", PROJECT_ROOT / "logs")


def bin_dir() -> Path:
    return PROJECT_ROOT / "bin"


@dataclass(frozen=True)
class AnalysisSettings:
    """All knobs of the highlight algorithm (see analysis.py)."""

    bin_seconds: int = 10            # chat is counted in intervals of this size
    sensitivity: int = 5             # 1 (only huge peaks) .. 10 (many, weaker peaks)
    pre_roll: int = 20               # clip starts this many seconds before the reaction
    post_roll: int = 10              # seconds kept after the reaction has died down
    min_clip_seconds: int = 30
    max_clip_seconds: int = 90
    max_highlights: int = 20
    baseline_window_seconds: int = 600   # window for the "normal activity" level
    per_user_cap: int = 2            # max messages counted per user per interval
    max_onset_lead_seconds: int = 30     # how far before the peak a reaction may start
    min_gap_seconds: int = 5         # minimum gap between two clips

    def clamped(self) -> "AnalysisSettings":
        """Return a copy with every value forced into a sane range."""
        s = asdict(self)
        s["bin_seconds"] = int(min(60, max(5, s["bin_seconds"])))
        s["sensitivity"] = int(min(10, max(1, s["sensitivity"])))
        s["pre_roll"] = int(min(120, max(0, s["pre_roll"])))
        s["post_roll"] = int(min(120, max(0, s["post_roll"])))
        s["min_clip_seconds"] = int(min(300, max(5, s["min_clip_seconds"])))
        s["max_clip_seconds"] = int(min(600, max(s["min_clip_seconds"], s["max_clip_seconds"])))
        s["max_highlights"] = int(min(200, max(1, s["max_highlights"])))
        s["baseline_window_seconds"] = int(max(6 * s["bin_seconds"], s["baseline_window_seconds"]))
        s["per_user_cap"] = int(max(1, s["per_user_cap"]))
        return AnalysisSettings(**s)

    def fingerprint(self) -> str:
        return hashlib.sha1(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisSettings":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    # Thresholds derived from the 1..10 sensitivity slider -----------------
    @property
    def z_threshold(self) -> float:
        return 7.0 - 0.5 * self.sensitivity          # 6.5 .. 2.0

    @property
    def ratio_threshold(self) -> float:
        return 1.3 + 0.07 * (10 - self.sensitivity)   # 1.93x .. 1.3x of normal
