"""Built-in coding activity adapter.

This module owns coding-specific request and evidence fields. The generic tutor
and activity lifecycle only retain the namespaced payload and opaque evidence IDs.
"""

from __future__ import annotations

from collections.abc import Mapping

from openlearn.activities import ActivityContractError


class CodingActivityAdapter:
    domain = "coding"
    activity_kinds = {"python_drill"}
    evidence_kinds = {"pytest_result"}
    tool_actions = {"create_drill_workspace", "open_configured_editor", "run_drill_tests"}

    def validate_request(self, kind: str, payload: Mapping[str, object]) -> dict[str, object]:
        if kind not in self.activity_kinds:
            raise ActivityContractError(f"unknown coding activity kind: {kind}")
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
            raise ActivityContractError("coding activity title must be non-empty bounded text")
        language = payload.get("language")
        if language != "python":
            raise ActivityContractError("python_drill requires language=python")
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
