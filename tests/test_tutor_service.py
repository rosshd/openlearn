from __future__ import annotations

import argparse
import copy
import hashlib
import json
import multiprocessing
import os
import queue
import threading
import time
from concurrent.futures import wait
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, mock
from uuid import uuid4

from openlearn import application, cli, config, interview_curriculum, providers, tutor_service
from openlearn.tutor_service import (
    TutorConflictError,
    TutorOperationError,
    course_revision,
    operation_status,
    start_turn,
    submit_turn,
)


def _reserve_generic_turn_process(
    home: str,
    submission_id: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    os.environ["OPENLEARN_HOME"] = home
    os.environ["OPENLEARN_MOCK"] = "1"
    cli._CONFIG_CACHE = None
    ready.wait(timeout=5)
    try:
        _revision, replay = tutor_service._prepare_turn(
            "web-tutor",
            f"answer {submission_id}",
            submission_id,
            0,
            "answer",
            "chat",
            None,
            None,
            None,
            None,
        )
        results.put(("reserved", replay is None))
    except TutorConflictError:
        results.put(("conflict", True))
    release.wait(timeout=5)


def _retry_follow_up_process(
    home: str,
    submission_id: str,
    ready: multiprocessing.queues.Queue,
    start: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
    provider_calls: multiprocessing.queues.Queue,
    results: multiprocessing.queues.Queue,
) -> None:
    os.environ["OPENLEARN_HOME"] = home
    os.environ["OPENLEARN_MOCK"] = "1"
    cli._CONFIG_CACHE = None
    credentials = config.ProviderCredentials(
        base_url="https://openrouter.ai/api/v1",
        model="test/model",
        api_key="test-key",
        verified=True,
    )
    config.provider_is_configured = lambda **_kwargs: True
    config.effective_provider_credentials = lambda: credentials

    def delayed_completion(*_args: object, **_kwargs: object) -> str:
        provider_calls.put(os.getpid())
        if not release.wait(timeout=5):
            raise providers.ProviderError("test_release_timeout")
        return json.dumps(
            {
                "title": "Vim Plugin Engineering",
                "goal": "Build maintainable Vim plugins around deliberate workflows.",
            }
        )

    providers.chat_completion = delayed_completion
    ready.put(os.getpid())
    if not start.wait(timeout=5):
        results.put(("error", "start_timeout"))
        return
    try:
        proposal = tutor_service.retry_follow_up_proposal("web-tutor", submission_id)
    except Exception as exc:  # pragma: no cover - returned for parent-process assertion
        results.put(("error", repr(exc)))
    else:
        results.put((proposal.state, proposal.replayed))


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
        with tutor_service._FUTURES_GUARD:
            futures = tuple(tutor_service._FUTURES.values())
        if futures:
            _done, unfinished = wait(futures, timeout=5)
            self.assertFalse(unfinished, "tutor workers must finish before home cleanup")
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

    def _ready_provider(self):
        credentials = config.ProviderCredentials(
            base_url="https://openrouter.ai/api/v1",
            model="test/model",
            api_key="test-key",
            verified=True,
        )
        return mock.patch.multiple(
            config,
            provider_is_configured=mock.DEFAULT,
            effective_provider_credentials=mock.DEFAULT,
        ), credentials

    def _complete_follow_up_source(self) -> None:
        topic = cli.read_topic("web-tutor")
        metadata = dict(topic.metadata)
        metadata["course_completed"] = True
        cli.write_topic(topic.path, metadata, topic.body)

    def _persist_pending_follow_up(
        self, submission_id: str, *, abandoned_claim: bool = False
    ) -> None:
        snapshot = application.course("web-tutor")
        generation = cli.current_topic_generation("web-tutor")
        self.assertIsNotNone(generation)
        record = {
            "schema_version": tutor_service._FOLLOW_UP_SCHEMA_VERSION,
            "source_slug": "web-tutor",
            "source_generation": generation,
            "source_title": snapshot.card.title,
            "source_goal": snapshot.card.goal,
            "submission_id": submission_id,
            "payload_hash": tutor_service._follow_up_payload_hash(
                "web-tutor", str(generation), "Build editor tools", ()
            ),
            "state": "pending",
            "interests": "Build editor tools",
            "weak_areas": [],
            "title": "",
            "goal": "",
            "error_code": None,
            "error_message": None,
            "created_slug": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if abandoned_claim:
            record["claim"] = {
                "token": str(uuid4()),
                "owner_id": str(uuid4()),
                "owner_pid": os.getpid(),
                "expires_at": (
                    datetime.now(timezone.utc) + tutor_service._FOLLOW_UP_CLAIM_TIMEOUT
                ).isoformat(),
            }
        self.assertIsNone(tutor_service._reserve_follow_up_record(record))

    def test_follow_up_proposal_is_durable_idempotent_and_confirmation_creates_once(
        self,
    ) -> None:
        self._complete_follow_up_source()
        submission_id = str(uuid4())
        config_patch, credentials = self._ready_provider()
        with config_patch as configured, mock.patch.object(
            providers,
            "chat_completion",
            return_value=json.dumps(
                {
                    "title": "Advanced Vim Automation",
                    "goal": "Build reliable editor automation with reusable commands.",
                }
            ),
        ) as completion:
            configured["provider_is_configured"].return_value = True
            configured["effective_provider_credentials"].return_value = credentials
            first = tutor_service.request_follow_up_proposal(
                "web-tutor",
                interests="Automate repeated editing work",
                submission_id=submission_id,
            )
            replay = tutor_service.request_follow_up_proposal(
                "web-tutor",
                interests="Automate repeated editing work",
                submission_id=submission_id,
            )

        self.assertEqual(first.state, "ready")
        self.assertTrue(replay.replayed)
        completion.assert_called_once()
        self.assertEqual(len(application.dashboard().courses), 1)

        created = tutor_service.confirm_follow_up_proposal("web-tutor", submission_id)
        confirmed_replay = tutor_service.confirm_follow_up_proposal(
            "web-tutor", submission_id
        )

        self.assertTrue(created.created)
        self.assertFalse(confirmed_replay.created)
        self.assertEqual(created.course_slug, confirmed_replay.course_slug)
        self.assertEqual(len(application.dashboard().courses), 2)

    def test_follow_up_proposal_requires_ready_provider_before_reserving(self) -> None:
        self._complete_follow_up_source()
        submission_id = str(uuid4())
        with mock.patch.object(config, "provider_is_configured", return_value=False):
            with self.assertRaises(tutor_service.FollowUpProviderNotReadyError):
                tutor_service.request_follow_up_proposal(
                    "web-tutor",
                    interests="Advanced motions",
                    submission_id=submission_id,
                )

        self.assertIsNone(tutor_service.follow_up_proposal_status("web-tutor", submission_id))

    def test_follow_up_proposal_failure_is_retryable_without_duplicate_operation(self) -> None:
        self._complete_follow_up_source()
        submission_id = str(uuid4())
        config_patch, credentials = self._ready_provider()
        with config_patch as configured, mock.patch.object(
            providers,
            "chat_completion",
            side_effect=[
                providers.ProviderError("provider_unreachable"),
                json.dumps(
                    {
                        "title": "Vim Internals",
                        "goal": "Understand and extend Vim's editing model.",
                    }
                ),
            ],
        ) as completion:
            configured["provider_is_configured"].return_value = True
            configured["effective_provider_credentials"].return_value = credentials
            failed = tutor_service.request_follow_up_proposal(
                "web-tutor",
                interests="Understand the editor deeply",
                submission_id=submission_id,
            )
            retried = tutor_service.retry_follow_up_proposal("web-tutor", submission_id)

        self.assertEqual(failed.state, "error")
        self.assertEqual(failed.error_code, "provider_unavailable")
        self.assertEqual(retried.state, "ready")
        self.assertEqual(completion.call_count, 2)

    def test_follow_up_proposal_exposes_pending_and_coalesces_duplicate_clicks(
        self,
    ) -> None:
        self._complete_follow_up_source()
        submission_id = str(uuid4())
        entered = threading.Event()
        release = threading.Event()
        results: list[application.FollowUpProposal] = []

        def delayed_completion(*_args, **_kwargs) -> str:
            entered.set()
            self.assertTrue(release.wait(timeout=2))
            return json.dumps(
                {
                    "title": "Vim Plugin Engineering",
                    "goal": "Build maintainable Vim plugins around deliberate workflows.",
                }
            )

        config_patch, credentials = self._ready_provider()
        with config_patch as configured, mock.patch.object(
            providers, "chat_completion", side_effect=delayed_completion
        ) as completion:
            configured["provider_is_configured"].return_value = True
            configured["effective_provider_credentials"].return_value = credentials
            worker = threading.Thread(
                target=lambda: results.append(
                    tutor_service.request_follow_up_proposal(
                        "web-tutor",
                        interests="Build editor tools",
                        submission_id=submission_id,
                    )
                )
            )
            worker.start()
            self.assertTrue(entered.wait(timeout=2))

            pending = tutor_service.follow_up_proposal_status(
                "web-tutor", submission_id
            )
            duplicate = tutor_service.request_follow_up_proposal(
                "web-tutor",
                interests="Build editor tools",
                submission_id=submission_id,
            )
            release.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertEqual(pending.state, "pending")
        self.assertEqual(duplicate.state, "pending")
        self.assertTrue(duplicate.replayed)
        self.assertEqual(results[0].state, "ready")
        completion.assert_called_once()

    def test_follow_up_retry_atomically_coalesces_concurrent_provider_calls(self) -> None:
        self._complete_follow_up_source()
        submission_id = str(uuid4())
        entered = threading.Event()
        release = threading.Event()
        retries: list[application.FollowUpProposal] = []
        provider_calls = 0

        def delayed_retry(*_args, **_kwargs) -> str:
            nonlocal provider_calls
            provider_calls += 1
            if provider_calls == 1:
                raise providers.ProviderError("provider_unreachable")
            entered.set()
            self.assertTrue(release.wait(timeout=2))
            return json.dumps(
                {
                    "title": "Vim Plugin Engineering",
                    "goal": "Build maintainable Vim plugins around deliberate workflows.",
                }
            )

        config_patch, credentials = self._ready_provider()
        with config_patch as configured, mock.patch.object(
            providers,
            "chat_completion",
            side_effect=delayed_retry,
        ) as completion:
            configured["provider_is_configured"].return_value = True
            configured["effective_provider_credentials"].return_value = credentials
            failed = tutor_service.request_follow_up_proposal(
                "web-tutor",
                interests="Build editor tools",
                submission_id=submission_id,
            )
            self.assertEqual(failed.state, "error")

            worker = threading.Thread(
                target=lambda: retries.append(
                    tutor_service.retry_follow_up_proposal(
                        "web-tutor", submission_id
                    )
                )
            )
            worker.start()
            self.assertTrue(entered.wait(timeout=2))
            duplicate = tutor_service.retry_follow_up_proposal(
                "web-tutor", submission_id
            )
            release.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(duplicate.state, "pending")
        self.assertTrue(duplicate.replayed)
        self.assertEqual(retries[0].state, "ready")
        self.assertEqual(completion.call_count, 2)

    def test_persisted_pending_follow_up_can_resume_after_restart(self) -> None:
        self._complete_follow_up_source()
        submission_id = str(uuid4())
        self._persist_pending_follow_up(submission_id, abandoned_claim=True)
        with tutor_service._FOLLOW_UP_RUNNING_GUARD:
            tutor_service._FOLLOW_UP_RUNNING.clear()
        config_patch, credentials = self._ready_provider()

        with config_patch as configured, mock.patch.object(
            providers,
            "chat_completion",
            return_value=json.dumps(
                {
                    "title": "Vim Plugin Engineering",
                    "goal": "Build maintainable Vim plugins around deliberate workflows.",
                }
            ),
        ) as completion:
            configured["provider_is_configured"].return_value = True
            configured["effective_provider_credentials"].return_value = credentials
            resumed = tutor_service.retry_follow_up_proposal(
                "web-tutor", submission_id
            )

        self.assertEqual(resumed.state, "ready")
        completion.assert_called_once()

    def test_persisted_pending_follow_up_has_one_cross_process_provider_owner(
        self,
    ) -> None:
        self._complete_follow_up_source()
        submission_id = str(uuid4())
        self._persist_pending_follow_up(submission_id)
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        release = context.Event()
        ready = context.Queue()
        provider_calls = context.Queue()
        results = context.Queue()
        processes = [
            context.Process(
                target=_retry_follow_up_process,
                args=(
                    str(self.home),
                    submission_id,
                    ready,
                    start,
                    release,
                    provider_calls,
                    results,
                ),
            )
            for _index in range(2)
        ]

        try:
            for process in processes:
                process.start()
            for _process in processes:
                ready.get(timeout=5)
            start.set()
            provider_calls.get(timeout=5)
            with self.assertRaises(queue.Empty):
                provider_calls.get(timeout=0.5)
            release.set()
            outcomes = [results.get(timeout=5) for _process in processes]
        finally:
            release.set()
            for process in processes:
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)

        self.assertNotIn("error", {outcome[0] for outcome in outcomes})
        self.assertEqual({outcome[0] for outcome in outcomes}, {"pending", "ready"})
        with self.assertRaises(queue.Empty):
            provider_calls.get_nowait()
        self.assertEqual(
            tutor_service.follow_up_proposal_status("web-tutor", submission_id).state,
            "ready",
        )

    def test_stale_follow_up_claim_cannot_publish_over_new_owner(self) -> None:
        self._complete_follow_up_source()
        submission_id = str(uuid4())
        self._persist_pending_follow_up(submission_id)
        first_record, first_token = tutor_service._claim_follow_up_record(
            "web-tutor", submission_id, "first-owner"
        )
        self.assertIsNotNone(first_token)
        second_record, second_token = tutor_service._claim_follow_up_record(
            "web-tutor", submission_id, "second-owner"
        )
        self.assertIsNotNone(second_token)
        assert first_token is not None
        assert second_token is not None

        stale_result = tutor_service._finish_claimed_follow_up_record(
            first_record,
            first_token,
            {"state": "ready", "title": "Stale title", "goal": "Stale goal"},
        )
        current = tutor_service.follow_up_proposal_status("web-tutor", submission_id)

        self.assertTrue(stale_result.replayed)
        self.assertEqual(stale_result.state, "pending")
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.state, "pending")
        committed = tutor_service._finish_claimed_follow_up_record(
            second_record,
            second_token,
            {"state": "ready", "title": "Current title", "goal": "Current goal"},
        )
        self.assertEqual(committed.state, "ready")
        self.assertEqual(committed.title, "Current title")

    def _create_interview_course(self, topic: str = "Interview Curriculum") -> str:
        cli.cmd_new(
            argparse.Namespace(
                topic=topic,
                goal="Prepare for technical interviews",
                mastery_profile="efficient",
                template=None,
                interview_prep=True,
            ),
            output_func=lambda _text="": None,
        )
        slug = cli.slugify(topic)
        application.prepare_interview_curriculum(slug, boundary="resume")
        return slug

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
            "Explain normal mode.",
            submission_id=submission_id,
            expected_revision=0,
        )

        self.assertEqual(first, replay)
        self.assertEqual(first.status, "committed")
        self.assertIsNotNone(first.move)
        self.assertEqual(course_revision("web-tutor"), 1)
        self.assertFalse(cli.topic_turn_journal_path("web-tutor").exists())
        receipt = cli.load_state("web-tutor")["_openlearn_internal"]["turn_results"][submission_id]
        self.assertNotIn("preview", receipt)
        body = cli.read_topic("web-tutor").body
        self.assertEqual(body.count("Explain normal mode."), 1)

    def test_same_submission_rejects_a_different_payload(self) -> None:
        submission_id = str(uuid4())
        submit_turn(
            "web-tutor",
            "Explain normal mode.",
            submission_id=submission_id,
            expected_revision=0,
        )

        with self.assertRaises(TutorConflictError, msg="idempotency payload changed"):
            submit_turn(
                "web-tutor",
                "Explain insert mode instead.",
                submission_id=submission_id,
                expected_revision=0,
            )

    def test_interview_navigation_reserves_then_commits_two_revisions(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())
        entered_provider = threading.Event()
        release_provider = threading.Event()
        original = cli.ask_topic

        def blocked(*args: object, **kwargs: object) -> str:
            entered_provider.set()
            release_provider.wait(timeout=3)
            return original(*args, **kwargs)

        with mock.patch.object(cli, "ask_topic", side_effect=blocked):
            pending = start_turn(
                slug,
                "Continue to the next concept.",
                intent="navigation",
                submission_id=submission_id,
                expected_revision=0,
            )
            self.assertEqual(pending.status, "saved")
            self.assertTrue(entered_provider.wait(timeout=3))
            reserved = cli.load_state(slug)
            self.assertEqual(
                reserved["_openlearn_internal"]["course_revision"], 1
            )
            active = reserved["interview_curriculum"]["active_operation"]
            self.assertEqual(active["submission_id"], submission_id)
            self.assertEqual(active["status"], "reserved")
            self.assertEqual(
                active["target"]["skill_ref"]["skill_id"],
                "concept.arrays-strings",
            )
            release_provider.set()
            with tutor_service._FUTURES_GUARD:
                future = tutor_service._FUTURES[(slug, submission_id)]
            result = future.result(timeout=3)

        self.assertEqual(result.status, "committed")
        committed = cli.load_state(slug)
        self.assertEqual(committed["_openlearn_internal"]["course_revision"], 2)
        self.assertIsNone(committed["interview_curriculum"]["active_operation"])
        self.assertIn(
            "concept.arrays-strings",
            committed["interview_curriculum"]["evidence"]["exposed"],
        )
        permanent = committed["_turn_receipts"][
            f"operation_{submission_id.replace('-', '')}"
        ]
        self.assertEqual(permanent["status"], "committed")
        self.assertNotIn("content", permanent["result"]["move"])
        self.assertNotIn(
            result.move.content,
            json.dumps(permanent, sort_keys=True),
        )
        events = cli.load_event_log(cli.topic_events_path(slug))
        progression_events = [
            event
            for event in events
            if event.get("event_type") == "interview_curriculum_advanced"
            and event.get("data", {}).get("submission_id") == submission_id
        ]
        self.assertEqual(len(progression_events), 1)
        self.assertEqual(
            progression_events[0]["data"]["skill_ref"]["skill_id"],
            "concept.arrays-strings",
        )
        internal = committed["_openlearn_internal"]
        internal["turn_results"] = {}
        cli.save_state(slug, committed)
        old = tutor_service.operation_result(slug, submission_id)
        self.assertIsNotNone(old)
        self.assertEqual(old.status, "committed")
        self.assertEqual(old.move.content, result.move.content)

    def test_interview_generation_is_bound_to_application_owned_target(self) -> None:
        slug = self._create_interview_course()
        captured: dict[str, str] = {}

        def response(_model: str, system: str, _user: str) -> str:
            captured["system"] = system
            return (
                "**Lesson:**\nUse indexed traversal to inspect each array position while "
                "stating the expected output. In Python, enumerate keeps the index and value "
                "together."
            )

        with mock.patch.object(cli, "call_openai", new=response):
            result = submit_turn(
                slug,
                "Continue to the next concept.",
                intent="navigation",
                submission_id=str(uuid4()),
                expected_revision=0,
            )

        system = captured["system"]
        normalized_system = " ".join(system.split())
        self.assertIn("coding-interview@1.0.0@interview-mastery-v1@concept.arrays-strings", system)
        self.assertIn("Arrays and strings", system)
        self.assertIn("Reason about indexed sequences", system)
        self.assertIn("Depth mode: learn", system)
        self.assertIn("Evidence goal:", system)
        self.assertIn("enumerate", system)
        self.assertIn("Clarify input and output", system)
        self.assertIn(
            "Assume the learner does not know the technical vocabulary",
            normalized_system,
        )
        self.assertIn("Define each new technical term in plain language", normalized_system)
        self.assertIn(
            "Never copy the formal skill description into the lesson",
            normalized_system,
        )
        self.assertNotIn("choose the next topic", system.casefold())
        receipt = next(
            value
            for key, value in cli.load_state(slug)["_turn_receipts"].items()
            if key.startswith("operation_")
        )
        self.assertEqual(receipt["target"]["skill_ref"]["skill_id"], "concept.arrays-strings")
        self.assertEqual(receipt["target"]["skill_label"], "Arrays and strings")
        self.assertIsNotNone(result.move)

    def test_conflicting_interview_target_uses_fallback_without_cursor_drift(self) -> None:
        slug = self._create_interview_course()
        sid = str(uuid4())
        adversarial = (
            "**Lesson:**\nLet's move to Dynamic programming and learn a DP table."
        )
        def conflicting(_model: str, _system: str, _user: str) -> str:
            return adversarial

        with mock.patch.object(cli, "call_openai", new=conflicting):
            result = submit_turn(
                slug,
                "Continue to the next concept.",
                intent="navigation",
                submission_id=sid,
                expected_revision=0,
            )

        self.assertIsNotNone(result.move)
        self.assertIn("Arrays and strings", result.move.content)
        self.assertNotIn("Dynamic programming", result.move.content)
        state = cli.load_state(slug)
        self.assertIn("concept.arrays-strings", state["interview_curriculum"]["evidence"]["exposed"])
        self.assertEqual(
            state["_turn_receipts"][f"operation_{sid.replace('-', '')}"]["target"]["skill_ref"]["skill_id"],
            "concept.arrays-strings",
        )

    def test_interview_check_and_judgment_keep_exact_stable_skill_identity(self) -> None:
        slug = self._create_interview_course()

        def check(_model: str, _system: str, _user: str) -> str:
            return (
                "**Lesson:**\nAn array boundary uses len(values) as an exclusive upper "
                "bound, so valid indices stop at len(values) - 1.\n\n"
                "**Check:**\nExplain how indexed traversal finds an array boundary."
            )

        with mock.patch.object(cli, "call_openai", new=check):
            submit_turn(
                slug,
                "Continue to the next concept.",
                intent="navigation",
                submission_id=str(uuid4()),
                expected_revision=0,
            )

        pending = cli.load_state(slug)["pending_question"]
        expected_ref = {
            "graph_id": "coding-interview",
            "graph_version": "1.0.0",
            "mastery_policy_version": "interview-mastery-v1",
            "skill_id": "concept.arrays-strings",
        }
        self.assertEqual(pending["concept_id"], "concept.arrays-strings")
        self.assertEqual(pending["curriculum_target"], expected_ref)
        self.assertEqual(pending["curriculum_evidence_kind"], "explanation")

        def judged(_model: str, system: str, _user: str) -> str:
            if "calibrated JSON judge" in system:
                return json.dumps(
                    {
                        "message_kind": "answer",
                        "last_answer_status": "correct",
                        "answer_score": 1.0,
                        "answer_kind": "production",
                        "is_transfer": True,
                        "known_add": ["pattern.dynamic-programming"],
                        "current_focus": "Dynamic programming",
                    }
                )
            return "**Feedback:**\nYour boundary explanation is correct."

        with mock.patch.object(cli, "call_openai", new=judged):
            submit_turn(
                slug,
                "I compare each index with len(values) before accessing it.",
                intent="answer",
                submission_id=str(uuid4()),
                expected_revision=2,
            )

        updated = cli.load_state(slug)
        self.assertIn("concept.arrays-strings", updated["concept_attempts"])
        self.assertNotIn("pattern.dynamic-programming", updated["concept_attempts"])
        canonical_evidence = updated["interview_curriculum"]["evidence"]
        self.assertEqual(
            canonical_evidence["answer_evidence"][-1]["skill_ref"], expected_ref
        )
        self.assertEqual(
            canonical_evidence["answer_evidence"][-1]["kinds"],
            ["explanation"],
        )
        self.assertNotIn("pattern.dynamic-programming", canonical_evidence["ready"])
        judged_events = [
            event
            for event in cli.load_event_log(cli.topic_events_path(slug))
            if event.get("event_type") == "answer_judged"
        ]
        self.assertEqual(judged_events[-1]["data"]["skill_ref"], expected_ref)

    def test_wrong_interview_answer_then_continue_remains_on_exact_target(self) -> None:
        slug = self._create_interview_course()

        def provider(_model: str, system: str, _user: str) -> str:
            if "calibrated JSON judge" in system:
                return json.dumps(
                    {
                        "message_kind": "answer",
                        "last_answer_status": "needs_work",
                        "answer_score": 0.1,
                        "answer_kind": "production",
                        "is_transfer": False,
                        "weak_spots_add": ["Dynamic programming"],
                    }
                )
            if "Pending question to grade:" in system and "Stored question:" in system:
                return (
                    "**Feedback:**\nThe boundary reasoning needs another attempt.\n\n"
                    "**Check:**\nExplain how indexed traversal avoids crossing an array boundary."
                )
            return (
                "**Lesson:**\nUse len(values) as the exclusive upper bound when traversing "
                "an indexed sequence.\n\n"
                "**Check:**\nExplain how indexed traversal avoids crossing an array boundary."
            )

        with mock.patch.object(cli, "call_openai", new=provider):
            submit_turn(
                slug,
                "Continue to the next concept.",
                intent="navigation",
                submission_id=str(uuid4()),
                expected_revision=0,
            )
            submit_turn(
                slug,
                "I would keep indexing until an exception occurs.",
                intent="answer",
                submission_id=str(uuid4()),
                expected_revision=2,
            )
            follow_up_id = str(uuid4())
            submit_turn(
                slug,
                "Continue.",
                intent="navigation",
                submission_id=follow_up_id,
                expected_revision=3,
            )

        state = cli.load_state(slug)
        canonical = state["interview_curriculum"]
        self.assertIn("concept.arrays-strings", canonical["evidence"]["weak"])
        self.assertNotIn("pattern.dynamic-programming", canonical["evidence"]["weak"])
        receipt = state["_turn_receipts"][
            f"operation_{follow_up_id.replace('-', '')}"
        ]
        self.assertEqual(
            receipt["target"]["skill_ref"]["skill_id"],
            "concept.arrays-strings",
        )

    def test_remediation_check_keeps_pinned_target_until_correct_answer(self) -> None:
        slug = self._create_interview_course()
        judgments = iter(("needs_work", "correct"))

        def provider(_model: str, system: str, _user: str) -> str:
            if "calibrated JSON judge" in system:
                status = next(judgments)
                return json.dumps(
                    {
                        "message_kind": "answer",
                        "last_answer_status": status,
                        "answer_score": 0.1 if status == "needs_work" else 1.0,
                        "answer_kind": "production",
                        "is_transfer": False,
                    }
                )
            if "Pending question to grade:" in system:
                return (
                    "**Feedback:**\nUse the length as the exclusive upper bound.\n\n"
                    "**Check:**\nExplain how indexed traversal avoids crossing an array boundary."
                )
            return (
                "**Lesson:**\nAn indexed traversal must stop before len(values), which is "
                "outside the valid index range.\n\n"
                "**Check:**\nExplain how indexed traversal finds an array boundary."
            )

        with mock.patch.object(cli, "call_openai", new=provider):
            submit_turn(
                slug,
                "Continue to the next concept.",
                intent="navigation",
                submission_id=str(uuid4()),
                expected_revision=0,
            )
            initial = copy.deepcopy(cli.load_state(slug)["pending_question"])
            submit_turn(
                slug,
                "I would keep indexing until Python raises an exception.",
                intent="answer",
                submission_id=str(uuid4()),
                expected_revision=2,
            )
            remediated = copy.deepcopy(cli.load_state(slug)["pending_question"])
            submit_turn(
                slug,
                "I stop before len(values), so the largest valid index is len(values) - 1.",
                intent="answer",
                submission_id=str(uuid4()),
                expected_revision=3,
            )

        attribution_keys = (
            "curriculum_target",
            "curriculum_evidence_kind",
            "curriculum_problem_id",
            "curriculum_transfer_family",
        )
        self.assertEqual(
            {key: remediated[key] for key in attribution_keys},
            {key: initial[key] for key in attribution_keys},
        )
        evidence = cli.load_state(slug)["interview_curriculum"]["evidence"][
            "answer_evidence"
        ]
        self.assertEqual(len(evidence), 2)
        self.assertEqual(evidence[0]["skill_ref"], initial["curriculum_target"])
        self.assertEqual(evidence[1]["skill_ref"], initial["curriculum_target"])
        self.assertEqual(evidence[0]["policy_record"]["outcome"], "fail")
        self.assertEqual(evidence[1]["policy_record"]["outcome"], "pass")
        for record in evidence:
            self.assertEqual(
                record["policy_record"]["problem_id"],
                initial["curriculum_problem_id"],
            )
            self.assertEqual(
                record["policy_record"]["transfer_family"],
                initial["curriculum_transfer_family"],
            )

    def test_recovered_internal_reasoning_is_replaced_by_target_fallback(self) -> None:
        slug = self._create_interview_course()
        sid = str(uuid4())
        tutor_service._reserve_interview_progression(
            slug,
            "Continue to the next concept.",
            sid,
            0,
            "navigation",
            "chat",
            progression_intent="continue",
        )

        def save_generated(state: dict[str, object]) -> None:
            canonical = state["interview_curriculum"]
            canonical["active_operation"]["status"] = "generated"
            canonical["active_operation"]["generated_response"] = (
                "<think>I should switch topics.</think>\n**Lesson:**\nArrays store values."
            )
            state["_openlearn_internal"]["active_turn"]["status"] = "generated"

        cli.update_state_atomic(slug, save_generated)
        with mock.patch.object(
            cli,
            "call_openai",
            side_effect=AssertionError("stored generated output must not call provider"),
        ):
            result = tutor_service.resume_interview_progression(slug)

        self.assertIsNotNone(result.move)
        self.assertIn("Arrays and strings", result.move.content)
        self.assertNotIn("think", result.move.content.casefold())

    def test_interview_provider_failure_retries_the_same_reserved_target(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())
        original = cli.ask_topic
        with mock.patch.object(cli, "ask_topic", side_effect=RuntimeError("provider down")):
            with self.assertRaises(TutorOperationError):
                submit_turn(
                    slug,
                    "Continue to the next concept.",
                    intent="navigation",
                    submission_id=submission_id,
                    expected_revision=0,
                )
        failed = cli.load_state(slug)
        active = failed["interview_curriculum"]["active_operation"]
        self.assertEqual(active["target"]["skill_ref"]["skill_id"], "concept.arrays-strings")
        self.assertEqual(failed["_openlearn_internal"]["course_revision"], 1)

        with mock.patch.object(cli, "ask_topic", wraps=original):
            result = submit_turn(
                slug,
                "Continue to the next concept.",
                intent="navigation",
                submission_id=submission_id,
                expected_revision=0,
            )
        self.assertEqual(result.status, "committed")
        self.assertEqual(course_revision(slug), 2)

    def test_skip_resumes_with_the_saved_progression_intent(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())
        with mock.patch.object(cli, "ask_topic", side_effect=RuntimeError("provider down")):
            with self.assertRaises(TutorOperationError):
                submit_turn(
                    slug,
                    "Skip this skill for now.",
                    intent="navigation",
                    progression_intent="skip",
                    submission_id=submission_id,
                    expected_revision=0,
                )
        active = cli.load_state(slug)["interview_curriculum"]["active_operation"]
        self.assertEqual(active["progression_intent"], "skip")
        reserved_skill = active["target"]["skill_ref"]["skill_id"]

        result = tutor_service.resume_interview_progression(slug)

        self.assertEqual(result.status, "committed")
        self.assertIn(
            reserved_skill,
            cli.load_state(slug)["interview_curriculum"]["evidence"]["exposed"],
        )
        self.assertNotIn(
            "concept.arrays-strings",
            cli.load_state(slug)["interview_curriculum"]["evidence"]["exposed"],
        )

    def test_generated_interview_response_reloads_without_provider_call(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())
        tutor_service._reserve_interview_progression(
            slug,
            "Continue to the next concept.",
            submission_id,
            0,
            "navigation",
            "chat",
            progression_intent="continue",
        )
        def save_generated(state: dict[str, object]) -> None:
            canonical = state["interview_curriculum"]
            canonical["active_operation"]["status"] = "generated"
            canonical["active_operation"]["generated_response"] = (
                "**Lesson:**\nArrays preserve order and support indexed traversal."
            )
            state["_openlearn_internal"]["active_turn"]["status"] = "generated"

        cli.update_state_atomic(slug, save_generated)
        persisted = tutor_service._interview_progression_state(slug)
        self.assertEqual(
            persisted["active_operation"]["generated_response"],
            "**Lesson:**\nArrays preserve order and support indexed traversal.",
        )

        with mock.patch.object(cli, "generate_validated_tutor_answer") as provider:
            result = submit_turn(
                slug,
                "Continue to the next concept.",
                intent="navigation",
                submission_id=submission_id,
                expected_revision=1,
            )

        provider.assert_not_called()
        self.assertEqual(result.status, "committed")
        self.assertIn("Arrays preserve order", result.move.content)
        self.assertEqual(course_revision(slug), 2)

    def test_failure_after_generated_persistence_retries_without_provider(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())
        original_commit = cli._commit_projected_turn
        with mock.patch.object(
            cli, "_commit_projected_turn", side_effect=RuntimeError("crash after generated")
        ):
            with self.assertRaises(TutorOperationError):
                submit_turn(
                    slug,
                    "Continue to the next concept.",
                    intent="navigation",
                    submission_id=submission_id,
                    expected_revision=0,
                )
        interrupted = cli.load_state(slug)
        operation = interrupted["interview_curriculum"]["active_operation"]
        self.assertEqual(operation["status"], "generated")
        self.assertIsInstance(operation["generated_response"], str)
        self.assertEqual(course_revision(slug), 1)

        with (
            mock.patch.object(cli, "_commit_projected_turn", wraps=original_commit),
            mock.patch.object(cli, "generate_validated_tutor_answer") as provider,
        ):
            result = submit_turn(
                slug,
                "Continue to the next concept.",
                intent="navigation",
                submission_id=submission_id,
                expected_revision=1,
            )
        provider.assert_not_called()
        self.assertEqual(result.status, "committed")
        self.assertEqual(course_revision(slug), 2)

    def test_explicit_cancellation_clears_reservation_without_progress(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())
        before = copy.deepcopy(cli.load_state(slug)["interview_curriculum"])
        tutor_service._reserve_interview_progression(
            slug,
            "Leave this one and keep going.",
            submission_id,
            0,
            "navigation",
            "chat",
            progression_intent="skip",
        )

        tutor_service.cancel_interview_progression(slug, submission_id)

        state = cli.load_state(slug)
        self.assertIsNone(state["interview_curriculum"]["active_operation"])
        self.assertEqual(state["interview_curriculum"]["cursor"], before["cursor"])
        self.assertEqual(
            state["interview_curriculum"].get("committed_check_target"),
            before.get("committed_check_target"),
        )
        self.assertEqual(
            state["interview_curriculum"].get("deferred"), before.get("deferred")
        )
        self.assertEqual(course_revision(slug), 1)
        self.assertNotIn(
            "concept.arrays-strings",
            state["interview_curriculum"]["evidence"]["exposed"],
        )
        self.assertNotIn(
            "concept.arrays-strings",
            state["interview_curriculum"]["evidence"]["ready"],
        )
        projection = interview_curriculum.compatibility_projection(
            state["interview_curriculum"]
        )
        metadata = cli.read_topic(slug).metadata
        self.assertEqual(metadata["current_unit"], projection["current_unit"])
        self.assertEqual(metadata["current_slide"], projection["current_slide"])

    def test_cancellation_recovers_every_projection_write_boundary(self) -> None:
        checkpoints = (
            "before_journal",
            "after_journal",
            "before_topic_write",
            "after_topic",
            "after_state",
            "after_events",
            "before_cleanup",
        )
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint):
                slug = self._create_interview_course(
                    f"Interview Cancellation {checkpoint}"
                )
                submission_id = str(uuid4())
                before = copy.deepcopy(cli.load_state(slug)["interview_curriculum"])
                tutor_service._reserve_interview_progression(
                    slug,
                    "Cancel this reservation.",
                    submission_id,
                    0,
                    "navigation",
                    "chat",
                    progression_intent="skip",
                )

                with mock.patch.object(
                    cli,
                    "_turn_commit_checkpoint",
                    side_effect=lambda stage: (
                        (_ for _ in ()).throw(RuntimeError(stage))
                        if stage == checkpoint
                        else None
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, checkpoint):
                        tutor_service.cancel_interview_progression(slug, submission_id)

                cli.recover_turn_commit(slug)
                tutor_service.cancel_interview_progression(slug, submission_id)
                state = cli.load_state(slug)
                canonical = state["interview_curriculum"]
                self.assertIsNone(canonical["active_operation"])
                self.assertEqual(canonical["cursor"], before["cursor"])
                self.assertEqual(canonical.get("deferred"), before.get("deferred"))
                self.assertEqual(course_revision(slug), 1)
                for key in ("due_review", "exposed", "ready", "weak"):
                    self.assertEqual(
                        canonical["evidence"].get(key), before["evidence"].get(key)
                    )
                projection = interview_curriculum.compatibility_projection(canonical)
                metadata = cli.read_topic(slug).metadata
                self.assertEqual(metadata["current_unit"], projection["current_unit"])
                self.assertEqual(metadata["current_slide"], projection["current_slide"])

    def test_practice_now_reserves_covered_skill_without_moving_cursor(self) -> None:
        slug = self._create_interview_course()
        state = cli.load_state(slug)
        canonical = state["interview_curriculum"]
        route = canonical["route"]["skills"]
        skill_ids = [item["skill_ref"]["skill_id"] for item in route]
        canonical["evidence"] = {
            "ready": list(skill_ids),
            "exposed": list(skill_ids),
            "weak": [],
            "due_review": [],
        }
        canonical["commit_index"] = 1
        original_cursor = copy.deepcopy(canonical["cursor"])
        cli.update_state_atomic(
            slug,
            lambda current: current.__setitem__("interview_curriculum", canonical),
        )

        submission_id = str(uuid4())
        tutor_service._reserve_interview_progression(
            slug,
            "Practice now using a covered curriculum concept.",
            submission_id,
            0,
            "navigation",
            "chat",
            progression_intent="practice",
        )

        reserved = cli.load_state(slug)["interview_curriculum"]
        self.assertEqual(reserved["active_operation"]["reason"], "practice_now")
        self.assertEqual(reserved["cursor"], original_cursor)
        self.assertNotEqual(
            reserved["committed_check_target"]["skill_ref"],
            reserved["cursor"]["skill_ref"],
        )
        self.assertEqual(
            reserved["active_operation"]["target"]["skill_ref"],
            reserved["committed_check_target"]["skill_ref"],
        )

    def test_interview_reservation_releases_store_locks_before_provider(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())
        provider_started = threading.Event()
        release_provider = threading.Event()
        state_writer_done = threading.Event()

        def blocked_provider(*_args: object, **_kwargs: object) -> str:
            provider_started.set()
            release_provider.wait(timeout=3)
            raise RuntimeError("stop after lock proof")

        with mock.patch.object(cli, "ask_topic", side_effect=blocked_provider):
            start_turn(
                slug,
                "Continue to the next concept.",
                intent="navigation",
                submission_id=submission_id,
                expected_revision=0,
            )
            self.assertTrue(provider_started.wait(timeout=3))

            writer = threading.Thread(
                target=lambda: (
                    cli.update_state_atomic(
                        slug, lambda state: state.__setitem__("lock_probe", True)
                    ),
                    state_writer_done.set(),
                )
            )
            writer.start()
            self.assertTrue(state_writer_done.wait(timeout=1))
            release_provider.set()
            writer.join(timeout=1)

        with tutor_service._FUTURES_GUARD:
            future = tutor_service._FUTURES.get((slug, submission_id))
        if future is not None:
            with self.assertRaises(TutorOperationError):
                future.result(timeout=3)

    def test_failure_after_saved_intent_resumes_same_payload_then_reserves(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())

        with mock.patch.object(
            tutor_service,
            "_progression_checkpoint",
            side_effect=lambda stage: (
                (_ for _ in ()).throw(RuntimeError("crash after saved"))
                if stage == "after_saved"
                else None
            ),
        ):
            with self.assertRaises(RuntimeError, msg="crash after saved"):
                tutor_service._reserve_interview_progression(
                    slug,
                    "Continue to the next concept.",
                    submission_id,
                    0,
                    "navigation",
                    "chat",
                    progression_intent="continue",
                )
        saved = cli.load_state(slug)
        self.assertEqual(saved["_openlearn_internal"]["course_revision"], 0)
        self.assertEqual(saved["_openlearn_internal"]["active_turn"]["status"], "saved")
        self.assertIsNone(saved["interview_curriculum"]["active_operation"])

        revision, replay = tutor_service._reserve_interview_progression(
            slug,
            "Continue to the next concept.",
            submission_id,
            0,
            "navigation",
            "chat",
            progression_intent="continue",
        )
        self.assertIsNone(replay)
        self.assertEqual(revision, 1)
        reserved = cli.load_state(slug)
        self.assertEqual(
            reserved["interview_curriculum"]["active_operation"]["target"]["skill_ref"][
                "skill_id"
            ],
            "concept.arrays-strings",
        )

    def test_failure_after_reservation_reloads_exact_target(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())
        with mock.patch.object(
            tutor_service,
            "_progression_checkpoint",
            side_effect=lambda stage: (
                (_ for _ in ()).throw(RuntimeError("crash after reserved"))
                if stage == "after_reserved"
                else None
            ),
        ):
            with self.assertRaises(RuntimeError, msg="crash after reserved"):
                tutor_service._reserve_interview_progression(
                    slug,
                    "Continue to the next concept.",
                    submission_id,
                    0,
                    "navigation",
                    "chat",
                    progression_intent="continue",
                )
        interrupted = cli.load_state(slug)
        active = interrupted["interview_curriculum"]["active_operation"]
        self.assertEqual(active["status"], "reserved")
        self.assertEqual(active["target"]["skill_ref"]["skill_id"], "concept.arrays-strings")
        self.assertEqual(course_revision(slug), 1)

        revision, replay = tutor_service._reserve_interview_progression(
            slug,
            "Continue to the next concept.",
            submission_id,
            1,
            "navigation",
            "chat",
            progression_intent="continue",
        )
        self.assertIsNone(replay)
        self.assertEqual(revision, 1)
        resumed = cli.load_state(slug)["interview_curriculum"]["active_operation"]
        self.assertEqual(resumed["target"], active["target"])
        metadata = cli.read_topic(slug).metadata
        projection = interview_curriculum.compatibility_projection(
            cli.load_state(slug)["interview_curriculum"]
        )
        self.assertEqual(metadata["current_unit"], projection["current_unit"])
        self.assertEqual(metadata["current_slide"], projection["current_slide"])

    def test_caught_up_navigation_clears_saved_operation_then_practice_commits(self) -> None:
        slug = self._create_interview_course()
        state = cli.load_state(slug)
        canonical = state["interview_curriculum"]
        skills = canonical["route"]["skills"]
        skill_ids = [item["skill_ref"]["skill_id"] for item in skills]
        canonical["evidence"] = {
            "ready": list(skill_ids),
            "exposed": list(skill_ids),
            "weak": [],
            "due_review": [],
        }
        cli.update_state_atomic(
            slug,
            lambda current: current.__setitem__("interview_curriculum", canonical),
        )

        caught_up = submit_turn(
            slug,
            "Continue to the next concept.",
            intent="navigation",
            progression_intent="continue",
            submission_id=str(uuid4()),
            expected_revision=0,
        )
        after = cli.load_state(slug)
        self.assertEqual(caught_up.move.kind, "caught_up")
        self.assertIsNone(after["_openlearn_internal"].get("active_turn"))
        self.assertIsNone(after["interview_curriculum"]["active_operation"])
        self.assertNotIn("pending_learner_prompt", after)
        caught_id = caught_up.submission_id
        after["_openlearn_internal"]["turn_results"] = {
            str(uuid4()): {"placeholder": index} for index in range(51)
        }
        cli.save_state(slug, after)
        replay = tutor_service.operation_result(slug, caught_id)
        self.assertEqual(replay, caught_up)
        with self.assertRaises(TutorConflictError):
            submit_turn(
                slug,
                "Use this reused ID for a different action.",
                intent="navigation",
                progression_intent="continue",
                submission_id=caught_id,
                expected_revision=0,
            )

        practiced = submit_turn(
            slug,
            "Practice now.",
            intent="navigation",
            progression_intent="practice",
            submission_id=str(uuid4()),
            expected_revision=0,
        )
        self.assertEqual(practiced.status, "committed")
        self.assertEqual(course_revision(slug), 2)

    def test_side_chat_commits_while_progression_provider_is_blocked(self) -> None:
        slug = self._create_interview_course()
        progression_started = threading.Event()
        release_progression = threading.Event()
        side_done = threading.Event()
        original = cli.generate_validated_tutor_answer

        def controlled(*args: object, **kwargs: object) -> str:
            if len(args) > 1 and args[1] == "Continue.":
                progression_started.set()
                release_progression.wait(timeout=3)
            return original(*args, **kwargs)

        with mock.patch.object(
            cli, "generate_validated_tutor_answer", side_effect=controlled
        ):
            progression_id = str(uuid4())
            start_turn(
                slug,
                "Continue.",
                intent="navigation",
                progression_intent="continue",
                submission_id=progression_id,
                expected_revision=0,
            )
            self.assertTrue(progression_started.wait(timeout=2))
            side_result: list[object] = []

            def side_chat() -> None:
                side_result.append(
                    submit_turn(
                        slug,
                        "Why does this matter?",
                        intent="question",
                        submission_id=str(uuid4()),
                        expected_revision=1,
                        session_kind=cli.SIDE_CHAT_SESSION_KIND,
                    )
                )
                side_done.set()

            thread = threading.Thread(target=side_chat)
            thread.start()
            self.assertTrue(side_done.wait(timeout=1))
            self.assertEqual(side_result[0].status, "committed")
            self.assertEqual(course_revision(slug), 1)
            release_progression.set()
            thread.join(timeout=2)

            with tutor_service._FUTURES_GUARD:
                future = tutor_service._FUTURES.get((slug, progression_id))
            if future is not None:
                progression = future.result(timeout=3)
                self.assertEqual(progression.status, "committed")

        state = cli.load_state(slug)
        internal = state["_openlearn_internal"]
        self.assertEqual(internal["course_revision"], 2)
        self.assertEqual(internal["side_chat_revision"], 1)
        self.assertIsNone(internal.get("active_turn"))
        self.assertIsNone(internal.get("active_side_chat"))
        self.assertNotIn("pending_learner_prompt", state)
        _body, log = cli.split_session_log(cli.read_topic(slug).body)
        entries = cli.session_entries(log)
        self.assertEqual(sum(entry["kind"] == "side_chat" for entry in entries), 1)
        self.assertEqual(sum(entry["prompt"] == "Continue." for entry in entries), 1)

    def test_side_chat_commits_after_navigation_against_its_captured_lesson(self) -> None:
        slug = self._create_interview_course()
        visible = "**Lesson:**\nOriginal visible lesson."
        cli.append_session(cli.read_topic(slug), "chat", "Begin", visible)
        side_entered = threading.Event()
        release_side = threading.Event()
        captured: dict[str, str] = {}
        original = cli.ask_topic

        def delayed_side(*args: object, **kwargs: object) -> str:
            if kwargs.get("session_kind") == cli.SIDE_CHAT_SESSION_KIND:
                side_entered.set()
                release_side.wait(timeout=3)
            return original(*args, **kwargs)

        def generated(_topic: object, prompt: str, *_args: object, **_kwargs: object) -> str:
            if "Why this lesson?" in prompt:
                captured["side_prompt"] = prompt
            return "**Lesson:** Mock reply."

        with (
            mock.patch.object(cli, "ask_topic", side_effect=delayed_side),
            mock.patch.object(cli, "generate_validated_tutor_answer", side_effect=generated),
        ):
            side_id = str(uuid4())
            start_turn(
                slug,
                "Why this lesson?",
                intent="question",
                submission_id=side_id,
                expected_revision=0,
                session_kind=cli.SIDE_CHAT_SESSION_KIND,
            )
            self.assertTrue(side_entered.wait(timeout=2))
            navigation = submit_turn(
                slug,
                "Continue.",
                intent="navigation",
                progression_intent="continue",
                submission_id=str(uuid4()),
                expected_revision=0,
            )
            self.assertEqual(navigation.status, "committed")
            release_side.set()
            with tutor_service._FUTURES_GUARD:
                future = tutor_service._FUTURES[(slug, side_id)]
            side = future.result(timeout=3)

        self.assertEqual(side.status, "committed")
        self.assertEqual(course_revision(slug), 2)
        self.assertIn("Original visible lesson", captured["side_prompt"])
        topic = cli.read_topic(slug)
        _body, log = cli.split_session_log(topic.body)
        side_entry = next(
            entry
            for entry in reversed(cli.session_entries(log))
            if entry["kind"] == cli.SIDE_CHAT_SESSION_KIND
        )
        self.assertEqual(side_entry["source_lesson_revision"], "0")
        source_ref = json.loads(side_entry["source_lesson_skill_ref"])
        initial_ref = cli.load_state(slug)["interview_curriculum"]["route"]["skills"][0][
            "skill_ref"
        ]
        self.assertEqual(source_ref, initial_ref)
        state = cli.load_state(slug)
        internal = state["_openlearn_internal"]
        self.assertEqual(internal["course_revision"], 2)
        self.assertEqual(internal["side_chat_revision"], 1)
        self.assertIsNone(internal.get("active_turn"))
        self.assertIsNone(internal.get("active_side_chat"))
        self.assertEqual(
            sum(entry["kind"] == "side_chat" for entry in cli.session_entries(log)),
            1,
        )

    def test_side_chat_cannot_replace_saved_navigation_prompt(self) -> None:
        slug = self._create_interview_course()
        navigation_id = str(uuid4())
        tutor_service._reserve_interview_progression(
            slug,
            "Continue with the exact original navigation request.",
            navigation_id,
            0,
            "navigation",
            "chat",
            progression_intent="continue",
        )
        tutor_service._prepare_turn(
            slug,
            "Why does the current lesson matter?",
            str(uuid4()),
            1,
            "question",
            cli.SIDE_CHAT_SESSION_KIND,
            None,
            None,
            None,
            None,
        )

        with mock.patch.object(tutor_service, "submit_turn") as resumed:
            tutor_service.resume_interview_progression(slug)

        self.assertEqual(
            resumed.call_args.args[1],
            "Continue with the exact original navigation request.",
        )

    def test_concurrent_generations_keep_response_metadata_request_local(self) -> None:
        cli.cmd_new(
            argparse.Namespace(
                topic="Side Metadata",
                goal="Keep response metadata isolated",
                mastery_profile="efficient",
                template="vim",
                interview_prep=False,
            ),
            output_func=lambda _text="": None,
        )
        main_started = threading.Event()
        side_finished = threading.Event()

        def generated(
            topic: object,
            _prompt: str,
            *_args: object,
            **kwargs: object,
        ) -> str:
            sink = kwargs.get("response_metadata_sink")
            slug = getattr(topic, "slug")
            if slug == "web-tutor":
                cli._LAST_RESPONSE_ANSWER_KEY = "A"
                cli._LAST_RESPONSE_FOCUS_TITLE = "Main response"
                main_started.set()
                self.assertTrue(side_finished.wait(timeout=3))
                metadata = cli.TutorResponseMetadata(
                    answer_key="A",
                    focus_title="Main response",
                )
                if callable(sink):
                    sink(metadata)
                return (
                    "**Check:**\nWhich answer belongs to the main turn?\n"
                    "A) Main\nB) Side\n<!-- answer: A --><!-- focus: Main response -->"
                )
            self.assertTrue(main_started.wait(timeout=3))
            cli._LAST_RESPONSE_ANSWER_KEY = "D"
            cli._LAST_RESPONSE_FOCUS_TITLE = "Side response"
            metadata = cli.TutorResponseMetadata(
                answer_key="D",
                focus_title="Side response",
            )
            if callable(sink):
                sink(metadata)
            return "**Lesson:**\nSide answer.<!-- answer: D --><!-- focus: Side response -->"

        main_result: list[object] = []

        def main_turn() -> None:
            main_result.append(
                submit_turn(
                    "web-tutor",
                    "Teach the main check.",
                    submission_id=str(uuid4()),
                    expected_revision=0,
                )
            )

        with (
            mock.patch.object(
                cli, "generate_validated_tutor_answer", side_effect=generated
            ),
            mock.patch.object(cli, "_LAST_RESPONSE_ANSWER_KEY", ""),
            mock.patch.object(cli, "_LAST_RESPONSE_FOCUS_TITLE", ""),
        ):
            main_thread = threading.Thread(target=main_turn)
            main_thread.start()
            self.assertTrue(main_started.wait(timeout=3))
            side = submit_turn(
                "side-metadata",
                "Why does this matter?",
                intent="question",
                submission_id=str(uuid4()),
                expected_revision=0,
                session_kind=cli.SIDE_CHAT_SESSION_KIND,
            )
            self.assertEqual(side.status, "committed")
            self.assertIn("Side answer", side.move.content)
            side_finished.set()
            main_thread.join(timeout=3)

        self.assertFalse(main_thread.is_alive())
        self.assertEqual(main_result[0].status, "committed")
        state = cli.load_state("web-tutor")
        self.assertEqual(state["pending_question"]["answer_key"], "A")
        self.assertIn(
            "Which answer belongs to the main turn?", main_result[0].move.prompt
        )

    def test_tampered_permanent_progression_receipt_fails_closed(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())
        submit_turn(
            slug,
            "Continue.",
            intent="navigation",
            progression_intent="continue",
            submission_id=submission_id,
            expected_revision=0,
        )

        def tamper(state: dict[str, object]) -> None:
            state["_openlearn_internal"]["turn_results"] = {}
            receipt = state["_turn_receipts"][
                f"operation_{submission_id.replace('-', '')}"
            ]
            receipt["target"]["skill_ref"]["skill_id"] = "pattern.sliding-window"

        cli.update_state_atomic(slug, tamper)
        with self.assertRaises(cli.OpenLearnError, msg="receipt integrity"):
            tutor_service.operation_result(slug, submission_id)

    def test_explicit_progression_intent_does_not_parse_skip_from_prompt(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())
        tutor_service._reserve_interview_progression(
            slug,
            "Explain why we should not skip prerequisite reasoning.",
            submission_id,
            0,
            "navigation",
            "chat",
            progression_intent="continue",
        )
        active = cli.load_state(slug)["interview_curriculum"]["active_operation"]
        self.assertEqual(active["target"]["skill_ref"]["skill_id"], "concept.arrays-strings")
        self.assertIsNone(active["deferred_skill_id"])

    def test_running_turn_publishes_stream_preview_without_persisting_tokens(self) -> None:
        started = threading.Event()
        release = threading.Event()
        submission_id = str(uuid4())

        def slow_turn(*_args: object, **kwargs: object) -> str:
            observer = kwargs["turn_observer"]
            observer.publish_phase("generating")
            observer.publish_preview("Lesson: Building the next explanation")
            started.set()
            release.wait(timeout=3)
            raise cli.ProviderRequestError(
                "provider_unavailable", "Provider request failed: temporary outage"
            )

        with mock.patch.object(cli, "ask_topic", side_effect=slow_turn):
            result = start_turn(
                "web-tutor",
                "My answer",
                submission_id=submission_id,
                expected_revision=0,
            )
            self.assertEqual(result.status, "saved")
            self.assertTrue(started.wait(timeout=3))
            running = operation_status("web-tutor", submission_id)
            self.assertIsNotNone(running)
            self.assertEqual(running.preview, "Lesson: Building the next explanation")
            state_text = cli.topic_state_path("web-tutor").read_text(encoding="utf-8")
            self.assertNotIn("Building the next explanation", state_text)
            release.set()

        deadline = time.monotonic() + 3
        terminal = operation_status("web-tutor", submission_id)
        while terminal is not None and terminal.status not in {"retryable_error", "conflict"}:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
            terminal = operation_status("web-tutor", submission_id)
        self.assertIsNotNone(terminal)
        self.assertEqual(terminal.error_code, "provider_unavailable")
        self.assertIsNone(terminal.preview)

    def test_question_turn_is_saved_as_side_chat(self) -> None:
        result = submit_turn(
            "web-tutor",
            "Why does Normal mode work that way?",
            intent="question",
            submission_id=str(uuid4()),
            expected_revision=0,
            session_kind=cli.SIDE_CHAT_SESSION_KIND,
        )

        self.assertEqual(result.status, "committed")
        self.assertEqual(course_revision("web-tutor"), 0)
        topic = cli.read_topic("web-tutor")
        _body, log = cli.split_session_log(topic.body)
        self.assertEqual(cli.session_entries(log)[-1]["kind"], "side_chat")
        self.assertIsNone(cli.last_tutor_lesson_entry(topic))

    def test_side_chat_question_does_not_answer_the_pending_check(self) -> None:
        question = "Which mode runs commands?"
        check = f"**Check:**\n{question}"
        cli.append_session(cli.read_topic("web-tutor"), "lesson", "Check", check)
        cli.save_pending_question(
            cli.read_topic("web-tutor"),
            check,
            "B",
            question_text=question,
        )

        with mock.patch.object(cli, "record_pending_attempt_reflection") as recorder:
            result = submit_turn(
                "web-tutor",
                "Can you explain what the question is asking?",
                intent="question",
                submission_id=str(uuid4()),
                expected_revision=0,
                session_kind=cli.SIDE_CHAT_SESSION_KIND,
            )

        self.assertEqual(result.status, "committed")
        recorder.assert_not_called()
        pending = cli.read_topic("web-tutor").metadata["pending_question"]
        self.assertEqual(pending["question"], question)
        self.assertEqual(pending["answer_key"], "B")
        self.assertEqual(cli.read_topic("web-tutor").metadata["last_answer_status"], "")
        self.assertEqual(cli.read_topic("web-tutor").metadata["known"], [])

    def test_side_chat_generation_receives_the_current_visible_lesson(self) -> None:
        current = "**Lesson:**\nTrace two examples before choosing an approach."
        cli.append_session(cli.read_topic("web-tutor"), "chat", "Continue", current)
        captured: dict[str, str] = {}
        submission_id = str(uuid4())
        source_id = "lesson_" + hashlib.sha256(current.encode("utf-8")).hexdigest()[:24]

        def answer(*_args: object, **kwargs: object) -> str:
            captured["user"] = str(kwargs["user"])
            return "**Lesson:**\nTracing exposes assumptions before code hides them."

        with (
            mock.patch.object(cli, "call_openai_streaming", side_effect=answer),
            mock.patch.object(cli, "finish_turn_update") as finish,
        ):
            result = submit_turn(
                "web-tutor",
                "Can you explain this slide more?",
                intent="question",
                submission_id=submission_id,
                expected_revision=0,
                session_kind=cli.SIDE_CHAT_SESSION_KIND,
                source_lesson_id=source_id,
                source_lesson_title="Saved lesson",
                source_lesson_revision=0,
            )

        self.assertEqual(result.status, "committed")
        self.assertIn("Trace two examples before choosing an approach", captured["user"])
        self.assertIn("Can you explain this slide more?", captured["user"])
        topic = cli.read_topic("web-tutor")
        _body, log = cli.split_session_log(topic.body)
        side_entry = cli.session_entries(log)[-1]
        self.assertEqual(side_entry["source_lesson_title"], "Saved lesson")
        self.assertEqual(
            side_entry["source_lesson_id"],
            source_id,
        )
        self.assertEqual(side_entry["source_lesson_revision"], "0")
        replay = submit_turn(
            "web-tutor",
            "Can you explain this slide more?",
            intent="question",
            submission_id=submission_id,
            expected_revision=0,
            session_kind=cli.SIDE_CHAT_SESSION_KIND,
            source_lesson_id=source_id,
            source_lesson_title="Saved lesson",
            source_lesson_revision=0,
        )
        self.assertEqual(replay, result)
        replay_topic = cli.read_topic("web-tutor")
        _body, replay_log = cli.split_session_log(replay_topic.body)
        self.assertEqual(
            sum(
                entry["kind"] == cli.SIDE_CHAT_SESSION_KIND
                for entry in cli.session_entries(replay_log)
            ),
            1,
        )
        with self.assertRaises(TutorConflictError, msg="source tuple changed"):
            submit_turn(
                "web-tutor",
                "Can you explain this slide more?",
                intent="question",
                submission_id=submission_id,
                expected_revision=0,
                session_kind=cli.SIDE_CHAT_SESSION_KIND,
                source_lesson_id=source_id,
                source_lesson_title="Different lesson title",
                source_lesson_revision=0,
            )
        finish.assert_not_called()

    def test_side_chat_resolves_a_committed_historical_lesson_without_advancing(self) -> None:
        slug = self._create_interview_course("Historical Side Chat")
        first = submit_turn(
            slug,
            "Begin the course.",
            intent="navigation",
            progression_intent="continue",
            submission_id=str(uuid4()),
            expected_revision=0,
        )
        self.assertEqual(first.status, "committed")
        historical = application.interview_learning(slug)
        self.assertIsNotNone(historical)
        assert historical is not None

        second = submit_turn(
            slug,
            "Continue.",
            intent="navigation",
            progression_intent="continue",
            submission_id=str(uuid4()),
            expected_revision=historical.revision,
        )
        self.assertEqual(second.status, "committed")
        current_revision = course_revision(slug)
        captured: dict[str, str] = {}

        def answer(*_args: object, **kwargs: object) -> str:
            captured["user"] = str(kwargs["user"])
            return "**Lesson:**\nThe old lesson answer stays in side chat."

        with mock.patch.object(cli, "call_openai_streaming", side_effect=answer):
            side = submit_turn(
                slug,
                "Explain the lesson I still had open.",
                intent="question",
                submission_id=str(uuid4()),
                expected_revision=current_revision,
                session_kind=cli.SIDE_CHAT_SESSION_KIND,
                source_lesson_id=historical.committed_lesson.lesson_id,
                source_lesson_title=historical.committed_lesson.title,
                source_lesson_revision=historical.revision,
            )

        self.assertEqual(side.status, "committed")
        self.assertEqual(course_revision(slug), current_revision)
        self.assertIn(historical.committed_lesson.content, captured["user"])
        topic = cli.read_topic(slug)
        _body, log = cli.split_session_log(topic.body)
        entry = cli.session_entries(log)[-1]
        self.assertEqual(entry["kind"], cli.SIDE_CHAT_SESSION_KIND)
        self.assertEqual(
            entry["source_lesson_id"], historical.committed_lesson.lesson_id
        )
        self.assertEqual(
            entry["source_lesson_title"], historical.committed_lesson.title
        )
        self.assertEqual(entry["source_lesson_revision"], str(historical.revision))
        self.assertEqual(
            json.loads(entry["source_lesson_skill_ref"])["skill_id"],
            historical.position.skill_id,
        )

        with self.assertRaises(TutorConflictError):
            submit_turn(
                slug,
                "Explain a fabricated source.",
                intent="question",
                submission_id=str(uuid4()),
                expected_revision=current_revision,
                session_kind=cli.SIDE_CHAT_SESSION_KIND,
                source_lesson_id=historical.committed_lesson.lesson_id,
                source_lesson_title="Fabricated title",
                source_lesson_revision=historical.revision,
            )

    def test_navigation_after_two_passive_lessons_generates_a_check(self) -> None:
        for index in range(2):
            cli.append_session(
                cli.read_topic("web-tutor"),
                "chat",
                f"Continue {index}",
                f"**Lesson:**\nPassive concept {index}.",
            )
        captured: dict[str, str] = {}

        def answer(*_args: object, **kwargs: object) -> str:
            captured["system"] = str(kwargs["system"])
            return "**Check:**\nHow would you apply the latest idea?"

        with mock.patch.object(cli, "call_openai_streaming", side_effect=answer):
            result = submit_turn(
                "web-tutor",
                "Continue to the next useful concept.",
                intent="navigation",
                submission_id=str(uuid4()),
                expected_revision=0,
            )

        self.assertEqual(result.status, "committed")
        self.assertIn("engagement check due", captured["system"])
        self.assertEqual(result.move.kind, "check")
        self.assertIn("How would you apply", result.move.prompt)

    def test_primary_lesson_focus_marker_updates_the_course_focus(self) -> None:
        response = (
            "**Lesson:**\nTrace one example before coding.\n\n"
            "<!-- focus: Tracing Concrete Examples -->"
        )
        with mock.patch.object(cli, "call_openai_streaming", return_value=response):
            result = submit_turn(
                "web-tutor",
                "Explain the next idea.",
                intent="question",
                submission_id=str(uuid4()),
                expected_revision=0,
            )

        self.assertEqual(result.status, "committed")
        self.assertEqual(
            cli.read_topic("web-tutor").metadata["current_focus"],
            "Tracing Concrete Examples",
        )

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

    def test_pre_generation_setup_failure_is_durable_and_retryable(self) -> None:
        submission_id = str(uuid4())
        original = tutor_service._interview_progression_state
        with mock.patch.object(
            tutor_service,
            "_interview_progression_state",
            side_effect=RuntimeError("setup failed before provider"),
        ):
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
        self.assertEqual(failed.error_code, "turn_failure")
        self.assertIsNone(
            cli.load_state("web-tutor")["_openlearn_internal"].get("active_turn")
        )

        with mock.patch.object(
            tutor_service, "_interview_progression_state", wraps=original
        ):
            retried = submit_turn(
                "web-tutor",
                "Explain registers.",
                submission_id=submission_id,
                expected_revision=0,
            )
        self.assertEqual(retried.status, "committed")

    def test_side_chat_executor_failure_does_not_clear_navigation_operation(self) -> None:
        navigation_id = str(uuid4())
        tutor_service._save_operation(
            "web-tutor",
            submission_id=navigation_id,
            status="saved",
            expected_revision=0,
            prompt="Continue.",
        )
        side_id = str(uuid4())
        with mock.patch.object(
            tutor_service._EXECUTOR,
            "submit",
            side_effect=RuntimeError("executor unavailable"),
        ):
            with self.assertRaises(TutorOperationError):
                start_turn(
                    "web-tutor",
                    "Why does this matter?",
                    intent="question",
                    submission_id=side_id,
                    expected_revision=0,
                    session_kind=cli.SIDE_CHAT_SESSION_KIND,
                )

        internal = cli.load_state("web-tutor")["_openlearn_internal"]
        self.assertEqual(internal["active_turn"]["submission_id"], navigation_id)
        self.assertIsNone(internal.get("active_side_chat"))
        self.assertEqual(internal["turn_results"][side_id]["status"], "retryable_error")

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

    def test_status_converts_same_pid_operation_without_local_worker_to_retryable(self) -> None:
        submission_id = str(uuid4())
        self._persist_active_turn(submission_id)
        state = cli.load_state("web-tutor")
        state["_openlearn_internal"]["active_turn"]["owner_pid"] = os.getpid()
        cli.save_state("web-tutor", state)

        recovered = operation_status("web-tutor", submission_id)

        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.status, "retryable_error")
        self.assertIsNone(
            cli.load_state("web-tutor")["_openlearn_internal"]["active_turn"]
        )

    def test_generic_turn_reservation_is_atomic_across_processes(self) -> None:
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        release = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=_reserve_generic_turn_process,
                args=(str(self.home), str(uuid4()), ready, release, results),
            )
            for _index in range(2)
        ]
        for process in processes:
            process.start()
        ready.set()
        outcomes = sorted(results.get(timeout=5)[0] for _index in processes)
        release.set()
        for process in processes:
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)

        self.assertEqual(outcomes, ["conflict", "reserved"])
        active = cli.load_state("web-tutor")["_openlearn_internal"]["active_turn"]
        self.assertIsInstance(active, dict)

    def test_operation_receipts_have_bounded_durable_retention_and_replay_window(
        self,
    ) -> None:
        state = cli.load_state("web-tutor")
        receipts = state.setdefault("_turn_receipts", {})
        state["_turn_receipts_schema"] = 2
        caught_up = (
            "You are caught up on the current curriculum. Choose Practice now "
            "or return when the next review is due."
        )
        submission_ids = [
            str(uuid4())
            for _index in range(cli.TURN_RECEIPT_DURABLE_RETENTION_LIMIT + 12)
        ]
        for submission_id in submission_ids:
            payload_hash = hashlib.sha256(submission_id.encode()).hexdigest()
            result = tutor_service.TutorTurnResult(
                submission_id=submission_id,
                status="committed",
                input_status="committed",
                message_kind="navigation",
                move=tutor_service.TutorMove(
                    move_id="caught-up-0",
                    revision=0,
                    kind="caught_up",
                    content=caught_up,
                    action_kind="practice",
                    prompt="",
                    history_summary="Caught up; practice is available.",
                ),
                payload_hash=payload_hash,
            )
            receipt = {
                "schema_version": 2,
                "receipt_kind": "caught_up",
                "submission_id": submission_id,
                "payload_hash": payload_hash,
                "base_revision": 0,
                "reservation_revision": 0,
                "final_revision": 0,
                "status": "committed",
                "mutation_id": f"turn_{uuid4().hex}",
                "target": None,
                "reason": "caught_up",
                "response_sha256": hashlib.sha256(caught_up.encode()).hexdigest(),
                "result": tutor_service._compact_result_dict(result),
            }
            receipt["receipt_sha256"] = cli._payload_sha256(receipt)
            receipts[f"operation_{submission_id.replace('-', '')}"] = receipt
        invalid_receipt = cli.topic_operation_receipts_dir("web-tutor") / (
            "operation_ffffffffffffffffffffffffffffffff.json"
        )
        invalid_receipt.parent.mkdir(parents=True, exist_ok=True)
        invalid_receipt.write_text("not json\n", encoding="utf-8")
        cli._externalize_operation_receipts_unlocked("web-tutor", state)
        cli.save_state("web-tutor", state)

        persisted = cli.load_state("web-tutor")
        hot_operations = [
            key for key in persisted["_turn_receipts"] if key.startswith("operation_")
        ]
        self.assertLessEqual(len(hot_operations), cli.TURN_RECEIPT_HOT_CACHE_LIMIT)
        receipt_files = list(cli.topic_operation_receipts_dir("web-tutor").glob("*.json"))
        valid_receipt_files = [path for path in receipt_files if path != invalid_receipt]
        self.assertEqual(
            len(valid_receipt_files), cli.TURN_RECEIPT_DURABLE_RETENTION_LIMIT
        )
        self.assertTrue(invalid_receipt.exists())
        self.assertIsNone(tutor_service.operation_result("web-tutor", submission_ids[0]))
        oldest_retained = submission_ids[-cli.TURN_RECEIPT_DURABLE_RETENTION_LIMIT]
        replay = tutor_service.operation_result("web-tutor", oldest_retained)
        self.assertIsNotNone(replay)
        self.assertEqual(replay.status, "committed")
        self.assertEqual(replay.move.content, caught_up)

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

    def test_polling_fresh_other_process_owner_does_not_orphan_operation(self) -> None:
        submission_id = str(uuid4())
        self._persist_active_turn(submission_id, status="reserved")
        state = cli.load_state("web-tutor")
        active = state["_openlearn_internal"]["active_turn"]
        active["owner_pid"] = 4242
        cli.save_state("web-tutor", state)

        with (
            mock.patch("openlearn.tutor_service._future_active", return_value=False),
            mock.patch("os.kill") as process_alive,
        ):
            current = operation_status("web-tutor", submission_id)

        process_alive.assert_called_once_with(4242, 0)
        self.assertIsNotNone(current)
        self.assertEqual(current.status, "reserved")
        self.assertIsNone(current.error_code)

    def test_explicit_resume_does_not_adopt_live_other_process_owner(self) -> None:
        slug = self._create_interview_course()
        submission_id = str(uuid4())
        tutor_service._reserve_interview_progression(
            slug,
            "Continue to the next concept.",
            submission_id,
            0,
            "navigation",
            "chat",
            progression_intent="continue",
        )
        cli.update_state_atomic(
            slug,
            lambda state: state["_openlearn_internal"]["active_turn"].__setitem__(
                "owner_pid", 4242
            ),
        )

        with mock.patch("os.kill") as process_alive:
            with self.assertRaises(TutorConflictError, msg="still running"):
                tutor_service.resume_interview_progression(slug)

        process_alive.assert_called_once_with(4242, 0)
        active = cli.load_state(slug)["interview_curriculum"]["active_operation"]
        self.assertEqual(active["submission_id"], submission_id)

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
