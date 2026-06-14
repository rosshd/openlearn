import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openlearn import cli


class CliStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.previous_env = {
            name: os.environ.get(name)
            for name in ("OPENLEARN_HOME", "OPENLEARN_MODEL", "OPENLEARN_BASE_URL", "OPENAI_API_KEY")
        }
        os.environ["OPENLEARN_HOME"] = self.home.name
        os.environ.pop("OPENLEARN_MODEL", None)
        os.environ.pop("OPENLEARN_BASE_URL", None)
        os.environ.pop("OPENAI_API_KEY", None)
        cli._CONFIG_CACHE = None

    def tearDown(self) -> None:
        for name, value in self.previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        cli._CONFIG_CACHE = None
        self.home.cleanup()

    def test_slugify_rejects_empty_slugs(self) -> None:
        self.assertEqual(cli.slugify("Python Basics!"), "python-basics")
        with self.assertRaises(cli.OpenLearnError):
            cli.slugify("!!!")

    def test_topic_round_trip_and_summary_metadata(self) -> None:
        call_silent(cli.cmd_init, Namespace())
        path = cli.topic_path("vim")
        cli.write_topic(
            path,
            {"topic": "Vim", "slug": "vim", "known": ["motions"]},
            "# Vim\n\n## Notes\n\nLots of body text\n",
        )

        topic = cli.read_topic("vim")
        summary = cli.read_topic_summary(path)

        self.assertEqual(topic.metadata["topic"], "Vim")
        self.assertIn("Lots of body text", topic.body)
        self.assertEqual(summary.slug, "vim")
        self.assertEqual(summary.metadata["known"], ["motions"])

    def test_new_topic_starts_unstarted(self) -> None:
        call_silent(
            cli.cmd_new,
            Namespace(
                topic="Intro AI",
                goal="Understand AI fundamentals",
            ),
        )

        topic = cli.read_topic("intro-ai")

        self.assertIs(topic.metadata["course_started"], False)
        self.assertNotIn("description", topic.metadata)
        self.assertNotIn("## Description", topic.body)
        self.assertIn("Understand AI fundamentals", topic.body)

    def test_append_session_preserves_metadata_and_review_updates_last_reviewed(self) -> None:
        call_silent(cli.cmd_init, Namespace())
        call_silent(cli.cmd_new, Namespace(topic="Append Test", goal="Check appends"))

        topic = cli.read_topic("append-test")
        cli.append_session(topic, "chat", "first prompt", "first answer")
        metadata, body = cli.parse_topic(topic.path.read_text(encoding="utf-8"))

        self.assertEqual(metadata["last_reviewed"], "")
        self.assertIn("first prompt", body)
        self.assertIn("first answer", body)

        topic = cli.read_topic("append-test")
        cli.append_session(topic, "review", "review prompt", "review answer", mark_reviewed=True)
        metadata, body = cli.parse_topic(topic.path.read_text(encoding="utf-8"))

        self.assertEqual(metadata["last_reviewed"], cli.today())
        self.assertIn("review prompt", body)
        self.assertIn("review answer", body)

    def test_model_commands_persist_readable_session_logs(self) -> None:
        call_silent(cli.cmd_init, Namespace())
        call_silent(cli.cmd_new, Namespace(topic="Persistence", goal="Check logs"))
        original_call_openai = cli.call_openai

        cli.call_openai = lambda *_args, **_kwargs: "model answer"
        try:
            call_silent(cli.cmd_resume, Namespace(topic="persistence", model=None))
            call_silent(cli.cmd_next, Namespace(topic="persistence", model=None))
            call_silent(cli.cmd_review, Namespace(topic="persistence", model=None))
        finally:
            cli.call_openai = original_call_openai

        metadata, body = cli.parse_topic(
            cli.topic_path("persistence").read_text(encoding="utf-8")
        )

        self.assertEqual(metadata["last_reviewed"], cli.today())
        self.assertIn("## Session Log", body)
        self.assertIn(" - resume", body)
        self.assertIn(" - next", body)
        self.assertIn(" - review", body)
        self.assertEqual(body.count("**Prompt**"), 3)
        self.assertEqual(body.count("**Response**"), 3)
        self.assertEqual(body.count("model answer"), 3)

    def test_config_uses_saved_values_and_environment_precedence(self) -> None:
        call_silent(cli.cmd_config_set_model, Namespace(model="saved-model"))
        call_silent(cli.cmd_config_set_base_url, Namespace(base_url="https://example.test/v1/"))
        call_silent(cli.cmd_config_set_key, Namespace(api_key="sk-saved"))

        self.assertEqual(cli.configured_model(), "saved-model")
        self.assertEqual(cli.configured_base_url(), "https://example.test/v1")
        self.assertEqual(cli.configured_openai_api_key(), "sk-saved")

        os.environ["OPENLEARN_MODEL"] = "env-model"
        os.environ["OPENLEARN_BASE_URL"] = "https://env.example/v1/"
        os.environ["OPENAI_API_KEY"] = "sk-env"

        self.assertEqual(cli.configured_model(), "env-model")
        self.assertEqual(cli.configured_base_url(), "https://env.example/v1")
        self.assertEqual(cli.configured_openai_api_key(), "sk-env")

    def test_config_show_masks_environment_api_key(self) -> None:
        os.environ["OPENAI_API_KEY"] = "sk-or-v1-test-secret-1234"
        output = capture_stdout(cli.cmd_config_show, Namespace())

        self.assertIn("API key: set by OPENAI_API_KEY (sk-o...1234)", output)
        self.assertNotIn("test-secret", output)

    def test_active_topic_resolution_falls_back_to_most_recent_topic(self) -> None:
        call_silent(cli.cmd_init, Namespace())
        cli.write_topic(
            cli.topic_path("older-topic"),
            {"topic": "Older Topic", "slug": "older-topic"},
            "# Older Topic\n",
        )
        cli.write_topic(
            cli.topic_path("newer-topic"),
            {"topic": "Newer Topic", "slug": "newer-topic"},
            "# Newer Topic\n",
        )
        os.utime(cli.topic_path("older-topic"), (100, 100))
        os.utime(cli.topic_path("newer-topic"), (200, 200))

        self.assertEqual(cli.resolve_topic_slug(None), "newer-topic")

        cli.set_active_topic("older-topic")

        self.assertEqual(cli.resolve_topic_slug(None), "older-topic")

    def test_topic_commands_update_active_topic(self) -> None:
        call_silent(cli.cmd_init, Namespace())
        call_silent(cli.cmd_new, Namespace(topic="Vim", goal="motions"))
        call_silent(cli.cmd_new, Namespace(topic="Operating Systems", goal="processes"))
        original_call_openai = cli.call_openai
        original_append_session = cli.append_session

        cli.call_openai = lambda *_args, **_kwargs: "ok"
        cli.append_session = lambda *_args, **_kwargs: None
        try:
            call_silent(cli.cmd_status, Namespace(topic="vim"))
            self.assertEqual(cli.get_active_topic(), "vim")

            call_silent(
                cli.cmd_chat,
                Namespace(topic="operating-systems", prompt="hi", model=None),
            )
            self.assertEqual(cli.get_active_topic(), "operating-systems")

            call_silent(cli.cmd_review, Namespace(topic="vim", model=None))
            self.assertEqual(cli.get_active_topic(), "vim")

            call_silent(cli.cmd_resume, Namespace(topic="operating-systems", model=None))
            self.assertEqual(cli.get_active_topic(), "operating-systems")

            call_silent(cli.cmd_next, Namespace(topic="vim", model=None))
            self.assertEqual(cli.get_active_topic(), "vim")
        finally:
            cli.call_openai = original_call_openai
            cli.append_session = original_append_session

    def test_edit_sets_active_topic_before_launching_editor(self) -> None:
        call_silent(cli.cmd_init, Namespace())
        call_silent(cli.cmd_new, Namespace(topic="Vim", goal="motions"))
        original_execvp = cli.os.execvp

        class EditorLaunched(Exception):
            pass

        def fake_execvp(_editor, _args):
            raise EditorLaunched

        cli.os.execvp = fake_execvp
        try:
            with self.assertRaises(EditorLaunched):
                cli.cmd_edit(Namespace(topic="vim"))
        finally:
            cli.os.execvp = original_execvp

        self.assertEqual(cli.get_active_topic(), "vim")

    def test_recent_lists_newest_first_and_marks_active_topic(self) -> None:
        call_silent(cli.cmd_init, Namespace())
        call_silent(cli.cmd_new, Namespace(topic="Older Topic", goal="old"))
        call_silent(cli.cmd_new, Namespace(topic="Newer Topic", goal="new"))
        os.utime(cli.topic_path("older-topic"), (100, 100))
        os.utime(cli.topic_path("newer-topic"), (200, 200))
        cli.set_active_topic("older-topic")

        output = capture_stdout(cli.cmd_recent, Namespace()).splitlines()

        self.assertIn("newer-topic", output[0])
        self.assertTrue(output[0].startswith("  "))
        self.assertIn("older-topic", output[1])
        self.assertTrue(output[1].startswith("* "))

    def test_choose_topic_returns_numbered_topic_selection(self) -> None:
        call_silent(cli.cmd_init, Namespace())
        call_silent(cli.cmd_new, Namespace(topic="Older Topic", goal="old"))
        call_silent(cli.cmd_new, Namespace(topic="Newer Topic", goal="new"))
        os.utime(cli.topic_path("older-topic"), (100, 100))
        os.utime(cli.topic_path("newer-topic"), (200, 200))
        cli.set_active_topic("older-topic")
        output = []

        selected = cli.choose_topic(
            iter_input(["2"]), output.append, "Switch to topic"
        )

        self.assertEqual(selected, "older-topic")
        self.assertIn("Switch to topic", output)
        self.assertTrue(any("1.   newer-topic" in line for line in output))
        self.assertTrue(any("2. * older-topic" in line for line in output))

    def test_delete_topic_requires_confirmation_and_clears_active_topic(self) -> None:
        call_silent(cli.cmd_init, Namespace())
        call_silent(cli.cmd_new, Namespace(topic="Delete Me", goal="temporary"))

        with self.assertRaises(cli.OpenLearnError):
            cli.cmd_delete(Namespace(topic="delete-me", yes=False))

        self.assertTrue(cli.topic_path("delete-me").exists())

        call_silent(cli.cmd_delete, Namespace(topic="delete-me", yes=True))

        self.assertFalse(cli.topic_path("delete-me").exists())
        self.assertIsNone(cli.get_active_topic())

    def test_delete_topic_rejects_missing_topic(self) -> None:
        call_silent(cli.cmd_init, Namespace())

        with self.assertRaises(cli.OpenLearnError):
            cli.cmd_delete(Namespace(topic="missing", yes=True))


class ProviderResponseTests(unittest.TestCase):
    def test_extract_response_text_supports_chat_completion_shape(self) -> None:
        text = cli.extract_response_text(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Practice macros with one repeatable edit.",
                        }
                    }
                ]
            }
        )

        self.assertEqual(text, "Practice macros with one repeatable edit.")

    def test_extract_response_text_supports_chat_content_parts(self) -> None:
        text = cli.extract_response_text(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "First part."},
                                {"type": "text", "text": "Second part."},
                            ],
                        }
                    }
                ]
            }
        )

        self.assertEqual(text, "First part.\nSecond part.")

    def test_extract_response_text_supports_responses_api_fallback_shape(self) -> None:
        text = cli.extract_response_text(
            {
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": "Review registers before macros."},
                            {"type": "text", "text": "Then record a small macro."},
                        ]
                    }
                ]
            }
        )

        self.assertEqual(text, "Review registers before macros.\nThen record a small macro.")

    def test_sanitize_model_output_removes_system_reminder_blocks(self) -> None:
        text = cli.sanitize_model_output(
            "Keep this answer.\n<system-reminder>hidden platform text</system-reminder>\n"
        )

        self.assertEqual(text, "Keep this answer.")

    def test_sanitize_model_output_removes_loose_system_reminder_lines(self) -> None:
        text = cli.sanitize_model_output(
            "Keep this answer.\nYour operational mode changed.\nStill useful."
        )

        self.assertEqual(text, "Keep this answer.\nStill useful.")

    def test_sanitize_model_output_normalizes_terminal_markdown(self) -> None:
        text = cli.sanitize_model_output("**Recap**\n* First item")

        self.assertEqual(text, "Recap\n- First item")

    def test_call_openai_sends_completion_limit(self) -> None:
        previous_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test"
        requests = []
        original_urlopen = cli.urlopen

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "short answer"}}]}).encode()

        def fake_urlopen(request, timeout=0):
            requests.append((request, timeout))
            return FakeResponse()

        cli.urlopen = fake_urlopen
        try:
            answer = cli.call_openai("test-model", "system", "user")
        finally:
            cli.urlopen = original_urlopen
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous_key

        payload = json.loads(requests[0][0].data.decode("utf-8"))
        self.assertEqual(answer, "short answer")
        self.assertEqual(payload["max_tokens"], cli.DEFAULT_MAX_TOKENS)
        self.assertIs(payload["include_reasoning"], False)

    def test_call_openai_streaming_prints_chunks_and_returns_text(self) -> None:
        previous_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test"
        requests = []
        original_urlopen = cli.urlopen

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def __iter__(self):
                events = [
                    {"choices": [{"delta": {"content": "Hello "}}]},
                    {"choices": [{"delta": {"content": "there"}}]},
                ]
                for event in events:
                    yield f"data: {json.dumps(event)}\n".encode()
                yield b"data: [DONE]\n"

        def fake_urlopen(request, timeout=0):
            requests.append((request, timeout))
            return FakeResponse()

        cli.urlopen = fake_urlopen
        try:
            output = []
            answer = cli.call_openai_streaming(
                "test-model", "system", "user", output_func=output.append
            )
        finally:
            cli.urlopen = original_urlopen
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous_key

        payload = json.loads(requests[0][0].data.decode("utf-8"))

        self.assertIs(payload["stream"], True)
        self.assertEqual(answer, "Hello there")
        self.assertEqual(output, ["Hello there"])


class InteractiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.previous_env = {name: os.environ.get(name) for name in ("OPENLEARN_HOME", "OPENAI_API_KEY")}
        os.environ["OPENLEARN_HOME"] = self.home.name
        os.environ["OPENAI_API_KEY"] = "sk-test"
        cli._CONFIG_CACHE = None

    def tearDown(self) -> None:
        for name, value in self.previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        cli._CONFIG_CACHE = None
        self.home.cleanup()

    def test_no_args_defaults_to_menu(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args([])

        self.assertIs(args.func, cli.cmd_menu)

    def test_main_handles_keyboard_interrupt_without_traceback(self) -> None:
        original_build_parser = cli.build_parser

        class FakeParser:
            def parse_args(self, _argv):
                return Namespace(
                    func=lambda _args: (_ for _ in ()).throw(KeyboardInterrupt)
                )

        cli.build_parser = lambda: FakeParser()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                exit_code = cli.main([])
        finally:
            cli.build_parser = original_build_parser

        self.assertEqual(exit_code, 130)
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_menu_quits_cleanly(self) -> None:
        output = []

        exit_code = cli.run_menu(input_func=iter_input(["q"]), output_func=output.append)

        self.assertEqual(exit_code, 0)
        self.assertIn("openLearn", output)
        self.assertTrue(any("Active topic:" in line for line in output))
        self.assertIn("1. New topic", output)
        self.assertNotIn("1. Resume", output)
        self.assertNotIn("10. REPL", output)

    def test_menu_clears_missing_active_topic_and_hides_learning_actions(self) -> None:
        cli.set_active_topic("missing-topic")
        output = []

        exit_code = cli.run_menu(input_func=iter_input(["q"]), output_func=output.append)

        self.assertEqual(exit_code, 0)
        self.assertIsNone(cli.get_active_topic())
        self.assertIn("Active topic: none", output)
        self.assertIn("1. New topic", output)
        self.assertNotIn("1. Resume", output)
        self.assertNotIn("2. Next step", output)

    def test_menu_learning_actions_enter_repl_automatically(self) -> None:
        call_silent(cli.cmd_new, Namespace(topic="Active Topic", goal="active"))
        mark_course_started("active-topic")
        cases = [
            ("1", ["1", "q"], [("resume", None, None), ("repl", False)]),
            ("2", ["2", "q"], [("next", None, None), ("repl", False)]),
            (
                "3",
                ["3", "What next?", "q"],
                [("ask", None, "What next?", None)],
            ),
            ("4", ["4", "q"], [("review", "active-topic", None), ("repl", False)]),
        ]
        original_cmd_resume = cli.cmd_resume
        original_cmd_next = cli.cmd_next
        original_ask_topic = cli.ask_topic
        original_cmd_review = cli.cmd_review
        original_resolve_topic_slug = cli.resolve_topic_slug
        original_run_repl = cli.run_repl

        def fake_cmd_resume(args: Namespace) -> int:
            calls.append(("resume", args.topic, args.model))
            return 0

        def fake_cmd_next(args: Namespace) -> int:
            calls.append(("next", args.topic, args.model))
            return 0

        def fake_ask_topic(topic: str | None, prompt: str, model: str | None) -> str:
            calls.append(("ask", topic, prompt, model))
            return "answer"

        def fake_cmd_review(args: Namespace) -> int:
            calls.append(("review", args.topic, args.model))
            return 0

        def fake_run_repl(**kwargs) -> int:
            calls.append(("repl", kwargs.get("show_intro", True)))
            return 0

        for choice, inputs, expected in cases:
            with self.subTest(choice=choice):
                calls = []
                cli.cmd_resume = fake_cmd_resume
                cli.cmd_next = fake_cmd_next
                cli.ask_topic = fake_ask_topic
                cli.cmd_review = fake_cmd_review
                cli.resolve_topic_slug = lambda _value: "active-topic"
                cli.run_repl = fake_run_repl
                try:
                    exit_code = cli.run_menu(
                        input_func=iter_input(inputs), output_func=lambda _text: None
                    )
                finally:
                    cli.cmd_resume = original_cmd_resume
                    cli.cmd_next = original_cmd_next
                    cli.ask_topic = original_ask_topic
                    cli.cmd_review = original_cmd_review
                    cli.resolve_topic_slug = original_resolve_topic_slug
                    cli.run_repl = original_run_repl

                self.assertEqual(exit_code, 0)
                self.assertEqual(calls, expected)

    def test_menu_can_create_topic(self) -> None:
        exit_code = call_silent(
            cli.run_menu,
            input_func=iter_input(
                ["1", "Menu Topic", "Practice menu flow", "m", "q"]
            ),
            output_func=lambda _text: None,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(cli.topic_path("menu-topic").exists())
        self.assertEqual(cli.get_active_topic(), "menu-topic")

    def test_menu_create_topic_can_continue_to_course_start(self) -> None:
        calls = []
        original_start_course = cli.start_course
        original_run_repl = cli.run_repl

        def fake_start_course(**_kwargs) -> int:
            calls.append("start")
            mark_course_started(cli.get_active_topic())
            return 0

        def fake_run_repl(**kwargs) -> int:
            calls.append(("repl", kwargs.get("show_intro", True)))
            return 0

        cli.start_course = fake_start_course
        cli.run_repl = fake_run_repl
        try:
            exit_code = call_silent(
                cli.run_menu,
                input_func=iter_input(
                    ["1", "Menu Topic", "Practice menu flow", "c", "q"]
                ),
                output_func=lambda _text: None,
            )
        finally:
            cli.start_course = original_start_course
            cli.run_repl = original_run_repl

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, ["start", ("repl", False)])
        self.assertTrue(cli.topic_path("menu-topic").exists())

    def test_menu_can_switch_topic_from_numbered_list(self) -> None:
        call_silent(cli.cmd_new, Namespace(topic="First Topic", goal="first"))
        call_silent(cli.cmd_new, Namespace(topic="Second Topic", goal="second"))
        os.utime(cli.topic_path("first-topic"), (100, 100))
        os.utime(cli.topic_path("second-topic"), (200, 200))

        exit_code = call_silent(
            cli.run_menu,
            input_func=iter_input(["4", "2", "q"]),
            output_func=lambda _text: None,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(cli.get_active_topic(), "first-topic")

    def test_menu_can_delete_topic_from_numbered_list_with_yes_no(self) -> None:
        call_silent(cli.cmd_new, Namespace(topic="First Topic", goal="first"))
        call_silent(cli.cmd_new, Namespace(topic="Second Topic", goal="second"))
        os.utime(cli.topic_path("first-topic"), (100, 100))
        os.utime(cli.topic_path("second-topic"), (200, 200))

        exit_code = call_silent(
            cli.run_menu,
            input_func=iter_input(["5", "2", "y", "q"]),
            output_func=lambda _text: None,
        )

        self.assertEqual(exit_code, 0)
        self.assertFalse(cli.topic_path("first-topic").exists())
        self.assertTrue(cli.topic_path("second-topic").exists())

    def test_menu_delete_no_cancels_without_error(self) -> None:
        call_silent(cli.cmd_new, Namespace(topic="First Topic", goal="first"))
        output = []

        exit_code = call_silent(
            cli.run_menu,
            input_func=iter_input(["5", "1", "n", "q"]),
            output_func=output.append,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(cli.topic_path("first-topic").exists())
        self.assertIn("Delete cancelled.", output)
        self.assertFalse(any(line.startswith("error:") for line in output))

    def test_menu_shows_start_course_for_unstarted_topic(self) -> None:
        call_silent(cli.cmd_new, Namespace(topic="Intro AI", goal="basics"))
        output = []

        exit_code = cli.run_menu(input_func=iter_input(["q"]), output_func=output.append)

        self.assertEqual(exit_code, 0)
        self.assertIn("1. Start course", output)
        self.assertNotIn("1. Resume", output)
        self.assertNotIn("2. Next step", output)
        self.assertNotIn("4. Review", output)
        self.assertNotIn("5. Status", output)

    def test_start_course_confirms_plan_then_teaches_first_lesson(self) -> None:
        call_silent(
            cli.cmd_new,
            Namespace(topic="Intro AI", goal="college course basics"),
        )
        calls = []
        original_call_openai = cli.call_openai

        def fake_call_openai(_model: str, _system: str, user: str) -> str:
            calls.append(user)
            if "Create a concise course plan" in user:
                return "Scope: AI basics\nUnits:\n1. Definitions - Explain AI."
            return (
                "Lesson: AI is building systems that perform intelligent tasks. "
                "Question: What is AI?"
            )

        cli.call_openai = fake_call_openai
        try:
            exit_code = call_silent(
                cli.start_course,
                input_func=iter_input(["y"]),
                output_func=lambda _text: None,
            )
        finally:
            cli.call_openai = original_call_openai

        metadata, body = cli.parse_topic(
            cli.topic_path("intro-ai").read_text(encoding="utf-8")
        )

        self.assertEqual(exit_code, 0)
        self.assertIs(metadata["course_started"], True)
        self.assertIn(" - course_plan", body)
        self.assertIn(" - lesson", body)
        self.assertIn("Scope: AI basics", body)
        self.assertIn("What is AI?", body)
        self.assertIn("college course basics", calls[0])

    def test_start_course_blank_revision_keeps_course_unstarted(self) -> None:
        call_silent(cli.cmd_new, Namespace(topic="Intro AI", goal="basics"))
        original_call_openai = cli.call_openai

        cli.call_openai = lambda *_args, **_kwargs: "Scope: AI basics"
        try:
            exit_code = call_silent(
                cli.start_course,
                input_func=iter_input(["n", ""]),
                output_func=lambda _text: None,
            )
        finally:
            cli.call_openai = original_call_openai

        topic = cli.read_topic("intro-ai")

        self.assertEqual(exit_code, 0)
        self.assertIs(topic.metadata["course_started"], False)
        self.assertNotIn("course_plan", topic.body)

    def test_start_course_rejecting_outline_requests_changes_then_regenerates(self) -> None:
        call_silent(cli.cmd_new, Namespace(topic="Intro AI", goal="basics"))
        calls = []
        original_call_openai = cli.call_openai

        def fake_call_openai(_model: str, _system: str, user: str) -> str:
            calls.append(user)
            if len(calls) == 1:
                return "Scope: Too broad"
            if len(calls) == 2:
                return "Scope: More math and search\nUnits:\n1. Search - Learn BFS."
            return "Lesson: Breadth-first search explores by depth. Question: What does BFS expand first?"

        cli.call_openai = fake_call_openai
        try:
            exit_code = call_silent(
                cli.start_course,
                input_func=iter_input(["n", "More math and search", "y"]),
                output_func=lambda _text: None,
            )
        finally:
            cli.call_openai = original_call_openai

        metadata, body = cli.parse_topic(
            cli.topic_path("intro-ai").read_text(encoding="utf-8")
        )

        self.assertEqual(exit_code, 0)
        self.assertIs(metadata["course_started"], True)
        self.assertIn("Revise it materially", calls[1])
        self.assertIn("Requested changes: More math and search", calls[1])
        self.assertIn("Rejected outline:\nScope: Too broad", calls[1])
        self.assertIn("Scope: More math and search", body)

    def test_repl_plain_text_asks_active_topic_and_appends_session(self) -> None:
        call_silent(cli.cmd_init, Namespace())
        call_silent(cli.cmd_new, Namespace(topic="Vim", goal="Learn motions"))
        calls = []
        original_call_openai = cli.call_openai

        def fake_call_openai(model: str, system: str, user: str) -> str:
            calls.append((model, system, user))
            return "Use h j k l for movement."

        cli.call_openai = fake_call_openai
        try:
            exit_code = call_silent(
                cli.run_repl,
                input_func=iter_input(["How do I move?", "/quit"]),
                output_func=lambda _text: None,
            )
        finally:
            cli.call_openai = original_call_openai

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls[0][2], "How do I move?")
        topic = cli.read_topic("vim")
        self.assertIn("How do I move?", topic.body)
        self.assertIn("Use h j k l for movement.", topic.body)

    def test_repl_help_and_unknown_commands_use_output_func(self) -> None:
        output = []

        cli.handle_repl_command("help", output_func=output.append)

        self.assertTrue(any("/resume" in line for line in output))
        with self.assertRaises(cli.OpenLearnError):
            cli.handle_repl_command("missing")

    def test_repl_reports_malformed_command_quotes_as_openlearn_error(self) -> None:
        with self.assertRaises(cli.OpenLearnError):
            cli.handle_repl_command('new "unfinished')


class PromptInstructionTests(unittest.TestCase):
    def test_resume_prompt_requests_natural_tutor_style(self) -> None:
        captured = []
        original_call_openai = cli.call_openai
        original_append_session = cli.append_session

        def fake_call_openai(model: str, system: str, user: str) -> str:
            captured.append(user)
            return "ok"

        cli.call_openai = fake_call_openai
        cli.append_session = lambda *_args, **_kwargs: None
        try:
            topic = cli.Topic(
                slug="demo",
                path=Path("demo.md"),
                metadata={"topic": "Demo", "model": "test-model"},
                body="# Demo\n",
            )
            original_read_topic = cli.read_topic
            original_resolve_topic_slug = cli.resolve_topic_slug
            original_set_active_topic = cli.set_active_topic
            cli.read_topic = lambda _slug: topic
            cli.resolve_topic_slug = lambda _value: "demo"
            cli.set_active_topic = lambda _slug: None
            try:
                call_silent(cli.cmd_resume, Namespace(topic=None, model=None))
            finally:
                cli.read_topic = original_read_topic
                cli.resolve_topic_slug = original_resolve_topic_slug
                cli.set_active_topic = original_set_active_topic
        finally:
            cli.call_openai = original_call_openai
            cli.append_session = original_append_session

        self.assertIn("Pick up naturally", captured[0])
        self.assertIn("Avoid template labels", captured[0])
        self.assertIn("warm, direct, and specific", captured[0])

    def test_next_prompt_asks_for_learner_response(self) -> None:
        captured = []
        topic = cli.Topic(
            slug="demo",
            path=Path("demo.md"),
            metadata={"topic": "Demo", "model": "test-model"},
            body="# Demo\n",
        )
        original_call_openai = cli.call_openai
        original_append_session = cli.append_session
        original_read_topic = cli.read_topic
        original_resolve_topic_slug = cli.resolve_topic_slug
        original_set_active_topic = cli.set_active_topic

        def fake_call_openai(model: str, system: str, user: str) -> str:
            captured.append(user)
            return "ok"

        cli.call_openai = fake_call_openai
        cli.append_session = lambda *_args, **_kwargs: None
        cli.read_topic = lambda _slug: topic
        cli.resolve_topic_slug = lambda _value: "demo"
        cli.set_active_topic = lambda _slug: None
        try:
            call_silent(cli.cmd_next, Namespace(topic=None, model=None))
        finally:
            cli.call_openai = original_call_openai
            cli.append_session = original_append_session
            cli.read_topic = original_read_topic
            cli.resolve_topic_slug = original_resolve_topic_slug
            cli.set_active_topic = original_set_active_topic

        self.assertIn("Sound like a human tutor", captured[0])
        self.assertIn("Teach one small idea", captured[0])
        self.assertIn("Ask a question only if it tests", captured[0])

    def test_review_prompt_does_not_include_answer_key(self) -> None:
        captured = []
        topic = cli.Topic(
            slug="demo",
            path=Path("demo.md"),
            metadata={"topic": "Demo", "model": "test-model"},
            body="# Demo\n",
        )
        original_call_openai = cli.call_openai
        original_append_session = cli.append_session
        original_read_topic = cli.read_topic
        original_set_active_topic = cli.set_active_topic

        def fake_call_openai(model: str, system: str, user: str) -> str:
            captured.append(user)
            return "ok"

        cli.call_openai = fake_call_openai
        cli.append_session = lambda *_args, **_kwargs: None
        cli.read_topic = lambda _slug: topic
        cli.set_active_topic = lambda _slug: None
        try:
            call_silent(cli.cmd_review, Namespace(topic="demo", model=None))
        finally:
            cli.call_openai = original_call_openai
            cli.append_session = original_append_session
            cli.read_topic = original_read_topic
            cli.set_active_topic = original_set_active_topic

        self.assertIn("no answer key", captured[0])
        self.assertIn("wait for the learner to answer", captured[0])
        self.assertNotIn("answer key at the end", captured[0])

    def test_learning_metadata_update_merges_known_and_weak_spots(self) -> None:
        home = tempfile.TemporaryDirectory()
        previous_home = os.environ.get("OPENLEARN_HOME")
        os.environ["OPENLEARN_HOME"] = home.name
        cli._CONFIG_CACHE = None
        original_call_openai = cli.call_openai

        cli.call_openai = lambda *_args, **_kwargs: json.dumps(
            {
                "known_add": ["normal mode", "normal mode"],
                "weak_spots_add": ["insert mode", "normal mode"],
                "review_due_add": ["mode switching", "normal mode"],
                "current_focus": "Vim modes",
            }
        )
        try:
            call_silent(cli.cmd_new, Namespace(topic="Vim", goal="learn vim"))
            topic = cli.read_topic("vim")
            cli.update_learning_metadata(
                topic,
                "Normal mode is for commands",
                "Correct. Insert mode needs more work.",
                "test-model",
            )
            updated = cli.read_topic("vim")
        finally:
            cli.call_openai = original_call_openai
            if previous_home is None:
                os.environ.pop("OPENLEARN_HOME", None)
            else:
                os.environ["OPENLEARN_HOME"] = previous_home
            cli._CONFIG_CACHE = None
            home.cleanup()

        self.assertEqual(updated.metadata["known"], ["normal mode"])
        self.assertEqual(updated.metadata["weak_spots"], ["insert mode"])
        self.assertEqual(updated.metadata["review_due"], ["mode switching"])
        self.assertEqual(updated.metadata["current_focus"], "Vim modes")

    def test_system_prompt_requests_answer_evaluation_before_advancing(self) -> None:
        topic = cli.Topic(
            slug="demo",
            path=Path("demo.md"),
            metadata={"topic": "Demo", "course_started": True},
            body="# Demo\n",
        )

        prompt = cli.system_prompt(topic)
        normalized = " ".join(prompt.split())

        self.assertIn("treat the learner's next message as an answer", normalized)
        self.assertIn("Evaluate it before moving on", normalized)
        self.assertIn("stay on the same concept", normalized)
        self.assertIn("Do not advance just because the learner says no", normalized)
        self.assertIn("Do not ask filler clarifying questions", normalized)

    def test_first_lesson_prompt_avoids_filler_questions(self) -> None:
        prompt = cli.first_lesson_prompt("Scope: Demo")

        self.assertIn("one important check-for-understanding", prompt)
        self.assertIn("Do not ask a question just to ask one", prompt)

    def test_resume_sanitizes_answer_before_printing_and_appending(self) -> None:
        appended = []
        topic = cli.Topic(
            slug="demo",
            path=Path("demo.md"),
            metadata={"topic": "Demo", "model": "test-model"},
            body="# Demo\n",
        )
        original_call_openai = cli.call_openai
        original_append_session = cli.append_session
        original_read_topic = cli.read_topic
        original_resolve_topic_slug = cli.resolve_topic_slug
        original_set_active_topic = cli.set_active_topic

        cli.call_openai = lambda *_args, **_kwargs: (
            "Recall question? <system-reminder>\n"
            "Your operational mode has changed from plan to build.\n"
            "</system-reminder>"
        )
        cli.append_session = lambda *_args, **_kwargs: appended.append(_args)
        cli.read_topic = lambda _slug: topic
        cli.resolve_topic_slug = lambda _value: "demo"
        cli.set_active_topic = lambda _slug: None
        try:
            output = capture_stdout(cli.cmd_resume, Namespace(topic=None, model=None))
        finally:
            cli.call_openai = original_call_openai
            cli.append_session = original_append_session
            cli.read_topic = original_read_topic
            cli.resolve_topic_slug = original_resolve_topic_slug
            cli.set_active_topic = original_set_active_topic

        self.assertEqual(output.strip(), "Recall question?")
        self.assertEqual(appended[0][3], "Recall question?")


class PromptContextTests(unittest.TestCase):
    def test_prompt_context_separates_notes_from_recent_sessions(self) -> None:
        body = "\n".join(
            [
                "# Vim",
                "",
                "## Current Goal",
                "Learn editing",
                "",
                "## Notes",
                "Important durable note",
                "",
                "## Session Log",
                "",
                session_entry(1, "old marker"),
                session_entry(2, "second marker"),
                session_entry(3, "third marker"),
                session_entry(4, "fourth marker"),
                session_entry(5, "latest marker"),
            ]
        )

        topic_context, recent_sessions = cli.prompt_context(body)

        self.assertIn("Important durable note", topic_context)
        self.assertNotIn("Session Log", topic_context)
        self.assertIn("latest marker", recent_sessions)
        self.assertIn("second marker", recent_sessions)
        self.assertNotIn("old marker", recent_sessions)

    def test_system_prompt_includes_recent_sessions_after_large_notes_section(self) -> None:
        notes = "\n".join(f"note {index}" for index in range(250))
        body = "\n".join(
            [
                "# Algorithms",
                "",
                "## Notes",
                notes,
                "",
                "## Session Log",
                "",
                session_entry(1, "binary search confusion"),
                session_entry(2, "latest heap insight"),
            ]
        )
        topic = cli.Topic(
            slug="algorithms",
            path=cli.topic_path("algorithms"),
            metadata={"topic": "Algorithms", "goal": "Learn algorithms"},
            body=body,
        )

        prompt = cli.system_prompt(topic)

        self.assertIn("note 0", prompt)
        self.assertNotIn("note 249", prompt)
        self.assertIn("binary search confusion", prompt)
        self.assertIn("latest heap insight", prompt)


def session_entry(index: int, marker: str) -> str:
    return f"### 2026-06-1{index} 00:00 UTC - chat\n\n**Prompt**\n\nquestion {index}\n\n**Response**\n\n{marker}\n"


def call_silent(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


def capture_stdout(func, *args, **kwargs):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        func(*args, **kwargs)
    return output.getvalue()


def iter_input(values):
    iterator = iter(values)

    def read(_prompt=""):
        return next(iterator)

    return read


def mark_course_started(slug: str) -> None:
    path = cli.topic_path(slug)
    metadata, body = cli.parse_topic(path.read_text(encoding="utf-8"))
    metadata = dict(metadata)
    metadata["course_started"] = True
    path.write_text(cli.format_topic(metadata, body), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
