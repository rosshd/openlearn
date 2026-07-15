"""Versioned artifact layout for exploratory terminal missions."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tests.dogfood.evidence import SCHEMA_VERSION, EvidenceRecorder
from tests.dogfood.pty_runner import PtyRunResult

MANIFEST_NAME = "manifest.json"
INTERACTIONS_NAME = "interactions.jsonl"


@dataclass(frozen=True)
class MissionMetadata:
    """Allow-listed mission context safe for the evidence manifest."""

    persona: str
    mission: str
    provider_mode: str
    openlearn_home: Path
    command: tuple[str, ...]


class EvidenceBundle:
    """Own a mission's manifest and sanitized interaction stream."""

    def __init__(
        self,
        root: Path,
        metadata: MissionMetadata,
        *,
        sensitive_values: Iterable[str] = (),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.recorder = EvidenceRecorder(
            self.root / INTERACTIONS_NAME,
            sensitive_values=sensitive_values,
        )
        self.recorder.path.touch()
        self._manifest_path = self.root / MANIFEST_NAME
        self._manifest: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "started_at": _format_timestamp(now()),
            "mission": {
                "persona": self.recorder.sanitize(metadata.persona),
                "goal": self.recorder.sanitize(metadata.mission),
            },
            "environment": {
                "provider_mode": self.recorder.sanitize(metadata.provider_mode),
                "openlearn_home": self.recorder.sanitize(str(metadata.openlearn_home)),
                "command": [self.recorder.sanitize(part) for part in metadata.command],
            },
            "artifacts": {"interactions": INTERACTIONS_NAME},
            "status": "running",
            "outcome": None,
        }
        self._completed = False
        self._write_manifest()

    def complete(
        self,
        result: PtyRunResult,
        *,
        achieved: bool,
        summary: str,
    ) -> None:
        """Finalize the manifest with the learner outcome and process metrics."""
        if self._completed:
            raise RuntimeError("evidence bundle is already complete")
        self._manifest["status"] = "completed"
        self._manifest["outcome"] = {
            "achieved": achieved,
            "summary": self.recorder.sanitize(summary),
            "exit_status": result.exit_status,
            "signal_status": result.signal_status,
            "interaction_count": result.interaction_count,
            "elapsed_seconds": result.elapsed_seconds,
        }
        self._write_manifest()
        self._completed = True

    def _write_manifest(self) -> None:
        temporary_path = self._manifest_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(self._manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self._manifest_path)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("manifest timestamp must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
