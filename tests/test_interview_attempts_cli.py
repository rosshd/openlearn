from __future__ import annotations

import os
import tempfile
import unittest
from argparse import Namespace
from unittest import mock

from openlearn import cli


def runner_result(kind: str, output: str = "") -> cli.code_runner.RunnerResult:
    return cli.code_runner.RunnerResult(
        kind=kind,
        stdout=output,
        stderr="",
        exit_code=0 if kind == "success" else 1,
        signal=None,
        duration_seconds=0.1,
        limit_reason=None,
        isolation="oci",
        runtime="docker",
        protections=("oci-container",),
    )


class DurableAttemptCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.previous_home = os.environ.get("OPENLEARN_HOME")
        os.environ["OPENLEARN_HOME"] = self.home.name
        cli._CONFIG_CACHE = None
        with mock.patch.object(
            cli, "infer_mastery_profile_from_goal", return_value="efficient"
        ):
            cli.cmd_new(
                Namespace(topic="Algorithms", goal="prepare for interviews"),
                output_func=lambda _text: None,
            )

    def tearDown(self) -> None:
        if self.previous_home is None:
            os.environ.pop("OPENLEARN_HOME", None)
        else:
            os.environ["OPENLEARN_HOME"] = self.previous_home
        cli._CONFIG_CACHE = None

    def start_drill(self, *, editor_error: Exception | None = None) -> None:
        editor = (
            mock.Mock(side_effect=editor_error)
            if editor_error is not None
            else mock.Mock(return_value="nvim")
        )
        with mock.patch.object(cli, "open_drill_in_editor", new=editor):
            cli.cmd_drill(
                Namespace(topic="algorithms", model=None, leetcode=True),
                output_func=lambda _text: None,
            )

    def test_editor_exit_keeps_attempt_workspace_and_restart_can_resume(self) -> None:
        self.start_drill(editor_error=cli.OpenLearnError("editor exited 1"))
        records = cli.attempt_store().list("algorithms")
        self.assertEqual(len(records), 1)
        attempt = records[0]
        self.assertEqual(attempt["status"], "active")
        self.assertEqual(len(attempt["snapshots"]), 1)

        restarted = cli.attempt_store().load(
            "algorithms", str(attempt["attempt_id"])
        )
        with mock.patch.object(cli, "open_drill_in_editor", return_value="nvim"):
            cli.cmd_attempt_resume(
                Namespace(topic="algorithms", attempt_id=attempt["attempt_id"]),
                output_func=lambda _text: None,
            )
        self.assertEqual(restarted["workspace_ref"], records[0]["workspace_ref"])
        self.assertIsNotNone(
            cli.attempt_store()
            .load("algorithms", str(attempt["attempt_id"]))["resumed_at"]
        )

    def test_provider_failure_after_check_preserves_code_result_and_completion(self) -> None:
        self.start_drill()
        workspace = cli.active_drill_path(cli.read_topic("algorithms"))
        workspace.write_text(
            workspace.read_text(encoding="utf-8").replace(
                "    pass", "    return sum(values)"
            ),
            encoding="utf-8",
        )
        def provider_failure(*_args, **_kwargs):
            raise cli.OpenLearnError("provider unavailable")

        with mock.patch.object(
            cli.code_runner, "run_python_tests", return_value=runner_result("success", "passed")
        ), mock.patch.object(
            cli,
            "call_openai",
            new=provider_failure,
        ):
            with self.assertRaisesRegex(cli.OpenLearnError, "provider unavailable"):
                cli.cmd_check(
                    Namespace(topic="algorithms", model=None, reduced_isolation=False),
                    output_func=lambda _text: None,
                )

        attempt = cli.attempt_store().list("algorithms")[0]
        self.assertEqual(attempt["status"], "completed")
        self.assertEqual(attempt["test_runs"][0]["outcome"], "passed")
        self.assertGreaterEqual(len(attempt["snapshots"]), 2)
        self.assertEqual(len(attempt["evidence_refs"]), 1)

    def test_runner_unavailable_is_persisted_as_infrastructure_not_incorrect(self) -> None:
        self.start_drill()
        with mock.patch.object(
            cli.code_runner,
            "run_python_tests",
            side_effect=cli.code_runner.RunnerUnavailableError("install Docker"),
        ):
            with self.assertRaisesRegex(cli.OpenLearnError, "install Docker"):
                cli.cmd_check(
                    Namespace(topic="algorithms", model=None, reduced_isolation=False),
                    output_func=lambda _text: None,
                )

        attempt = cli.attempt_store().list("algorithms")[0]
        run = attempt["test_runs"][0]
        self.assertEqual(run["outcome"], "runner_unavailable")
        self.assertFalse(run["learner_failure"])
        self.assertEqual(attempt["status"], "active")

    def test_starting_new_drill_abandons_prior_attempt_without_deleting_code(self) -> None:
        self.start_drill()
        first = cli.attempt_store().list("algorithms")[0]
        first_workspace = cli.attempt_store().resolve_workspace(first)

        self.start_drill()

        attempts = cli.attempt_store().list("algorithms")
        self.assertEqual(
            sorted(attempt["status"] for attempt in attempts),
            ["abandoned", "active"],
        )
        self.assertTrue(first_workspace.exists())

    def test_legacy_check_migrates_without_rewriting_learner_code_or_duplicate_runs(self) -> None:
        workspace = cli.topic_drill_dir("algorithms") / "legacy.py"
        source = (
            "def solve(value):\n"
            "    return value\n\n"
            "if False:\n"
            "    def test_case_1():\n"
            "        assert solve(*[1]) == 1\n"
        )
        workspace.write_text(source, encoding="utf-8")
        cli.save_active_drill("algorithms", workspace)
        def provider_answer(*_args, **_kwargs):
            return "**Feedback:**\nSaved."

        with mock.patch.object(
            cli.code_runner, "run_python_tests", return_value=runner_result("success", "passed")
        ), mock.patch.object(
            cli, "call_openai", new=provider_answer
        ):
            cli.cmd_check(
                Namespace(topic="algorithms", model=None, reduced_isolation=False),
                output_func=lambda _text: None,
            )

        attempts = cli.attempt_store().list("algorithms")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["problem"]["catalog_id"], "legacy-drill")
        self.assertEqual(len(attempts[0]["test_runs"]), 1)
        self.assertIn("return value", workspace.read_text(encoding="utf-8"))
        self.assertNotIn("test_case_1", workspace.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
