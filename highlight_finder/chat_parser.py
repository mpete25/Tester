"""Read TwitchDownloader chat JSON into a compact pandas DataFrame.

A six hour stream with a busy chat can be several hundred MB of JSON, so the
file is parsed as a stream (ijson) and only the few fields we need are kept.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import IO, Iterator

import pandas as pd

from .errors import HighlightFinderError, NoChatDataError
from .logging_setup import get_logger

log = get_logger("chat_parser")

MESSAGE_COLUMNS = ["offset", "user", "name", "body"]


def _open(path: Path) -> IO[bytes]:
    if path.suffix == ".gz":
        return gzip.open(path, "rb")
    return path.open("rb")


def _iter_comments(path: Path) -> Iterator[dict]:
    try:
        import ijson  # streaming parser – constant memory
    except ImportError:  # pragma: no cover - fallback when ijson is missing
        log.warning("ijson er ikke installeret – indlæser hele chat-filen i hukommelsen.")
        with _open(path) as fh:
            yield from (json.load(fh).get("comments") or [])
        return
    with _open(path) as fh:
        yield from ijson.items(fh, "comments.item", use_float=True)


def read_chat_header(path: Path) -> dict:
    """Return the ``video`` and ``streamer`` objects that precede the comments."""
    header: dict = {"video": {}, "streamer": {}}
    try:
        import ijson
    except ImportError:  # pragma: no cover
        with _open(path) as fh:
            doc = json.load(fh)
        return {"video": doc.get("video") or {}, "streamer": doc.get("streamer") or {}}
    with _open(path) as fh:
        try:
            for prefix, event, value in ijson.parse(fh, use_float=True):
                if prefix == "comments" and event == "start_array":
                    break
                parts = prefix.split(".")
                if len(parts) == 2 and parts[0] in header and event in ("string", "number", "boolean", "integer", "double"):
                    header[parts[0]][parts[1]] = value
        except ijson.common.IncompleteJSONError as exc:
            raise HighlightFinderError("Chat-filen er beskadiget eller ufuldstændig.", details=str(exc)) from exc
    return header


def parse_chat_file(path: Path) -> pd.DataFrame:
    """Parse a chat JSON file into a DataFrame with columns ``MESSAGE_COLUMNS``.

    ``offset`` is ``content_offset_seconds``: seconds from the start of the VOD,
    i.e. exactly the timeline TwitchDownloader uses when trimming video.
    """
    offsets: list[float] = []
    users: list[str] = []
    names: list[str] = []
    bodies: list[str] = []
    skipped = 0
    try:
        for comment in _iter_comments(path):
            try:
                offset = float(comment["content_offset_seconds"])
                commenter = comment.get("commenter") or {}
                message = comment.get("message") or {}
                body = message.get("body")
                if body is None:
                    body = "".join(f.get("text", "") for f in message.get("fragments") or [])
                body = body.strip()
                name = commenter.get("display_name") or commenter.get("name") or ""
                user = str(commenter.get("_id") or commenter.get("name") or name or "").lower()
            except (KeyError, TypeError, ValueError, AttributeError):
                skipped += 1
                continue
            if not body or offset < 0:
                skipped += 1
                continue
            offsets.append(offset)
            users.append(user or f"anon-{len(offsets)}")
            names.append(name)
            bodies.append(body)
    except HighlightFinderError:
        raise
    except Exception as exc:  # malformed / truncated JSON
        if not offsets:
            raise HighlightFinderError(
                "Chat-filen kunne ikke læses.", hint="Prøv at analysere VOD'en igen.", details=str(exc)
            ) from exc
        log.warning("Chat-filen sluttede uventet efter %s beskeder: %s", len(offsets), exc)

    if not offsets:
        raise NoChatDataError("Der blev ikke fundet nogen chatbeskeder til denne VOD.")
    df = pd.DataFrame({"offset": offsets, "user": users, "name": names, "body": bodies})
    df = df.sort_values("offset", kind="stable").reset_index(drop=True)
    log.info("Indlæste %s chatbeskeder (%s sprunget over)", len(df), skipped)
    return df
