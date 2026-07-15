from __future__ import annotations

import json
from pathlib import Path

from tests.dogfood.evidence import EvidenceRecorder


class FakeClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_recorder_sanitizes_input_and_echoed_terminal_output(tmp_path: Path) -> None:
    evidence_path = tmp_path / "events.jsonl"
    secret = "private learner material"
    recorder = EvidenceRecorder(
        evidence_path,
        sensitive_values=[secret],
        clock=FakeClock(10.0, 10.25, 10.5),
    )

    recorder.record_input(f"import {secret}")
    recorder.record_output(f"openlearn> import {secret}\r\nImported")

    persisted = evidence_path.read_text(encoding="utf-8")
    assert secret not in persisted
    assert read_events(evidence_path) == [
        {
            "schema_version": 1,
            "event": "input",
            "elapsed_seconds": 0.25,
            "text": "import [REDACTED]",
        },
        {
            "schema_version": 1,
            "event": "output",
            "elapsed_seconds": 0.5,
            "text": "openlearn> import [REDACTED]\r\nImported",
        },
    ]


def test_recorder_redacts_credential_shapes_without_explicit_values(tmp_path: Path) -> None:
    evidence_path = tmp_path / "events.jsonl"
    recorder = EvidenceRecorder(evidence_path, clock=FakeClock(2.0, 2.1))

    recorder.record_output(
        "key=sk-or-v1-abcdefghijklmnopqrstuvwxyz Bearer abc.def-0123456789"
    )

    persisted = evidence_path.read_text(encoding="utf-8")
    assert "sk-or-v1-abcdefghijklmnopqrstuvwxyz" not in persisted
    assert "abc.def-0123456789" not in persisted
    assert read_events(evidence_path)[0]["text"] == "key=[REDACTED] Bearer [REDACTED]"


def test_recorder_ignores_empty_sensitive_values(tmp_path: Path) -> None:
    evidence_path = tmp_path / "events.jsonl"
    recorder = EvidenceRecorder(
        evidence_path,
        sensitive_values=["", "specific-secret"],
        clock=FakeClock(4.0, 4.1),
    )

    recorder.record_input("ordinary input with specific-secret")

    assert read_events(evidence_path)[0]["text"] == "ordinary input with [REDACTED]"
