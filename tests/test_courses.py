from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
import unittest
from unittest import mock
from uuid import uuid4

from openlearn import application, cli, courses, interview_curriculum, interview_prep
from openlearn.application import CalibrationContext, CourseCreationRequest
from openlearn.course_templates import CourseTemplate
from openlearn.courses import course_conversation_source, create_course


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

    def test_deletion_removes_every_course_owned_artifact(self) -> None:
        result = create_course(CourseCreationRequest(name="Owned Artifacts", goal="Learn"))
        slug = result.course.slug
        generation = cli.current_topic_generation(slug)
        assert generation is not None
        preview = application.preview_course_deletion(slug)
        owned_files = (
            cli.interview_profile_path(slug),
            cli.interview_edit_journal_path(slug),
            cli.topic_activity_journal_path(slug),
            cli.topic_turn_journal_path(slug),
            cli.interview_reconciliation_journal_path(slug),
            cli.interview_reconciliation_receipt_path(slug),
            cli.interview_route_journal_path(slug),
            cli.course_settings_journal_path(slug),
        )
        for path in owned_files:
            cli.write_text_atomic(path, "{}\n")
        owned_dirs = (
            cli.topic_data_dir(slug),
            cli.topic_drill_dir(slug),
            cli.attempt_store().topic_dir(slug),
            cli.topics_dir() / "interview-attempts" / slug,
        )
        for directory in owned_dirs:
            directory.mkdir(parents=True, exist_ok=True)
            cli.write_text_atomic(directory / "owned.json", "{}\n")
        unrelated = cli.topics_dir() / "drills" / "other-course" / "keep.py"
        unrelated.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text_atomic(unrelated, "keep\n")

        deleted = application.confirm_course_deletion(
            preview,
            confirmation_slug=slug,
            confirmation_title="Owned Artifacts",
        )

        self.assertTrue(deleted.deleted)
        tombstone = cli.read_topic_deletion_tombstone(slug)
        self.assertIsNotNone(tombstone)
        assert tombstone is not None
        self.assertEqual(tombstone["deleted_title"], "Owned Artifacts")
        legacy_tombstone = dict(tombstone)
        legacy_tombstone.pop("deleted_title")
        cli.write_text_atomic(
            cli.topic_deletion_tombstone_path(slug),
            json.dumps(legacy_tombstone, indent=2, sort_keys=True) + "\n",
        )
        self.assertIsNotNone(cli.read_topic_deletion_tombstone(slug))
        for path in owned_files:
            self.assertFalse(path.exists(), path)
        for directory in owned_dirs:
            self.assertFalse(directory.exists(), directory)
        self.assertTrue(unrelated.exists())

    def test_dashboard_recovers_interrupted_deletion_at_every_checkpoint(self) -> None:
        for stage in (
            "after_tombstone",
            "after_topic",
            "after_state",
            "after_events",
            "after_journals",
        ):
            with self.subTest(stage=stage):
                result = create_course(
                    CourseCreationRequest(name=f"Interrupted deletion {stage}", goal="Learn")
                )
                slug = result.course.slug
                topic_path = cli.topic_path(slug)
                owned_paths = (
                    cli.topic_backup_path(topic_path),
                    cli.interview_profile_path(slug),
                    cli.interview_edit_journal_path(slug),
                    cli.topic_activity_journal_path(slug),
                    cli.topic_turn_journal_path(slug),
                    cli.interview_reconciliation_journal_path(slug),
                    cli.interview_reconciliation_receipt_path(slug),
                    cli.interview_route_journal_path(slug),
                    cli.course_settings_journal_path(slug),
                    cli.topic_events_path(slug),
                )
                owned_directories = (
                    cli.topic_data_dir(slug),
                    cli.topic_drill_dir(slug),
                    cli.attempt_store().topic_dir(slug),
                    cli.topics_dir() / "interview-attempts" / slug,
                )
                for path in owned_paths:
                    cli.write_text_atomic(path, "{}\n")
                for directory in owned_directories:
                    directory.mkdir(parents=True, exist_ok=True)
                    cli.write_text_atomic(directory / "owned.json", "{}\n")

                def crash(boundary: str, *, expected: str = stage) -> None:
                    if boundary == expected:
                        raise RuntimeError(expected)

                with mock.patch.object(cli, "_topic_delete_checkpoint", side_effect=crash):
                    with self.assertRaisesRegex(RuntimeError, stage):
                        cli.delete_topic_files(slug)

                dashboard = application.dashboard()

                self.assertNotIn(slug, {card.slug for card in dashboard.courses})
                self.assertTrue(cli.topic_deletion_tombstone_path(slug).exists())
                for path in (topic_path, cli.topic_state_path(slug), *owned_paths):
                    self.assertFalse(path.exists(), path)
                for directory in owned_directories:
                    self.assertFalse(directory.exists(), directory)

    def test_stale_deletion_preview_cannot_delete_recreated_generation(self) -> None:
        created = create_course(CourseCreationRequest(name="Generation Fence", goal="Old"))
        preview = application.preview_course_deletion(created.course.slug)
        cli.delete_topic_files(
            created.course.slug,
            expected_generation=preview.topic_generation,
            allow_replay=True,
        )
        tombstone = cli.topic_deletion_tombstone_path(created.course.slug)
        cli.durable_unlink(tombstone)
        recreated = create_course(CourseCreationRequest(name="Generation Fence", goal="New"))
        self.assertEqual(recreated.course.slug, created.course.slug)

        with self.assertRaisesRegex(
            courses.CourseDeletionConflictError, "generation changed"
        ):
            application.confirm_course_deletion(
                preview,
                confirmation_slug=preview.slug,
                confirmation_title=preview.title,
            )

        self.assertEqual(cli.read_topic(recreated.course.slug).metadata["goal"], "New")

    def test_deletion_preview_cannot_confirm_after_course_title_changes(self) -> None:
        created = create_course(CourseCreationRequest(name="Old Title", goal="Learn"))
        preview = application.preview_course_deletion(created.course.slug)
        settings = application.preview_course_settings(
            created.course.slug,
            application.CourseSettingsChange(title="New Title"),
        )
        application.confirm_course_settings(settings, submission_id=str(uuid4()))

        with self.assertRaisesRegex(courses.CourseDeletionConflictError, "title changed"):
            application.confirm_course_deletion(
                preview,
                confirmation_slug=preview.slug,
                confirmation_title=preview.title,
            )

        self.assertTrue(cli.topic_path(created.course.slug).exists())

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

    def test_settings_confirmation_rejects_stale_revision_and_active_turn(self) -> None:
        result = create_course(CourseCreationRequest(name="Settings Fence", goal="Learn"))
        preview = application.preview_course_settings(
            result.course.slug,
            application.CourseSettingsChange(goal="Learn safely"),
        )

        def advance(state: dict[str, object]) -> None:
            internal = state.setdefault("_openlearn_internal", {})
            assert isinstance(internal, dict)
            internal["course_revision"] = 1

        cli.update_state_atomic(result.course.slug, advance)
        with self.assertRaisesRegex(
            courses.CourseSettingsConflictError, "revision changed"
        ):
            application.confirm_course_settings(preview, submission_id=str(uuid4()))

        def reserve(state: dict[str, object]) -> None:
            internal = state.setdefault("_openlearn_internal", {})
            assert isinstance(internal, dict)
            internal["active_turn"] = {"submission_id": str(uuid4())}

        cli.update_state_atomic(result.course.slug, reserve)
        with self.assertRaisesRegex(
            courses.CourseSettingsConflictError, "operation is active"
        ):
            application.preview_course_settings(
                result.course.slug,
                application.CourseSettingsChange(goal="Still blocked"),
            )

    def test_interview_settings_use_profile_validation_and_preserve_course_identity(
        self,
    ) -> None:
        result = create_course(
            CourseCreationRequest(
                name="Interview Settings",
                template_id="technical-interview-prep",
            )
        )
        slug = result.course.slug
        preview = application.preview_course_settings(
            slug,
            application.CourseSettingsChange(
                title="Backend Interview Practice",
                difficulty="efficient",
                weekly_minutes=240,
                session_minutes=60,
                interview_fields={"role_family": "backend", "target_level": "mid"},
            ),
        )

        application.confirm_course_settings(preview, submission_id=str(uuid4()))

        profile = interview_prep.load_profile(cli.interview_profile_path(slug))
        values = profile["profile"]
        self.assertEqual(cli.read_topic(slug).slug, slug)
        self.assertEqual(cli.read_topic(slug).metadata["topic"], "Backend Interview Practice")
        self.assertEqual(cli.read_topic(slug).metadata["mastery_profile"], "efficient")
        self.assertEqual(values["role_family"], "backend")
        self.assertEqual(values["target_level"], "mid")
        self.assertEqual(values["weekly_minutes"], 240)
        self.assertEqual(values["session_minutes"], 60)
        self.assertEqual(profile["profile_revision"], 2)

    def test_interview_outline_settings_rematerialize_route_and_preserve_evidence(
        self,
    ) -> None:
        result = create_course(
            CourseCreationRequest(
                name="Interview Route Settings",
                template_id="technical-interview-prep",
            )
        )
        slug = result.course.slug
        accepted = application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4())
        )
        canonical = accepted["canonical"]
        first_skill = canonical["route"]["skills"][0]["skill_ref"]["skill_id"]
        state = cli.load_state(slug)
        state["interview_curriculum"]["evidence"]["weak"] = [first_skill]
        cli.write_text_atomic(
            cli.topic_state_path(slug), json.dumps(state, indent=2, sort_keys=True) + "\n"
        )
        preview = application.preview_course_settings(
            slug,
            application.CourseSettingsChange(
                interview_fields={"target_level": "mid", "interview_focus": "balanced"}
            ),
        )

        application.confirm_course_settings(preview, submission_id=str(uuid4()))

        updated = cli.load_state(slug)["interview_curriculum"]
        profile = interview_prep.load_profile(cli.interview_profile_path(slug))["profile"]
        self.assertEqual(updated["route"]["target_level"], "mid")
        self.assertEqual(updated["route"]["route_id"], "balanced")
        self.assertEqual(profile["target_level"], "mid")
        remaining_ids = {
            item["skill_ref"]["skill_id"] for item in updated["route"]["skills"]
        }
        if first_skill in remaining_ids:
            self.assertIn(first_skill, updated["evidence"]["weak"])

    def test_settings_journal_recovers_each_publication_checkpoint(self) -> None:
        for stage in (
            "after_journal",
            "after_topic",
            "after_state",
            "after_profile",
            "after_event",
            "after_receipt",
        ):
            result = create_course(
                CourseCreationRequest(name=f"Settings {stage}", goal="Before")
            )
            slug = result.course.slug
            preview = application.preview_course_settings(
                slug,
                application.CourseSettingsChange(goal="After", difficulty="deep"),
            )
            submission_id = str(uuid4())

            def crash(boundary: str, *, expected: str = stage) -> None:
                if boundary == expected:
                    raise RuntimeError(expected)

            with mock.patch.object(courses, "_settings_checkpoint", side_effect=crash):
                with self.assertRaisesRegex(RuntimeError, stage):
                    application.confirm_course_settings(
                        preview, submission_id=submission_id
                    )

            self.assertTrue(cli.course_settings_journal_path(slug).exists())
            self.assertTrue(courses.recover_course_settings(slug))
            self.assertFalse(cli.course_settings_journal_path(slug).exists())
            saved = cli.read_topic(slug)
            self.assertEqual(saved.metadata["goal"], "After")
            self.assertEqual(saved.metadata["mastery_profile"], "deep")
            receipt = json.loads(
                cli.course_settings_receipt_path(slug, submission_id).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(receipt["final_revision"], 1)

    def test_legacy_route_acceptance_retry_without_submission_id_is_idempotent(self) -> None:
        result = create_course(
            CourseCreationRequest(
                name="Legacy Route Retry",
                template_id="technical-interview-prep",
            )
        )

        first = application.accept_interview_curriculum(result.course.slug, action="skip")
        replay = application.accept_interview_curriculum(result.course.slug, action="skip")

        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["receipt"], first["receipt"])
        self.assertEqual(len(cli.load_state(result.course.slug)["_interview_route_receipts"]), 1)
        events = cli.load_event_log(cli.topic_events_path(result.course.slug))
        self.assertEqual(
            sum(event.get("event_id") == f"{first['receipt']['action_id']}:0" for event in events),
            1,
        )

        changed = application.accept_interview_curriculum(
            result.course.slug,
            action="change",
            changes={"target_level": "mid"},
        )
        self.assertFalse(changed["replayed"])
        self.assertNotEqual(changed["receipt"]["action_id"], first["receipt"]["action_id"])

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
        self.assertEqual(
            canonical["evidence"]["due_review"],
            ["concept.arrays-strings", "concept.hashing"],
        )
        self.assertEqual(canonical["legacy_context"]["raw_review_due"], ["Arrays and Hashing"])
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

    def test_dashboard_projection_never_reads_course_transcripts(self) -> None:
        generic = create_course(
            CourseCreationRequest(
                name="Transcript Free Generic",
                template_id="python-basics",
            )
        ).course
        interview = create_course(
            CourseCreationRequest(
                name="Transcript Free Interview",
                template_id="technical-interview-prep",
            )
        ).course
        application.accept_interview_curriculum(
            interview.slug, action="skip", submission_id=str(uuid4())
        )

        with mock.patch.object(
            cli,
            "read_topic",
            side_effect=AssertionError("dashboard must not parse transcripts"),
        ):
            dashboard = application.dashboard(selected_slug=generic.slug)

        self.assertEqual(dashboard.selected.slug, generic.slug)
        self.assertEqual({card.slug for card in dashboard.courses}, {generic.slug, interview.slug})

    def test_follow_up_recommendation_prefers_exact_template_then_tag_overlap(
        self,
    ) -> None:
        templates = [
            CourseTemplate(
                name="Source",
                slug="source",
                goal="Source goal",
                tags=("algorithms",),
                units=("Unit 1: Source",),
            ),
            CourseTemplate(
                name="Weak Area Specialty",
                slug="weak-specialty",
                goal="Practice graphs",
                tags=("advanced",),
                units=("Unit 1: Graphs",),
                specializes_tags=("graphs",),
            ),
            CourseTemplate(
                name="Exact Specialty",
                slug="exact-specialty",
                goal="Go deeper",
                tags=("advanced",),
                units=("Unit 1: Advanced source",),
                specializes_template_ids=("source",),
            ),
        ]

        with mock.patch.object(courses, "available_course_templates", return_value=templates):
            recommendation = courses.recommend_follow_up_template("source", weak_areas=("Graphs",))

        self.assertIsNotNone(recommendation)
        assert recommendation is not None
        self.assertEqual(recommendation.template_id, "exact-specialty")
        self.assertEqual(recommendation.kind, "curated")

    def test_due_review_queue_is_scoped_to_one_course(self) -> None:
        first = create_course(CourseCreationRequest(name="First Review", goal="Learn"))
        second = create_course(CourseCreationRequest(name="Second Review", goal="Learn"))
        for slug, concept in (
            (first.course.slug, "First concept"),
            (second.course.slug, "Second concept"),
        ):
            topic = cli.read_topic(slug)
            metadata = dict(topic.metadata)
            metadata["review_due"] = [
                {"concept": concept, "due": "2026-08-01", "difficulty": "hard"}
            ]
            cli.write_topic(topic.path, metadata, topic.body)

        queue = courses.course_due_reviews(first.course.slug, today_value="2026-08-17")

        self.assertEqual(queue.slug, first.course.slug)
        self.assertEqual(tuple(item.concept for item in queue.items), ("First concept",))

    def test_builtin_interview_course_has_model_free_curated_specialty(self) -> None:
        with mock.patch(
            "openlearn.providers.chat_completion",
            side_effect=AssertionError("curated ranking must not call a provider"),
        ):
            recommendation = courses.recommend_follow_up_template(
                "technical-interview-prep",
                weak_areas=("Dynamic programming",),
            )

        self.assertIsNotNone(recommendation)
        assert recommendation is not None
        self.assertEqual(recommendation.template_id, "algorithms")

    def test_dashboard_metadata_snapshot_recovers_pending_route_acceptance(self) -> None:
        result = create_course(
            CourseCreationRequest(
                name="Dashboard Route Recovery",
                template_id="technical-interview-prep",
            )
        )
        slug = result.course.slug
        with mock.patch(
            "openlearn.courses._route_acceptance_checkpoint",
            side_effect=lambda stage: (
                (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_state" else None
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "after_state"):
                application.accept_interview_curriculum(
                    slug,
                    action="skip",
                    submission_id=str(uuid4()),
                    expected_revision=0,
                )

        self.assertTrue(cli.interview_route_journal_path(slug).exists())
        dashboard = application.dashboard()

        self.assertFalse(cli.interview_route_journal_path(slug).exists())
        card = next(item for item in dashboard.courses if item.slug == slug)
        self.assertIsNotNone(card.interview)
        self.assertEqual(cli.load_state(slug)["_openlearn_internal"]["course_revision"], 1)

    def test_conversation_snapshot_cannot_pair_new_transcript_with_old_revision(
        self,
    ) -> None:
        result = create_course(
            CourseCreationRequest(name="Atomic Chat Snapshot", goal="Learn safely")
        )
        slug = result.course.slug
        topic = cli.read_topic(slug)
        entry = cli._session_entry(
            cli.SIDE_CHAT_SESSION_KIND,
            "Why?",
            "Because the snapshot is atomic.",
            created="2026-08-13T12:00:00+00:00",
            mutation_id=str(uuid4()),
        )
        updated_body = topic.body.rstrip() + "\n\n" + entry + "\n"
        topic_written = threading.Event()
        release_writer = threading.Event()
        reader_started = threading.Event()
        snapshot: list[dict[str, object]] = []

        def write_both_stores() -> None:
            with cli.topic_store_locks(slug):
                cli.write_text_atomic(
                    cli.topic_path(slug),
                    cli.format_topic(topic.metadata, updated_body),
                )
                topic_written.set()
                release_writer.wait(timeout=2)
                state = cli._load_state_unlocked(slug)
                internal = state.get("_openlearn_internal")
                internal = dict(internal) if isinstance(internal, dict) else {}
                internal["course_revision"] = 0
                internal["side_chat_revision"] = 1
                state["_openlearn_internal"] = internal
                cli.write_text_atomic(
                    cli.topic_state_path(slug),
                    json.dumps(state, indent=2, sort_keys=True) + "\n",
                )

        def read_snapshot() -> None:
            reader_started.set()
            snapshot.append(course_conversation_source(slug))

        writer = threading.Thread(target=write_both_stores)
        writer.start()
        self.assertTrue(topic_written.wait(timeout=1))
        reader = threading.Thread(target=read_snapshot)
        reader.start()
        self.assertTrue(reader_started.wait(timeout=1))
        reader.join(timeout=0.05)
        self.assertTrue(reader.is_alive())

        release_writer.set()
        writer.join(timeout=2)
        reader.join(timeout=2)

        self.assertFalse(writer.is_alive())
        self.assertFalse(reader.is_alive())
        self.assertEqual(snapshot[0]["course_revision"], 0)
        self.assertEqual(snapshot[0]["side_chat_revision"], 1)
        self.assertIn("Because the snapshot is atomic.", str(snapshot[0]["body"]))

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

    def test_schema_one_reconciliation_journal_migrates_before_recovery(self) -> None:
        slug = self._legacy_interview_course()
        with mock.patch(
            "openlearn.courses._reconciliation_checkpoint",
            side_effect=lambda stage: (
                (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_journal" else None
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "after_journal"):
                application.prepare_interview_curriculum(slug, boundary="resume")
        journal_path = cli.interview_reconciliation_journal_path(slug)
        legacy = json.loads(journal_path.read_text(encoding="utf-8"))
        legacy["schema_version"] = courses.LEGACY_RECONCILIATION_SCHEMA_VERSION
        legacy.pop("journal_sha256")
        legacy["receipt"]["schema_version"] = courses.LEGACY_RECONCILIATION_SCHEMA_VERSION
        legacy["receipt"].pop("receipt_sha256")
        cli.write_text_atomic(journal_path, json.dumps(legacy, indent=2, sort_keys=True) + "\n")

        application.prepare_interview_curriculum(slug, boundary="resume")

        self.assertFalse(journal_path.exists())
        receipt = json.loads(
            cli.interview_reconciliation_receipt_path(slug).read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["schema_version"], courses.RECONCILIATION_SCHEMA_VERSION)
        self.assertIsInstance(receipt["receipt_sha256"], str)
        self.assertIn("interview_curriculum", cli.load_state(slug))

    def test_schema_one_reconciliation_receipt_migrates_before_replay(self) -> None:
        slug = self._legacy_interview_course()
        expected = application.prepare_interview_curriculum(slug, boundary="resume")
        receipt_path = cli.interview_reconciliation_receipt_path(slug)
        legacy = json.loads(receipt_path.read_text(encoding="utf-8"))
        legacy["schema_version"] = courses.LEGACY_RECONCILIATION_SCHEMA_VERSION
        legacy.pop("receipt_sha256")
        cli.write_text_atomic(receipt_path, json.dumps(legacy, indent=2, sort_keys=True) + "\n")
        cli.update_state_atomic(slug, lambda state: state.pop("interview_curriculum"))

        recovered = application.prepare_interview_curriculum(slug, boundary="resume")

        self.assertEqual(recovered, expected)
        migrated = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["schema_version"], courses.RECONCILIATION_SCHEMA_VERSION)
        self.assertIsInstance(migrated["receipt_sha256"], str)

    def test_schema_one_reconciliation_artifact_with_conflicting_identity_fails_closed(
        self,
    ) -> None:
        slug = self._legacy_interview_course()
        application.prepare_interview_curriculum(slug, boundary="resume")
        receipt_path = cli.interview_reconciliation_receipt_path(slug)
        legacy = json.loads(receipt_path.read_text(encoding="utf-8"))
        legacy["schema_version"] = courses.LEGACY_RECONCILIATION_SCHEMA_VERSION
        legacy.pop("receipt_sha256")
        legacy["reconciliation_id"] = "reconcile_conflicting"
        cli.write_text_atomic(receipt_path, json.dumps(legacy, indent=2, sort_keys=True) + "\n")
        cli.update_state_atomic(slug, lambda state: state.pop("interview_curriculum"))
        before_state = cli.topic_state_path(slug).read_bytes()

        with self.assertRaisesRegex(cli.OpenLearnError, "invalid identity"):
            application.prepare_interview_curriculum(slug, boundary="resume")

        self.assertEqual(cli.topic_state_path(slug).read_bytes(), before_state)
        self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8")), legacy)

    def test_profile_edit_recovers_route_acceptance_interrupted_after_profile_write(self) -> None:
        result = create_course(
            CourseCreationRequest(
                name="Route Then Edit",
                template_id="technical-interview-prep",
            )
        )
        slug = result.course.slug
        with mock.patch(
            "openlearn.courses._route_acceptance_checkpoint",
            side_effect=lambda stage: (
                (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_profile" else None
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "after_profile"):
                application.accept_interview_curriculum(
                    slug,
                    action="skip",
                    submission_id=str(uuid4()),
                    expected_revision=0,
                )

        self.assertTrue(cli.interview_route_journal_path(slug).exists())
        cli.cmd_interview_edit(
            argparse.Namespace(
                topic=slug,
                field="target_level",
                value="mid",
            ),
            output_func=lambda _text="": None,
        )

        self.assertFalse(cli.interview_route_journal_path(slug).exists())
        state = cli.load_state(slug)
        self.assertIn("interview_curriculum", state)
        self.assertEqual(state["_openlearn_internal"]["course_revision"], 1)
        profile = cli._load_interview_profile(slug)
        self.assertEqual(profile["profile"]["target_level"], "mid")
        self.assertEqual(profile["profile_revision"], 2)

    def test_reconciliation_rebuilds_when_evidence_changes_after_journal(self) -> None:
        slug = self._legacy_interview_course()
        with mock.patch(
            "openlearn.courses._reconciliation_checkpoint",
            side_effect=lambda stage: (
                (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_state" else None
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "after_state"):
                application.prepare_interview_curriculum(slug, boundary="resume")
        stale = json.loads(
            cli.interview_reconciliation_journal_path(slug).read_text(encoding="utf-8")
        )
        cli.update_state_atomic(
            slug,
            lambda state: state.__setitem__(
                "assessment_history",
                [{"concept": "Graphs", "correct": False}],
            ),
        )

        application.prepare_interview_curriculum(slug, boundary="resume")

        state = cli.load_state(slug)
        self.assertEqual(
            state["assessment_history"],
            [{"concept": "Graphs", "correct": False}],
        )
        canonical = state["interview_curriculum"]
        self.assertNotEqual(
            canonical["reconciliation"]["source_fingerprint"],
            stale["source_fingerprint"],
        )
        self.assertFalse(cli.interview_reconciliation_journal_path(slug).exists())

    def test_reconciliation_fails_closed_on_malformed_authoritative_state(self) -> None:
        slug = self._legacy_interview_course()
        state_path = cli.topic_state_path(slug)
        state_path.write_text('{"concept_attempts":', encoding="utf-8")
        before = {
            path: path.read_bytes()
            for path in (
                cli.topic_path(slug),
                state_path,
                cli.interview_profile_path(slug),
            )
        }

        with self.assertRaisesRegex(cli.OpenLearnError, "source state is unreadable"):
            application.prepare_interview_curriculum(slug, boundary="resume")

        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertFalse(cli.interview_reconciliation_journal_path(slug).exists())

    def test_reconciliation_fails_closed_on_truncated_authoritative_events(self) -> None:
        slug = self._legacy_interview_course()
        events_path = cli.topic_events_path(slug)
        events_path.write_text('{"event_type":"answer_judged"', encoding="utf-8")
        before = {
            path: path.read_bytes()
            for path in (
                cli.topic_path(slug),
                cli.topic_state_path(slug),
                cli.interview_profile_path(slug),
                events_path,
            )
        }

        with self.assertRaisesRegex(cli.OpenLearnError, "source events are malformed"):
            application.prepare_interview_curriculum(slug, boundary="resume")

        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertFalse(cli.interview_reconciliation_journal_path(slug).exists())

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
                (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_journal" else None
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

    def test_reconciliation_recovery_repairs_projection_after_state_publication(self) -> None:
        slug = self._legacy_interview_course()
        first = application.prepare_interview_curriculum(slug, boundary="resume")
        canonical = cli.load_state(slug)["interview_curriculum"]
        expected = interview_curriculum.compatibility_projection(canonical)
        cli.update_state_atomic(slug, lambda state: state.pop("interview_curriculum"))
        metadata, body = cli.parse_topic(cli.topic_path(slug).read_text(encoding="utf-8"))
        metadata["current_focus"] = "stale compatibility projection"
        cli.write_text_atomic(cli.topic_path(slug), cli.format_topic(metadata, body))
        with mock.patch(
            "openlearn.courses._reconciliation_checkpoint",
            side_effect=lambda stage: (
                (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_state" else None
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "after_state"):
                application.prepare_interview_curriculum(slug, boundary="resume")
        self.assertIn("interview_curriculum", cli._load_state_unlocked(slug))
        interrupted_metadata, _body = cli.parse_topic(
            cli.topic_path(slug).read_text(encoding="utf-8")
        )
        self.assertEqual(interrupted_metadata["current_focus"], "stale compatibility projection")
        self.assertTrue(cli.interview_reconciliation_journal_path(slug).exists())

        recovered = application.prepare_interview_curriculum(slug, boundary="resume")

        metadata, _body = cli.parse_topic(cli.topic_path(slug).read_text(encoding="utf-8"))
        for key in ("course_units", "current_unit", "current_slide", "current_focus"):
            self.assertEqual(metadata[key], expected[key])
        self.assertEqual(recovered, first)
        self.assertFalse(cli.interview_reconciliation_journal_path(slug).exists())

    def test_reconciliation_rejects_valid_json_with_poisoned_identity(self) -> None:
        slug = self._legacy_interview_course()
        with mock.patch(
            "openlearn.courses._reconciliation_checkpoint",
            side_effect=lambda stage: (
                (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_journal" else None
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "after_journal"):
                application.prepare_interview_curriculum(slug, boundary="resume")
        journal_path = cli.interview_reconciliation_journal_path(slug)
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["canonical_state"]["cursor"]["unit_id"] = "poisoned"
        cli.write_text_atomic(journal_path, json.dumps(journal, indent=2, sort_keys=True) + "\n")
        before_state = cli.topic_state_path(slug).read_bytes()

        with self.assertRaisesRegex(cli.OpenLearnError, "invalid identity"):
            application.prepare_interview_curriculum(slug, boundary="resume")

        self.assertEqual(cli.topic_state_path(slug).read_bytes(), before_state)
        self.assertTrue(journal_path.exists())

    def test_legacy_route_replay_requires_unchanged_profile(self) -> None:
        result = create_course(
            CourseCreationRequest(
                name="Legacy Replay Profile Fence",
                template_id="technical-interview-prep",
            )
        )
        slug = result.course.slug
        first = application.accept_interview_curriculum(slug, action="skip")
        cli.cmd_interview_edit(
            argparse.Namespace(topic=slug, field="target_level", value="mid"),
            output_func=lambda _text="": None,
        )

        second = application.accept_interview_curriculum(slug, action="skip")

        self.assertFalse(second["replayed"])
        self.assertNotEqual(first["receipt"]["action_id"], second["receipt"]["action_id"])
        self.assertEqual(second["receipt"]["final_revision"], 2)

    def test_route_change_retires_removed_check_and_projection_across_recovery(self) -> None:
        result = create_course(
            CourseCreationRequest(
                name="Retire Removed Check",
                template_id="technical-interview-prep",
            )
        )
        slug = result.course.slug
        accepted = application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4())
        )
        optional = next(
            item
            for item in accepted["canonical"]["route"]["skills"]
            if item["requirement"] == "optional"
        )
        pending = {
            "question": "Explain the optional technique.",
            "kind": "free_response",
            "skill_ref": optional["skill_ref"],
        }
        state = cli.load_state(slug)
        state["interview_curriculum"]["committed_check_target"] = {
            "skill_ref": optional["skill_ref"],
            "evidence_kind": "production",
            "problem_id": "problem.optional",
            "transfer_family": "optional",
        }
        state["pending_question"] = pending
        cli.write_text_atomic(
            cli.topic_state_path(slug), json.dumps(state, indent=2, sort_keys=True) + "\n"
        )
        metadata, body = cli.parse_topic(cli.topic_path(slug).read_text(encoding="utf-8"))
        metadata["pending_question"] = pending
        cli.write_text_atomic(cli.topic_path(slug), cli.format_topic(metadata, body))
        submission_id = str(uuid4())
        with mock.patch(
            "openlearn.courses._route_acceptance_checkpoint",
            side_effect=lambda stage: (
                (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_state" else None
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "after_state"):
                application.accept_interview_curriculum(
                    slug,
                    action="change",
                    changes={"optional_skill_ids": []},
                    submission_id=submission_id,
                    expected_revision=1,
                )

        application.accept_interview_curriculum(
            slug,
            action="change",
            changes={"optional_skill_ids": []},
            submission_id=submission_id,
            expected_revision=1,
        )

        repaired = cli.load_state(slug)
        self.assertNotIn("pending_question", repaired)
        self.assertNotIn("pending_question", cli.read_topic(slug).metadata)
        self.assertEqual(len(repaired["_interview_retired_checks"]), 1)
        self.assertEqual(
            repaired["_interview_retired_checks"][0]["skill_ref"],
            optional["skill_ref"],
        )

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
