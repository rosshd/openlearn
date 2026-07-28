from __future__ import annotations

import json
import os
import tempfile
import threading
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

    def start_scaffolded_drill(self, *, purpose: str = "practice") -> None:
        action = cli.parse_tutor_coding_drill_action(
            {
                "action": "start_coding_drill",
                "objective": "Use a frequency map.",
                "title": "Frequency Count",
                "language": "python",
                "difficulty": 2,
                "scaffolding_level": 2,
                "purpose": purpose,
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

    def start_official_drill(self, *, purpose: str = "practice") -> None:
        action = cli.parse_tutor_coding_drill_action(
            {
                "action": "start_coding_drill",
                "objective": "Practice an official linked problem.",
                "title": "Official Linked Problem",
                "language": "python",
                "difficulty": 2,
                "scaffolding_level": 1,
                "purpose": purpose,
                "source": {
                    "kind": "official_link",
                    "name": "LeetCode",
                    "uri": "https://leetcode.com/problems/two-sum/",
                },
                "plan_prompt": "Describe your approach.",
                "todo_steps": [],
                "worked_example": None,
                "hints": ["Use a map."],
                "reflection_prompt": "Explain one edge case.",
                "transfer_prompt": "Solve a related lookup problem.",
                "drill": {
                    "title": "Official Linked Problem",
                    "description": "Use the official statement.",
                    "function_stub": "def official_attempt():\n    pass",
                    "test_cases": [],
                },
            }
        )
        answers = iter(("yes", "No clarification.", "Use a hash map."))
        with mock.patch.object(
            cli, "open_drill_in_editor", return_value="nvim"
        ), mock.patch.object(cli, "open_official_problem_link"):
            cli.orchestrate_tutor_coding_drill(
                cli.read_topic("algorithms"),
                action,
                input_func=lambda _prompt: next(answers),
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

    def test_feedback_crash_boundaries_replay_without_provider_or_runner(self) -> None:
        stages = (
            "after_answer_saved",
            "after_session_append",
            "after_assistance",
            "after_activity_completion",
            "after_attempt_completion",
            "after_reflection_registration",
            "after_delivery",
        )
        for passed in (False, True):
            for index, stage in enumerate(stages):
                with self.subTest(passed=passed, stage=stage):
                    slug = f"crash-{int(passed)}-{index}"
                    with mock.patch.object(
                        cli,
                        "infer_mastery_profile_from_goal",
                        return_value="efficient",
                    ):
                        cli.cmd_new(
                            Namespace(
                                topic=slug,
                                goal="prepare for coding interviews",
                            ),
                            output_func=lambda _text: None,
                        )
                    with mock.patch.object(
                        cli, "open_drill_in_editor", return_value="nvim"
                    ):
                        cli.cmd_drill(
                            Namespace(topic=slug, model=None, leetcode=True),
                            output_func=lambda _text: None,
                        )
                    topic = cli.read_topic(slug)
                    workspace = cli.active_drill_path(topic)
                    if passed:
                        workspace.write_text(
                            workspace.read_text(encoding="utf-8").replace(
                                "    pass", "    return [0, 1]"
                            ),
                            encoding="utf-8",
                        )
                    runner = mock.Mock(
                        return_value=runner_result(
                            "success" if passed else "test_failure",
                            "passed" if passed else "failed",
                        )
                    )
                    provider_calls: list[int] = []

                    def provider(*_args, **_kwargs):
                        provider_calls.append(1)
                        return "**Feedback:**\nDurable answer."

                    def no_provider(*_args, **_kwargs):
                        raise AssertionError("provider replayed")

                    crashed = False

                    def checkpoint(current: str) -> None:
                        nonlocal crashed
                        if current == stage and not crashed:
                            crashed = True
                            raise RuntimeError(f"crash at {stage}")

                    with mock.patch.object(
                        cli.code_runner, "run_python_tests", new=runner
                    ), mock.patch.object(
                        cli, "call_openai", new=provider
                    ), mock.patch.object(
                        cli, "_attempt_feedback_checkpoint", side_effect=checkpoint
                    ):
                        with self.assertRaisesRegex(RuntimeError, stage):
                            cli.cmd_check(
                                Namespace(
                                    topic=slug,
                                    model=None,
                                    reduced_isolation=False,
                                ),
                                output_func=lambda _text: None,
                            )

                    with mock.patch.object(
                        cli.code_runner,
                        "run_python_tests",
                        side_effect=AssertionError("runner replayed"),
                    ), mock.patch.object(
                        cli,
                        "call_openai",
                        new=no_provider,
                    ):
                        result = cli.cmd_check(
                            Namespace(
                                topic=slug,
                                model=None,
                                reduced_isolation=False,
                            ),
                            output_func=lambda _text: None,
                        )

                    attempt = cli.attempt_store().list(slug)[0]
                    self.assertEqual(result, 0 if passed else 1)
                    self.assertEqual(runner.call_count, 1)
                    self.assertEqual(provider_calls, [1])
                    self.assertEqual(len(attempt["test_runs"]), 1)
                    self.assertEqual(len(attempt["evidence_refs"]), 1)
                    self.assertEqual(len(attempt["feedback"]["deliveries"]), 1)
                    marker = (
                        "<!-- openlearn-feedback:"
                        f"{attempt['feedback']['deliveries'][0]['feedback_id']} -->"
                    )
                    self.assertEqual(
                        cli.topic_path(slug)
                        .read_text(encoding="utf-8")
                        .count(marker),
                        1,
                    )

    def test_concurrent_check_reports_busy_and_runs_once(self) -> None:
        self.start_drill()
        entered = threading.Event()
        release = threading.Event()

        def blocked_runner(*_args, **_kwargs):
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return runner_result("test_failure", "failed")

        results: list[int] = []
        errors: list[BaseException] = []

        def first_check() -> None:
            try:
                results.append(
                    cli.cmd_check(
                        Namespace(
                            topic="algorithms",
                            model=None,
                            reduced_isolation=False,
                        ),
                        output_func=lambda _text: None,
                    )
                )
            except BaseException as exc:
                errors.append(exc)

        def provider_answer(*_args, **_kwargs):
            return "**Feedback:**\nRetry."

        with mock.patch.object(
            cli.code_runner, "run_python_tests", side_effect=blocked_runner
        ), mock.patch.object(
            cli, "call_openai", new=provider_answer
        ):
            worker = threading.Thread(target=first_check)
            worker.start()
            self.assertTrue(entered.wait(timeout=5))
            with self.assertRaisesRegex(cli.OpenLearnError, "busy"):
                cli.cmd_check(
                    Namespace(
                        topic="algorithms",
                        model=None,
                        reduced_isolation=False,
                    ),
                    output_func=lambda _text: None,
                )
            release.set()
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [1])
        attempt = cli.attempt_store().list("algorithms")[0]
        self.assertEqual(len(attempt["test_runs"]), 1)

    def test_official_link_feedback_retry_is_deterministic(self) -> None:
        self.start_official_drill()
        workspace = cli.active_drill_path(cli.read_topic("algorithms"))
        workspace.write_text(
            workspace.read_text(encoding="utf-8")
            + "\n# Learner approach: complement map\n",
            encoding="utf-8",
        )
        provider_calls: list[int] = []

        def provider(*_args, **_kwargs):
            provider_calls.append(1)
            if len(provider_calls) == 1:
                raise cli.OpenLearnError("provider unavailable")
            return "**Feedback:**\nArtifact saved."

        with mock.patch.object(
            cli.code_runner,
            "run_python_tests",
            side_effect=AssertionError("official link ran tests"),
        ), mock.patch.object(cli, "call_openai", new=provider):
            with self.assertRaisesRegex(cli.OpenLearnError, "provider unavailable"):
                cli.cmd_check(
                    Namespace(
                        topic="algorithms",
                        model=None,
                        reduced_isolation=False,
                    ),
                    output_func=lambda _text: None,
                )
            self.assertEqual(
                cli.cmd_check(
                    Namespace(
                        topic="algorithms",
                        model=None,
                        reduced_isolation=False,
                    ),
                    output_func=lambda _text: None,
                ),
                0,
            )
            self.assertEqual(
                cli.cmd_check(
                    Namespace(
                        topic="algorithms",
                        model=None,
                        reduced_isolation=False,
                    ),
                    output_func=lambda _text: None,
                ),
                0,
            )

        attempt = cli.attempt_store().list("algorithms")[0]
        self.assertEqual(provider_calls, [1, 1])
        self.assertEqual(len(attempt["test_runs"]), 1)
        self.assertEqual(attempt["test_runs"][0]["outcome"], "artifact_saved")
        self.assertEqual(len(attempt["evidence_refs"]), 1)
        self.assertEqual(len(attempt["feedback"]["deliveries"]), 1)

    def test_retry_binding_protects_active_retry_when_source_is_abandoned(self) -> None:
        self.start_drill()
        source = cli.attempt_store().list("algorithms")[0]
        output: list[str] = []
        cli.cmd_attempt_retry(
            Namespace(topic="algorithms", attempt_id=source["attempt_id"]),
            output_func=output.append,
        )
        binding = cli.active_attempt_binding("algorithms")
        self.assertIsNotNone(binding)
        retry_id = str(binding["attempt_id"])
        retry = cli.attempt_store().load("algorithms", retry_id)
        retry_workspace = cli.attempt_store().resolve_workspace(retry)

        cli.cmd_attempt_abandon(
            Namespace(topic="algorithms", attempt_id=source["attempt_id"]),
            output_func=lambda _text: None,
        )

        self.assertEqual(
            cli.active_attempt_binding("algorithms")["attempt_id"], retry_id
        )
        self.assertEqual(
            cli.active_drill_path(cli.read_topic("algorithms")), retry_workspace
        )
        self.assertIsNotNone(cli.active_topic_activity("algorithms"))

        cli.cmd_attempt_abandon(
            Namespace(topic="algorithms", attempt_id=retry_id),
            output_func=lambda _text: None,
        )
        self.assertIsNone(cli.active_attempt_binding("algorithms"))
        self.assertNotIn("active_drill", cli.read_topic("algorithms").metadata)

    def test_natural_mastery_reflection_is_saved_with_initial_provenance(self) -> None:
        self.start_scaffolded_drill(purpose="mastery_check")
        attempt = cli.attempt_store().list("algorithms")[0]
        self.assertEqual(attempt["clarification"], "yes")
        self.assertEqual(attempt["plan"], "yes")
        def provider_answer(*_args, **_kwargs):
            return "**Check:**\nExplain why the map gives linear time."

        with mock.patch.object(
            cli.code_runner,
            "run_python_tests",
            return_value=runner_result("success", "passed"),
        ), mock.patch.object(
            cli,
            "call_openai",
            new=provider_answer,
        ):
            cli.cmd_check(
                Namespace(
                    topic="algorithms",
                    model=None,
                    reduced_isolation=False,
                ),
                output_func=lambda _text: None,
            )

        pending = cli.read_topic("algorithms").metadata["pending_question"]
        self.assertEqual(pending["attempt_id"], attempt["attempt_id"])
        with mock.patch.object(
            cli, "learner_message_needs_judgment", return_value=False
        ), mock.patch.object(
            cli,
            "generate_validated_tutor_answer",
            return_value="**Feedback:**\nThat explains the invariant.",
        ), mock.patch.object(cli, "finish_turn_update"):
            cli.ask_topic(
                "algorithms",
                "Each item is inserted and looked up once, so time is O(n).",
                output_func=lambda _text: None,
            )

        updated = cli.attempt_store().load(
            "algorithms", str(attempt["attempt_id"])
        )
        reflections = [
            entry
            for entry in updated["reasoning"]["entries"]
            if entry["kind"] == "reflection"
        ]
        self.assertEqual(len(reflections), 1)
        self.assertIn("O(n)", reflections[0]["content"])
        self.assertEqual(reflections[0]["evidence_id"], pending["evidence_id"])

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
