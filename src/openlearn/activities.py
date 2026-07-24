"""Domain-neutral contracts for consented, evidence-producing practice activities."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

ACTIVITY_SCHEMA_VERSION = 1
ACTIVITY_ID_PATTERN = re.compile(r"^act_[a-f0-9]{32}$")
ACTIVITY_STATUSES = {
    "proposed",
    "accepted",
    "active",
    "completed",
    "abandoned",
    "cancelled",
    "failed",
}
ACTIVITY_PURPOSES = {"practice", "mastery_check"}
TERMINAL_ACTIVITY_STATUSES = {"completed", "abandoned", "cancelled", "failed"}
ACTIVITY_TRANSITIONS = {
    "proposed": {"accepted", "cancelled"},
    "accepted": {"active", "cancelled", "failed"},
    "active": {"completed", "abandoned", "cancelled", "failed"},
}
MAX_ACTIVITY_TEXT = 500
MAX_CONCEPTS = 16
MAX_EVIDENCE_KINDS = 8
MAX_DOMAIN_PAYLOAD_BYTES = 8_192
MAX_EVIDENCE_REFS = 64
REQUIRED_ACTIVITY_FIELDS = {
    "schema_version",
    "activity_id",
    "revision",
    "domain",
    "kind",
    "objective",
    "concept_ids",
    "requested_evidence",
    "scaffolding_level",
    "purpose",
    "status",
    "domain_payload",
    "resources",
    "evidence_refs",
    "created_at",
    "updated_at",
}
OPTIONAL_ACTIVITY_FIELDS = {"status_reason"}


class ActivityContractError(ValueError):
    """Raised when an activity or adapter request violates the safe contract."""


class ActivityAdapter(Protocol):
    """Narrow domain-owned validation boundary.

    Adapters validate semantic activity kinds and payloads. Tool execution remains
    in explicit application code after the learner has accepted the activity.
    """

    domain: str

    def validate_request(self, kind: str, payload: Mapping[str, object]) -> dict[str, object]:
        """Return a bounded, normalized domain payload or raise."""
        ...

    def validate_evidence(self, kind: str, payload: Mapping[str, object]) -> dict[str, object]:
        """Return bounded domain evidence safe to persist and show to the tutor."""
        ...


@dataclass
class ActivityRegistry:
    """Explicit built-in adapter registry. It never loads arbitrary packages."""

    adapters: dict[str, ActivityAdapter]

    def __init__(self, adapters: tuple[ActivityAdapter, ...] = ()) -> None:
        self.adapters = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ActivityAdapter) -> None:
        domain = _identifier(adapter.domain, "activity domain")
        if domain in self.adapters:
            raise ActivityContractError(f"activity domain already registered: {domain}")
        self.adapters[domain] = adapter

    def adapter_for(self, domain: str) -> ActivityAdapter:
        normalized = _identifier(domain, "activity domain")
        try:
            return self.adapters[normalized]
        except KeyError as exc:
            raise ActivityContractError(f"unknown activity domain: {normalized}") from exc


def propose_activity(
    request: Mapping[str, object],
    registry: ActivityRegistry,
    *,
    now: Callable[[], datetime] | None = None,
    activity_id: str | None = None,
) -> dict[str, object]:
    """Validate and create a side-effect-free proposed activity."""
    domain = _identifier(request.get("domain"), "activity domain")
    kind = _identifier(request.get("kind"), "activity kind")
    objective = _bounded_text(request.get("objective"), "activity objective")
    concept_ids = _stable_id_list(request.get("concept_ids"), "concept_ids", MAX_CONCEPTS)
    requested_evidence = _string_list(
        request.get("requested_evidence"), "requested_evidence", MAX_EVIDENCE_KINDS
    )
    if not requested_evidence:
        raise ActivityContractError("requested_evidence must not be empty")
    purpose = request.get("purpose")
    if purpose not in ACTIVITY_PURPOSES:
        raise ActivityContractError("activity purpose must be practice or mastery_check")
    scaffolding = request.get("scaffolding_level", 0)
    if not isinstance(scaffolding, int) or isinstance(scaffolding, bool) or not 0 <= scaffolding <= 3:
        raise ActivityContractError("scaffolding_level must be an integer from 0 to 3")
    raw_payload = request.get("domain_payload", {})
    if not isinstance(raw_payload, Mapping):
        raise ActivityContractError("domain_payload must be an object")
    _require_bounded_json(raw_payload, "domain_payload")
    adapter = registry.adapter_for(domain)
    payload = adapter.validate_request(kind, raw_payload)
    _require_bounded_json(payload, "domain_payload")
    resources = _validate_resources(request.get("resources", []))
    identifier = activity_id or f"act_{uuid4().hex}"
    if not ACTIVITY_ID_PATTERN.fullmatch(identifier):
        raise ActivityContractError("activity_id must be a stable act_ identifier")
    clock = now or (lambda: datetime.now(timezone.utc))
    timestamp = clock().astimezone(timezone.utc).isoformat()
    return {
        "schema_version": ACTIVITY_SCHEMA_VERSION,
        "activity_id": identifier,
        "revision": 1,
        "domain": domain,
        "kind": kind,
        "objective": objective,
        "concept_ids": concept_ids,
        "requested_evidence": requested_evidence,
        "scaffolding_level": scaffolding,
        "purpose": purpose,
        "status": "proposed",
        "domain_payload": {domain: payload},
        "resources": resources,
        "evidence_refs": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def transition_activity(
    activity: Mapping[str, object],
    target: str,
    *,
    reason: str = "",
    now: Callable[[], datetime] | None = None,
) -> tuple[dict[str, object], bool]:
    """Apply one validated lifecycle transition.

    Repeating the current target is idempotent and produces no new event.
    """
    current = validate_activity(activity)
    target_status = _identifier(target, "activity status")
    if target_status not in ACTIVITY_STATUSES:
        raise ActivityContractError(f"unknown activity status: {target_status}")
    previous_status = str(current["status"])
    if target_status == previous_status:
        return current, False
    if target_status not in ACTIVITY_TRANSITIONS.get(previous_status, set()):
        raise ActivityContractError(
            f"invalid activity transition: {previous_status} -> {target_status}"
        )
    updated = dict(current)
    updated["status"] = target_status
    current_revision = current["revision"]
    assert isinstance(current_revision, int)
    updated["revision"] = current_revision + 1
    clock = now or (lambda: datetime.now(timezone.utc))
    updated["updated_at"] = clock().astimezone(timezone.utc).isoformat()
    if reason:
        updated["status_reason"] = _bounded_text(reason, "activity transition reason")
    else:
        updated.pop("status_reason", None)
    return updated, True


def accept_activity(
    activity: Mapping[str, object],
    *,
    learner_confirmed: bool,
    now: Callable[[], datetime] | None = None,
) -> tuple[dict[str, object], bool]:
    """Accept a proposal only when the application captured learner consent."""
    if learner_confirmed is not True:
        raise ActivityContractError("activity acceptance requires explicit learner confirmation")
    return transition_activity(activity, "accepted", now=now)


def attach_evidence_reference(
    activity: Mapping[str, object],
    evidence_id: str,
    *,
    event_type: str = "activity_evidence_recorded",
) -> tuple[dict[str, object], bool]:
    """Attach an opaque event reference without copying domain evidence into state."""
    current = validate_activity(activity)
    if str(current["status"]) not in {"active", "completed"}:
        raise ActivityContractError("activity evidence requires an active or completed activity")
    identifier = _identifier(evidence_id, "evidence id")
    current_refs = current["evidence_refs"]
    assert isinstance(current_refs, list)
    refs = list(current_refs)
    if any(isinstance(item, dict) and item.get("evidence_id") == identifier for item in refs):
        return current, False
    if len(refs) >= MAX_EVIDENCE_REFS:
        raise ActivityContractError(
            f"activity cannot contain more than {MAX_EVIDENCE_REFS} evidence references"
        )
    refs.append({"evidence_id": identifier, "event_type": _identifier(event_type, "event type")})
    updated = dict(current)
    updated["evidence_refs"] = refs
    current_revision = current["revision"]
    assert isinstance(current_revision, int)
    updated["revision"] = current_revision + 1
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    return updated, True


def validate_activity(
    activity: Mapping[str, object], registry: ActivityRegistry | None = None
) -> dict[str, object]:
    """Canonically validate every persisted field and optional adapter payload."""
    keys = set(activity)
    missing = REQUIRED_ACTIVITY_FIELDS - keys
    unexpected = keys - REQUIRED_ACTIVITY_FIELDS - OPTIONAL_ACTIVITY_FIELDS
    if missing:
        raise ActivityContractError(
            f"activity is missing required fields: {', '.join(sorted(missing))}"
        )
    if unexpected:
        raise ActivityContractError(
            f"activity has unexpected fields: {', '.join(sorted(unexpected))}"
        )
    schema_version = activity.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != ACTIVITY_SCHEMA_VERSION
    ):
        raise ActivityContractError("unsupported activity schema_version")
    identifier = activity.get("activity_id")
    if not isinstance(identifier, str) or not ACTIVITY_ID_PATTERN.fullmatch(identifier):
        raise ActivityContractError("invalid activity_id")
    status = activity.get("status")
    if status not in ACTIVITY_STATUSES:
        raise ActivityContractError("invalid activity status")
    revision = activity.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ActivityContractError("invalid activity revision")
    domain = _identifier(activity.get("domain"), "activity domain")
    kind = _identifier(activity.get("kind"), "activity kind")
    objective = _bounded_text(activity.get("objective"), "activity objective")
    if objective != activity.get("objective"):
        raise ActivityContractError("persisted activity objective is not canonical")
    concept_ids = _stable_id_list(activity.get("concept_ids"), "concept_ids", MAX_CONCEPTS)
    if concept_ids != activity.get("concept_ids"):
        raise ActivityContractError("persisted concept_ids are not canonical")
    requested_evidence = _string_list(
        activity.get("requested_evidence"), "requested_evidence", MAX_EVIDENCE_KINDS
    )
    if not requested_evidence:
        raise ActivityContractError("requested_evidence must not be empty")
    if requested_evidence != activity.get("requested_evidence"):
        raise ActivityContractError("persisted requested_evidence is not canonical")
    scaffolding = activity.get("scaffolding_level")
    if not isinstance(scaffolding, int) or isinstance(scaffolding, bool) or not 0 <= scaffolding <= 3:
        raise ActivityContractError("invalid scaffolding_level")
    purpose = activity.get("purpose")
    if purpose not in ACTIVITY_PURPOSES:
        raise ActivityContractError("invalid activity purpose")
    payload = activity.get("domain_payload")
    if not isinstance(payload, Mapping) or set(payload) != {domain}:
        raise ActivityContractError("domain_payload must contain only its domain namespace")
    _require_bounded_json(payload, "domain_payload")
    domain_payload = payload[domain]
    if not isinstance(domain_payload, Mapping):
        raise ActivityContractError("domain payload namespace must contain an object")
    if registry is not None:
        adapter = registry.adapter_for(domain)
        normalized = adapter.validate_request(kind, domain_payload)
        if normalized != dict(domain_payload):
            raise ActivityContractError("persisted domain payload is not canonical")
    resources = activity.get("resources")
    validated_resources = _validate_resources(resources)
    if validated_resources != resources:
        raise ActivityContractError("persisted resources are not canonical")
    refs = activity.get("evidence_refs")
    _validate_evidence_refs(refs)
    created = _timestamp(activity.get("created_at"), "created_at")
    updated = _timestamp(activity.get("updated_at"), "updated_at")
    if updated < created:
        raise ActivityContractError("activity updated_at precedes created_at")
    if "status_reason" in activity:
        reason = _bounded_text(activity.get("status_reason"), "activity status_reason")
        if reason != activity.get("status_reason"):
            raise ActivityContractError("persisted activity status_reason is not canonical")
    return dict(activity)


def activity_event_data(
    activity: Mapping[str, object], *, previous_status: str | None = None
) -> dict[str, object]:
    """Return the generic lifecycle event projection, excluding domain payloads."""
    current = validate_activity(activity)
    data: dict[str, object] = {
        "activity_id": current["activity_id"],
        "activity_schema_version": current["schema_version"],
        "activity_revision": current["revision"],
        "domain": current["domain"],
        "kind": current["kind"],
        "purpose": current["purpose"],
        "status": current["status"],
        "concept_ids": current["concept_ids"],
    }
    if previous_status is not None:
        data["previous_status"] = previous_status
    if current.get("status_reason"):
        data["reason"] = current["status_reason"]
    return data


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
        raise ActivityContractError(f"{label} must be a lowercase identifier")
    return value


def _bounded_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActivityContractError(f"{label} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > MAX_ACTIVITY_TEXT:
        raise ActivityContractError(f"{label} is too long")
    return normalized


def _string_list(value: object, label: str, limit: int) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ActivityContractError(f"{label} must be a list with at most {limit} items")
    normalized: list[str] = []
    for item in value:
        candidate = _identifier(item, label)
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _stable_id_list(value: object, label: str, limit: int) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ActivityContractError(f"{label} must be a list with at most {limit} items")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", item
        ):
            raise ActivityContractError(f"{label} contains an invalid stable identifier")
        if item not in normalized:
            normalized.append(item)
    return normalized


def _require_bounded_json(value: object, label: str) -> None:
    try:
        encoded = json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ActivityContractError(f"{label} must contain JSON values") from exc
    if len(encoded.encode("utf-8")) > MAX_DOMAIN_PAYLOAD_BYTES:
        raise ActivityContractError(f"{label} exceeds {MAX_DOMAIN_PAYLOAD_BYTES} bytes")


def _validate_resources(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 8:
        raise ActivityContractError("resources must be a list with at most 8 items")
    resources: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ActivityContractError("each resource must be an object")
        allowed = {"resource_id", "source", "license", "uri"}
        if set(item) - allowed or not {"resource_id", "source", "license"} <= set(item):
            raise ActivityContractError("resource fields are malformed")
        resource = {
            "resource_id": _identifier(item.get("resource_id"), "resource id"),
            "source": _bounded_text(item.get("source"), "resource source"),
            "license": _bounded_text(item.get("license"), "resource license"),
        }
        if resource["resource_id"] in seen:
            raise ActivityContractError("resource IDs must be unique")
        seen.add(resource["resource_id"])
        if item.get("uri") is not None:
            resource["uri"] = _bounded_text(item.get("uri"), "resource uri")
        resources.append(resource)
    return resources


def _validate_evidence_refs(value: object) -> None:
    if not isinstance(value, list) or len(value) > MAX_EVIDENCE_REFS:
        raise ActivityContractError(
            f"evidence_refs must be a list with at most {MAX_EVIDENCE_REFS} items"
        )
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"evidence_id", "event_type"}:
            raise ActivityContractError("evidence reference fields are malformed")
        evidence_id = _identifier(item.get("evidence_id"), "evidence id")
        _identifier(item.get("event_type"), "event type")
        if evidence_id in seen:
            raise ActivityContractError("evidence IDs must be unique")
        seen.add(evidence_id)


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ActivityContractError(f"activity {label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActivityContractError(f"activity {label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ActivityContractError(f"activity {label} must include a timezone")
    if parsed.isoformat() != value:
        raise ActivityContractError(f"activity {label} is not canonical ISO-8601")
    return parsed
