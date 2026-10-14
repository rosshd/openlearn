"""Local web permission for one course's current sources and named provider."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json

from openlearn import cli, config, source_context, source_imports

VERSION = 1
KEY = "source_course_consent"
REVISION_KEY = "source_consent_revision"
DISCLOSURE = (
    "Allow screened excerpts from this course's sources, your lesson answers and questions, "
    "and relevant lesson context to be sent to OpenRouter using deepseek/deepseek-v4.1-flash "
    "throughout this course, including planning, grading and repair calls. "
    "Screening can miss personal information. Remove sensitive details before uploading. "
    "Unrelated goals, profiles, preferences, placement and private notes are excluded. "
    "Responses and source references are saved in local history, which ordinary tutoring may use later. "
    "This permission covers source mode, not an app-wide privacy setting. "
    "You can revoke this permission in Options. Changed sources require approval again."
)


def scope(slug: str) -> str:
    """Bind all eligible files, not a lesson-dependent subset of excerpts."""
    records = source_imports.list_course_sources(slug)
    names = sorted({record.context_file for record in records} | {
        path.name for path in cli.context_source_files(slug)
    })
    topic = cli.read_topic(slug)
    files = []
    remaining = source_context.TOTAL_READ_BYTE_LIMIT
    for name in names[:source_context.FILE_LIMIT]:
        if (source_context._PERSONAL.search(name) or source_context._PRIVATE_ARTIFACT.search(name)
                or source_context._CONTROL.search(name)):
            files.append((name, "excluded"))
            continue
        try:
            _text, checksum, size = source_context._read(topic, name, remaining)
            remaining -= size
        except (OSError, UnicodeError, ValueError, cli.OpenLearnError):
            checksum = "unavailable"
        files.append((name, checksum))
    internal = cli.load_state(slug).get("_openlearn_internal", {})
    generation = internal.get(REVISION_KEY, 0) if isinstance(internal, dict) else 0
    value = [VERSION, slug, source_context.APPROVED_BASE_URL, source_context.APPROVED_MODEL,
             generation, names, files, [asdict(record) for record in records]]
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def recorded(slug: str) -> bool:
    internal = cli.load_state(slug).get("_openlearn_internal", {})
    return isinstance(internal, dict) and isinstance(internal.get(KEY), dict)


def approved(slug: str, current_scope: str | None = None) -> bool:
    if (config.configured_base_url().rstrip("/") != source_context.APPROVED_BASE_URL
            or config.configured_model() != source_context.APPROVED_MODEL):
        return False
    internal = cli.load_state(slug).get("_openlearn_internal", {})
    record = internal.get(KEY) if isinstance(internal, dict) else None
    # Unapproved courses never read source contents merely to check permission.
    return (isinstance(record, dict) and record.get("version") == VERSION
            and record.get("scope") == (current_scope or scope(slug)))


def grant(slug: str, expected_scope: str) -> bool:
    with cli.topic_store_locks(slug, include_journal=True):
        if scope(slug) != expected_scope:
            return False
        def update(state):
            value = state.get("_openlearn_internal")
            internal = dict(value) if isinstance(value, dict) else {}
            internal[KEY] = {"version": VERSION, "scope": expected_scope,
                             "approved_at": datetime.now(timezone.utc).isoformat()}
            state["_openlearn_internal"] = internal
        cli.update_state_atomic(slug, update)
    return True


def revoke(slug: str) -> None:
    def update(state):
        value = state.get("_openlearn_internal")
        internal = dict(value) if isinstance(value, dict) else {}
        internal.pop(KEY, None)
        revision = internal.get(REVISION_KEY)
        internal[REVISION_KEY] = (revision if type(revision) is int else 0) + 1
        state["_openlearn_internal"] = internal
    cli.update_state_atomic(slug, update)
