from __future__ import annotations

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

    def finish_attempt(self) -> dict[str, object]:
        self.create()
        interview_prep.start_placement(self.path, self.append, now=lambda: NOW)
        responses = {
            "calibration": "I use Python weekly but have not interviewed recently.",
            "clarification": "Can width be zero, and may text contain Unicode?",
            "plan": "Use a sliding window and counts; shrink duplicates.",
            "implementation": (
                "def first_unique_window(text, width):\n"
                "    for i in range(len(text) - width + 1):\n"
                "        if len(set(text[i:i + width])) == width:\n"
                "            return i\n"
                "    return -1"
            ),
            "tests": "empty input, width 1, duplicate windows, no match, Unicode",
            "complexity": "O(n * width) time and O(width) space.",
            "follow_up": "Track last-seen positions to avoid rebuilding each set.",
        }
        for stage in interview_prep.PLACEMENT_STAGES:
            interview_prep.record_placement_evidence(
                self.path, stage, responses[stage], self.append, now=lambda: NOW
            )
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
        deferred = interview_prep.defer_placement(
            self.path, self.append, now=lambda: NOW
        )
        self.assertEqual(deferred["placement"]["status"], "deferred")

        started = interview_prep.start_placement(
            self.path, self.append, now=lambda: NOW
        )
        self.assertEqual(started["placement"]["status"], "in_progress")
        self.assertEqual(started["placement"]["next_stage"], "calibration")

    def test_interrupted_placement_resumes_without_duplicating_evidence(self) -> None:
        self.create()
        interview_prep.start_placement(self.path, self.append, now=lambda: NOW)
        interview_prep.record_placement_evidence(
            self.path, "calibration", "Python, somewhat rusty.", self.append, now=lambda: NOW
        )

        resumed = interview_prep.start_placement(
            self.path, self.append, now=lambda: NOW
        )
        self.assertEqual(resumed["placement"]["next_stage"], "clarification")
        self.assertEqual(len(resumed["placement"]["evidence_refs"]), 1)

        with self.assertRaisesRegex(ValueError, "expected clarification"):
            interview_prep.record_placement_evidence(
                self.path, "plan", "Use a set.", self.append, now=lambda: NOW
            )

    def test_interrupted_placement_can_be_explicitly_discarded(self) -> None:
        self.create()
        interview_prep.start_placement(self.path, self.append, now=lambda: NOW)
        interview_prep.record_placement_evidence(
            self.path, "calibration", "Some practice.", self.append, now=lambda: NOW
        )

        discarded = interview_prep.discard_placement(
            self.path, self.append, now=lambda: NOW
        )
        self.assertEqual(discarded["placement"]["status"], "not_started")
        self.assertEqual(discarded["placement"]["evidence_refs"], [])
        self.assertTrue(any(kind == "interview_placement_discarded" for kind, _ in self.events))
        self.assertTrue(
            any(kind == "interview_placement_evidence" for kind, _ in self.events),
            "discarding state must preserve append-only attempt evidence",
        )

    def test_completed_result_is_provisional_and_time_bounded(self) -> None:
        result = self.finish_attempt()
        placement = result["placement"]
        recommendations = result["recommendations"]

        self.assertEqual(placement["status"], "provisional")
        self.assertEqual(placement["rubric_version"], interview_prep.PLACEMENT_RUBRIC_VERSION)
        self.assertEqual(len(placement["evidence_refs"]), len(interview_prep.PLACEMENT_STAGES))
        self.assertNotIn("responses", placement)
        self.assertTrue(placement["result"]["provisional"])
        self.assertFalse(placement["result"]["mastery_update_applied"])
        self.assertEqual(
            set(placement["result"]["gaps"]),
            {"prerequisites", "coding_fluency", "reasoning", "interview_process"},
        )
        self.assertLessEqual(recommendations["weekly_minutes"], 180)
        self.assertLessEqual(
            recommendations["sessions_per_week"] * recommendations["session_minutes"], 180
        )
        evidence_events = [data for kind, data in self.events if kind == "interview_placement_evidence"]
        self.assertTrue(any(data["evidence_kind"] == "implementation" for data in evidence_events))
        self.assertTrue(any(data["evidence_kind"] == "complexity" for data in evidence_events))
        self.assertTrue(all(data["mastery_update_applied"] is False for data in evidence_events))

    def test_repeated_and_stale_placement_keep_prior_attempt_evidence(self) -> None:
        first = self.finish_attempt()
        first_attempt = first["placement"]["attempt_id"]
        event_count = len(self.events)

        stale = interview_prep.refresh_staleness(
            self.path, now=lambda: NOW + timedelta(days=interview_prep.STALE_AFTER_DAYS + 1)
        )
        self.assertEqual(stale["placement"]["status"], "stale")

        second = interview_prep.start_placement(
            self.path,
            self.append,
            now=lambda: NOW + timedelta(days=interview_prep.STALE_AFTER_DAYS + 1),
        )
        self.assertNotEqual(second["placement"]["attempt_id"], first_attempt)
        self.assertGreater(len(self.events), event_count)

    def test_weak_or_skipped_evidence_distinguishes_gap_axes(self) -> None:
        self.create()
        interview_prep.start_placement(self.path, self.append, now=lambda: NOW)
        for stage in interview_prep.PLACEMENT_STAGES:
            interview_prep.record_placement_evidence(
                self.path,
                stage,
                f"Learner chose a less demanding baseline and skipped {stage}.",
                self.append,
                now=lambda: NOW,
            )

        result = interview_prep.load_profile(self.path)["placement"]["result"]
        self.assertEqual(result["starting_level"], "foundational")
        self.assertEqual(
            {axis: detail["status"] for axis, detail in result["gaps"].items()},
            {
                "prerequisites": "likely_gap",
                "coding_fluency": "likely_gap",
                "reasoning": "likely_gap",
                "interview_process": "likely_gap",
            },
        )

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
