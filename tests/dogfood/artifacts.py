"""Versioned artifact layout for exploratory terminal missions."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tests.dogfood.evidence import SCHEMA_VERSION, EvidenceRecorder
from tests.dogfood.codex_driver import CodexDecision
from tests.dogfood.pty_runner import PtyRunResult

MANIFEST_NAME = "manifest.json"
INTERACTIONS_NAME = "interactions.jsonl"
FRAMES_DIRECTORY = "frames"
FINAL_STATE_NAME = "final-state.json"
DECISIONS_NAME = "decisions.jsonl"


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
        sensitive_values: Iterable[str],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._now = now
        self._openlearn_home = metadata.openlearn_home
        self.recorder = EvidenceRecorder(
            self.root / INTERACTIONS_NAME,
            sensitive_values=sensitive_values,
        )
        self.recorder.path.touch()
        self._decisions_path = self.root / DECISIONS_NAME
        self._decisions_path.touch()
        self._observation_ids: set[str] = set()
        self._manifest_path = self.root / MANIFEST_NAME
        self._frames: list[dict[str, str]] = []
        self._manifest: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "started_at": _format_timestamp(self._now()),
            "mission": {
                "persona": self.recorder.sanitize(metadata.persona),
                "goal": self.recorder.sanitize(metadata.mission),
            },
            "environment": {
                "provider_mode": self.recorder.sanitize(metadata.provider_mode),
                "openlearn_home": self.recorder.sanitize(str(metadata.openlearn_home)),
                "command": [self.recorder.sanitize(part) for part in metadata.command],
            },
            "artifacts": {
                "interactions": INTERACTIONS_NAME,
                "decisions": DECISIONS_NAME,
                "frames": self._frames,
                "final_state": None,
            },
            "status": "running",
            "outcome": None,
        }
        self._completed = False
        self._write_manifest()

    def record_decision(
        self,
        *,
        observation_id: str,
        observation: str,
        observation_truncated: bool,
        observation_original_chars: int,
        decision: CodexDecision,
        source_kind: str,
        turns_remaining: int,
        seconds_remaining: float,
        prior_actions: tuple[str, ...],
        elapsed_seconds: float,
    ) -> None:
        """Persist one concise action linked to its exact supplied observation."""
        if self._completed:
            raise RuntimeError("cannot record a decision after bundle completion")
        if not re.fullmatch(r"observation-[0-9]{4}", observation_id):
            raise ValueError("invalid observation ID")
        if observation_id in self._observation_ids:
            raise ValueError("observation ID must be immutable and unique")
        if source_kind not in {"fake", "codex"}:
            raise ValueError("decision source kind must be fake or codex")
        if observation_original_chars < len(observation):
            raise ValueError("original observation size is invalid")

        payload = {
            "schema_version": SCHEMA_VERSION,
            "observation_id": observation_id,
            "observation": self.recorder.sanitize_terminal(observation),
            "observation_truncated": observation_truncated,
            "observation_original_chars": observation_original_chars,
            "prior_actions": [self.recorder.sanitize(action) for action in prior_actions],
            "action": _decision_action(decision, self.recorder),
            "source_kind": source_kind,
            "turns_remaining": turns_remaining,
            "seconds_remaining": round(max(0.0, seconds_remaining), 6),
            "elapsed_seconds": round(max(0.0, elapsed_seconds), 6),
        }
        with self._decisions_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
        self._observation_ids.add(observation_id)

    def capture_frame(self, label: str, rendered_output: str) -> Path:
        """Persist a sanitized terminal checkpoint and register it in the manifest."""
        if self._completed:
            raise RuntimeError("cannot capture a frame after bundle completion")
        sanitized_label = self.recorder.sanitize(label).strip()
        if not sanitized_label:
            raise ValueError("frame label must not be empty")

        frame_number = len(self._frames) + 1
        slug = re.sub(r"[^a-z0-9]+", "-", sanitized_label.lower()).strip("-")
        slug = slug[:60].rstrip("-") or "checkpoint"
        relative_path = Path(FRAMES_DIRECTORY) / f"{frame_number:03}-{slug}.txt"
        frame_path = self.root / relative_path
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = frame_path.with_suffix(".txt.tmp")
        temporary_path.write_text(
            self.recorder.sanitize_terminal(rendered_output),
            encoding="utf-8",
        )
        temporary_path.replace(frame_path)

        self._frames.append(
            {
                "label": sanitized_label,
                "path": relative_path.as_posix(),
                "captured_at": _format_timestamp(self._now()),
            }
        )
        self._write_manifest()
        return frame_path

    def capture_final_state(self) -> Path:
        """Persist a sanitized inventory of the isolated home without file contents."""
        if self._completed:
            raise RuntimeError("cannot capture final state after bundle completion")
        if not self._openlearn_home.is_dir():
            raise RuntimeError("isolated OPENLEARN_HOME does not exist")

        entries = []
        for path in sorted(self._openlearn_home.rglob("*")):
            relative_path = path.relative_to(self._openlearn_home).as_posix()
            if path.is_symlink():
                kind = "symlink"
            elif path.is_dir():
                kind = "directory"
            elif path.is_file():
                kind = "file"
            else:
                kind = "other"
            entries.append(
                {
                    "path": self.recorder.sanitize(relative_path),
                    "kind": kind,
                }
            )

        final_state_path = self.root / FINAL_STATE_NAME
        temporary_path = final_state_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "captured_at": _format_timestamp(self._now()),
                    "entries": entries,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(final_state_path)
        artifacts = self._manifest["artifacts"]
        assert isinstance(artifacts, dict)
        artifacts["final_state"] = FINAL_STATE_NAME
        self._write_manifest()
        return final_state_path

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

    def fail(self, summary: str) -> None:
        """Finalize a mission that failed before a process result was available."""
        if self._completed:
            raise RuntimeError("evidence bundle is already complete")
        self._manifest["status"] = "failed"
        self._manifest["outcome"] = {
            "achieved": False,
            "summary": self.recorder.sanitize(summary),
            "exit_status": None,
            "signal_status": None,
            "interaction_count": None,
            "elapsed_seconds": None,
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


def _decision_action(
    decision: CodexDecision,
    recorder: EvidenceRecorder,
) -> dict[str, str]:
    if decision.action == "submit_text":
        assert decision.text is not None
        return {"action": decision.action, "text": recorder.sanitize(decision.text)}
    if decision.action == "press_key":
        assert decision.key is not None
        return {"action": decision.action, "key": decision.key}
    assert decision.reason is not None
    return {"action": decision.action, "reason": recorder.sanitize(decision.reason)}
