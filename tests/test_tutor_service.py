from __future__ import annotations

import argparse
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, mock
from uuid import uuid4

from openlearn import cli, tutor_service
from openlearn.tutor_service import (
    TutorConflictError,
    TutorOperationError,
    course_revision,
    operation_status,
    start_turn,
    submit_turn,
)


class TutorServiceTests(TestCase):
    def setUp(self) -> None:
        self.temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.home = Path(self.temp)
        self.enterContext(
            mock.patch.dict(
                "os.environ", {"OPENLEARN_HOME": str(self.home), "OPENLEARN_MOCK": "1"}, clear=False
            )
        )
        cli._CONFIG_CACHE = None
        cli.cmd_new(
            argparse.Namespace(
                topic="Web Tutor",
                goal="Learn one useful concept",
                mastery_profile="efficient",
                template="vim",
                interview_prep=False,
            ),
            output_func=lambda _text="": None,
        )

    def tearDown(self) -> None:
        cli._CONFIG_CACHE = None

    def _persist_active_turn(
        self,
        submission_id: str,
        *,
        status: str = "generating",
        updated_at: datetime | None = None,
    ) -> None:
        state = cli.load_state("web-tutor")
        internal = state.setdefault("_openlearn_internal", {})
        self.assertIsInstance(internal, dict)
        internal["active_turn"] = {
            "submission_id": submission_id,
            "status": status,
            "expected_revision": 0,
            "prompt": "Explain motions.",
            "updated_at": (updated_at or datetime.now(timezone.utc)).isoformat(),
        }
        cli.save_state("web-tutor", state)

    def test_submit_turn_returns_structured_move_and_replays(self) -> None:
        submission_id = str(uuid4())
        first = submit_turn(
            "web-tutor",
            "Explain normal mode.",
            submission_id=submission_id,
            expected_revision=0,
        )
        replay = submit_turn(
            "web-tutor",
            "This text is ignored for an idempotent replay.",
            submission_id=submission_id,
            expected_revision=0,
        )

        self.assertEqual(first, replay)
        self.assertEqual(first.status, "committed")
        self.assertIsNotNone(first.move)
        self.assertEqual(course_revision("web-tutor"), 1)
        self.assertFalse(cli.topic_turn_journal_path("web-tutor").exists())
        body = cli.read_topic("web-tutor").body
        self.assertEqual(body.count("Explain normal mode."), 1)

    def test_stale_revision_conflicts_before_mutation(self) -> None:
        with self.assertRaises(TutorConflictError):
            submit_turn(
                "web-tutor",
                "My answer",
                submission_id=str(uuid4()),
                expected_revision=7,
            )
        self.assertIsNone(cli.load_pending_learner_prompt("web-tutor"))

    def test_web_turn_does_not_launch_specialized_action(self) -> None:
        with mock.patch.object(cli, "orchestrate_tutor_coding_drill") as launch:
            submit_turn(
                "web-tutor",
                "Give me a coding activity.",
                submission_id=str(uuid4()),
                expected_revision=0,
            )
        launch.assert_not_called()

    def test_async_turn_exposes_saved_operation_until_commit(self) -> None:
        submission_id = str(uuid4())
        pending = start_turn(
            "web-tutor",
            "Explain motions.",
            submission_id=submission_id,
            expected_revision=0,
        )

        self.assertEqual(pending.status, "saved")
        for _attempt in range(100):
            result = operation_status("web-tutor", submission_id)
            if result is not None and result.status == "committed":
                break
            time.sleep(0.01)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "committed")

    def test_failed_turn_can_retry_with_same_submission(self) -> None:
        submission_id = str(uuid4())
        original = cli.ask_topic
        with mock.patch.object(cli, "ask_topic", side_effect=RuntimeError("provider down")):
            with self.assertRaises(TutorOperationError):
                submit_turn(
                    "web-tutor",
                    "Explain registers.",
                    submission_id=submission_id,
                    expected_revision=0,
                )
        failed = operation_status("web-tutor", submission_id)
        self.assertIsNotNone(failed)
        self.assertEqual(failed.status, "retryable_error")

        with mock.patch.object(cli, "ask_topic", wraps=original):
            retried = submit_turn(
                "web-tutor",
                "Explain registers.",
                submission_id=submission_id,
                expected_revision=0,
            )
        self.assertEqual(retried.status, "committed")

    def test_status_converts_restart_orphan_to_retryable_receipt(self) -> None:
        submission_id = str(uuid4())
        self._persist_active_turn(submission_id)

        with mock.patch.object(cli, "ask_topic") as ask_topic:
            recovered = operation_status("web-tutor", submission_id)

        ask_topic.assert_not_called()
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.status, "retryable_error")
        self.assertEqual(recovered.error_code, "operation_interrupted")
        internal = cli.load_state("web-tutor")["_openlearn_internal"]
        self.assertIsNone(internal["active_turn"])
        self.assertEqual(
            internal["turn_results"][submission_id]["status"], "retryable_error"
        )

    def test_start_recovers_orphan_before_same_submission_can_retry(self) -> None:
        submission_id = str(uuid4())
        self._persist_active_turn(submission_id, status="saved")

        with mock.patch.object(cli, "ask_topic") as ask_topic:
            recovered = start_turn(
                "web-tutor",
                "Explain motions.",
                submission_id=submission_id,
                expected_revision=0,
            )

        ask_topic.assert_not_called()
        self.assertEqual(recovered.status, "retryable_error")
        retried = submit_turn(
            "web-tutor",
            "Explain motions.",
            submission_id=submission_id,
            expected_revision=0,
        )
        self.assertEqual(retried.status, "committed")

    def test_status_does_not_expire_live_operation_from_persisted_timestamp(self) -> None:
        submission_id = str(uuid4())
        self._persist_active_turn(
            submission_id,
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=4),
        )

        with mock.patch("openlearn.tutor_service._future_active", return_value=True):
            recovered = operation_status("web-tutor", submission_id)

        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.status, "generating")
        self.assertIsNone(recovered.error_code)

    def test_polling_keeps_blocked_live_worker_active_until_provider_returns(self) -> None:
        submission_id = str(uuid4())
        provider_started = threading.Event()
        release_provider = threading.Event()

        def blocked_provider(*_args: object, **_kwargs: object) -> str:
            provider_started.set()
            release_provider.wait(timeout=1)
            raise RuntimeError("provider stayed blocked")

        with mock.patch.object(cli, "ask_topic", side_effect=blocked_provider):
            start_turn(
                "web-tutor",
                "Explain motions.",
                submission_id=submission_id,
                expected_revision=0,
            )
            self.assertTrue(provider_started.wait(timeout=1))
            with tutor_service._FUTURES_GUARD:
                future = tutor_service._FUTURES[("web-tutor", submission_id)]
            state = cli.load_state("web-tutor")
            active = state["_openlearn_internal"]["active_turn"]
            active["updated_at"] = (
                datetime.now(timezone.utc) - timedelta(minutes=4)
            ).isoformat()
            cli.save_state("web-tutor", state)

            current = operation_status("web-tutor", submission_id)

            self.assertIsNotNone(current)
            self.assertIn(current.status, {"saved", "generating"})
            self.assertFalse(future.done())
            release_provider.set()
            with self.assertRaises(TutorOperationError):
                future.result(timeout=1)

    def test_live_operation_exposes_only_real_persisted_status(self) -> None:
        submission_id = str(uuid4())
        self._persist_active_turn(submission_id, status="generating")

        with mock.patch("openlearn.tutor_service._future_active", return_value=True):
            current = operation_status("web-tutor", submission_id)

        self.assertIsNotNone(current)
        self.assertEqual(current.status, "generating")

    def test_second_submission_conflicts_while_turn_is_live(self) -> None:
        active_submission = str(uuid4())
        self._persist_active_turn(active_submission)

        with mock.patch("openlearn.tutor_service._future_active", return_value=True):
            with self.assertRaises(TutorConflictError):
                submit_turn(
                    "web-tutor",
                    "A second response.",
                    submission_id=str(uuid4()),
                    expected_revision=0,
                )
        self.assertIsNone(cli.load_pending_learner_prompt("web-tutor"))

    def test_cli_turn_advances_shared_revision(self) -> None:
        cli.ask_topic("web-tutor", "Continue.", output_func=lambda _text="": None)

        self.assertEqual(course_revision("web-tutor"), 1)
        with self.assertRaises(TutorConflictError):
            submit_turn(
                "web-tutor",
                "A stale browser response.",
                submission_id=str(uuid4()),
                expected_revision=0,
            )

    def test_operation_state_update_cannot_overwrite_concurrent_cli_state(self) -> None:
        entered_update = threading.Event()
        release_update = threading.Event()
        writer_done = threading.Event()
        original_internal_state = tutor_service._internal_state

        def blocked_internal_state(state: dict[str, object]) -> dict[str, object]:
            if threading.current_thread().name == "operation-writer":
                entered_update.set()
                release_update.wait(timeout=2)
            return original_internal_state(state)

        with mock.patch.object(
            tutor_service, "_internal_state", side_effect=blocked_internal_state
        ):
            operation_writer = threading.Thread(
                name="operation-writer",
                target=tutor_service._save_operation,
                kwargs={
                    "slug": "web-tutor",
                    "submission_id": str(uuid4()),
                    "status": "saved",
                    "expected_revision": 0,
                    "prompt": "Saved response",
                },
            )
            operation_writer.start()
            self.assertTrue(entered_update.wait(timeout=1))

            def write_cli_state() -> None:
                cli.update_state_atomic(
                    "web-tutor", lambda state: state.__setitem__("known", ["motions"])
                )
                writer_done.set()

            cli_writer = threading.Thread(target=write_cli_state)
            cli_writer.start()
            self.assertFalse(writer_done.wait(timeout=0.05))
            release_update.set()
            operation_writer.join(timeout=2)
            cli_writer.join(timeout=2)

        self.assertFalse(operation_writer.is_alive())
        self.assertFalse(cli_writer.is_alive())
        self.assertEqual(cli.load_state("web-tutor")["known"], ["motions"])

    def test_cli_turn_fences_web_commit_at_durable_revision_boundary(self) -> None:
        submission_id = str(uuid4())
        web_commit_ready = threading.Event()
        release_web_commit = threading.Event()
        original_commit = cli._commit_projected_turn

        def controlled_commit(*args: object, **kwargs: object) -> None:
            if threading.current_thread().name.startswith("openlearn-tutor"):
                web_commit_ready.set()
                release_web_commit.wait(timeout=2)
            original_commit(*args, **kwargs)

        with mock.patch.object(cli, "_commit_projected_turn", new=controlled_commit):
            pending = start_turn(
                "web-tutor",
                "Web response that must be fenced.",
                submission_id=submission_id,
                expected_revision=0,
            )
            self.assertEqual(pending.status, "saved")
            self.assertTrue(web_commit_ready.wait(timeout=1))

            cli.ask_topic(
                "web-tutor",
                "CLI response wins this revision.",
                output_func=lambda _text="": None,
            )
            release_web_commit.set()
            with tutor_service._FUTURES_GUARD:
                future = tutor_service._FUTURES[("web-tutor", submission_id)]
            with self.assertRaises(TutorConflictError):
                future.result(timeout=2)

        result = operation_status("web-tutor", submission_id)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "conflict")
        self.assertEqual(course_revision("web-tutor"), 1)
        body = cli.read_topic("web-tutor").body
        self.assertIn("CLI response wins this revision.", body)
        self.assertNotIn("Web response that must be fenced.", body)
