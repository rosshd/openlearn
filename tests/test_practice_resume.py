from __future__ import annotations

import argparse
import copy
import os
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, mock
from uuid import uuid4

from openlearn import cli, tutor_service


CHECK = "**Check:**\nWhich value does return send?\nA) The result\nB) Nothing\n<!-- answer: A -->"


class PracticeResumeTests(TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {
            "OPENLEARN_HOME": str(self.home), "OPENLEARN_MOCK": "1",
        }))
        cli.clear_config_cache()
        cli.cmd_new(argparse.Namespace(topic="Practice", goal="Learn functions"),
                    output_func=lambda _text: None)

    def tearDown(self) -> None:
        cli.clear_config_cache()

    def test_natural_quiz_request_accepts_check_without_judging(self) -> None:
        with (
            mock.patch.object(cli, "call_openai") as judge,
            mock.patch.object(cli, "call_openai_streaming", return_value=CHECK),
            mock.patch.object(cli, "maybe_suggest_videos"),
        ):
            cli.ask_topic("practice", "quiz me", output_func=lambda _text: None)
        judge.assert_not_called()
        self.assertEqual(cli.load_state("practice")["pending_question"]["answer_key"], "A")

    def seed_pending(self) -> dict[str, object]:
        cli.save_pending_question(cli.read_topic("practice"), cli.sanitize_model_output(CHECK), "A")
        def enrich(state: dict[str, object]) -> None:
            state["pending_question"]["curriculum_target"] = {"skill_id": "functions"}
            state["pending_question"]["concept_id"] = "functions"
            state["pending_remediation"] = {"concept_id": "functions", "stage": "hint"}
            state["misconceptions"] = ["return prints the value"]
            state["pending_learner_prompt"] = "my saved attempt"
        cli.update_state_atomic("practice", enrich)
        return copy.deepcopy(cli.load_state("practice"))

    def test_bounded_practice_wording(self) -> None:
        for text in (
            "quiz me", "Please quiz me.", "Can you quiz me?", "Could you please test me?",
            "give me a practice question", "ask me one question about functions please",
        ):
            with self.subTest(text=text):
                self.assertEqual(cli.classify_ungraded_learner_message(text), "practice")
                self.assertFalse(cli.learner_message_needs_judgment({"pending_question": {}}, text))
        for text in (
            "explain quizzes", "Can you explain practice?", "give me a quiz explanation",
            "quiz me and then move on", "skip", "next", "Why is this useful?",
            "I would return a result", "I'm confused", "How do I ask a practice question?",
        ):
            with self.subTest(text=text):
                self.assertFalse(cli.learner_requests_practice(text))

    def test_existing_check_restored_without_provider_or_learning_evidence(self) -> None:
        before = self.seed_pending()
        with (
            mock.patch.object(cli, "call_openai") as judge,
            mock.patch.object(cli, "call_openai_streaming") as provider,
            mock.patch.object(cli, "record_pending_attempt_reflection") as reflection,
            mock.patch.object(cli, "maybe_suggest_videos"),
        ):
            answer = cli.ask_topic("practice", "Can you quiz me?", output_func=lambda _text: None)
        judge.assert_not_called()
        provider.assert_not_called()
        reflection.assert_not_called()
        self.assertEqual(answer, cli.pending_check_response(before))
        after = cli.load_state("practice")
        for key in ("pending_question", "pending_remediation", "misconceptions", "pending_learner_prompt"):
            self.assertEqual(after[key], before[key])
        self.assertFalse(any(event["event_type"] == "answer_judged"
                             for event in cli.load_event_log(cli.topic_events_path("practice"))))

    def test_resume_in_fresh_process_preserves_exact_state_and_history(self) -> None:
        before = self.seed_pending()
        body = cli.topic_path("practice").read_text()
        events = cli.topic_events_path("practice").read_text()
        code = (
            "from argparse import Namespace; from openlearn import cli; "
            "cli.call_openai_streaming = lambda **kw: (_ for _ in ()).throw(AssertionError('provider')); "
            "cli.cmd_resume(Namespace(topic='practice', model=None))"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                env={**os.environ, "PYTHONPATH": str(Path(cli.__file__).parents[1])})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Which value does return send?", result.stdout)
        self.assertNotIn("answer: A", result.stdout)
        self.assertEqual(cli.load_state("practice"), before)
        self.assertEqual(cli.topic_path("practice").read_text(), body)
        self.assertEqual(cli.topic_events_path("practice").read_text(), events)

    def test_side_chat_practice_does_not_replace_course_check(self) -> None:
        before = self.seed_pending()
        with (
            mock.patch.object(cli, "call_openai") as judge,
            mock.patch.object(cli, "call_openai_streaming", return_value="**Lesson:**\nWe can discuss practice here."),
        ):
            result = tutor_service.submit_turn("practice", "Can you quiz me?", intent="question",
                session_kind=cli.SIDE_CHAT_SESSION_KIND, submission_id=str(uuid4()), expected_revision=0)
        self.assertEqual(result.status, "committed")
        self.assertEqual(cli.load_state("practice")["pending_question"], before["pending_question"])
        self.assertEqual(cli.load_pending_learner_prompt("practice"), "my saved attempt")
        judge.assert_not_called()

    def test_repl_resume_activates_requested_pending_topic_before_answer(self) -> None:
        for name in ("Alpha", "Beta"):
            cli.cmd_new(argparse.Namespace(topic=name, goal=f"Learn {name}"),
                        output_func=lambda _text: None)
        cli.save_pending_question(cli.read_topic("alpha"), cli.sanitize_model_output(CHECK), "A")
        beta_check = "**Check:**\nWhich value does print display?\nA) Nothing\nB) The argument"
        cli.save_pending_question(cli.read_topic("beta"), beta_check, "B")
        cli.set_active_topic("beta")
        beta_state = copy.deepcopy(cli.load_state("beta"))
        beta_body = cli.topic_path("beta").read_text()
        beta_events = cli.topic_events_path("beta").read_text()
        inputs = iter(("/resume alpha", "A", "/q"))
        output: list[str] = []
        with (
            mock.patch.object(cli, "call_openai", return_value=json.dumps({
                "message_kind": "answer", "answer_score": 1.0,
                "last_answer_status": "correct", "answer_kind": "recognition",
            })) as judge,
            mock.patch.object(cli, "call_openai_streaming",
                              return_value="**Next:**\nPress Enter to continue."),
            mock.patch.object(cli, "maybe_suggest_videos"),
        ):
            result = cli.run_repl(input_func=lambda _prompt: next(inputs),
                                  output_func=output.append, show_intro=False)
        self.assertEqual(result, 0)
        self.assertEqual(cli.get_active_topic(), "alpha")
        self.assertTrue(any("Which value does return send?" in text for text in output))
        judge.assert_called_once()
        self.assertIn("Which value does return send?", judge.call_args.args[2])
        self.assertIn('"answer_key": "A"', judge.call_args.args[2])
        self.assertTrue(any(event["event_type"] == "answer_judged"
                            for event in cli.load_event_log(cli.topic_events_path("alpha"))))
        self.assertEqual(cli.load_state("beta"), beta_state)
        self.assertEqual(cli.topic_path("beta").read_text(), beta_body)
        self.assertEqual(cli.topic_events_path("beta").read_text(), beta_events)

    def test_interview_next_keeps_explicit_navigation_route(self) -> None:
        with (
            mock.patch.object(cli, "interview_profile_path") as profile,
            mock.patch.object(cli, "cmd_resume", return_value=0) as resume,
        ):
            profile.return_value.exists.return_value = True
            args = argparse.Namespace(topic="practice", model=None)
            cli.cmd_next(args)
        resume.assert_called_once_with(args, output_func=print, restore_pending=False)

    def test_service_practice_replay_and_later_answer_judged(self) -> None:
        sid = str(uuid4())
        with (
            mock.patch.object(cli, "call_openai") as judge,
            mock.patch.object(cli, "call_openai_streaming", return_value=CHECK) as provider,
            mock.patch.object(cli, "maybe_suggest_videos"),
        ):
            first = tutor_service.submit_turn("practice", "Could you quiz me?",
                submission_id=sid, expected_revision=0)
            pending = copy.deepcopy(cli.load_state("practice")["pending_question"])
            replay = tutor_service.submit_turn("practice", "Could you quiz me?",
                submission_id=sid, expected_revision=0)
            restored = tutor_service.submit_turn("practice", "quiz me",
                submission_id=str(uuid4()), expected_revision=1)
        self.assertEqual(first.status, "committed")
        self.assertEqual(first, replay)
        self.assertEqual(restored.move.prompt, first.move.prompt)
        self.assertEqual(cli.load_state("practice")["pending_question"], pending)
        provider.assert_called_once()
        judge.assert_not_called()
        with (
            mock.patch.object(cli, "call_openai", return_value=json.dumps({
                "message_kind": "answer", "answer_score": 1.0,
                "last_answer_status": "correct", "answer_kind": "recognition",
            })) as judge,
            mock.patch.object(cli, "call_openai_streaming",
                              return_value="**Next:**\nPress Enter to continue."),
            mock.patch.object(cli, "maybe_suggest_videos"),
        ):
            result = tutor_service.submit_turn("practice", "A", submission_id=str(uuid4()),
                                               expected_revision=2)
        self.assertEqual(result.status, "committed")
        judge.assert_called_once()
        self.assertTrue(any(event["event_type"] == "answer_judged"
                            for event in cli.load_event_log(cli.topic_events_path("practice"))))

    def test_service_provider_failure_retries_same_practice_without_false_evidence(self) -> None:
        sid = str(uuid4())
        before = copy.deepcopy(cli.load_state("practice"))
        with mock.patch.object(cli, "call_openai_streaming", side_effect=cli.OpenLearnError("offline")):
            with self.assertRaises(tutor_service.TutorOperationError):
                tutor_service.submit_turn("practice", "quiz me", submission_id=sid,
                                          expected_revision=0)
        failed = cli.load_state("practice")
        self.assertNotIn("pending_question", failed)
        self.assertEqual(failed.get("known"), before.get("known"))
        self.assertEqual(cli.load_pending_learner_prompt("practice"), "quiz me")
        with (
            mock.patch.object(cli, "call_openai") as judge,
            mock.patch.object(cli, "call_openai_streaming", return_value=CHECK),
            mock.patch.object(cli, "maybe_suggest_videos"),
        ):
            result = tutor_service.submit_turn("practice", "quiz me", submission_id=sid,
                                               expected_revision=0)
        self.assertEqual(result.status, "committed")
        judge.assert_not_called()
        self.assertEqual(cli.load_state("practice")["pending_question"]["answer_key"], "A")
