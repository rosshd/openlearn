from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


TutorSessionKind = Literal["chat", "side_chat"]


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
    source_path: Path | None = None
    source_root: Path | None = None
    source_checksum: str | None = None
