"""Versioned, deterministic curriculum authority for Technical Interview Prep.

This module is intentionally output-free and storage-free. It loads immutable
package assets, validates their cross-graph references, and exposes stable route
projections for application-layer progression code.
"""

from __future__ import annotations

import importlib.resources
import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from importlib.resources.abc import Traversable
from types import MappingProxyType
from typing import Literal

from openlearn import interview_skills


FOCUSES = ("coding", "balanced", "system-design")
DEPTH_MODES = ("learn", "practice", "review", "verify")
LEVELS = ("intern", "entry", "mid", "senior", "staff")
STABLE_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
RESOURCE_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.json")
PACING_POSTURES = ("accelerated", "standard")
DATE_HORIZONS = ("long-term", "accelerated", "near-term", "open-ended")

CONFIDENCE_AREA_SKILLS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "arrays_hashing": ("concept.arrays-strings", "concept.hashing"),
        "two_pointers": ("pattern.two-pointers",),
        "sliding_window": ("pattern.sliding-window",),
        "stack": ("concept.stacks-queues",),
        "binary_search": ("pattern.binary-search",),
        "linked_lists": ("concept.linked-structures",),
        "trees": ("concept.trees",),
        "graphs": ("concept.graphs", "pattern.bfs", "pattern.dfs"),
        "heaps": ("concept.heaps",),
        "backtracking": ("concept.recursion", "pattern.backtracking"),
        "dynamic_programming": (
            "concept.dynamic-programming-state",
            "pattern.dynamic-programming",
        ),
        "intervals_greedy": ("pattern.intervals", "pattern.greedy"),
        "requirements_scope": ("system.requirements-scope",),
        "capacity_estimation": ("system.capacity-estimation",),
        "api_design": ("system.api-contracts",),
        "data_modeling": ("system.access-patterns-data-modeling",),
        "databases_partitioning": (
            "system.storage-indexing",
            "system.replication-partitioning",
        ),
        "caching_delivery": (
            "system.cache-invalidation",
            "system.content-delivery",
        ),
        "messaging_async": (
            "system.messaging-delivery",
            "system.idempotency-retries",
        ),
        "reliability_observability": (
            "system.reliability-backpressure",
            "system.observability",
        ),
        "tradeoff_communication": ("communication.system-tradeoffs",),
    }
)


class CurriculumBundleError(ValueError):
    """A bundled interview curriculum is missing or internally inconsistent."""


@dataclass(frozen=True)
class CurriculumGraphReference:
    alias: str
    graph_id: str
    graph_version: str
    mastery_policy_version: str
    resource: str


@dataclass(frozen=True)
class CurriculumSkillReference:
    graph_id: str
    graph_version: str
    mastery_policy_version: str
    skill_id: str

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (
            self.graph_id,
            self.graph_version,
            self.mastery_policy_version,
            self.skill_id,
        )


@dataclass(frozen=True)
class CurriculumApplicability:
    required_for: tuple[str, ...]
    optional_for: tuple[str, ...]
    minimum_required_level: str | None


@dataclass(frozen=True)
class CurriculumSection:
    section_id: str
    label: str
    skill_refs: tuple[CurriculumSkillReference, ...]
    applicability: CurriculumApplicability
    embedded_habit: str
    python_hooks: tuple[str, ...]
    embedded_skill_refs: tuple[CurriculumSkillReference, ...]

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(ref.skill_id for ref in self.skill_refs)

    @property
    def embedded_skill_ids(self) -> tuple[str, ...]:
        return tuple(ref.skill_id for ref in self.embedded_skill_refs)


@dataclass(frozen=True)
class CurriculumUnit:
    unit_id: str
    label: str
    sections: tuple[CurriculumSection, ...]


@dataclass(frozen=True)
class CurriculumRoute:
    route_id: str
    label: str
    skill_refs: tuple[CurriculumSkillReference, ...]
    first_session_skill_id: str

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(ref.skill_id for ref in self.skill_refs)


@dataclass(frozen=True)
class CurriculumRoleExtension:
    role_family: str
    section_id: str
    skill_refs: tuple[CurriculumSkillReference, ...]

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(ref.skill_id for ref in self.skill_refs)


@dataclass(frozen=True)
class MaterializedInterviewSkill:
    skill_ref: CurriculumSkillReference
    unit_id: str
    unit_label: str
    section_id: str
    section_label: str
    requirement: str
    depth_mode: str
    weekly_minutes: int
    embedded_habit: str
    python_hooks: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "skill_ref": _ref_dict(self.skill_ref),
            "unit_id": self.unit_id,
            "unit_label": self.unit_label,
            "section_id": self.section_id,
            "section_label": self.section_label,
            "requirement": self.requirement,
            "depth_mode": self.depth_mode,
            "weekly_minutes": self.weekly_minutes,
            "embedded_habit": self.embedded_habit,
            "python_hooks": list(self.python_hooks),
        }


@dataclass(frozen=True)
class MaterializedInterviewRoute:
    bundle_id: str
    bundle_version: str
    route_id: str
    role_family: str
    target_level: str
    date_horizon: str
    recommended_pacing_posture: str
    pacing_posture: str
    weekly_minutes: int
    session_minutes: int
    route_fingerprint: str
    allocation_fingerprint: str
    skills: tuple[MaterializedInterviewSkill, ...]
    prerequisite_edges: tuple[tuple[str, str], ...]

    @property
    def skill_refs(self) -> tuple[CurriculumSkillReference, ...]:
        return tuple(item.skill_ref for item in self.skills)

    @property
    def first_session(self) -> MaterializedInterviewSkill:
        return self.skills[0]

    def skill(self, skill_id: str) -> MaterializedInterviewSkill:
        for item in self.skills:
            if item.skill_ref.skill_id == skill_id:
                return item
        raise CurriculumBundleError(f"skill is absent from materialized route: {skill_id}")

    def to_dict(self) -> dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "route_id": self.route_id,
            "role_family": self.role_family,
            "target_level": self.target_level,
            "date_horizon": self.date_horizon,
            "recommended_pacing_posture": self.recommended_pacing_posture,
            "pacing_posture": self.pacing_posture,
            "weekly_minutes": self.weekly_minutes,
            "session_minutes": self.session_minutes,
            "route_fingerprint": self.route_fingerprint,
            "allocation_fingerprint": self.allocation_fingerprint,
            "first_session": self.first_session.to_dict(),
            "skills": [item.to_dict() for item in self.skills],
            "prerequisite_edges": [list(edge) for edge in self.prerequisite_edges],
        }


CANONICAL_STATE_SCHEMA_VERSION = 1

ProgressionIntent = Literal["continue", "skip", "revisit", "practice"]


@dataclass(frozen=True)
class ProgressionTarget:
    bundle_id: str
    bundle_version: str
    unit_id: str
    unit_label: str
    section_id: str
    section_label: str
    skill_ref: Mapping[str, str]
    skill_label: str
    skill_description: str
    requirement: str
    depth_mode: str
    evidence_kind: str
    evidence_goal: str
    embedded_habit: str
    python_hooks: tuple[str, ...]

    @property
    def skill_id(self) -> str:
        return self.skill_ref["skill_id"]

    def to_dict(self) -> dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "unit_id": self.unit_id,
            "unit_label": self.unit_label,
            "section_id": self.section_id,
            "section_label": self.section_label,
            "skill_ref": dict(self.skill_ref),
            "skill_label": self.skill_label,
            "skill_description": self.skill_description,
            "requirement": self.requirement,
            "depth_mode": self.depth_mode,
            "evidence_kind": self.evidence_kind,
            "evidence_goal": self.evidence_goal,
            "embedded_habit": self.embedded_habit,
            "python_hooks": list(self.python_hooks),
        }


@dataclass(frozen=True)
class ProgressionResolution:
    state: dict[str, object]
    target: ProgressionTarget | None
    reason: str
    caught_up: bool = False
    deferred_skill_id: str | None = None


def _progression_route_items(
    state: Mapping[str, object],
) -> tuple[tuple[dict[str, object], str], ...]:
    route = state.get("route")
    skills = route.get("skills") if isinstance(route, Mapping) else None
    if not isinstance(skills, list) or not skills:
        raise CurriculumBundleError("canonical interview route has no progression skills")
    result: list[tuple[dict[str, object], str]] = []
    for raw in skills:
        if not isinstance(raw, Mapping):
            raise CurriculumBundleError("canonical interview route skill is malformed")
        ref = raw.get("skill_ref")
        skill_id = ref.get("skill_id") if isinstance(ref, Mapping) else None
        if not isinstance(skill_id, str):
            raise CurriculumBundleError("canonical interview route skill identity is malformed")
        result.append((dict(raw), skill_id))
    return tuple(result)


def _progression_target(
    item: Mapping[str, object],
    bundle: "InterviewCurriculumBundle",
    *,
    evidence_kind: str,
) -> ProgressionTarget:
    ref = item.get("skill_ref")
    if not isinstance(ref, Mapping):
        raise CurriculumBundleError("canonical interview target is malformed")
    normalized_ref = {str(key): str(value) for key, value in ref.items()}
    graph = bundle.graph_registry.graph(
        normalized_ref["graph_id"],
        normalized_ref["graph_version"],
        normalized_ref["mastery_policy_version"],
    )
    skill = graph.skill(normalized_ref["skill_id"])
    depth_mode = str(item.get("depth_mode") or "learn")
    evidence_goals = {
        "learn": "Build an accurate mental model, then make one small application attempt.",
        "practice": "Produce or apply the skill with only a minimal reminder.",
        "review": "Retrieve the skill, correct any gap, and avoid replaying a beginner lesson.",
        "verify": "Complete one unassisted production or transfer check without answer leakage.",
    }
    return ProgressionTarget(
        bundle_id=bundle.bundle_id,
        bundle_version=bundle.bundle_version,
        unit_id=str(item["unit_id"]),
        unit_label=str(item.get("unit_label") or item["unit_id"]),
        section_id=str(item["section_id"]),
        section_label=str(item.get("section_label") or item["section_id"]),
        skill_ref=MappingProxyType(normalized_ref),
        skill_label=skill.name,
        skill_description=skill.description,
        requirement=str(item.get("requirement") or "required"),
        depth_mode=depth_mode,
        evidence_kind=evidence_kind,
        evidence_goal=evidence_goals.get(depth_mode, evidence_goals["learn"]),
        embedded_habit=str(item.get("embedded_habit") or "Explain the key decision aloud."),
        python_hooks=tuple(
            str(value)
            for value in item.get("python_hooks", [])
            if isinstance(value, str) and value.strip()
        ),
    )


def _next_evidence_kind(
    state: Mapping[str, object],
    item: Mapping[str, object],
    bundle: "InterviewCurriculumBundle",
) -> str:
    """Choose the next pinned-policy check kind without trusting model labels."""
    ref = item.get("skill_ref")
    if not isinstance(ref, Mapping):
        raise CurriculumBundleError("canonical interview target is malformed")
    graph = bundle.graph_registry.graph(
        str(ref.get("graph_id") or ""),
        str(ref.get("graph_version") or ""),
        str(ref.get("mastery_policy_version") or ""),
    )
    skill = graph.skill(str(ref.get("skill_id") or ""))
    evidence = state.get("evidence")
    records = evidence.get("answer_evidence") if isinstance(evidence, Mapping) else None
    counts, required = _evidence_policy_progress(records, ref, skill)
    for kind in interview_skills.EVIDENCE_KINDS:
        if counts[kind] < required[kind]:
            return kind
    return "production"


def _evidence_policy_progress(
    records_value: object,
    skill_ref: Mapping[str, object],
    skill: interview_skills.InterviewSkill,
) -> tuple[dict[str, int], dict[str, int]]:
    """Count trusted evidence against one pinned skill policy."""
    records = records_value if isinstance(records_value, list) else []
    matching = [
        record
        for record in records
        if isinstance(record, Mapping)
        and record.get("status") == "correct"
        and isinstance(record.get("skill_ref"), Mapping)
        and all(
            record["skill_ref"].get(key) == skill_ref.get(key)
            for key in (
                "graph_id",
                "graph_version",
                "mastery_policy_version",
                "skill_id",
            )
        )
    ]
    counts = {
        kind: sum(kind in record.get("kinds", []) for record in matching)
        for kind in interview_skills.EVIDENCE_KINDS
    }
    required = dict(skill.evidence_policy.minimum)
    required["transfer"] = max(
        required["transfer"], skill.evidence_policy.transfer.minimum_novel_contexts
    )
    return counts, required


def target_identity(target: Mapping[str, object]) -> str:
    ref = target.get("skill_ref")
    if not isinstance(ref, Mapping):
        raise CurriculumBundleError("interview target has no stable skill identity")
    values = tuple(
        ref.get(key)
        for key in ("graph_id", "graph_version", "mastery_policy_version", "skill_id")
    )
    if not all(isinstance(value, str) and value for value in values):
        raise CurriculumBundleError("interview target stable skill identity is malformed")
    return "@".join(str(value) for value in values)


def target_response_error(answer: str, target: Mapping[str, object]) -> str | None:
    """Reject explicit target replacement, invented choices, or private reasoning."""
    skill_ref = target.get("skill_ref")
    skill_id = skill_ref.get("skill_id") if isinstance(skill_ref, Mapping) else None
    if not isinstance(skill_id, str):
        return "malformed reserved target"
    if re.search(
        r"(?is)<(?:think|analysis|reasoning)\b|(?:thinking process|analysis|reasoning)\s*:",
        answer,
    ):
        return "internal reasoning exposed"
    candidate_ids = set(
        re.findall(
            r"\b[a-z0-9]+(?:-[a-z0-9]+)*\.[a-z0-9]+(?:-[a-z0-9]+)*\b",
            answer,
        )
    )
    target_bundle_id = target.get("bundle_id")
    target_bundle_version = target.get("bundle_version")
    bundle = (
        load_pinned_bundle(target_bundle_id, target_bundle_version)
        if isinstance(target_bundle_id, str) and isinstance(target_bundle_version, str)
        else load_default_bundle()
    )
    known_skill_ids: set[str] = set()
    competing_skill_names: list[str] = []
    for graph_ref in bundle.graphs:
        graph = bundle.graph_registry.graph(
            graph_ref.graph_id,
            graph_ref.graph_version,
            graph_ref.mastery_policy_version,
        )
        for skill in graph.skills:
            known_skill_ids.add(skill.skill_id)
            if skill.skill_id == skill_id:
                continue
            competing_skill_names.append(skill.name)
    if (candidate_ids & known_skill_ids) - {skill_id}:
        return "response names a conflicting stable target"
    learner_sections = re.findall(
        r"(?is)(?:^|\n)\s*(?:\*\*)?"
        r"(Lesson|Check|Example|Feedback|Hint):(?:\*\*)?\s*"
        r"(.*?)(?=(?:\n\s*(?:\*\*)?(?:Lesson|Check|Example|Feedback|Hint):)|\Z)",
        answer,
    )
    target_label = str(target.get("skill_label") or "")
    for section_kind, section_text in learner_sections:
        for candidate_name in competing_skill_names:
            candidate_match = re.search(
                rf"(?i)\b{re.escape(candidate_name)}\b", section_text
            )
            if candidate_match is None:
                continue
            sentence_start = max(
                section_text.rfind(".", 0, candidate_match.start()),
                section_text.rfind("\n", 0, candidate_match.start()),
            )
            sentence_end = section_text.find(".", candidate_match.end())
            if sentence_end < 0:
                sentence_end = len(section_text)
            sentence = section_text[sentence_start + 1 : sentence_end]
            is_supporting_comparison = bool(
                re.search(
                    r"(?i)\b(?:unlike|compar(?:e|ed|ing)\s+(?:them\s+)?with|"
                    r"in\s+contrast\s+to|versus)\b",
                    sentence,
                )
                and target_label
                and re.search(rf"(?i)\b{re.escape(target_label)}\b", sentence)
            )
            if is_supporting_comparison:
                continue
            before_candidate = section_text[
                max(0, candidate_match.start() - 80) : candidate_match.start()
            ]
            directly_assigned = re.search(
                r"(?i)\b(?:explain|implement|practice|learn|teach|study|verify|trace|"
                r"apply|solve|derive|focus\s+on)\b[^.\n]{0,64}$",
                before_candidate,
            )
            if directly_assigned or section_kind.casefold() in {
                "lesson",
                "check",
                "example",
            }:
                return "response names a conflicting stable target"
    focus_markers = re.findall(r"(?is)<!--\s*focus\s*:\s*(.*?)\s*-->", answer)
    label = str(target.get("skill_label") or "")
    if any(marker.strip().casefold() != label.casefold() for marker in focus_markers):
        return "response declares a conflicting focus"
    if re.search(
        r"(?i)\b(?:you\s+(?:chose|selected|decided)|your\s+(?:choice|selection))\b",
        answer,
    ):
        return "response invents a learner choice"
    return None


def apply_answer_judgment(
    canonical_state: Mapping[str, object],
    event_data: Mapping[str, object],
    *,
    evidence_id: str,
    observed_at: str,
) -> dict[str, object]:
    """Project one trusted answer judgment into the pinned curriculum state."""
    state = copy.deepcopy(dict(canonical_state))
    bundle_id = state.get("bundle_id")
    bundle_version = state.get("bundle_version")
    if not isinstance(bundle_id, str) or not isinstance(bundle_version, str):
        raise CurriculumBundleError("canonical interview curriculum binding is malformed")
    bundle = load_pinned_bundle(bundle_id, bundle_version)
    raw_ref = event_data.get("skill_ref")
    if not isinstance(raw_ref, Mapping):
        raise CurriculumBundleError("answer judgment has no stable skill identity")
    identity_keys = (
        "graph_id",
        "graph_version",
        "mastery_policy_version",
        "skill_id",
    )
    if set(raw_ref) != set(identity_keys) or not all(
        isinstance(raw_ref.get(key), str) and raw_ref.get(key) for key in identity_keys
    ):
        raise CurriculumBundleError("answer judgment stable skill identity is malformed")
    skill_ref = {key: str(raw_ref[key]) for key in identity_keys}
    route_items = _progression_route_items(state)
    if not any(
        isinstance(item.get("skill_ref"), Mapping)
        and all(item["skill_ref"].get(key) == skill_ref[key] for key in identity_keys)
        for item, _skill_id in route_items
    ):
        raise CurriculumBundleError("answer judgment target is absent from the pinned route")
    cursor = state.get("cursor")
    cursor_ref = cursor.get("skill_ref") if isinstance(cursor, Mapping) else None
    check_target = state.get("committed_check_target")
    check_ref = (
        check_target.get("skill_ref") if isinstance(check_target, Mapping) else None
    )
    reserved_refs = [
        ref for ref in (check_ref, cursor_ref) if isinstance(ref, Mapping)
    ]
    if not any(
        all(ref.get(key) == skill_ref[key] for key in identity_keys)
        for ref in reserved_refs
    ):
        raise CurriculumBundleError("answer judgment does not match the reserved target")
    graph = bundle.graph_registry.graph(
        skill_ref["graph_id"],
        skill_ref["graph_version"],
        skill_ref["mastery_policy_version"],
    )
    skill = graph.skill(skill_ref["skill_id"])
    status = event_data.get("status")
    if status not in {"correct", "partial", "needs_work"}:
        raise CurriculumBundleError("answer judgment status is malformed")
    if not isinstance(evidence_id, str) or not evidence_id:
        raise CurriculumBundleError("answer judgment evidence id is malformed")
    if not isinstance(observed_at, str) or not observed_at:
        raise CurriculumBundleError("answer judgment timestamp is malformed")
    kinds: list[str] = []
    if status == "correct":
        evidence_kind = event_data.get("evidence_kind")
        if evidence_kind not in interview_skills.EVIDENCE_KINDS:
            route_item = next(
                item
                for item, _skill_id in route_items
                if isinstance(item.get("skill_ref"), Mapping)
                and all(item["skill_ref"].get(key) == skill_ref[key] for key in identity_keys)
            )
            evidence_kind = _next_evidence_kind(state, route_item, bundle)
        if evidence_kind not in skill.evidence_policy.explicit_check_kinds:
            raise CurriculumBundleError(
                "answer judgment evidence kind is not allowed by the pinned policy"
            )
        kinds = [str(evidence_kind)]
    evidence = state.get("evidence")
    if not isinstance(evidence, dict):
        raise CurriculumBundleError("canonical interview evidence is malformed")
    records = evidence.get("answer_evidence")
    records = (
        [copy.deepcopy(dict(item)) for item in records if isinstance(item, Mapping)]
        if isinstance(records, list)
        else []
    )
    if not any(item.get("evidence_id") == evidence_id for item in records):
        record: dict[str, object] = {
            "evidence_id": evidence_id,
            "observed_at": observed_at,
            "skill_ref": skill_ref,
            "status": status,
            "kinds": kinds,
        }
        score = event_data.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            record["score"] = float(score)
        records.append(record)
    evidence["answer_evidence"] = records

    skill_id_value = skill_ref["skill_id"]
    weak = {
        value for value in evidence.get("weak", []) if isinstance(value, str)
    }
    ready = {
        value for value in evidence.get("ready", []) if isinstance(value, str)
    }
    due = {
        value for value in evidence.get("due_review", []) if isinstance(value, str)
    }
    if status in {"partial", "needs_work"}:
        weak.add(skill_id_value)
        ready.discard(skill_id_value)
        if isinstance(cursor, dict) and isinstance(cursor_ref, Mapping) and all(
            cursor_ref.get(key) == skill_ref[key] for key in identity_keys
        ):
            cursor["instruction_status"] = "needs_work"
    else:
        weak.discard(skill_id_value)
        counts, required = _evidence_policy_progress(records, skill_ref, skill)
        is_ready = all(counts[kind] >= required[kind] for kind in interview_skills.EVIDENCE_KINDS)
        readiness = evidence.get("readiness")
        readiness = copy.deepcopy(dict(readiness)) if isinstance(readiness, Mapping) else {}
        readiness[target_identity({"skill_ref": skill_ref})] = {
            "skill_ref": skill_ref,
            "status": "ready" if is_ready else "provisional",
            "counts": counts,
            "missing": [
                f"{kind} {counts[kind]}/{required[kind]}"
                for kind in interview_skills.EVIDENCE_KINDS
                if counts[kind] < required[kind]
            ],
        }
        evidence["readiness"] = readiness
        if is_ready:
            ready.add(skill_id_value)
            due.discard(skill_id_value)
        else:
            ready.discard(skill_id_value)
    evidence["weak"] = sorted(weak)
    evidence["ready"] = sorted(ready)
    evidence["due_review"] = sorted(due)
    return state


def deterministic_target_fallback(target: Mapping[str, object]) -> str:
    """Return one learner-facing bundle-bound move when provider output is unsafe."""
    label = str(target.get("skill_label") or "this skill")
    description = str(target.get("skill_description") or "Build the core technical model.")
    depth = str(target.get("depth_mode") or "learn")
    evidence_kind = str(target.get("evidence_kind") or "production")
    habit = str(target.get("embedded_habit") or "Explain the key decision aloud.")
    hooks = target.get("python_hooks")
    python_text = ""
    if isinstance(hooks, list) and hooks:
        python_text = f" In Python, use {str(hooks[0]).rstrip('.')} where it supports the approach."
    if depth == "verify":
        check_move = {
            "recognition": "identify the correct representation and justify the choice",
            "explanation": "explain the invariant or core representation in your own words",
            "production": "produce or trace the approach on one concrete input",
            "transfer": "apply the skill to a new concrete input",
            "delayed_retrieval": "retrieve the approach and apply it without a refresher",
        }.get(evidence_kind, "produce or trace the approach on one concrete input")
        return (
            f"**Check:**\nVerify {label} without a refresher: {check_move}. {habit}"
        )
    if depth == "practice":
        check_move = {
            "recognition": "identify the right representation and justify it",
            "explanation": "explain the key invariant in your own words",
            "production": "trace or produce the key implementation step",
            "transfer": "apply the approach to a new small example",
            "delayed_retrieval": "retrieve and apply the approach without a refresher",
        }.get(evidence_kind, "trace or produce the key implementation step")
        return (
            f"**Check:**\nFor {label}, {check_move}. {habit}{python_text}"
        )
    if depth == "review":
        return (
            f"**Check:**\nRetrieve {label}: summarize the core idea, then name one edge case "
            f"that could break an implementation. {habit}"
        )
    return f"**Lesson:**\n{label}: {description} {habit}{python_text}".strip()


def resolve_progression_target(
    canonical_state: Mapping[str, object],
    *,
    intent: ProgressionIntent,
    explicit_skill_id: str | None = None,
    session_id: str = "",
) -> ProgressionResolution:
    """Resolve one deterministic instructional target without awarding mastery."""
    if intent not in {"continue", "skip", "revisit", "practice"}:
        raise ValueError("unsupported interview progression intent")
    state = copy.deepcopy(dict(canonical_state))
    bundle_id = state.get("bundle_id")
    bundle_version = state.get("bundle_version")
    if not isinstance(bundle_id, str) or not isinstance(bundle_version, str):
        raise CurriculumBundleError("canonical interview curriculum binding is malformed")
    bundle = load_pinned_bundle(bundle_id, bundle_version)
    route_items = _progression_route_items(state)
    by_id = {skill_id: item for item, skill_id in route_items}
    evidence = state.get("evidence")
    if not isinstance(evidence, dict):
        raise CurriculumBundleError("canonical interview evidence is malformed")

    def skill_set(key: str) -> set[str]:
        value = evidence.get(key)
        return {item for item in value if isinstance(item, str)} if isinstance(value, list) else set()

    ready = skill_set("ready")
    exposed = skill_set("exposed")
    weak = skill_set("weak")
    due = skill_set("due_review")
    commit_index = state.get("commit_index")
    commit_index = commit_index if isinstance(commit_index, int) and commit_index >= 0 else 0
    current_session = session_id or str(state.get("session_id") or "")
    deferred_raw = state.get("deferred")
    deferred = [dict(item) for item in deferred_raw if isinstance(item, Mapping)] if isinstance(deferred_raw, list) else []
    deferred_by_id = {
        str(item["skill_id"]): item
        for item in deferred
        if isinstance(item.get("skill_id"), str)
    }
    deferred_skill_id: str | None = None

    cursor = state.get("cursor")
    cursor_ref = cursor.get("skill_ref") if isinstance(cursor, Mapping) else None
    cursor_skill = cursor_ref.get("skill_id") if isinstance(cursor_ref, Mapping) else None
    if intent == "skip" and isinstance(cursor_skill, str) and cursor_skill in by_id:
        deferred_skill_id = cursor_skill
        deferred_by_id[cursor_skill] = {
            "skill_id": cursor_skill,
            "deferred_at_commit_index": commit_index,
            "deferred_session_id": current_session,
            "return_reason": "explicit_skip",
        }
        deferred = [
            deferred_by_id[skill_id]
            for _item, skill_id in route_items
            if skill_id in deferred_by_id
        ]
        state["deferred"] = deferred

    if intent == "revisit":
        if explicit_skill_id not in by_id:
            raise CurriculumBundleError("requested interview skill is absent from the route")
        selected = by_id[str(explicit_skill_id)]
        reason = "explicit_revisit"
    elif intent == "practice":
        candidates = [(item, skill_id) for item, skill_id in route_items if skill_id in exposed]
        due_candidates = [pair for pair in candidates if pair[1] in due]
        weak_candidates = [
            pair for pair in candidates if pair[1] in weak and pair[1] not in ready | due
        ]
        unready_candidates = [
            pair
            for pair in candidates
            if pair[1] not in ready | due | weak
        ]
        remaining = [
            pair for pair in candidates if pair[1] not in due | weak and pair[1] in ready
        ]
        pool = due_candidates or weak_candidates or unready_candidates or remaining
        selected = pool[commit_index % len(pool)][0] if pool else None
        if selected is None:
            return ProgressionResolution(state, None, "caught_up", caught_up=True)
        evidence_kind = _next_evidence_kind(state, selected, bundle)
        target = _progression_target(selected, bundle, evidence_kind=evidence_kind)
        state["committed_check_target"] = {
            "skill_ref": dict(target.skill_ref),
            "evidence_kind": evidence_kind,
            "practice": True,
        }
        return ProgressionResolution(
            state, target, "practice_now"
        )
    else:
        excluded = {deferred_skill_id} if deferred_skill_id else set()
        selected = next(
            (item for item, skill_id in route_items if skill_id in due and skill_id not in excluded),
            None,
        )
        reason = "due_review"
        if selected is None:
            selected = next(
                (
                    item
                    for item, skill_id in route_items
                    if skill_id in weak
                    and item.get("requirement") == "required"
                    and skill_id not in excluded
                ),
                None,
            )
            reason = "weakened_required"
        if selected is None:
            selected = next(
                (
                    item
                    for item, skill_id in route_items
                    if skill_id in deferred_by_id
                    and skill_id not in excluded
                    and (
                        commit_index
                        > int(deferred_by_id[skill_id].get("deferred_at_commit_index") or 0)
                        or current_session
                        != str(deferred_by_id[skill_id].get("deferred_session_id") or "")
                    )
                ),
                None,
            )
            reason = "deferred_return"
        if selected is None:
            selected = next(
                (
                    item
                    for item, skill_id in route_items
                    if item.get("requirement") == "required"
                    and skill_id not in ready | exposed | set(deferred_by_id) | excluded
                ),
                None,
            )
            reason = "uncovered_required"
        if selected is None:
            selected = next(
                (
                    item
                    for item, skill_id in route_items
                    if item.get("requirement") == "optional"
                    and skill_id not in ready | exposed | set(deferred_by_id) | excluded
                ),
                None,
            )
            reason = "uncovered_optional"
        if selected is None:
            return ProgressionResolution(
                state,
                None,
                "caught_up",
                caught_up=True,
                deferred_skill_id=deferred_skill_id,
            )

    evidence_kind = _next_evidence_kind(state, selected, bundle)
    target = _progression_target(selected, bundle, evidence_kind=evidence_kind)
    state["cursor"] = {
        "unit_id": target.unit_id,
        "section_id": target.section_id,
        "skill_ref": dict(target.skill_ref),
        "instruction_status": "reserved",
    }
    state["committed_check_target"] = {
        "skill_ref": dict(target.skill_ref),
        "evidence_kind": evidence_kind,
    }
    state["session_id"] = current_session
    return ProgressionResolution(
        state,
        target,
        reason,
        deferred_skill_id=deferred_skill_id,
    )


def record_progression_commit(
    canonical_state: Mapping[str, object], skill_id: str
) -> dict[str, object]:
    """Record target-specific exposure after its learner-visible turn commits."""
    state = copy.deepcopy(dict(canonical_state))
    evidence = state.get("evidence")
    if not isinstance(evidence, dict):
        raise CurriculumBundleError("canonical interview evidence is malformed")
    exposed = evidence.get("exposed")
    values = {item for item in exposed if isinstance(item, str)} if isinstance(exposed, list) else set()
    values.add(skill_id)
    evidence["exposed"] = sorted(values)
    deferred = state.get("deferred")
    if isinstance(deferred, list):
        state["deferred"] = [
            item
            for item in deferred
            if not isinstance(item, Mapping) or item.get("skill_id") != skill_id
        ]
    commit_index = state.get("commit_index")
    state["commit_index"] = (
        commit_index + 1 if isinstance(commit_index, int) and commit_index >= 0 else 1
    )
    route_item = next(
        (item for item, route_skill_id in _progression_route_items(state) if route_skill_id == skill_id),
        None,
    )
    if route_item is not None:
        ref = route_item.get("skill_ref")
        if isinstance(ref, Mapping):
            existing = state.get("committed_check_target")
            evidence_kind = (
                existing.get("evidence_kind")
                if isinstance(existing, Mapping)
                and isinstance(existing.get("skill_ref"), Mapping)
                and existing["skill_ref"].get("skill_id") == skill_id
                else None
            )
            state["committed_check_target"] = {
                "skill_ref": dict(ref),
                "evidence_kind": (
                    evidence_kind
                    if evidence_kind in interview_skills.EVIDENCE_KINDS
                    else "production"
                ),
                **(
                    {"practice": True}
                    if isinstance(existing, Mapping)
                    and existing.get("practice") is True
                    else {}
                ),
            }
    cursor = state.get("cursor")
    cursor_ref = cursor.get("skill_ref") if isinstance(cursor, dict) else None
    check_target = state.get("committed_check_target")
    is_practice = isinstance(check_target, Mapping) and check_target.get("practice") is True
    if (
        not is_practice
        and isinstance(cursor, dict)
        and isinstance(cursor_ref, Mapping)
        and cursor_ref.get("skill_id") == skill_id
    ):
        cursor["instruction_status"] = "covered"
    return state


def _canonical_fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def canonical_fingerprint(value: object) -> str:
    """Return the stable digest used by allocation and migration receipts."""
    return _canonical_fingerprint(value)


def _legacy_label_target(
    bundle: "InterviewCurriculumBundle", route: Mapping[str, object], label: str
) -> tuple[str, ...] | None:
    normalized = label.strip().casefold()
    skills = route.get("skills")
    if not isinstance(skills, list):
        return None
    direct: dict[str, tuple[str, ...]] = {}
    sections: dict[str, list[str]] = {}
    for item in skills:
        if not isinstance(item, Mapping):
            continue
        ref = item.get("skill_ref")
        skill_id = ref.get("skill_id") if isinstance(ref, Mapping) else None
        section_id = item.get("section_id")
        if not isinstance(skill_id, str) or not isinstance(section_id, str):
            continue
        direct[skill_id.casefold()] = (skill_id,)
        section_label = item.get("section_label")
        if isinstance(section_label, str):
            sections.setdefault(section_label.casefold(), []).append(skill_id)
        sections.setdefault(section_id.casefold(), []).append(skill_id)
    if normalized in direct:
        return direct[normalized]
    target_section = next(
        (
            section_id
            for alias, section_id in bundle.legacy_aliases.items()
            if alias.casefold() == normalized
        ),
        None,
    )
    if target_section is not None:
        return tuple(sections.get(target_section.casefold(), ())) or None
    return tuple(sections.get(normalized, ())) or None


def _legacy_labels(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    labels: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            labels.append(item)
        elif isinstance(item, Mapping):
            label = item.get("concept") or item.get("label") or item.get("skill_id")
            if isinstance(label, str) and label.strip():
                labels.append(label)
    return tuple(labels)


def build_canonical_curriculum_state(
    bundle: "InterviewCurriculumBundle",
    route: Mapping[str, object],
    *,
    metadata: Mapping[str, object],
    dynamic_state: Mapping[str, object],
    source_fingerprint: str,
    reconciliation_id: str,
) -> dict[str, object]:
    """Conservatively map legacy state onto one pinned materialized route.

    Free-text and historical labels remain context only. Only structured scored
    attempts are admitted as readiness evidence in this compatibility migration.
    """
    ready: set[str] = set()
    ready_at: dict[str, str] = {}
    exposed: set[str] = set()
    weak: set[str] = set()
    aliases_applied: dict[str, list[str]] = {}
    unassessed: list[str] = []

    for source in (
        metadata.get("known"),
        metadata.get("weak_spots"),
        metadata.get("review_due"),
    ):
        for label in _legacy_labels(source):
            targets = _legacy_label_target(bundle, route, label)
            if targets:
                aliases_applied[label] = list(targets)
            if label not in unassessed:
                unassessed.append(label)

    attempts = dynamic_state.get("concept_attempts")
    if isinstance(attempts, Mapping):
        for label, record in attempts.items():
            if not isinstance(label, str) or not isinstance(record, Mapping):
                continue
            targets = _legacy_label_target(bundle, route, label)
            if not targets:
                if label not in unassessed:
                    unassessed.append(label)
                continue
            aliases_applied[label] = list(targets)
            attempts_count = record.get("attempts")
            correct_sum = record.get("correct_sum")
            if (
                isinstance(attempts_count, (int, float))
                and not isinstance(attempts_count, bool)
                and attempts_count > 0
                and isinstance(correct_sum, (int, float))
                and not isinstance(correct_sum, bool)
            ):
                exposed.update(targets)
                if float(correct_sum) / float(attempts_count) >= 0.7:
                    ready.update(targets)
                    observed_at = record.get("last_correct_at") or record.get("updated_at")
                    if isinstance(observed_at, str):
                        for target in targets:
                            ready_at[target] = observed_at
            elif label not in unassessed:
                unassessed.append(label)

    events = dynamic_state.get("legacy_events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, Mapping):
                continue
            data = event.get("data")
            if not isinstance(data, Mapping):
                continue
            label = data.get("concept_id") or data.get("concept") or data.get("current_focus")
            if not isinstance(label, str):
                continue
            targets = _legacy_label_target(bundle, route, label)
            if not targets:
                continue
            event_type = event.get("event_type")
            timestamp = event.get("ts")
            passed = False
            failed = False
            if event_type == "review_graded":
                passed = data.get("difficulty") == "easy"
                failed = data.get("difficulty") == "missed"
            elif event_type == "answer_judged":
                score = data.get("score")
                passed = data.get("status") == "correct" or (
                    isinstance(score, (int, float))
                    and not isinstance(score, bool)
                    and float(score) >= 0.8
                )
                failed = data.get("status") == "needs_work"
            if passed:
                exposed.update(targets)
                ready.update(targets)
                weak.difference_update(targets)
                if isinstance(timestamp, str):
                    for target in targets:
                        ready_at[target] = timestamp
            elif failed:
                exposed.update(targets)
                weak.update(targets)
                ready.difference_update(targets)

    weak_value = metadata.get("weak_spots")
    weak_items = weak_value if isinstance(weak_value, list) else []
    for item in weak_items:
        label = (
            item
            if isinstance(item, str)
            else (item.get("concept") or item.get("label") if isinstance(item, Mapping) else None)
        )
        if not isinstance(label, str):
            continue
        targets = _legacy_label_target(bundle, route, label)
        if targets:
            weak_at = item.get("updated_at") if isinstance(item, Mapping) else None
            for target in targets:
                if (
                    target not in ready
                    or isinstance(weak_at, str)
                    and weak_at > ready_at.get(target, "")
                ):
                    weak.add(target)

    due_review: set[str] = set()
    raw_review_due = _legacy_labels(metadata.get("review_due"))
    for label in raw_review_due:
        targets = _legacy_label_target(bundle, route, label)
        if targets:
            due_review.update(targets)
            aliases_applied.setdefault(label, list(targets))
        else:
            unassessed.append(f"review_due:{label}")

    for key in ("slide_coverage", "placement_result"):
        value = metadata.get(key)
        if value not in (None, {}, []):
            unassessed.append(f"{key}:{_canonical_fingerprint(value)}")

    skills = route.get("skills")
    route_skills = (
        [item for item in skills if isinstance(item, Mapping)] if isinstance(skills, list) else []
    )
    current = next(
        (
            item
            for item in route_skills
            if isinstance(item.get("skill_ref"), Mapping)
            and item["skill_ref"].get("skill_id") not in ready
        ),
        route_skills[-1] if route_skills else None,
    )
    if current is None:
        raise CurriculumBundleError("materialized route has no skills")
    current_ref = current["skill_ref"]
    assert isinstance(current_ref, Mapping)
    cursor = {
        "unit_id": current["unit_id"],
        "section_id": current["section_id"],
        "skill_ref": dict(current_ref),
        "instruction_status": "review" if current_ref.get("skill_id") in weak else "uncovered",
    }
    return {
        "schema_version": CANONICAL_STATE_SCHEMA_VERSION,
        "bundle_id": route["bundle_id"],
        "bundle_version": route["bundle_version"],
        "route_id": route["route_id"],
        "route_fingerprint": route["route_fingerprint"],
        "allocation_fingerprint": route["allocation_fingerprint"],
        "route": dict(route),
        "cursor": cursor,
        "evidence": {
            "ready": sorted(ready),
            "exposed": sorted(exposed),
            "weak": sorted(weak),
            "due_review": sorted(due_review),
        },
        "legacy_context": {
            "aliases_applied": {key: aliases_applied[key] for key in sorted(aliases_applied)},
            "unassessed": sorted(set(unassessed)),
            "raw_review_due": list(raw_review_due),
        },
        "reconciliation": {
            "reconciliation_id": reconciliation_id,
            "source_fingerprint": source_fingerprint,
            "from_version": "legacy-unversioned",
            "to_version": route["bundle_version"],
        },
        "active_operation": None,
    }


def canonical_state_from_route(
    route: Mapping[str, object], *, acceptance_id: str
) -> dict[str, object]:
    """Create a fresh canonical state from an accepted materialized route."""
    skills = route.get("skills")
    if not isinstance(skills, list) or not skills or not isinstance(skills[0], Mapping):
        raise CurriculumBundleError("accepted interview route has no skills")
    first = skills[0]
    ref = first.get("skill_ref")
    if not isinstance(ref, Mapping):
        raise CurriculumBundleError("accepted interview route has no first skill identity")
    return {
        "schema_version": CANONICAL_STATE_SCHEMA_VERSION,
        "bundle_id": route["bundle_id"],
        "bundle_version": route["bundle_version"],
        "route_id": route["route_id"],
        "route_fingerprint": route["route_fingerprint"],
        "allocation_fingerprint": route["allocation_fingerprint"],
        "route": copy.deepcopy(dict(route)),
        "cursor": {
            "unit_id": first["unit_id"],
            "section_id": first["section_id"],
            "skill_ref": copy.deepcopy(dict(ref)),
            "instruction_status": "uncovered",
        },
        "evidence": {
            "ready": [],
            "exposed": [],
            "weak": [],
            "due_review": [],
        },
        "legacy_context": {"aliases_applied": {}, "unassessed": []},
        "reconciliation": {
            "reconciliation_id": acceptance_id,
            "source_fingerprint": canonical_fingerprint(route),
            "from_version": "new-course",
            "to_version": route["bundle_version"],
        },
        "route_history": [],
        "active_operation": None,
    }


def rematerialize_canonical_state(
    canonical_state: Mapping[str, object],
    route: Mapping[str, object],
    *,
    change_id: str,
) -> tuple[dict[str, object], str]:
    """Move a canonical state to another pinned route without discarding evidence."""
    state = copy.deepcopy(dict(canonical_state))
    if isinstance(state.get("active_operation"), Mapping):
        raise CurriculumBundleError(
            "finish or cancel the active tutor operation before changing the course outline"
        )
    if (
        state.get("bundle_id") != route.get("bundle_id")
        or state.get("bundle_version") != route.get("bundle_version")
    ):
        raise CurriculumBundleError("route changes must stay on the pinned curriculum version")
    skills_raw = route.get("skills")
    skills = (
        [dict(item) for item in skills_raw if isinstance(item, Mapping)]
        if isinstance(skills_raw, list)
        else []
    )
    if not skills:
        raise CurriculumBundleError("changed interview route has no skills")
    route_identities: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for item in skills:
        ref = item.get("skill_ref")
        if not isinstance(ref, Mapping):
            raise CurriculumBundleError("changed interview route has a malformed skill")
        identity = tuple(
            str(ref.get(key) or "")
            for key in ("graph_id", "graph_version", "mastery_policy_version", "skill_id")
        )
        if not all(identity):
            raise CurriculumBundleError("changed interview route has a malformed skill identity")
        route_identities[identity] = item

    old_cursor = state.get("cursor")
    old_ref = old_cursor.get("skill_ref") if isinstance(old_cursor, Mapping) else None
    old_identity = (
        tuple(
            str(old_ref.get(key) or "")
            for key in ("graph_id", "graph_version", "mastery_policy_version", "skill_id")
        )
        if isinstance(old_ref, Mapping)
        else ()
    )
    evidence = state.get("evidence")
    evidence = copy.deepcopy(dict(evidence)) if isinstance(evidence, Mapping) else {}
    ready = {
        value for value in evidence.get("ready", []) if isinstance(value, str)
    }
    current = route_identities.get(old_identity) if old_identity else None
    cursor_decision = "retained-eligible-cursor"
    if current is None:
        current = next(
            (
                item
                for item in skills
                if isinstance(item.get("skill_ref"), Mapping)
                and item["skill_ref"].get("skill_id") not in ready
                and item.get("requirement") == "required"
            ),
            skills[0],
        )
        cursor_decision = "earliest-eligible-unmet-prerequisite"
    current_ref = current.get("skill_ref")
    assert isinstance(current_ref, Mapping)

    old_route = state.get("route")
    history = state.get("route_history")
    history_values = (
        [copy.deepcopy(dict(item)) for item in history if isinstance(item, Mapping)]
        if isinstance(history, list)
        else []
    )
    old_route_skills = old_route.get("skills", []) if isinstance(old_route, Mapping) else []
    old_skill_ids = {
        str(item["skill_ref"]["skill_id"])
        for item in old_route_skills
        if isinstance(item, Mapping)
        and isinstance(item.get("skill_ref"), Mapping)
    }
    new_skill_ids = {
        str(item["skill_ref"]["skill_id"])
        for item in skills
        if isinstance(item.get("skill_ref"), Mapping)
    }
    history_values.append(
        {
            "change_id": change_id,
            "route_id": state.get("route_id"),
            "route_fingerprint": state.get("route_fingerprint"),
            "cursor": copy.deepcopy(old_cursor),
            "out_of_route_skill_ids": sorted(old_skill_ids - new_skill_ids),
        }
    )
    state.update(
        {
            "route_id": route["route_id"],
            "route_fingerprint": route["route_fingerprint"],
            "allocation_fingerprint": route["allocation_fingerprint"],
            "route": copy.deepcopy(dict(route)),
            "cursor": {
                "unit_id": current["unit_id"],
                "section_id": current["section_id"],
                "skill_ref": copy.deepcopy(dict(current_ref)),
                "instruction_status": (
                    str(old_cursor.get("instruction_status") or "uncovered")
                    if current is route_identities.get(old_identity)
                    and isinstance(old_cursor, Mapping)
                    else "uncovered"
                ),
            },
            "route_history": history_values,
            "evidence": evidence,
        }
    )
    return state, cursor_decision


def compatibility_projection(state: Mapping[str, object]) -> dict[str, object]:
    route = state.get("route")
    cursor = state.get("cursor")
    if not isinstance(route, Mapping) or not isinstance(cursor, Mapping):
        raise CurriculumBundleError("canonical interview curriculum state is malformed")
    skills = route.get("skills")
    if not isinstance(skills, list):
        raise CurriculumBundleError("canonical interview route is malformed")
    units: list[dict[str, object]] = []
    unit_indexes: dict[str, int] = {}
    current_unit_id = cursor.get("unit_id")
    current_section_id = cursor.get("section_id")
    current_skill_ref = cursor.get("skill_ref")
    current_unit = 1
    current_slide = 1
    current_focus = ""
    for item in skills:
        if not isinstance(item, Mapping):
            continue
        unit_id = item.get("unit_id")
        if not isinstance(unit_id, str):
            continue
        if unit_id not in unit_indexes:
            unit_indexes[unit_id] = len(units)
            units.append(
                {
                    "unit": len(units) + 1,
                    "title": str(item.get("unit_label") or unit_id),
                    "slide_count": 0,
                    "concepts": [],
                }
            )
        unit = units[unit_indexes[unit_id]]
        unit["slide_count"] = int(unit["slide_count"]) + 1
        concepts = unit["concepts"]
        assert isinstance(concepts, list)
        ref = item.get("skill_ref")
        skill_id = ref.get("skill_id") if isinstance(ref, Mapping) else ""
        concepts.append({"id": skill_id, "label": str(item.get("section_label") or skill_id)})
        if (
            unit_id == current_unit_id
            and item.get("section_id") == current_section_id
            and isinstance(current_skill_ref, Mapping)
            and isinstance(ref, Mapping)
            and all(
                ref.get(key) == current_skill_ref.get(key)
                for key in (
                    "graph_id",
                    "graph_version",
                    "mastery_policy_version",
                    "skill_id",
                )
            )
        ):
            current_unit = int(unit["unit"])
            current_slide = int(unit["slide_count"])
            current_focus = str(item.get("section_label") or skill_id)
    return {
        "course_units": units,
        "current_unit": current_unit,
        "current_slide": current_slide,
        "current_focus": current_focus,
    }


@dataclass(frozen=True)
class InterviewCurriculumBundle:
    schema_version: int
    bundle_id: str
    bundle_version: str
    graphs: tuple[CurriculumGraphReference, ...]
    units: tuple[CurriculumUnit, ...]
    routes: tuple[CurriculumRoute, ...]
    role_extensions: tuple[CurriculumRoleExtension, ...]
    confidence_mapping: Mapping[int, str]
    confidence_skill_mapping: Mapping[str, tuple[str, ...]]
    legacy_aliases: Mapping[str, str]
    graph_registry: interview_skills.SkillGraphBundleRegistry

    def route(self, route_id: str) -> CurriculumRoute:
        for route in self.routes:
            if route.route_id == route_id:
                return route
        raise CurriculumBundleError(f"unknown interview curriculum route: {route_id}")

    def section(self, section_id: str) -> CurriculumSection:
        for unit in self.units:
            for section in unit.sections:
                if section.section_id == section_id:
                    return section
        raise CurriculumBundleError(f"unknown interview curriculum section: {section_id}")

    def display_units(self, route_id: str = "balanced") -> tuple[str, ...]:
        route = self.route(route_id)
        locations = {
            ref.identity: (unit.label, section.label)
            for unit in self.units
            for section in unit.sections
            for ref in section.skill_refs
        }
        labels: list[str] = []
        seen_sections: set[tuple[str, str]] = set()
        for ref in route.skill_refs:
            location = locations[ref.identity]
            if location in seen_sections:
                continue
            seen_sections.add(location)
            labels.append(f"{location[0]}: {location[1]}")
        return tuple(labels)


def bundle_resource() -> Traversable:
    return importlib.resources.files("openlearn").joinpath(
        "interview_curricula", "technical-interview-v1.json"
    )


@lru_cache(maxsize=None)
def load_pinned_bundle(bundle_id: str, bundle_version: str) -> InterviewCurriculumBundle:
    """Load the immutable package asset matching an existing course binding."""
    resources = importlib.resources.files("openlearn").joinpath("interview_curricula")
    for resource in resources.iterdir():
        if not resource.name.endswith(".json"):
            continue
        try:
            raw = json.loads(resource.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        if raw.get("bundle_id") == bundle_id and raw.get("bundle_version") == bundle_version:
            return validate_bundle(raw)
    raise CurriculumBundleError(
        f"pinned interview curriculum is unavailable: {bundle_id}@{bundle_version}"
    )


def load_bundle_dict() -> dict[str, object]:
    try:
        value = json.loads(bundle_resource().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CurriculumBundleError("bundled interview curriculum is unreadable") from exc
    if not isinstance(value, dict):
        raise CurriculumBundleError("interview curriculum must be a JSON object")
    return value


@lru_cache(maxsize=1)
def load_default_bundle() -> InterviewCurriculumBundle:
    return validate_bundle(load_bundle_dict())


def _ref_dict(ref: CurriculumSkillReference) -> dict[str, str]:
    return {
        "graph_id": ref.graph_id,
        "graph_version": ref.graph_version,
        "mastery_policy_version": ref.mastery_policy_version,
        "skill_id": ref.skill_id,
    }


def _fingerprint(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CurriculumBundleError(f"{label} must be trimmed non-empty text")
    return value


def _stable_id(value: object, label: str) -> str:
    result = _text(value, label)
    if not STABLE_ID_PATTERN.fullmatch(result):
        raise CurriculumBundleError(f"{label} must be a stable lowercase ID")
    return result


def _text_list(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise CurriculumBundleError(f"{label} must be a string list")
    result = tuple(_text(item, label) for item in value)
    if len(result) != len(set(result)):
        raise CurriculumBundleError(f"{label} must not contain duplicates")
    return result


def _graph_resource(filename: str) -> Traversable:
    if not RESOURCE_PATTERN.fullmatch(filename):
        raise CurriculumBundleError("graph resource must be a bundled JSON filename")
    return importlib.resources.files("openlearn").joinpath("interview_skill_graphs", filename)


def _load_graph(reference: CurriculumGraphReference) -> interview_skills.InterviewSkillGraph:
    try:
        raw = json.loads(_graph_resource(reference.resource).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CurriculumBundleError(
            f"interview skill graph resource is unreadable: {reference.resource}"
        ) from exc
    if not isinstance(raw, dict):
        raise CurriculumBundleError(
            f"interview skill graph resource must be an object: {reference.resource}"
        )
    try:
        graph = interview_skills.validate_graph(raw)
    except interview_skills.SkillGraphError as exc:
        raise CurriculumBundleError(
            f"interview skill graph is invalid: {reference.resource}"
        ) from exc
    identity = (graph.graph_id, graph.graph_version, graph.mastery_policy_version)
    expected = (
        reference.graph_id,
        reference.graph_version,
        reference.mastery_policy_version,
    )
    if identity != expected:
        raise CurriculumBundleError(
            f"interview skill graph identity does not match: {reference.resource}"
        )
    return graph


def _parse_graphs(
    value: object,
) -> tuple[
    tuple[CurriculumGraphReference, ...],
    Mapping[str, CurriculumGraphReference],
    interview_skills.SkillGraphBundleRegistry,
]:
    if not isinstance(value, list) or not value:
        raise CurriculumBundleError("graphs must be a non-empty list")
    references: list[CurriculumGraphReference] = []
    aliases: dict[str, CurriculumGraphReference] = {}
    identities: set[tuple[str, str, str]] = set()
    resources: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "alias",
            "graph_id",
            "graph_version",
            "mastery_policy_version",
            "resource",
        }:
            raise CurriculumBundleError("graph reference fields are invalid")
        reference = CurriculumGraphReference(
            alias=_stable_id(raw.get("alias"), "graph alias"),
            graph_id=_stable_id(raw.get("graph_id"), "graph_id"),
            graph_version=_text(raw.get("graph_version"), "graph_version"),
            mastery_policy_version=_stable_id(
                raw.get("mastery_policy_version"), "mastery_policy_version"
            ),
            resource=_text(raw.get("resource"), "graph resource"),
        )
        identity = (
            reference.graph_id,
            reference.graph_version,
            reference.mastery_policy_version,
        )
        if reference.alias in aliases or identity in identities or reference.resource in resources:
            raise CurriculumBundleError("duplicate graph reference")
        aliases[reference.alias] = reference
        identities.add(identity)
        resources.add(reference.resource)
        references.append(reference)
    graphs = tuple(_load_graph(reference) for reference in references)
    return (
        tuple(references),
        MappingProxyType(aliases),
        interview_skills.SkillGraphBundleRegistry.from_graphs(graphs),
    )


def _skill_ref(
    value: object,
    aliases: Mapping[str, CurriculumGraphReference],
    registry: interview_skills.SkillGraphBundleRegistry,
    label: str,
) -> CurriculumSkillReference:
    if not isinstance(value, dict) or set(value) != {"graph", "skill_id"}:
        raise CurriculumBundleError(f"{label} fields are invalid")
    alias = _stable_id(value.get("graph"), f"{label} graph")
    try:
        graph_ref = aliases[alias]
    except KeyError as exc:
        raise CurriculumBundleError(f"{label} references an unknown graph") from exc
    skill_id = _stable_id(value.get("skill_id"), f"{label} skill_id")
    try:
        graph = registry.graph(
            graph_ref.graph_id,
            graph_ref.graph_version,
            graph_ref.mastery_policy_version,
        )
        graph.skill(skill_id)
    except (interview_skills.EvidenceRecordError, interview_skills.SkillGraphError) as exc:
        raise CurriculumBundleError(f"{label} references unknown skill {skill_id}") from exc
    return CurriculumSkillReference(
        graph_id=graph_ref.graph_id,
        graph_version=graph_ref.graph_version,
        mastery_policy_version=graph_ref.mastery_policy_version,
        skill_id=skill_id,
    )


def _skill_refs(
    value: object,
    aliases: Mapping[str, CurriculumGraphReference],
    registry: interview_skills.SkillGraphBundleRegistry,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[CurriculumSkillReference, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise CurriculumBundleError(f"{label} must be a list")
    refs = tuple(_skill_ref(item, aliases, registry, label) for item in value)
    identities = tuple(ref.identity for ref in refs)
    if len(identities) != len(set(identities)):
        raise CurriculumBundleError(f"{label} must not contain duplicates")
    return refs


def _parse_applicability(value: object, section_id: str) -> CurriculumApplicability:
    if not isinstance(value, dict) or set(value) != {
        "required_for",
        "optional_for",
        "minimum_required_level",
    }:
        raise CurriculumBundleError(f"{section_id} applicability fields are invalid")
    required = _text_list(
        value.get("required_for"),
        f"{section_id} required applicability",
        allow_empty=True,
    )
    optional = _text_list(
        value.get("optional_for"),
        f"{section_id} optional applicability",
        allow_empty=True,
    )
    if not set(required).union(optional) <= set(FOCUSES) or set(required) & set(optional):
        raise CurriculumBundleError(f"{section_id} applicability is invalid")
    level = value.get("minimum_required_level")
    if level is not None and level not in LEVELS:
        raise CurriculumBundleError(f"{section_id} applicability level is invalid")
    return CurriculumApplicability(required, optional, level)


def _validate_prerequisites(
    route: CurriculumRoute,
    registry: interview_skills.SkillGraphBundleRegistry,
) -> None:
    positions = {ref.identity: index for index, ref in enumerate(route.skill_refs)}
    by_skill = {ref.skill_id: ref for ref in route.skill_refs}
    for ref in route.skill_refs:
        graph = registry.graph(ref.graph_id, ref.graph_version, ref.mastery_policy_version)
        skill = graph.skill(ref.skill_id)
        for prerequisite in skill.prerequisites:
            if prerequisite.kind != "blocking" or prerequisite.skill_id not in by_skill:
                continue
            prerequisite_ref = by_skill[prerequisite.skill_id]
            if positions[prerequisite_ref.identity] >= positions[ref.identity]:
                raise CurriculumBundleError(
                    f"{route.route_id} route breaks blocking prerequisite order for {ref.skill_id}"
                )


def validate_bundle(raw: Mapping[str, object]) -> InterviewCurriculumBundle:
    expected_fields = {
        "schema_version",
        "bundle_id",
        "bundle_version",
        "graphs",
        "confidence_mapping",
        "legacy_aliases",
        "units",
        "routes",
        "role_extensions",
    }
    if set(raw) != expected_fields:
        raise CurriculumBundleError("interview curriculum bundle fields are invalid")
    if raw.get("schema_version") != 1:
        raise CurriculumBundleError("unsupported interview curriculum schema")
    graphs, aliases, registry = _parse_graphs(raw.get("graphs"))

    units_value = raw.get("units")
    if not isinstance(units_value, list) or not units_value:
        raise CurriculumBundleError("units must be a non-empty list")
    units: list[CurriculumUnit] = []
    unit_ids: set[str] = set()
    sections: dict[str, CurriculumSection] = {}
    skill_locations: dict[tuple[str, str, str, str], str] = {}
    for unit_value in units_value:
        if not isinstance(unit_value, dict) or set(unit_value) != {
            "id",
            "label",
            "sections",
        }:
            raise CurriculumBundleError("unit fields are invalid")
        unit_id = _stable_id(unit_value.get("id"), "unit id")
        if unit_id in unit_ids:
            raise CurriculumBundleError(f"duplicate unit id: {unit_id}")
        unit_ids.add(unit_id)
        section_values = unit_value.get("sections")
        if not isinstance(section_values, list) or not section_values:
            raise CurriculumBundleError(f"{unit_id} sections must be a non-empty list")
        parsed_sections: list[CurriculumSection] = []
        for section_value in section_values:
            if not isinstance(section_value, dict) or set(section_value) != {
                "id",
                "label",
                "skill_refs",
                "applicability",
                "embedded_habit",
                "python_hooks",
                "embedded_skill_refs",
            }:
                raise CurriculumBundleError("section fields are invalid")
            section_id = _stable_id(section_value.get("id"), "section id")
            if section_id in sections:
                raise CurriculumBundleError(f"duplicate section id: {section_id}")
            refs = _skill_refs(
                section_value.get("skill_refs"),
                aliases,
                registry,
                f"{section_id} skill references",
            )
            for ref in refs:
                if ref.identity in skill_locations:
                    raise CurriculumBundleError(f"duplicate progression skill: {ref.skill_id}")
                skill_locations[ref.identity] = section_id
            section = CurriculumSection(
                section_id=section_id,
                label=_text(section_value.get("label"), f"{section_id} label"),
                skill_refs=refs,
                applicability=_parse_applicability(section_value.get("applicability"), section_id),
                embedded_habit=_text(
                    section_value.get("embedded_habit"),
                    f"{section_id} embedded_habit",
                ),
                python_hooks=_text_list(
                    section_value.get("python_hooks"),
                    f"{section_id} python_hooks",
                    allow_empty=True,
                ),
                embedded_skill_refs=_skill_refs(
                    section_value.get("embedded_skill_refs"),
                    aliases,
                    registry,
                    f"{section_id} embedded skill references",
                    allow_empty=True,
                ),
            )
            sections[section_id] = section
            parsed_sections.append(section)
        units.append(
            CurriculumUnit(
                unit_id=unit_id,
                label=_text(unit_value.get("label"), f"{unit_id} label"),
                sections=tuple(parsed_sections),
            )
        )

    routes_value = raw.get("routes")
    if not isinstance(routes_value, list) or not routes_value:
        raise CurriculumBundleError("routes must be a non-empty list")
    routes: list[CurriculumRoute] = []
    route_ids: set[str] = set()
    for route_value in routes_value:
        if not isinstance(route_value, dict) or set(route_value) != {
            "id",
            "label",
            "skill_refs",
            "first_session_skill_id",
        }:
            raise CurriculumBundleError("route fields are invalid")
        route_id = _stable_id(route_value.get("id"), "route id")
        if route_id not in FOCUSES or route_id in route_ids:
            raise CurriculumBundleError(f"invalid or duplicate route id: {route_id}")
        route_ids.add(route_id)
        refs = _skill_refs(
            route_value.get("skill_refs"),
            aliases,
            registry,
            f"{route_id} route skill references",
        )
        for ref in refs:
            if ref.identity not in skill_locations:
                raise CurriculumBundleError(
                    f"{route_id} route skill is absent from curriculum sections: {ref.skill_id}"
                )
        first = _stable_id(
            route_value.get("first_session_skill_id"),
            f"{route_id} first_session_skill_id",
        )
        if refs[0].skill_id != first:
            raise CurriculumBundleError(
                f"{route_id} first-session target must be the first route skill"
            )
        route = CurriculumRoute(
            route_id=route_id,
            label=_text(route_value.get("label"), f"{route_id} label"),
            skill_refs=refs,
            first_session_skill_id=first,
        )
        _validate_prerequisites(route, registry)
        routes.append(route)
    if route_ids != set(FOCUSES):
        raise CurriculumBundleError("coding, balanced, and system-design routes are required")

    extensions_value = raw.get("role_extensions")
    if not isinstance(extensions_value, list):
        raise CurriculumBundleError("role_extensions must be a list")
    extensions: list[CurriculumRoleExtension] = []
    role_families: set[str] = set()
    for value in extensions_value:
        if not isinstance(value, dict) or set(value) != {
            "role_family",
            "section_id",
            "skill_refs",
        }:
            raise CurriculumBundleError("role extension fields are invalid")
        role_family = _stable_id(value.get("role_family"), "role family")
        section_id = _stable_id(value.get("section_id"), "role section id")
        if role_family in role_families or section_id not in sections:
            raise CurriculumBundleError("role extension identity is invalid")
        role_families.add(role_family)
        refs = _skill_refs(
            value.get("skill_refs"),
            aliases,
            registry,
            f"{role_family} role extension references",
        )
        if tuple(ref.identity for ref in refs) != tuple(
            ref.identity for ref in sections[section_id].skill_refs
        ):
            raise CurriculumBundleError(
                f"{role_family} role extension must match its curriculum section"
            )
        extensions.append(CurriculumRoleExtension(role_family, section_id, refs))

    confidence_value = raw.get("confidence_mapping")
    if not isinstance(confidence_value, dict) or set(confidence_value) != {
        "1",
        "2",
        "3",
        "4",
        "5",
    }:
        raise CurriculumBundleError("confidence mapping must define ratings 1 through 5")
    confidence_mapping = {
        int(key): _text(value, f"confidence {key}") for key, value in confidence_value.items()
    }
    if not set(confidence_mapping.values()) <= set(DEPTH_MODES):
        raise CurriculumBundleError("confidence mapping contains an invalid depth mode")

    aliases_value = raw.get("legacy_aliases")
    if not isinstance(aliases_value, dict):
        raise CurriculumBundleError("legacy_aliases must be an object")
    legacy_aliases: dict[str, str] = {}
    for legacy, section_id in aliases_value.items():
        legacy_text = _text(legacy, "legacy alias")
        section_text = _stable_id(section_id, f"legacy alias {legacy_text}")
        if section_text not in sections:
            raise CurriculumBundleError(f"legacy alias references unknown section: {section_text}")
        legacy_aliases[legacy_text] = section_text

    return InterviewCurriculumBundle(
        schema_version=1,
        bundle_id=_stable_id(raw.get("bundle_id"), "bundle_id"),
        bundle_version=_text(raw.get("bundle_version"), "bundle_version"),
        graphs=graphs,
        units=tuple(units),
        routes=tuple(routes),
        role_extensions=tuple(extensions),
        confidence_mapping=MappingProxyType(confidence_mapping),
        confidence_skill_mapping=CONFIDENCE_AREA_SKILLS,
        legacy_aliases=MappingProxyType(legacy_aliases),
        graph_registry=registry,
    )


def _date_horizon(interview_date: str, current_date: date) -> str:
    if not interview_date:
        return "open-ended"
    try:
        remaining = (date.fromisoformat(interview_date) - current_date).days
    except ValueError as exc:
        raise ValueError("interview date must use YYYY-MM-DD") from exc
    if remaining < 0:
        return "long-term"
    if remaining <= 28:
        return "accelerated"
    if remaining <= 84:
        return "near-term"
    return "open-ended"


def _normalized_focus(value: str) -> str:
    normalized = value.strip().casefold().replace("_", "-")
    if normalized not in FOCUSES:
        raise ValueError("interview focus is invalid")
    return normalized


def _normalized_level(value: str) -> str:
    normalized = value.strip().casefold()
    aliases = {"senior+": "senior", "unspecified": "entry", "": "entry"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in LEVELS:
        raise ValueError("target level is invalid")
    return normalized


def _normalized_role(value: str) -> str:
    normalized = value.strip().casefold()
    aliases = {
        "data / ml": "data-ml",
        "data/ml": "data-ml",
        "data ml": "data-ml",
        "general swe": "general-swe",
        "": "general-swe",
    }
    return aliases.get(normalized, normalized.replace("_", "-"))


def _section_locations(
    bundle: InterviewCurriculumBundle,
) -> dict[tuple[str, str, str, str], tuple[CurriculumUnit, CurriculumSection]]:
    return {
        ref.identity: (unit, section)
        for unit in bundle.units
        for section in unit.sections
        for ref in section.skill_refs
    }


def _required_at_level(applicability: CurriculumApplicability, level: str) -> bool:
    minimum = applicability.minimum_required_level
    if minimum is None:
        return True
    return LEVELS.index(level) >= LEVELS.index(minimum)


def _route_requirement(
    section: CurriculumSection,
    ref: CurriculumSkillReference,
    *,
    focus: str,
    level: str,
) -> str:
    applicability = section.applicability
    if focus in applicability.required_for and _required_at_level(applicability, level):
        return "required"
    if focus == "system-design" and ref.skill_id in {
        "concept.constraint-reading",
        "concept.complexity-analysis",
        "concept.arrays-strings",
        "concept.hashing",
        "pattern.sliding-window",
    }:
        return "required"
    return "optional"


def _confidence_depths(
    bundle: InterviewCurriculumBundle,
    ratings: Mapping[str, int],
) -> dict[str, str]:
    unknown = set(ratings) - set(bundle.confidence_skill_mapping)
    if unknown:
        raise ValueError(f"confidence area is invalid: {sorted(unknown)[0]}")
    depths: dict[str, str] = {}
    for area, rating in ratings.items():
        if isinstance(rating, bool) or not isinstance(rating, int) or rating not in range(1, 6):
            raise ValueError(f"confidence rating is invalid: {area}")
        for skill_id in bundle.confidence_skill_mapping[area]:
            depths[skill_id] = bundle.confidence_mapping[rating]
    return depths


def _prerequisite_edges(
    bundle: InterviewCurriculumBundle,
    refs: tuple[CurriculumSkillReference, ...],
) -> tuple[tuple[str, str], ...]:
    included = {ref.skill_id for ref in refs}
    edges: list[tuple[str, str]] = []
    for ref in refs:
        graph = bundle.graph_registry.graph(
            ref.graph_id, ref.graph_version, ref.mastery_policy_version
        )
        skill = graph.skill(ref.skill_id)
        edges.extend(
            (prerequisite.skill_id, ref.skill_id)
            for prerequisite in skill.prerequisites
            if prerequisite.kind == "blocking" and prerequisite.skill_id in included
        )
    return tuple(edges)


def _allocated_minutes(
    requirements: tuple[str, ...],
    depths: tuple[str, ...],
    *,
    weekly_minutes: int,
    active_count: int,
) -> tuple[int, ...]:
    active_count = min(len(requirements), max(1, active_count))
    weights = {
        "learn": 4,
        "practice": 3,
        "review": 2,
        "verify": 1,
    }
    active_weights = [
        weights[depths[index]] + (2 if requirements[index] == "required" else 0)
        for index in range(active_count)
    ]
    total_weight = sum(active_weights)
    base = [weekly_minutes * weight // total_weight for weight in active_weights]
    remainder = weekly_minutes - sum(base)
    fractions = sorted(
        range(active_count),
        key=lambda index: (
            -(weekly_minutes * active_weights[index] % total_weight),
            index,
        ),
    )
    for index in fractions[:remainder]:
        base[index] += 1
    return tuple((*base, *(0 for _ in range(len(requirements) - active_count))))


def materialize_adaptive_route(
    bundle: InterviewCurriculumBundle,
    *,
    role_family: str,
    target_level: str,
    interview_focus: str,
    interview_date: str,
    weekly_minutes: int,
    session_minutes: int,
    confidence_ratings: Mapping[str, int],
    pacing_posture_override: str | None,
    current_date: date,
) -> MaterializedInterviewRoute:
    """Project profile context into a deterministic, evidence-free route allocation."""
    focus = _normalized_focus(interview_focus)
    level = _normalized_level(target_level)
    role = _normalized_role(role_family)
    if (
        isinstance(weekly_minutes, bool)
        or isinstance(session_minutes, bool)
        or not isinstance(weekly_minutes, int)
        or not isinstance(session_minutes, int)
        or weekly_minutes < 1
        or session_minutes < 1
        or session_minutes > weekly_minutes
    ):
        raise ValueError("weekly and session minutes are invalid")
    if pacing_posture_override not in {None, "standard"}:
        raise ValueError("pacing posture override is invalid")

    horizon = _date_horizon(interview_date, current_date)
    recommended = "accelerated" if horizon == "accelerated" else "standard"
    pacing = pacing_posture_override or recommended
    route = bundle.route(focus)
    refs = list(route.skill_refs)
    extension = next(
        (item for item in bundle.role_extensions if item.role_family == role),
        None,
    )
    if extension is not None:
        refs.extend(extension.skill_refs)
    ref_tuple = tuple(refs)
    locations = _section_locations(bundle)
    confidence_depths = _confidence_depths(bundle, confidence_ratings)
    requirements = tuple(
        (
            "required"
            if extension is not None
            and ref in extension.skill_refs
            and focus in {"balanced", "system-design"}
            else "optional"
            if extension is not None and ref in extension.skill_refs
            else _route_requirement(locations[ref.identity][1], ref, focus=focus, level=level)
        )
        for ref in ref_tuple
    )
    depths = tuple(confidence_depths.get(ref.skill_id, "learn") for ref in ref_tuple)
    session_count = max(1, (weekly_minutes + session_minutes - 1) // session_minutes)
    active_count = (
        session_count
        if pacing == "accelerated"
        else min(len(ref_tuple), session_count * 2)
        if horizon == "near-term"
        else len(ref_tuple)
    )
    minutes = _allocated_minutes(
        requirements,
        depths,
        weekly_minutes=weekly_minutes,
        active_count=active_count,
    )
    edges = _prerequisite_edges(bundle, ref_tuple)
    route_payload: dict[str, object] = {
        "bundle_id": bundle.bundle_id,
        "bundle_version": bundle.bundle_version,
        "route_id": focus,
        "role_family": role,
        "target_level": level,
        "skill_refs": [_ref_dict(ref) for ref in ref_tuple],
        "requirements": list(requirements),
        "prerequisite_edges": [list(edge) for edge in edges],
    }
    route_fingerprint = _fingerprint(route_payload)
    allocation_payload: dict[str, object] = {
        "route_fingerprint": route_fingerprint,
        "current_date": current_date.isoformat(),
        "interview_date": interview_date,
        "date_horizon": horizon,
        "recommended_pacing_posture": recommended,
        "pacing_posture": pacing,
        "weekly_minutes": weekly_minutes,
        "session_minutes": session_minutes,
        "depths": list(depths),
        "minutes": list(minutes),
    }
    allocation_fingerprint = _fingerprint(allocation_payload)
    skills = tuple(
        MaterializedInterviewSkill(
            skill_ref=ref,
            unit_id=locations[ref.identity][0].unit_id,
            unit_label=locations[ref.identity][0].label,
            section_id=locations[ref.identity][1].section_id,
            section_label=locations[ref.identity][1].label,
            requirement=requirements[index],
            depth_mode=depths[index],
            weekly_minutes=minutes[index],
            embedded_habit=locations[ref.identity][1].embedded_habit,
            python_hooks=locations[ref.identity][1].python_hooks,
        )
        for index, ref in enumerate(ref_tuple)
    )
    return MaterializedInterviewRoute(
        bundle_id=bundle.bundle_id,
        bundle_version=bundle.bundle_version,
        route_id=focus,
        role_family=role,
        target_level=level,
        date_horizon=horizon,
        recommended_pacing_posture=recommended,
        pacing_posture=pacing,
        weekly_minutes=weekly_minutes,
        session_minutes=session_minutes,
        route_fingerprint=route_fingerprint,
        allocation_fingerprint=allocation_fingerprint,
        skills=skills,
        prerequisite_edges=edges,
    )
