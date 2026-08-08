from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openlearn import interview_prep


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


class InterviewPrepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "algorithms.interview.json"
        self.events: list[tuple[str, dict[str, object]]] = []
        self.evidence_index = 0
        self.profile = {
            "role_family": "backend",
            "target_level": "senior",
            "interview_date": "2026-10-01",
            "coding_language": "python",
            "weekly_minutes": 180,
            "session_minutes": 45,
            "data_structures_experience": "intermediate",
            "algorithms_experience": "rusty",
            "interview_experience": "limited",
            "target_notes": "General product-company loop",
            "accessibility_preferences": "Prefer text prompts and no timers",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def append(self, event_type: str, data: dict[str, object]) -> None:
        self.events.append((event_type, data))

    def create(self) -> dict[str, object]:
        return interview_prep.create_profile(self.path, self.profile, now=lambda: NOW)

    def start(self) -> dict[str, object]:
        return interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            lifecycle_version=interview_prep.PLACEMENT_V1,
            now=lambda: NOW,
        )

    def record(self, stage: str, response: str) -> dict[str, object]:
        self.evidence_index += 1
        return interview_prep.record_placement_evidence(
            self.path,
            stage,
            response,
            evidence_id=f"evidence_{self.evidence_index:032x}",
            now=lambda: NOW,
        )

    def finish_attempt(self) -> dict[str, object]:
        self.create()
        self.start()
        passing_source = (
            "def first_unique_window(text, width):\n"
            "    if width <= 0 or width > len(text):\n"
            "        return -1\n"
            "    for i in range(len(text) - width + 1):\n"
            "        if len(set(text[i:i + width])) == width:\n"
            "            return i\n"
            "    return -1"
        )
        passing_execution = interview_prep.placement_execution_evidence(
            passing_source,
            outcome="passed",
            tests_passed=True,
            return_code=0,
        )
        responses = {
            "calibration": "I use Python weekly but have not interviewed recently.",
            "clarification": "Can width be zero, and may text contain Unicode?",
            "plan": "Use a sliding window and counts; shrink duplicates.",
            "implementation": passing_execution,
            "tests": passing_execution,
            "complexity": "O(n * width) time and O(width) space.",
            "follow_up": "Track last-seen positions to avoid rebuilding each set.",
        }
        for stage in interview_prep.PLACEMENT_STAGES:
            self.record(stage, responses[stage])
        return interview_prep.load_profile(self.path)

    def test_create_inspect_edit_and_clear_profile(self) -> None:
        created = self.create()

        self.assertEqual(created["profile"]["role_family"], "backend")
        self.assertEqual(created["profile_revision"], 1)
        self.assertEqual(created["placement"]["status"], "not_started")

        edited = interview_prep.edit_profile(
            self.path,
            {"weekly_minutes": 90, "target_level": "staff"},
            self.append,
            now=lambda: NOW,
        )
        self.assertEqual(edited["profile_revision"], 2)
        self.assertEqual(edited["profile"]["weekly_minutes"], 90)
        self.assertIsNone(edited["recommendations"])

        interview_prep.clear_profile(self.path, self.append, now=lambda: NOW)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.events[-1][0], "interview_profile_cleared")

    def test_deferred_placement_can_be_started_later(self) -> None:
        self.create()
        deferred = interview_prep.defer_placement(self.path, self.append, now=lambda: NOW)
        self.assertEqual(deferred["placement"]["status"], "deferred")

        started = self.start()
        self.assertEqual(started["placement"]["status"], "in_progress")
        self.assertEqual(started["placement"]["next_stage"], "calibration")

    def test_new_placement_defaults_to_v3_reasoning_lifecycle(self) -> None:
        created = self.create()

        self.assertEqual(created["placement"]["lifecycle_version"], interview_prep.PLACEMENT_V3)
        self.assertIsNone(created["placement"]["draft"])
        started = interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            now=lambda: NOW,
        )
        self.assertIsNone(interview_prep.placement_feedback(started["placement"]))

        self.assertEqual(started["placement"]["next_stage"], "clarification")
        self.assertEqual(started["placement"]["rubric_version"], interview_prep.PLACEMENT_V3)

    def test_v3_draft_is_durable_bounded_and_does_not_advance(self) -> None:
        self.create()
        started = interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            now=lambda: NOW,
        )

        first = interview_prep.append_placement_draft_line(
            self.path, "clarification", "Can width be zero?", now=lambda: NOW
        )
        second = interview_prep.append_placement_draft_line(
            self.path, "clarification", "Can text contain Unicode?", now=lambda: NOW
        )

        self.assertEqual(started["placement"]["next_stage"], "clarification")
        self.assertEqual(second["placement"]["next_stage"], "clarification")
        self.assertEqual(second["placement"]["evidence_refs"], [])
        self.assertEqual(
            interview_prep.placement_draft(self.path),
            {
                "stage": "clarification",
                "lines": ["Can width be zero?", "Can text contain Unicode?"],
                "updated_at": NOW.isoformat(),
            },
        )
        undone = interview_prep.undo_placement_draft_line(
            self.path, "clarification", now=lambda: NOW
        )
        self.assertEqual(undone["placement"]["draft"]["lines"], ["Can width be zero?"])
        cleared = interview_prep.clear_placement_draft(self.path, "clarification", now=lambda: NOW)
        self.assertIsNone(cleared["placement"]["draft"])
        with self.assertRaisesRegex(ValueError, "non-empty"):
            interview_prep.append_placement_draft_line(
                self.path, "clarification", "   ", now=lambda: NOW
            )
        with self.assertRaisesRegex(ValueError, "too large"):
            interview_prep.append_placement_draft_line(
                self.path, "clarification", "x" * 4_001, now=lambda: NOW
            )
        with self.assertRaisesRegex(ValueError, "expected clarification"):
            interview_prep.append_placement_draft_line(
                self.path, "reasoning", "Use a set.", now=lambda: NOW
            )
        self.assertEqual(first["placement"]["draft"]["stage"], "clarification")

    def test_v3_publish_uses_deterministic_id_and_reconciles_matching_draft(self) -> None:
        self.create()
        started = interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            now=lambda: NOW,
        )
        placement = started["placement"]
        evidence_id = interview_prep.placement_evidence_id(placement, "clarification")
        self.assertEqual(
            evidence_id,
            interview_prep.placement_evidence_id(placement, "clarification"),
        )
        interview_prep.append_placement_draft_line(
            self.path, "clarification", "Can width be zero?", now=lambda: NOW
        )

        published = interview_prep.record_placement_evidence(
            self.path,
            "clarification",
            "Can width be zero?",
            evidence_id=evidence_id,
            now=lambda: NOW,
        )
        stale_projection = json.loads(self.path.read_text(encoding="utf-8"))
        stale_projection["placement"]["draft"] = {
            "stage": "clarification",
            "lines": ["Can width be zero?"],
            "updated_at": NOW.isoformat(),
        }
        self.path.write_text(json.dumps(stale_projection), encoding="utf-8")
        replayed = interview_prep.record_placement_evidence(
            self.path,
            "clarification",
            "Can width be zero?",
            evidence_id=evidence_id,
            now=lambda: NOW,
        )

        self.assertEqual(published, replayed)
        self.assertIsNone(replayed["placement"]["draft"])
        self.assertEqual(len(replayed["placement"]["evidence_refs"]), 1)
        with self.assertRaisesRegex(ValueError, "deterministic"):
            interview_prep.record_placement_evidence(
                self.path,
                "reasoning",
                "Use a set.",
                evidence_id="evidence_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                now=lambda: NOW,
            )

    def test_v3_reasoning_passport_keeps_coding_unobserved_and_grants_no_mastery(
        self,
    ) -> None:
        self.create()
        started = interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            now=lambda: NOW,
        )
        clarification_id = interview_prep.placement_evidence_id(
            started["placement"], "clarification"
        )
        after_clarification = interview_prep.record_placement_evidence(
            self.path,
            "clarification",
            "Can width be zero and can text contain Unicode?",
            evidence_id=clarification_id,
            now=lambda: NOW,
        )
        reasoning_id = interview_prep.placement_evidence_id(
            after_clarification["placement"], "reasoning"
        )
        completed = interview_prep.record_placement_evidence(
            self.path,
            "reasoning",
            (
                "I would use a sliding window with a set, return -1 for empty or "
                "invalid widths, and test duplicates and width one. This takes "
                "O(n) time and O(width) space."
            ),
            evidence_id=reasoning_id,
            now=lambda: NOW,
        )

        result = completed["placement"]["result"]
        self.assertEqual(result["gaps"]["coding_fluency"]["status"], "uncertain")
        self.assertEqual(result["gaps"]["coding_fluency"]["evidence"], [])
        self.assertFalse(result["mastery_update_applied"])
        self.assertEqual(result["patterns_marked_known"], [])
        self.assertEqual(
            result["passport"]["uncertainty_to_verify"],
            "Implement and test a complete solution without autocomplete.",
        )
        self.assertEqual(result["passport"]["first_activity"], "Sliding Window Foundations")
        self.assertNotIn("Can width", json.dumps(result["passport"]))

    def test_v3_feedback_uses_observed_answer_signals_for_constructive_coaching(self) -> None:
        self.create()
        started = interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            now=lambda: NOW,
        )
        clarification_id = interview_prep.placement_evidence_id(
            started["placement"], "clarification"
        )
        after_clarification = interview_prep.record_placement_evidence(
            self.path,
            "clarification",
            "Can width be zero?",
            evidence_id=clarification_id,
            now=lambda: NOW,
        )

        interim = interview_prep.placement_feedback(after_clarification["placement"])

        self.assertEqual(interim["title"], "Good clarification habit")
        self.assertIn("direct clarifying question", interim["strengths"][0])
        weak_clarification = copy.deepcopy(after_clarification["placement"])
        weak_clarification["observations"]["clarification"]["signals"] = []
        weak_feedback = interview_prep.placement_feedback(weak_clarification)
        self.assertEqual(weak_feedback["title"], "Sharpen your clarification")
        self.assertEqual(weak_feedback["strengths"], [])
        reasoning_id = interview_prep.placement_evidence_id(
            after_clarification["placement"], "reasoning"
        )
        completed = interview_prep.record_placement_evidence(
            self.path,
            "reasoning",
            "Use a sliding window with a set and test invalid widths and duplicates.",
            evidence_id=reasoning_id,
            now=lambda: NOW,
        )

        feedback = interview_prep.placement_feedback(completed["placement"])

        self.assertEqual(feedback["title"], "Your reasoning snapshot")
        self.assertIn("Named an approach and data structure", feedback["strengths"])
        self.assertIn("Covered edge cases and expected tests", feedback["strengths"])
        self.assertEqual(
            feedback["improvement"],
            "State the time complexity and explain why the window processes each character only a bounded number of times.",
        )
        self.assertEqual(feedback["next_step"], "Sliding Window Foundations")

        labels = interview_prep.PLACEMENT_V3_SIGNAL_LABELS
        cases = (
            (set(), "Name the data structure and the invariant"),
            (
                {"named_data_structure_or_strategy"},
                "Call out invalid widths, duplicate characters",
            ),
            (
                {
                    "named_data_structure_or_strategy",
                    "covered_edges_and_tests",
                },
                "State the time complexity",
            ),
            (
                {
                    "named_data_structure_or_strategy",
                    "covered_edges_and_tests",
                    "stated_time_complexity",
                },
                "State the space complexity",
            ),
            (
                set(labels),
                "Practice structuring an approach with edge cases and complexity.",
            ),
        )
        for signals, expected_improvement in cases:
            with self.subTest(signals=signals):
                placement = copy.deepcopy(completed["placement"])
                placement["observations"]["reasoning"]["signals"] = list(signals)
                branch_feedback = interview_prep.placement_feedback(placement)
                self.assertIn(expected_improvement, branch_feedback["improvement"])

    def test_v3_rejects_malformed_drafts_and_baseline_but_legacy_reset_survives(
        self,
    ) -> None:
        value = self.create()
        value["placement"]["draft"] = {
            "stage": "reasoning",
            "lines": ["cross-stage draft"],
            "updated_at": NOW.isoformat(),
        }
        self.path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "draft"):
            interview_prep.load_profile(self.path)

        self.path.unlink()
        self.create()
        interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            now=lambda: NOW,
        )
        with self.assertRaisesRegex(ValueError, "legacy"):
            interview_prep.complete_with_baseline(
                self.path,
                evidence_id="evidence_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                reason="baseline",
                now=lambda: NOW,
            )
        discarded = interview_prep.discard_placement(self.path, self.append, now=lambda: NOW)
        self.assertEqual(discarded["placement"]["lifecycle_version"], interview_prep.PLACEMENT_V3)
        self.assertIsNone(discarded["placement"]["draft"])

    def test_interrupted_placement_resumes_without_duplicating_evidence(self) -> None:
        self.create()
        self.start()
        self.record("calibration", "Python, somewhat rusty.")

        resumed = self.start()
        self.assertEqual(resumed["placement"]["next_stage"], "clarification")
        self.assertEqual(len(resumed["placement"]["evidence_refs"]), 1)

        with self.assertRaisesRegex(ValueError, "expected clarification"):
            self.record("plan", "Use a set.")

    def test_compact_v2_records_versions_and_skips_optional_debrief_without_evidence(
        self,
    ) -> None:
        self.create()
        started = interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            lifecycle_version=interview_prep.PLACEMENT_V2,
            now=lambda: NOW,
        )

        placement = started["placement"]
        self.assertEqual(placement["lifecycle_version"], interview_prep.PLACEMENT_V2)
        self.assertEqual(placement["rubric_version"], interview_prep.PLACEMENT_V2)
        self.assertEqual(placement["next_stage"], "conversation")
        self.record("conversation", "I would clarify constraints and use a set window.")
        execution = interview_prep.placement_execution_evidence(
            "def first_unique_window(text, width):\n    return -1",
            outcome="passed",
            tests_passed=True,
            return_code=0,
        )
        self.record("implementation", execution)

        interrupted = interview_prep.load_profile(self.path)
        self.assertEqual(interrupted["placement"]["next_stage"], "debrief")
        self.assertEqual(len(interrupted["placement"]["evidence_refs"]), 2)
        completed = interview_prep.skip_optional_placement_stage(
            self.path, "debrief", now=lambda: NOW
        )
        repeated = interview_prep.skip_optional_placement_stage(
            self.path, "debrief", now=lambda: NOW
        )

        self.assertEqual(completed, repeated)
        self.assertEqual(completed["placement"]["status"], "provisional")
        self.assertNotIn("debrief", completed["placement"]["observations"])
        self.assertEqual(len(completed["placement"]["evidence_refs"]), 2)
        self.assertFalse(completed["placement"]["result"]["mastery_update_applied"])
        self.assertEqual(completed["placement"]["result"]["patterns_marked_known"], [])

    def test_compact_v2_baseline_is_terminal_and_grants_no_mastery(self) -> None:
        self.create()
        interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            lifecycle_version=interview_prep.PLACEMENT_V2,
            now=lambda: NOW,
        )

        value = interview_prep.complete_with_baseline(
            self.path,
            evidence_id="evidence_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            reason="Prefer a lower-demand baseline.",
            now=lambda: NOW,
        )

        self.assertEqual(value["placement"]["status"], "provisional")
        self.assertEqual(value["placement"]["rubric_version"], interview_prep.PLACEMENT_V2)
        self.assertFalse(value["placement"]["result"]["mastery_update_applied"])

    def test_compact_v2_can_complete_with_recorded_debrief(self) -> None:
        self.create()
        interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            lifecycle_version=interview_prep.PLACEMENT_V2,
            now=lambda: NOW,
        )
        self.record("conversation", "I would clarify constraints and use a set window.")
        self.record(
            "implementation",
            interview_prep.placement_execution_evidence(
                "def first_unique_window(text, width):\n    return -1",
                outcome="passed",
                tests_passed=True,
                return_code=0,
            ),
        )

        completed = self.record("debrief", "I would improve the solution with last-seen indices.")

        self.assertEqual(completed["placement"]["status"], "provisional")
        self.assertIn("debrief", completed["placement"]["observations"])
        self.assertEqual(len(completed["placement"]["evidence_refs"]), 3)

    def test_legacy_v1_resume_and_terminal_result_keep_v1_semantics(self) -> None:
        self.create()
        self.start()
        self.record("calibration", "I write Python regularly.")
        legacy = json.loads(self.path.read_text(encoding="utf-8"))
        del legacy["placement"]["lifecycle_version"]
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        before_load = self.path.read_text(encoding="utf-8")

        resumed = interview_prep.load_profile(self.path)

        self.assertEqual(resumed["placement"]["next_stage"], "clarification")
        self.assertNotIn("lifecycle_version", resumed["placement"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), before_load)
        responses = {
            "clarification": "Can width be zero?",
            "plan": "Use a set for each window.",
            "implementation": interview_prep.placement_execution_evidence(
                "def first_unique_window(text, width):\n    return -1",
                outcome="passed",
                tests_passed=True,
                return_code=0,
            ),
            "tests": interview_prep.placement_execution_evidence(
                "def first_unique_window(text, width):\n    return -1",
                outcome="passed",
                tests_passed=True,
                return_code=0,
            ),
            "complexity": "O(n * width) time and O(width) space.",
            "follow_up": "Use last-seen indices for streaming input.",
        }
        for stage in interview_prep.PLACEMENT_V1_STAGES[1:]:
            self.record(stage, responses[stage])
        completed = interview_prep.load_profile(self.path)

        self.assertEqual(completed["placement"]["status"], "provisional")
        self.assertEqual(completed["placement"]["rubric_version"], interview_prep.PLACEMENT_V1)
        self.assertNotIn("lifecycle_version", completed["placement"])
        self.assertIn("tests", completed["placement"]["observations"])
        self.assertEqual(
            completed["placement"]["result"]["gaps"]["prerequisites"]["evidence"],
            ["plan", "implementation"],
        )

    def test_legacy_defer_resolves_versions_without_key_error(self) -> None:
        value = self.create()
        value["placement"] = interview_prep._empty_placement(
            interview_prep.PLACEMENT_V1, include_lifecycle=False
        )
        self.path.write_text(json.dumps(value), encoding="utf-8")

        deferred = interview_prep.defer_placement(self.path, self.append, now=lambda: NOW)

        self.assertEqual(deferred["placement"]["status"], "deferred")
        self.assertEqual(self.events[-1][1]["lifecycle_version"], interview_prep.PLACEMENT_V1)

    def test_legacy_discard_preserves_missing_lifecycle_key(self) -> None:
        value = self.create()
        value = self.start()
        del value["placement"]["lifecycle_version"]
        self.path.write_text(json.dumps(value), encoding="utf-8")

        discarded = interview_prep.discard_placement(self.path, self.append, now=lambda: NOW)

        self.assertEqual(discarded["placement"]["status"], "not_started")
        self.assertNotIn("lifecycle_version", discarded["placement"])
        self.assertEqual(discarded["placement"]["rubric_version"], interview_prep.PLACEMENT_V1)

    def test_unknown_or_mismatched_versions_fail_closed(self) -> None:
        for lifecycle, rubric, message in (
            (None, interview_prep.PLACEMENT_V1, "lifecycle"),
            ([interview_prep.PLACEMENT_V1], interview_prep.PLACEMENT_V1, "lifecycle"),
            ("coding-placement-v99", interview_prep.PLACEMENT_V1, "lifecycle"),
            (interview_prep.PLACEMENT_V2, "coding-placement-v99", "rubric"),
            (interview_prep.PLACEMENT_V2, interview_prep.PLACEMENT_V1, "do not match"),
        ):
            with self.subTest(lifecycle=lifecycle, rubric=rubric):
                value = self.create()
                value["placement"]["lifecycle_version"] = lifecycle
                value["placement"]["rubric_version"] = rubric
                self.path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    interview_prep.load_profile(self.path)
                self.path.unlink()

    def test_interrupted_placement_can_be_explicitly_discarded(self) -> None:
        self.create()
        self.start()
        self.record("calibration", "Some practice.")

        discarded = interview_prep.discard_placement(self.path, self.append, now=lambda: NOW)
        self.assertEqual(discarded["placement"]["status"], "not_started")
        self.assertEqual(discarded["placement"]["evidence_refs"], [])
        discard_events = [
            data for kind, data in self.events if kind == "interview_placement_state_discarded"
        ]
        self.assertEqual(len(discard_events), 1)
        self.assertEqual(len(discard_events[0]["evidence_refs"]), 1)
        self.assertFalse(discard_events[0]["attempt_evidence_deleted"])

    def test_v2_profile_edit_and_discard_resets_preserve_lifecycle(self) -> None:
        self.create()
        interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            lifecycle_version=interview_prep.PLACEMENT_V2,
            now=lambda: NOW,
        )

        edited = interview_prep.edit_profile(
            self.path,
            {"weekly_minutes": 90},
            self.append,
            now=lambda: NOW,
        )

        self.assertEqual(edited["placement"]["lifecycle_version"], interview_prep.PLACEMENT_V2)
        self.assertEqual(edited["placement"]["rubric_version"], interview_prep.PLACEMENT_V2)
        interview_prep.start_placement(
            self.path,
            activity_id="act_0123456789abcdef0123456789abcdef",
            lifecycle_version=interview_prep.PLACEMENT_V2,
            now=lambda: NOW,
        )

        discarded = interview_prep.discard_placement(self.path, self.append, now=lambda: NOW)

        self.assertEqual(discarded["placement"]["lifecycle_version"], interview_prep.PLACEMENT_V2)
        self.assertEqual(discarded["placement"]["rubric_version"], interview_prep.PLACEMENT_V2)

    def test_completed_result_is_provisional_and_time_bounded(self) -> None:
        result = self.finish_attempt()
        placement = result["placement"]
        recommendations = result["recommendations"]

        self.assertEqual(placement["status"], "provisional")
        self.assertEqual(placement["rubric_version"], interview_prep.PLACEMENT_V1)
        self.assertEqual(len(placement["evidence_refs"]), len(interview_prep.PLACEMENT_STAGES))
        self.assertNotIn("responses", placement)
        self.assertTrue(placement["result"]["provisional"])
        self.assertFalse(placement["result"]["mastery_update_applied"])
        self.assertEqual(
            set(placement["result"]["gaps"]),
            {"prerequisites", "coding_fluency", "reasoning", "interview_process"},
        )
        self.assertEqual(recommendations["weekly_minutes"], 180)
        self.assertLessEqual(
            recommendations["sessions_per_week"] * recommendations["session_minutes"], 180
        )
        self.assertTrue(
            all(set(ref) == {"evidence_id", "event_type"} for ref in placement["evidence_refs"])
        )

    def test_problem_bundle_is_runnable_and_covers_boundaries(self) -> None:
        problem = interview_prep.PLACEMENT_PROBLEM

        self.assertEqual(problem["function_name"], "first_unique_window")
        self.assertGreaterEqual(len(problem["examples"]), 2)
        case_names = {case["name"] for case in problem["test_cases"]}
        self.assertEqual(
            case_names,
            {
                "normal_match",
                "no_match",
                "repeated_characters",
                "width_one",
                "nonpositive_width",
                "width_greater_than_text",
                "empty_text",
            },
        )
        namespace: dict[str, object] = {}
        exec(problem["function_stub"], namespace)
        with self.assertRaises(NotImplementedError):
            namespace["first_unique_window"]("abc", 2)

        candidate_namespace: dict[str, object] = {}
        exec(
            "def first_unique_window(text, width):\n"
            "    if width <= 0 or width > len(text):\n"
            "        return -1\n"
            "    for index in range(len(text) - width + 1):\n"
            "        if len(set(text[index:index + width])) == width:\n"
            "            return index\n"
            "    return -1\n",
            candidate_namespace,
        )
        function = candidate_namespace[problem["function_name"]]
        for case in problem["test_cases"]:
            inputs = case["inputs"]
            self.assertEqual(
                function(inputs["text"], inputs["width"]),
                case["expected"],
                case["name"],
            )

    def test_clarification_response_combines_contract_and_examples(self) -> None:
        response = interview_prep.placement_clarification_response(
            "What are the inputs and outputs formatted as? "
            "can you also give me example inputs and outputs"
        )

        self.assertIn("Python string", response)
        self.assertIn("integer", response)
        self.assertIn("zero-based", response)
        self.assertIn("first_unique_window(", response)
        self.assertGreaterEqual(response.count("->"), 2)

    def test_unknown_clarification_receives_complete_contract(self) -> None:
        response = interview_prep.placement_clarification_response(
            "Could you tell me anything else useful before I begin?"
        )

        self.assertIn("Return -1", response)
        self.assertIn("Character comparison", response)
        self.assertGreaterEqual(response.count("->"), 2)

    def test_execution_evidence_requires_exact_versioned_envelope(self) -> None:
        source = "def first_unique_window(text, width):\n    return -1"
        passed = interview_prep.placement_execution_evidence(
            source,
            outcome="passed",
            tests_passed=True,
            return_code=0,
        )
        failed = interview_prep.placement_execution_evidence(
            source,
            outcome="failed",
            tests_passed=False,
            return_code=1,
        )

        plain_implementation = interview_prep._evidence_observation(
            "implementation", source, coding_language="python"
        )
        failed_implementation = interview_prep._evidence_observation(
            "implementation", failed, coding_language="python"
        )
        passed_implementation = interview_prep._evidence_observation(
            "implementation", passed, coding_language="python"
        )
        failed_tests = interview_prep._evidence_observation(
            "tests", failed, coding_language="python"
        )
        passed_tests = interview_prep._evidence_observation(
            "tests", passed, coding_language="python"
        )

        self.assertEqual(plain_implementation["status"], "not_observed")
        self.assertEqual(failed_implementation["status"], "not_observed")
        self.assertEqual(failed_tests["status"], "not_observed")
        self.assertEqual(passed_implementation["status"], "observed")
        self.assertEqual(passed_tests["status"], "observed")
        self.assertIn("execution_passed", passed_implementation["signals"])

        malformed = json.loads(passed)
        malformed["extra"] = True
        self.assertEqual(
            interview_prep._evidence_observation(
                "tests", json.dumps(malformed), coding_language="python"
            )["status"],
            "not_observed",
        )

    def test_test_prose_and_skips_do_not_become_observed_execution(self) -> None:
        prose = interview_prep._evidence_observation(
            "tests",
            "I would test empty and edge cases with an assert.",
            coding_language="python",
        )
        skipped = interview_prep._evidence_observation(
            "implementation",
            "Learner skipped implementation.",
            coding_language="python",
        )
        unsupported = interview_prep._evidence_observation(
            "implementation",
            "function first_unique_window(text, width) { return -1; }",
            coding_language="javascript",
        )

        self.assertEqual(prose["status"], "not_observed")
        self.assertEqual(skipped["status"], "uncertain")
        self.assertEqual(unsupported["status"], "uncertain")

    def test_strategy_signals_require_complete_words(self) -> None:
        false_positive = interview_prep._evidence_observation(
            "reasoning",
            "I would offset the latest pointer, test edges, use O(n) time and O(1) space.",
            coding_language="python",
            rubric_version=interview_prep.PLACEMENT_V3,
        )
        positive = interview_prep._evidence_observation(
            "reasoning",
            "I would use a hashmap window, test edges, use O(n) time and O(1) space.",
            coding_language="python",
            rubric_version=interview_prep.PLACEMENT_V3,
        )

        self.assertNotIn(
            "named_data_structure_or_strategy", false_positive["signals"]
        )
        self.assertEqual(false_positive["status"], "not_observed")
        self.assertIn("named_data_structure_or_strategy", positive["signals"])
        self.assertEqual(positive["status"], "observed")

    def test_v3_reasoning_signals_require_substantive_specific_evidence(self) -> None:
        cases = (
            (
                "Return the first index and test empty input.",
                {"covered_edges_and_tests"},
            ),
            ("A window test with O(n) space.", {"stated_space_complexity"}),
            (
                "Use a sliding window with a set. Test invalid widths. "
                "O(n) time and O(width) space.",
                set(interview_prep.PLACEMENT_V3_SIGNAL_LABELS),
            ),
            (
                "Use a sliding window and handle duplicate input. "
                "Linear time with constant extra space.",
                set(interview_prep.PLACEMENT_V3_SIGNAL_LABELS),
            ),
        )
        for response, expected in cases:
            with self.subTest(response=response):
                observation = interview_prep._evidence_observation(
                    "reasoning",
                    response,
                    coding_language="python",
                    rubric_version=interview_prep.PLACEMENT_V3,
                )
                self.assertEqual(set(observation["signals"]), expected)

        vague = interview_prep._evidence_observation(
            "clarification",
            "What?",
            coding_language="python",
            rubric_version=interview_prep.PLACEMENT_V3,
        )
        specific = interview_prep._evidence_observation(
            "clarification",
            "Should width be zero?",
            coding_language="python",
            rubric_version=interview_prep.PLACEMENT_V3,
        )
        self.assertNotIn("asked_clarifying_question", vague["signals"])
        self.assertIn("asked_clarifying_question", specific["signals"])

    def test_practice_schedule_preserves_non_divisible_weekly_budget(self) -> None:
        scheduled, session, sessions = interview_prep.practice_schedule(
            {"weekly_minutes": 120, "session_minutes": 45}
        )

        self.assertEqual((scheduled, session, sessions), (120, 40, 3))
        self.profile["weekly_minutes"] = 120
        self.profile["session_minutes"] = 45
        recommendations = self.finish_attempt()["recommendations"]
        self.assertEqual(recommendations["weekly_minutes"], 120)
        self.assertEqual(recommendations["session_minutes"], 40)
        self.assertEqual(recommendations["sessions_per_week"], 3)

    def test_repeated_and_stale_placement_keep_prior_attempt_evidence(self) -> None:
        first = self.finish_attempt()
        first_attempt = first["placement"]["attempt_id"]
        stale = interview_prep.refresh_staleness(
            self.path, now=lambda: NOW + timedelta(days=interview_prep.STALE_AFTER_DAYS + 1)
        )
        self.assertEqual(stale["placement"]["status"], "stale")

        second = interview_prep.start_placement(
            self.path,
            activity_id="act_1123456789abcdef0123456789abcdef",
            now=lambda: NOW + timedelta(days=interview_prep.STALE_AFTER_DAYS + 1),
        )
        self.assertNotEqual(second["placement"]["attempt_id"], first_attempt)

    def test_weak_or_skipped_evidence_distinguishes_gap_axes(self) -> None:
        self.create()
        self.start()
        for stage in interview_prep.PLACEMENT_STAGES:
            self.record(
                stage,
                f"Learner chose a less demanding baseline and skipped {stage}.",
            )

        result = interview_prep.load_profile(self.path)["placement"]["result"]
        self.assertEqual(result["starting_level"], "uncertain-baseline")
        self.assertEqual(
            {axis: detail["status"] for axis, detail in result["gaps"].items()},
            {
                "prerequisites": "uncertain",
                "coding_fluency": "uncertain",
                "reasoning": "uncertain",
                "interview_process": "uncertain",
            },
        )

    def test_explicit_inability_and_prose_code_are_not_observed(self) -> None:
        self.create()
        self.start()
        responses = {
            "calibration": "I have not practiced interviews.",
            "clarification": "Learner skipped clarification; evidence remains uncertain.",
            "plan": "I can't explain how a set or window would work.",
            "implementation": "I would def a function and return an index.",
            "tests": "I do not know how to test an empty or duplicate input.",
            "complexity": "I cannot explain O(n) complexity.",
            "follow_up": "I am unable to answer the follow up.",
        }
        for stage in interview_prep.PLACEMENT_STAGES:
            self.record(stage, responses[stage])

        placement = interview_prep.load_profile(self.path)["placement"]
        observations = placement["observations"]
        self.assertEqual(observations["clarification"]["status"], "uncertain")
        self.assertEqual(observations["plan"]["status"], "not_observed")
        self.assertEqual(observations["implementation"]["status"], "not_observed")
        self.assertEqual(observations["tests"]["status"], "not_observed")
        self.assertEqual(placement["result"]["gaps"]["coding_fluency"]["status"], "not_observed")

    def test_learner_selected_baseline_ends_with_uncertainty(self) -> None:
        self.create()
        self.start()
        value = interview_prep.complete_with_baseline(
            self.path,
            evidence_id="evidence_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            reason="Prefer a lower-demand baseline today.",
            now=lambda: NOW,
        )

        self.assertEqual(value["placement"]["status"], "provisional")
        self.assertEqual(
            value["placement"]["result"]["starting_level"],
            "learner-selected-baseline",
        )
        self.assertTrue(
            all(
                detail["status"] == "uncertain"
                for detail in value["placement"]["result"]["gaps"].values()
            )
        )

    def test_recommendations_follow_axes_target_and_horizon(self) -> None:
        self.profile["interview_date"] = "2026-08-01"
        self.create()
        self.start()
        for stage in interview_prep.PLACEMENT_STAGES:
            self.record(stage, f"I do not know how to answer {stage}.")
        value = interview_prep.load_profile(self.path)

        recommendations = value["recommendations"]
        self.assertEqual(recommendations["horizon"], "urgent")
        self.assertIn("backend", recommendations["target"])
        self.assertIn("interview format", recommendations["priorities"][0])
        self.assertLessEqual(
            recommendations["sessions_per_week"] * recommendations["session_minutes"],
            self.profile["weekly_minutes"],
        )

    def test_malformed_persisted_placement_and_recommendations_are_rejected(self) -> None:
        value = self.create()
        value["placement"]["evidence_refs"] = [{"evidence_id": "../transcript"}]
        self.path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "evidence reference"):
            interview_prep.load_profile(self.path)

        self.path.unlink()
        value = self.create()
        value["recommendations"] = {"priorities": "not-a-list"}
        self.path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "recommendations"):
            interview_prep.load_profile(self.path)

    def test_profile_edit_invalidates_recommendations_without_deleting_attempts(self) -> None:
        completed = self.finish_attempt()
        refs = list(completed["placement"]["evidence_refs"])
        evidence_events = len(
            [kind for kind, _data in self.events if kind == "interview_placement_evidence"]
        )

        edited = interview_prep.edit_profile(
            self.path,
            {"role_family": "frontend", "weekly_minutes": 60},
            self.append,
            now=lambda: NOW,
        )

        self.assertEqual(edited["placement"]["status"], "stale")
        self.assertEqual(edited["placement"]["evidence_refs"], refs)
        self.assertIsNone(edited["recommendations"])
        self.assertEqual(
            len([kind for kind, _data in self.events if kind == "interview_placement_evidence"]),
            evidence_events,
        )

    def test_saved_profile_is_versioned_and_does_not_use_topic_metadata(self) -> None:
        saved = self.create()
        raw = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(saved["schema_version"], interview_prep.PROFILE_SCHEMA_VERSION)
        self.assertEqual(raw, saved)
        self.assertNotIn("known", json.dumps(saved))
        self.assertNotIn("concept_attempts", json.dumps(saved))


if __name__ == "__main__":
    unittest.main()
