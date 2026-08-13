from __future__ import annotations

from copy import deepcopy

import pytest

from openlearn import interview_curriculum, interview_skills


CODING_SKILLS = (
    "concept.arrays-strings",
    "concept.hashing",
    "concept.constraint-reading",
    "concept.complexity-analysis",
    "pattern.two-pointers",
    "pattern.sliding-window",
    "pattern.prefix-suffix-aggregation",
    "pattern.intervals",
    "pattern.binary-search",
    "concept.linked-structures",
    "concept.stacks-queues",
    "concept.trees",
    "concept.heaps",
    "concept.tries",
    "concept.graphs",
    "pattern.bfs",
    "pattern.dfs",
    "concept.union-find",
    "pattern.topological-ordering",
    "concept.shortest-paths",
    "concept.recursion",
    "pattern.backtracking",
    "pattern.greedy",
    "concept.dynamic-programming-state",
    "pattern.dynamic-programming",
)

DESIGN_SKILLS = (
    "system.requirements-scope",
    "system.capacity-estimation",
    "system.api-contracts",
    "system.access-patterns-data-modeling",
    "system.storage-indexing",
    "system.replication-partitioning",
    "system.cache-invalidation",
    "system.content-delivery",
    "system.messaging-delivery",
    "system.idempotency-retries",
    "system.reliability-backpressure",
    "system.observability",
    "communication.system-tradeoffs",
)


def test_default_bundle_validates_exact_reviewed_route_snapshots() -> None:
    bundle = interview_curriculum.load_default_bundle()

    assert bundle.bundle_id == "technical-interview"
    assert bundle.bundle_version == "1.0.0"
    assert bundle.route("coding").skill_ids == CODING_SKILLS
    assert bundle.route("balanced").skill_ids == (
        CODING_SKILLS[:9]
        + DESIGN_SKILLS[:3]
        + CODING_SKILLS[9:14]
        + DESIGN_SKILLS[3:8]
        + CODING_SKILLS[14:20]
        + CODING_SKILLS[20:]
        + DESIGN_SKILLS[8:]
    )
    assert bundle.route("system-design").skill_ids == DESIGN_SKILLS + (
        "concept.constraint-reading",
        "concept.complexity-analysis",
        "concept.arrays-strings",
        "concept.hashing",
        "pattern.sliding-window",
    )
    assert bundle.route("coding").first_session_skill_id == "concept.arrays-strings"
    assert bundle.route("balanced").first_session_skill_id == "concept.arrays-strings"
    assert (
        bundle.route("system-design").first_session_skill_id
        == "system.requirements-scope"
    )


def test_bundle_has_unique_stable_structure_and_exact_embedded_contract() -> None:
    bundle = interview_curriculum.load_default_bundle()

    assert len({unit.unit_id for unit in bundle.units}) == len(bundle.units)
    sections = tuple(section for unit in bundle.units for section in unit.sections)
    assert len({section.section_id for section in sections}) == len(sections)
    assert bundle.confidence_mapping == {
        1: "learn",
        2: "learn",
        3: "practice",
        4: "review",
        5: "verify",
    }
    first = bundle.section("arrays-and-hashing")
    assert "Clarify input and output" in first.embedded_habit
    assert first.python_hooks == (
        "indexed traversal",
        "dictionaries",
        "sets",
        "counting",
        "enumerate",
    )
    assert "communication.problem-clarification" in first.embedded_skill_ids
    assert "process.edge-case-design" in first.embedded_skill_ids


def test_bundle_rejects_invalid_fields_missing_duplicate_and_unknown_ids() -> None:
    invalid_fields = interview_curriculum.load_bundle_dict()
    invalid_fields["unexpected"] = True
    with pytest.raises(interview_curriculum.CurriculumBundleError, match="fields"):
        interview_curriculum.validate_bundle(invalid_fields)

    duplicate = interview_curriculum.load_bundle_dict()
    duplicate["units"].append(deepcopy(duplicate["units"][0]))
    with pytest.raises(interview_curriculum.CurriculumBundleError, match="duplicate unit"):
        interview_curriculum.validate_bundle(duplicate)

    missing = interview_curriculum.load_bundle_dict()
    missing["routes"][0]["skill_refs"][0]["skill_id"] = "concept.missing"
    with pytest.raises(interview_curriculum.CurriculumBundleError, match="unknown skill"):
        interview_curriculum.validate_bundle(missing)

    applicability = interview_curriculum.load_bundle_dict()
    applicability["units"][0]["sections"][0]["applicability"]["required_for"] = [
        "invalid-focus"
    ]
    with pytest.raises(interview_curriculum.CurriculumBundleError, match="applicability"):
        interview_curriculum.validate_bundle(applicability)


def test_bundle_rejects_route_that_breaks_blocking_prerequisite_order() -> None:
    raw = interview_curriculum.load_bundle_dict()
    route = raw["routes"][0]["skill_refs"]
    hashing_index = next(
        index for index, ref in enumerate(route) if ref["skill_id"] == "concept.hashing"
    )
    window_index = next(
        index for index, ref in enumerate(route) if ref["skill_id"] == "pattern.sliding-window"
    )
    route[hashing_index], route[window_index] = route[window_index], route[hashing_index]

    with pytest.raises(
        interview_curriculum.CurriculumBundleError,
        match="blocking prerequisite",
    ):
        interview_curriculum.validate_bundle(raw)


def test_multi_graph_registry_requires_full_identity_for_new_evidence() -> None:
    bundle = interview_curriculum.load_default_bundle()
    registry = bundle.graph_registry
    supplement = registry.graph(
        "technical-interview-supplement",
        "1.0.0",
        "interview-mastery-v1",
    )
    problem = supplement.problem("problem.system-requirements")
    record = {
        "evidence_id": "ev-system-1",
        "graph_id": supplement.graph_id,
        "graph_version": supplement.graph_version,
        "mastery_policy_version": supplement.mastery_policy_version,
        "skill_id": "system.requirements-scope",
        "problem_id": problem.problem_id,
        "kind": "production",
        "outcome": "pass",
        "observed_at": "2026-08-12T12:00:00Z",
        "independent": True,
        "assistance": "none",
        "completion_state": "complete",
        "novel_context": True,
        "explicit_check": True,
        "transfer_family": problem.transfer_family,
    }

    validated = interview_skills.validate_evidence_record(record, registry)

    assert validated.graph is supplement
    assert validated.skill.skill_id == "system.requirements-scope"
    ambiguous = dict(record)
    ambiguous.pop("graph_id")
    with pytest.raises(interview_skills.EvidenceRecordError, match="graph_id"):
        interview_skills.validate_evidence_record(ambiguous, registry)


def test_historical_coding_graph_and_problem_catalog_remain_loadable() -> None:
    bundle = interview_curriculum.load_default_bundle()
    historical = bundle.graph_registry.graph(
        "coding-interview", "1.0.0", "interview-mastery-v1"
    )

    assert historical is not None
    assert historical.problem("problem.minimum-window-substring").title
