from __future__ import annotations

import json
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

    def start_scaffolded_drill(self) -> None:
        action = cli.parse_tutor_coding_drill_action(
            {
                "action": "start_coding_drill",
                "objective": "Use a frequency map.",
                "title": "Frequency Count",
                "language": "python",
                "difficulty": 2,
                "scaffolding_level": 2,
                "purpose": "practice",
                "source": {
                    "kind": "licensed",
                    "name": "openLearn exercise bank",
                    "license": "AGPL-3.0-or-later",
                },
                "plan_prompt": "State what the map stores.",
                "todo_steps": ["Create the map.", "Update each count."],
                "worked_example": None,
                "hints": ["Start from an empty dictionary.", "Update one item at a time."],
                "reflection_prompt": "Why is this linear?",
                "transfer_prompt": "Find the first unique item.",
                "drill": {
                    "title": "Frequency Count",
                    "description": "Return counts.",
                    "function_stub": "def frequencies(values):\n    pass",
                    "test_cases": [
                        {
                            "input": [["a", "a", "b"]],
                            "expected": {"a": 2, "b": 1},
                        }
                    ],
                },
            }
        )
        with mock.patch.object(cli, "open_drill_in_editor", return_value="nvim"):
            cli.orchestrate_tutor_coding_drill(
                cli.read_topic("algorithms"),
                action,
                input_func=lambda _prompt: "yes",
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
        self.assertEqual(attempt["status"], "active")
        self.assertEqual(attempt["test_runs"][0]["outcome"], "passed")
        self.assertGreaterEqual(len(attempt["snapshots"]), 2)
        self.assertEqual(len(attempt["evidence_refs"]), 1)
        self.assertIsNotNone(attempt["feedback"]["pending"])

        with mock.patch.object(
            cli.code_runner, "run_python_tests", side_effect=AssertionError("reran code")
        ), mock.patch.object(cli, "call_openai", new=provider_failure):
            with self.assertRaisesRegex(cli.OpenLearnError, "provider unavailable"):
                cli.cmd_check(
                    Namespace(topic="algorithms", model=None, reduced_isolation=False),
                    output_func=lambda _text: None,
                )
        attempt = cli.attempt_store().load("algorithms", str(attempt["attempt_id"]))
        self.assertEqual(len(attempt["feedback"]["pending"]["failures"]), 2)

        provider_calls = []

        def provider_answer(*_args, **_kwargs):
            provider_calls.append(1)
            return "**Feedback:**\nSaved result."

        with mock.patch.object(
            cli.code_runner, "run_python_tests", side_effect=AssertionError("reran code")
        ), mock.patch.object(cli, "call_openai", new=provider_answer):
            result = cli.cmd_check(
                Namespace(topic="algorithms", model=None, reduced_isolation=False),
                output_func=lambda _text: None,
            )

        retried = cli.attempt_store().load("algorithms", str(attempt["attempt_id"]))
        self.assertEqual(result, 0)
        self.assertEqual(provider_calls, [1])
        self.assertEqual(retried["status"], "completed")
        self.assertEqual(len(retried["test_runs"]), 1)
        self.assertEqual(len(retried["evidence_refs"]), 1)
        self.assertIsNone(retried["feedback"]["pending"])

    def test_inspect_translates_corrupt_attempt_to_openlearn_error(self) -> None:
        self.start_drill()
        attempt = cli.attempt_store().list("algorithms")[0]
        state_path = cli.attempt_store().state_path(
            "algorithms", str(attempt["attempt_id"])
        )
        corrupted = dict(attempt)
        corrupted["feedback"] = {"pending": {"feedback_id": "wrong"}, "deliveries": []}
        state_path.write_text(json.dumps(corrupted), encoding="utf-8")

        with self.assertRaisesRegex(cli.OpenLearnError, "feedback"):
            cli.cmd_attempt_inspect(
                Namespace(
                    topic="algorithms",
                    attempt_id=str(attempt["attempt_id"]),
                ),
                output_func=lambda _text: None,
            )

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

    def test_failed_feedback_retries_without_rerun_duplicate_or_hint_advance(self) -> None:
        self.start_scaffolded_drill()
        runner = mock.Mock(return_value=runner_result("test_failure", "failed"))

        def provider_failure(*_args, **_kwargs):
            raise cli.OpenLearnError("provider unavailable")

        for _retry in range(2):
            with mock.patch.object(
                cli.code_runner, "run_python_tests", new=runner
            ), mock.patch.object(cli, "call_openai", new=provider_failure):
                with self.assertRaisesRegex(cli.OpenLearnError, "provider unavailable"):
                    cli.cmd_check(
                        Namespace(
                            topic="algorithms",
                            model=None,
                            reduced_isolation=False,
                        ),
                        output_func=lambda _text: None,
                    )

        pending_attempt = cli.attempt_store().list("algorithms")[0]
        pending = pending_attempt["feedback"]["pending"]
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(len(pending_attempt["test_runs"]), 1)
        self.assertEqual(len(pending_attempt["evidence_refs"]), 1)
        self.assertEqual(pending["hint_index"], 1)
        self.assertEqual(len(pending["failures"]), 2)
        self.assertEqual(len(pending_attempt["assistance"]["hints"]), 1)

        def provider_answer(*_args, **_kwargs):
            return "**Feedback:**\nUse the selected hint, then retry."

        with mock.patch.object(
            cli.code_runner, "run_python_tests", side_effect=AssertionError("reran code")
        ), mock.patch.object(cli, "call_openai", new=provider_answer):
            result = cli.cmd_check(
                Namespace(topic="algorithms", model=None, reduced_isolation=False),
                output_func=lambda _text: None,
            )

        delivered = cli.attempt_store().load(
            "algorithms", str(pending_attempt["attempt_id"])
        )
        self.assertEqual(result, 1)
        self.assertEqual(len(delivered["test_runs"]), 1)
        self.assertEqual(len(delivered["evidence_refs"]), 1)
        self.assertEqual(len(delivered["assistance"]["hints"]), 2)
        self.assertNotIn(
            "Update one item at a time.",
            [entry["content"] for entry in delivered["assistance"]["hints"]],
        )

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

    def test_resume_and_retry_restore_exact_attempt_activity_then_abandon_clears_owner(
        self,
    ) -> None:
        self.start_scaffolded_drill()
        first = cli.attempt_store().list("algorithms")[0]
        first_id = str(first["attempt_id"])
        first_activity_id = str(first["activity_id"])
        first_tests = first["execution"]["test_cases"]

        self.start_drill()
        second = cli.attempt_store().list("algorithms")[0]
        self.assertNotEqual(second["activity_id"], first_activity_id)

        with mock.patch.object(cli, "open_drill_in_editor", return_value="nvim"):
            cli.cmd_attempt_resume(
                Namespace(topic="algorithms", attempt_id=first_id),
                output_func=lambda _text: None,
            )
        resumed = cli.attempt_store().load("algorithms", first_id)
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(
            cli.active_topic_activity("algorithms")["activity_id"],
            first_activity_id,
        )
        self.assertEqual(
            cli.active_drill_path(cli.read_topic("algorithms")),
            cli.attempt_store().resolve_workspace(resumed),
        )

        self.start_drill()
        output = []
        cli.cmd_attempt_retry(
            Namespace(topic="algorithms", attempt_id=first_id),
            output_func=output.append,
        )
        retry_id = next(
            str(record["attempt_id"])
            for record in cli.attempt_store().list("algorithms")
            if str(record["attempt_id"]) in "\n".join(output)
        )
        retried = cli.attempt_store().load("algorithms", retry_id)
        self.assertEqual(retried["execution"]["test_cases"], first_tests)
        self.assertEqual(retried["activity_id"], first_activity_id)
        self.assertEqual(
            cli.active_topic_activity("algorithms")["activity_id"],
            first_activity_id,
        )

        cli.cmd_attempt_abandon(
            Namespace(topic="algorithms", attempt_id=retry_id),
            output_func=lambda _text: None,
        )
        self.assertNotIn("active_drill", cli.read_topic("algorithms").metadata)
        self.assertIsNone(cli.active_topic_activity("algorithms"))
        with self.assertRaisesRegex(cli.OpenLearnError, "no active drill"):
            cli.cmd_check(
                Namespace(topic="algorithms", model=None, reduced_isolation=False),
                output_func=lambda _text: None,
            )

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
