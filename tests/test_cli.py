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

    def test_menu_quits_cleanly(self) -> None:
        output = []

        exit_code = cli.run_menu(input_func=iter_input(["q"]), output_func=output.append)

        self.assertEqual(exit_code, 0)
        self.assertIn("openLearn", output)
        self.assertTrue(any("Active topic:" in line for line in output))

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
    def test_resume_prompt_requests_terminal_friendly_output(self) -> None:
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

        self.assertIn("plain-text labels", captured[0])
        self.assertIn("Do not use Markdown headings", captured[0])
        self.assertIn("under 140 words", captured[0])

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


if __name__ == "__main__":
    unittest.main()
