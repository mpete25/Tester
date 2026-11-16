"""Twitch VOD links: validation and metadata container."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

from .errors import InvalidVodUrlError

_ID_RE = re.compile(r"^\d{6,12}$")
_PATH_PATTERNS = (
    re.compile(r"^/videos/(\d+)/?$"),
    re.compile(r"^/\w+/v(?:ideo)?/(\d+)/?$"),
)
_TWITCH_HOSTS = {"twitch.tv", "www.twitch.tv", "m.twitch.tv", "go.twitch.tv"}


def parse_vod_id(text: str) -> str:
    """Return the numeric VOD id for a Twitch VOD URL or raw id.

    Raises :class:`InvalidVodUrlError` with an explanation otherwise.
    """
    raw = (text or "").strip()
    if not raw:
        raise InvalidVodUrlError("Indsæt et Twitch VOD-link.")
    if _ID_RE.match(raw):
        return raw
    candidate = raw if "://" in raw else "https://" + raw
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if host == "clips.twitch.tv" or "/clip/" in parsed.path:
        raise InvalidVodUrlError(
            "Det ligner et Twitch-klip. Værktøjet analyserer hele VODs (twitch.tv/videos/…)."
        )
    if host not in _TWITCH_HOSTS:
        raise InvalidVodUrlError(f"Det er ikke et Twitch-link: {raw}")
    for pattern in _PATH_PATTERNS:
        m = pattern.match(parsed.path)
        if m and _ID_RE.match(m.group(1)):
            return m.group(1)
    raise InvalidVodUrlError(
        "Linket peger ikke på en VOD. Åbn videoen på Twitch og kopiér adressen "
        "(twitch.tv/videos/<nummer>)."
    )


def parse_vod_list(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Parse several links (one per line / comma / space separated).

    Returns ``(unique_ids, [(bad_input, error_message), ...])``.
    """
    ids: list[str] = []
    errors: list[tuple[str, str]] = []
    for token in re.split(r"[\s,;]+", text or ""):
        if not token:
            continue
        try:
            vod_id = parse_vod_id(token)
        except InvalidVodUrlError as exc:
            errors.append((token, str(exc)))
            continue
        if vod_id not in ids:
            ids.append(vod_id)
    return ids, errors


def vod_url(vod_id: str, at_seconds: float | None = None) -> str:
    url = f"https://www.twitch.tv/videos/{vod_id}"
    if at_seconds is not None:
        from .timeutil import twitch_timestamp

        url += f"?t={twitch_timestamp(at_seconds)}"
    return url


@dataclass
class VodInfo:
    vod_id: str
    title: str = ""
    streamer: str = ""
    streamer_login: str = ""
    game: str = ""
    length_seconds: float = 0.0
    created_at: str = ""
    view_count: int = 0
    qualities: list[str] = field(default_factory=list)
    chapters: list[dict] = field(default_factory=list)
    is_demo: bool = False

    @property
    def url(self) -> str:
        return vod_url(self.vod_id)

    @property
    def label(self) -> str:
        who = self.streamer or "Ukendt streamer"
        return f"{who} – {self.title or self.vod_id}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VodInfo":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)
