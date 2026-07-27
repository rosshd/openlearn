from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openlearn import code_runner


class CodeRunnerTests(unittest.TestCase):
    def test_oci_command_claims_required_protections_without_shell(self) -> None:
        policy = code_runner.ResourcePolicy()
        command = code_runner.build_oci_create_command(
            "/usr/bin/docker",
            "docker",
            code_runner.DEFAULT_RUNNER_IMAGE,
            Path("/tmp/attempt"),
            Path("/tmp/tests"),
            "openlearn-fixed",
            policy,
        )

        self.assertEqual(command[:2], ["/usr/bin/docker", "create"])
        self.assertTrue(any("@sha256:" in argument for argument in command))
        self.assertEqual(command[command.index("--pull") + 1], "never")
        self.assertIn("none", command[command.index("--network") + 1])
        self.assertIn("--read-only", command)
        self.assertEqual(command[command.index("--cap-drop") + 1], "ALL")
        self.assertEqual(
            command[command.index("--security-opt") + 1],
            "no-new-privileges",
        )
        self.assertEqual(command[command.index("--user") + 1], "65532:65532")
        self.assertIn("--memory", command)
        self.assertIn("--pids-limit", command)
        self.assertIn("--ulimit", command)
        self.assertNotIn("/var/run/docker.sock", " ".join(command))

    def test_diagnostics_fail_closed_before_missing_image_can_pull(self) -> None:
        calls = []

        def fake_run(args, **_kwargs):
            calls.append(args)
            if args[-1] == "info":
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 1, "", "not found")

        with mock.patch.object(code_runner.shutil, "which", return_value="/usr/bin/docker"):
            diagnostic = code_runner.diagnose_runtime(run=fake_run)

        self.assertFalse(diagnostic.ready)
        self.assertTrue(diagnostic.runtime_ready)
        self.assertFalse(diagnostic.image_ready)
        self.assertEqual([call[1:3] for call in calls], [["info"], ["image", "inspect"]])
        self.assertIn("explicitly acquire", code_runner.runtime_setup_guidance(diagnostic))

    def test_unpinned_image_is_rejected_without_runtime_calls(self) -> None:
        run = mock.Mock()

        diagnostic = code_runner.diagnose_runtime(image="python:latest", run=run)

        self.assertFalse(diagnostic.ready)
        self.assertIn("not pinned", diagnostic.detail)
        run.assert_not_called()

    def test_reduced_mode_scrubs_secret_environment_and_reports_warning_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            solution = Path(raw) / "solution.py"
            solution.write_text(
                "import os\n"
                "def inspect_secret():\n"
                "    return os.environ.get('OPENAI_API_KEY')\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                code_runner.os.environ,
                {"OPENAI_API_KEY": "must-not-leak"},
                clear=False,
            ):
                result = code_runner.run_python_tests(
                    solution,
                    function_name="inspect_secret",
                    test_cases=[{"input": [], "expected": None}],
                    reduced_isolation=True,
                )

        self.assertTrue(result.passed)
        self.assertEqual(result.isolation, "reduced")
        self.assertNotIn("network-disabled", result.protections)
        self.assertNotIn("must-not-leak", result.stdout + result.stderr)

    def test_reduced_mode_bounds_infinite_loop(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            solution = Path(raw) / "solution.py"
            solution.write_text(
                "def spin():\n"
                "    while True:\n"
                "        pass\n",
                encoding="utf-8",
            )
            result = code_runner.run_python_tests(
                solution,
                function_name="spin",
                test_cases=[{"input": [], "expected": None}],
                policy=code_runner.ResourcePolicy(
                    wall_seconds=0.2,
                    cpu_seconds=2,
                    memory_bytes=128 * 1024 * 1024,
                    process_limit=8,
                    output_bytes=4096,
                    file_bytes=4096,
                ),
                reduced_isolation=True,
            )

        self.assertEqual(result.kind, "timeout")
        self.assertEqual(result.limit_reason, "wall_time")

    def test_reduced_mode_bounds_excessive_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            solution = Path(raw) / "solution.py"
            solution.write_text(
                "def noisy():\n"
                "    while True:\n"
                "        print('x' * 1024)\n",
                encoding="utf-8",
            )
            result = code_runner.run_python_tests(
                solution,
                function_name="noisy",
                test_cases=[{"input": [], "expected": None}],
                policy=code_runner.ResourcePolicy(
                    wall_seconds=3,
                    cpu_seconds=2,
                    memory_bytes=128 * 1024 * 1024,
                    process_limit=8,
                    output_bytes=4096,
                    file_bytes=4096,
                ),
                reduced_isolation=True,
            )

        self.assertEqual(result.kind, "output_limit")
        self.assertIn("output truncated", result.stderr)

    def test_solution_outcome_types_remain_distinct_from_runner_failure(self) -> None:
        cases = [
            ("def solve(:\n    pass\n", "compile_error"),
            ("raise RuntimeError('import failed')\n", "runtime_error"),
            ("def solve():\n    return 2\n", "test_failure"),
            ("def solve():\n    return 1\n", "success"),
        ]
        for source, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as raw:
                solution = Path(raw) / "solution.py"
                solution.write_text(source, encoding="utf-8")
                result = code_runner.run_python_tests(
                    solution,
                    function_name="solve",
                    test_cases=[{"input": [], "expected": 1}],
                    reduced_isolation=True,
                )
                self.assertEqual(result.kind, expected)


if __name__ == "__main__":
    unittest.main()
