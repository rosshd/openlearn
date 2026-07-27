from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from openlearn import cli
from tests.evals.test_tutor_behavior_harness import _judge_payload, _read_jsonl
from tests.evals.tutor_behavior import (
    MULTI_TURN_SCENARIOS_DIR,
    _multi_turn_replay_fingerprint,
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
        "turn_rubrics": [
            ["The tutor targets the lexical-scope gap with one bounded scaffold."],
            ["The tutor responds to the repaired prerequisite without reteaching it."],
        ],
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
    by_name = {scenario["name"]: scenario for scenario in scenarios}
    for name in (
        "repeated_miss_fading",
        "deferred_review",
        "copied_answer_transfer",
    ):
        generated_count = sum(
            turn["role"] == "assistant" and turn["content"] is None
            for turn in by_name[name]["turns"]
        )
        assert len(by_name[name]["turn_rubrics"]) == generated_count


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
                "last_answer_status": "partial",
                "answer_score": 0.5,
                "answer_kind": "production",
                "is_transfer": False,
            },
        )
    )
    tutor_systems: list[str] = []
    judge_prompts: list[str] = []

    def fake_streaming(model: str, system: str, user: str, output_func) -> str:
        tutor_systems.append(system)
        if "The blocking prerequisite is lexical scope" in system:
            return "**Check:** Apply lexical scope to a new closure."
        return "**Check:** Identify the binding this closure retains."

    def fake_call(model: str, system: str, user: str) -> str:
        if system == cli.METADATA_EXTRACTOR_SYSTEM:
            return json.dumps(next(judgments))
        judge_prompts.append(user)
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
    assert turns[1]["judgment"]["status"] == "partial"
    assert turns[1]["state_before_sha256"] == turns[0]["state_after_sha256"]
    assert turns[1]["prior_state_sha256"] == turns[0]["state_after_sha256"]
    assert turns[1]["prior_state_used"] is True
    assert "lexical scope" in tutor_systems[0]
    assert "lexical scope" in tutor_systems[1]
    assert "The blocking prerequisite is lexical scope" in tutor_systems[1]
    assert turns[0]["selected_move"]["name"] == "remediation_hint"
    assert turns[1]["selected_move"]["name"] == "worked_example"
    assert turns[1]["actual_system_prompt_sha256"] == hashlib.sha256(
        tutor_systems[1].encode("utf-8")
    ).hexdigest()
    assert turns[1]["actual_system_prompt_replay_sha256"]
    assert record["sequence_judge"]["pass"] is True
    assert "The tutor targets the lexical-scope gap" in judge_prompts[0]
    assert "repaired prerequisite" not in judge_prompts[0]
    assert "repaired prerequisite" in judge_prompts[1]
    assert "completed adaptive sequence as a whole" in judge_prompts[2]
    assert "The tutor repairs the lexical-scope prerequisite" in judge_prompts[2]
    assert "Across the sequence, every tutor response" in judge_prompts[2]
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


@pytest.mark.parametrize("failure_turn", [1, 2])
def test_multi_turn_tutor_provider_failure_preserves_partial_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_turn: int,
) -> None:
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(scenarios_dir, _minimal_scenario())
    tutor_calls = 0

    def fake_streaming(model: str, system: str, user: str, output_func) -> str:
        nonlocal tutor_calls
        tutor_calls += 1
        if tutor_calls == failure_turn:
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
    assert len(record["turns"]) == failure_turn
    if failure_turn == 2:
        assert record["turns"][0]["tutor_output"]
    failed_turn = record["turns"][-1]
    assert failed_turn["learner_input"]
    assert failed_turn["tutor_output"] == ""
    assert failed_turn["error"] == {
        "stage": "tutor_provider",
        "message": "provider unavailable",
    }
    assert failed_turn["events"] == []
    assert failed_turn["actual_system_prompt_sha256"]
    assert failed_turn["judgment"]["graded"] is False
    assert record["partial_evidence"] is True
    assert record["replay_fingerprint"]


def test_judge_provider_failure_retains_current_tutor_evidence_and_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(scenarios_dir, _minimal_scenario())

    monkeypatch.setattr(
        cli,
        "call_openai_streaming",
        lambda model, system, user, output_func: (
            "**Check:** Apply lexical scope to a new closure."
        ),
    )

    def fake_call(model: str, system: str, user: str) -> str:
        if system == cli.METADATA_EXTRACTOR_SYSTEM:
            return json.dumps(
                {
                    "message_kind": "answer",
                    "last_answer_status": "needs_work",
                    "answer_score": 0.2,
                    "answer_kind": "production",
                    "answer_gap": "lexical scope",
                }
            )
        raise cli.OpenLearnError("judge unavailable")

    monkeypatch.setattr(cli, "call_openai", fake_call)

    outcome = run_evaluation(
        tmp_path / "run",
        tutor_model="tutor-model",
        judge_model="judge-model",
        scenario_ids=["two_turn_adaptation"],
        scenarios_dir=scenarios_dir,
    )
    record = _read_jsonl(outcome.evidence_dir / "turns.jsonl")[0]
    failed_turn = record["turns"][0]

    assert outcome.passed is False
    assert len(record["turns"]) == 1
    assert failed_turn["tutor_output"]
    assert failed_turn["events"]
    assert failed_turn["state_delta"]
    assert failed_turn["actual_system_prompt_sha256"]
    assert failed_turn["judgment"]["graded"] is True
    assert failed_turn["error"] == {
        "stage": "turn_judge_provider",
        "message": "judge unavailable",
    }
    assert record["partial_evidence"] is True
    assert record["replay_fingerprint"]


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
    pending_created_dates = []
    for index, mocked_today in enumerate(("2025-02-03", "2031-11-19")):
        monkeypatch.setattr(cli, "today", lambda value=mocked_today: value)
        install_mocks()
        outcome = run_evaluation(
            tmp_path / f"run-{index}",
            tutor_model="tutor-model",
            judge_model="judge-model",
            scenario_ids=["two_turn_adaptation"],
            scenarios_dir=scenarios_dir,
        )
        record = _read_jsonl(outcome.evidence_dir / "turns.jsonl")[0]
        fingerprints.append(record["replay_fingerprint"])
        pending_created_dates.append(
            record["turns"][0]["policy_state"]["pending_question"]["created"]
        )

    assert pending_created_dates == ["2025-02-03", "2031-11-19"]
    assert fingerprints[0] == fingerprints[1]


def test_replay_fingerprint_changes_for_semantic_evidence() -> None:
    record = {
        "schema_version": 2,
        "suite": "multi-turn",
        "contract_version": "adaptive-sequence-v1",
        "scenario": "adaptive",
        "family": "prerequisite_repair",
        "rubric_version": "test",
        "rubric": ["repair before resuming"],
        "assessment_mode": False,
        "assessment_item_count": {"min": 1, "max": 1},
        "turns": [
            {
                "turn": 1,
                "learner_input": "attempt",
                "tutor_output": "Review this on 2026-01-04.",
                "selected_move": {"name": "feedback"},
                "judgment": {"graded": True, "score": 0.2},
                "judge": {
                    "pass": True,
                    "score": 0.9,
                    "evidence": "Review is scheduled for 2026-01-04.",
                },
                "rubric": ["give bounded feedback"],
                "events": [
                    {
                        "event_type": "answer_judged",
                        "data": {"score": 0.2, "review_due": "2026-01-04"},
                    }
                ],
                "actual_system_prompt_replay_sha256": "prompt-a",
                "state_before_replay_sha256": "state-a",
                "state_after_replay_sha256": "state-b",
                "policy_state": {
                    "pending_remediation": {"stage": "hint"},
                    "review_due": [{"due": "2026-01-04"}],
                },
            }
        ],
        "sequence_judge": {"pass": True, "score": 0.9},
        "state_assertions": {"pass": True},
        "event_assertions": {"pass": True},
        "provenance": {
            "fixture_sha256": "fixture-a",
            "openlearn_home": "/tmp/run-a/home",
        },
    }
    mutations = []
    changed_event = deepcopy(record)
    changed_event["turns"][0]["events"][0]["event_type"] = "mastery_changed"
    mutations.append(changed_event)
    changed_event_date = deepcopy(record)
    changed_event_date["turns"][0]["events"][0]["data"][
        "review_due"
    ] = "2026-01-05"
    mutations.append(changed_event_date)
    changed_output = deepcopy(record)
    changed_output["turns"][0]["tutor_output"] = "Review this on 2026-01-05."
    mutations.append(changed_output)
    changed_due = deepcopy(record)
    changed_due["turns"][0]["policy_state"]["review_due"][0]["due"] = "2026-01-05"
    mutations.append(changed_due)
    changed_rubric = deepcopy(record)
    changed_rubric["rubric"][0] = "defer before resuming"
    mutations.append(changed_rubric)
    changed_fixture = deepcopy(record)
    changed_fixture["provenance"]["fixture_sha256"] = "fixture-b"
    mutations.append(changed_fixture)
    changed_judge = deepcopy(record)
    changed_judge["turns"][0]["judge"][
        "evidence"
    ] = "Review is scheduled for 2026-01-05."
    mutations.append(changed_judge)
    changed_prompt = deepcopy(record)
    changed_prompt["turns"][0]["actual_system_prompt_replay_sha256"] = "prompt-b"
    mutations.append(changed_prompt)
    changed_state = deepcopy(record)
    changed_state["turns"][0]["state_after_replay_sha256"] = "state-c"
    mutations.append(changed_state)
    changed_assertion = deepcopy(record)
    changed_assertion["event_assertions"]["pass"] = False
    mutations.append(changed_assertion)

    baseline = _multi_turn_replay_fingerprint(record)
    assert all(
        _multi_turn_replay_fingerprint(changed) != baseline for changed in mutations
    )


def test_replay_fingerprint_ignores_volatile_timestamps_paths_and_ids() -> None:
    record = {
        "schema_version": 2,
        "suite": "multi-turn",
        "contract_version": "adaptive-sequence-v1",
        "scenario": "adaptive",
        "family": "prerequisite_repair",
        "rubric_version": "test",
        "rubric": ["repair before resuming"],
        "turns": [
            {
                "turn": 1,
                "learner_input": "attempt",
                "tutor_output": "feedback",
                "selected_move": {"name": "feedback"},
                "judgment": {"graded": True, "score": 0.2},
                "judge": {"pass": True, "score": 0.9},
                "events": [
                    {
                        "event_type": "answer_judged",
                        "event_id": "turn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0",
                        "ts": "2026-01-01T10:00:00+00:00",
                        "data": {
                            "created": "2026-01-01",
                            "path": "/tmp/run-a/home/topic.md",
                        },
                    }
                ],
                "actual_system_prompt_replay_sha256": "prompt-a",
                "state_before_replay_sha256": "state-a",
                "state_after_replay_sha256": "state-b",
                "policy_state": {
                    "created": "2026-01-01",
                    "pending_question": {
                        "question": "Try once.",
                        "created": "2026-01-01",
                    },
                    "pending_remediation": {
                        "stage": "hint",
                        "created": "2026-01-01",
                    },
                    "review_due": [{"due": "2026-01-02"}],
                },
            }
        ],
        "sequence_judge": {"pass": True, "score": 0.9},
        "state_assertions": {"pass": True},
        "event_assertions": {"pass": True},
        "provenance": {
            "fixture_sha256": "fixture-a",
            "openlearn_home": "/tmp/run-a/home",
        },
    }
    replay = deepcopy(record)
    replay["turns"][0]["events"][0]["ts"] = "2027-04-05T11:30:00+00:00"
    replay["turns"][0]["events"][0][
        "event_id"
    ] = "turn_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:0"
    replay["turns"][0]["events"][0]["data"][
        "path"
    ] = "/private/tmp/run-b/home/topic.md"
    replay["turns"][0]["policy_state"]["created"] = "2027-04-05"
    replay["turns"][0]["policy_state"]["pending_question"]["created"] = "2027-04-05"
    replay["turns"][0]["policy_state"]["pending_remediation"][
        "created"
    ] = "2027-04-05"
    replay["provenance"]["openlearn_home"] = "/private/tmp/run-b/home"

    assert _multi_turn_replay_fingerprint(record) == _multi_turn_replay_fingerprint(
        replay
    )


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
