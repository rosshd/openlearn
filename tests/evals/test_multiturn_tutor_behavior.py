from __future__ import annotations

import json
from pathlib import Path

import pytest

from openlearn import cli
from tests.evals.test_tutor_behavior_harness import _judge_payload, _read_jsonl
from tests.evals.tutor_behavior import (
    MULTI_TURN_SCENARIOS_DIR,
    load_scenarios,
    main,
    run_evaluation,
)


REQUIRED_FAMILIES = {
    "repeated_miss_fading",
    "copied_answer_transfer",
    "advanced_learner",
    "explicit_navigation",
    "off_topic_return",
    "learner_context_fidelity",
    "answer_seeking",
    "prerequisite_repair",
}


def _write_scenario(directory: Path, scenario: dict[str, object]) -> None:
    directory.mkdir()
    (directory / f"{scenario['name']}.json").write_text(
        json.dumps(scenario),
        encoding="utf-8",
    )


def _minimal_scenario() -> dict[str, object]:
    return {
        "name": "two_turn_adaptation",
        "family": "prerequisite_repair",
        "persona": "A learner who first exposes and then repairs a prerequisite gap.",
        "description": "The second move must use the state saved by the first move.",
        "topic": "closures",
        "goal": "Understand closures",
        "turns": [
            {"role": "assistant", "content": "What does a closure retain?"},
            {"role": "user", "content": "I think it retains the function name."},
            {"role": "assistant", "content": None},
            {"role": "user", "content": "It retains bindings from its lexical scope."},
            {"role": "assistant", "content": None},
        ],
        "rubric": ["The tutor repairs the lexical-scope prerequisite."],
    }


def test_multi_turn_fixtures_cover_every_required_family() -> None:
    scenarios = load_scenarios(MULTI_TURN_SCENARIOS_DIR)

    assert len(scenarios) >= 8
    assert {scenario["family"] for scenario in scenarios} == REQUIRED_FAMILIES
    for scenario in scenarios:
        turns = scenario["turns"]
        assert sum(turn["role"] == "user" for turn in turns) >= 2
        assert sum(
            turn["role"] == "assistant" and turn["content"] is None for turn in turns
        ) >= 2
    deferred = next(
        scenario for scenario in scenarios if scenario["name"] == "deferred_review"
    )
    assert "deferred" in deferred["description"]
    assert any("scheduled for review" in item for item in deferred["rubric"])


def test_multi_turn_run_records_ordered_state_linked_turn_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(scenarios_dir, _minimal_scenario())
    caller_home = tmp_path / "caller-home"
    caller_home.mkdir()
    marker = caller_home / "keep.txt"
    marker.write_text("untouched", encoding="utf-8")
    monkeypatch.setenv("OPENLEARN_HOME", str(caller_home))
    monkeypatch.setenv("OPENAI_API_KEY", "private-eval-credential")
    tutor_systems: list[str] = []
    judgments = iter(
        (
            {
                "message_kind": "answer",
                "last_answer_status": "needs_work",
                "answer_score": 0.2,
                "answer_kind": "production",
                "is_transfer": False,
                "answer_gap": "lexical scope",
            },
            {
                "message_kind": "answer",
                "last_answer_status": "correct",
                "answer_score": 0.9,
                "answer_kind": "production",
                "is_transfer": True,
            },
        )
    )

    def fake_streaming(model: str, system: str, user: str, output_func) -> str:
        tutor_systems.append(system)
        return (
            "**Check:** Apply the idea to a new closure."
            if len(tutor_systems) == 1
            else "**Next:** Press Enter to continue, or type what you want more help with."
        )

    def fake_call(model: str, system: str, user: str) -> str:
        if system == cli.METADATA_EXTRACTOR_SYSTEM:
            return json.dumps(next(judgments))
        return json.dumps(_judge_payload())

    monkeypatch.setattr(cli, "call_openai_streaming", fake_streaming)
    monkeypatch.setattr(cli, "call_openai", fake_call)

    outcome = run_evaluation(
        tmp_path / "run",
        tutor_model="tutor-model",
        judge_model="judge-model",
        scenario_ids=["two_turn_adaptation"],
        scenarios_dir=scenarios_dir,
    )
    record = _read_jsonl(outcome.evidence_dir / "turns.jsonl")[0]
    turns = record["turns"]

    assert outcome.passed is True
    assert len(turns) == 2
    assert all(
        {
            "learner_input",
            "tutor_output",
            "selected_move",
            "judgment",
            "state_delta",
            "events",
        }
        <= set(turn)
        for turn in turns
    )
    assert turns[0]["judgment"]["status"] == "needs_work"
    assert turns[1]["judgment"]["status"] == "correct"
    assert turns[1]["state_before_sha256"] == turns[0]["state_after_sha256"]
    assert turns[1]["prior_state_sha256"] == turns[0]["state_after_sha256"]
    assert turns[1]["prior_state_used"] is True
    assert "lexical scope" in tutor_systems[0]
    assert turns[0]["selected_move"]["name"] == "remediation_hint"
    assert turns[1]["selected_move"]["name"] in {"transfer_check", "advance"}
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert Path(record["provenance"]["openlearn_home"]).is_relative_to(
        tmp_path / "run" / "homes"
    )
    assert "private-eval-credential" not in (
        outcome.evidence_dir / "turns.jsonl"
    ).read_text(encoding="utf-8")


def test_navigation_turn_clears_gate_without_answer_judgment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _minimal_scenario()
    scenario["name"] = "navigation"
    scenario["state"] = {
        "pending_question": {
            "kind": "free_response",
            "question": "Explain closures.",
            "created": "2026-01-01",
        },
        "last_answer_status": "needs_work",
        "consecutive_misses": 2,
        "pending_remediation": {
            "concept_id": "closures",
            "label": "closures",
            "stage": "worked_example",
            "misses": 2,
            "minimum_score": 0.7,
        },
    }
    scenario["turns"] = [
        {"role": "user", "content": "Skip this and move on."},
        {"role": "assistant", "content": None},
        {"role": "user", "content": "What are we learning now?"},
        {"role": "assistant", "content": None},
    ]
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(scenarios_dir, scenario)
    extractor_calls: list[str] = []

    monkeypatch.setattr(
        cli,
        "call_openai_streaming",
        lambda model, system, user, output_func: (
            "**Next:** Press Enter to continue, or type what you want more help with."
        ),
    )

    def fake_call(model: str, system: str, user: str) -> str:
        if system == cli.METADATA_EXTRACTOR_SYSTEM:
            extractor_calls.append(user)
            return json.dumps({"message_kind": "question"})
        return json.dumps(_judge_payload())

    monkeypatch.setattr(cli, "call_openai", fake_call)

    outcome = run_evaluation(
        tmp_path / "run",
        tutor_model="tutor-model",
        judge_model="judge-model",
        scenario_ids=["navigation"],
        scenarios_dir=scenarios_dir,
    )
    turns = _read_jsonl(outcome.evidence_dir / "turns.jsonl")[0]["turns"]

    assert outcome.passed is True
    assert turns[0]["selected_move"]["name"] == "navigation"
    assert turns[0]["judgment"]["graded"] is False
    assert all(event["event_type"] != "answer_judged" for event in turns[0]["events"])
    assert turns[0]["state_delta"]["pending_remediation"]["after"] is None
    assert not any("Skip this and move on." in prompt for prompt in extractor_calls)


def test_multi_turn_provider_failure_preserves_partial_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(scenarios_dir, _minimal_scenario())
    tutor_calls = 0

    def fake_streaming(model: str, system: str, user: str, output_func) -> str:
        nonlocal tutor_calls
        tutor_calls += 1
        if tutor_calls == 2:
            raise cli.OpenLearnError("provider unavailable")
        return "**Check:** Try the same idea with a different closure."

    monkeypatch.setattr(cli, "call_openai_streaming", fake_streaming)
    monkeypatch.setattr(
        cli,
        "call_openai",
        lambda model, system, user: (
            json.dumps(_judge_payload())
            if system != cli.METADATA_EXTRACTOR_SYSTEM
            else json.dumps(
                {
                    "message_kind": "answer",
                    "last_answer_status": "needs_work",
                    "answer_score": 0.2,
                    "answer_kind": "production",
                }
            )
        ),
    )

    outcome = run_evaluation(
        tmp_path / "run",
        tutor_model="tutor-model",
        judge_model="judge-model",
        scenario_ids=["two_turn_adaptation"],
        scenarios_dir=scenarios_dir,
    )
    record = _read_jsonl(outcome.evidence_dir / "turns.jsonl")[0]

    assert outcome.passed is False
    assert len(record["turns"]) == 2
    assert record["turns"][0]["tutor_output"]
    assert record["turns"][1]["tutor_output"] == ""
    assert record["turns"][1]["error"] == "provider unavailable"
    assert record["partial_evidence"] is True


def test_mocked_replay_has_stable_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(scenarios_dir, _minimal_scenario())

    def install_mocks() -> None:
        judgments = iter(
            (
                {
                    "message_kind": "answer",
                    "last_answer_status": "needs_work",
                    "answer_score": 0.2,
                    "answer_kind": "production",
                },
                {
                    "message_kind": "answer",
                    "last_answer_status": "correct",
                    "answer_score": 0.9,
                    "answer_kind": "production",
                    "is_transfer": True,
                },
            )
        )
        monkeypatch.setattr(
            cli,
            "call_openai_streaming",
            lambda model, system, user, output_func: (
                "**Check:** Apply the idea to a novel case."
            ),
        )
        monkeypatch.setattr(
            cli,
            "call_openai",
            lambda model, system, user: (
                json.dumps(_judge_payload())
                if system != cli.METADATA_EXTRACTOR_SYSTEM
                else json.dumps(next(judgments))
            ),
        )

    fingerprints = []
    for index in range(2):
        install_mocks()
        outcome = run_evaluation(
            tmp_path / f"run-{index}",
            tutor_model="tutor-model",
            judge_model="judge-model",
            scenario_ids=["two_turn_adaptation"],
            scenarios_dir=scenarios_dir,
        )
        fingerprints.append(
            _read_jsonl(outcome.evidence_dir / "turns.jsonl")[0][
                "replay_fingerprint"
            ]
        )

    assert fingerprints[0] == fingerprints[1]


def test_cli_can_select_multi_turn_suite_and_one_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "tests.evals.tutor_behavior.validate_live_configuration",
        lambda **kwargs: None,
    )

    def fake_run(run_root: Path, **kwargs):
        captured.update(kwargs)
        return type(
            "Outcome",
            (),
            {
                "passed": True,
                "scenario_count": 1,
                "failure_count": 0,
                "evidence_dir": tmp_path / "evidence",
            },
        )()

    monkeypatch.setattr("tests.evals.tutor_behavior.run_evaluation", fake_run)

    result = main(
        [
            str(tmp_path / "run"),
            "--judge-model",
            "judge-model",
            "--suite",
            "multi-turn",
            "--scenario",
            "explicit_navigation",
        ]
    )

    assert result == 0
    assert captured["scenarios_dir"] == MULTI_TURN_SCENARIOS_DIR
    assert captured["scenario_ids"] == ["explicit_navigation"]
    assert "scenarios=1" in capsys.readouterr().out


def test_cli_runs_full_multi_turn_suite_when_no_scenario_is_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "tests.evals.tutor_behavior.validate_live_configuration",
        lambda **kwargs: None,
    )

    def fake_run(run_root: Path, **kwargs):
        captured.update(kwargs)
        return type(
            "Outcome",
            (),
            {
                "passed": True,
                "scenario_count": 9,
                "failure_count": 0,
                "evidence_dir": tmp_path / "evidence",
            },
        )()

    monkeypatch.setattr("tests.evals.tutor_behavior.run_evaluation", fake_run)

    result = main(
        [
            str(tmp_path / "run"),
            "--judge-model",
            "judge-model",
            "--suite",
            "multi-turn",
        ]
    )

    assert result == 0
    assert captured["scenarios_dir"] == MULTI_TURN_SCENARIOS_DIR
    assert captured["scenario_ids"] is None
