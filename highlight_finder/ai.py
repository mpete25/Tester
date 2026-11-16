"""Optional AI titles from a *local* Ollama server (https://ollama.com).

Completely optional: nothing here is imported by the core pipeline, no API key
or paid service is involved, and every call fails soft (returns ``None``).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .analysis import Highlight
from .logging_setup import get_logger
from .reactions import CATEGORY_LABELS
from .vod import VodInfo

log = get_logger("ai")

DEFAULT_MODEL = "llama3.2"


def ollama_url() -> str:
    return os.environ.get("THF_OLLAMA_URL", "http://localhost:11434").rstrip("/")


def _request(path: str, payload: dict | None = None, timeout: float = 2.0) -> dict | None:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(ollama_url() + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("Ollama ikke tilgængelig (%s): %s", path, exc)
        return None


def available_models() -> list[str]:
    """Models installed in the local Ollama server; empty list if it is not running."""
    data = _request("/api/tags", timeout=1.5)
    if not data:
        return []
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def build_prompt(h: Highlight, info: VodInfo | None, language: str = "da") -> str:
    lang = "dansk" if language == "da" else "English"
    reactions = ", ".join(f"{t} x{n}" for t, n in h.top_reactions[:5]) or "-"
    words = ", ".join(w for w, _n in h.top_words[:6]) or "-"
    quotes = "\n".join(f"- {m['text']}" for m in h.notable_messages[:5]) or "-"
    return (
        f"You write short, catchy titles for Twitch clips. Answer in {lang} with ONE title only, "
        "max 60 characters, no quotes, no hashtags.\n\n"
        f"Streamer: {info.streamer if info else '-'}\nGame/category: {info.game if info else '-'}\n"
        f"Type of moment: {CATEGORY_LABELS.get(h.kind, h.kind)}\n"
        f"Chat reactions: {reactions}\nFrequent words in chat: {words}\nChat messages:\n{quotes}\n\nTitle:"
    )


def generate_title(h: Highlight, info: VodInfo | None, model: str = DEFAULT_MODEL, language: str = "da", timeout: float = 60.0) -> str | None:
    """Ask the local model for a title. Returns ``None`` on any problem."""
    payload = {"model": model, "prompt": build_prompt(h, info, language), "stream": False, "options": {"temperature": 0.8}}
    data = _request("/api/generate", payload, timeout=timeout)
    if not data or not data.get("response"):
        return None
    title = data["response"].strip().splitlines()[0].strip().strip('"“”\'')
    return title[:100] or None
