import contextlib
import io
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


def call_silent(func, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args)


if __name__ == "__main__":
    unittest.main()
