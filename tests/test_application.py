from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone

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


if __name__ == "__main__":
    unittest.main()
