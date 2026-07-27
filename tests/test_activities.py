from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from datetime import datetime, timezone

from openlearn.activities import (
    ActivityContractError,
    ActivityRegistry,
    accept_activity,
    activity_event_data,
    attach_evidence_reference,
    propose_activity,
    transition_activity,
)
from openlearn.coding_activities import (
    CodingActivityAdapter,
    CodingDrillAction,
    extract_coding_drill_action,
)


FIXED_TIME = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
FIXED_ID = "act_0123456789abcdef0123456789abcdef"


class FixtureAdapter:
    def __init__(self, domain: str, kinds: set[str], evidence: set[str]) -> None:
        self.domain = domain
        self.kinds = kinds
        self.evidence = evidence

    def validate_request(self, kind: str, payload: Mapping[str, object]) -> dict[str, object]:
        if kind not in self.kinds:
            raise ActivityContractError(f"unknown {self.domain} activity kind: {kind}")
        return dict(payload)

    def validate_evidence(self, kind: str, payload: Mapping[str, object]) -> dict[str, object]:
        if kind not in self.evidence:
            raise ActivityContractError(f"unknown {self.domain} evidence kind: {kind}")
        return dict(payload)


class ActivityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ActivityRegistry(
            (
                CodingActivityAdapter(),
                FixtureAdapter("instrument", {"timed_repetition"}, {"timer_result", "recording"}),
                FixtureAdapter(
                    "electronics",
                    {"circuit_exercise"},
                    {"diagram", "simulator_result", "measurement"},
                ),
            )
        )

    def proposal(self, **changes: object) -> dict[str, object]:
        request: dict[str, object] = {
            "domain": "coding",
            "kind": "python_drill",
            "objective": "Practice producing a two-sum implementation.",
            "concept_ids": ["hash_map_lookup"],
            "requested_evidence": ["pytest_result"],
            "scaffolding_level": 1,
            "purpose": "practice",
            "domain_payload": {
                "title": "Two Sum",
                "language": "python",
                "tool_requests": [{"action": "create_drill_workspace", "payload": {}}],
            },
            "resources": [
                {
                    "resource_id": "curated_bank",
                    "source": "openlearn built-in drills",
                    "license": "AGPL-3.0-or-later",
                }
            ],
        }
        request.update(changes)
        return propose_activity(
            request,
            self.registry,
            now=lambda: FIXED_TIME,
            activity_id=FIXED_ID,
        )

    def test_coding_proposal_is_versioned_namespaced_and_separates_resources(self) -> None:
        activity = self.proposal()

        self.assertEqual(activity["schema_version"], 1)
        self.assertEqual(activity["status"], "proposed")
        self.assertEqual(activity["purpose"], "practice")
        self.assertEqual(set(activity["domain_payload"]), {"coding"})
        self.assertNotIn("resources", activity["domain_payload"]["coding"])
        self.assertEqual(activity["evidence_refs"], [])

    def test_generic_lifecycle_supports_future_domain_fixtures(self) -> None:
        fixtures = [
            {
                "domain": "instrument",
                "kind": "timed_repetition",
                "objective": "Repeat the bar steadily for two minutes.",
                "concept_ids": ["steady_tempo"],
                "requested_evidence": ["timer_result"],
                "scaffolding_level": 2,
                "purpose": "practice",
                "domain_payload": {"duration_seconds": 120, "tempo_bpm": 80},
            },
            {
                "domain": "electronics",
                "kind": "circuit_exercise",
                "objective": "Predict and verify the divider output.",
                "concept_ids": ["voltage_divider"],
                "requested_evidence": ["measurement"],
                "scaffolding_level": 1,
                "purpose": "mastery_check",
                "domain_payload": {"diagram_ref": "fixture_divider", "unit": "volts"},
            },
        ]

        for index, request in enumerate(fixtures, start=1):
            with self.subTest(domain=request["domain"]):
                activity = propose_activity(
                    request,
                    self.registry,
                    now=lambda: FIXED_TIME,
                    activity_id=f"act_{index:032x}",
                )
                activity, _ = accept_activity(
                    activity, learner_confirmed=True, now=lambda: FIXED_TIME
                )
                activity, _ = transition_activity(activity, "active", now=lambda: FIXED_TIME)
                activity, _ = transition_activity(activity, "completed", now=lambda: FIXED_TIME)
                self.assertEqual(activity["status"], "completed")
                self.assertEqual(activity["purpose"], request["purpose"])

    def test_transition_is_validated_and_idempotent(self) -> None:
        proposed = self.proposal()
        accepted, changed = accept_activity(
            proposed, learner_confirmed=True, now=lambda: FIXED_TIME
        )
        repeated, repeated_changed = accept_activity(
            accepted, learner_confirmed=True, now=lambda: FIXED_TIME
        )

        self.assertTrue(changed)
        self.assertFalse(repeated_changed)
        self.assertEqual(repeated, accepted)
        with self.assertRaisesRegex(ActivityContractError, "invalid activity transition"):
            transition_activity(proposed, "completed")

    def test_cancelled_proposal_has_no_evidence_and_cannot_start(self) -> None:
        cancelled, _ = transition_activity(self.proposal(), "cancelled")

        self.assertEqual(cancelled["evidence_refs"], [])
        with self.assertRaisesRegex(ActivityContractError, "invalid activity transition"):
            transition_activity(cancelled, "active")

    def test_evidence_is_an_opaque_reference_and_does_not_change_purpose(self) -> None:
        activity, _ = accept_activity(self.proposal(), learner_confirmed=True)
        activity, _ = transition_activity(activity, "active")
        updated, changed = attach_evidence_reference(activity, "evidence_abc123")

        self.assertTrue(changed)
        self.assertEqual(updated["purpose"], "practice")
        self.assertEqual(
            updated["evidence_refs"],
            [{"evidence_id": "evidence_abc123", "event_type": "activity_evidence_recorded"}],
        )
        self.assertNotIn("mastery", updated)

    def test_evidence_reference_cap_rejects_the_sixty_fifth_reference(self) -> None:
        activity, _ = accept_activity(self.proposal(), learner_confirmed=True)
        activity, _ = transition_activity(activity, "active")
        activity["evidence_refs"] = [
            {
                "evidence_id": f"evidence_{index:02d}",
                "event_type": "activity_evidence_recorded",
            }
            for index in range(63)
        ]
        at_limit, changed = attach_evidence_reference(activity, "evidence_63")

        self.assertTrue(changed)
        self.assertEqual(len(at_limit["evidence_refs"]), 64)
        with self.assertRaisesRegex(ActivityContractError, "more than 64"):
            attach_evidence_reference(at_limit, "evidence_64")

    def test_lifecycle_event_projection_excludes_domain_payload_and_resources(self) -> None:
        data = activity_event_data(self.proposal())

        self.assertNotIn("domain_payload", data)
        self.assertNotIn("resources", data)
        self.assertEqual(data["activity_id"], FIXED_ID)

    def test_unknown_domain_kind_and_malformed_payload_fail_safely(self) -> None:
        with self.assertRaisesRegex(ActivityContractError, "unknown activity domain"):
            self.proposal(domain="unknown")
        with self.assertRaisesRegex(ActivityContractError, "unknown coding activity kind"):
            self.proposal(kind="shell_command")
        with self.assertRaisesRegex(ActivityContractError, "unknown coding tool action"):
            self.proposal(
                domain_payload={
                    "title": "Unsafe",
                    "language": "python",
                    "tool_requests": [{"action": "run_arbitrary_shell", "payload": {}}],
                }
            )
        with self.assertRaisesRegex(ActivityContractError, "domain_payload must be an object"):
            self.proposal(domain_payload="not-an-object")

    def test_completion_does_not_create_mastery_evidence(self) -> None:
        activity, _ = accept_activity(self.proposal(), learner_confirmed=True)
        activity, _ = transition_activity(activity, "active")
        completed, _ = transition_activity(activity, "completed")

        self.assertNotIn("mastery", completed)
        self.assertEqual(completed["evidence_refs"], [])

    def test_tutor_drill_action_is_narrow_typed_and_stripped_from_visible_text(self) -> None:
        raw = (
            "**Example:**\nA small hash-map drill would make the tradeoff observable.\n"
            '<!-- openlearn-action: {"action":"start_coding_drill",'
            '"objective":"Implement one-pass lookup without nested loops.",'
            '"title":"Pair Lookup","language":"python","difficulty":2,'
            '"scaffolding_level":1,"purpose":"practice",'
            '"source":{"kind":"generated","name":"openLearn original"},'
            '"plan_prompt":"Name the lookup invariant before coding.",'
            '"hints":["What should the map remember?","Check before inserting."],'
            '"reflection_prompt":"Why does lookup order avoid reusing an item?",'
            '"transfer_prompt":"Solve a related complement lookup with a different output.",'
            '"drill":{"title":"Pair Lookup","description":"Return two matching indexes.",'
            '"function_stub":"def pair_lookup(values, target):\\n    pass",'
            '"test_cases":[{"input":[[2,7,11],9],"expected":[0,1]}]}} -->'
        )

        visible, action = extract_coding_drill_action(raw)

        self.assertNotIn("openlearn-action", visible)
        self.assertIsInstance(action, CodingDrillAction)
        assert action is not None
        self.assertEqual(action.purpose, "practice")
        self.assertEqual(action.difficulty, 2)
        self.assertEqual(action.hints[0], "What should the map remember?")

    def test_tutor_drill_action_rejects_malformed_or_unsafe_fields(self) -> None:
        base = {
            "action": "start_coding_drill",
            "objective": "Practice lookup.",
            "title": "Lookup",
            "language": "python",
            "difficulty": 2,
            "scaffolding_level": 0,
            "purpose": "practice",
            "source": {"kind": "generated", "name": "openLearn original"},
            "plan_prompt": "",
            "hints": [],
            "reflection_prompt": "Explain the invariant.",
            "transfer_prompt": "",
            "drill": {
                "title": "Lookup",
                "description": "Return the matching index.",
                "function_stub": "def lookup(values, target):\n    pass",
                "test_cases": [{"input": [[1], 1], "expected": 0}],
            },
        }
        unsafe_variants = [
            {**base, "command": "rm -rf /"},
            {**base, "path": "/tmp/owned"},
            {**base, "language": "bash"},
            {**base, "difficulty": 9},
            {**base, "purpose": "automatic_mastery"},
            {
                **base,
                "drill": {
                    **base["drill"],
                    "description": 'Safe text."""\nprint("unexpected")\n"""',
                },
            },
            {
                **base,
                "source": {
                    "kind": "official_link",
                    "name": "copied problem",
                    "uri": "https://example.com/problem",
                },
            },
        ]

        for payload in unsafe_variants:
            with self.subTest(payload=payload):
                marker = f"<!-- openlearn-action: {json.dumps(payload)} -->"
                with self.assertRaises(ActivityContractError):
                    extract_coding_drill_action(f"**Lesson:**\nOffer.\n{marker}")

    def test_coding_evidence_keeps_attempt_feedback_fields_bounded(self) -> None:
        adapter = CodingActivityAdapter()

        evidence = adapter.validate_evidence(
            "pytest_result",
            {
                "return_code": 1,
                "summary": "FAILED test_case_1",
                "artifact_excerpt": "def solve():\n    return None",
                "attempt_number": 2,
                "hint_stage": 1,
                "tests_passed": False,
            },
        )

        self.assertEqual(evidence["attempt_number"], 2)
        self.assertEqual(evidence["hint_stage"], 1)
        with self.assertRaises(ActivityContractError):
            adapter.validate_evidence(
                "pytest_result",
                {
                    **evidence,
                    "artifact_excerpt": "x" * 8_001,
                },
            )


if __name__ == "__main__":
    unittest.main()
