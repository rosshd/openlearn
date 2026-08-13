from __future__ import annotations

from copy import deepcopy
from datetime import date

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
    assert bundle.route("system-design").first_session_skill_id == "system.requirements-scope"


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
    applicability["units"][0]["sections"][0]["applicability"]["required_for"] = ["invalid-focus"]
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
    historical = bundle.graph_registry.graph("coding-interview", "1.0.0", "interview-mastery-v1")

    assert historical is not None
    assert historical.problem("problem.minimum-window-substring").title


def _adaptive_route(
    *,
    focus: str = "coding",
    role: str = "backend",
    level: str = "entry",
    interview_date: str = "2026-08-20",
    weekly_minutes: int = 120,
    session_minutes: int = 45,
    pacing_override: str | None = None,
    ratings: dict[str, int] | None = None,
) -> interview_curriculum.MaterializedInterviewRoute:
    return interview_curriculum.materialize_adaptive_route(
        interview_curriculum.load_default_bundle(),
        role_family=role,
        target_level=level,
        interview_focus=focus,
        interview_date=interview_date,
        weekly_minutes=weekly_minutes,
        session_minutes=session_minutes,
        confidence_ratings=ratings or {},
        pacing_posture_override=pacing_override,
        current_date=date(2026, 8, 12),
    )


def test_adaptive_route_maps_confidence_to_depth_without_mastery() -> None:
    route = _adaptive_route(
        ratings={
            "arrays_hashing": 1,
            "sliding_window": 5,
        }
    )

    assert route.date_horizon == "accelerated"
    assert route.recommended_pacing_posture == "accelerated"
    assert route.pacing_posture == "accelerated"
    assert route.first_session.skill_ref.skill_id == "concept.arrays-strings"
    assert "Clarify input and output" in route.first_session.embedded_habit
    assert route.skill("concept.arrays-strings").depth_mode == "learn"
    assert route.skill("pattern.sliding-window").depth_mode == "verify"
    assert route.skill("backend.api-boundaries").requirement == "optional"
    serialized = route.to_dict()
    assert "mastery" not in serialized
    assert "mastered_skills" not in serialized
    assert "evidence" not in serialized


@pytest.mark.parametrize(
    ("interview_date", "expected"),
    [
        ("2026-08-11", "long-term"),
        ("2026-08-12", "accelerated"),
        ("2026-09-09", "accelerated"),
        ("2026-09-10", "near-term"),
        ("2026-11-05", "open-ended"),
        ("", "open-ended"),
    ],
)
def test_adaptive_route_uses_four_date_horizons(
    interview_date: str,
    expected: str,
) -> None:
    assert _adaptive_route(interview_date=interview_date).date_horizon == expected


def test_standard_override_changes_only_allocation_overlay() -> None:
    accelerated = _adaptive_route()
    standard = _adaptive_route(pacing_override="standard")

    assert accelerated.route_fingerprint == standard.route_fingerprint
    assert accelerated.skill_refs == standard.skill_refs
    assert accelerated.prerequisite_edges == standard.prerequisite_edges
    assert accelerated.allocation_fingerprint != standard.allocation_fingerprint
    assert standard.pacing_posture == "standard"
    assert sum(item.weekly_minutes > 0 for item in standard.skills) > sum(
        item.weekly_minutes > 0 for item in accelerated.skills
    )


def test_time_allocation_changes_budget_without_reordering_route() -> None:
    small = _adaptive_route(weekly_minutes=60, session_minutes=30)
    large = _adaptive_route(weekly_minutes=240, session_minutes=60)

    assert small.skill_refs == large.skill_refs
    assert small.route_fingerprint == large.route_fingerprint
    assert sum(item.weekly_minutes for item in small.skills) == 60
    assert sum(item.weekly_minutes for item in large.skills) == 240
    assert small.allocation_fingerprint != large.allocation_fingerprint


def test_focus_role_and_level_select_only_applicable_versioned_skills() -> None:
    coding = _adaptive_route(focus="coding", role="general SWE", level="entry")
    system = _adaptive_route(
        focus="system_design",
        role="backend",
        level="senior",
    )
    senior_coding = _adaptive_route(focus="coding", level="senior")

    assert coding.first_session.skill_ref.skill_id == "concept.arrays-strings"
    assert not any(ref.skill_id.startswith("system.") for ref in coding.skill_refs)
    assert not any(ref.skill_id.startswith("backend.") for ref in coding.skill_refs)
    assert system.first_session.skill_ref.skill_id == "system.requirements-scope"
    assert any(ref.skill_id == "backend.api-boundaries" for ref in system.skill_refs)
    assert not any(ref.skill_id.startswith("frontend.") for ref in system.skill_refs)
    assert coding.skill("concept.tries").requirement == "optional"
    assert senior_coding.skill("concept.tries").requirement == "required"


def test_materialization_is_deterministic_and_rejects_invalid_inputs() -> None:
    first = _adaptive_route(ratings={"graphs": 2})
    second = _adaptive_route(ratings={"graphs": 2})

    assert first.to_dict() == second.to_dict()
    assert first.allocation_fingerprint == second.allocation_fingerprint
    with pytest.raises(ValueError, match="pacing"):
        _adaptive_route(pacing_override="fast")
    with pytest.raises(ValueError, match="confidence"):
        _adaptive_route(ratings={"graphs": 6})


def _canonical_progression_state() -> dict[str, object]:
    bundle = interview_curriculum.load_default_bundle()
    route = _adaptive_route(interview_date="").to_dict()
    return interview_curriculum.build_canonical_curriculum_state(
        bundle,
        route,
        metadata={},
        dynamic_state={},
        source_fingerprint="source",
        reconciliation_id="reconcile",
    )


def test_progression_resolver_selects_uncovered_and_due_targets_deterministically() -> None:
    state = _canonical_progression_state()

    first = interview_curriculum.resolve_progression_target(state, intent="continue")
    assert first.target is not None
    assert first.target.skill_id == "concept.arrays-strings"
    assert first.reason == "uncovered_required"

    evidence = state["evidence"]
    assert isinstance(evidence, dict)
    evidence["exposed"] = ["concept.arrays-strings"]
    evidence["due_review"] = ["concept.arrays-strings"]
    due = interview_curriculum.resolve_progression_target(state, intent="continue")
    assert due.target is not None
    assert due.target.skill_id == "concept.arrays-strings"
    assert due.reason == "due_review"


def test_skip_defers_without_mastery_and_returns_only_after_another_commit() -> None:
    state = _canonical_progression_state()

    skipped = interview_curriculum.resolve_progression_target(state, intent="skip")
    assert skipped.target is not None
    assert skipped.target.skill_id == "concept.hashing"
    assert skipped.deferred_skill_id == "concept.arrays-strings"
    assert "concept.arrays-strings" not in skipped.state["evidence"]["ready"]
    assert "concept.arrays-strings" not in skipped.state["evidence"]["exposed"]

    before_commit = interview_curriculum.resolve_progression_target(
        skipped.state, intent="continue"
    )
    assert before_commit.target is not None
    assert before_commit.target.skill_id != "concept.arrays-strings"

    after_commit = interview_curriculum.record_progression_commit(
        skipped.state, "concept.hashing"
    )
    returned = interview_curriculum.resolve_progression_target(
        after_commit, intent="continue"
    )
    assert returned.target is not None
    assert returned.target.skill_id == "concept.arrays-strings"
    assert returned.reason == "deferred_return"


def test_instructional_commit_does_not_clear_due_or_weak_evidence() -> None:
    state = _canonical_progression_state()
    evidence = state["evidence"]
    assert isinstance(evidence, dict)
    evidence["due_review"] = ["concept.arrays-strings"]
    evidence["weak"] = ["concept.arrays-strings"]

    committed = interview_curriculum.record_progression_commit(
        state, "concept.arrays-strings"
    )

    assert committed["evidence"]["due_review"] == ["concept.arrays-strings"]
    assert committed["evidence"]["weak"] == ["concept.arrays-strings"]


def test_caught_up_practice_selects_covered_skill_without_moving_forward_cursor() -> None:
    state = _canonical_progression_state()
    route = state["route"]
    assert isinstance(route, dict)
    skills = route["skills"]
    assert isinstance(skills, list)
    skill_ids = [item["skill_ref"]["skill_id"] for item in skills]
    state["evidence"] = {
        "ready": list(skill_ids),
        "exposed": list(skill_ids),
        "weak": [],
        "due_review": [],
    }
    original_cursor = deepcopy(state["cursor"])

    caught_up = interview_curriculum.resolve_progression_target(state, intent="continue")
    assert caught_up.target is None
    assert caught_up.caught_up is True

    practice = interview_curriculum.resolve_progression_target(state, intent="practice")
    assert practice.target is not None
    assert practice.reason == "practice_now"
    assert practice.state["cursor"] == original_cursor


def test_compatibility_projection_uses_full_cursor_identity_within_one_section() -> None:
    state = _canonical_progression_state()
    route = state["route"]
    assert isinstance(route, dict)
    skills = route["skills"]
    assert isinstance(skills, list)
    first = skills[0]
    second = skills[1]
    assert first["section_id"] == second["section_id"]
    state["cursor"] = {
        "unit_id": first["unit_id"],
        "section_id": first["section_id"],
        "skill_ref": deepcopy(first["skill_ref"]),
        "instruction_status": "uncovered",
    }

    projection = interview_curriculum.compatibility_projection(state)

    assert projection["current_slide"] == 1
