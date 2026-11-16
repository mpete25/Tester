"""Thin wrapper around TwitchDownloaderCLI (https://github.com/lay295/TwitchDownloader).

Commands used (verified against the CLI source, release 1.56.x):

* ``info --id <id> --format Raw``          – VOD metadata as JSON on stdout
* ``chatdownload --id <id> -o chat.json``  – full chat, no video needed
* ``videodownload --id <id> -o out.mp4 -b <s>s -e <s>s`` – only the wanted part

The CLI prints progress as ``[STATUS] - Downloading 42% [2/4]`` using carriage
returns, which :func:`run_cli` turns into progress callbacks.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from . import config
from .errors import (
    HighlightFinderError,
    NetworkError,
    ToolNotFoundError,
    VodUnavailableError,
)
from .logging_setup import get_logger
from .vod import VodInfo

log = get_logger("twitch_cli")

ProgressFn = Callable[[float, str], None]

CLI_NAME = "TwitchDownloaderCLI"
INSTALL_HINT = "Kør ./scripts/setup_macos.sh i projektmappen – så hentes værktøjet automatisk."

_STATUS_RE = re.compile(r"\[STATUS\] - (?P<text>.*?)(?:\s*(?P<pct>\d{1,3})%)?(?:\s*\[(?P<step>\d+)/(?P<steps>\d+)\])?\s*$")
_NETWORK_MARKERS = (
    "HttpRequestException",
    "SocketException",
    "No such host",
    "nodename nor servname",
    "Name or service not known",
    "Connection refused",
    "Connection reset",
    "timed out",
    "TaskCanceledException",
    "The SSL connection could not be established",
    "Network is unreachable",
)
_UNAVAILABLE_MARKERS = (
    "Invalid VOD",
    "deleted/expired",
    "Unable to parse VOD",
    "Unable to parse Vod",
    "Video not found",
)
_RESTRICTED_MARKERS = (
    "Insufficient access",
    "vod_manifest_restricted",
    "unauthorized_entitlements",
)


def _candidates(env_var: str, name: str) -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get(env_var)
    if env:
        paths.append(Path(env).expanduser())
    paths.append(config.bin_dir() / name)
    found = shutil.which(name)
    if found:
        paths.append(Path(found))
    paths.extend(Path(d) / name for d in config.EXTRA_BIN_DIRS)
    return paths


def _first_executable(paths: list[Path]) -> Optional[Path]:
    for path in paths:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def find_cli() -> Optional[Path]:
    return _first_executable(_candidates("THF_TWITCHDOWNLOADER_CLI", CLI_NAME))


def find_ffmpeg() -> Optional[Path]:
    return _first_executable(_candidates("THF_FFMPEG", "ffmpeg"))


def require_cli() -> Path:
    path = find_cli()
    if path is None:
        raise ToolNotFoundError("TwitchDownloaderCLI blev ikke fundet.", hint=INSTALL_HINT)
    return path


def require_ffmpeg() -> Path:
    path = find_ffmpeg()
    if path is None:
        raise ToolNotFoundError("FFmpeg blev ikke fundet.", hint="Installér med: brew install ffmpeg")
    return path


def _classify_failure(action: str, output: str, returncode: int) -> HighlightFinderError:
    """Turn raw CLI output into a friendly, typed error."""
    tail = "\n".join(output.strip().splitlines()[-15:])
    if any(m in output for m in _RESTRICTED_MARKERS):
        return VodUnavailableError(
            "VOD'en kræver adgang (fx kun for abonnenter).",
            hint="Sæt miljøvariablen THF_TWITCH_OAUTH til dit OAuth-token, hvis du har adgang.",
            details=tail,
        )
    if any(m in output for m in _UNAVAILABLE_MARKERS):
        return VodUnavailableError("VOD'en findes ikke eller er ikke længere tilgængelig.", details=tail)
    if any(m in output for m in _NETWORK_MARKERS):
        return NetworkError(f"Netværksfejl under {action}.", details=tail)
    return HighlightFinderError(
        f"TwitchDownloaderCLI fejlede under {action} (kode {returncode}).",
        hint="Se loggen for detaljer.",
        details=tail,
    )


def _split_lines(buffer: bytes) -> tuple[list[str], bytes]:
    """Split on both \\r and \\n – the CLI rewrites its status line with \\r."""
    parts = re.split(rb"[\r\n]+", buffer)
    rest = parts.pop()
    return [p.decode("utf-8", errors="replace") for p in parts if p.strip()], rest


def run_cli(
    args: list[str],
    action: str,
    progress: Optional[ProgressFn] = None,
    timeout: Optional[float] = None,
) -> str:
    """Run the CLI, stream its status lines to ``progress`` and return all output."""
    cli = require_cli()
    cmd = [str(cli), *args]
    safe_cmd = " ".join("<token>" if i and cmd[i - 1] == "--oauth" else c for i, c in enumerate(cmd))
    log.info("Kører: %s", safe_cmd)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, bufsize=0)
    except OSError as exc:
        raise ToolNotFoundError(
            f"TwitchDownloaderCLI kunne ikke startes: {exc}",
            hint="På macOS: chmod +x bin/TwitchDownloaderCLI && xattr -d com.apple.quarantine bin/TwitchDownloaderCLI",
        ) from exc

    collected: list[str] = []
    buffer = b""
    last_logged = ""
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    try:
        while True:
            chunk = os.read(fd, 8192)
            if not chunk:
                break
            lines, buffer = _split_lines(buffer + chunk)
            for line in lines:
                line = line.rstrip()
                collected.append(line)
                m = _STATUS_RE.search(line)
                if m:
                    text = m.group("text").strip()
                    pct = m.group("pct")
                    if progress:
                        step, steps = m.group("step"), m.group("steps")
                        frac = int(pct) / 100 if pct is not None else 0.0
                        if step and steps:  # spread multi-step commands over 0..1
                            frac = (int(step) - 1 + frac) / int(steps)
                        progress(min(1.0, frac), f"{text} {pct}%" if pct else text)
                    if text != last_logged:  # do not flood the log with every percent
                        log.info("CLI: %s", text)
                        last_logged = text
                else:
                    log.info("CLI: %s", line)
            if timeout and time.monotonic() - started > timeout:
                proc.kill()
                raise NetworkError(f"Timeout under {action} (efter {int(timeout)} sekunder).")
        if buffer.strip():
            collected.append(buffer.decode("utf-8", errors="replace").strip())
        returncode = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()

    output = "\n".join(collected)
    if returncode != 0:
        log.error("CLI fejlede (%s) under %s:\n%s", returncode, action, "\n".join(collected[-30:]))
        raise _classify_failure(action, output, returncode)
    log.info("Færdig med %s på %.1f s", action, time.monotonic() - started)
    return output


def _oauth_args() -> list[str]:
    token = os.environ.get("THF_TWITCH_OAUTH", "").strip()
    return ["--oauth", token] if token else []


def parse_info_output(vod_id: str, stdout: str) -> VodInfo:
    """Parse ``info --format Raw``: video JSON, chapters JSON, then the m3u8 playlist."""
    json_docs: list[dict] = []
    playlist_lines: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and len(json_docs) < 2:
            try:
                json_docs.append(json.loads(stripped))
                continue
            except json.JSONDecodeError:
                pass
        playlist_lines.append(stripped)

    video = None
    chapters_doc = None
    for doc in json_docs:
        data = doc.get("data") or {}
        if "video" in data and video is None and "moments" not in (data.get("video") or {}):
            video = data.get("video")
        else:
            chapters_doc = doc
    if not video:
        raise VodUnavailableError("VOD'en findes ikke eller er ikke længere tilgængelig.")

    playlist = "\n".join(playlist_lines)
    if any(m in playlist for m in _RESTRICTED_MARKERS):
        log.warning("VOD %s ser ud til at være begrænset (kun abonnenter).", vod_id)
    qualities: list[str] = []
    for name in re.findall(r'#EXT-X-MEDIA:[^\n]*?NAME="([^"]+)"', playlist):
        clean = name.replace(" (source)", "").strip()
        if clean and clean not in qualities:
            qualities.append(clean)

    chapters: list[dict] = []
    try:
        edges = chapters_doc["data"]["video"]["moments"]["edges"] if chapters_doc else []
        for edge in edges or []:
            node = edge.get("node") or {}
            chapters.append(
                {
                    "start": (node.get("positionMilliseconds") or 0) / 1000,
                    "length": (node.get("durationMilliseconds") or 0) / 1000,
                    "name": node.get("description") or "",
                }
            )
    except (KeyError, TypeError, AttributeError):
        chapters = []

    owner = video.get("owner") or {}
    game = video.get("game") or {}
    return VodInfo(
        vod_id=vod_id,
        title=(video.get("title") or "").strip(),
        streamer=owner.get("displayName") or owner.get("login") or "",
        streamer_login=owner.get("login") or "",
        game=game.get("displayName") or "",
        length_seconds=float(video.get("lengthSeconds") or 0),
        created_at=str(video.get("createdAt") or ""),
        view_count=int(video.get("viewCount") or 0),
        qualities=qualities,
        chapters=chapters,
    )


def fetch_vod_info(vod_id: str, timeout: float = 60) -> VodInfo:
    """Fetch VOD metadata without downloading anything else."""
    cli = require_cli()
    cmd = [str(cli), "info", "--id", vod_id, "--format", "Raw", "--banner", "false", "--log-level", "Error", *_oauth_args()]
    log.info("Henter VOD-information for %s", vod_id)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise NetworkError("Timeout ved hentning af VOD-information.") from exc
    except OSError as exc:
        raise ToolNotFoundError(f"TwitchDownloaderCLI kunne ikke startes: {exc}", hint=INSTALL_HINT) from exc
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        log.error("info fejlede (%s): %s", proc.returncode, (stderr or stdout)[-2000:])
        raise _classify_failure("hentning af VOD-information", stdout + "\n" + stderr, proc.returncode)
    info = parse_info_output(vod_id, stdout)
    log.info("VOD %s: %s – %s (%.0f s)", vod_id, info.streamer, info.title, info.length_seconds)
    return info


def download_chat(vod_id: str, output: Path, progress: Optional[ProgressFn] = None, retries: int = 2) -> Path:
    """Download the complete chat as JSON. Retries on network errors."""
    output.parent.mkdir(parents=True, exist_ok=True)
    config.tmp_dir().mkdir(parents=True, exist_ok=True)
    args = [
        "chatdownload",
        "--id", vod_id,
        "-o", str(output),
        "--collision", "Overwrite",
        "--banner", "false",
        "--threads", "4",
        "--temp-path", str(config.tmp_dir()),
    ]
    attempt = 0
    while True:
        try:
            run_cli(args, "download af chat", progress)
            break
        except NetworkError:
            attempt += 1
            if attempt > retries:
                raise
            wait = 3 * attempt
            log.warning("Netværksfejl – prøver igen om %s s (forsøg %s/%s)", wait, attempt, retries)
            if progress:
                progress(0.0, f"Netværksfejl – prøver igen om {wait} s…")
            time.sleep(wait)
    if not output.exists() or output.stat().st_size == 0:
        raise HighlightFinderError("Chat-filen blev ikke oprettet.", hint="Se loggen for detaljer.")
    return output


def download_video_segment(
    vod_id: str,
    start: int,
    end: int,
    output: Path,
    quality: Optional[str] = None,
    progress: Optional[ProgressFn] = None,
) -> Path:
    """Download only ``start``..``end`` (whole seconds) of the VOD as MP4.

    Whole seconds with an ``s`` suffix are used on purpose: they are parsed the
    same way regardless of the system locale (decimal comma vs. point).
    """
    if end <= start:
        raise HighlightFinderError("Sluttidspunktet skal ligge efter starttidspunktet.")
    ffmpeg = require_ffmpeg()
    output.parent.mkdir(parents=True, exist_ok=True)
    config.tmp_dir().mkdir(parents=True, exist_ok=True)
    args = [
        "videodownload",
        "--id", vod_id,
        "-o", str(output),
        "-b", f"{int(start)}s",
        "-e", f"{int(end)}s",
        "--trim-mode", "Exact",
        "--threads", "4",
        "--collision", "Overwrite",
        "--banner", "false",
        "--ffmpeg-path", str(ffmpeg),
        "--temp-path", str(config.tmp_dir()),
        *_oauth_args(),
    ]
    if quality:
        args += ["--quality", quality]
    run_cli(args, "download af video", progress)
    if not output.exists() or output.stat().st_size == 0:
        raise HighlightFinderError("Videofilen blev ikke oprettet.", hint="Se loggen for detaljer.")
    return output


def cli_version() -> str:
    cli = find_cli()
    if cli is None:
        return ""
    try:
        proc = subprocess.run([str(cli), "--version"], capture_output=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    m = re.search(r"\d+\.\d+\.\d+", text)
    return m.group(0) if m else ""
