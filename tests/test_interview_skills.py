from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openlearn import course_templates, interview_skills


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "interview_skill_states.json").read_text(
        encoding="utf-8"
    )
)
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _graph_dict() -> dict[str, object]:
    return interview_skills.load_graph_dict()


def _evidence(*names: str) -> list[dict[str, object]]:
    return [
        deepcopy(record)
        for name in names
        for record in FIXTURES[name]
    ]


def _versioned_graph(
    graph_version: str,
    mastery_policy_version: str,
) -> dict[str, object]:
    raw = _graph_dict()
    raw["graph_version"] = graph_version
    raw["mastery_policy_version"] = mastery_policy_version
    return raw


def _with_versions(
    evidence: list[dict[str, object]],
    graph_version: str,
    mastery_policy_version: str,
) -> list[dict[str, object]]:
    for record in evidence:
        record["graph_version"] = graph_version
        record["mastery_policy_version"] = mastery_policy_version
    return evidence


def test_default_graph_has_versioned_stable_contract_and_all_skill_domains() -> None:
    graph = interview_skills.load_default_graph()

    assert graph.graph_id == "coding-interview"
    assert graph.graph_version == "1.0.0"
    assert graph.mastery_policy_version == "interview-mastery-v1"
    assert {skill.category for skill in graph.skills} == {
        "concept",
        "pattern",
        "process",
        "communication",
    }
    assert len({skill.skill_id for skill in graph.skills}) == len(graph.skills)
    assert all(
        skill.skill_id.startswith(f"{skill.category}.")
        for skill in graph.skills
    )
    assert all(
        set(skill.evidence_policy.minimum)
        == set(interview_skills.EVIDENCE_KINDS)
        for skill in graph.skills
    )
    assert all(skill.evidence_policy.transfer.minimum_novel_contexts >= 1 for skill in graph.skills)


def test_graph_rejects_unknown_edges_cycles_and_invalid_problem_roles() -> None:
    unknown = _graph_dict()
    unknown["skills"][0]["prerequisites"] = [
        {"skill_id": "concept.does-not-exist", "kind": "blocking"}
    ]
    with pytest.raises(interview_skills.SkillGraphError, match="unknown prerequisite"):
        interview_skills.validate_graph(unknown)

    cycle = _graph_dict()
    cycle["skills"][0]["prerequisites"] = [
        {"skill_id": cycle["skills"][1]["id"], "kind": "blocking"}
    ]
    cycle["skills"][1]["prerequisites"] = [
        {"skill_id": cycle["skills"][0]["id"], "kind": "supporting"}
    ]
    with pytest.raises(interview_skills.SkillGraphError, match="cycle"):
        interview_skills.validate_graph(cycle)

    invalid_role = _graph_dict()
    invalid_role["problems"][0]["skills"][0]["role"] = "automatic-award"
    with pytest.raises(interview_skills.SkillGraphError, match="problem skill role"):
        interview_skills.validate_graph(invalid_role)


def test_problem_roles_bound_inference_and_explicit_checks() -> None:
    graph = interview_skills.load_default_graph()
    problem = graph.problem("problem.minimum-window-substring")

    primary = interview_skills.inferable_skills(problem, "production", explicit_check=False)
    supporting = interview_skills.inferable_skills(
        problem, "production", explicit_check=True
    )

    assert primary == ("pattern.sliding-window",)
    assert "concept.hashing" in supporting
    assert "concept.complexity-analysis" not in primary
    assert "concept.complexity-analysis" in interview_skills.inferable_skills(
        problem, "explanation", explicit_check=True
    )


def test_advanced_learner_is_ready() -> None:
    graph = interview_skills.load_default_graph()
    assessments = interview_skills.assess_skills(
        graph,
        _evidence("advanced_learner"),
        now=NOW,
    )
    skill = assessments["pattern.sliding-window"]

    assert skill.readiness == "ready"
    assert skill.selection_status == "ready"
    assert skill.evidence_graph_versions == ("1.0.0",)
    assert skill.evidence_policy_versions == ("interview-mastery-v1",)
    assert any("qualifying transfer" in reason for reason in skill.reasons)


def test_historical_evidence_uses_record_graph_roles_after_role_change() -> None:
    historical = interview_skills.load_default_graph()
    current_raw = _versioned_graph("2.0.0", "interview-mastery-v2")
    problem = next(
        item
        for item in current_raw["problems"]
        if item["id"] == "problem.longest-repeating-replacement"
    )
    for ref in problem["skills"]:
        if ref["skill_id"] == "pattern.sliding-window":
            ref["role"] = "supporting"
        elif ref["skill_id"] == "concept.arrays-strings":
            ref["role"] = "primary"
    current = interview_skills.validate_graph(current_raw)
    registry = interview_skills.SkillGraphRegistry.from_graphs(
        (historical, current)
    )

    skill = interview_skills.assess_skills(
        current,
        _evidence("advanced_learner"),
        registry=registry,
        now=NOW,
    )["pattern.sliding-window"]

    assert skill.readiness == "ready"
    assert any("reassessed" in reason.lower() for reason in skill.reasons)


def test_historical_evidence_uses_record_policy_after_policy_change() -> None:
    historical = interview_skills.load_default_graph()
    current_raw = _versioned_graph("2.0.0", "interview-mastery-v2")
    pattern_policy = next(
        item
        for item in current_raw["evidence_policies"]
        if item["id"] == "pattern-v1"
    )
    pattern_policy["inferable_kinds"].remove("production")
    current = interview_skills.validate_graph(current_raw)
    registry = interview_skills.SkillGraphRegistry.from_graphs(
        (historical, current)
    )

    skill = interview_skills.assess_skills(
        current,
        _evidence("advanced_learner"),
        registry=registry,
        now=NOW,
    )["pattern.sliding-window"]

    assert skill.qualifying_counts["production"] == 1
    assert skill.readiness == "ready"


def test_removed_problem_does_not_invalidate_versioned_historical_evidence() -> None:
    historical = interview_skills.load_default_graph()
    current_raw = _versioned_graph("2.0.0", "interview-mastery-v2")
    current_raw["problems"] = [
        problem
        for problem in current_raw["problems"]
        if problem["id"] != "problem.longest-repeating-replacement"
    ]
    current = interview_skills.validate_graph(current_raw)
    registry = interview_skills.SkillGraphRegistry.from_graphs(
        (historical, current)
    )

    skill = interview_skills.assess_skills(
        current,
        _evidence("advanced_learner"),
        registry=registry,
        now=NOW,
    )["pattern.sliding-window"]

    assert skill.qualifying_counts["transfer"] == 1
    assert skill.readiness == "ready"


def test_removed_skill_evidence_remains_validated_orphaned_history() -> None:
    historical = interview_skills.load_default_graph()
    current_raw = _versioned_graph("2.0.0", "interview-mastery-v2")
    current_raw["skills"] = [
        skill
        for skill in current_raw["skills"]
        if skill["id"] != "concept.tries"
    ]
    current_raw["problems"] = [
        problem
        for problem in current_raw["problems"]
        if problem["id"] != "problem.autocomplete"
    ]
    current = interview_skills.validate_graph(current_raw)
    registry = interview_skills.SkillGraphRegistry.from_graphs(
        (historical, current)
    )
    record = deepcopy(_evidence("advanced_learner")[0])
    record.update(
        {
            "evidence_id": "ev-retired-tries",
            "skill_id": "concept.tries",
            "problem_id": "problem.autocomplete",
            "kind": "explanation",
            "explicit_check": True,
            "transfer_family": "prefix-tree",
        }
    )

    validated = interview_skills.validate_evidence_record(record, registry)
    current_evidence, orphaned = interview_skills.partition_evidence(
        current, (record,)
    )

    assert validated.skill.skill_id == "concept.tries"
    assert current_evidence == ()
    assert orphaned == (record,)


def test_prerequisite_gap_blocks_without_discarding_candidate_evidence() -> None:
    graph = interview_skills.load_default_graph()
    assessments = interview_skills.assess_skills(
        graph,
        _evidence("prerequisite_gap"),
        now=NOW,
    )
    skill = assessments["pattern.sliding-window"]

    assert skill.selection_status == "blocked"
    assert skill.readiness == "provisional"
    assert skill.qualifying_counts["production"] == 1
    assert any("blocking prerequisite" in reason for reason in skill.reasons)


def test_hint_dependent_success_is_weak_and_explains_deferred_credit() -> None:
    graph = interview_skills.load_default_graph()
    evidence = [
        record
        for record in _evidence("advanced_learner")
        if record["skill_id"] != "pattern.sliding-window"
    ]
    evidence.extend(_evidence("hint_dependent_success"))
    skill = interview_skills.assess_skills(
        graph,
        evidence,
        now=NOW,
    )["pattern.sliding-window"]

    assert skill.selection_status == "weak"
    assert skill.readiness == "weak"
    assert skill.qualifying_counts["production"] == 0
    assert any("hint-dependent" in reason for reason in skill.reasons)


def test_delayed_transfer_failure_is_due() -> None:
    graph = interview_skills.load_default_graph()
    evidence = _evidence("advanced_learner", "delayed_transfer_failure")
    skill = interview_skills.assess_skills(graph, evidence, now=NOW)[
        "pattern.sliding-window"
    ]

    assert skill.selection_status == "due"
    assert skill.readiness == "provisional"
    assert any("latest delayed retrieval failed" in reason for reason in skill.reasons)


def test_early_delayed_retrieval_does_not_satisfy_the_delay_contract() -> None:
    graph = interview_skills.load_default_graph()
    evidence = _evidence("advanced_learner")
    for record in evidence:
        if (
            record["skill_id"] == "pattern.sliding-window"
            and record["kind"] == "delayed_retrieval"
        ):
            record["observed_at"] = "2026-06-02T12:00:00+00:00"

    skill = interview_skills.assess_skills(graph, evidence, now=NOW)[
        "pattern.sliding-window"
    ]

    assert skill.readiness == "provisional"
    assert skill.qualifying_counts["delayed_retrieval"] == 0
    assert any("before the policy delay" in reason for reason in skill.reasons)


def test_nonqualifying_recent_attempt_does_not_hide_due_retrieval() -> None:
    graph = interview_skills.load_default_graph()
    evidence = _evidence("advanced_learner")
    recent_hinted = deepcopy(
        next(
            record
            for record in evidence
            if record["skill_id"] == "pattern.sliding-window"
            and record["kind"] == "delayed_retrieval"
        )
    )
    recent_hinted["evidence_id"] = "ev-recent-hinted-retrieval"
    recent_hinted["observed_at"] = "2026-08-09T12:00:00+00:00"
    recent_hinted["independent"] = False
    recent_hinted["assistance"] = "worked_example"
    evidence.append(recent_hinted)

    skill = interview_skills.assess_skills(
        graph,
        evidence,
        now=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
    )["pattern.sliding-window"]

    assert skill.readiness == "provisional"
    assert skill.selection_status == "due"
    assert any("outside the review window" in reason for reason in skill.reasons)


def test_query_statuses_are_deterministic_and_have_visible_reasons() -> None:
    graph = interview_skills.load_default_graph()
    assessments = interview_skills.assess_skills(
        graph,
        _evidence("advanced_learner"),
        now=NOW,
    )

    first = interview_skills.query_skills(assessments, "unassessed")
    second = interview_skills.query_skills(assessments, "unassessed")

    assert first == second
    assert first == tuple(sorted(first))
    assert all(assessments[skill_id].reasons for skill_id in first)
    assert "pattern.sliding-window" in interview_skills.query_skills(
        assessments, "ready"
    )


def test_unknown_historical_skill_evidence_is_retained_as_orphaned_history() -> None:
    graph = interview_skills.load_default_graph()
    evidence = _evidence("advanced_learner")
    orphan = deepcopy(evidence[0])
    orphan["evidence_id"] = "ev-retired-skill"
    orphan["skill_id"] = "pattern.retired-pattern"
    orphan["graph_version"] = "0.8.0"
    evidence.append(orphan)

    current, orphaned = interview_skills.partition_evidence(graph, evidence)

    assert len(current) == len(evidence) - 1
    assert orphaned == (orphan,)


@pytest.mark.parametrize(
    ("assistance", "completion_state"),
    [
        ("copied_structure", "complete"),
        ("partial_code", "partial"),
        ("worked_example", "complete"),
        ("editorial", "complete"),
    ],
)
def test_assisted_or_incomplete_production_never_earns_independent_credit(
    assistance: str,
    completion_state: str,
) -> None:
    graph = interview_skills.load_default_graph()
    evidence = [
        record
        for record in _evidence("advanced_learner")
        if record["skill_id"] != "pattern.sliding-window"
        or record["kind"] != "production"
    ]
    production = deepcopy(
        next(
            record
            for record in _evidence("advanced_learner")
            if record["skill_id"] == "pattern.sliding-window"
            and record["kind"] == "production"
        )
    )
    production["assistance"] = assistance
    production["completion_state"] = completion_state
    production["independent"] = True
    evidence.append(production)

    skill = interview_skills.assess_skills(graph, evidence, now=NOW)[
        "pattern.sliding-window"
    ]

    assert skill.qualifying_counts["production"] == 0
    assert skill.readiness == "provisional"
    assert any("assistance" in reason.lower() for reason in skill.reasons)


def test_unknown_assistance_provenance_is_rejected() -> None:
    graph = interview_skills.load_default_graph()
    registry = interview_skills.SkillGraphRegistry.from_graphs((graph,))
    record = deepcopy(_evidence("advanced_learner")[0])
    record["assistance"] = "some_help"

    with pytest.raises(interview_skills.EvidenceRecordError, match="assistance"):
        interview_skills.validate_evidence_record(record, registry)


def test_spoofed_transfer_family_does_not_earn_credit() -> None:
    graph = interview_skills.load_default_graph()
    evidence = _evidence("advanced_learner")
    transfer = next(
        record
        for record in evidence
        if record["skill_id"] == "pattern.sliding-window"
        and record["kind"] == "transfer"
    )
    transfer["transfer_family"] = "invented-breadth"

    skill = interview_skills.assess_skills(graph, evidence, now=NOW)[
        "pattern.sliding-window"
    ]

    assert skill.qualifying_counts["transfer"] == 0
    assert skill.readiness == "provisional"
    assert any("transfer family" in reason.lower() for reason in skill.reasons)


def test_repeated_same_problem_cannot_manufacture_transfer_breadth() -> None:
    raw = _versioned_graph("2.0.0", "interview-mastery-v2")
    pattern_policy = next(
        item for item in raw["evidence_policies"] if item["id"] == "pattern-v1"
    )
    pattern_policy["transfer"]["minimum_novel_contexts"] = 2
    graph = interview_skills.validate_graph(raw)
    evidence = _with_versions(
        _evidence("advanced_learner"),
        graph.graph_version,
        graph.mastery_policy_version,
    )
    transfer = next(
        record
        for record in evidence
        if record["skill_id"] == "pattern.sliding-window"
        and record["kind"] == "transfer"
    )
    duplicate = deepcopy(transfer)
    duplicate["evidence_id"] = "ev-duplicate-transfer"
    duplicate["observed_at"] = "2026-06-04T12:00:00+00:00"
    evidence.append(duplicate)

    skill = interview_skills.assess_skills(graph, evidence, now=NOW)[
        "pattern.sliding-window"
    ]

    assert skill.qualifying_counts["transfer"] == 1
    assert skill.readiness == "provisional"


def test_blocking_prerequisites_propagate_through_three_level_chain() -> None:
    raw = _versioned_graph("2.0.0", "interview-mastery-v2")
    hashing = next(item for item in raw["skills"] if item["id"] == "concept.hashing")
    hashing["prerequisites"] = [
        {"skill_id": "concept.arrays-strings", "kind": "blocking"}
    ]
    graph = interview_skills.validate_graph(raw)
    evidence = _with_versions(
        [
            record
            for record in _evidence("advanced_learner")
            if record["skill_id"] != "concept.arrays-strings"
        ],
        graph.graph_version,
        graph.mastery_policy_version,
    )

    assessments = interview_skills.assess_skills(graph, evidence, now=NOW)

    assert assessments["concept.hashing"].readiness == "ready"
    assert assessments["concept.hashing"].selection_status == "blocked"
    assert assessments["pattern.sliding-window"].selection_status == "blocked"
    assert any(
        "concept.hashing" in reason
        for reason in assessments["pattern.sliding-window"].reasons
    )


def test_algorithms_template_seeds_from_graph_without_defining_the_graph() -> None:
    template = course_templates.load_course_template("algorithms")
    graph = interview_skills.load_default_graph()

    seed = interview_skills.seed_interview_course(template, graph)

    assert seed.source_template == "algorithms"
    assert seed.graph_id == graph.graph_id
    assert seed.graph_version == graph.graph_version
    assert seed.units
    assert all(unit.skill_ids for unit in seed.units)
    assert set(seed.skill_ids) == {skill.skill_id for skill in graph.skills}
