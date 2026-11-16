"""Orchestration: link -> VOD info -> chat -> analysis, with caching and progress."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

from . import cache, chat_parser, sample_data, twitch_cli
from .analysis import AnalysisResult, analyze, annotate_messages
from .config import AnalysisSettings
from .errors import HighlightFinderError, NoChatDataError
from .insights import enrich_highlights
from .logging_setup import get_logger
from .vod import VodInfo, parse_vod_id

log = get_logger("pipeline")

# progress(stage, fraction 0..1, message); stages: info, chat, parse, analyze
StageProgressFn = Callable[[str, float, str], None]


@dataclass
class VodAnalysis:
    info: VodInfo
    messages: pd.DataFrame      # annotated (bot / react columns)
    result: AnalysisResult
    from_cache: bool = False


def _noop(stage: str, fraction: float, message: str) -> None:
    pass


def _merge_header(info: VodInfo, header: dict) -> VodInfo:
    """Fill gaps in ``info`` from the metadata embedded in the chat file."""
    video, streamer = header.get("video") or {}, header.get("streamer") or {}
    info.title = info.title or str(video.get("title") or "")
    info.game = info.game or str(video.get("game") or "")
    info.streamer = info.streamer or str(streamer.get("name") or "")
    info.streamer_login = info.streamer_login or str(streamer.get("login") or "")
    info.created_at = info.created_at or str(video.get("created_at") or "")
    if not info.length_seconds:
        info.length_seconds = float(video.get("length") or 0) if float(video.get("length") or 0) > 0 else float(video.get("end") or 0)
    return info


def fetch_chat(vod_id: str, progress: StageProgressFn = _noop, force: bool = False) -> tuple[VodInfo, pd.DataFrame, bool]:
    """Return ``(info, messages, from_cache)``; downloads only what is missing."""
    if not force and cache.has_messages(vod_id):
        info, messages = cache.load_info(vod_id), cache.load_messages(vod_id)
        if info is not None and messages is not None and len(messages):
            log.info("Bruger gemt chat for VOD %s (%s beskeder)", vod_id, len(messages))
            progress("chat", 1.0, "Chat fundet i cachen")
            return info, messages, True

    progress("info", 0.0, "Henter VOD-information…")
    info = twitch_cli.fetch_vod_info(vod_id)
    progress("info", 1.0, f"{info.streamer} – {info.title}")

    raw = cache.raw_chat_path(vod_id)
    progress("chat", 0.0, "Henter chat…")
    twitch_cli.download_chat(vod_id, raw, progress=lambda f, m: progress("chat", f, m))
    progress("parse", 0.0, "Læser chatbeskeder…")
    try:
        header = chat_parser.read_chat_header(raw)
        messages = chat_parser.parse_chat_file(raw)
    finally:
        if os.environ.get("THF_KEEP_RAW_CHAT") != "1":
            raw.unlink(missing_ok=True)
    info = _merge_header(info, header)
    if not info.length_seconds:
        info.length_seconds = float(messages["offset"].max())
    cache.save_info(info)
    cache.save_messages(vod_id, messages)
    progress("parse", 1.0, f"{len(messages):,} beskeder".replace(",", "."))
    return info, messages, False


def run_analysis(
    info: VodInfo, messages: pd.DataFrame, settings: Optional[AnalysisSettings] = None, language: str = "da", save: bool = True,
) -> AnalysisResult:
    """Analyse already loaded chat (fast – used when settings change)."""
    if messages is None or len(messages) == 0:
        raise NoChatDataError("Der blev ikke fundet nogen chatbeskeder til denne VOD.")
    annotated = messages if {"bot", "react"} <= set(messages.columns) else annotate_messages(messages)
    result = analyze(annotated, info.length_seconds, settings)
    enrich_highlights(result.highlights, annotated, info, language)
    if save:
        try:
            cache.save_analysis(info.vod_id, result)
        except OSError as exc:
            log.warning("Kunne ikke gemme analyseresultatet: %s", exc)
    return result


def analyze_vod(
    link: str, settings: Optional[AnalysisSettings] = None, progress: StageProgressFn = _noop,
    force_download: bool = False, language: str = "da",
) -> VodAnalysis:
    """Full workflow for one VOD link. Raises :class:`HighlightFinderError`."""
    vod_id = parse_vod_id(link)
    log.info("Analyserer VOD %s", vod_id)
    try:
        info, messages, from_cache = fetch_chat(vod_id, progress, force=force_download)
        progress("analyze", 0.0, "Analyserer chataktivitet…")
        annotated = annotate_messages(messages)
        result = run_analysis(info, annotated, settings, language)
        progress("analyze", 1.0, f"{len(result.highlights)} highlights fundet")
    except HighlightFinderError as exc:
        log.error("Analyse af %s fejlede: %s %s", vod_id, exc, exc.details)
        raise
    except Exception as exc:
        log.exception("Uventet fejl under analyse af %s", vod_id)
        raise HighlightFinderError(f"Uventet fejl: {exc}", hint="Se loggen for detaljer.") from exc
    return VodAnalysis(info, annotated, result, from_cache)


def load_demo(settings: Optional[AnalysisSettings] = None, language: str = "da", duration: float = 6 * 3600) -> VodAnalysis:
    """Synthetic six hour stream – lets you try the app without touching Twitch."""
    vod_id = sample_data.DEMO_VOD_ID
    if not cache.has_messages(vod_id):
        raw = cache.raw_chat_path(vod_id)
        sample_data.write_chat_json(raw, duration=duration, vod_id=vod_id)
        try:
            header = chat_parser.read_chat_header(raw)
            messages = chat_parser.parse_chat_file(raw)
        finally:
            raw.unlink(missing_ok=True)
        info = _merge_header(VodInfo(vod_id=vod_id, is_demo=True), header)
        cache.save_info(info)
        cache.save_messages(vod_id, messages)
    info, messages = cache.load_info(vod_id), cache.load_messages(vod_id)
    annotated = annotate_messages(messages)
    return VodAnalysis(info, annotated, run_analysis(info, annotated, settings, language), True)
