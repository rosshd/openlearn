"""Built-in coding activity adapter.

This module owns coding-specific request and evidence fields. The generic tutor
and activity lifecycle only retain the namespaced payload and opaque evidence IDs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from openlearn.activities import ActivityContractError

ACTION_MARKER_PATTERN = re.compile(
    r"(?is)<!--\s*openlearn-action\s*:\s*(.*?)\s*-->"
)
DRILL_ACTION_FIELDS = {
    "action",
    "objective",
    "title",
    "language",
    "difficulty",
    "scaffolding_level",
    "purpose",
    "source",
    "plan_prompt",
    "todo_steps",
    "worked_example",
    "hints",
    "reflection_prompt",
    "transfer_prompt",
    "drill",
}
DRILL_SPEC_FIELDS = {"title", "description", "function_stub", "test_cases"}
SOURCE_FIELDS = {
    "generated": {"kind", "name"},
    "curated": {"kind", "name", "license"},
    "licensed": {"kind", "name", "license"},
    "official_link": {"kind", "name", "uri"},
}
MAX_ACTION_TEXT = 1_000
MAX_HINTS = 3
MAX_TODO_STEPS = 4
MAX_TRACE_STEPS = 6


@dataclass(frozen=True)
class CodingDrillAction:
    """One fully validated semantic tutor action with no executable/path fields."""

    objective: str
    title: str
    language: str
    difficulty: int
    scaffolding_level: int
    purpose: str
    source: dict[str, str]
    plan_prompt: str
    todo_steps: tuple[str, ...]
    worked_example: dict[str, object] | None
    hints: tuple[str, ...]
    reflection_prompt: str
    transfer_prompt: str
    drill: dict[str, object]


def extract_coding_drill_action(text: str) -> tuple[str, CodingDrillAction | None]:
    """Remove and decode at most one hidden tutor action marker."""
    matches = list(ACTION_MARKER_PATTERN.finditer(text))
    if not matches:
        return text, None
    if len(matches) != 1:
        raise ActivityContractError("tutor response must contain at most one activity action")
    marker = matches[0]
    try:
        payload = json.loads(marker.group(1))
    except json.JSONDecodeError as exc:
        raise ActivityContractError("tutor coding drill action is malformed JSON") from exc
    if not isinstance(payload, Mapping):
        raise ActivityContractError("tutor coding drill action must be an object")
    action = parse_coding_drill_action(payload)
    visible = (text[: marker.start()] + text[marker.end() :]).strip()
    return visible, action


def suppress_coding_drill_action(text: str) -> str:
    """Remove rejected hidden action metadata while retaining learner-facing text."""
    return ACTION_MARKER_PATTERN.sub("", text).strip()


def parse_coding_drill_action(payload: Mapping[str, object]) -> CodingDrillAction:
    """Validate the allow-listed `start_coding_drill` action schema."""
    if set(payload) != DRILL_ACTION_FIELDS:
        raise ActivityContractError("tutor coding drill action fields are malformed")
    if payload.get("action") != "start_coding_drill":
        raise ActivityContractError("unknown tutor coding drill action")
    objective = _text(payload.get("objective"), "coding drill objective")
    title = _text(payload.get("title"), "coding drill title", limit=200)
    if payload.get("language") != "python":
        raise ActivityContractError("tutor coding drills currently require language=python")
    difficulty = payload.get("difficulty")
    if not isinstance(difficulty, int) or isinstance(difficulty, bool) or not 1 <= difficulty <= 3:
        raise ActivityContractError("coding drill difficulty must be an integer from 1 to 3")
    scaffolding = payload.get("scaffolding_level")
    if not isinstance(scaffolding, int) or isinstance(scaffolding, bool) or not 0 <= scaffolding <= 3:
        raise ActivityContractError("scaffolding_level must be an integer from 0 to 3")
    purpose = payload.get("purpose")
    if purpose not in {"practice", "mastery_check"}:
        raise ActivityContractError("coding drill purpose must be practice or mastery_check")
    source = _source(payload.get("source"))
    plan_prompt = _optional_text(payload.get("plan_prompt"), "coding drill plan prompt")
    todo_value = payload.get("todo_steps")
    if not isinstance(todo_value, list) or len(todo_value) > MAX_TODO_STEPS:
        raise ActivityContractError(
            f"coding drill todo_steps must contain at most {MAX_TODO_STEPS} items"
        )
    todo_steps = tuple(_line_text(item, "coding drill TODO step") for item in todo_value)
    worked_example = _worked_example(payload.get("worked_example"))
    if scaffolding == 0 and (plan_prompt or todo_steps or worked_example is not None):
        raise ActivityContractError("level 0 scaffolding must be unaided")
    if scaffolding == 1 and (not plan_prompt or todo_steps or worked_example is not None):
        raise ActivityContractError("level 1 scaffolding requires only a plan prompt")
    if scaffolding == 2 and (not plan_prompt or not todo_steps or worked_example is not None):
        raise ActivityContractError(
            "level 2 scaffolding requires a plan prompt and partial TODO steps"
        )
    if scaffolding == 3 and (
        not plan_prompt or not todo_steps or worked_example is None
    ):
        raise ActivityContractError(
            "level 3 scaffolding requires plan, TODO, and worked trace support"
        )
    hints_value = payload.get("hints")
    if not isinstance(hints_value, list) or len(hints_value) > MAX_HINTS:
        raise ActivityContractError(f"coding drill hints must contain at most {MAX_HINTS} items")
    hints = tuple(_text(item, "coding drill hint") for item in hints_value)
    reflection = _text(payload.get("reflection_prompt"), "coding drill reflection prompt")
    transfer = _optional_text(payload.get("transfer_prompt"), "coding drill transfer prompt")
    drill_value = payload.get("drill")
    if not isinstance(drill_value, Mapping) or set(drill_value) != DRILL_SPEC_FIELDS:
        raise ActivityContractError("coding drill specification fields are malformed")
    drill = dict(drill_value)
    try:
        encoded_drill = json.dumps(drill, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ActivityContractError("coding drill specification must contain JSON values") from exc
    if len(encoded_drill.encode("utf-8")) > 16_384:
        raise ActivityContractError("coding drill specification is too large")
    if drill.get("title") != title:
        raise ActivityContractError("coding drill action and specification titles must match")
    for key in ("description", "function_stub"):
        _text(drill.get(key), f"coding drill {key}", limit=4_000)
    cases = drill.get("test_cases")
    if not isinstance(cases, list) or len(cases) > 8:
        raise ActivityContractError("coding drill test_cases must be a bounded list")
    if source["kind"] == "official_link":
        if cases:
            raise ActivityContractError(
                "official-link drills must not reproduce remote problem tests"
            )
    elif not cases:
        raise ActivityContractError("local coding drills require test cases")
    return CodingDrillAction(
        objective=objective,
        title=title,
        language="python",
        difficulty=difficulty,
        scaffolding_level=scaffolding,
        purpose=str(purpose),
        source=source,
        plan_prompt=plan_prompt,
        todo_steps=todo_steps,
        worked_example=worked_example,
        hints=hints,
        reflection_prompt=reflection,
        transfer_prompt=transfer,
        drill=drill,
    )


def _source(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ActivityContractError("coding drill source must be an object")
    kind = value.get("kind")
    if (
        not isinstance(kind, str)
        or kind not in SOURCE_FIELDS
        or set(value) != SOURCE_FIELDS[kind]
    ):
        raise ActivityContractError("coding drill source fields are malformed")
    source = {"kind": str(kind), "name": _text(value.get("name"), "coding drill source name")}
    if kind in {"curated", "licensed"}:
        source["license"] = _text(value.get("license"), "coding drill source license")
    if kind == "official_link":
        uri = _text(value.get("uri"), "coding drill official URI")
        parsed = urlparse(uri)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"leetcode.com", "www.leetcode.com"}
            or not parsed.path.startswith("/problems/")
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ActivityContractError(
                "official-link drills require an official HTTPS LeetCode problem URL"
            )
        source["uri"] = uri
    return source


def _worked_example(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"input", "trace", "result"}:
        raise ActivityContractError("coding drill worked_example fields are malformed")
    trace = value.get("trace")
    if not isinstance(trace, list) or not 1 <= len(trace) <= MAX_TRACE_STEPS:
        raise ActivityContractError(
            f"coding drill worked trace must contain 1-{MAX_TRACE_STEPS} steps"
        )
    return {
        "input": _line_text(value.get("input"), "coding drill worked input"),
        "trace": [_line_text(item, "coding drill worked trace step") for item in trace],
        "result": _line_text(value.get("result"), "coding drill worked result"),
    }


def _line_text(value: object, label: str) -> str:
    normalized = _text(value, label, limit=200)
    if "\n" in normalized or "\r" in normalized:
        raise ActivityContractError(f"{label} must be one line")
    return normalized


def _text(value: object, label: str, *, limit: int = MAX_ACTION_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActivityContractError(f"{label} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > limit:
        raise ActivityContractError(f"{label} is too long")
    if '"""' in normalized:
        raise ActivityContractError(f"{label} contains an unsafe docstring delimiter")
    return normalized


def _optional_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ActivityContractError(f"{label} must be text")
    normalized = value.strip()
    if len(normalized) > MAX_ACTION_TEXT:
        raise ActivityContractError(f"{label} is too long")
    if '"""' in normalized:
        raise ActivityContractError(f"{label} contains an unsafe docstring delimiter")
    return normalized


class CodingActivityAdapter:
    domain = "coding"
    activity_kinds = {"python_drill", "interview_problem"}
    evidence_kinds = {
        "artifact_snapshot",
        "interview_observation",
        "pytest_result",
    }
    tool_actions = {
        "create_drill_workspace",
        "open_configured_editor",
        "open_official_problem_link",
        "run_drill_tests",
    }
    interview_tool_actions = {
        "create_drill_workspace",
        "open_configured_editor",
        "run_drill_tests",
    }

    def validate_request(self, kind: str, payload: Mapping[str, object]) -> dict[str, object]:
        if kind not in self.activity_kinds:
            raise ActivityContractError(f"unknown coding activity kind: {kind}")
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            raise ActivityContractError("coding activity title must be non-empty bounded text")
        language = payload.get("language")
        if not isinstance(language, str) or not language.strip():
            raise ActivityContractError("coding activity language must be non-empty text")
        if kind == "python_drill" and language != "python":
            raise ActivityContractError("python_drill requires language=python")
        normalized: dict[str, object] = {
            "title": title.strip(),
            "language": "python" if kind == "python_drill" else language.strip(),
        }
        if kind == "interview_problem":
            if language.strip().lower() != "python":
                raise ActivityContractError(
                    "interview_problem rubric currently requires language=python"
                )
            problem_id = payload.get("problem_id")
            if (
                not isinstance(problem_id, str)
                or not problem_id.strip()
                or len(problem_id.strip()) > 200
            ):
                raise ActivityContractError(
                    "interview_problem requires a bounded problem_id"
                )
            normalized["problem_id"] = problem_id.strip()
        raw_tools = payload.get("tool_requests", [])
        if not isinstance(raw_tools, list) or len(raw_tools) > 3:
            raise ActivityContractError("coding tool_requests must be a bounded list")
        tools: list[dict[str, object]] = []
        seen_actions: set[str] = set()
        for item in raw_tools:
            if not isinstance(item, Mapping):
                raise ActivityContractError("coding tool request must be an object")
            if kind == "interview_problem" and set(item) != {"action", "payload"}:
                raise ActivityContractError(
                    "interview_problem tool request fields are malformed"
                )
            action = item.get("action")
            allowed_actions = (
                self.interview_tool_actions
                if kind == "interview_problem"
                else self.tool_actions
            )
            if not isinstance(action, str) or action not in allowed_actions:
                raise ActivityContractError(f"unknown coding tool action: {action}")
            if kind == "interview_problem" and action in seen_actions:
                raise ActivityContractError(
                    f"duplicate interview_problem tool action: {action}"
                )
            seen_actions.add(action)
            action_payload = item.get("payload", {})
            if not isinstance(action_payload, Mapping) or action_payload:
                raise ActivityContractError(
                    "built-in coding tool actions do not accept arbitrary payloads"
                )
            tools.append({"action": action, "payload": {}})
        normalized["tool_requests"] = tools
        if kind == "python_drill":
            optional_text = ("plan_prompt", "reflection_prompt", "transfer_prompt")
            for field in optional_text:
                if field in payload:
                    normalized[field] = _optional_text(payload.get(field), f"coding {field}")
            if "difficulty" in payload:
                difficulty = payload.get("difficulty")
                if (
                    not isinstance(difficulty, int)
                    or isinstance(difficulty, bool)
                    or not 1 <= difficulty <= 3
                ):
                    raise ActivityContractError(
                        "coding difficulty must be an integer from 1 to 3"
                    )
                normalized["difficulty"] = difficulty
            if "hints" in payload:
                hints = payload.get("hints")
                if not isinstance(hints, list) or len(hints) > MAX_HINTS:
                    raise ActivityContractError("coding hints must be a bounded list")
                normalized["hints"] = [_text(item, "coding hint") for item in hints]
            if "todo_steps" in payload:
                todo_steps = payload.get("todo_steps")
                if not isinstance(todo_steps, list) or len(todo_steps) > MAX_TODO_STEPS:
                    raise ActivityContractError("coding TODO steps must be a bounded list")
                normalized["todo_steps"] = [
                    _line_text(item, "coding TODO step") for item in todo_steps
                ]
            if "worked_example" in payload:
                normalized["worked_example"] = _worked_example(
                    payload.get("worked_example")
                )
        if "function_name" in payload:
            function_name = payload.get("function_name")
            if not isinstance(function_name, str) or not function_name.isidentifier():
                raise ActivityContractError(
                    "coding function_name must be a Python identifier"
                )
            normalized["function_name"] = function_name
        if "test_cases" in payload:
            test_cases = payload.get("test_cases")
            if not isinstance(test_cases, list) or not 1 <= len(test_cases) <= 64:
                raise ActivityContractError(
                    "coding test_cases must contain 1-64 cases"
                )
            try:
                encoded_cases = json.dumps(
                    test_cases,
                    ensure_ascii=True,
                    allow_nan=False,
                )
            except (TypeError, ValueError) as exc:
                raise ActivityContractError(
                    "coding test_cases must contain JSON values"
                ) from exc
            if len(encoded_cases) > 64_000:
                raise ActivityContractError("coding test_cases are too large")
            for case in test_cases:
                if not isinstance(case, Mapping) or set(case) != {"input", "expected"}:
                    raise ActivityContractError(
                        "each coding test case requires input and expected"
                    )
            normalized["test_cases"] = [dict(case) for case in test_cases]
        if kind == "interview_problem" and tools and (
            "function_name" not in normalized or "test_cases" not in normalized
        ):
            raise ActivityContractError(
                "executable interview_problem requires function_name and test_cases"
            )
        return normalized

    def validate_evidence(self, kind: str, payload: Mapping[str, object]) -> dict[str, object]:
        if kind not in self.evidence_kinds:
            raise ActivityContractError(f"unknown coding evidence kind: {kind}")
        if kind == "interview_observation":
            stage = payload.get("stage")
            allowed_stages = {
                "calibration",
                "clarification",
                "plan",
                "implementation",
                "tests",
                "complexity",
                "follow_up",
                "conversation",
                "debrief",
                "reasoning",
                "baseline",
            }
            if stage not in allowed_stages:
                raise ActivityContractError("invalid interview observation stage")
            response = payload.get("response")
            if not isinstance(response, str) or not response.strip():
                raise ActivityContractError(
                    "interview observation response must be non-empty"
                )
            normalized = response.strip()
            if len(normalized) > 40_000:
                raise ActivityContractError("interview observation response is too long")
            return {"stage": stage, "response": normalized}
        if kind == "artifact_snapshot":
            if set(payload) != {"artifact_excerpt", "attempt_number"}:
                raise ActivityContractError("artifact snapshot evidence fields are malformed")
            artifact = payload.get("artifact_excerpt")
            attempt = payload.get("attempt_number")
            if not isinstance(artifact, str) or len(artifact) > 8_000:
                raise ActivityContractError("artifact snapshot is too long")
            if not isinstance(attempt, int) or isinstance(attempt, bool) or not 1 <= attempt <= 64:
                raise ActivityContractError("artifact snapshot attempt_number is invalid")
            return {"artifact_excerpt": artifact, "attempt_number": attempt}
        return_code = payload.get("return_code")
        if not isinstance(return_code, int) or isinstance(return_code, bool):
            raise ActivityContractError("pytest evidence return_code must be an integer")
        summary = payload.get("summary")
        if not isinstance(summary, str):
            raise ActivityContractError("pytest evidence summary must be text")
        normalized = summary.strip()
        if len(normalized) > 4_000:
            raise ActivityContractError("pytest evidence summary is too long")
        evidence: dict[str, object] = {"return_code": return_code, "summary": normalized}
        optional_fields = {
            "artifact_excerpt",
            "attempt_number",
            "hint_stage",
            "tests_passed",
        }
        unexpected = set(payload) - {"return_code", "summary"} - optional_fields
        if unexpected:
            raise ActivityContractError("pytest evidence fields are malformed")
        if "artifact_excerpt" in payload:
            artifact = payload.get("artifact_excerpt")
            if not isinstance(artifact, str) or len(artifact) > 8_000:
                raise ActivityContractError("pytest evidence artifact_excerpt is too long")
            evidence["artifact_excerpt"] = artifact
        if "attempt_number" in payload:
            attempt = payload.get("attempt_number")
            if not isinstance(attempt, int) or isinstance(attempt, bool) or not 1 <= attempt <= 64:
                raise ActivityContractError("pytest evidence attempt_number is invalid")
            evidence["attempt_number"] = attempt
        if "hint_stage" in payload:
            hint_stage = payload.get("hint_stage")
            if (
                not isinstance(hint_stage, int)
                or isinstance(hint_stage, bool)
                or not 0 <= hint_stage <= MAX_HINTS
            ):
                raise ActivityContractError("pytest evidence hint_stage is invalid")
            evidence["hint_stage"] = hint_stage
        if "tests_passed" in payload:
            if not isinstance(payload.get("tests_passed"), bool):
                raise ActivityContractError("pytest evidence tests_passed must be boolean")
            evidence["tests_passed"] = payload["tests_passed"]
        return evidence
