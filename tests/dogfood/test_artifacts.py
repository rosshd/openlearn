from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.dogfood.artifacts import DECISIONS_NAME, EvidenceBundle, MissionMetadata
from tests.dogfood.codex_driver import CodexDecision
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
            "decisions": "decisions.jsonl",
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


def test_bundle_records_exact_bounded_decision_evidence(tmp_path: Path) -> None:
    secret = "do-not-persist"
    bundle = EvidenceBundle(
        tmp_path / "evidence",
        MissionMetadata(
            persona="Beginner",
            mission="Reach the visible outcome",
            provider_mode="mock",
            openlearn_home=tmp_path / "home",
            command=("openlearn", "menu"),
        ),
        sensitive_values=[secret],
        now=lambda: datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
    )

    bundle.record_decision(
        observation_id="observation-0001",
        observation=f"latest {secret}",
        observation_truncated=True,
        observation_original_chars=99,
        decision=CodexDecision(
            action="submit_text",
            text="2",
            commentary=f"reasoning-like {secret}",
        ),
        source_kind="fake",
        turns_remaining=3,
        seconds_remaining=4.5,
        prior_actions=(),
        elapsed_seconds=0.25,
    )

    manifest = json.loads((bundle.root / "manifest.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (bundle.root / DECISIONS_NAME).read_text(encoding="utf-8").splitlines()
    ]
    persisted = (bundle.root / DECISIONS_NAME).read_text(encoding="utf-8")

    assert manifest["artifacts"]["decisions"] == DECISIONS_NAME
    assert records == [
        {
            "schema_version": 1,
            "observation_id": "observation-0001",
            "observation": "latest [REDACTED]",
            "observation_truncated": True,
            "observation_original_chars": 99,
            "prior_actions": [],
            "action": {"action": "submit_text", "text": "2"},
            "source_kind": "fake",
            "turns_remaining": 3,
            "seconds_remaining": 4.5,
            "elapsed_seconds": 0.25,
        }
    ]
    assert secret not in persisted
    assert "commentary" not in persisted
    assert "prompt" not in persisted
    assert "stderr" not in persisted


def test_bundle_rejects_duplicate_observation_ids_and_invalid_source(tmp_path: Path) -> None:
    bundle = EvidenceBundle(
        tmp_path / "evidence",
        MissionMetadata(
            persona="Beginner",
            mission="Goal",
            provider_mode="mock",
            openlearn_home=tmp_path / "home",
            command=("openlearn",),
        ),
        sensitive_values=(),
    )
    kwargs = {
        "observation_id": "observation-0001",
        "observation": "menu",
        "observation_truncated": False,
        "observation_original_chars": 4,
        "decision": CodexDecision(action="press_key", key="enter"),
        "source_kind": "fake",
        "turns_remaining": 1,
        "seconds_remaining": 1.0,
        "prior_actions": (),
        "elapsed_seconds": 0.0,
    }
    bundle.record_decision(**kwargs)

    with pytest.raises(ValueError, match="observation ID"):
        bundle.record_decision(**kwargs)
    with pytest.raises(ValueError, match="source kind"):
        bundle.record_decision(
            **{
                **kwargs,
                "observation_id": "observation-0002",
                "source_kind": "heuristic",
            }
        )
