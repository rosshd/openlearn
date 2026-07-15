"""Sanitized evidence persistence for exploratory terminal missions."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path

SCHEMA_VERSION = 1
REDACTION_MARKER = "[REDACTED]"

_CREDENTIAL_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
)


class EvidenceRecorder:
    """Append sanitized terminal interactions to a versioned JSONL stream."""

    def __init__(
        self,
        path: Path,
        *,
        sensitive_values: Iterable[str] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self._clock = clock
        self._started_at = clock()
        self._sensitive_values = tuple(
            sorted({value for value in sensitive_values if value}, key=len, reverse=True)
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_input(self, text: str) -> None:
        self._record("input", text)

    def record_output(self, text: str) -> None:
        self._record("output", text)

    def _record(self, event: str, text: str) -> None:
        sanitized = self._sanitize(text)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "event": event,
            "elapsed_seconds": round(max(0.0, self._clock() - self._started_at), 6),
            "text": sanitized,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")

    def _sanitize(self, text: str) -> str:
        sanitized = text
        for value in self._sensitive_values:
            sanitized = sanitized.replace(value, REDACTION_MARKER)
        for pattern in _CREDENTIAL_PATTERNS:
            if pattern.pattern.startswith("(?i)"):
                sanitized = pattern.sub(f"Bearer {REDACTION_MARKER}", sanitized)
            else:
                sanitized = pattern.sub(REDACTION_MARKER, sanitized)
        return sanitized
