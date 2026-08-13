from __future__ import annotations

import os
import json
import tempfile
import threading
import unittest
from unittest import mock
from datetime import datetime, timezone
from uuid import uuid4

from openlearn import application, cli, interview_prep


class ApplicationQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("OPENLEARN_HOME")
        os.environ["OPENLEARN_HOME"] = self.home.name
        cli._CONFIG_CACHE = None

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("OPENLEARN_HOME", None)
        else:
            os.environ["OPENLEARN_HOME"] = self.previous
        cli._CONFIG_CACHE = None
        self.home.cleanup()

    def test_template_catalog_is_structured_and_contains_reference_course(self) -> None:
        catalog = application.templates()

        interview = next(
            item for item in catalog.templates if item.template_id == "technical-interview-prep"
        )
        self.assertEqual(interview.entry_mode, "interview_prep")
        self.assertIn("LeetCode-style", interview.goal)

    def test_dashboard_read_does_not_mutate_global_state_or_events(self) -> None:
        created = application.create_course(
            application.CourseCreationRequest(name="Python", goal="Learn Python")
        )
        state_path = cli.state_path()
        events_path = cli.topic_events_path(created.course.slug)
        before_state = state_path.read_bytes() if state_path.exists() else None
        before_events = events_path.read_bytes() if events_path.exists() else None

        dashboard = application.dashboard(now=datetime(2026, 8, 7, 12, tzinfo=timezone.utc))

        self.assertEqual(dashboard.resume.slug, "python")
        self.assertEqual(state_path.read_bytes() if state_path.exists() else None, before_state)
        self.assertEqual(events_path.read_bytes() if events_path.exists() else None, before_events)

    def test_dashboard_prefers_active_incomplete_then_recent_incomplete(self) -> None:
        first = application.create_course(
            application.CourseCreationRequest(name="First", goal="First goal")
        ).course
        second = application.create_course(
            application.CourseCreationRequest(name="Second", goal="Second goal")
        ).course
        cli.set_active_topic(first.slug)

        dashboard = application.dashboard()
        self.assertEqual(dashboard.resume.slug, first.slug)

        topic = cli.read_topic(first.slug)
        metadata = dict(topic.metadata)
        metadata["course_completed"] = True
        cli.write_topic(topic.path, metadata, topic.body)
        dashboard = application.dashboard()
        self.assertEqual(dashboard.resume.slug, second.slug)

    def test_provider_lifecycle_projection_never_exposes_the_key(self) -> None:
        application.set_provider_api_key("application-secret")

        status = application.provider_status()

        self.assertTrue(status.key_configured)
        self.assertFalse(status.verified)
        self.assertNotIn("application-secret", repr(status))

        cleared = application.remove_provider_api_key()
        self.assertFalse(cleared.key_configured)

    def test_interview_placement_lifecycle_is_available_without_a_presentation(self) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Technical Interview Prep",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug

        initial = application.sync_interview_placement(slug)
        self.assertEqual(initial["placement"]["status"], "not_started")

        started = application.start_interview_placement(slug)
        placement = started["placement"]
        self.assertEqual(placement["status"], "in_progress")
        evidence_id = interview_prep.placement_evidence_id(placement, "clarification")
        recorded = application.record_interview_placement_response(
            slug,
            stage="clarification",
            response="What constraints and edge cases should I cover?",
            evidence_id=evidence_id,
        )
        self.assertIsNotNone(recorded)
        assert recorded is not None
        self.assertEqual(recorded["placement"]["next_stage"], "reasoning")

        discarded = application.discard_interview_placement(slug)
        self.assertEqual(discarded["placement"]["status"], "not_started")

    def test_interview_learning_projection_separates_committed_lesson_and_reserved_target(
        self,
    ) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Projected Interview Course",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug
        application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4())
        )
        state = cli.load_state(slug)
        canonical = state["interview_curriculum"]
        first, second = canonical["route"]["skills"][:2]
        first_id = first["skill_ref"]["skill_id"]
        operation_id = str(uuid4())
        canonical["cursor"] = {
            "unit_id": second["unit_id"],
            "section_id": second["section_id"],
            "skill_ref": second["skill_ref"],
            "instruction_status": "reserved",
        }
        canonical["evidence"]["exposed"] = [first_id]
        canonical["active_operation"] = {
            "submission_id": operation_id,
            "status": "reserved",
            "target": second,
            "reason": "uncovered_required",
            "rollback": {
                "cursor": {
                    "present": True,
                    "value": {
                        "unit_id": first["unit_id"],
                        "section_id": first["section_id"],
                        "skill_ref": first["skill_ref"],
                        "instruction_status": "covered",
                    },
                }
            },
        }
        state["interview_curriculum"] = canonical
        state["_openlearn_internal"]["active_turn"] = {
            "submission_id": operation_id,
            "status": "reserved",
            "owner_pid": os.getpid(),
        }
        cli.write_text_atomic(
            cli.topic_state_path(slug),
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )
        cli.append_session(
            cli.read_topic(slug),
            "next",
            "Teach the first target.",
            "**Lesson:** The committed first lesson stays readable.",
        )

        projection = application.interview_learning(slug)

        self.assertIsNotNone(projection)
        assert projection is not None
        self.assertEqual(projection.position.skill_id, first_id)
        self.assertEqual(
            (
                projection.position.graph_id,
                projection.position.graph_version,
                projection.position.mastery_policy_version,
                projection.position.skill_id,
            ),
            tuple(
                first["skill_ref"][key]
                for key in (
                    "graph_id",
                    "graph_version",
                    "mastery_policy_version",
                    "skill_id",
                )
            ),
        )
        self.assertEqual(
            projection.next_target.skill_id,
            second["skill_ref"]["skill_id"],
        )
        self.assertEqual(
            (
                projection.next_target.graph_id,
                projection.next_target.graph_version,
                projection.next_target.mastery_policy_version,
                projection.next_target.skill_id,
            ),
            tuple(
                second["skill_ref"][key]
                for key in (
                    "graph_id",
                    "graph_version",
                    "mastery_policy_version",
                    "skill_id",
                )
            ),
        )
        self.assertIn("stays readable", projection.committed_lesson.content)
        self.assertEqual(projection.operation.state, "reserved")
        self.assertEqual(projection.coverage.covered, 1)
        self.assertNotIn("Step", projection.position.label)

    def test_interview_progress_separates_historical_coverage_from_readiness_work(
        self,
    ) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Interview Progress Measures",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug
        application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4())
        )
        state = cli.load_state(slug)
        canonical = state["interview_curriculum"]
        first, second = canonical["route"]["skills"][:2]
        first_id = first["skill_ref"]["skill_id"]
        second_id = second["skill_ref"]["skill_id"]
        canonical["evidence"]["exposed"] = [first_id]
        canonical["evidence"]["due_review"] = [first_id]
        canonical["deferred"] = [
            {
                "skill_id": second_id,
                "return_reason": "explicit_skip",
                "deferred_at_commit_index": 0,
                "deferred_session_id": "session-a",
            }
        ]
        state["interview_curriculum"] = canonical
        cli.write_text_atomic(
            cli.topic_state_path(slug),
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )
        topic = cli.read_topic(slug)
        metadata = dict(topic.metadata)
        metadata["review_due"] = [
            {
                "concept": "Unrelated generic review",
                "due": "2026-01-01",
                "difficulty": "hard",
            },
            {
                "concept": first_id,
                "due": "2026-02-01",
                "difficulty": "hard",
            },
        ]
        cli.write_topic(topic.path, metadata, topic.body)

        projection = application.interview_learning(slug)

        assert projection is not None
        self.assertEqual(projection.coverage.covered, 1)
        self.assertGreaterEqual(projection.readiness.due, 1)
        self.assertGreaterEqual(projection.readiness.deferred, 1)
        self.assertGreaterEqual(projection.readiness.total, 2)
        self.assertEqual(projection.readiness.next_retrieval, "2026-02-01")

    def test_identical_committed_lesson_text_keeps_distinct_source_ids(self) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Repeated Lesson Identity",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug
        application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4())
        )
        response = "**Lesson:** The same words can support separate turns."
        topic = cli.read_topic(slug)
        first_entry = cli._session_entry(
            "next",
            "Continue",
            response,
            created="2026-08-13 12:00 UTC",
            mutation_id="turn_first_occurrence",
        )
        cli.write_topic(topic.path, topic.metadata, topic.body.rstrip() + "\n\n" + first_entry + "\n")
        first = application.interview_learning(slug)
        assert first is not None

        topic = cli.read_topic(slug)
        second_entry = cli._session_entry(
            "next",
            "Continue",
            response,
            created="2026-08-13 12:01 UTC",
            mutation_id="turn_second_occurrence",
        )
        cli.write_topic(
            topic.path,
            topic.metadata,
            topic.body.rstrip() + "\n\n" + second_entry + "\n",
        )
        second = application.interview_learning(slug)
        assert second is not None

        self.assertEqual(first.committed_lesson.content, second.committed_lesson.content)
        self.assertEqual(first.committed_lesson.lesson_id, "lesson_turn_first_occurrence")
        self.assertEqual(second.committed_lesson.lesson_id, "lesson_turn_second_occurrence")

    def test_interview_coverage_distinguishes_absent_and_explicit_optional_selection(
        self,
    ) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Optional Coverage Course",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug
        application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4())
        )
        state = cli.load_state(slug)
        canonical = state["interview_curriculum"]
        route = canonical["route"]
        optional_ids = [
            item["skill_ref"]["skill_id"]
            for item in route["skills"]
            if item["requirement"] == "optional"
        ]
        required_count = sum(
            item["requirement"] == "required" for item in route["skills"]
        )
        self.assertTrue(optional_ids)

        route.pop("optional_skill_ids", None)
        cli.write_text_atomic(
            cli.topic_state_path(slug), json.dumps(state, indent=2, sort_keys=True) + "\n"
        )
        absent = application.interview_learning(slug)
        assert absent is not None
        self.assertEqual(absent.coverage.total, len(route["skills"]))

        state = cli.load_state(slug)
        route = state["interview_curriculum"]["route"]
        route["optional_skill_ids"] = []
        cli.write_text_atomic(
            cli.topic_state_path(slug), json.dumps(state, indent=2, sort_keys=True) + "\n"
        )
        empty = application.interview_learning(slug)
        assert empty is not None
        self.assertEqual(empty.coverage.total, required_count)

        state = cli.load_state(slug)
        route = state["interview_curriculum"]["route"]
        route["optional_skill_ids"] = [optional_ids[0]]
        cli.write_text_atomic(
            cli.topic_state_path(slug), json.dumps(state, indent=2, sort_keys=True) + "\n"
        )
        subset = application.interview_learning(slug)
        assert subset is not None
        self.assertEqual(subset.coverage.total, required_count + 1)

    def test_interview_snapshot_recovers_turn_journal_created_after_initial_check(
        self,
    ) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Snapshot Fence Course",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug
        application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4())
        )
        journal_saved = threading.Event()
        release_writer = threading.Event()
        errors: list[BaseException] = []

        def checkpoint(stage: str) -> None:
            if stage == "after_journal":
                journal_saved.set()
                release_writer.wait(timeout=3)

        def teach() -> None:
            try:
                cli.ask_topic(slug, "Teach the current concept.", output_func=lambda _x: None)
            except BaseException as error:
                errors.append(error)

        with (
            mock.patch.object(cli, "_turn_commit_checkpoint", side_effect=checkpoint),
            mock.patch.object(
                cli,
                "generate_validated_tutor_answer",
                return_value="**Lesson:** Snapshot-fenced committed content.",
            ),
        ):
            worker = threading.Thread(target=teach)
            worker.start()
            self.assertTrue(journal_saved.wait(timeout=3))
            projection = application.interview_learning(slug)
            release_writer.set()
            worker.join(timeout=3)

        self.assertFalse(errors)
        self.assertIsNotNone(projection)
        assert projection is not None
        self.assertIn("Snapshot-fenced", projection.committed_lesson.content)
        self.assertFalse(cli.topic_turn_journal_path(slug).exists())


if __name__ == "__main__":
    unittest.main()
