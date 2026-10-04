from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

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


def test_legacy_review_labels_map_to_stable_ids_and_preserve_raw_unmatched_context() -> None:
    bundle = interview_curriculum.load_default_bundle()
    route = _adaptive_route(interview_date="").to_dict()
    state = interview_curriculum.build_canonical_curriculum_state(
        bundle,
        route,
        metadata={"review_due": ["Arrays and Hashing", "Mystery legacy topic"]},
        dynamic_state={},
        source_fingerprint="source",
        reconciliation_id="reconcile",
    )

    assert state["evidence"]["due_review"] == [
        "concept.arrays-strings",
        "concept.hashing",
    ]
    assert state["legacy_context"]["aliases_applied"]["Arrays and Hashing"] == [
        "concept.arrays-strings",
        "concept.hashing",
    ]
    assert state["legacy_context"]["raw_review_due"] == [
        "Arrays and Hashing",
        "Mystery legacy topic",
    ]
    assert "review_due:Mystery legacy topic" in state["legacy_context"]["unassessed"]


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


def test_target_validator_allows_python_dotted_names_but_rejects_known_skill_ids() -> None:
    state = _canonical_progression_state()
    resolution = interview_curriculum.resolve_progression_target(state, intent="continue")
    assert resolution.target is not None
    target = resolution.target.to_dict()

    assert (
        interview_curriculum.target_response_error(
            "**Lesson:** Use collections.deque and dict.get for the implementation.",
            target,
        )
        is None
    )
    assert (
        interview_curriculum.target_response_error(
            "**Lesson:** Reason about indexed sequences, mutation, traversal, and "
            "boundary conditions.",
            target,
        )
        == "response copies formal skill description"
    )
    assert (
        interview_curriculum.target_response_error(
            "**Lesson:** Switch to concept.hashing next.",
            target,
        )
        == "response names a conflicting stable target"
    )
    assert target["bundle_id"] == "technical-interview"
    assert target["bundle_version"] == "1.0.0"

    assert (
        interview_curriculum.target_response_error(
            "**Lesson:** Arrays and strings are useful. Dynamic programming builds "
            "solutions from overlapping subproblems using a recurrence table.",
            target,
        )
        == "response names a conflicting stable target"
    )
    assert (
        interview_curriculum.target_response_error(
            "**Lesson:** Learn Arrays and strings by comparing them with Dynamic "
            "programming.",
            target,
        )
        is None
    )


def test_target_validator_rejects_wrong_skill_in_labeled_move_without_blocking_comparison() -> None:
    state = _canonical_progression_state()
    resolution = interview_curriculum.resolve_progression_target(state, intent="continue")
    assert resolution.target is not None
    target = resolution.target.to_dict()

    assert (
        interview_curriculum.target_response_error(
            "**Check:** Explain the recurrence for Dynamic programming.",
            target,
        )
        == "response names a conflicting stable target"
    )
    assert (
        interview_curriculum.target_response_error(
            "**Example:** Unlike Dynamic programming, Arrays and strings can be traversed "
            "directly by index for this problem.",
            target,
        )
        is None
    )


def test_judged_evidence_uses_full_identity_and_preserves_due_until_policy_ready() -> None:
    state = _canonical_progression_state()
    resolution = interview_curriculum.resolve_progression_target(state, intent="continue")
    assert resolution.target is not None
    target_ref = dict(resolution.target.skill_ref)
    state = resolution.state
    state["evidence"]["due_review"] = [target_ref["skill_id"]]

    weak = interview_curriculum.apply_answer_judgment(
        state,
        {
            "skill_ref": target_ref,
            "status": "needs_work",
            "score": 0.2,
            "answer_kind": "production",
            "is_transfer": False,
        },
        evidence_id="turn_weak",
        observed_at="2026-08-13T12:00:00+00:00",
    )
    assert target_ref["skill_id"] in weak["evidence"]["weak"]
    assert target_ref["skill_id"] in weak["evidence"]["due_review"]
    assert weak["evidence"]["answer_evidence"][-1]["skill_ref"] == target_ref

    correct = interview_curriculum.apply_answer_judgment(
        weak,
        {
            "skill_ref": target_ref,
            "status": "correct",
            "score": 1.0,
            "answer_kind": "production",
            "is_transfer": True,
        },
        evidence_id="turn_transfer",
        observed_at="2026-08-13T12:05:00+00:00",
    )
    identity = interview_curriculum.target_identity({"skill_ref": target_ref})
    assert correct["evidence"]["readiness"][identity]["status"] == "provisional"
    assert target_ref["skill_id"] not in correct["evidence"]["ready"]
    assert target_ref["skill_id"] in correct["evidence"]["due_review"]
    assert correct["evidence"]["answer_evidence"][-1]["kinds"] == ["explanation"]


def test_pinned_evidence_kinds_reach_readiness_once_per_distinct_evidence_id() -> None:
    resolution = interview_curriculum.resolve_progression_target(
        _canonical_progression_state(), intent="continue"
    )
    assert resolution.target is not None
    state = resolution.state
    target_ref = dict(resolution.target.skill_ref)

    moments = (
        "2026-08-13T12:00:00+00:00",
        "2026-08-13T12:05:00+00:00",
        "2026-08-20T12:00:00+00:00",
    )
    for index, (kind, observed_at) in enumerate(
        zip(("explanation", "transfer", "delayed_retrieval"), moments, strict=True)
    ):
        state = interview_curriculum.apply_answer_judgment(
            state,
            {
                "skill_ref": target_ref,
                "status": "correct",
                "score": 1.0,
                "evidence_kind": kind,
            },
            evidence_id=f"turn_{index}",
            observed_at=observed_at,
        )
    identity = interview_curriculum.target_identity({"skill_ref": target_ref})
    assert state["evidence"]["readiness"][identity]["status"] == "ready"
    assert target_ref["skill_id"] in state["evidence"]["ready"]

    replayed = interview_curriculum.apply_answer_judgment(
        state,
        {
            "skill_ref": target_ref,
            "status": "correct",
            "score": 1.0,
            "evidence_kind": "delayed_retrieval",
        },
        evidence_id="turn_2",
        observed_at="2026-08-20T12:00:00+00:00",
    )
    assert len(replayed["evidence"]["answer_evidence"]) == 3
    assert replayed["evidence"]["readiness"][identity]["counts"] == {
        "recognition": 0,
        "explanation": 1,
        "production": 0,
        "transfer": 1,
        "delayed_retrieval": 1,
    }


def test_readiness_requires_full_provenance_and_exact_delay_boundary() -> None:
    resolution = interview_curriculum.resolve_progression_target(
        _canonical_progression_state(), intent="continue"
    )
    assert resolution.target is not None
    state = resolution.state
    target_ref = dict(resolution.target.skill_ref)
    first_at = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)

    def judge(
        current: dict[str, object],
        kind: str,
        evidence_id: str,
        observed_at: datetime,
        **provenance: object,
    ) -> dict[str, object]:
        return interview_curriculum.apply_answer_judgment(
            current,
            {
                "skill_ref": target_ref,
                "status": "correct",
                "score": 1.0,
                "evidence_kind": kind,
                **provenance,
            },
            evidence_id=evidence_id,
            observed_at=observed_at.isoformat(),
        )

    state = judge(state, "explanation", "explanation", first_at)
    state = judge(state, "transfer", "transfer", first_at + timedelta(minutes=1))
    too_early = judge(
        state,
        "delayed_retrieval",
        "delayed-too-early",
        first_at + timedelta(days=7) - timedelta(seconds=1),
    )
    identity = interview_curriculum.target_identity({"skill_ref": target_ref})
    assert too_early["evidence"]["readiness"][identity]["status"] == "provisional"

    on_boundary = judge(
        state,
        "delayed_retrieval",
        "delayed-on-boundary",
        first_at + timedelta(days=7),
    )
    assert on_boundary["evidence"]["readiness"][identity]["status"] == "ready"

    assisted = judge(
        state,
        "delayed_retrieval",
        "delayed-assisted",
        first_at + timedelta(days=8),
        assistance="worked_example",
        independent=False,
    )
    assert assisted["evidence"]["readiness"][identity]["status"] == "provisional"

    incomplete = judge(
        state,
        "delayed_retrieval",
        "delayed-incomplete",
        first_at + timedelta(days=8),
        completion_state="partial",
    )
    assert incomplete["evidence"]["readiness"][identity]["status"] == "provisional"


def test_evidence_replay_is_idempotent_but_conflicting_duplicate_is_rejected() -> None:
    resolution = interview_curriculum.resolve_progression_target(
        _canonical_progression_state(), intent="continue"
    )
    assert resolution.target is not None
    target_ref = dict(resolution.target.skill_ref)
    event = {
        "skill_ref": target_ref,
        "status": "correct",
        "score": 1.0,
        "evidence_kind": "explanation",
    }
    state = interview_curriculum.apply_answer_judgment(
        resolution.state,
        event,
        evidence_id="stable-evidence-id",
        observed_at="2026-08-13T12:00:00+00:00",
    )
    replayed = interview_curriculum.apply_answer_judgment(
        state,
        event,
        evidence_id="stable-evidence-id",
        observed_at="2026-08-13T12:00:00+00:00",
    )
    assert replayed["evidence"]["answer_evidence"] == state["evidence"]["answer_evidence"]

    with pytest.raises(interview_curriculum.CurriculumBundleError, match="conflicting"):
        interview_curriculum.apply_answer_judgment(
            state,
            {**event, "evidence_kind": "transfer"},
            evidence_id="stable-evidence-id",
            observed_at="2026-08-13T12:00:00+00:00",
        )


def test_readiness_refresh_marks_stale_delayed_retrieval_due() -> None:
    resolution = interview_curriculum.resolve_progression_target(
        _canonical_progression_state(), intent="continue"
    )
    assert resolution.target is not None
    state = resolution.state
    target_ref = dict(resolution.target.skill_ref)
    first_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    for kind, evidence_id, observed_at in (
        ("explanation", "explanation", first_at),
        ("transfer", "transfer", first_at + timedelta(minutes=1)),
        ("delayed_retrieval", "delayed", first_at + timedelta(days=7)),
    ):
        state = interview_curriculum.apply_answer_judgment(
            state,
            {
                "skill_ref": target_ref,
                "status": "correct",
                "score": 1.0,
                "evidence_kind": kind,
            },
            evidence_id=evidence_id,
            observed_at=observed_at.isoformat(),
        )

    refreshed = interview_curriculum.resolve_progression_target(
        state,
        intent="continue",
        now=first_at + timedelta(days=68),
    )
    assert target_ref["skill_id"] in refreshed.state["evidence"]["due_review"]
    assert target_ref["skill_id"] not in refreshed.state["evidence"]["ready"]


def test_correct_stale_review_refreshes_schedule_until_next_interval() -> None:
    resolution = interview_curriculum.resolve_progression_target(
        _canonical_progression_state(), intent="continue"
    )
    assert resolution.target is not None
    state = resolution.state
    target_ref = dict(resolution.target.skill_ref)
    first_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    for kind, evidence_id, observed_at in (
        ("explanation", "explanation", first_at),
        ("transfer", "transfer", first_at + timedelta(minutes=1)),
        ("delayed_retrieval", "delayed", first_at + timedelta(days=7)),
    ):
        state = interview_curriculum.apply_answer_judgment(
            state,
            {
                "skill_ref": target_ref,
                "status": "correct",
                "score": 1.0,
                "evidence_kind": kind,
            },
            evidence_id=evidence_id,
            observed_at=observed_at.isoformat(),
        )

    stale_at = first_at + timedelta(days=68)
    stale = interview_curriculum.resolve_progression_target(
        state, intent="continue", now=stale_at
    )
    assert stale.target is not None
    assert stale.target.skill_id == target_ref["skill_id"]
    assert stale.reason == "due_review"
    assert stale.target.evidence_kind == "delayed_retrieval"

    reviewed = interview_curriculum.apply_answer_judgment(
        stale.state,
        {
            "skill_ref": target_ref,
            "status": "correct",
            "score": 1.0,
            "evidence_kind": stale.target.evidence_kind,
        },
        evidence_id="refreshed-delayed",
        observed_at=stale_at.isoformat(),
    )
    assert target_ref["skill_id"] not in reviewed["evidence"]["due_review"]
    assert target_ref["skill_id"] in reviewed["evidence"]["ready"]
    bundle = interview_curriculum.load_default_bundle()
    graph = bundle.graph_registry.graph(
        target_ref["graph_id"],
        target_ref["graph_version"],
        target_ref["mastery_policy_version"],
    )
    due_after_days = graph.skill(
        target_ref["skill_id"]
    ).evidence_policy.transfer.due_after_days

    before_next_interval = interview_curriculum.resolve_progression_target(
        reviewed,
        intent="continue",
        now=stale_at + timedelta(days=due_after_days),
    )
    assert target_ref["skill_id"] not in before_next_interval.state["evidence"][
        "due_review"
    ]

    next_interval = interview_curriculum.resolve_progression_target(
        reviewed,
        intent="continue",
        now=stale_at + timedelta(days=due_after_days, seconds=1),
    )
    assert next_interval.target is not None
    assert next_interval.target.skill_id == target_ref["skill_id"]
    assert next_interval.reason == "due_review"


def test_check_targets_pin_problem_identity_and_active_depth() -> None:
    state = _canonical_progression_state()
    first = interview_curriculum.resolve_progression_target(state, intent="continue")
    assert first.target is not None
    assert first.target.problem_id.startswith("problem.")
    assert first.target.transfer_family
    assert first.state["committed_check_target"]["problem_id"] == first.target.problem_id

    state = interview_curriculum.record_progression_commit(first.state, first.target.skill_id)
    practice = interview_curriculum.resolve_progression_target(state, intent="practice")
    assert practice.target is not None
    assert practice.target.depth_mode == "practice"
    assert "**Check:**" in interview_curriculum.deterministic_target_fallback(
        practice.target.to_dict()
    )
    assert (
        interview_curriculum.target_response_error(
            "**Lesson:** A passive recap.", practice.target.to_dict()
        )
        == "retrieval target has no Check"
    )
    assert (
        interview_curriculum.target_response_error(
            "**Check:** Explain the invariant.", practice.target.to_dict()
        )
        == "practice target has no teaching content"
    )
    practice_fallback = interview_curriculum.deterministic_target_fallback(
        practice.target.to_dict()
    )
    assert "**Lesson:**" in practice_fallback
    assert "**Example:**" in practice_fallback
    assert "**Check:**" in practice_fallback
    assert "starts at 0" in practice_fallback
    assert "'cat'" in practice_fallback
    assert "indexed sequences" not in practice_fallback
    assert "representation" not in practice_fallback
    assert "invariant" not in practice_fallback
    assert "indexed traversal" not in practice_fallback
    assert (
        interview_curriculum.target_response_error(
            "**Feedback:**\nThe key invariant is that positions stay fixed.\n\n"
            "**Check:**\nWhich value is at position 1?",
            practice.target.to_dict(),
        )
        == "response uses invariant without defining it"
    )
    assert (
        interview_curriculum.target_response_error(
            "**Feedback:**\nAn invariant is a rule that stays true while an algorithm "
            "runs. Here, each value stays at its numbered position.\n\n"
            "**Check:**\nWhich value is at position 1?",
            practice.target.to_dict(),
        )
        is None
    )

    revisit = interview_curriculum.resolve_progression_target(
        state, intent="revisit", explicit_skill_id=first.target.skill_id
    )
    assert revisit.target is not None
    assert revisit.target.depth_mode == "review"


def test_practice_commit_persists_target_independent_of_forward_cursor() -> None:
    state = _canonical_progression_state()
    route = state["route"]
    assert isinstance(route, dict)
    second = route["skills"][1]
    state["evidence"]["exposed"] = [second["skill_ref"]["skill_id"]]
    cursor = deepcopy(state["cursor"])
    practice = interview_curriculum.resolve_progression_target(state, intent="practice")
    assert practice.target is not None

    committed = interview_curriculum.record_progression_commit(
        practice.state, practice.target.skill_id
    )

    assert committed["cursor"] == cursor
    assert committed["committed_target"]["skill_ref"] == dict(practice.target.skill_ref)
    assert set(committed["committed_target"]["skill_ref"]) == {
        "graph_id",
        "graph_version",
        "mastery_policy_version",
        "skill_id",
    }


def test_incorrect_required_evidence_remains_due_and_unready() -> None:
    resolution = interview_curriculum.resolve_progression_target(
        _canonical_progression_state(), intent="continue"
    )
    assert resolution.target is not None
    state = resolution.state
    target_ref = dict(resolution.target.skill_ref)
    state["evidence"]["due_review"] = [target_ref["skill_id"]]

    judged = interview_curriculum.apply_answer_judgment(
        state,
        {
            "skill_ref": target_ref,
            "status": "needs_work",
            "score": 0.0,
            "evidence_kind": resolution.target.evidence_kind,
        },
        evidence_id="turn_wrong",
        observed_at="2026-08-13T12:00:00+00:00",
    )
    assert judged["evidence"]["answer_evidence"][-1]["kinds"] == []
    assert target_ref["skill_id"] in judged["evidence"]["weak"]
    assert target_ref["skill_id"] in judged["evidence"]["due_review"]
    assert target_ref["skill_id"] not in judged["evidence"]["ready"]


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


def test_skip_practice_defers_visible_target_without_moving_forward_cursor() -> None:
    state = _canonical_progression_state()
    route = state["route"]
    assert isinstance(route, dict)
    practice_skill = route["skills"][1]["skill_ref"]["skill_id"]
    state["evidence"]["exposed"] = [practice_skill]
    practice = interview_curriculum.resolve_progression_target(state, intent="practice")
    assert practice.target is not None
    assert practice.target.skill_id == practice_skill
    cursor_before = deepcopy(practice.state["cursor"])
    evidence_before = deepcopy(practice.state["evidence"])

    skipped = interview_curriculum.resolve_progression_target(
        practice.state, intent="skip", session_id="same-session"
    )

    assert skipped.deferred_skill_id == practice_skill
    assert skipped.target is not None
    assert skipped.target.skill_id != practice_skill
    assert skipped.state["cursor"] == cursor_before
    assert skipped.state["evidence"] == evidence_before
    assert [item["skill_id"] for item in skipped.state["deferred"]] == [
        practice_skill
    ]


def test_repeated_skip_defers_each_visible_due_target_without_moving_cursor() -> None:
    state = _canonical_progression_state()
    route = state["route"]
    assert isinstance(route, dict)
    due_skills = [
        route["skills"][index]["skill_ref"]["skill_id"] for index in (1, 2)
    ]
    state["evidence"]["exposed"] = list(due_skills)
    state["evidence"]["due_review"] = list(due_skills)
    cursor_before = deepcopy(state["cursor"])

    practice = interview_curriculum.resolve_progression_target(
        state, intent="practice", session_id="same-session"
    )
    assert practice.target is not None
    assert practice.target.skill_id == due_skills[0]

    first_skip = interview_curriculum.resolve_progression_target(
        practice.state, intent="skip", session_id="same-session"
    )
    assert first_skip.deferred_skill_id == due_skills[0]
    assert first_skip.target is not None
    assert first_skip.target.skill_id == due_skills[1]
    evidence_before_second_skip = deepcopy(first_skip.state["evidence"])

    second_skip = interview_curriculum.resolve_progression_target(
        first_skip.state, intent="skip", session_id="same-session"
    )
    assert second_skip.deferred_skill_id == due_skills[1]
    assert second_skip.target is not None
    assert second_skip.target.skill_id not in due_skills
    assert second_skip.state["cursor"] == cursor_before
    assert second_skip.state["evidence"] == evidence_before_second_skip
    assert [item["skill_id"] for item in second_skip.state["deferred"]] == due_skills


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


def test_practice_commit_does_not_cover_the_forward_cursor() -> None:
    state = _canonical_progression_state()
    cursor_before = deepcopy(state["cursor"])

    committed = interview_curriculum.record_progression_commit(
        state, "concept.hashing"
    )

    assert committed["cursor"] == cursor_before
    assert "concept.hashing" in committed["evidence"]["exposed"]


def test_route_change_returns_to_exposed_but_unready_prerequisite() -> None:
    state = _canonical_progression_state()
    route = deepcopy(state["route"])
    first = route["skills"][0]
    evidence = state["evidence"]
    assert isinstance(evidence, dict)
    evidence["exposed"] = [first["skill_ref"]["skill_id"]]
    evidence["ready"] = []
    removed_ref = deepcopy(first["skill_ref"])
    removed_ref["skill_id"] = "removed-skill"
    state["cursor"] = {
        "unit_id": "removed-unit",
        "section_id": "removed-section",
        "skill_ref": removed_ref,
        "instruction_status": "covered",
    }

    changed, decision = interview_curriculum.rematerialize_canonical_state(
        state, route, change_id="change-exposed-unready"
    )

    assert decision == "earliest-eligible-unmet-prerequisite"
    assert changed["cursor"]["skill_ref"] == first["skill_ref"]


def test_route_change_retires_removed_committed_check_target() -> None:
    state = _canonical_progression_state()
    route = deepcopy(state["route"])
    removed = route["skills"].pop()
    state["committed_check_target"] = {
        "skill_ref": deepcopy(removed["skill_ref"]),
        "evidence_kind": "production",
        "problem_id": "problem.minimum-window-substring",
        "transfer_family": "window-counts",
    }

    changed, _decision = interview_curriculum.rematerialize_canonical_state(
        state, route, change_id="remove-check-target"
    )

    assert "committed_check_target" not in changed
    retirement = changed["route_history"][-1]["retired_check_target"]
    assert retirement["reason"] == "target_removed_from_route"
    assert retirement["skill_ref"] == removed["skill_ref"]


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


def test_practice_targets_due_then_weak_and_rotates_without_moving_cursor() -> None:
    state = _canonical_progression_state()
    route = state["route"]
    assert isinstance(route, dict)
    skills = route["skills"]
    assert isinstance(skills, list)
    ids = [item["skill_ref"]["skill_id"] for item in skills[:3]]
    state["evidence"] = {
        "ready": [],
        "exposed": ids,
        "weak": [ids[1]],
        "due_review": [ids[2]],
    }
    cursor = deepcopy(state["cursor"])

    due = interview_curriculum.resolve_progression_target(state, intent="practice")
    assert due.target is not None and due.target.skill_id == ids[2]
    state["evidence"]["due_review"] = []
    weak = interview_curriculum.resolve_progression_target(state, intent="practice")
    assert weak.target is not None and weak.target.skill_id == ids[1]
    state["evidence"]["weak"] = []
    first = interview_curriculum.resolve_progression_target(state, intent="practice")
    assert first.target is not None
    committed = interview_curriculum.record_progression_commit(
        first.state, first.target.skill_id
    )
    second = interview_curriculum.resolve_progression_target(
        committed, intent="practice"
    )
    assert second.target is not None
    assert second.target.skill_id != first.target.skill_id
    assert second.state["cursor"] == cursor


def test_practice_answer_updates_committed_target_not_forward_cursor() -> None:
    state = _canonical_progression_state()
    route = state["route"]
    assert isinstance(route, dict)
    skills = route["skills"]
    assert isinstance(skills, list)
    practice_id = skills[1]["skill_ref"]["skill_id"]
    state["evidence"]["exposed"] = [practice_id]
    cursor = deepcopy(state["cursor"])
    practice = interview_curriculum.resolve_progression_target(state, intent="practice")
    assert practice.target is not None and practice.target.skill_id == practice_id

    correct = interview_curriculum.apply_answer_judgment(
        practice.state,
        {
            "skill_ref": dict(practice.target.skill_ref),
            "status": "correct",
            "score": 1.0,
            "evidence_kind": practice.target.evidence_kind,
        },
        evidence_id="practice_correct",
        observed_at="2026-08-13T12:00:00+00:00",
    )
    assert correct["cursor"] == cursor
    assert correct["evidence"]["answer_evidence"][-1]["skill_ref"]["skill_id"] == practice_id

    wrong = interview_curriculum.apply_answer_judgment(
        practice.state,
        {
            "skill_ref": dict(practice.target.skill_ref),
            "status": "needs_work",
            "score": 0.0,
            "evidence_kind": practice.target.evidence_kind,
        },
        evidence_id="practice_wrong",
        observed_at="2026-08-13T12:01:00+00:00",
    )
    assert wrong["cursor"] == cursor
    assert practice_id in wrong["evidence"]["weak"]
    assert cursor["skill_ref"]["skill_id"] not in wrong["evidence"]["weak"]


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
