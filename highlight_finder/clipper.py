"""Export highlights as MP4 files.

Only the wanted seconds of the VOD are downloaded (TwitchDownloaderCLI
``videodownload -b/-e``). The 16:9 file is that download untouched – original
video and audio streams. The optional 9:16 version for Shorts/TikTok is made
with FFmpeg (video re-encoded to 1080x1920, audio copied).
"""
from __future__ import annotations

import csv
import math
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import config, twitch_cli
from .errors import ClipExportError, HighlightFinderError
from .logging_setup import get_logger
from .timeutil import filename_time, format_time
from .vod import VodInfo

log = get_logger("clipper")

ProgressFn = Callable[[float, str], None]

FORMAT_WIDE = "16:9"
FORMAT_VERTICAL = "9:16"
VERTICAL_MODES = {
    "crop": "Beskær til midten (fylder hele skærmen)",
    "blur": "Hele billedet med sløret baggrund",
}
MAX_CLIP_SECONDS = 15 * 60

_VERTICAL_FILTERS = {
    "crop": "[0:v]crop='min(iw,ih*9/16)':ih,scale=1080:1920:flags=lanczos,setsar=1[v]",
    "blur": (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:4[bgb];"
        "[fg]scale=1080:-2:flags=lanczos[fgs];"
        "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    ),
}


@dataclass
class ExportedClip:
    path: Path
    format: str
    start: float
    end: float
    title: str


def slugify(text: str, max_length: int = 50) -> str:
    """File-system safe name that keeps letters like æøå."""
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    return text[:max_length].rstrip("-") or "clip"


def clip_folder(vod: VodInfo) -> Path:
    streamer = slugify(vod.streamer or "ukendt-streamer", 40)
    return config.clips_dir() / streamer / f"{vod.vod_id}_{slugify(vod.title or 'vod', 40)}"


def clip_filename(start: float, title: str, fmt: str, score: Optional[int] = None) -> str:
    parts = [filename_time(start)]
    if score is not None:
        parts.append(f"score{int(score):02d}")
    parts.append(slugify(title, 45))
    parts.append(fmt.replace(":", "x"))
    return "_".join(parts) + ".mp4"


def _run_ffmpeg(args: list[str], duration: float, progress: Optional[ProgressFn], label: str) -> None:
    ffmpeg = twitch_cli.require_ffmpeg()
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args, "-progress", "pipe:1", "-nostats"]
    log.info("Kører: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    assert proc.stdout is not None and proc.stderr is not None
    for line in proc.stdout:
        if progress and line.startswith("out_time_us=") and duration > 0:
            try:
                done = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                continue
            progress(min(1.0, done / duration), label)
    stderr = proc.stderr.read()
    if proc.wait() != 0:
        log.error("FFmpeg fejlede: %s", stderr[-3000:])
        raise ClipExportError("FFmpeg kunne ikke behandle klippet.", hint="Se loggen for detaljer.", details=stderr[-800:])


def make_vertical(source: Path, dest: Path, mode: str = "crop", duration: float = 0.0, progress: Optional[ProgressFn] = None) -> Path:
    """Create a 1080x1920 version of ``source``; audio is copied unchanged."""
    if mode not in _VERTICAL_FILTERS:
        raise ClipExportError(f"Ukendt 9:16-tilstand: {mode}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-i", str(source),
        "-filter_complex", _VERTICAL_FILTERS[mode],
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(dest),
    ]
    _run_ffmpeg(args, duration, progress, "Laver 9:16-version")
    return dest


def _append_index(folder: Path, vod: VodInfo, clip: ExportedClip, score: Optional[int]) -> None:
    index = folder / "clips.csv"
    new = not index.exists()
    with index.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if new:
            writer.writerow(["exported_at", "file", "format", "start", "end", "seconds", "score", "title", "vod_url"])
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"), clip.path.name, clip.format, format_time(clip.start),
            format_time(clip.end), int(clip.end - clip.start), "" if score is None else score, clip.title,
            f"{vod.url}?t={int(clip.start)}s",
        ])


def export_clip(
    vod: VodInfo,
    start: float,
    end: float,
    title: str = "",
    formats: tuple[str, ...] = (FORMAT_WIDE,),
    vertical_mode: str = "crop",
    quality: Optional[str] = None,
    score: Optional[int] = None,
    progress: Optional[ProgressFn] = None,
) -> list[ExportedClip]:
    """Download ``start``..``end`` of the VOD and write the requested formats."""
    if vod.is_demo:
        raise ClipExportError("Demo-streamen har ingen video – analysér en rigtig VOD for at eksportere klip.")
    start_s, end_s = int(math.floor(max(0.0, start))), int(math.ceil(end))
    if vod.length_seconds:
        end_s = min(end_s, int(math.ceil(vod.length_seconds)))
    if end_s - start_s < 1:
        raise ClipExportError("Klippet skal være mindst 1 sekund langt.")
    if end_s - start_s > MAX_CLIP_SECONDS:
        raise ClipExportError(f"Klippet er for langt (maks. {MAX_CLIP_SECONDS // 60} minutter).")
    formats = tuple(dict.fromkeys(formats))
    if not formats or any(f not in (FORMAT_WIDE, FORMAT_VERTICAL) for f in formats):
        raise ClipExportError("Vælg mindst ét format: 16:9 eller 9:16.")

    title = title.strip() or f"Highlight {format_time(start_s)}"
    folder = clip_folder(vod)
    folder.mkdir(parents=True, exist_ok=True)
    source = config.tmp_dir() / f"{vod.vod_id}_{start_s}_{end_s}.mp4"
    want_vertical = FORMAT_VERTICAL in formats
    download_share = 0.6 if want_vertical else 1.0
    log.info("Eksporterer %s %s-%s (%s) som %s", vod.vod_id, format_time(start_s), format_time(end_s), title, formats)

    def dl_progress(frac: float, message: str) -> None:
        if progress:
            progress(frac * download_share, f"Henter video: {message}")

    exported: list[ExportedClip] = []
    try:
        twitch_cli.download_video_segment(vod.vod_id, start_s, end_s, source, quality=quality, progress=dl_progress)
        if want_vertical:
            dest = folder / clip_filename(start_s, title, FORMAT_VERTICAL, score)
            make_vertical(
                source, dest, vertical_mode, duration=end_s - start_s,
                progress=(lambda f, m: progress(download_share + f * (1 - download_share), m)) if progress else None,
            )
            exported.append(ExportedClip(dest, FORMAT_VERTICAL, start_s, end_s, title))
        if FORMAT_WIDE in formats:
            dest = folder / clip_filename(start_s, title, FORMAT_WIDE, score)
            shutil.move(str(source), str(dest))   # the untouched download: original audio + video
            exported.insert(0, ExportedClip(dest, FORMAT_WIDE, start_s, end_s, title))
    except HighlightFinderError:
        raise
    except OSError as exc:
        raise ClipExportError(f"Kunne ikke gemme klippet: {exc}") from exc
    finally:
        source.unlink(missing_ok=True)

    for clip in exported:
        _append_index(folder, vod, clip, score)
        log.info("Gemte %s (%.1f MB)", clip.path, clip.path.stat().st_size / 1e6)
    if progress:
        progress(1.0, "Færdig")
    return exported
