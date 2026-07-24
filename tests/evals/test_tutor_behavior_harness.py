from __future__ import annotations

import json
from pathlib import Path

import pytest

from openlearn import cli
from tests.evals.tutor_behavior import (
    SCENARIOS_DIR,
    load_scenarios,
    run_evaluation,
    validate_live_configuration,
)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def mocked_providers(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    calls: dict[str, list[str]] = {"models": [], "judge_prompts": []}

    def fake_streaming(
        model: str,
        system: str,
        user: str,
        output_func,
    ) -> str:
        calls["models"].append(model)
        return (
            "**Feedback:** You have identified part of the idea.\n"
            "**Lesson:** Let us isolate the missing piece with a small example.\n"
            "**Check:** How would you apply it in a new case?"
        )

    def fake_call(model: str, system: str, user: str) -> str:
        calls["models"].append(model)
        if system == cli.METADATA_EXTRACTOR_SYSTEM:
            return json.dumps(
                {
                    "last_answer_status": "partial",
                    "answer_score": 0.5,
                    "answer_kind": "production",
                    "is_transfer": False,
                    "answer_gap": "the missing prerequisite",
                }
            )
        calls["judge_prompts"].append(user)
        return json.dumps(
            {
                "pass": True,
                "score": 0.9,
                "reason": "The response follows the scenario rubric.",
            }
        )

    monkeypatch.setattr(cli, "call_openai_streaming", fake_streaming)
    monkeypatch.setattr(cli, "call_openai", fake_call)
    return calls


def test_run_evaluation_uses_isolated_homes_and_writes_reviewable_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocked_providers: dict[str, list[str]],
) -> None:
    caller_home = tmp_path / "caller-home"
    caller_home.mkdir()
    marker = caller_home / "keep.txt"
    marker.write_text("untouched", encoding="utf-8")
    monkeypatch.setenv("OPENLEARN_HOME", str(caller_home))
    run_root = tmp_path / "run"
    scenario_ids = [scenario["name"] for scenario in load_scenarios()[:4]]

    outcome = run_evaluation(
        run_root,
        tutor_model="tutor-model",
        judge_model="judge-model",
        scenario_ids=scenario_ids,
    )

    assert outcome.passed is True
    assert outcome.scenario_count == 4
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert cli.project_home() == caller_home

    manifest = json.loads(
        (outcome.evidence_dir / "manifest.json").read_text(encoding="utf-8")
    )
    turns = _read_jsonl(outcome.evidence_dir / "turns.jsonl")
    summary = (outcome.evidence_dir / "summary.md").read_text(encoding="utf-8")

    assert manifest["status"] == "completed"
    assert manifest["outcome"] == {"passed": 4, "failed": 0, "total": 4}
    assert len(turns) == 4
    assert "# Tutor behavior eval" in summary
    assert all(record["persona"] for record in turns)
    assert all(record["learner_message"] for record in turns)
    assert all(record["tutor_response"] for record in turns)
    assert all(record["state_delta"] for record in turns)
    assert all(record["judge"]["pass"] is True for record in turns)
    assert all(record["provenance"]["judge_model"] == "judge-model" for record in turns)
    assert all(
        Path(record["provenance"]["openlearn_home"]).is_relative_to(run_root / "homes")
        for record in turns
    )
    first_metadata, _body = cli.parse_topic(
        (
            run_root
            / "homes"
            / scenario_ids[0]
            / "learning-topics"
            / "variables.md"
        ).read_text(encoding="utf-8")
    )
    assert first_metadata["course_started"] is True
    assert (outcome.evidence_dir / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert (outcome.evidence_dir / "turns.jsonl").stat().st_mode & 0o777 == 0o600
    assert "tutor-model" in mocked_providers["models"]
    assert "judge-model" in mocked_providers["models"]
    assert any(
        "What makes a recursive function stop?" in prompt
        for prompt in mocked_providers["judge_prompts"]
    )


def test_run_evaluation_preserves_failed_verdict_and_redacts_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "nonstandard-provider-token"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    monkeypatch.setattr(
        cli,
        "call_openai_streaming",
        lambda model, system, user, output_func: f"Unsafe echo: {secret}",
    )

    def fake_call(model: str, system: str, user: str) -> str:
        if system == cli.METADATA_EXTRACTOR_SYSTEM:
            return "{}"
        return json.dumps({"pass": False, "score": 0.2, "reason": f"Failed {secret}"})

    monkeypatch.setattr(cli, "call_openai", fake_call)

    outcome = run_evaluation(
        tmp_path / "run",
        tutor_model="tutor-model",
        judge_model="judge-model",
        scenario_ids=["correct_brief_answer"],
    )

    assert outcome.passed is False
    persisted = (outcome.evidence_dir / "turns.jsonl").read_text(encoding="utf-8")
    assert secret not in persisted
    assert "[REDACTED]" in persisted
    assert _read_jsonl(outcome.evidence_dir / "turns.jsonl")[0]["judge"]["pass"] is False


def test_answer_first_scenarios_record_prior_focus_and_same_turn_struggling_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tutor_systems: dict[str, str] = {}
    call_order: list[tuple[str, str]] = []

    def fake_streaming(model: str, system: str, user: str, output_func) -> str:
        call_order.append(("tutor", user))
        tutor_systems[user] = system
        return "**Feedback:**\nTargeted feedback from the updated learner state."

    def fake_call(model: str, system: str, user: str) -> str:
        if system != cli.METADATA_EXTRACTOR_SYSTEM:
            return json.dumps({"pass": True, "score": 0.9, "reason": "Policy followed."})
        scenario = "functions" if "result the function sends back" in user else "pointers"
        call_order.append(("judge", scenario))
        if "result the function sends back" in user:
            return json.dumps(
                {
                    "message_kind": "answer",
                    "last_answer_status": "correct",
                    "answer_score": 1.0,
                    "answer_kind": "production",
                    "is_transfer": True,
                }
            )
        return json.dumps(
            {
                "message_kind": "answer",
                "last_answer_status": "needs_work",
                "answer_score": 0.2,
                "answer_kind": "production",
                "answer_gap": "memory addresses",
            }
        )

    monkeypatch.setattr(cli, "call_openai_streaming", fake_streaming)
    monkeypatch.setattr(cli, "call_openai", fake_call)

    outcome = run_evaluation(
        tmp_path / "run",
        tutor_model="tutor-model",
        judge_model="judge-model",
        scenario_ids=["correct_full_answer", "prerequisite_gap"],
    )
    records = {
        record["scenario"]: record
        for record in _read_jsonl(outcome.evidence_dir / "turns.jsonl")
    }

    correct_events = records["correct_full_answer"]["events"]
    answer_event = next(event for event in correct_events if event["event_type"] == "answer_judged")
    assert answer_event["data"]["current_focus"] == "return values"
    assert call_order[:2] == [
        ("judge", "functions"),
        ("tutor", records["correct_full_answer"]["learner_message"]),
    ]
    gap_system = tutor_systems[
        "I think *p just means the pointer variable's name. I don't really get what an address is."
    ]
    assert "Tier move: struggling" in gap_system
    assert "Address this prerequisite gap before continuing: memory addresses" in gap_system


def test_run_evaluation_rejects_existing_output_root(
    tmp_path: Path,
    mocked_providers: dict[str, list[str]],
) -> None:
    run_root = tmp_path / "existing"
    run_root.mkdir()

    with pytest.raises(ValueError, match="must not already exist"):
        run_evaluation(
            run_root,
            tutor_model="tutor-model",
            judge_model="judge-model",
        )


def test_default_evaluation_requires_at_least_four_scenarios(tmp_path: Path) -> None:
    scenarios_dir = tmp_path / "empty-scenarios"
    scenarios_dir.mkdir()

    with pytest.raises(ValueError, match="at least four"):
        run_evaluation(
            tmp_path / "run",
            tutor_model="tutor-model",
            judge_model="judge-model",
            scenarios_dir=scenarios_dir,
        )


def test_provider_failure_is_preserved_as_failed_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_provider(model: str, system: str, user: str, output_func) -> str:
        raise cli.OpenLearnError("provider unavailable")

    monkeypatch.setattr(cli, "call_openai_streaming", fail_provider)

    outcome = run_evaluation(
        tmp_path / "run",
        tutor_model="tutor-model",
        judge_model="judge-model",
        scenario_ids=["correct_brief_answer"],
    )

    record = _read_jsonl(outcome.evidence_dir / "turns.jsonl")[0]
    assert outcome.passed is False
    assert record["judge"] == {
        "pass": False,
        "score": 0.0,
        "reason": "Harness error: provider unavailable",
        "threshold": 0.7,
    }
    assert record["provenance"]["openlearn_home"].endswith(
        "/homes/correct_brief_answer"
    )


@pytest.mark.parametrize(
    ("tutor_model", "judge_model", "api_key", "mock_enabled", "message"),
    [
        ("same", "same", "key", False, "must differ"),
        ("tutor", "judge", None, False, "API key"),
        ("tutor", "judge", "key", True, "OPENLEARN_MOCK"),
    ],
)
def test_validate_live_configuration_fails_clearly(
    tutor_model: str,
    judge_model: str,
    api_key: str | None,
    mock_enabled: bool,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_live_configuration(
            tutor_model=tutor_model,
            judge_model=judge_model,
            api_key=api_key,
            mock_enabled=mock_enabled,
        )


def test_all_scenarios_name_a_learner_persona() -> None:
    assert len(load_scenarios(SCENARIOS_DIR)) >= 4
    assert all(scenario.get("persona") for scenario in load_scenarios(SCENARIOS_DIR))
