"""Versioned, deterministic curriculum authority for Technical Interview Prep.

This module is intentionally output-free and storage-free. It loads immutable
package assets, validates their cross-graph references, and exposes stable route
projections for application-layer progression code.
"""

from __future__ import annotations

import importlib.resources
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from importlib.resources.abc import Traversable
from types import MappingProxyType

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
            "due_review": list(_legacy_labels(metadata.get("review_due"))),
        },
        "legacy_context": {
            "aliases_applied": {key: aliases_applied[key] for key in sorted(aliases_applied)},
            "unassessed": sorted(set(unassessed)),
        },
        "reconciliation": {
            "reconciliation_id": reconciliation_id,
            "source_fingerprint": source_fingerprint,
            "from_version": "legacy-unversioned",
            "to_version": route["bundle_version"],
        },
        "active_operation": None,
    }


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
        if unit_id == current_unit_id and item.get("section_id") == current_section_id:
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


def load_bundle_dict() -> dict[str, object]:
    try:
        value = json.loads(bundle_resource().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CurriculumBundleError("bundled interview curriculum is unreadable") from exc
    if not isinstance(value, dict):
        raise CurriculumBundleError("interview curriculum must be a JSON object")
    return value


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
