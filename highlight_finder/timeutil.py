"""Small helpers for converting between seconds and hh:mm:ss strings."""
from __future__ import annotations

import re

_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:[.,]\d+)?)\s*$")
_UNIT_RE = re.compile(r"^\s*(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:[.,]\d+)?)s?)?\s*$")


def format_time(seconds: float) -> str:
    """1234.5 -> '0:20:34' (always h:mm:ss so it is unambiguous)."""
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def format_duration(seconds: float) -> str:
    """Compact human duration: '45s', '1m 05s', '2t 03m'."""
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}t {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def twitch_timestamp(seconds: float) -> str:
    """Timestamp for ``?t=`` links on twitch.tv, e.g. '1h02m03s'."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s"


def filename_time(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}h{m:02d}m{s:02d}s"


def parse_time(text: str) -> float:
    """Parse 'h:mm:ss', 'mm:ss', '1h2m3s' or plain seconds. Raises ValueError."""
    if text is None:
        raise ValueError("Tomt tidspunkt")
    text = str(text).strip()
    if not text:
        raise ValueError("Tomt tidspunkt")
    m = _TIME_RE.match(text)
    if m:
        h = int(m.group(1) or 0)
        mins = int(m.group(2))
        secs = float(m.group(3).replace(",", "."))
        if secs >= 60 or (m.group(1) is not None and mins >= 60):
            raise ValueError(f"Ugyldigt tidspunkt: {text!r}")
        return h * 3600 + mins * 60 + secs
    m = _UNIT_RE.match(text.lower())
    if m and any(m.groups()):
        h = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        secs = float((m.group(3) or "0").replace(",", "."))
        return h * 3600 + mins * 60 + secs
    raise ValueError(f"Kunne ikke forstå tidspunktet {text!r} – brug t:mm:ss")
