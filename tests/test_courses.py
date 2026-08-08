from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from unittest import mock
from uuid import uuid4

from openlearn import cli
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


if __name__ == "__main__":
    unittest.main()
