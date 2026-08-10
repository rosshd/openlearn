from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.evals.outcome import (
    OUTCOME_BASELINE_PATH,
    OUTCOME_CONTRACT_VERSION,
    OUTCOME_SCHEMA_VERSION,
    OUTCOME_SCENARIOS_DIR,
    SIMULATED_START,
    aggregate_metrics,
    outcome_record,
    run_outcome_evaluation,
    scenario_metrics,
    validate_outcome_scenarios,
)
from tests.evals.tutor_behavior import EvaluationOutcome, load_scenarios


def _event(
    event_type: str,
    *,
    day: int,
    concept: str,
    **data: object,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "ts": f"2026-01-{day:02d}T12:00:00+00:00",
        "event_type": event_type,
        "slug": "outcome",
        "data": {"concept_id": concept, **data},
    }


def _turn(
    number: int,
    *,
    events: list[dict[str, object]],
    move: str,
    tutor_output: str,
    probe: str = "practice",
) -> dict[str, object]:
    return {
        "turn": number,
        "learner_input": f"learner answer {number}",
        "tutor_output": tutor_output,
        "selected_move": {"name": move},
        "events": events,
        "outcome_schedule": {
            "gap_days_before": 0,
            "probe": probe,
            "simulated_at": f"2026-01-{number:02d}T12:00:00+00:00",
        },
    }


def test_outcome_fixtures_cover_required_result_patterns() -> None:
    scenarios = load_scenarios(OUTCOME_SCENARIOS_DIR)

    validate_outcome_scenarios(scenarios)

    by_name = {scenario["name"]: scenario for scenario in scenarios}
    delayed = by_name["immediate_success_delayed_failure"]
    assert [item["gap_days_before"] for item in delayed["outcome_schedule"]] == [
        0,
        7,
    ]
    assert [item["probe"] for item in delayed["outcome_schedule"]] == [
        "practice",
        "retrieval",
    ]
    transfer = by_name["contingent_tutoring_novel_transfer"]
    assert any(item["probe"] == "transfer" for item in transfer["outcome_schedule"])
    assert len(transfer["turns"]) >= 7


def test_outcome_record_projects_time_gaps_without_sleeping() -> None:
    fixture = load_scenarios(OUTCOME_SCENARIOS_DIR)[1]
    behavior_record = {
        "scenario": fixture["name"],
        "family": fixture["family"],
        "description": fixture["description"],
        "judge": {"pass": True},
        "partial_evidence": False,
        "provenance": {"fixture": "fixture.json"},
        "turns": [
            _turn(
                1,
                events=[
                    _event(
                        "answer_judged",
                        day=27,
                        concept="binary-search-invariant",
                        status="correct",
                        score=0.9,
                        answer_kind="production",
                        source="review",
                    )
                ],
                move="transfer_check",
                tutor_output="**Check:** Try another interval.",
            ),
            _turn(
                2,
                events=[
                    _event(
                        "answer_judged",
                        day=27,
                        concept="binary-search-invariant",
                        status="needs_work",
                        score=0.2,
                        answer_kind="production",
                        source="review",
                    )
                ],
                move="remediation_hint",
                tutor_output="**Hint:** Focus on the active interval.",
            ),
        ],
    }

    record = outcome_record(behavior_record, fixture)

    first, second = record["turns"]
    assert first["events"][0]["ts"] == SIMULATED_START.isoformat()
    assert second["events"][0]["ts"] == "2026-01-08T12:00:00+00:00"
    assert record["metrics"]["delayed_retrieval"] == {
        "attempts": 1,
        "passed": 0,
        "pass_rate": 0.0,
        "by_concept": {
            "binary-search-invariant": {"attempts": 1, "passed": 0}
        },
    }


def test_metrics_surface_efficiency_dependency_and_false_mastery() -> None:
    repeated_probe = "**Check:** Explain the invariant again."
    turns = [
        _turn(
            1,
            events=[
                _event(
                    "answer_judged",
                    day=1,
                    concept="alpha",
                    status="correct",
                    score=0.9,
                    answer_kind="production",
                    source="review",
                ),
                _event(
                    "mastery_changed",
                    day=1,
                    concept="alpha",
                    mastered=True,
                ),
            ],
            move="transfer_check",
            tutor_output=repeated_probe,
        ),
        _turn(
            2,
            events=[
                _event(
                    "answer_judged",
                    day=8,
                    concept="alpha",
                    status="needs_work",
                    score=0.2,
                    answer_kind="production",
                    source="review",
                )
            ],
            move="feedback",
            tutor_output=repeated_probe,
            probe="retrieval",
        ),
        _turn(
            3,
            events=[
                _event(
                    "answer_judged",
                    day=8,
                    concept="beta",
                    status="needs_work",
                    score=0.2,
                    answer_kind="production",
                    source="review",
                ),
                _event(
                    "concept_deferred",
                    day=8,
                    concept="beta",
                ),
            ],
            move="remediation_hint",
            tutor_output="**Check:** Apply it to beta.",
        ),
        _turn(
            4,
            events=[
                _event(
                    "answer_judged",
                    day=10,
                    concept="beta",
                    status="correct",
                    score=0.9,
                    answer_kind="production",
                    is_transfer=True,
                    source="review",
                ),
                _event(
                    "mastery_changed",
                    day=10,
                    concept="beta",
                    mastered=True,
                ),
            ],
            move="advance",
            tutor_output="**Check:** Apply it to gamma.",
            probe="retrieval",
        ),
    ]

    metrics = scenario_metrics(turns, maximum_probes=2)

    assert metrics["delayed_retrieval"]["attempts"] == 2
    assert metrics["delayed_retrieval"]["passed"] == 0
    assert metrics["novel_transfer"] == {
        "attempts": 0,
        "passed": 0,
        "pass_rate": None,
    }
    assert metrics["turns_to_criterion"] == 1
    assert metrics["probes"] == {"total": 4, "redundant": 1}
    assert metrics["hint_worked_example_dependency"]["unresolved_concepts"] == 1
    assert metrics["concepts"] == {
        "covered": 2,
        "mastered": 2,
        "covered_without_false_mastery": 1,
        "false_mastery": 1,
    }
    assert metrics["deferred_recovery"] == {
        "deferred": 1,
        "recovered": 0,
        "rate": 0.0,
    }
    assert metrics["diagnostics"]["false_mastery"] == ["beta"]
    assert metrics["diagnostics"]["excessive_probing"]["excess"] == 2
    assert metrics["diagnostics"]["unresolved_hint_dependency"] == ["beta"]
    assert metrics["diagnostics"]["delayed_retrieval_failures"] == 2
    baseline = json.loads(OUTCOME_BASELINE_PATH.read_text(encoding="utf-8"))
    aggregate = aggregate_metrics([{"metrics": metrics, "turns": turns}])
    for key, value in baseline["results"].items():
        assert aggregate[key] == value


def test_delayed_retrieval_counts_disqualified_evidence_as_failure() -> None:
    concepts = ("recognition", "gamed", "hinted", "worked", "independent")
    turns = [
        _turn(
            1,
            events=[
                _event("concept_exposed", day=1, concept=concept)
                for concept in concepts
            ],
            move="advance",
            tutor_output="Return after the scheduled gap.",
        ),
        _turn(
            2,
            events=[
                _event(
                    "answer_judged",
                    day=3,
                    concept="recognition",
                    status="correct",
                    score=0.9,
                    answer_kind="recognition",
                    source="review",
                )
            ],
            move="advance",
            tutor_output="Continue.",
            probe="retrieval",
        ),
        _turn(
            3,
            events=[
                _event(
                    "answer_judged",
                    day=3,
                    concept="gamed",
                    status="correct",
                    score=0.9,
                    answer_kind="production",
                    gaming_suspected=True,
                    source="review",
                )
            ],
            move="remediation_hint",
            tutor_output="**Hint:** Isolate one relationship.",
            probe="retrieval",
        ),
        _turn(
            4,
            events=[
                _event(
                    "answer_judged",
                    day=3,
                    concept="hinted",
                    status="correct",
                    score=0.9,
                    answer_kind="production",
                    source="review",
                )
            ],
            move="worked_example",
            tutor_output="Here is one worked example.",
            probe="retrieval",
        ),
        _turn(
            5,
            events=[
                _event(
                    "answer_judged",
                    day=3,
                    concept="worked",
                    status="correct",
                    score=0.9,
                    answer_kind="production",
                    source="review",
                )
            ],
            move="advance",
            tutor_output="Now work independently.",
            probe="retrieval",
        ),
        _turn(
            6,
            events=[
                _event(
                    "answer_judged",
                    day=3,
                    concept="independent",
                    status="correct",
                    score=0.9,
                    answer_kind="production",
                    source="review",
                )
            ],
            move="advance",
            tutor_output="Continue.",
            probe="retrieval",
        ),
    ]

    metrics = scenario_metrics(turns, maximum_probes=6)

    assert metrics["delayed_retrieval"] == {
        "attempts": 5,
        "passed": 1,
        "pass_rate": 0.2,
        "by_concept": {
            "recognition": {"attempts": 1, "passed": 0},
            "gamed": {"attempts": 1, "passed": 0},
            "hinted": {"attempts": 1, "passed": 0},
            "worked": {"attempts": 1, "passed": 0},
            "independent": {"attempts": 1, "passed": 1},
        },
    }
    assert metrics["diagnostics"]["delayed_retrieval_failures"] == 4
    assert metrics["turns_to_criterion"] == 6


def test_only_independent_production_counts_as_qualifying_outcome() -> None:
    turns = [
        _turn(
            1,
            events=[
                _event(
                    "answer_judged",
                    day=1,
                    concept="recognition",
                    status="correct",
                    score=0.9,
                    answer_kind="recognition",
                    is_transfer=True,
                    source="review",
                )
            ],
            move="advance",
            tutor_output="Continue.",
        ),
        _turn(
            2,
            events=[
                _event(
                    "answer_judged",
                    day=2,
                    concept="gamed",
                    status="correct",
                    score=0.9,
                    answer_kind="production",
                    is_transfer=True,
                    gaming_suspected=True,
                    source="review",
                ),
                _event("concept_deferred", day=2, concept="supported"),
            ],
            move="remediation_hint",
            tutor_output="**Hint:** Isolate the invariant.",
        ),
        _turn(
            3,
            events=[
                _event(
                    "answer_judged",
                    day=3,
                    concept="supported",
                    status="correct",
                    score=0.9,
                    answer_kind="production",
                    is_transfer=True,
                    source="review",
                ),
                _event("concept_deferred", day=3, concept="independent"),
            ],
            move="advance",
            tutor_output="Try a new case.",
            probe="retrieval",
        ),
        _turn(
            4,
            events=[
                _event(
                    "answer_judged",
                    day=5,
                    concept="independent",
                    status="correct",
                    score=0.9,
                    answer_kind="production",
                    is_transfer=True,
                    source="review",
                )
            ],
            move="advance",
            tutor_output="Continue.",
            probe="retrieval",
        ),
    ]

    metrics = scenario_metrics(turns, maximum_probes=4)

    assert metrics["delayed_retrieval"] == {
        "attempts": 2,
        "passed": 1,
        "pass_rate": 0.5,
        "by_concept": {
            "supported": {"attempts": 1, "passed": 0},
            "independent": {"attempts": 1, "passed": 1},
        },
    }
    assert metrics["turns_to_criterion"] == 4
    assert metrics["novel_transfer"] == {
        "attempts": 1,
        "passed": 1,
        "pass_rate": 1.0,
    }
    assert metrics["deferred_recovery"] == {
        "deferred": 2,
        "recovered": 1,
        "rate": 0.5,
    }
    assert metrics["diagnostics"]["unrecovered_deferred_concepts"] == [
        "supported"
    ]
    assert metrics["hint_worked_example_dependency"][
        "unresolved_concepts"
    ] == 1


def test_premature_mastery_remains_false_after_independent_recovery() -> None:
    turns = [
        _turn(
            1,
            events=[
                _event(
                    "answer_judged",
                    day=1,
                    concept="omega",
                    status="needs_work",
                    score=0.2,
                    answer_kind="production",
                    source="review",
                )
            ],
            move="remediation_hint",
            tutor_output="**Hint:** Focus on the invariant.",
        ),
        _turn(
            2,
            events=[
                _event(
                    "answer_judged",
                    day=2,
                    concept="omega",
                    status="correct",
                    score=0.9,
                    answer_kind="production",
                    source="review",
                ),
                _event(
                    "mastery_changed",
                    day=2,
                    concept="omega",
                    mastered=True,
                ),
            ],
            move="advance",
            tutor_output="Try it independently next.",
        ),
        _turn(
            3,
            events=[
                _event(
                    "answer_judged",
                    day=3,
                    concept="omega",
                    status="correct",
                    score=0.9,
                    answer_kind="production",
                    source="review",
                )
            ],
            move="advance",
            tutor_output="Continue.",
        ),
    ]

    metrics = scenario_metrics(turns, maximum_probes=3)

    assert metrics["turns_to_criterion"] == 3
    assert metrics["diagnostics"]["false_mastery"] == ["omega"]
    assert metrics["diagnostics"]["false_mastery_later_recovered"] == ["omega"]
    assert metrics["concepts"]["false_mastery"] == 1
    assert metrics["concepts"]["covered_without_false_mastery"] == 0
    assert metrics["hint_worked_example_dependency"] == {
        "supported_correct_concepts": 1,
        "resolved_concepts": 1,
        "unresolved_concepts": 0,
    }


def test_partial_record_and_aggregate_metrics_remain_valid() -> None:
    fixture = load_scenarios(OUTCOME_SCENARIOS_DIR)[0]
    behavior_record = {
        "scenario": fixture["name"],
        "family": fixture["family"],
        "description": fixture["description"],
        "judge": {"pass": False},
        "partial_evidence": True,
        "provenance": {},
        "turns": [
            _turn(
                1,
                events=[],
                move="remediation_hint",
                tutor_output="**Hint:** Start with initialization.",
            )
        ],
    }

    record = outcome_record(behavior_record, fixture)
    aggregate = aggregate_metrics([record])

    assert record["complete"] is False
    assert record["metrics"]["turns_to_criterion"] is None
    assert aggregate["delayed_retrieval"]["pass_rate"] is None
    assert aggregate["turns_to_criterion"] == {
        "observations": 0,
        "median": None,
    }


def test_run_writes_human_reviewable_nonblocking_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_evaluation(
        run_root: Path,
        *,
        tutor_model: str,
        judge_model: str,
        scenario_ids,
        scenarios_dir: Path,
    ) -> EvaluationOutcome:
        evidence = run_root / "evidence"
        evidence.mkdir(parents=True)
        fixture = next(
            scenario
            for scenario in load_scenarios(scenarios_dir)
            if scenario["name"] == scenario_ids[0]
        )
        record = {
            "scenario": fixture["name"],
            "family": fixture["family"],
            "description": fixture["description"],
            "judge": {"pass": True},
            "partial_evidence": False,
            "provenance": {"fixture": "sanitized"},
            "turns": [
                _turn(
                    index,
                    events=[],
                    move="advance",
                    tutor_output="Concise feedback.",
                )
                for index in range(1, len(fixture["outcome_schedule"]) + 1)
            ],
        }
        (evidence / "turns.jsonl").write_text(
            json.dumps(record) + "\n",
            encoding="utf-8",
        )
        (evidence / "manifest.json").write_text(
            json.dumps(
                {
                    "models": {
                        "tutor": tutor_model,
                        "judge": judge_model,
                    }
                }
            ),
            encoding="utf-8",
        )
        return EvaluationOutcome(
            run_root=run_root,
            evidence_dir=evidence,
            passed=True,
            scenario_count=1,
            failure_count=0,
        )

    monkeypatch.setattr(
        "tests.evals.outcome.run_evaluation",
        fake_run_evaluation,
    )

    outcome = run_outcome_evaluation(
        tmp_path / "run",
        tutor_model="tutor-model",
        judge_model="judge-model",
        scenario_ids=["immediate_success_delayed_failure"],
    )

    manifest = json.loads(
        (outcome.evidence_dir / "manifest.json").read_text(encoding="utf-8")
    )
    summary = (outcome.evidence_dir / "summary.md").read_text(encoding="utf-8")
    record = json.loads(
        (outcome.evidence_dir / "scenarios.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert manifest["schema_version"] == OUTCOME_SCHEMA_VERSION
    assert manifest["contract_version"] == OUTCOME_CONTRACT_VERSION
    assert manifest["coverage"] == {
        "available_scenarios": 2,
        "selected_scenarios": 1,
        "completed_scenarios": 1,
        "partial": True,
    }
    assert manifest["release_blocking"] is False
    assert manifest["calibration"]["status"] == "uncalibrated"
    assert manifest["calibration"]["provider_backed_runs_required"] == 3
    assert manifest["time_simulation"]["sleeps"] is False
    assert record["schema_version"] == OUTCOME_SCHEMA_VERSION
    assert "This baseline is diagnostic and does not block a release." in summary
    assert "Scenario diagnostics" in summary
    if os.name != "nt":
        assert (outcome.evidence_dir / "manifest.json").stat().st_mode & 0o777 == 0o600
        assert (outcome.evidence_dir / "scenarios.jsonl").stat().st_mode & 0o777 == 0o600
