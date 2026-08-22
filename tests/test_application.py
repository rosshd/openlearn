from __future__ import annotations

import os
import json
import tempfile
import threading
import unittest
from unittest import mock
from datetime import datetime, timezone
from uuid import uuid4

from openlearn import application, cli, interview_curriculum, interview_prep


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

    def test_dashboard_selection_previews_without_changing_active_course(self) -> None:
        active = application.create_course(
            application.CourseCreationRequest(
                name="Active Python",
                template_id="python-basics",
            )
        ).course
        selected = application.create_course(
            application.CourseCreationRequest(
                name="Selected Git",
                template_id="git",
            )
        ).course
        cli.set_active_topic(active.slug)
        before = cli.state_path().read_bytes()

        dashboard = application.dashboard(selected_slug=selected.slug)

        self.assertEqual(dashboard.active_slug, active.slug)
        self.assertEqual(dashboard.resume.slug, active.slug)
        self.assertEqual(dashboard.selected.slug, selected.slug)
        self.assertEqual(dashboard.selected_slug, selected.slug)
        self.assertEqual(cli.state_path().read_bytes(), before)

    def test_course_activation_preserves_non_study_global_state(self) -> None:
        first = application.create_course(
            application.CourseCreationRequest(name="First", goal="First goal")
        ).course
        second = application.create_course(
            application.CourseCreationRequest(name="Second", goal="Second goal")
        ).course
        cli.write_text_atomic(
            cli.state_path(),
            json.dumps(
                {
                    "active_topic": first.slug,
                    "study_streak": 8,
                    "longest_streak": 13,
                    "last_study_date": "2026-08-16",
                    "learner_preference": "keep-me",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )

        result = application.activate_course(second.slug)

        saved = json.loads(cli.state_path().read_text(encoding="utf-8"))
        self.assertEqual(result.slug, second.slug)
        self.assertEqual(saved["active_topic"], second.slug)
        self.assertEqual(saved["study_streak"], 8)
        self.assertEqual(saved["longest_streak"], 13)
        self.assertEqual(saved["last_study_date"], "2026-08-16")
        self.assertEqual(saved["learner_preference"], "keep-me")

    def test_course_deletion_requires_exact_confirmation_and_preserves_other_state(
        self,
    ) -> None:
        first = application.create_course(
            application.CourseCreationRequest(name="First Course", goal="First goal")
        ).course
        second = application.create_course(
            application.CourseCreationRequest(name="Second Course", goal="Second goal")
        ).course
        cli.activate_topic_without_study(first.slug)
        cli.update_state_atomic(
            second.slug,
            lambda state: state.__setitem__("known", ["keep-this-evidence"]),
        )
        cli.write_text_atomic(
            cli.state_path(),
            json.dumps(
                {
                    "active_topic": first.slug,
                    "study_streak": 8,
                    "longest_streak": 13,
                    "last_study_date": "2026-08-16",
                    "learner_preference": "keep-me",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        cli.write_text_atomic(
            cli.config_path(),
            json.dumps({"openai_api_key": "do-not-touch"}, sort_keys=True) + "\n",
        )
        config_before = cli.config_path().read_bytes()
        second_before = cli.topic_state_path(second.slug).read_bytes()
        preview = application.preview_course_deletion(first.slug)

        self.assertEqual(preview.slug, first.slug)
        self.assertEqual(preview.title, "First Course")
        self.assertEqual(preview.backup_scope, "whole-home")
        with self.assertRaisesRegex(application.CourseDeletionConfirmationError, "exact"):
            application.confirm_course_deletion(
                preview,
                confirmation_slug="wrong-course",
                confirmation_title="First Course",
            )
        with self.assertRaisesRegex(application.CourseDeletionConfirmationError, "exact"):
            application.confirm_course_deletion(
                preview,
                confirmation_slug=first.slug,
                confirmation_title="first course",
            )
        self.assertTrue(cli.topic_path(first.slug).exists())

        with mock.patch(
            "openlearn.data_management.create_backup",
            side_effect=AssertionError("deletion must not create a backup implicitly"),
        ):
            result = application.confirm_course_deletion(
                preview,
                confirmation_slug=first.slug,
                confirmation_title="First Course",
            )

        saved = json.loads(cli.state_path().read_text(encoding="utf-8"))
        self.assertTrue(result.deleted)
        self.assertFalse(result.replayed)
        self.assertEqual(result.next_selected_slug, second.slug)
        self.assertNotIn("active_topic", saved)
        self.assertEqual(saved["study_streak"], 8)
        self.assertEqual(saved["longest_streak"], 13)
        self.assertEqual(saved["last_study_date"], "2026-08-16")
        self.assertEqual(saved["learner_preference"], "keep-me")
        self.assertEqual(cli.config_path().read_bytes(), config_before)
        self.assertEqual(cli.topic_state_path(second.slug).read_bytes(), second_before)

        replay = application.confirm_course_deletion(
            preview,
            confirmation_slug=first.slug,
            confirmation_title="First Course",
        )
        self.assertFalse(replay.deleted)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.next_selected_slug, second.slug)

    def test_generic_course_settings_preview_is_read_only_and_confirm_is_idempotent(
        self,
    ) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Python Notes",
                template_id="python-basics",
            )
        ).course
        slug = created.slug
        topic = cli.read_topic(slug)
        learner_body = topic.body + "\nA learner-authored note.\n"
        cli.write_text_atomic(
            topic.path,
            cli.format_topic(cli.stable_metadata_for_topic(topic.metadata), learner_body),
        )
        before = topic.path.read_bytes()
        preview = application.preview_course_settings(
            slug,
            application.CourseSettingsChange(
                title="Python Interview Practice",
                goal="Solve Python interview problems",
                difficulty="deep",
                weekly_minutes=180,
                session_minutes=60,
            ),
        )
        self.assertEqual(topic.path.read_bytes(), before)

        submission_id = str(uuid4())
        first = application.confirm_course_settings(preview, submission_id=submission_id)
        replay = application.confirm_course_settings(preview, submission_id=submission_id)

        updated = cli.read_topic(slug)
        self.assertEqual(updated.slug, slug)
        self.assertEqual(updated.metadata["topic"], "Python Interview Practice")
        self.assertEqual(updated.metadata["goal"], "Solve Python interview problems")
        self.assertEqual(updated.metadata["mastery_profile"], "deep")
        self.assertEqual(updated.metadata["weekly_minutes"], 180)
        self.assertEqual(updated.metadata["session_minutes"], 60)
        self.assertIn("A learner-authored note.", updated.body)
        self.assertEqual(first.receipt_id, replay.receipt_id)
        self.assertTrue(replay.replayed)

    def test_course_library_projection_exposes_ordered_path_and_first_pass_coverage(
        self,
    ) -> None:
        course = application.create_course(
            application.CourseCreationRequest(
                name="Python Path",
                template_id="python-basics",
            )
        ).course

        projected = application.course(course.slug).card.library

        self.assertIsNotNone(projected.current)
        assert projected.current is not None
        self.assertEqual(projected.current.identity, "unit:1")
        self.assertEqual(projected.current.title, "Variables and Data Types")
        self.assertEqual(len(projected.upcoming), 5)
        self.assertEqual(projected.upcoming[0].identity, "unit:2")
        self.assertEqual(projected.coverage.covered, 0)
        self.assertEqual(projected.coverage.total, len(projected.path))
        self.assertFalse(projected.first_pass_complete)

    def test_interview_library_projection_uses_accepted_route_and_separates_readiness(
        self,
    ) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Interview Library Path",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug
        accepted = application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4())
        )
        canonical = accepted["canonical"]
        first = canonical["route"]["skills"][0]
        first_id = first["skill_ref"]["skill_id"]
        state = cli.load_state(slug)
        state["interview_curriculum"]["evidence"]["exposed"] = [first_id]
        state["interview_curriculum"]["evidence"]["weak"] = [first_id]
        state["interview_curriculum"]["evidence"]["due_review"] = [first_id]
        cli.write_text_atomic(
            cli.topic_state_path(slug),
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )

        projected = application.course(slug).card.library

        self.assertEqual(projected.coverage.covered, 1)
        self.assertEqual(projected.coverage.total, len(projected.path))
        self.assertEqual(projected.current.identity, first_id)
        self.assertEqual(projected.weak_areas, (projected.current.title,))
        self.assertTrue(projected.review.actionable)
        self.assertEqual(projected.review.due, 1)
        self.assertEqual(projected.review.kind, "canonical")
        self.assertFalse(projected.first_pass_complete)

    def test_interview_deepening_opens_weak_practice_without_awarding_mastery(self) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Interview Deepening",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug
        accepted = application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4())
        )
        first_id = accepted["canonical"]["route"]["skills"][0]["skill_ref"]["skill_id"]
        state = cli.load_state(slug)
        canonical = state["interview_curriculum"]
        canonical["evidence"]["exposed"] = [first_id]
        canonical["evidence"]["weak"] = [first_id]
        state["interview_curriculum"] = canonical
        cli.write_text_atomic(
            cli.topic_state_path(slug),
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )

        with mock.patch.dict(os.environ, {"OPENLEARN_MOCK": "1"}, clear=False):
            application.advance_course_growth(
                slug,
                action="deepen",
                submission_id=str(uuid4()),
            )

        after = cli.load_state(slug)["interview_curriculum"]["evidence"]
        self.assertNotIn(first_id, after["ready"])

    def test_provider_lifecycle_projection_never_exposes_the_key(self) -> None:
        application.set_provider_api_key("application-secret")

        status = application.provider_status()

        self.assertTrue(status.key_configured)
        self.assertFalse(status.verified)
        self.assertNotIn("application-secret", repr(status))

        cleared = application.remove_provider_api_key()
        self.assertFalse(cleared.key_configured)

    def test_interview_placement_lifecycle_starts_the_rapid_confidence_route(self) -> None:
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
        self.assertEqual(placement["lifecycle_version"], interview_prep.PLACEMENT_V4)
        self.assertEqual(placement["next_stage"], "confidence")
        self.assertIsNone(placement["activity_id"])
        self.assertEqual(placement["evidence_refs"], [])

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
        application.accept_interview_curriculum(slug, action="skip", submission_id=str(uuid4()))
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

    def test_interview_learning_reads_dynamic_pending_question_from_state(self) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Pending Interview Check",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug
        application.accept_interview_curriculum(slug, action="skip", submission_id=str(uuid4()))
        state = cli.load_state(slug)
        state["pending_question"] = {
            "kind": "free_response",
            "question": "Explain the current invariant.",
        }
        cli.update_state_atomic(
            slug,
            lambda current: current.__setitem__("pending_question", state["pending_question"]),
        )

        projection = application.interview_learning(slug)

        self.assertIsNotNone(projection)
        assert projection is not None
        self.assertEqual(projection.pending_prompt, "Explain the current invariant.")

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
        application.accept_interview_curriculum(slug, action="skip", submission_id=str(uuid4()))
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

    def test_practice_lesson_projects_committed_target_instead_of_forward_cursor(
        self,
    ) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Practice Lesson Identity",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug
        application.accept_interview_curriculum(slug, action="skip", submission_id=str(uuid4()))
        state = cli.load_state(slug)
        canonical = state["interview_curriculum"]
        first, second = canonical["route"]["skills"][:2]
        canonical["cursor"] = {
            "unit_id": first["unit_id"],
            "section_id": first["section_id"],
            "skill_ref": first["skill_ref"],
            "instruction_status": "covered",
        }
        canonical["committed_target"] = {
            **second,
            "depth_mode": "practice",
        }
        canonical["evidence"]["exposed"] = [
            first["skill_ref"]["skill_id"],
            second["skill_ref"]["skill_id"],
        ]
        state["interview_curriculum"] = canonical
        cli.write_text_atomic(
            cli.topic_state_path(slug),
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )
        cli.append_session(
            cli.read_topic(slug),
            "next",
            "Practice a covered skill.",
            "**Check:** Explain the hashing invariant.",
        )

        projection = application.interview_learning(slug)

        assert projection is not None
        self.assertEqual(projection.position.skill_id, second["skill_ref"]["skill_id"])
        self.assertEqual(projection.position.emphasis, "Practice")
        self.assertEqual(projection.committed_lesson.title, projection.position.skill_label)

    def test_practice_lesson_repairs_a_historical_check_only_response(self) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Check Only Practice Lesson",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug
        application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4())
        )
        state = cli.load_state(slug)
        canonical = state["interview_curriculum"]
        canonical["route"]["skills"][0]["depth_mode"] = "practice"
        resolution = interview_curriculum.resolve_progression_target(
            canonical, intent="continue"
        )
        assert resolution.target is not None
        state["interview_curriculum"] = interview_curriculum.record_progression_commit(
            resolution.state, resolution.target.skill_id
        )
        cli.write_text_atomic(
            cli.topic_state_path(slug),
            json.dumps(state, indent=2, sort_keys=True) + "\n",
        )
        cli.append_session(
            cli.read_topic(slug),
            "next",
            "Start the practice lesson.",
            "**Check:** Explain the arrays invariant.",
        )

        projection = application.interview_learning(slug)

        assert projection is not None
        self.assertIn("**Lesson:**", projection.committed_lesson.content)
        self.assertIn("**Example:**", projection.committed_lesson.content)
        self.assertIn("**Check:**", projection.committed_lesson.content)
        self.assertIn("Arrays and strings", projection.committed_lesson.content)

    def test_identical_committed_lesson_text_keeps_distinct_source_ids(self) -> None:
        created = application.create_course(
            application.CourseCreationRequest(
                name="Repeated Lesson Identity",
                template_id="technical-interview-prep",
            )
        )
        slug = created.course.slug
        application.accept_interview_curriculum(slug, action="skip", submission_id=str(uuid4()))
        response = "**Lesson:** The same words can support separate turns."
        topic = cli.read_topic(slug)
        first_entry = cli._session_entry(
            "next",
            "Continue",
            response,
            created="2026-08-13 12:00 UTC",
            mutation_id="turn_first_occurrence",
        )
        cli.write_topic(
            topic.path, topic.metadata, topic.body.rstrip() + "\n\n" + first_entry + "\n"
        )
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
        application.accept_interview_curriculum(slug, action="skip", submission_id=str(uuid4()))
        state = cli.load_state(slug)
        canonical = state["interview_curriculum"]
        route = canonical["route"]
        optional_ids = [
            item["skill_ref"]["skill_id"]
            for item in route["skills"]
            if item["requirement"] == "optional"
        ]
        required_count = sum(item["requirement"] == "required" for item in route["skills"])
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
        application.accept_interview_curriculum(slug, action="skip", submission_id=str(uuid4()))
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
