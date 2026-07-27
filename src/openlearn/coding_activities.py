"""Built-in coding activity adapter.

This module owns coding-specific request and evidence fields. The generic tutor
and activity lifecycle only retain the namespaced payload and opaque evidence IDs.
"""

from __future__ import annotations

from collections.abc import Mapping

from openlearn.activities import ActivityContractError


class CodingActivityAdapter:
    domain = "coding"
    activity_kinds = {"python_drill", "interview_problem"}
    evidence_kinds = {"pytest_result", "interview_observation"}
    tool_actions = {"create_drill_workspace", "open_configured_editor", "run_drill_tests"}

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
        if kind == "interview_problem":
            problem_id = payload.get("problem_id")
            if (
                not isinstance(problem_id, str)
                or not problem_id.strip()
                or len(problem_id.strip()) > 200
            ):
                raise ActivityContractError(
                    "interview_problem requires a bounded problem_id"
                )
            if payload.get("tool_requests", []) != []:
                raise ActivityContractError(
                    "interview_problem does not permit tool requests"
                )
            return {
                "title": title.strip(),
                "language": language.strip(),
                "problem_id": problem_id.strip(),
                "tool_requests": [],
            }
        raw_tools = payload.get("tool_requests", [])
        if not isinstance(raw_tools, list) or len(raw_tools) > 3:
            raise ActivityContractError("coding tool_requests must be a bounded list")
        tools: list[dict[str, object]] = []
        for item in raw_tools:
            if not isinstance(item, Mapping):
                raise ActivityContractError("coding tool request must be an object")
            action = item.get("action")
            if action not in self.tool_actions:
                raise ActivityContractError(f"unknown coding tool action: {action}")
            action_payload = item.get("payload", {})
            if not isinstance(action_payload, Mapping) or action_payload:
                raise ActivityContractError(
                    "built-in coding tool actions do not accept arbitrary payloads"
                )
            tools.append({"action": action, "payload": {}})
        return {
            "title": title.strip(),
            "language": "python",
            "tool_requests": tools,
        }

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
        return_code = payload.get("return_code")
        if not isinstance(return_code, int) or isinstance(return_code, bool):
            raise ActivityContractError("pytest evidence return_code must be an integer")
        summary = payload.get("summary")
        if not isinstance(summary, str):
            raise ActivityContractError("pytest evidence summary must be text")
        normalized = summary.strip()
        if len(normalized) > 4_000:
            raise ActivityContractError("pytest evidence summary is too long")
        return {"return_code": return_code, "summary": normalized}
