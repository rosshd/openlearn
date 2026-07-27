from __future__ import annotations

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
from openlearn.coding_activities import CodingActivityAdapter


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

    def test_interview_placement_uses_validated_lifecycle_and_evidence(self) -> None:
        activity = propose_activity(
            {
                "domain": "coding",
                "kind": "interview_problem",
                "objective": "Calibrate coding interview readiness.",
                "concept_ids": ["coding_interview_baseline"],
                "requested_evidence": ["interview_observation"],
                "scaffolding_level": 0,
                "purpose": "placement",
                "domain_payload": {
                    "title": "First unique window",
                    "language": "python",
                    "problem_id": "first_unique_window_v1",
                    "tool_requests": [],
                },
                "resources": [
                    {
                        "resource_id": "first_unique_window_v1",
                        "source": "openLearn original problem bank",
                        "license": "AGPL-3.0-or-later",
                    }
                ],
            },
            self.registry,
            activity_id=FIXED_ID,
        )
        activity, _ = accept_activity(activity, learner_confirmed=True)
        activity, _ = transition_activity(activity, "active")
        evidence = self.registry.adapter_for("coding").validate_evidence(
            "interview_observation",
            {"stage": "implementation", "response": "def solve():\n    return -1"},
        )
        activity, _ = attach_evidence_reference(activity, "evidence_abc123")
        activity, _ = transition_activity(activity, "completed")

        self.assertEqual(activity["purpose"], "placement")
        self.assertEqual(evidence["stage"], "implementation")
        self.assertEqual(activity["status"], "completed")
        self.assertNotIn("mastery", activity)

    def test_interview_activity_rejects_tools_and_malformed_evidence(self) -> None:
        adapter = self.registry.adapter_for("coding")
        with self.assertRaisesRegex(ActivityContractError, "does not permit tool"):
            adapter.validate_request(
                "interview_problem",
                {
                    "title": "Unsafe",
                    "language": "python",
                    "problem_id": "unsafe",
                    "tool_requests": [
                        {"action": "run_drill_tests", "payload": {}}
                    ],
                },
            )
        with self.assertRaisesRegex(ActivityContractError, "invalid interview"):
            adapter.validate_evidence(
                "interview_observation",
                {"stage": "system_design", "response": "Try a cache."},
            )
        with self.assertRaisesRegex(ActivityContractError, "requires language=python"):
            adapter.validate_request(
                "interview_problem",
                {
                    "title": "Unsupported rubric",
                    "language": "javascript",
                    "problem_id": "first_unique_window_v1",
                    "tool_requests": [],
                },
            )

    def test_placement_purpose_remains_domain_neutral(self) -> None:
        activity = propose_activity(
            {
                "domain": "instrument",
                "kind": "timed_repetition",
                "objective": "Calibrate a starting tempo.",
                "concept_ids": ["steady_tempo"],
                "requested_evidence": ["timer_result"],
                "scaffolding_level": 0,
                "purpose": "placement",
                "domain_payload": {"duration_seconds": 30, "tempo_bpm": 60},
            },
            self.registry,
            activity_id=FIXED_ID,
        )

        self.assertEqual(activity["domain"], "instrument")
        self.assertEqual(activity["purpose"], "placement")


if __name__ == "__main__":
    unittest.main()
