"""Pure URL handling for consent-gated external video embeds."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit


YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = frozenset({"m.youtube.com", "youtube.com", "www.youtube.com"})
_SHORT_HOSTS = frozenset({"youtu.be"})
_REDIRECT_QUERY_KEYS = frozenset({"continue", "next", "q", "redirect", "redirect_url", "url"})
_INVALID_URL_MESSAGE = "Enter a valid supported YouTube video URL."


class VideoURLValidationError(ValueError):
    """Raised when an external video URL cannot be safely embedded."""


@dataclass(frozen=True, slots=True)
class VideoDescriptor:
    """A normalized video reference that a UI may load only after consent."""

    provider: Literal["youtube"]
    video_id: str
    canonical_url: str
    embed_url: str
    requires_consent: Literal[True]


def describe_youtube_video(value: str) -> VideoDescriptor:
    """Validate a YouTube URL and return a tracking-free, consent-gated descriptor."""

    try:
        video_id = _youtube_video_id(value)
    except (TypeError, ValueError, UnicodeError):
        raise VideoURLValidationError(_INVALID_URL_MESSAGE) from None

    return VideoDescriptor(
        provider="youtube",
        video_id=video_id,
        canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        embed_url=f"https://www.youtube-nocookie.com/embed/{video_id}",
        requires_consent=True,
    )


def _youtube_video_id(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError
    if (
        _contains_control_character(value)
        or _has_invalid_percent_escape(value)
        or _contains_control_character(unquote(value))
    ):
        raise ValueError

    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError
    if parsed.username is not None or parsed.password is not None:
        raise ValueError
    if parsed.port is not None:
        raise ValueError

    hostname = parsed.hostname
    if hostname is None:
        raise ValueError
    hostname = hostname.lower()
    if _contains_control_character(unquote(parsed.path)):
        raise ValueError

    query_pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    if any(
        _contains_control_character(key) or _contains_control_character(item)
        for key, item in query_pairs
    ):
        raise ValueError
    if any(key.lower() in _REDIRECT_QUERY_KEYS for key, _ in query_pairs):
        raise ValueError

    if hostname in _YOUTUBE_HOSTS:
        video_id = _video_id_from_youtube_path(parsed.path, query_pairs)
    elif hostname in _SHORT_HOSTS:
        video_id = _single_path_segment(parsed.path)
    else:
        raise ValueError

    if YOUTUBE_VIDEO_ID_PATTERN.fullmatch(video_id) is None:
        raise ValueError
    return video_id


def _video_id_from_youtube_path(path: str, query_pairs: list[tuple[str, str]]) -> str:
    if path == "/watch":
        video_ids = [value for key, value in query_pairs if key == "v"]
        if len(video_ids) != 1:
            raise ValueError
        return video_ids[0]

    normalized_path = path[:-1] if path.endswith("/") else path
    segments = normalized_path.split("/")
    if len(segments) == 3 and not segments[0] and segments[1] in {"embed", "shorts"}:
        return segments[2]
    raise ValueError


def _single_path_segment(path: str) -> str:
    normalized_path = path[:-1] if path.endswith("/") else path
    segments = normalized_path.split("/")
    if len(segments) != 2 or segments[0]:
        raise ValueError
    return segments[1]


def _has_invalid_percent_escape(value: str) -> bool:
    index = 0
    while index < len(value):
        if value[index] != "%":
            index += 1
            continue
        if index + 2 >= len(value) or not all(
            character in "0123456789abcdefABCDEF" for character in value[index + 1 : index + 3]
        ):
            return True
        index += 3
    return False


def _contains_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)
