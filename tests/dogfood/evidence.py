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
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), REDACTION_MARKER),
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
        f"Bearer {REDACTION_MARKER}",
    ),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), REDACTION_MARKER),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), REDACTION_MARKER),
    (
        re.compile(
            r"(?i)\b((?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://)"
            r"[^/@\s:]+:[^/@\s]+@"
        ),
        rf"\1{REDACTION_MARKER}@",
    ),
)
_OSC_SEQUENCE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_SEQUENCE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_UNSAFE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class EvidenceRecorder:
    """Append sanitized terminal interactions to a versioned JSONL stream."""

    def __init__(
        self,
        path: Path,
        *,
        sensitive_values: Iterable[str],
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

    def sanitize(self, text: str) -> str:
        """Return text with the same redaction rules used for persisted events."""
        sanitized = text
        for value in self._sensitive_values:
            sanitized = sanitized.replace(value, REDACTION_MARKER)
        for pattern, replacement in _CREDENTIAL_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized

    def sanitize_terminal(self, text: str) -> str:
        """Redact secrets and remove terminal controls before persistence."""
        sanitized = text.replace("\r\n", "\n").replace("\r", "\n")
        sanitized = _OSC_SEQUENCE.sub("", sanitized)
        sanitized = _ANSI_SEQUENCE.sub("", sanitized)
        sanitized = _UNSAFE_CONTROL.sub("", sanitized)
        return self.sanitize(sanitized)

    def _record(self, event: str, text: str) -> None:
        sanitized = self.sanitize_terminal(text) if event == "output" else self.sanitize(text)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "event": event,
            "elapsed_seconds": round(max(0.0, self._clock() - self._started_at), 6),
            "text": sanitized,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
