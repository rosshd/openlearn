"""Shared, output-free course queries and creation operations."""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from openlearn import interview_curriculum, interview_prep
from openlearn import stats as stats_metrics
from openlearn.application import (
    CalibrationContext,
    CourseCard,
    CourseCreationRequest,
    CourseCreationResult,
    CourseProgress,
    CourseSnapshot,
    DashboardSnapshot,
    ReviewProgress,
    TemplateCatalog,
    TemplateSummary,
    UnitProgress,
    interview_learning_card_projection,
)
from openlearn.course_templates import available_course_templates, load_course_template

CALIBRATION_STATE_KEY = "progressive_calibration"
CREATION_SUBMISSION_STATE_KEY = "course_creation_submission_id"
CREATION_SUBMISSION_METADATA_KEY = "course_creation_submission_id"
CALIBRATION_TEXT_LIMIT = 4_000
RECONCILIATION_SCHEMA_VERSION = 1
ROUTE_ACCEPTANCE_SCHEMA_VERSION = 1


class RouteAcceptanceConflictError(RuntimeError):
    """A pending route transaction lost its optimistic concurrency fence."""


def _cli():
    """Import the legacy storage boundary lazily while it is being extracted."""
    from openlearn import cli

    return cli


def template_catalog() -> TemplateCatalog:
    return TemplateCatalog(
        templates=tuple(
            TemplateSummary(
                template_id=template.slug,
                name=template.name,
                goal=template.goal,
                tags=template.tags,
                units=template.units,
                entry_mode=template.entry_mode,
            )
            for template in available_course_templates()
        )
    )


def available_course_slug(name: str) -> str:
    """Choose the stable ``slug``, ``slug-2``, ... sequence without overwriting."""
    cli = _cli()
    base = cli.slugify(name)
    candidate = base
    suffix = 2
    while (
        cli.topic_path(candidate).exists()
        or cli.topic_deletion_tombstone_path(candidate).exists()
    ):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _unit_progress(metadata: dict[str, object]) -> tuple[UnitProgress, ...]:
    return tuple(
        UnitProgress(
            unit=int(cast(int | str | float, row["unit"])),
            title=str(row["title"]),
            known=int(cast(int | str | float, row["known"])),
            total=int(cast(int | str | float, row["total"])),
            percent=int(cast(int | str | float, row["percent"])),
        )
        for row in stats_metrics.unit_mastery(metadata)
    )


def _review_progress(metadata: dict[str, object], today: date) -> ReviewProgress:
    values = stats_metrics.review_forecast(metadata, today)
    return ReviewProgress(
        due_today=values["due_today"],
        due_this_week=values["due_this_week"],
        due_later=values["due_later"],
    )


def _course_progress(metadata: dict[str, object], today: date) -> CourseProgress:
    units = _unit_progress(metadata)
    known = sum(unit.known for unit in units)
    total = sum(unit.total for unit in units)
    return CourseProgress(
        known=known,
        total=total,
        percent=round(known / total * 100) if total else 0,
        units=units,
        reviews=_review_progress(metadata, today),
    )


def _calibration_from_state(state: dict[str, object]) -> CalibrationContext | None:
    value = state.get(CALIBRATION_STATE_KEY)
    if not isinstance(value, dict):
        return None
    goal = value.get("goal")
    experience = value.get("experience")
    skipped = value.get("skipped")
    recorded_at = value.get("recorded_at")
    return CalibrationContext(
        goal=goal if isinstance(goal, str) else "",
        experience=experience if isinstance(experience, str) else "",
        skipped=skipped if isinstance(skipped, bool) else False,
        recorded_at=recorded_at if isinstance(recorded_at, str) else "",
    )


def course_snapshot(slug: str, *, today: date | None = None) -> CourseSnapshot:
    cli = _cli()
    canonical = cli.slugify(slug)
    if canonical != slug:
        raise cli.OpenLearnError(f"invalid topic slug: {slug}")
    interview_source = (
        interview_learning_source(canonical)
        if cli.interview_profile_path(canonical).exists()
        else None
    )
    if interview_source is None:
        topic = cli.read_topic_stats(canonical)
        metadata = topic.metadata
        state = cli.load_state(canonical)
        topic_path = topic.path
        interview_card = None
    else:
        source_metadata = cast(dict[str, object], interview_source["metadata"])
        state = cast(dict[str, object], interview_source["state"])
        metadata = cli.merge_topic_state(
            cli.normalize_topic_metadata(source_metadata, canonical), state
        )
        topic_path = cli.topic_path(canonical)
        interview_card = interview_learning_card_projection(state, metadata)
    modified = datetime.fromtimestamp(topic_path.stat().st_mtime, timezone.utc).isoformat()
    card = CourseCard(
        slug=canonical,
        title=str(metadata.get("topic") or canonical.replace("-", " ").title()),
        goal=str(metadata.get("goal") or ""),
        current_focus=str(metadata.get("current_focus") or ""),
        started=metadata.get("course_started") is True,
        completed=metadata.get("course_completed") is True,
        updated_at=modified,
        progress=_course_progress(metadata, today or date.today()),
        template_id=(
            str(metadata["template_id"]) if isinstance(metadata.get("template_id"), str) else None
        ),
        interview=interview_card,
    )
    return CourseSnapshot(
        card=card,
        calibration=_calibration_from_state(state),
        mastery_profile=str(metadata.get("mastery_profile") or "proficient"),
        model=str(metadata.get("model") or ""),
        created_at=str(metadata.get("created") or ""),
    )


def interview_learning_source(slug: str) -> dict[str, object] | None:
    """Read one recovery-fenced interview lesson generation for presentation."""
    cli = _cli()
    canonical_slug = cli.slugify(slug)
    if canonical_slug != slug:
        raise cli.OpenLearnError(f"invalid topic slug: {slug}")
    route_journal = cli.interview_route_journal_path(slug)
    turn_journal = cli.topic_turn_journal_path(slug)
    while True:
        recover_interview_route_acceptance(slug)
        cli.recover_turn_commit(slug)
        # Hold both journal creation locks through the store snapshot. Writers
        # use journal-before-topic order, so no new recovery authority can
        # appear between the recovery checks and these reads.
        with (
            cli.file_lock(route_journal),
            cli.file_lock(turn_journal),
            cli.topic_store_locks(slug),
        ):
            if route_journal.exists() or turn_journal.exists():
                continue
            cli.raise_if_topic_tombstoned(slug)
            state = cli._load_state_unlocked(slug)
            canonical = state.get("interview_curriculum")
            if not isinstance(canonical, dict):
                return None
            topic_text = cli.topic_path(slug).read_text(encoding="utf-8")
            metadata, body = cli.parse_topic(topic_text)
            return {
                "slug": slug,
                "metadata": copy.deepcopy(metadata),
                "body": body,
                "state": copy.deepcopy(state),
            }


def list_course_snapshots(*, today: date | None = None) -> tuple[CourseSnapshot, ...]:
    cli = _cli()
    if not cli.topics_dir().exists():
        return ()
    paths = cli.recent_topic_paths()
    return tuple(course_snapshot(path.stem, today=today) for path in paths)


def dashboard_snapshot(*, now: datetime | None = None) -> DashboardSnapshot:
    """Read dashboard data without activating a course or recording activity."""
    cli = _cli()
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    snapshots = list_course_snapshots(today=moment.date())
    cards = tuple(item.card for item in snapshots)
    active_slug = cli.get_active_topic()
    active = next((card for card in cards if card.slug == active_slug and card.incomplete), None)
    resume = active or next((card for card in cards if card.incomplete), None)
    if resume is None and cards:
        resume = cards[0]
    reviews = ReviewProgress(
        due_today=sum(card.progress.reviews.due_today for card in cards),
        due_this_week=sum(card.progress.reviews.due_this_week for card in cards),
        due_later=sum(card.progress.reviews.due_later for card in cards),
    )
    return DashboardSnapshot(
        courses=cards,
        resume=resume,
        active_slug=active_slug,
        reviews=reviews,
        generated_at=moment.astimezone(timezone.utc).isoformat(),
    )


def _reconciliation_checkpoint(_stage: str) -> None:
    """Fault-injection seam for the curriculum reconciliation publication stages."""


def _route_acceptance_checkpoint(_stage: str) -> None:
    """Fault-injection seam for route acceptance publication stages."""


def _event_has_id(path: Path, event_id: str) -> bool:
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event_id") == event_id:
            return True
    return False


def _route_journal_identity(value: dict[str, object]) -> dict[str, object]:
    return {key: value[key] for key in value if key != "journal_sha256"}


def _validated_route_acceptance_journal(
    slug: str, value: object
) -> dict[str, object]:
    required = {
        "schema_version",
        "slug",
        "topic_generation",
        "profile_before_fingerprint",
        "profile_after",
        "canonical_after",
        "metadata_projection",
        "receipt",
        "event",
        "journal_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise _cli().OpenLearnError("saved interview curriculum acceptance is malformed")
    receipt = value.get("receipt")
    event = value.get("event")
    if (
        value.get("schema_version") != ROUTE_ACCEPTANCE_SCHEMA_VERSION
        or value.get("slug") != slug
        or not isinstance(value.get("topic_generation"), str)
        or not isinstance(value.get("profile_before_fingerprint"), str)
        or not isinstance(value.get("profile_after"), dict)
        or not isinstance(value.get("canonical_after"), dict)
        or not isinstance(value.get("metadata_projection"), dict)
        or not isinstance(receipt, dict)
        or not isinstance(event, dict)
    ):
        raise _cli().OpenLearnError("saved interview curriculum acceptance is malformed")
    action_id = receipt.get("action_id")
    if (
        receipt.get("schema_version") != ROUTE_ACCEPTANCE_SCHEMA_VERSION
        or not isinstance(action_id, str)
        or not action_id.startswith("route_")
        or receipt.get("topic_generation") != value["topic_generation"]
        or event.get("event_id") != f"{action_id}:0"
        or event.get("slug") != slug
        or event.get("data") != receipt
        or value.get("journal_sha256")
        != interview_curriculum.canonical_fingerprint(_route_journal_identity(value))
    ):
        raise _cli().OpenLearnError("saved interview curriculum acceptance has invalid identity")
    return copy.deepcopy(value)


def _apply_route_acceptance_journal(slug: str, journal: dict[str, object]) -> None:
    cli = _cli()
    journal = _validated_route_acceptance_journal(slug, journal)
    generation = journal["topic_generation"]
    profile_after = journal.get("profile_after")
    canonical_after = journal.get("canonical_after")
    projection = journal.get("metadata_projection")
    receipt = journal.get("receipt")
    event = journal.get("event")
    if not all(isinstance(value, dict) for value in (profile_after, canonical_after, projection, receipt, event)):
        raise cli.OpenLearnError("saved interview curriculum acceptance is malformed")
    profile_path = cli.interview_profile_path(slug)
    with cli.topic_store_locks(slug), cli.file_lock(profile_path):
        if generation != cli.current_topic_generation(slug):
            raise RouteAcceptanceConflictError(
                "topic changed during interview curriculum acceptance"
            )
        current_profile = interview_prep.load_profile(profile_path)
        current_profile_fingerprint = interview_curriculum.canonical_fingerprint(current_profile)
        before_fingerprint = journal.get("profile_before_fingerprint")
        after_fingerprint = interview_curriculum.canonical_fingerprint(profile_after)
        if current_profile_fingerprint not in {before_fingerprint, after_fingerprint}:
            raise RouteAcceptanceConflictError(
                "interview profile changed while course outline confirmation was pending"
            )
        state = cli._load_state_unlocked(slug)
        internal_raw = state.get("_openlearn_internal")
        internal = copy.deepcopy(internal_raw) if isinstance(internal_raw, dict) else {}
        revision = internal.get("course_revision", 0)
        base_revision = receipt.get("base_revision")
        final_revision = receipt.get("final_revision")
        if revision not in {base_revision, final_revision}:
            raise RouteAcceptanceConflictError(
                "course changed while course outline confirmation was pending"
            )
        active = internal.get("active_turn")
        if isinstance(active, dict) and revision != final_revision:
            raise RouteAcceptanceConflictError(
                "finish or cancel the active tutor operation before changing the course outline"
            )
        receipts_raw = state.get("_interview_route_receipts")
        receipts = copy.deepcopy(receipts_raw) if isinstance(receipts_raw, dict) else {}
        action_id = str(receipt["action_id"])
        existing = receipts.get(action_id)
        if revision == final_revision and (
            existing != receipt or state.get("interview_curriculum") != canonical_after
        ):
            raise RouteAcceptanceConflictError(
                "course changed while course outline confirmation was pending"
            )
        topic_text = cli.topic_path(slug).read_text(encoding="utf-8")
        metadata, body = cli.parse_topic(topic_text)

        # All optimistic-concurrency fences are checked before the first write.
        if current_profile_fingerprint != after_fingerprint:
            interview_prep._write(profile_path, profile_after)
        _route_acceptance_checkpoint("after_profile")
        if existing is not None and existing != receipt:
            raise cli.OpenLearnError("interview curriculum acceptance receipt conflicts")
        receipts[action_id] = copy.deepcopy(receipt)
        state["_interview_route_receipts"] = receipts
        state["interview_curriculum"] = copy.deepcopy(canonical_after)
        internal["schema_version"] = 1
        internal["course_revision"] = final_revision
        internal.setdefault("turn_results", {})
        state["_openlearn_internal"] = internal
        cli.write_text_atomic(
            cli.topic_state_path(slug),
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )
        _route_acceptance_checkpoint("after_state")

        metadata = dict(metadata)
        metadata.update(copy.deepcopy(projection))
        metadata["course_started"] = True
        metadata["course_completed"] = False
        cli.write_text_atomic(cli.topic_path(slug), cli.format_topic(metadata, body))
        _route_acceptance_checkpoint("after_topic")

        events_path = cli.topic_events_path(slug)
        event_id = str(event["event_id"])
        if not _event_has_id(events_path, event_id):
            existing_events = events_path.read_text(encoding="utf-8") if events_path.exists() else ""
            if existing_events and not existing_events.endswith("\n"):
                existing_events += "\n"
            cli.write_text_atomic(
                events_path,
                existing_events + json.dumps(event, sort_keys=True) + "\n",
            )
        _route_acceptance_checkpoint("after_event")


def recover_interview_route_acceptance(slug: str) -> bool:
    """Finish one validated pending route transaction before interview state is used."""
    cli = _cli()
    journal_path = cli.interview_route_journal_path(slug)
    with cli.file_lock(journal_path):
        if not journal_path.exists():
            return False
        try:
            raw = json.loads(journal_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise cli.OpenLearnError(
                "saved interview curriculum acceptance is unreadable"
            ) from exc
        journal = _validated_route_acceptance_journal(slug, raw)
        try:
            _apply_route_acceptance_journal(slug, journal)
        except RouteAcceptanceConflictError:
            cli.durable_unlink(journal_path)
            raise
        cli.durable_unlink(journal_path)
        return True


def accept_interview_curriculum(
    slug: str,
    *,
    action: str,
    changes: dict[str, object] | None = None,
    outline: str = "",
    submission_id: str | None = None,
    expected_revision: int | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Confirm, skip, or change one route through a recoverable shared transaction."""
    cli = _cli()
    if action not in {"confirm", "skip", "change"}:
        raise ValueError("interview curriculum acceptance action is invalid")
    moment = now or datetime.now(timezone.utc)
    canonical_slug = cli.slugify(slug)
    if canonical_slug != slug or not cli.interview_profile_path(slug).exists():
        raise cli.OpenLearnError(f"interview course not found: {slug}")
    payload_hash = interview_curriculum.canonical_fingerprint(
        {"action": action, "changes": changes or {}, "outline": outline.strip()}
    )
    if submission_id is not None:
        try:
            parsed = UUID(submission_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("submission ID must be a canonical UUID") from exc
        if str(parsed) != submission_id:
            raise ValueError("submission ID must be a canonical UUID")
        action_id = f"route_{parsed.hex}"
    else:
        action_id = ""
    journal_path = cli.interview_route_journal_path(slug)
    with cli.file_lock(journal_path):
        if journal_path.exists():
            try:
                pending = json.loads(journal_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise cli.OpenLearnError(
                    "saved interview curriculum acceptance is unreadable"
                ) from exc
            if not isinstance(pending, dict):
                raise cli.OpenLearnError("saved interview curriculum acceptance is malformed")
            _apply_route_acceptance_journal(
                slug, _validated_route_acceptance_journal(slug, pending)
            )
            cli.durable_unlink(journal_path)

        with cli.topic_store_locks(slug), cli.file_lock(cli.interview_profile_path(slug)):
            cli.raise_if_topic_tombstoned(slug)
            state = cli._load_state_unlocked(slug)
            receipts_raw = state.get("_interview_route_receipts")
            receipts = receipts_raw if isinstance(receipts_raw, dict) else {}
            prior = receipts.get(action_id)
            if isinstance(prior, dict):
                if prior.get("payload_hash") != payload_hash:
                    raise cli.OpenLearnError(
                        "submission ID was already used for another course outline change"
                    )
                canonical = state.get("interview_curriculum")
                if not isinstance(canonical, dict):
                    raise cli.OpenLearnError("saved curriculum acceptance lost its route")
                return {
                    "profile": interview_prep.load_profile(cli.interview_profile_path(slug)),
                    "canonical": canonical,
                    "receipt": prior,
                    "replayed": True,
                }
            internal_raw = state.get("_openlearn_internal")
            internal = internal_raw if isinstance(internal_raw, dict) else {}
            revision = internal.get("course_revision", 0)
            if not isinstance(revision, int) or revision < 0:
                revision = 0
            if submission_id is None:
                prior = next(
                    (
                        value
                        for value in receipts.values()
                        if isinstance(value, dict)
                        and value.get("submission_mode") == "legacy"
                        and value.get("payload_hash") == payload_hash
                        and value.get("final_revision") == revision
                    ),
                    None,
                )
                if isinstance(prior, dict):
                    canonical = state.get("interview_curriculum")
                    if not isinstance(canonical, dict):
                        raise cli.OpenLearnError(
                            "saved curriculum acceptance lost its route"
                        )
                    return {
                        "profile": interview_prep.load_profile(
                            cli.interview_profile_path(slug)
                        ),
                        "canonical": canonical,
                        "receipt": prior,
                        "replayed": True,
                    }
                legacy_identity = interview_curriculum.canonical_fingerprint(
                    {
                        "slug": slug,
                        "topic_generation": cli.current_topic_generation(slug),
                        "base_revision": revision,
                        "payload_hash": payload_hash,
                    }
                )
                action_id = f"route_legacy_{legacy_identity[:32]}"
            if expected_revision is not None and expected_revision != revision:
                raise cli.OpenLearnError("course changed elsewhere; reload before confirming")
            if isinstance(internal.get("active_turn"), dict):
                raise cli.OpenLearnError(
                    "finish or cancel the active tutor operation before changing the course outline"
                )
            profile_before = interview_prep.load_profile(cli.interview_profile_path(slug))
            existing_canonical = state.get("interview_curriculum")
            bundle = (
                interview_curriculum.load_pinned_bundle(
                    str(existing_canonical["bundle_id"]),
                    str(existing_canonical["bundle_version"]),
                )
                if isinstance(existing_canonical, dict)
                else interview_curriculum.load_default_bundle()
            )
            profile_after, route = interview_prep.accepted_curriculum_profile(
                profile_before,
                action=action,
                changes=changes,
                outline=outline,
                now=moment,
                bundle=bundle,
            )
            if isinstance(existing_canonical, dict):
                canonical_after, cursor_decision = (
                    interview_curriculum.rematerialize_canonical_state(
                        existing_canonical, route, change_id=action_id
                    )
                )
                old_route_fingerprint = existing_canonical.get("route_fingerprint")
                old_cursor = copy.deepcopy(existing_canonical.get("cursor"))
            else:
                canonical_after = interview_curriculum.canonical_state_from_route(
                    route, acceptance_id=action_id
                )
                cursor_decision = "first-technical-target"
                old_route_fingerprint = None
                old_cursor = None
            projection = interview_curriculum.compatibility_projection(canonical_after)
            final_revision = revision + 1
            receipt = {
                "schema_version": ROUTE_ACCEPTANCE_SCHEMA_VERSION,
                "action_id": action_id,
                "action": action,
                "submission_mode": (
                    "explicit" if submission_id is not None else "legacy"
                ),
                "payload_hash": payload_hash,
                "topic_generation": cli.current_topic_generation(slug),
                "base_revision": revision,
                "final_revision": final_revision,
                "old_route_fingerprint": old_route_fingerprint,
                "new_route_fingerprint": route["route_fingerprint"],
                "old_cursor": old_cursor,
                "new_cursor": copy.deepcopy(canonical_after["cursor"]),
                "cursor_decision": cursor_decision,
                "profile_fingerprint": interview_curriculum.canonical_fingerprint(
                    profile_after
                ),
                "created_at": moment.astimezone(timezone.utc).isoformat(),
            }
            event = {
                "schema_version": cli.EVENT_SCHEMA_VERSION,
                "event_id": f"{action_id}:0",
                "ts": moment.astimezone(timezone.utc).isoformat(),
                "event_type": "interview_curriculum_route_accepted",
                "slug": slug,
                "data": copy.deepcopy(receipt),
            }
            journal = {
                "schema_version": ROUTE_ACCEPTANCE_SCHEMA_VERSION,
                "slug": slug,
                "topic_generation": cli.current_topic_generation(slug),
                "profile_before_fingerprint": interview_curriculum.canonical_fingerprint(
                    profile_before
                ),
                "profile_after": profile_after,
                "canonical_after": canonical_after,
                "metadata_projection": projection,
                "receipt": receipt,
                "event": event,
            }
            journal["journal_sha256"] = interview_curriculum.canonical_fingerprint(
                _route_journal_identity(journal)
            )
            _validated_route_acceptance_journal(slug, journal)
            cli.write_text_atomic(journal_path, json.dumps(journal, indent=2, sort_keys=True) + "\n")
            journal_path.chmod(0o600)
            _route_acceptance_checkpoint("after_journal")
        try:
            _apply_route_acceptance_journal(slug, journal)
        except RouteAcceptanceConflictError:
            cli.durable_unlink(journal_path)
            raise
        cli.durable_unlink(journal_path)
        return {
            "profile": profile_after,
            "canonical": canonical_after,
            "receipt": receipt,
            "replayed": False,
        }


def _route_for_profile(profile_value: dict[str, object]) -> dict[str, object]:
    allocation = profile_value.get("curriculum_allocation")
    if isinstance(allocation, dict) and isinstance(allocation.get("route"), dict):
        return copy.deepcopy(cast(dict[str, object], allocation["route"]))
    profile = profile_value.get("profile")
    placement = profile_value.get("placement")
    if not isinstance(profile, dict):
        raise ValueError("interview-prep profile is missing")
    survey = placement.get("survey") if isinstance(placement, dict) else None
    survey = survey if isinstance(survey, dict) else {}
    ratings = survey.get("ratings")
    ratings = ratings if isinstance(ratings, dict) else {}
    route = interview_curriculum.materialize_adaptive_route(
        interview_curriculum.load_default_bundle(),
        role_family=str(survey.get("role_family") or profile.get("role_family") or "general SWE"),
        target_level=str(survey.get("target_level") or profile.get("target_level") or "entry"),
        interview_focus=str(survey.get("interview_focus") or "coding"),
        interview_date=str(profile.get("interview_date") or ""),
        weekly_minutes=int(cast(int, profile.get("weekly_minutes") or 120)),
        session_minutes=int(cast(int, profile.get("session_minutes") or 45)),
        confidence_ratings={str(key): int(value) for key, value in ratings.items()},
        pacing_posture_override=None,
        current_date=date.today(),
    )
    return route.to_dict()


def _load_authoritative_state_unlocked(slug: str) -> dict[str, object]:
    """Read reconciliation input without the display layer's corrupt-as-empty fallback."""
    cli = _cli()
    path = cli.topic_state_path(slug)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise cli.OpenLearnError(
            "interview curriculum source state is unreadable; repair it before continuing"
        ) from exc
    if not isinstance(value, dict):
        raise cli.OpenLearnError(
            "interview curriculum source state is malformed; repair it before continuing"
        )
    return value


def _load_authoritative_events(path: Path) -> list[dict[str, object]]:
    """Read the complete append-only authority or fail without publishing."""
    cli = _cli()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise cli.OpenLearnError(
            "interview curriculum source events are unreadable; repair them before continuing"
        ) from exc
    events: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise cli.OpenLearnError(
                "interview curriculum source events are malformed at "
                f"line {line_number}; repair them before continuing"
            ) from exc
        if not isinstance(value, dict):
            raise cli.OpenLearnError(
                "interview curriculum source events are malformed at "
                f"line {line_number}; repair them before continuing"
            )
        events.append(value)
    return events


_RECONCILIATION_METADATA_KEYS = (
    "template_id",
    "template_units",
    "course_units",
    "current_unit",
    "current_slide",
    "current_focus",
    "known",
    "weak_spots",
    "review_due",
    "placement_result",
    "slide_coverage",
)
_RECONCILIATION_STATE_KEYS = (
    "concept_attempts",
    "review_history",
    "assessment_history",
)


def _reconciliation_source_payload(
    slug: str,
    *,
    profile_value: dict[str, object],
    journal: dict[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], str]:
    """Capture mutable legacy authorities while normalizing our own partial writes."""
    cli = _cli()
    topic_text = cli.topic_path(slug).read_text(encoding="utf-8")
    raw_metadata, body = cli.parse_topic(topic_text)
    state = _load_authoritative_state_unlocked(slug)
    metadata = cli.merge_topic_state(cli.normalize_topic_metadata(raw_metadata, slug), state)
    events = _load_authoritative_events(cli.topic_events_path(slug))
    if journal is not None:
        source_before = journal.get("source_payload")
        projections = [
            value
            for value in (
                journal.get("metadata_projection"),
                journal.get("superseded_projection"),
            )
            if isinstance(value, dict)
        ]
        source_metadata = (
            source_before.get("metadata") if isinstance(source_before, dict) else None
        )
        if projections and isinstance(source_metadata, dict):
            metadata = dict(metadata)
            for key in ("course_units", "current_unit", "current_slide", "current_focus"):
                if any(raw_metadata.get(key) == projection.get(key) for projection in projections):
                    if key in source_metadata:
                        metadata[key] = copy.deepcopy(source_metadata[key])
                    else:
                        metadata.pop(key, None)
        reconciliation_ids = {
            value
            for value in (
                journal.get("reconciliation_id"),
                journal.get("superseded_reconciliation_id"),
            )
            if isinstance(value, str)
        }
        events = [
            event
            for event in events
            if not (
                isinstance(event.get("data"), dict)
                and event["data"].get("reconciliation_id") in reconciliation_ids
            )
        ]
    payload = {
        "topic_generation": cli.topic_generation_from_metadata(slug, raw_metadata),
        "metadata": {key: metadata.get(key) for key in _RECONCILIATION_METADATA_KEYS},
        "state": {key: state.get(key) for key in _RECONCILIATION_STATE_KEYS},
        "transcript_fingerprint": interview_curriculum.canonical_fingerprint(body),
        "profile_context": {
            "profile": profile_value.get("profile"),
            "placement": profile_value.get("placement"),
        },
        "events": events,
        "allocation": profile_value.get("curriculum_allocation"),
    }
    return payload, raw_metadata, state, body


def _event_has_reconciliation(events_path: Path, reconciliation_id: str) -> bool:
    for event in _load_authoritative_events(events_path):
        data = event.get("data") if isinstance(event, dict) else None
        if isinstance(data, dict) and data.get("reconciliation_id") == reconciliation_id:
            return True
    return False


def _apply_reconciliation_journal(slug: str, journal: dict[str, object]) -> bool:
    cli = _cli()
    generation = cli.current_topic_generation(slug)
    if generation != journal.get("topic_generation"):
        cli.durable_unlink(cli.interview_reconciliation_journal_path(slug))
        return False
    canonical_state = journal.get("canonical_state")
    metadata_projection = journal.get("metadata_projection")
    event = journal.get("event")
    receipt = journal.get("receipt")
    if not all(
        isinstance(item, dict)
        for item in (canonical_state, metadata_projection, event, receipt)
    ):
        raise cli.OpenLearnError("interview curriculum reconciliation journal is malformed")
    source_payload = journal.get("source_payload")
    if not isinstance(source_payload, dict):
        raise cli.OpenLearnError("interview curriculum reconciliation journal is malformed")
    profile_value = interview_prep.load_profile(cli.interview_profile_path(slug))
    current_source, _raw_metadata, _state, _body = _reconciliation_source_payload(
        slug, profile_value=profile_value, journal=journal
    )
    if (
        interview_curriculum.canonical_fingerprint(current_source)
        != journal.get("source_fingerprint")
    ):
        cli.durable_unlink(cli.interview_reconciliation_journal_path(slug))
        return False
    reconciliation_id = str(journal["reconciliation_id"])
    state_path = cli.topic_state_path(slug)
    topic_path = cli.topic_path(slug)
    raw_metadata, body = cli.parse_topic(topic_path.read_text(encoding="utf-8"))
    current_state = cli._load_state_unlocked(slug)
    current_canonical = current_state.get("interview_curriculum")
    if current_canonical != canonical_state:
        merged_state = dict(current_state)
        merged_state["interview_curriculum"] = canonical_state
        cli.write_text_atomic(
            state_path,
            json.dumps(merged_state, indent=2, sort_keys=True) + "\n",
        )
    projection_keys = ("course_units", "current_unit", "current_slide", "current_focus")
    if any(raw_metadata.get(key) != metadata_projection.get(key) for key in projection_keys):
        merged_metadata = dict(raw_metadata)
        for key in projection_keys:
            merged_metadata[key] = metadata_projection[key]
        cli.write_text_atomic(topic_path, cli.format_topic(merged_metadata, body))
    _reconciliation_checkpoint("after_state")

    events_path = cli.topic_events_path(slug)
    if not _event_has_reconciliation(events_path, reconciliation_id):
        existing = events_path.read_text(encoding="utf-8") if events_path.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        cli.write_text_atomic(events_path, existing + json.dumps(event, sort_keys=True) + "\n")
    _reconciliation_checkpoint("after_event")

    receipt_path = cli.interview_reconciliation_receipt_path(slug)
    expected_receipt = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if not receipt_path.exists() or receipt_path.read_text(encoding="utf-8") != expected_receipt:
        cli.write_text_atomic(receipt_path, expected_receipt)
        receipt_path.chmod(0o600)
    _reconciliation_checkpoint("after_receipt")
    cli.durable_unlink(cli.interview_reconciliation_journal_path(slug))
    return True


def _position_from_canonical(canonical: dict[str, object]) -> dict[str, object]:
    cursor = canonical.get("cursor")
    route = canonical.get("route")
    if not isinstance(cursor, dict) or not isinstance(route, dict):
        raise ValueError("canonical interview curriculum state is malformed")
    ref = cursor.get("skill_ref")
    if not isinstance(ref, dict) or not isinstance(ref.get("skill_id"), str):
        raise ValueError("canonical interview curriculum cursor is malformed")
    skill_id = str(ref["skill_id"])
    identity_keys = (
        "graph_id",
        "graph_version",
        "mastery_policy_version",
        "skill_id",
    )
    skills = route.get("skills")
    selected = (
        next(
            (
                item
                for item in skills
                if isinstance(item, dict)
                and isinstance(item.get("skill_ref"), dict)
                and all(item["skill_ref"].get(key) == ref.get(key) for key in identity_keys)
            ),
            None,
        )
        if isinstance(skills, list)
        else None
    )
    return {
        "unit_id": cursor["unit_id"],
        "section_id": cursor["section_id"],
        "skill_id": skill_id,
        "emphasis": selected.get("depth_mode", "learn") if isinstance(selected, dict) else "learn",
        "review_reason": (
            "legacy weak spot" if cursor.get("instruction_status") == "review" else None
        ),
    }


def prepare_interview_curriculum(slug: str, *, boundary: str = "resume") -> dict[str, object]:
    """Explicitly reconcile a legacy interview course into its pinned route."""
    if boundary not in {"preparation", "resume"}:
        raise ValueError("interview curriculum preparation boundary is invalid")
    cli = _cli()
    recover_interview_route_acceptance(slug)
    canonical_slug = cli.slugify(slug)
    if canonical_slug != slug or not cli.interview_profile_path(slug).exists():
        raise cli.OpenLearnError(f"interview course not found: {slug}")
    journal_path = cli.interview_reconciliation_journal_path(slug)
    profile_path = cli.interview_profile_path(slug)
    with (
        cli.file_lock(journal_path),
        cli.topic_store_locks(slug),
        cli.file_lock(profile_path),
    ):
        cli.raise_if_topic_tombstoned(slug)
        stale_journal: dict[str, object] | None = None
        if journal_path.exists():
            try:
                pending = json.loads(journal_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise cli.OpenLearnError(
                    "interview curriculum reconciliation journal is unreadable"
                ) from exc
            if not isinstance(pending, dict):
                raise cli.OpenLearnError("interview curriculum reconciliation journal is malformed")
            if not _apply_reconciliation_journal(slug, pending) and pending.get(
                "topic_generation"
            ) == cli.current_topic_generation(slug):
                stale_journal = pending

        profile_value = interview_prep.load_profile(profile_path)
        state = _load_authoritative_state_unlocked(slug)
        existing = state.get("interview_curriculum")
        stale_canonical = (
            stale_journal.get("canonical_state")
            if isinstance(stale_journal, dict)
            else None
        )
        if isinstance(existing, dict) and existing != stale_canonical:
            return _position_from_canonical(existing)
        receipt_path = cli.interview_reconciliation_receipt_path(slug)
        if receipt_path.exists() and stale_journal is None:
            try:
                receipt_value = json.loads(receipt_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise cli.OpenLearnError(
                    "interview curriculum reconciliation receipt is unreadable"
                ) from exc
            receipt_canonical = (
                receipt_value.get("canonical_state")
                if isinstance(receipt_value, dict)
                and receipt_value.get("topic_generation") == cli.current_topic_generation(slug)
                else None
            )
            if isinstance(receipt_canonical, dict):
                state["interview_curriculum"] = receipt_canonical
                cli.write_text_atomic(
                    cli.topic_state_path(slug),
                    json.dumps(state, indent=2, sort_keys=True) + "\n",
                )
                projection = interview_curriculum.compatibility_projection(receipt_canonical)
                current_text = cli.topic_path(slug).read_text(encoding="utf-8")
                current_metadata, current_body = cli.parse_topic(current_text)
                current_metadata.update(projection)
                cli.write_text_atomic(
                    cli.topic_path(slug),
                    cli.format_topic(current_metadata, current_body),
                )
                return _position_from_canonical(receipt_canonical)
        route = _route_for_profile(profile_value)
        source_payload, raw_metadata, state, body = _reconciliation_source_payload(
            slug,
            profile_value=profile_value,
            journal=stale_journal,
        )
        metadata = cli.merge_topic_state(cli.normalize_topic_metadata(raw_metadata, slug), state)
        legacy_events = cast(list[dict[str, object]], source_payload["events"])
        source_fingerprint = interview_curriculum.canonical_fingerprint(source_payload)
        generation = cli.topic_generation_from_metadata(slug, raw_metadata)
        reconciliation_id = "reconcile_" + interview_curriculum.canonical_fingerprint(
            {
                "generation": generation,
                "source_fingerprint": source_fingerprint,
                "bundle_id": route["bundle_id"],
                "bundle_version": route["bundle_version"],
            }
        )
        bundle = interview_curriculum.load_default_bundle()
        canonical = interview_curriculum.build_canonical_curriculum_state(
            bundle,
            route,
            metadata=metadata,
            dynamic_state={**state, "legacy_events": legacy_events},
            source_fingerprint=source_fingerprint,
            reconciliation_id=reconciliation_id,
        )
        projection = interview_curriculum.compatibility_projection(canonical)
        timestamp = datetime.now(timezone.utc).isoformat()
        event = {
            "schema_version": cli.EVENT_SCHEMA_VERSION,
            "ts": timestamp,
            "event_type": "interview_curriculum_reconciled",
            "slug": slug,
            "data": {
                "reconciliation_id": reconciliation_id,
                "source_fingerprint": source_fingerprint,
                "from_version": "legacy-unversioned",
                "to_version": route["bundle_version"],
                "aliases_applied": canonical["legacy_context"]["aliases_applied"],
                "unmatched_references": canonical["legacy_context"]["unassessed"],
                "pinned_destination": {
                    "bundle_id": route["bundle_id"],
                    "bundle_version": route["bundle_version"],
                    "route_id": route["route_id"],
                },
            },
        }
        receipt = {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "slug": slug,
            "topic_generation": generation,
            "reconciliation_id": reconciliation_id,
            "source_fingerprint": source_fingerprint,
            "from_version": "legacy-unversioned",
            "to_version": route["bundle_version"],
            "aliases_applied": canonical["legacy_context"]["aliases_applied"],
            "unmatched_references": canonical["legacy_context"]["unassessed"],
            "pinned_destination": event["data"]["pinned_destination"],
            "canonical_state": canonical,
        }
        journal = {
            "schema_version": RECONCILIATION_SCHEMA_VERSION,
            "slug": slug,
            "topic_generation": generation,
            "reconciliation_id": reconciliation_id,
            "source_fingerprint": source_fingerprint,
            "source_payload": source_payload,
            "canonical_state": canonical,
            "metadata_projection": projection,
            "event": event,
            "receipt": receipt,
        }
        if stale_journal is not None:
            journal["superseded_projection"] = stale_journal.get(
                "metadata_projection"
            )
            journal["superseded_reconciliation_id"] = stale_journal.get(
                "reconciliation_id"
            )
        cli.write_text_atomic(journal_path, json.dumps(journal, indent=2, sort_keys=True) + "\n")
        journal_path.chmod(0o600)
        _reconciliation_checkpoint("after_journal")
        _apply_reconciliation_journal(slug, journal)
        return _position_from_canonical(canonical)


def _validate_request(request: CourseCreationRequest) -> None:
    cli = _cli()
    if not request.name.strip():
        raise cli.OpenLearnError("course name is required")
    if request.name != request.name.strip():
        raise cli.OpenLearnError("course name must not start or end with whitespace")
    if request.template_id is None and not request.goal.strip():
        raise cli.OpenLearnError("a freeform course goal is required")
    if request.submission_id is not None:
        try:
            parsed = UUID(request.submission_id)
        except (ValueError, AttributeError) as exc:
            raise cli.OpenLearnError("submission ID must be a canonical UUID") from exc
        if str(parsed) != request.submission_id:
            raise cli.OpenLearnError("submission ID must be a canonical UUID")
    calibration = request.calibration
    if calibration is not None and (
        len(calibration.goal) > CALIBRATION_TEXT_LIMIT
        or len(calibration.experience) > CALIBRATION_TEXT_LIMIT
    ):
        raise cli.OpenLearnError("calibration text must be at most 4000 characters")


def _find_creation_submission(submission_id: str) -> str | None:
    cli = _cli()
    for path in cli.recent_topic_paths():
        state = cli.load_state(path.stem)
        if state.get(CREATION_SUBMISSION_STATE_KEY) == submission_id:
            return path.stem
        summary = cli.read_topic_summary(path)
        if summary.metadata.get(CREATION_SUBMISSION_METADATA_KEY) == submission_id:
            cli.update_state_atomic(
                path.stem,
                lambda current: current.__setitem__(
                    CREATION_SUBMISSION_STATE_KEY, submission_id
                ),
            )
            return path.stem
    return None


def _repair_creation_artifacts(slug: str, request: CourseCreationRequest) -> None:
    """Complete only missing creation artifacts after an interrupted first write."""
    cli = _cli()

    def repair_state(state: dict[str, object]) -> None:
        if request.submission_id is not None:
            state.setdefault(CREATION_SUBMISSION_STATE_KEY, request.submission_id)
        if request.calibration is not None and CALIBRATION_STATE_KEY not in state:
            calibration = request.calibration
            state[CALIBRATION_STATE_KEY] = {
                "goal": calibration.goal.strip(),
                "experience": calibration.experience.strip(),
                "skipped": calibration.skipped,
                "recorded_at": (
                    calibration.recorded_at
                    or datetime.now(timezone.utc).isoformat()
                ),
            }

    cli.update_state_atomic(slug, repair_state)
    summary = cli.read_topic_summary(cli.topic_path(slug))
    template_id = summary.metadata.get("template_id")
    if not isinstance(template_id, str):
        return
    template = load_course_template(template_id)
    if template.entry_mode != "interview_prep":
        return
    profile_path = cli.interview_profile_path(slug)
    with cli.topic_store_locks(slug):
        if not profile_path.exists():
            interview_prep.create_profile(
                profile_path,
                cli.default_interview_profile_values(),
            )


def _course_body(title: str, goal: str) -> str:
    return f"""# {title}

## Current Goal

{goal}

## Notes

- Add class notes, links, questions, or source summaries here.

## Session Log

"""


def _creation_metadata(
    *,
    title: str,
    slug: str,
    goal: str,
    mastery_profile: str,
    template_id: str | None,
    template_units: tuple[str, ...],
    submission_id: str | None,
) -> dict[str, object]:
    cli = _cli()
    metadata: dict[str, object] = {
        "topic": title,
        "slug": slug,
        "topic_generation": f"topic_{uuid4().hex}",
        "mastery_profile": mastery_profile,
        "current_focus": "",
        "course_started": False,
        "course_completed": False,
        "level": "beginner",
        "model": cli.configured_model(),
        "created": date.today().isoformat(),
        "last_reviewed": "",
        "goal": goal,
        "known": [],
        "weak_spots": [],
        "review_due": [],
        "course_options": cli.default_course_options(),
        "last_answer_status": "",
        "consecutive_correct": 0,
        "consecutive_misses": 0,
        "last_video_focus": None,
        "quiz_history": [],
        "placement_result": {},
        "review_session_active": False,
    }
    if template_id is not None:
        metadata["template_id"] = template_id
        metadata["template_units"] = list(template_units)
    if submission_id is not None:
        metadata[CREATION_SUBMISSION_METADATA_KEY] = submission_id
    return metadata


def _save_creation_state(slug: str, request: CourseCreationRequest) -> None:
    cli = _cli()
    state = cli.load_state(slug)
    if request.submission_id is not None:
        state[CREATION_SUBMISSION_STATE_KEY] = request.submission_id
    if request.calibration is not None:
        calibration = request.calibration
        state[CALIBRATION_STATE_KEY] = {
            "goal": calibration.goal.strip(),
            "experience": calibration.experience.strip(),
            "skipped": calibration.skipped,
            "recorded_at": calibration.recorded_at or datetime.now(timezone.utc).isoformat(),
        }
    cli.save_state(slug, state)


def create_course(request: CourseCreationRequest) -> CourseCreationResult:
    """Create a durable course without invoking interactive CLI command handlers."""
    _validate_request(request)
    cli = _cli()
    cli.topics_dir().mkdir(parents=True, exist_ok=True)
    creation_lock = cli.topics_dir() / ".course-creation"
    with cli.file_lock(creation_lock):
        if request.submission_id is not None:
            existing = _find_creation_submission(request.submission_id)
            if existing is not None:
                _repair_creation_artifacts(existing, request)
                return CourseCreationResult(course=course_snapshot(existing), created=False)

        template = (
            load_course_template(request.template_id) if request.template_id is not None else None
        )
        slug = available_course_slug(request.name)
        title = request.name
        goal = request.goal.strip() or (template.goal if template is not None else "")
        metadata = _creation_metadata(
            title=title,
            slug=slug,
            goal=goal,
            mastery_profile=request.mastery_profile,
            template_id=template.slug if template is not None else None,
            template_units=template.units if template is not None else (),
            submission_id=request.submission_id,
        )
        path = cli.topic_path(slug)
        with cli.topic_store_locks(slug, include_journal=True):
            if path.exists():
                raise cli.OpenLearnError(f"topic already exists: {slug}")
            cli.write_topic(path, metadata, _course_body(title, goal))
            _save_creation_state(slug, request)
            if template is not None and template.entry_mode == "interview_prep":
                interview_prep.create_profile(
                    cli.interview_profile_path(slug),
                    cli.default_interview_profile_values(),
                )

        return CourseCreationResult(course=course_snapshot(slug), created=True)
