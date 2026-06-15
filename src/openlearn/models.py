from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Topic:
    slug: str
    path: Path
    metadata: dict[str, object]
    body: str


@dataclass(frozen=True)
class TopicSummary:
    slug: str
    path: Path
    metadata: dict[str, object]


@dataclass(frozen=True)
class PendingContext:
    filename: str
    text: str
