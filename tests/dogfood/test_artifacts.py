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

    bundle.recorder.record_input(f"typed {secret}")
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

    assert secret not in persisted
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
        "artifacts": {"interactions": "interactions.jsonl"},
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
