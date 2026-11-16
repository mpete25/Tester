"""On-disk cache per VOD so nothing is downloaded or parsed twice.

``data/vods/<vod_id>/``
    ``info.json``         VOD metadata
    ``messages.parquet``  compact chat (offset, user, name, body)
    ``highlights.json``   last analysis result (for reference / other tools)
    ``highlights.csv``    same, as a spreadsheet-friendly table
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config
from .analysis import AnalysisResult
from .chat_parser import MESSAGE_COLUMNS
from .logging_setup import get_logger
from .timeutil import format_time
from .vod import VodInfo

log = get_logger("cache")


def vod_dir(vod_id: str) -> Path:
    return config.vods_dir() / str(vod_id)


def info_path(vod_id: str) -> Path:
    return vod_dir(vod_id) / "info.json"


def messages_path(vod_id: str) -> Path:
    return vod_dir(vod_id) / "messages.parquet"


def raw_chat_path(vod_id: str) -> Path:
    return vod_dir(vod_id) / "chat.json"


def has_messages(vod_id: str) -> bool:
    return messages_path(vod_id).is_file() and info_path(vod_id).is_file()


def save_info(info: VodInfo) -> None:
    path = info_path(info.vod_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = info.to_dict()
    data["cached_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_info(vod_id: str) -> VodInfo | None:
    path = info_path(vod_id)
    if not path.is_file():
        return None
    try:
        return VodInfo.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError) as exc:
        log.warning("Kunne ikke læse %s: %s", path, exc)
        return None


def save_messages(vod_id: str, df: pd.DataFrame) -> None:
    path = messages_path(vod_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df[MESSAGE_COLUMNS].to_parquet(tmp, index=False)
    tmp.replace(path)


def load_messages(vod_id: str) -> pd.DataFrame | None:
    path = messages_path(vod_id)
    if not path.is_file():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as exc:  # corrupt cache -> behave as if it is missing
        log.warning("Chat-cachen for %s er beskadiget (%s) – henter igen.", vod_id, exc)
        return None


def save_analysis(vod_id: str, result: AnalysisResult) -> None:
    folder = vod_dir(vod_id)
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "vod_id": vod_id,
        "analysed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": result.settings.__dict__,
        "stats": result.stats,
        "highlights": [h.to_dict() for h in result.highlights],
    }
    (folder / "highlights.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = [
        {
            "rank": h.rank, "score": h.score, "type": h.kind, "peak": format_time(h.peak_time),
            "clip_start": format_time(h.clip_start), "clip_end": format_time(h.clip_end),
            "messages": h.messages, "increase_pct": h.increase_pct, "unique_users": h.unique_users,
            "top_reactions": ", ".join(f"{t} x{n}" for t, n in h.top_reactions),
            "title": h.titles[0] if h.titles else "",
        }
        for h in result.highlights
    ]
    pd.DataFrame(rows).to_csv(folder / "highlights.csv", index=False)


def list_cached_vods() -> list[VodInfo]:
    """Analysed VODs, newest first."""
    root = config.vods_dir()
    if not root.is_dir():
        return []
    found: list[tuple[float, VodInfo]] = []
    for folder in root.iterdir():
        if folder.is_dir() and has_messages(folder.name):
            info = load_info(folder.name)
            if info:
                found.append((info_path(folder.name).stat().st_mtime, info))
    return [info for _mtime, info in sorted(found, key=lambda x: -x[0])]


def delete_vod(vod_id: str) -> None:
    folder = vod_dir(vod_id)
    if folder.is_dir() and folder.parent == config.vods_dir():
        shutil.rmtree(folder)
        log.info("Slettede cache for VOD %s", vod_id)
