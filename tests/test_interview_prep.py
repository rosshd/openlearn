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
        deferred = interview_prep.defer_placement(
            self.path, self.append, now=lambda: NOW
        )
        self.assertEqual(deferred["placement"]["status"], "deferred")

        started = self.start()
        self.assertEqual(started["placement"]["status"], "in_progress")
        self.assertEqual(started["placement"]["next_stage"], "calibration")

    def test_interrupted_placement_resumes_without_duplicating_evidence(self) -> None:
        self.create()
        self.start()
        self.record("calibration", "Python, somewhat rusty.")

        resumed = self.start()
        self.assertEqual(resumed["placement"]["next_stage"], "clarification")
        self.assertEqual(len(resumed["placement"]["evidence_refs"]), 1)

        with self.assertRaisesRegex(ValueError, "expected clarification"):
            self.record("plan", "Use a set.")

    def test_interrupted_placement_can_be_explicitly_discarded(self) -> None:
        self.create()
        self.start()
        self.record("calibration", "Some practice.")

        discarded = interview_prep.discard_placement(
            self.path, self.append, now=lambda: NOW
        )
        self.assertEqual(discarded["placement"]["status"], "not_started")
        self.assertEqual(discarded["placement"]["evidence_refs"], [])
        discard_events = [
            data
            for kind, data in self.events
            if kind == "interview_placement_state_discarded"
        ]
        self.assertEqual(len(discard_events), 1)
        self.assertEqual(len(discard_events[0]["evidence_refs"]), 1)
        self.assertFalse(discard_events[0]["attempt_evidence_deleted"])

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
        self.assertTrue(
            all(
                set(ref) == {"evidence_id", "event_type"}
                for ref in placement["evidence_refs"]
            )
        )

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
