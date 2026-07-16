from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tests.dogfood.artifacts import EvidenceBundle, MissionMetadata
from tests.dogfood.pty_runner import PtyRunResult


def test_bundle_persists_sanitized_mission_manifest_and_outcome(
    tmp_path: Path,
) -> None:
    secret = "private learner detail"
    home = tmp_path / "isolated-home"
    bundle = EvidenceBundle(
        tmp_path / "evidence",
        MissionMetadata(
            persona=f"Curious beginner with {secret}",
            mission="Create a mock-mode course through the terminal",
            provider_mode="mock",
            openlearn_home=home,
            command=("openlearn", "menu"),
        ),
        sensitive_values=[secret],
        now=lambda: datetime(2026, 7, 14, 16, 30, tzinfo=UTC),
    )

    initial_manifest = json.loads(
        (tmp_path / "evidence" / "manifest.json").read_text(encoding="utf-8")
    )
    assert initial_manifest["status"] == "running"
    assert (tmp_path / "evidence" / "interactions.jsonl").is_file()

    private_topic = home / "learning-topics" / f"{secret}.md"
    private_topic.parent.mkdir(parents=True)
    private_topic.write_text(
        f"Private contents: {secret}; credential: sk-test-12345678\n",
        encoding="utf-8",
    )
    (home / "state.json").write_text(f'{{"private": "{secret}"}}\n', encoding="utf-8")

    bundle.recorder.record_input(f"typed {secret}")
    frame_path = bundle.capture_frame(
        "Course prompt / decision",
        f"Rendered terminal containing {secret} and sk-test-12345678",
    )
    final_state_path = bundle.capture_final_state()
    bundle.complete(
        PtyRunResult(
            exit_status=0,
            signal_status=None,
            interaction_count=1,
            elapsed_seconds=1.25,
        ),
        achieved=True,
        summary=f"Finished without exposing {secret}",
    )

    manifest_path = tmp_path / "evidence" / "manifest.json"
    interactions_path = tmp_path / "evidence" / "interactions.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    persisted = manifest_path.read_text(encoding="utf-8")
    persisted += interactions_path.read_text(encoding="utf-8")
    persisted += frame_path.read_text(encoding="utf-8")
    persisted += final_state_path.read_text(encoding="utf-8")

    assert secret not in persisted
    assert "sk-test-12345678" not in persisted
    assert frame_path.relative_to(tmp_path / "evidence").as_posix() == (
        "frames/001-course-prompt-decision.txt"
    )
    assert frame_path.read_text(encoding="utf-8") == (
        "Rendered terminal containing [REDACTED] and [REDACTED]"
    )
    assert manifest == {
        "schema_version": 1,
        "started_at": "2026-07-14T16:30:00Z",
        "mission": {
            "persona": "Curious beginner with [REDACTED]",
            "goal": "Create a mock-mode course through the terminal",
        },
        "environment": {
            "provider_mode": "mock",
            "openlearn_home": str(home),
            "command": ["openlearn", "menu"],
        },
        "artifacts": {
            "interactions": "interactions.jsonl",
            "frames": [
                {
                    "label": "Course prompt / decision",
                    "path": "frames/001-course-prompt-decision.txt",
                    "captured_at": "2026-07-14T16:30:00Z",
                }
            ],
            "final_state": "final-state.json",
        },
        "status": "completed",
        "outcome": {
            "achieved": True,
            "summary": "Finished without exposing [REDACTED]",
            "exit_status": 0,
            "signal_status": None,
            "interaction_count": 1,
            "elapsed_seconds": 1.25,
        },
    }
    assert json.loads(final_state_path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "captured_at": "2026-07-14T16:30:00Z",
        "entries": [
            {"path": "learning-topics", "kind": "directory"},
            {"path": "learning-topics/[REDACTED].md", "kind": "file"},
            {"path": "state.json", "kind": "file"},
        ],
    }
