"""Opt-in delayed-retrieval and teaching-efficiency outcome evaluation."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openlearn import cli
from tests.evals.tutor_behavior import (
    _select_scenarios,
    _write_json,
    load_scenarios,
    run_evaluation,
    validate_live_configuration,
)

OUTCOME_SCHEMA_VERSION = 1
OUTCOME_CONTRACT_VERSION = "learning-outcomes-v1"
OUTCOME_SCENARIOS_DIR = Path(__file__).parent / "outcome_scenarios"
OUTCOME_BASELINE_PATH = (
    Path(__file__).parent / "fixtures" / "outcome_baseline_v1.json"
)
SIMULATED_START = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
SUPPORT_MOVES = {"remediation_hint", "worked_example"}
PROPOSED_THRESHOLDS: dict[str, float | int] = {
    "delayed_retrieval_pass_rate": 0.7,
    "novel_transfer_pass_rate": 0.7,
    "median_turns_to_criterion": 4,
    "maximum_redundant_probes_per_scenario": 1,
    "maximum_unresolved_hint_dependency_rate": 0.2,
    "maximum_false_mastery_count": 0,
}


@dataclass(frozen=True)
class OutcomeEvaluation:
    run_root: Path
    evidence_dir: Path
    scenario_count: int
    complete_count: int
    partial: bool


def validate_outcome_scenarios(
    scenarios: list[dict[str, object]],
) -> None:
    for scenario in scenarios:
        turns = scenario.get("turns")
        schedule = scenario.get("outcome_schedule")
        generated_count = (
            sum(
                isinstance(turn, dict)
                and turn.get("role") == "assistant"
                and turn.get("content") is None
                for turn in turns
            )
            if isinstance(turns, list)
            else 0
        )
        if (
            not isinstance(schedule, list)
            or len(schedule) != generated_count
            or not schedule
        ):
            raise ValueError(
                f"outcome scenario {scenario.get('name')} needs one schedule item "
                "per generated tutor move"
            )
        for index, item in enumerate(schedule, start=1):
            if not isinstance(item, dict):
                raise ValueError(
                    f"outcome scenario {scenario.get('name')} schedule {index} "
                    "must be an object"
                )
            gap = item.get("gap_days_before", 0)
            if (
                not isinstance(gap, int)
                or isinstance(gap, bool)
                or gap < 0
            ):
                raise ValueError(
                    f"outcome scenario {scenario.get('name')} schedule {index} "
                    "has an invalid gap_days_before"
                )
            probe = item.get("probe")
            if probe not in {"practice", "retrieval", "transfer"}:
                raise ValueError(
                    f"outcome scenario {scenario.get('name')} schedule {index} "
                    "has an invalid probe"
                )
        max_probes = scenario.get("maximum_probes")
        if (
            not isinstance(max_probes, int)
            or isinstance(max_probes, bool)
            or max_probes < 1
        ):
            raise ValueError(
                f"outcome scenario {scenario.get('name')} needs maximum_probes"
            )


def run_outcome_evaluation(
    run_root: Path,
    *,
    tutor_model: str,
    judge_model: str,
    scenario_ids: Sequence[str] | None = None,
    scenarios_dir: Path = OUTCOME_SCENARIOS_DIR,
) -> OutcomeEvaluation:
    run_root = run_root.expanduser().resolve()
    if run_root.exists():
        raise ValueError(f"run root must not already exist: {run_root}")
    scenarios = load_scenarios(scenarios_dir)
    validate_outcome_scenarios(scenarios)
    selected = _select_scenarios(scenarios, scenario_ids)
    run_root.mkdir(mode=0o700)
    behavior_outcome = run_evaluation(
        run_root / "behavior",
        tutor_model=tutor_model,
        judge_model=judge_model,
        scenario_ids=[str(scenario["name"]) for scenario in selected],
        scenarios_dir=scenarios_dir,
    )
    behavior_manifest = json.loads(
        (behavior_outcome.evidence_dir / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    sanitized_models = behavior_manifest.get("models")
    if not isinstance(sanitized_models, dict):
        sanitized_models = {"tutor": "unknown", "judge": "unknown"}
    behavior_records = _read_jsonl(behavior_outcome.evidence_dir / "turns.jsonl")
    fixtures = {str(scenario["name"]): scenario for scenario in selected}
    records = [
        outcome_record(record, fixtures[str(record["scenario"])])
        for record in behavior_records
    ]
    evidence_dir = run_root / "evidence"
    evidence_dir.mkdir(mode=0o700)
    scenarios_path = evidence_dir / "scenarios.jsonl"
    for record in records:
        _append_jsonl(scenarios_path, record)
    complete_count = sum(record["complete"] is True for record in records)
    metrics = aggregate_metrics(records)
    partial = len(selected) < len(scenarios) or complete_count < len(selected)
    manifest = {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "contract_version": OUTCOME_CONTRACT_VERSION,
        "status": "completed",
        "models": sanitized_models,
        "coverage": {
            "available_scenarios": len(scenarios),
            "selected_scenarios": len(selected),
            "completed_scenarios": complete_count,
            "partial": partial,
        },
        "time_simulation": {
            "kind": "deterministic_event_projection",
            "start": SIMULATED_START.isoformat(),
            "sleeps": False,
        },
        "metrics": metrics,
        "proposed_thresholds": PROPOSED_THRESHOLDS,
        "calibration": {
            "deterministic_baseline": str(
                OUTCOME_BASELINE_PATH.relative_to(Path(__file__).parents[2])
            ),
            "provider_backed_runs_required": 3,
            "provider_backed_runs_recorded": 0,
            "status": "uncalibrated",
        },
        "release_blocking": False,
        "artifacts": {
            "scenarios": "scenarios.jsonl",
            "summary": "summary.md",
            "behavior_evidence": "../behavior/evidence",
        },
    }
    _write_json(evidence_dir / "manifest.json", manifest)
    summary_path = evidence_dir / "summary.md"
    summary_path.write_text(
        render_outcome_summary(records, metrics, partial=partial),
        encoding="utf-8",
    )
    summary_path.chmod(0o600)
    return OutcomeEvaluation(
        run_root=run_root,
        evidence_dir=evidence_dir,
        scenario_count=len(records),
        complete_count=complete_count,
        partial=partial,
    )


def outcome_record(
    behavior_record: dict[str, object],
    scenario: dict[str, object],
) -> dict[str, object]:
    raw_turns = behavior_record.get("turns")
    turns = raw_turns if isinstance(raw_turns, list) else []
    schedule = scenario["outcome_schedule"]
    if not isinstance(schedule, list):
        raise ValueError("validated outcome schedule is missing")
    simulated_turns: list[dict[str, object]] = []
    logical_time = SIMULATED_START
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            continue
        item = schedule[index] if index < len(schedule) else {}
        if not isinstance(item, dict):
            item = {}
        logical_time += timedelta(days=int(item.get("gap_days_before", 0)))
        projected = dict(turn)
        projected["outcome_schedule"] = {
            "gap_days_before": int(item.get("gap_days_before", 0)),
            "probe": item.get("probe"),
            "simulated_at": logical_time.isoformat(),
        }
        projected_events: list[dict[str, object]] = []
        raw_events = turn.get("events")
        for event in raw_events if isinstance(raw_events, list) else []:
            if not isinstance(event, dict):
                continue
            projected_event = dict(event)
            projected_event["ts"] = logical_time.isoformat()
            projected_events.append(projected_event)
        projected["events"] = projected_events
        simulated_turns.append(projected)
    complete = (
        len(simulated_turns) == len(schedule)
        and not behavior_record.get("partial_evidence")
        and all(not turn.get("error") for turn in simulated_turns)
    )
    metrics = scenario_metrics(
        simulated_turns,
        maximum_probes=int(scenario["maximum_probes"]),
    )
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "contract_version": OUTCOME_CONTRACT_VERSION,
        "scenario": behavior_record.get("scenario"),
        "family": behavior_record.get("family"),
        "description": behavior_record.get("description", ""),
        "complete": complete,
        "behavior_judge_passed": (
            behavior_record.get("judge", {}).get("pass")
            if isinstance(behavior_record.get("judge"), dict)
            else False
        ),
        "turns": simulated_turns,
        "metrics": metrics,
        "diagnostics": metrics["diagnostics"],
        "provenance": behavior_record.get("provenance", {}),
    }


def scenario_metrics(
    turns: list[dict[str, object]],
    *,
    maximum_probes: int,
) -> dict[str, object]:
    events = [
        event
        for turn in turns
        for event in (
            turn.get("events") if isinstance(turn.get("events"), list) else []
        )
        if isinstance(event, dict)
    ]
    answers: list[dict[str, object]] = []
    criterion_turn: int | None = None
    transfer_attempts = 0
    transfer_passed = 0
    supported_correct: set[str] = set()
    independent_correct: set[str] = set()
    mastery_events: set[str] = set()
    false_mastery: set[str] = set()
    deferred_at: dict[str, int] = {}
    for index, turn in enumerate(turns):
        prior_move = _move_name(turns[index - 1]) if index else ""
        support = prior_move if prior_move in SUPPORT_MOVES else "none"
        raw_events = turn.get("events")
        for event in raw_events if isinstance(raw_events, list) else []:
            if not isinstance(event, dict):
                continue
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            event_type = event.get("event_type")
            concept = _concept(data)
            if event_type == "concept_deferred" and concept:
                deferred_at.setdefault(concept, index + 1)
                continue
            if event_type == "mastery_changed" and data.get("mastered") is True:
                if concept:
                    mastery_events.add(concept)
                    if concept not in independent_correct:
                        false_mastery.add(concept)
                continue
            if event_type != "answer_judged":
                continue
            answer = {
                "turn": index + 1,
                "concept": concept,
                "passed": _passed(data),
                "production": data.get("answer_kind") == "production",
                "gaming_suspected": data.get("gaming_suspected") is True,
                "support": support,
                "transfer": data.get("is_transfer") is True,
                "retrieval": (
                    _answer_is_retrieval(turns, index + 1)
                    and bool(cli.event_retrieval_source(event))
                ),
                "ts": event.get("ts"),
            }
            answers.append(answer)
            if qualifying_outcome_evidence(answer, "criterion"):
                if concept:
                    independent_correct.add(concept)
            elif _valid_production_evidence(answer) and concept:
                supported_correct.add(concept)
            if (
                qualifying_outcome_evidence(answer, "criterion")
                and criterion_turn is None
            ):
                criterion_turn = index + 1
            if _eligible_outcome_attempt(answer, "novel_transfer"):
                transfer_attempts += 1
                if qualifying_outcome_evidence(answer, "novel_transfer"):
                    transfer_passed += 1

    recovered_false_mastery = sorted(false_mastery & independent_correct)
    false_mastery_list = sorted(false_mastery)
    unresolved_dependency = sorted(supported_correct - independent_correct)
    deferred = set(deferred_at)
    recovered = {
        str(answer["concept"])
        for answer in answers
        if answer["concept"]
        and answer["concept"] in deferred
        and int(answer["turn"]) > deferred_at[str(answer["concept"])]
        and qualifying_outcome_evidence(answer, "deferred_recovery")
    }
    tutor_words = sum(
        _word_count(str(turn.get("tutor_output") or "")) for turn in turns
    )
    probes = [
        _probe_text(str(turn.get("tutor_output") or ""))
        for turn in turns
        if _probe_text(str(turn.get("tutor_output") or ""))
    ]
    redundant_probes = len(probes) - len(set(probes))
    excessive_probes = max(0, len(probes) - maximum_probes)
    delayed = _delayed_retrieval_outcome_metric(events, answers)
    covered = {str(answer["concept"]) for answer in answers if answer["concept"]}
    diagnostics = {
        "false_mastery": false_mastery_list,
        "false_mastery_later_recovered": recovered_false_mastery,
        "excessive_probing": {
            "actual": len(probes),
            "maximum": maximum_probes,
            "excess": excessive_probes,
        },
        "redundant_probes": redundant_probes,
        "unresolved_hint_dependency": unresolved_dependency,
        "delayed_retrieval_failures": int(delayed["attempts"])
        - int(delayed["passed"]),
        "unrecovered_deferred_concepts": sorted(deferred - recovered),
    }
    return {
        "delayed_retrieval": delayed,
        "novel_transfer": {
            "attempts": transfer_attempts,
            "passed": transfer_passed,
            "pass_rate": (
                transfer_passed / transfer_attempts if transfer_attempts else None
            ),
        },
        "turns_to_criterion": criterion_turn,
        "tutor_words": {
            "total": tutor_words,
            "per_turn": tutor_words / len(turns) if turns else None,
            "per_mastered_concept": (
                tutor_words / len(mastery_events) if mastery_events else None
            ),
        },
        "probes": {
            "total": len(probes),
            "redundant": redundant_probes,
        },
        "hint_worked_example_dependency": {
            "supported_correct_concepts": len(supported_correct),
            "resolved_concepts": len(supported_correct & independent_correct),
            "unresolved_concepts": len(unresolved_dependency),
        },
        "concepts": {
            "covered": len(covered),
            "mastered": len(mastery_events),
            "covered_without_false_mastery": len(covered - false_mastery),
            "false_mastery": len(false_mastery),
        },
        "deferred_recovery": {
            "deferred": len(deferred),
            "recovered": len(recovered),
            "rate": len(recovered) / len(deferred) if deferred else None,
        },
        "diagnostics": diagnostics,
    }


def qualifying_outcome_evidence(
    answer: dict[str, object],
    semantic: str,
) -> bool:
    if not (
        answer.get("passed") is True
        and _eligible_outcome_attempt(answer, semantic)
    ):
        return False
    if semantic == "criterion":
        return True
    if semantic == "novel_transfer":
        return answer.get("transfer") is True
    if semantic in {"deferred_recovery", "delayed_retrieval"}:
        return answer.get("retrieval") is True
    raise ValueError(f"unknown outcome semantic: {semantic}")


def _eligible_outcome_attempt(
    answer: dict[str, object],
    semantic: str,
) -> bool:
    if not (
        answer.get("production") is True
        and answer.get("gaming_suspected") is not True
        and answer.get("support") == "none"
    ):
        return False
    if semantic == "criterion":
        return True
    if semantic == "novel_transfer":
        return answer.get("transfer") is True
    if semantic in {"deferred_recovery", "delayed_retrieval"}:
        return answer.get("retrieval") is True
    raise ValueError(f"unknown outcome semantic: {semantic}")


def _valid_production_evidence(answer: dict[str, object]) -> bool:
    return (
        answer.get("passed") is True
        and answer.get("production") is True
        and answer.get("gaming_suspected") is not True
        and answer.get("support") in SUPPORT_MOVES
    )


def _delayed_retrieval_outcome_metric(
    events: list[dict[str, object]],
    answers: list[dict[str, object]],
    *,
    min_spacing_days: int = 1,
) -> dict[str, object]:
    first_seen: dict[str, datetime] = {}
    for event in sorted(events, key=lambda item: str(item.get("ts") or "")):
        concept = cli.event_concept_id(event)
        timestamp = cli.parse_event_ts(event.get("ts"))
        if concept and timestamp is not None:
            first_seen.setdefault(concept, timestamp)

    attempts = 0
    passed = 0
    by_concept: dict[str, dict[str, int]] = {}
    for answer in answers:
        concept = answer.get("concept")
        timestamp = cli.parse_event_ts(answer.get("ts"))
        if (
            not isinstance(concept, str)
            or not concept
            or timestamp is None
            or answer.get("retrieval") is not True
        ):
            continue
        seen_at = first_seen.get(concept)
        if seen_at is None:
            continue
        elapsed_days = (timestamp - seen_at).total_seconds() / 86400
        if elapsed_days < max(0, min_spacing_days):
            continue
        attempts += 1
        qualified = qualifying_outcome_evidence(
            answer,
            "delayed_retrieval",
        )
        concept_counts = by_concept.setdefault(
            concept,
            {"attempts": 0, "passed": 0},
        )
        concept_counts["attempts"] += 1
        if qualified:
            passed += 1
            concept_counts["passed"] += 1
    return {
        "attempts": attempts,
        "passed": passed,
        "pass_rate": passed / attempts if attempts else None,
        "by_concept": by_concept,
    }


def aggregate_metrics(records: list[dict[str, object]]) -> dict[str, object]:
    scenario_values = [
        record["metrics"]
        for record in records
        if isinstance(record.get("metrics"), dict)
    ]
    delayed_attempts = sum(
        int(value["delayed_retrieval"]["attempts"]) for value in scenario_values
    )
    delayed_passed = sum(
        int(value["delayed_retrieval"]["passed"]) for value in scenario_values
    )
    transfer_attempts = sum(
        int(value["novel_transfer"]["attempts"]) for value in scenario_values
    )
    transfer_passed = sum(
        int(value["novel_transfer"]["passed"]) for value in scenario_values
    )
    criterion = sorted(
        int(value["turns_to_criterion"])
        for value in scenario_values
        if isinstance(value.get("turns_to_criterion"), int)
    )
    total_words = sum(int(value["tutor_words"]["total"]) for value in scenario_values)
    total_turns = sum(
        len(record.get("turns", []))
        for record in records
        if isinstance(record.get("turns"), list)
    )
    mastered = sum(int(value["concepts"]["mastered"]) for value in scenario_values)
    supported_correct = sum(
        int(
            value["hint_worked_example_dependency"][
                "supported_correct_concepts"
            ]
        )
        for value in scenario_values
    )
    resolved_dependency = sum(
        int(value["hint_worked_example_dependency"]["resolved_concepts"])
        for value in scenario_values
    )
    unresolved_dependency = sum(
        int(value["hint_worked_example_dependency"]["unresolved_concepts"])
        for value in scenario_values
    )
    return {
        "delayed_retrieval": {
            "attempts": delayed_attempts,
            "passed": delayed_passed,
            "pass_rate": (
                delayed_passed / delayed_attempts if delayed_attempts else None
            ),
        },
        "novel_transfer": {
            "attempts": transfer_attempts,
            "passed": transfer_passed,
            "pass_rate": (
                transfer_passed / transfer_attempts if transfer_attempts else None
            ),
        },
        "turns_to_criterion": {
            "observations": len(criterion),
            "median": _median(criterion),
        },
        "tutor_words": {
            "total": total_words,
            "per_turn": total_words / total_turns if total_turns else None,
            "per_mastered_concept": total_words / mastered if mastered else None,
        },
        "probes": {
            "total": sum(
                int(value["probes"]["total"]) for value in scenario_values
            ),
            "redundant": sum(
                int(value["probes"]["redundant"]) for value in scenario_values
            ),
            "excessive": sum(
                int(value["diagnostics"]["excessive_probing"]["excess"])
                for value in scenario_values
            ),
        },
        "hint_worked_example_dependency": {
            "supported_correct_concepts": supported_correct,
            "resolved_concepts": resolved_dependency,
            "unresolved_concepts": unresolved_dependency,
            "unresolved_rate": (
                unresolved_dependency / supported_correct
                if supported_correct
                else None
            ),
        },
        "concepts_covered_without_false_mastery": sum(
            int(value["concepts"]["covered_without_false_mastery"])
            for value in scenario_values
        ),
        "false_mastery": sum(
            int(value["concepts"]["false_mastery"]) for value in scenario_values
        ),
        "deferred_recovery": _aggregate_deferred_recovery(scenario_values),
    }


def render_outcome_summary(
    records: list[dict[str, object]],
    metrics: dict[str, object],
    *,
    partial: bool,
) -> str:
    delayed = metrics["delayed_retrieval"]
    transfer = metrics["novel_transfer"]
    lines = [
        "# Learning outcome eval",
        "",
        f"Coverage: {'partial' if partial else 'complete'} ({len(records)} scenarios).",
        "",
        "This baseline is diagnostic and does not block a release.",
        "",
        "## Aggregate metrics",
        "",
        f"- Delayed retrieval: {_ratio(delayed)}",
        "",
        f"- Novel transfer: {_ratio(transfer)}",
        "",
        f"- Median turns to criterion: {metrics['turns_to_criterion']['median']}",
        "",
        f"- Tutor words per turn: {_number(metrics['tutor_words']['per_turn'])}",
        "",
        f"- Tutor words per mastered concept: "
        f"{_number(metrics['tutor_words']['per_mastered_concept'])}",
        "",
        f"- Probes: {metrics['probes']['total']} total, "
        f"{metrics['probes']['redundant']} redundant, "
        f"{metrics['probes']['excessive']} excessive",
        "",
        f"- Unresolved hint or worked-example dependencies: "
        f"{metrics['hint_worked_example_dependency']['unresolved_concepts']} "
        f"({_number(metrics['hint_worked_example_dependency']['unresolved_rate'])})",
        "",
        f"- Concepts covered without false mastery: "
        f"{metrics['concepts_covered_without_false_mastery']}",
        "",
        f"- False mastery events: {metrics['false_mastery']}",
        "",
        f"- Deferred recovery: {metrics['deferred_recovery']['recovered']}/"
        f"{metrics['deferred_recovery']['deferred']} "
        f"({_number(metrics['deferred_recovery']['rate'])})",
        "",
        "## Scenario diagnostics",
        "",
    ]
    for record in records:
        diagnostics = record["diagnostics"]
        visible = _visible_diagnostics(diagnostics)
        lines.append(
            f"- **{record['scenario']}**: "
            f"{'complete' if record['complete'] else 'partial'}; {visible}"
        )
        lines.append("")
    lines.extend(
        [
            "Inspect `scenarios.jsonl` for simulated timestamps, durable events, "
            "state-linked turns, and metric inputs.",
            "",
            "The proposed thresholds in `manifest.json` remain non-blocking until "
            "provider-backed baselines are calibrated and approved.",
            "",
        ]
    )
    return "\n".join(lines)


def _events_of_type(
    turn: dict[str, object], event_type: str
) -> list[dict[str, object]]:
    events = turn.get("events")
    if not isinstance(events, list):
        return []
    return [
        event
        for event in events
        if isinstance(event, dict) and event.get("event_type") == event_type
    ]


def _aggregate_deferred_recovery(
    values: list[dict[str, object]],
) -> dict[str, object]:
    deferred = sum(
        int(value["deferred_recovery"]["deferred"]) for value in values
    )
    recovered = sum(
        int(value["deferred_recovery"]["recovered"]) for value in values
    )
    return {
        "deferred": deferred,
        "recovered": recovered,
        "rate": recovered / deferred if deferred else None,
    }


def _move_name(turn: dict[str, object]) -> str:
    move = turn.get("selected_move")
    if not isinstance(move, dict):
        return ""
    value = move.get("name")
    return value if isinstance(value, str) else ""


def _concept(data: dict[str, object]) -> str:
    value = data.get("concept_id")
    return value.strip() if isinstance(value, str) else ""


def _passed(data: dict[str, object]) -> bool:
    status = data.get("status")
    score = data.get("score")
    return status == "correct" or (
        isinstance(score, (int, float))
        and not isinstance(score, bool)
        and float(score) >= 0.8
    )


def _answer_is_retrieval(turns: list[dict[str, object]], turn_number: int) -> bool:
    turn = turns[turn_number - 1]
    schedule = turn.get("outcome_schedule")
    return isinstance(schedule, dict) and schedule.get("probe") == "retrieval"


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def _probe_text(text: str) -> str:
    match = re.search(r"(?is)\*\*Check:\*\*\s*(.+)", text)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip().casefold()


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return float(values[middle])
    return (values[middle - 1] + values[middle]) / 2


def _ratio(value: object) -> str:
    if not isinstance(value, dict):
        return "n/a"
    return (
        f"{value.get('passed', 0)}/{value.get('attempts', 0)} "
        f"({_number(value.get('pass_rate'))})"
    )


def _number(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def _visible_diagnostics(value: object) -> str:
    if not isinstance(value, dict):
        return "diagnostics unavailable"
    findings: list[str] = []
    if value.get("false_mastery"):
        findings.append(f"false mastery={value['false_mastery']}")
    probing = value.get("excessive_probing")
    if isinstance(probing, dict) and probing.get("excess"):
        findings.append(
            f"excess probes={probing['actual']}/{probing['maximum']}"
        )
    if value.get("redundant_probes"):
        findings.append(f"redundant probes={value['redundant_probes']}")
    if value.get("unresolved_hint_dependency"):
        findings.append(
            f"unresolved support={value['unresolved_hint_dependency']}"
        )
    if value.get("delayed_retrieval_failures"):
        findings.append(
            f"delayed failures={value['delayed_retrieval_failures']}"
        )
    if value.get("unrecovered_deferred_concepts"):
        findings.append(
            f"unrecovered deferred={value['unrecovered_deferred_concepts']}"
        )
    return "; ".join(findings) if findings else "no diagnostic failures"


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and isinstance((value := json.loads(line)), dict)
    ]


def _append_jsonl(path: Path, value: object) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    path.chmod(0o600)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the opt-in learning outcome eval and save evidence"
    )
    parser.add_argument("run_root", type=Path)
    parser.add_argument(
        "--tutor-model",
        default=os.environ.get("OPENLEARN_MODEL") or cli.DEFAULT_MODEL,
    )
    parser.add_argument(
        "--judge-model",
        default=os.environ.get("OPENLEARN_EVAL_JUDGE_MODEL"),
    )
    parser.add_argument("--scenario", action="append", dest="scenario_ids")
    args = parser.parse_args(argv)
    if not args.judge_model:
        parser.error(
            "set OPENLEARN_EVAL_JUDGE_MODEL or pass --judge-model; "
            "it must differ from the tutor model"
        )
    try:
        validate_live_configuration(
            tutor_model=args.tutor_model,
            judge_model=args.judge_model,
            api_key=os.environ.get("OPENAI_API_KEY"),
            mock_enabled=os.environ.get("OPENLEARN_MOCK", "").lower()
            in {"1", "true", "yes"},
        )
        outcome = run_outcome_evaluation(
            args.run_root,
            tutor_model=args.tutor_model,
            judge_model=args.judge_model,
            scenario_ids=args.scenario_ids,
        )
    except (cli.OpenLearnError, OSError, ValueError) as exc:
        parser.exit(2, f"learning outcome eval: {exc}\n")
    print(
        f"scenarios={outcome.scenario_count} complete={outcome.complete_count} "
        f"partial={str(outcome.partial).lower()} evidence={outcome.evidence_dir}"
    )
    return 0 if outcome.complete_count == outcome.scenario_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
