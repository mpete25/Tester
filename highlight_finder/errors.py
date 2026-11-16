"""Exceptions with user-facing (Danish) messages."""
from __future__ import annotations


class HighlightFinderError(Exception):
    """Base class. ``str(exc)`` is safe to show directly in the UI."""

    hint: str = ""

    def __init__(self, message: str, hint: str = "", details: str = ""):
        super().__init__(message)
        self.hint = hint or self.hint
        self.details = details


class InvalidVodUrlError(HighlightFinderError):
    hint = "Brug et link som https://www.twitch.tv/videos/1234567890"


class ToolNotFoundError(HighlightFinderError):
    pass


class VodUnavailableError(HighlightFinderError):
    hint = "VOD'en kan være slettet, udløbet eller kun for abonnenter."


class NetworkError(HighlightFinderError):
    hint = "Tjek din internetforbindelse og prøv igen."


class NoChatDataError(HighlightFinderError):
    hint = "Streamen har ingen gemt chat (eller chatten var slået fra)."


class ClipExportError(HighlightFinderError):
    pass
