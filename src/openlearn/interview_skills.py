"""Versioned interview skill graph and deterministic mastery evidence policy.

The bundled graph is canonical curriculum data. Learner evidence remains an
append-only caller-owned stream and is never rewritten by this module.
"""

from __future__ import annotations

import importlib.resources
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib.resources.abc import Traversable
from types import MappingProxyType
from typing import Literal

from openlearn.course_templates import CourseTemplate

EVIDENCE_KINDS = (
    "recognition",
    "explanation",
    "production",
    "transfer",
    "delayed_retrieval",
)
SKILL_CATEGORIES = ("concept", "pattern", "process", "communication")
PREREQUISITE_KINDS = ("blocking", "supporting")
PROBLEM_ROLES = ("primary", "supporting")
SELECTION_STATUSES = ("ready", "blocked", "weak", "due", "unassessed")
SKILL_ID_PATTERN = re.compile(
    r"(?:concept|pattern|process|communication)\.[a-z0-9]+(?:-[a-z0-9]+)*"
)


class SkillGraphError(ValueError):
    """The canonical interview graph or supplied evidence is malformed."""


@dataclass(frozen=True)
class TransferExpectation:
    minimum_novel_contexts: int
    minimum_delay_days: int
    due_after_days: int


@dataclass(frozen=True)
class EvidencePolicy:
    policy_id: str
    minimum: Mapping[str, int]
    inferable_kinds: tuple[str, ...]
    explicit_check_kinds: tuple[str, ...]
    transfer: TransferExpectation


@dataclass(frozen=True)
class Prerequisite:
    skill_id: str
    kind: Literal["blocking", "supporting"]


@dataclass(frozen=True)
class InterviewSkill:
    skill_id: str
    name: str
    category: Literal["concept", "pattern", "process", "communication"]
    description: str
    prerequisites: tuple[Prerequisite, ...]
    evidence_policy: EvidencePolicy
    curriculum_unit: str
    curriculum_order: int


@dataclass(frozen=True)
class ProblemSkillRef:
    skill_id: str
    role: Literal["primary", "supporting"]


@dataclass(frozen=True)
class InterviewProblem:
    problem_id: str
    title: str
    transfer_family: str
    skills: tuple[ProblemSkillRef, ...]


@dataclass(frozen=True)
class InterviewSkillGraph:
    schema_version: int
    graph_id: str
    graph_version: str
    mastery_policy_version: str
    skills: tuple[InterviewSkill, ...]
    problems: tuple[InterviewProblem, ...]

    def skill(self, skill_id: str) -> InterviewSkill:
        for skill in self.skills:
            if skill.skill_id == skill_id:
                return skill
        raise SkillGraphError(f"unknown skill: {skill_id}")

    def problem(self, problem_id: str) -> InterviewProblem:
        for problem in self.problems:
            if problem.problem_id == problem_id:
                return problem
        raise SkillGraphError(f"unknown problem: {problem_id}")


@dataclass(frozen=True)
class SkillAssessment:
    skill_id: str
    readiness: Literal["ready", "provisional", "weak", "unassessed"]
    selection_status: Literal["ready", "blocked", "weak", "due", "unassessed"]
    qualifying_counts: Mapping[str, int]
    reasons: tuple[str, ...]
    evidence_graph_versions: tuple[str, ...]
    evidence_policy_versions: tuple[str, ...]


@dataclass(frozen=True)
class CourseSeedUnit:
    title: str
    skill_ids: tuple[str, ...]


@dataclass(frozen=True)
class InterviewCourseSeed:
    source_template: str
    graph_id: str
    graph_version: str
    units: tuple[CourseSeedUnit, ...]

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(
            skill_id for unit in self.units for skill_id in unit.skill_ids
        )


def graph_resource() -> Traversable:
    return importlib.resources.files("openlearn").joinpath(
        "interview_skill_graphs", "coding-interview-v1.json"
    )


def load_graph_dict() -> dict[str, object]:
    try:
        value = json.loads(graph_resource().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkillGraphError("bundled interview skill graph is unreadable") from exc
    if not isinstance(value, dict):
        raise SkillGraphError("interview skill graph must be a JSON object")
    return value


def load_default_graph() -> InterviewSkillGraph:
    return validate_graph(load_graph_dict())


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SkillGraphError(f"{label} must be trimmed non-empty text")
    return value


def _positive_int(value: object, label: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SkillGraphError(f"{label} must be an integer of at least {minimum}")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SkillGraphError(f"{label} must be a string list")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise SkillGraphError(f"{label} must not contain duplicates")
    return result


def validate_graph(raw: Mapping[str, object]) -> InterviewSkillGraph:
    required = {
        "schema_version",
        "graph_id",
        "graph_version",
        "mastery_policy_version",
        "evidence_policies",
        "skills",
        "problems",
    }
    if set(raw) != required:
        raise SkillGraphError("interview skill graph fields are invalid")
    if raw.get("schema_version") != 1:
        raise SkillGraphError("unsupported interview skill graph schema")
    graph_id = _string(raw.get("graph_id"), "graph_id")
    graph_version = _string(raw.get("graph_version"), "graph_version")
    mastery_policy_version = _string(
        raw.get("mastery_policy_version"), "mastery_policy_version"
    )
    policy_values = raw.get("evidence_policies")
    if not isinstance(policy_values, list) or not policy_values:
        raise SkillGraphError("evidence_policies must be a non-empty list")
    policies: dict[str, EvidencePolicy] = {}
    for value in policy_values:
        if not isinstance(value, dict) or set(value) != {
            "id",
            "minimum",
            "inferable_kinds",
            "explicit_check_kinds",
            "transfer",
        }:
            raise SkillGraphError("evidence policy fields are invalid")
        policy_id = _string(value.get("id"), "evidence policy id")
        minimum = value.get("minimum")
        if not isinstance(minimum, dict) or set(minimum) != set(EVIDENCE_KINDS):
            raise SkillGraphError("evidence policy minimum must declare every evidence kind")
        normalized_minimum = {
            kind: _positive_int(
                minimum[kind], f"{policy_id} minimum {kind}", allow_zero=True
            )
            for kind in EVIDENCE_KINDS
        }
        inferable = _string_tuple(
            value.get("inferable_kinds"), f"{policy_id} inferable_kinds"
        )
        explicit = _string_tuple(
            value.get("explicit_check_kinds"), f"{policy_id} explicit_check_kinds"
        )
        if not set(inferable).union(explicit) <= set(EVIDENCE_KINDS):
            raise SkillGraphError(f"{policy_id} contains an invalid evidence kind")
        transfer = value.get("transfer")
        if not isinstance(transfer, dict) or set(transfer) != {
            "minimum_novel_contexts",
            "minimum_delay_days",
            "due_after_days",
        }:
            raise SkillGraphError(f"{policy_id} transfer expectation is invalid")
        parsed = EvidencePolicy(
            policy_id=policy_id,
            minimum=MappingProxyType(normalized_minimum),
            inferable_kinds=inferable,
            explicit_check_kinds=explicit,
            transfer=TransferExpectation(
                minimum_novel_contexts=_positive_int(
                    transfer["minimum_novel_contexts"],
                    f"{policy_id} minimum_novel_contexts",
                ),
                minimum_delay_days=_positive_int(
                    transfer["minimum_delay_days"],
                    f"{policy_id} minimum_delay_days",
                ),
                due_after_days=_positive_int(
                    transfer["due_after_days"], f"{policy_id} due_after_days"
                ),
            ),
        )
        if policy_id in policies:
            raise SkillGraphError(f"duplicate evidence policy: {policy_id}")
        policies[policy_id] = parsed

    skill_values = raw.get("skills")
    if not isinstance(skill_values, list) or not skill_values:
        raise SkillGraphError("skills must be a non-empty list")
    skills: list[InterviewSkill] = []
    seen_skill_ids: set[str] = set()
    for value in skill_values:
        if not isinstance(value, dict) or set(value) != {
            "id",
            "name",
            "category",
            "description",
            "prerequisites",
            "evidence_policy",
            "curriculum_unit",
            "curriculum_order",
        }:
            raise SkillGraphError("skill fields are invalid")
        skill_id = _string(value.get("id"), "skill id")
        category = value.get("category")
        if (
            category not in SKILL_CATEGORIES
            or not SKILL_ID_PATTERN.fullmatch(skill_id)
            or not skill_id.startswith(f"{category}.")
        ):
            raise SkillGraphError(f"invalid stable skill id: {skill_id}")
        if skill_id in seen_skill_ids:
            raise SkillGraphError(f"duplicate skill id: {skill_id}")
        seen_skill_ids.add(skill_id)
        policy_id = _string(value.get("evidence_policy"), f"{skill_id} evidence_policy")
        if policy_id not in policies:
            raise SkillGraphError(f"{skill_id} references unknown evidence policy")
        prerequisites_value = value.get("prerequisites")
        if not isinstance(prerequisites_value, list):
            raise SkillGraphError(f"{skill_id} prerequisites must be a list")
        prerequisites: list[Prerequisite] = []
        prerequisite_ids: set[str] = set()
        for prerequisite in prerequisites_value:
            if not isinstance(prerequisite, dict) or set(prerequisite) != {
                "skill_id",
                "kind",
            }:
                raise SkillGraphError(f"{skill_id} prerequisite fields are invalid")
            target = _string(prerequisite.get("skill_id"), "prerequisite skill_id")
            kind = prerequisite.get("kind")
            if kind not in PREREQUISITE_KINDS:
                raise SkillGraphError(f"{skill_id} prerequisite kind is invalid")
            if target == skill_id or target in prerequisite_ids:
                raise SkillGraphError(f"{skill_id} has an invalid prerequisite")
            prerequisite_ids.add(target)
            prerequisites.append(Prerequisite(target, kind))
        skills.append(
            InterviewSkill(
                skill_id=skill_id,
                name=_string(value.get("name"), f"{skill_id} name"),
                category=category,
                description=_string(value.get("description"), f"{skill_id} description"),
                prerequisites=tuple(prerequisites),
                evidence_policy=policies[policy_id],
                curriculum_unit=_string(
                    value.get("curriculum_unit"), f"{skill_id} curriculum_unit"
                ),
                curriculum_order=_positive_int(
                    value.get("curriculum_order"),
                    f"{skill_id} curriculum_order",
                    allow_zero=True,
                ),
            )
        )
    for skill in skills:
        for prerequisite in skill.prerequisites:
            if prerequisite.skill_id not in seen_skill_ids:
                raise SkillGraphError(
                    f"{skill.skill_id} has unknown prerequisite {prerequisite.skill_id}"
                )
    _reject_cycles(skills)

    problem_values = raw.get("problems")
    if not isinstance(problem_values, list) or not problem_values:
        raise SkillGraphError("problems must be a non-empty list")
    problems: list[InterviewProblem] = []
    seen_problem_ids: set[str] = set()
    for value in problem_values:
        if not isinstance(value, dict) or set(value) != {
            "id",
            "title",
            "transfer_family",
            "skills",
        }:
            raise SkillGraphError("problem fields are invalid")
        problem_id = _string(value.get("id"), "problem id")
        if problem_id in seen_problem_ids:
            raise SkillGraphError(f"duplicate problem id: {problem_id}")
        seen_problem_ids.add(problem_id)
        refs_value = value.get("skills")
        if not isinstance(refs_value, list) or not refs_value:
            raise SkillGraphError(f"{problem_id} skills must be a non-empty list")
        refs: list[ProblemSkillRef] = []
        ref_ids: set[str] = set()
        for ref in refs_value:
            if not isinstance(ref, dict) or set(ref) != {"skill_id", "role"}:
                raise SkillGraphError(f"{problem_id} problem skill fields are invalid")
            skill_id = _string(ref.get("skill_id"), "problem skill id")
            role = ref.get("role")
            if role not in PROBLEM_ROLES:
                raise SkillGraphError(f"{problem_id} problem skill role is invalid")
            if skill_id not in seen_skill_ids or skill_id in ref_ids:
                raise SkillGraphError(f"{problem_id} has an invalid skill reference")
            ref_ids.add(skill_id)
            refs.append(ProblemSkillRef(skill_id, role))
        if not any(ref.role == "primary" for ref in refs):
            raise SkillGraphError(f"{problem_id} requires a primary skill")
        problems.append(
            InterviewProblem(
                problem_id=problem_id,
                title=_string(value.get("title"), f"{problem_id} title"),
                transfer_family=_string(
                    value.get("transfer_family"), f"{problem_id} transfer_family"
                ),
                skills=tuple(refs),
            )
        )
    return InterviewSkillGraph(
        schema_version=1,
        graph_id=graph_id,
        graph_version=graph_version,
        mastery_policy_version=mastery_policy_version,
        skills=tuple(skills),
        problems=tuple(problems),
    )


def _reject_cycles(skills: Iterable[InterviewSkill]) -> None:
    edges = {
        skill.skill_id: tuple(item.skill_id for item in skill.prerequisites)
        for skill in skills
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(skill_id: str) -> None:
        if skill_id in visiting:
            raise SkillGraphError(f"interview skill graph contains a cycle at {skill_id}")
        if skill_id in visited:
            return
        visiting.add(skill_id)
        for prerequisite in edges[skill_id]:
            visit(prerequisite)
        visiting.remove(skill_id)
        visited.add(skill_id)

    for skill_id in edges:
        visit(skill_id)


def inferable_skills(
    problem: InterviewProblem,
    kind: str,
    *,
    explicit_check: bool,
    graph: InterviewSkillGraph | None = None,
) -> tuple[str, ...]:
    if kind not in EVIDENCE_KINDS:
        raise SkillGraphError(f"invalid evidence kind: {kind}")
    graph = graph or load_default_graph()
    result: list[str] = []
    for ref in problem.skills:
        policy = graph.skill(ref.skill_id).evidence_policy
        if explicit_check:
            allowed = kind in policy.explicit_check_kinds
        else:
            allowed = ref.role == "primary" and kind in policy.inferable_kinds
        if allowed:
            result.append(ref.skill_id)
    return tuple(result)


def partition_evidence(
    graph: InterviewSkillGraph,
    evidence: Iterable[Mapping[str, object]],
) -> tuple[tuple[Mapping[str, object], ...], tuple[Mapping[str, object], ...]]:
    current_ids = {skill.skill_id for skill in graph.skills}
    current: list[Mapping[str, object]] = []
    orphaned: list[Mapping[str, object]] = []
    for record in evidence:
        (current if record.get("skill_id") in current_ids else orphaned).append(record)
    return tuple(current), tuple(orphaned)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _record_qualifies(
    graph: InterviewSkillGraph,
    skill: InterviewSkill,
    record: Mapping[str, object],
) -> tuple[bool, str | None]:
    kind = record.get("kind")
    if kind not in EVIDENCE_KINDS:
        return False, None
    targets_skill, reason = _record_targets_skill(graph, skill, record)
    if not targets_skill:
        return False, reason
    if record.get("outcome") != "pass":
        return False, None
    if kind in {"production", "transfer", "delayed_retrieval"} and (
        record.get("independent") is not True or record.get("hint_level") != "none"
    ):
        return False, "hint-dependent or copied success defers mastery credit"
    if kind in {"transfer", "delayed_retrieval"} and record.get("novel_context") is not True:
        return False, "near-duplicate success does not qualify as broad transfer"
    if _parse_timestamp(record.get("observed_at")) is None:
        return False, "evidence timestamp is invalid"
    return True, None


def _record_targets_skill(
    graph: InterviewSkillGraph,
    skill: InterviewSkill,
    record: Mapping[str, object],
) -> tuple[bool, str | None]:
    kind = record.get("kind")
    if kind not in EVIDENCE_KINDS:
        return False, None
    explicit = record.get("explicit_check") is True
    problem_id = record.get("problem_id")
    if not isinstance(problem_id, str):
        return False, "evidence has no valid problem reference"
    try:
        problem = graph.problem(problem_id)
    except SkillGraphError:
        return False, "evidence references a problem outside the current graph"
    allowed = inferable_skills(
        problem, kind, explicit_check=explicit, graph=graph
    )
    if skill.skill_id not in allowed:
        return False, "supporting problem participation does not award skill credit"
    return True, None


def assess_skills(
    graph: InterviewSkillGraph,
    evidence: Iterable[Mapping[str, object]],
    *,
    now: datetime | None = None,
) -> dict[str, SkillAssessment]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current, _orphaned = partition_evidence(graph, evidence)
    by_skill: dict[str, list[Mapping[str, object]]] = {
        skill.skill_id: [] for skill in graph.skills
    }
    for record in current:
        by_skill[str(record.get("skill_id"))].append(record)

    base: dict[
        str,
        tuple[str, dict[str, int], list[str], tuple[str, ...], tuple[str, ...], bool],
    ] = {}
    for skill in graph.skills:
        records = sorted(
            by_skill[skill.skill_id],
            key=lambda item: (
                _parse_timestamp(item.get("observed_at")) or datetime.min.replace(
                    tzinfo=timezone.utc
                ),
                str(item.get("evidence_id", "")),
            ),
        )
        qualifying: dict[str, list[Mapping[str, object]]] = {
            kind: [] for kind in EVIDENCE_KINDS
        }
        rejection_reasons: list[str] = []
        for record in records:
            qualifies, reason = _record_qualifies(graph, skill, record)
            if qualifies:
                qualifying[str(record["kind"])].append(record)
            elif reason and reason not in rejection_reasons:
                rejection_reasons.append(reason)
        prior_qualifying = [
            record
            for kind in ("explanation", "production", "transfer")
            for record in qualifying[kind]
        ]
        if qualifying["delayed_retrieval"]:
            if prior_qualifying:
                first_prior = min(
                    timestamp
                    for record in prior_qualifying
                    if (timestamp := _parse_timestamp(record.get("observed_at")))
                    is not None
                )
                delay = timedelta(
                    days=skill.evidence_policy.transfer.minimum_delay_days
                )
                qualifying["delayed_retrieval"] = [
                    record
                    for record in qualifying["delayed_retrieval"]
                    if (
                        timestamp := _parse_timestamp(record.get("observed_at"))
                    )
                    is not None
                    and timestamp - first_prior >= delay
                ]
            else:
                qualifying["delayed_retrieval"] = []
            if not qualifying["delayed_retrieval"]:
                rejection_reasons.append(
                    "delayed retrieval occurred before the policy delay or without prior evidence"
                )
        counts = {kind: len(items) for kind, items in qualifying.items()}
        for kind in ("transfer", "delayed_retrieval"):
            families = {
                str(item.get("transfer_family"))
                for item in qualifying[kind]
                if item.get("transfer_family")
            }
            counts[kind] = len(families)
        required = dict(skill.evidence_policy.minimum)
        required["transfer"] = max(
            required["transfer"],
            skill.evidence_policy.transfer.minimum_novel_contexts,
        )
        meets = all(counts[kind] >= required[kind] for kind in EVIDENCE_KINDS)
        delayed_records = [
            record
            for record in records
            if record.get("kind") == "delayed_retrieval"
            and _parse_timestamp(record.get("observed_at")) is not None
            and _record_targets_skill(graph, skill, record)[0]
        ]
        latest_delayed_failed = bool(
            delayed_records and delayed_records[-1].get("outcome") == "fail"
        )
        qualified_delayed_timestamps = [
            timestamp
            for record in qualifying["delayed_retrieval"]
            if (timestamp := _parse_timestamp(record.get("observed_at"))) is not None
        ]
        stale = bool(
            meets
            and qualified_delayed_timestamps
            and now - max(qualified_delayed_timestamps)
            > timedelta(days=skill.evidence_policy.transfer.due_after_days)
        )
        due = latest_delayed_failed or stale
        if not records:
            readiness = "unassessed"
        elif meets and not due:
            readiness = "ready"
        elif any(counts.values()):
            readiness = "provisional"
        else:
            readiness = "weak"
        reasons = list(rejection_reasons)
        missing = [
            f"{kind} {counts[kind]}/{required[kind]}"
            for kind in EVIDENCE_KINDS
            if counts[kind] < required[kind]
        ]
        if readiness == "ready":
            reasons.append(
                "Ready: qualifying transfer and delayed retrieval meet the current policy."
            )
        elif readiness == "unassessed":
            reasons.append("Unassessed: no evidence has been recorded for this skill.")
        elif readiness == "provisional" and missing:
            reasons.append("Provisional evidence; still needs " + ", ".join(missing) + ".")
        elif readiness == "weak" and missing:
            reasons.append(
                "Weak: recorded attempts do not yet qualify; still needs "
                + ", ".join(missing)
                + "."
            )
        if latest_delayed_failed:
            reasons.append("Due: latest delayed retrieval failed.")
        elif stale:
            reasons.append("Due: qualifying delayed retrieval is outside the review window.")
        versions = tuple(
            sorted(
                {
                    str(record.get("graph_version"))
                    for record in records
                    if isinstance(record.get("graph_version"), str)
                }
            )
        )
        policy_versions = tuple(
            sorted(
                {
                    str(record.get("mastery_policy_version"))
                    for record in records
                    if isinstance(record.get("mastery_policy_version"), str)
                }
            )
        )
        base[skill.skill_id] = (
            readiness,
            counts,
            reasons,
            versions,
            policy_versions,
            due,
        )

    assessments: dict[str, SkillAssessment] = {}
    for skill in graph.skills:
        readiness, counts, reasons, versions, policy_versions, due = base[
            skill.skill_id
        ]
        blockers = [
            prerequisite.skill_id
            for prerequisite in skill.prerequisites
            if prerequisite.kind == "blocking"
            and base[prerequisite.skill_id][0] != "ready"
        ]
        if blockers:
            selection_status = "blocked"
            reasons.append(
                "Blocked by blocking prerequisite"
                + ("s " if len(blockers) > 1 else " ")
                + ", ".join(sorted(blockers))
                + "."
            )
        elif due:
            selection_status = "due"
        elif readiness == "ready":
            selection_status = "ready"
        elif readiness == "unassessed":
            selection_status = "unassessed"
        else:
            selection_status = "weak"
        assessments[skill.skill_id] = SkillAssessment(
            skill_id=skill.skill_id,
            readiness=readiness,
            selection_status=selection_status,
            qualifying_counts=MappingProxyType(dict(counts)),
            reasons=tuple(reasons),
            evidence_graph_versions=versions,
            evidence_policy_versions=policy_versions,
        )
    return assessments


def query_skills(
    assessments: Mapping[str, SkillAssessment],
    status: str,
) -> tuple[str, ...]:
    if status not in SELECTION_STATUSES:
        raise SkillGraphError(f"invalid skill query status: {status}")
    return tuple(
        sorted(
            skill_id
            for skill_id, assessment in assessments.items()
            if assessment.selection_status == status
        )
    )


def seed_interview_course(
    template: CourseTemplate,
    graph: InterviewSkillGraph,
) -> InterviewCourseSeed:
    if template.slug != "algorithms":
        raise SkillGraphError("only the algorithms template can seed interview prep")
    grouped: dict[str, list[InterviewSkill]] = {}
    for skill in sorted(
        graph.skills, key=lambda item: (item.curriculum_order, item.skill_id)
    ):
        grouped.setdefault(skill.curriculum_unit, []).append(skill)
    units = tuple(
        CourseSeedUnit(
            title=title,
            skill_ids=tuple(skill.skill_id for skill in skills),
        )
        for title, skills in grouped.items()
    )
    return InterviewCourseSeed(
        source_template=template.slug,
        graph_id=graph.graph_id,
        graph_version=graph.graph_version,
        units=units,
    )
