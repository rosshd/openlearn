from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from unittest import mock
from uuid import uuid4

from openlearn import application, cli
from openlearn.application import CalibrationContext, CourseCreationRequest
from openlearn.courses import create_course


class CourseServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.previous = {
            name: os.environ.get(name) for name in ("OPENLEARN_HOME", "OPENLEARN_MOCK")
        }
        os.environ["OPENLEARN_HOME"] = self.home.name
        os.environ["OPENLEARN_MOCK"] = "1"
        cli._CONFIG_CACHE = None

    def tearDown(self) -> None:
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        cli._CONFIG_CACHE = None
        self.home.cleanup()

    def test_freeform_creation_uses_deterministic_collision_suffixes(self) -> None:
        first = create_course(CourseCreationRequest(name="Data Science", goal="Learn"))
        second = create_course(CourseCreationRequest(name="Data Science", goal="Learn"))
        third = create_course(CourseCreationRequest(name="Data Science", goal="Learn"))

        self.assertEqual(
            [first.course.slug, second.course.slug, third.course.slug],
            ["data-science", "data-science-2", "data-science-3"],
        )

    def test_recreating_deleted_course_skips_tombstoned_slug(self) -> None:
        request = CourseCreationRequest(
            name="Technical Interview Prep",
            template_id="technical-interview-prep",
        )
        first = create_course(request)
        cli.delete_topic_files(first.course.slug)

        recreated = create_course(request)

        self.assertEqual(recreated.course.slug, "technical-interview-prep-2")
        self.assertEqual(cli.read_topic(recreated.course.slug).slug, recreated.course.slug)
        self.assertTrue(cli.topic_deletion_tombstone_path(first.course.slug).exists())

    def test_repeated_submission_returns_original_course(self) -> None:
        submission_id = str(uuid4())
        request = CourseCreationRequest(
            name="Linux", goal="Learn Linux", submission_id=submission_id
        )

        first = create_course(request)
        replay = create_course(request)

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(replay.course.slug, first.course.slug)
        self.assertEqual(len(list(cli.topics_dir().glob("*.md"))), 1)

    def test_creation_retry_recovers_identity_after_state_write_crash(self) -> None:
        submission_id = str(uuid4())
        calibration = CalibrationContext(
            goal="Prepare for interviews",
            experience="I know basic Python.",
            recorded_at="2026-08-07T12:00:00+00:00",
        )
        request = CourseCreationRequest(
            name="Crash Safe Interview Prep",
            template_id="technical-interview-prep",
            submission_id=submission_id,
            calibration=calibration,
        )
        with mock.patch(
            "openlearn.courses._save_creation_state",
            side_effect=RuntimeError("simulated process failure after topic write"),
        ):
            with self.assertRaises(RuntimeError):
                create_course(request)

        replay = create_course(request)

        self.assertFalse(replay.created)
        self.assertEqual(replay.course.slug, "crash-safe-interview-prep")
        self.assertEqual(len(list(cli.topics_dir().glob("*.md"))), 1)
        state = cli.load_state(replay.course.slug)
        self.assertEqual(
            state["course_creation_submission_id"],
            submission_id,
        )
        self.assertEqual(
            state["progressive_calibration"]["experience"],
            calibration.experience,
        )
        self.assertTrue(cli.interview_profile_path(replay.course.slug).exists())

    def test_creation_identity_backfill_preserves_concurrent_course_state(self) -> None:
        submission_id = str(uuid4())
        request = CourseCreationRequest(
            name="Concurrent Recovery",
            goal="Recover safely",
            submission_id=submission_id,
        )
        with mock.patch(
            "openlearn.courses._save_creation_state",
            side_effect=RuntimeError("simulated process failure after topic write"),
        ):
            with self.assertRaises(RuntimeError):
                create_course(request)

        stale_state_loaded = threading.Event()
        continue_recovery = threading.Event()
        original_load_state = cli.load_state
        paused = False

        def controlled_load_state(slug: str) -> dict[str, object]:
            nonlocal paused
            state = original_load_state(slug)
            if threading.current_thread().name == "course-recovery" and not paused:
                paused = True
                stale_state_loaded.set()
                continue_recovery.wait(timeout=2)
            return state

        recovered: list[object] = []

        def recover_course() -> None:
            recovered.append(create_course(request))

        with mock.patch.object(cli, "load_state", new=controlled_load_state):
            recovery = threading.Thread(target=recover_course, name="course-recovery")
            recovery.start()
            self.assertTrue(stale_state_loaded.wait(timeout=1))
            cli.update_state_atomic(
                "concurrent-recovery",
                lambda state: state.__setitem__("known", ["concurrent-progress"]),
            )
            continue_recovery.set()
            recovery.join(timeout=2)

        self.assertFalse(recovery.is_alive())
        self.assertEqual(len(recovered), 1)
        self.assertEqual(
            cli.load_state("concurrent-recovery")["known"],
            ["concurrent-progress"],
        )

    def test_template_creation_persists_units_and_interview_profile(self) -> None:
        result = create_course(
            CourseCreationRequest(
                name="Interview Prep",
                template_id="technical-interview-prep",
            )
        )

        topic = cli.read_topic(result.course.slug)
        self.assertEqual(topic.metadata["template_id"], "technical-interview-prep")
        self.assertGreater(len(topic.metadata["template_units"]), 0)
        self.assertTrue(cli.interview_profile_path(result.course.slug).exists())

    def test_calibration_is_context_only_and_creates_no_evidence(self) -> None:
        calibration = CalibrationContext(
            goal="Pass interviews",
            experience="I have solved many graph problems",
        )
        result = create_course(
            CourseCreationRequest(
                name="Algorithms",
                goal="Practice algorithms",
                calibration=calibration,
            )
        )

        topic = cli.read_topic(result.course.slug)
        state = cli.load_state(result.course.slug)
        self.assertEqual(topic.metadata["known"], [])
        self.assertEqual(topic.metadata["placement_result"], {})
        self.assertNotIn("concept_attempts", state)
        self.assertEqual(
            state["progressive_calibration"]["experience"],
            calibration.experience,
        )
        self.assertFalse(cli.topic_events_path(result.course.slug).exists())

    def test_reading_existing_course_preserves_interview_profile_bytes(self) -> None:
        result = create_course(
            CourseCreationRequest(
                name="Technical Interview Prep",
                template_id="technical-interview-prep",
            )
        )
        profile = cli.interview_profile_path(result.course.slug)
        value = json.loads(profile.read_text(encoding="utf-8"))
        value["placement"]["status"] = "paused"
        profile.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        before = profile.read_bytes()

        from openlearn.courses import course_snapshot

        snapshot = course_snapshot(result.course.slug)

        self.assertEqual(snapshot.slug, result.course.slug)
        self.assertEqual(profile.read_bytes(), before)

    def _legacy_interview_course(self) -> str:
        result = create_course(
            CourseCreationRequest(
                name="Legacy Interview",
                template_id="technical-interview-prep",
            )
        )
        slug = result.course.slug
        topic = cli.read_topic(slug)
        metadata = dict(topic.metadata)
        metadata["known"] = ["Interview Problem Solving", "Custom legacy exercise"]
        metadata["weak_spots"] = ["Arrays and Hashing"]
        metadata["review_due"] = [
            {
                "concept": "Arrays and Hashing",
                "due": "2026-08-13",
                "difficulty": "hard",
            }
        ]
        metadata["placement_result"] = {"legacy_note": "keep me"}
        cli.write_topic(topic.path, metadata, topic.body + "\nLegacy transcript stays.\n")
        cli.update_state_atomic(
            slug,
            lambda state: state.__setitem__(
                "concept_attempts",
                {
                    "Arrays and Hashing": {
                        "attempts": 2,
                        "correct_sum": 2.0,
                        "last_correct_at": "2026-08-12T10:00:00+00:00",
                    }
                },
            ),
        )
        return slug

    def test_explicit_resume_reconciles_legacy_state_once_and_preserves_history(self) -> None:
        slug = self._legacy_interview_course()
        topic_before = cli.topic_path(slug).read_text(encoding="utf-8")
        _metadata_before, body_before = cli.parse_topic(topic_before)
        profile_before = cli.interview_profile_path(slug).read_bytes()

        first = application.prepare_interview_curriculum(slug, boundary="resume")
        state_bytes = cli.topic_state_path(slug).read_bytes()
        events_bytes = cli.topic_events_path(slug).read_bytes()
        second = application.prepare_interview_curriculum(slug, boundary="resume")

        self.assertEqual(first, second)
        self.assertEqual(state_bytes, cli.topic_state_path(slug).read_bytes())
        self.assertEqual(events_bytes, cli.topic_events_path(slug).read_bytes())
        topic_after = cli.topic_path(slug).read_text(encoding="utf-8")
        _metadata_after, body_after = cli.parse_topic(topic_after)
        self.assertEqual(body_after, body_before)
        self.assertEqual(profile_before, cli.interview_profile_path(slug).read_bytes())
        self.assertIn("Interview Problem Solving", topic_before)
        state = cli.load_state(slug)
        canonical = state["interview_curriculum"]
        self.assertEqual(canonical["bundle_version"], "1.0.0")
        self.assertEqual(first.unit_id, canonical["cursor"]["unit_id"])
        self.assertEqual(first.section_id, canonical["cursor"]["section_id"])
        self.assertEqual(first.skill_id, canonical["cursor"]["skill_ref"]["skill_id"])
        self.assertIn("Interview Problem Solving", canonical["legacy_context"]["unassessed"])
        self.assertIn("Custom legacy exercise", canonical["legacy_context"]["unassessed"])
        self.assertIn("concept.arrays-strings", canonical["evidence"]["ready"])
        self.assertNotIn("concept.arrays-strings", canonical["evidence"]["weak"])
        self.assertIn("Arrays and Hashing", canonical["evidence"]["due_review"])
        self.assertEqual(state["concept_attempts"]["Arrays and Hashing"]["attempts"], 2)
        self.assertEqual(
            cli.read_topic(slug).metadata["placement_result"], {"legacy_note": "keep me"}
        )

    def test_dashboard_does_not_reconcile_legacy_interview_course(self) -> None:
        slug = self._legacy_interview_course()
        watched = [
            cli.topic_path(slug),
            cli.topic_state_path(slug),
            cli.interview_profile_path(slug),
            cli.topic_events_path(slug),
        ]
        before = {path: path.read_bytes() if path.exists() else None for path in watched}

        application.dashboard()

        self.assertEqual(
            before,
            {path: path.read_bytes() if path.exists() else None for path in watched},
        )
        self.assertNotIn("interview_curriculum", cli.load_state(slug))

    def test_reconciliation_recovers_each_publication_boundary_exactly_once(self) -> None:
        for boundary in ("after_journal", "after_state", "after_event", "after_receipt"):
            with self.subTest(boundary=boundary):
                slug = self._legacy_interview_course()
                with mock.patch(
                    "openlearn.courses._reconciliation_checkpoint",
                    side_effect=lambda stage: (
                        (_ for _ in ()).throw(RuntimeError(stage)) if stage == boundary else None
                    ),
                ):
                    with self.assertRaises(RuntimeError):
                        application.prepare_interview_curriculum(slug, boundary="resume")

                application.prepare_interview_curriculum(slug, boundary="resume")
                events = [
                    json.loads(line)
                    for line in cli.topic_events_path(slug).read_text().splitlines()
                    if line
                ]
                reconciled = [
                    event
                    for event in events
                    if event.get("event_type") == "interview_curriculum_reconciled"
                ]
                self.assertEqual(len(reconciled), 1)
                self.assertFalse(cli.interview_reconciliation_journal_path(slug).exists())
                self.assertTrue(cli.interview_reconciliation_receipt_path(slug).exists())

                cli.delete_topic_files(slug)
                self.assertFalse(cli.interview_reconciliation_journal_path(slug).exists())
                self.assertFalse(cli.interview_reconciliation_receipt_path(slug).exists())

    def test_pinned_canonical_state_does_not_rematerialize_against_new_default(self) -> None:
        slug = self._legacy_interview_course()
        first = application.prepare_interview_curriculum(slug, boundary="resume")

        with mock.patch(
            "openlearn.courses._route_for_profile",
            side_effect=AssertionError("pinned course must not consult a newer default"),
        ):
            resumed = application.prepare_interview_curriculum(slug, boundary="resume")

        self.assertEqual(resumed, first)

    def test_journal_recovery_preserves_unrelated_state_written_after_crash(self) -> None:
        slug = self._legacy_interview_course()
        with mock.patch(
            "openlearn.courses._reconciliation_checkpoint",
            side_effect=lambda stage: (
                (_ for _ in ()).throw(RuntimeError(stage))
                if stage == "after_journal"
                else None
            ),
        ):
            with self.assertRaises(RuntimeError):
                application.prepare_interview_curriculum(slug, boundary="resume")
        cli.update_state_atomic(
            slug, lambda state: state.__setitem__("pending_learner_prompt", "keep this")
        )

        application.prepare_interview_curriculum(slug, boundary="resume")

        self.assertEqual(cli.load_state(slug)["pending_learner_prompt"], "keep this")

    def test_receipt_recovers_missing_canonical_state_before_fallback(self) -> None:
        slug = self._legacy_interview_course()
        first = application.prepare_interview_curriculum(slug, boundary="resume")
        cli.update_state_atomic(slug, lambda state: state.pop("interview_curriculum"))

        with mock.patch(
            "openlearn.courses._route_for_profile",
            side_effect=AssertionError("receipt recovery must precede route fallback"),
        ):
            recovered = application.prepare_interview_curriculum(slug, boundary="resume")

        self.assertEqual(recovered, first)
        self.assertIn("interview_curriculum", cli.load_state(slug))

    def test_manual_repair_removes_stale_reconciliation_artifacts(self) -> None:
        slug = self._legacy_interview_course()
        application.prepare_interview_curriculum(slug, boundary="resume")
        journal = cli.interview_reconciliation_journal_path(slug)
        journal.write_text("{}\n", encoding="utf-8")

        cli.repair_topic_metadata(slug)

        self.assertFalse(journal.exists())
        self.assertFalse(cli.interview_reconciliation_receipt_path(slug).exists())
        self.assertIn("interview_curriculum", cli.load_state(slug))


if __name__ == "__main__":
    unittest.main()
