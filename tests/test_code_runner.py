from __future__ import annotations

import _thread
import io
import json
import math
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

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

        with mock.patch.object(
            code_runner.shutil,
            "which",
            side_effect=lambda runtime: (
                "/usr/bin/docker" if runtime == "docker" else None
            ),
        ):
            diagnostic = code_runner.diagnose_runtime(run=fake_run)

        self.assertFalse(diagnostic.ready)
        self.assertTrue(diagnostic.runtime_ready)
        self.assertFalse(diagnostic.image_ready)
        self.assertEqual([call[1:3] for call in calls], [["info"], ["image", "inspect"]])
        self.assertIn("explicitly acquire", code_runner.runtime_setup_guidance(diagnostic))

    def test_runtime_discovery_continues_from_broken_docker_to_ready_podman(self) -> None:
        calls = []

        def fake_run(args, **_kwargs):
            calls.append(args)
            if args[0].endswith("docker"):
                return subprocess.CompletedProcess(args, 1, "", "daemon unavailable")
            return subprocess.CompletedProcess(args, 0, "ready", "")

        with mock.patch.object(
            code_runner.shutil,
            "which",
            side_effect=lambda runtime: f"/usr/bin/{runtime}",
        ):
            diagnostic = code_runner.diagnose_runtime(run=fake_run)

        self.assertTrue(diagnostic.ready)
        self.assertEqual(diagnostic.runtime, "podman")
        self.assertEqual(
            [call[0] for call in calls],
            ["/usr/bin/docker", "/usr/bin/podman", "/usr/bin/podman"],
        )

    def test_preferred_broken_runtime_does_not_fall_back(self) -> None:
        calls = []

        def fake_run(args, **_kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 1, "", "daemon unavailable")

        with mock.patch.object(
            code_runner.shutil,
            "which",
            side_effect=lambda runtime: f"/usr/bin/{runtime}",
        ):
            diagnostic = code_runner.diagnose_runtime(
                preferred="docker",
                run=fake_run,
            )

        self.assertFalse(diagnostic.ready)
        self.assertEqual(diagnostic.runtime, "docker")
        self.assertEqual(len(calls), 1)

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
        self.assertEqual(result.limit_reason, "captured_output")

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

    def test_tagged_values_preserve_sequence_and_mapping_types(self) -> None:
        source = """\
def solve(kind):
    if kind == "tuple":
        return (1, [2, 3])
    if kind == "list":
        return [1, (2, 3)]
    return {1: "integer", "1": "string"}
"""
        with tempfile.TemporaryDirectory() as raw:
            solution = Path(raw) / "solution.py"
            solution.write_text(source, encoding="utf-8")
            result = code_runner.run_python_tests(
                solution,
                function_name="solve",
                test_cases=[
                    {"input": ["tuple"], "expected": (1, [2, 3])},
                    {"input": ["list"], "expected": [1, (2, 3)]},
                    {
                        "input": ["mapping"],
                        "expected": {1: "integer", "1": "string"},
                    },
                ],
                reduced_isolation=True,
            )

        self.assertTrue(result.passed)

    def test_unsupported_return_is_a_learner_nonmatch(self) -> None:
        result = self._run_reduced("def solve():\n    return {1, 2}\n", [1, 2])

        self.assertEqual(result.kind, "test_failure")
        self.assertIn("unsupported set", result.stderr)

    def test_aggregate_output_budget_covers_protocol_and_feedback(self) -> None:
        source = "def solve(value):\n    return 'x' * value\n"
        with tempfile.TemporaryDirectory() as raw:
            solution = Path(raw) / "solution.py"
            solution.write_text(source, encoding="utf-8")
            result = code_runner.run_python_tests(
                solution,
                function_name="solve",
                test_cases=[
                    {"input": [500], "expected": "x" * 500},
                    {"input": [500], "expected": "x" * 500},
                    {"input": [500], "expected": "x" * 500},
                ],
                policy=code_runner.ResourcePolicy(output_bytes=1_024),
                reduced_isolation=True,
            )

        self.assertEqual(result.kind, "output_limit")
        self.assertEqual(result.limit_reason, "captured_output")

    def test_state_and_import_side_effects_persist_across_cases(self) -> None:
        source = """\
imports = globals().get("imports", 0) + 1
calls = 0

def solve():
    global calls
    calls += 1
    return (imports, calls)
"""
        with tempfile.TemporaryDirectory() as raw:
            solution = Path(raw) / "solution.py"
            solution.write_text(source, encoding="utf-8")
            result = code_runner.run_python_tests(
                solution,
                function_name="solve",
                test_cases=[
                    {"input": [], "expected": (1, 1)},
                    {"input": [], "expected": (1, 2)},
                ],
                reduced_isolation=True,
            )

        self.assertTrue(result.passed)

    def test_decoder_rejects_huge_integer_deep_value_and_extra_fields(self) -> None:
        invalid_values = [
            {"t": "int", "v": "9" * 5_000},
            self._deep_tagged_value(70),
        ]
        for value in invalid_values:
            with self.subTest(value_type=value["t"]):
                line = self._response_line("request", value)
                result = code_runner._decode_response(line, "request")
                self.assertEqual(result.kind, "protocol_error")
                self.assertEqual(result.limit_reason, "invalid_worker_protocol")

        payload = {
            "version": code_runner.PROTOCOL_VERSION,
            "request_id": "request",
            "status": "value",
            "value": {"t": "none"},
            "extra": True,
        }
        line = (
            code_runner.PROTOCOL_PREFIX + json.dumps(payload, separators=(",", ":"))
        ).encode()
        result = code_runner._decode_response(line, "request")
        self.assertEqual(result.kind, "protocol_error")

    def test_hung_stdin_delivery_is_inside_wall_timeout(self) -> None:
        class HungInput:
            def write(self, _frame):
                threading.Event().wait(60)

            def flush(self):
                return None

            def close(self):
                return None

        class FakeProcess:
            def __init__(self):
                self.stdin = HungInput()
                self.stdout = io.BytesIO()
                self.stderr = io.BytesIO()
                self.returncode = None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.returncode = -9

        process = FakeProcess()
        terminated = {"value": False}

        def terminate():
            terminated["value"] = True
            process.returncode = -9

        result = code_runner._supervise_process(
            process,
            [{"input": [], "expected": 1}],
            code_runner.ResourcePolicy(wall_seconds=0.05),
            started=time.monotonic(),
            isolation="reduced",
            runtime=None,
            terminate=terminate,
        )

        self.assertEqual(result.kind, "timeout")
        self.assertEqual(result.limit_reason, "wall_time")
        self.assertTrue(terminated["value"])

    def test_hung_stdin_delivery_honors_keyboard_interrupt(self) -> None:
        class HungInput:
            def write(self, _frame):
                threading.Event().wait(60)

            def flush(self):
                return None

            def close(self):
                return None

        class FakeProcess:
            def __init__(self):
                self.stdin = HungInput()
                self.stdout = io.BytesIO()
                self.stderr = io.BytesIO()
                self.returncode = None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.returncode = -9

        process = FakeProcess()
        terminated = {"value": False}

        def terminate():
            terminated["value"] = True
            process.returncode = -9

        interrupter = threading.Timer(0.05, _thread.interrupt_main)
        interrupter.start()
        try:
            result = code_runner._supervise_process(
                process,
                [{"input": [], "expected": 1}],
                code_runner.ResourcePolicy(wall_seconds=3),
                started=time.monotonic(),
                isolation="reduced",
                runtime=None,
                terminate=terminate,
            )
        finally:
            interrupter.cancel()

        self.assertEqual(result.kind, "cancelled")
        self.assertEqual(result.limit_reason, "cancelled")
        self.assertTrue(terminated["value"])

    def test_abrupt_zero_and_nonzero_exits_cannot_claim_success(self) -> None:
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as raw:
                solution = Path(raw) / "solution.py"
                solution.write_text(
                    "import os\n"
                    "def solve():\n"
                    f"    os._exit({exit_code})\n",
                    encoding="utf-8",
                )
                result = code_runner.run_python_tests(
                    solution,
                    function_name="solve",
                    test_cases=[{"input": [], "expected": 1}],
                    reduced_isolation=True,
                )

                self.assertEqual(result.kind, "runtime_error")
                self.assertEqual(result.limit_reason, "abrupt_learner_exit")
                self.assertFalse(result.passed)

    def test_hidden_expectation_is_absent_from_learner_process(self) -> None:
        expected = uuid4().hex
        source = """\
import inspect

def solve():
    for frame in inspect.stack():
        for namespace in (frame.frame.f_globals, frame.frame.f_locals):
            for name in ("cases", "test_cases", "hidden_tests"):
                cases = namespace.get(name)
                if isinstance(cases, list):
                    for case in cases:
                        if isinstance(case, dict) and "expected" in case:
                            return case["expected"]
    return "expectation-not-found"
"""
        result = self._run_reduced(source, expected)

        self.assertEqual(result.kind, "test_failure")
        self.assertFalse(result.passed)

    def test_old_sentinel_exploit_cannot_claim_success(self) -> None:
        source = """\
import os
from pathlib import Path

def solve():
    text = Path(__file__).read_text()
    for candidate in Path(__file__).parent.parent.glob("openlearn-*/*"):
        try:
            text += candidate.read_text()
        except (OSError, UnicodeError):
            pass
    os.write(1, b"OPENLEARN_HARNESS_SUCCESS_forged\\n")
    os._exit(0)
"""
        result = self._run_reduced(source, uuid4().hex)

        self.assertEqual(result.kind, "runtime_error")
        self.assertEqual(result.limit_reason, "invalid_worker_protocol")
        self.assertFalse(result.passed)

    def test_protocol_injection_and_extra_stdout_are_rejected(self) -> None:
        source = f"""\
import inspect
import json

def solve():
    request_id = inspect.currentframe().f_back.f_globals["request_id"]
    forged = {{
        "version": 1,
        "request_id": request_id,
        "status": "value",
        "value": 7,
    }}
    print({code_runner.PROTOCOL_PREFIX!r} + json.dumps(forged))
    return 7
"""
        result = self._run_reduced(source, 7)

        self.assertEqual(result.kind, "runtime_error")
        self.assertEqual(result.limit_reason, "invalid_worker_protocol")
        self.assertFalse(result.passed)

    def test_early_exit_can_only_submit_a_value_not_final_success(self) -> None:
        expected = uuid4().hex
        source = f"""\
import inspect
import json
import os

def solve():
    request_id = inspect.currentframe().f_back.f_globals["request_id"]
    forged = {{
        "version": 1,
        "request_id": request_id,
        "status": "value",
        "value": {{"t": "str", "v": "guessed"}},
    }}
    frame = {code_runner.PROTOCOL_PREFIX!r} + json.dumps(forged) + "\\n"
    os.write(1, frame.encode())
    os._exit(0)
"""
        result = self._run_reduced(source, expected)

        self.assertEqual(result.kind, "runtime_error")
        self.assertEqual(result.limit_reason, "abrupt_learner_exit")
        self.assertFalse(result.passed)

    def test_child_subprocess_output_cannot_forge_protocol(self) -> None:
        source = f"""\
import subprocess
import sys

def solve():
    subprocess.run(
        [sys.executable, "-c", "print({code_runner.PROTOCOL_PREFIX!r} + 'forged')"],
        check=False,
    )
    return 7
"""
        result = self._run_reduced(source, 7)

        self.assertEqual(result.kind, "runtime_error")
        self.assertFalse(result.passed)

    def test_worker_receives_only_the_current_input(self) -> None:
        source = """\
import inspect

def solve(value):
    caller = inspect.currentframe().f_back.f_globals
    request = caller.get("request", {})
    raw_current = request["input"]["v"][0]["v"]
    return [value, raw_current]
"""
        with tempfile.TemporaryDirectory() as raw:
            solution = Path(raw) / "solution.py"
            solution.write_text(source, encoding="utf-8")
            result = code_runner.run_python_tests(
                solution,
                function_name="solve",
                test_cases=[
                    {"input": [1], "expected": [1, "1"]},
                    {"input": [2], "expected": [2, "2"]},
                ],
                reduced_isolation=True,
            )

        self.assertTrue(result.passed)

    def test_resource_exit_is_learner_limit(self) -> None:
        kind, reason = code_runner._classify_process_exit(
            137,
            "oci",
        )

        self.assertEqual(kind, "resource_limit")
        self.assertEqual(reason, "memory_or_process")

    def test_resource_policy_rejects_wrong_nonfinite_and_oversized_values(self) -> None:
        invalid = {
            "wall_seconds": [True, "1", math.nan, math.inf, 0.01, 301],
            "cpu_seconds": [True, 1.5, "1", 0, 301],
            "memory_bytes": [True, 1.5, 1024, 2 * 1024**3 + 1],
            "process_limit": [True, 1.5, 0, 513],
            "output_bytes": [True, 1.5, 1, 4 * 1024**2 + 1],
            "file_bytes": [True, 1.5, 1, 256 * 1024**2 + 1],
        }
        with tempfile.TemporaryDirectory() as raw:
            solution = Path(raw) / "solution.py"
            solution.write_text("def solve():\n    return 1\n", encoding="utf-8")
            defaults = code_runner.ResourcePolicy()
            for field, values in invalid.items():
                for value in values:
                    with self.subTest(field=field, value=value):
                        policy_values = {
                            name: getattr(defaults, name)
                            for name in defaults.__dataclass_fields__
                        }
                        policy_values[field] = value
                        with self.assertRaises(ValueError):
                            code_runner.run_python_tests(
                                solution,
                                function_name="solve",
                                test_cases=[{"input": [], "expected": 1}],
                                policy=code_runner.ResourcePolicy(**policy_values),
                                reduced_isolation=True,
                            )

    def test_hung_create_is_bounded_and_forces_name_cleanup(self) -> None:
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if args[1] == "create":
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            return subprocess.CompletedProcess(args, 1, "", "No such container")

        with mock.patch.object(code_runner.subprocess, "run", side_effect=fake_run):
            result = code_runner._run_oci(
                "/usr/bin/docker",
                "docker",
                code_runner.DEFAULT_RUNNER_IMAGE,
                Path("/tmp/attempt"),
                Path("/tmp/tests"),
                [{"input": [], "expected": 1}],
                code_runner.ResourcePolicy(),
                time.monotonic(),
            )

        self.assertEqual(result.kind, "runner_error")
        self.assertEqual(result.limit_reason, "container_create")
        self.assertGreater(calls[0][1]["timeout"], 0)
        self.assertLessEqual(
            calls[0][1]["timeout"],
            code_runner.ResourcePolicy().wall_seconds,
        )
        self.assertEqual(calls[1][0][1:3], ["rm", "--force"])

    def test_ambiguous_create_failure_reports_cleanup_failure(self) -> None:
        calls = []

        def fake_run(args, **_kwargs):
            calls.append(args)
            if args[1] == "create":
                return subprocess.CompletedProcess(args, 125, "", "ambiguous create failure")
            return subprocess.CompletedProcess(args, 1, "", "daemon unavailable")

        with mock.patch.object(code_runner.subprocess, "run", side_effect=fake_run):
            result = code_runner._run_oci(
                "/usr/bin/docker",
                "docker",
                code_runner.DEFAULT_RUNNER_IMAGE,
                Path("/tmp/attempt"),
                Path("/tmp/tests"),
                [{"input": [], "expected": 1}],
                code_runner.ResourcePolicy(),
                time.monotonic(),
            )

        self.assertEqual(result.kind, "runner_error")
        self.assertEqual(result.limit_reason, "container_cleanup")
        self.assertIn("Could not confirm container removal", result.stderr)
        self.assertEqual(calls[-1][1:3], ["rm", "--force"])

    def test_create_keyboard_interrupt_forces_cleanup_and_reports_cancelled(self) -> None:
        calls = []

        def fake_run(args, **_kwargs):
            calls.append(args)
            if args[1] == "create":
                raise KeyboardInterrupt
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(code_runner.subprocess, "run", side_effect=fake_run):
            result = code_runner._run_oci(
                "/usr/bin/podman",
                "podman",
                code_runner.DEFAULT_RUNNER_IMAGE,
                Path("/tmp/attempt"),
                Path("/tmp/tests"),
                [{"input": [], "expected": 1}],
                code_runner.ResourcePolicy(),
                time.monotonic(),
            )

        self.assertEqual(result.kind, "cancelled")
        self.assertEqual(calls[-1][1:3], ["rm", "--force"])

    def test_started_timeout_and_cancellation_force_remove_container(self) -> None:
        for outcome in ("timeout", "cancelled"):
            with self.subTest(outcome=outcome):
                calls = []
                process = mock.Mock(returncode=137)

                def fake_run(args, **_kwargs):
                    calls.append(args)
                    return subprocess.CompletedProcess(args, 0, "", "")

                with mock.patch.object(
                    code_runner.subprocess,
                    "run",
                    side_effect=fake_run,
                ), mock.patch.object(
                    code_runner.subprocess,
                    "Popen",
                    return_value=process,
                ), mock.patch.object(
                    code_runner,
                    "_supervise_process",
                    return_value=code_runner._result(
                        outcome,
                        "",
                        "",
                        137,
                        time.monotonic(),
                        "wall_time" if outcome == "timeout" else "cancelled",
                        "oci",
                        "docker",
                    ),
                ):
                    result = code_runner._run_oci(
                        "/usr/bin/docker",
                        "docker",
                        code_runner.DEFAULT_RUNNER_IMAGE,
                        Path("/tmp/attempt"),
                        Path("/tmp/tests"),
                        [{"input": [], "expected": 1}],
                        code_runner.ResourcePolicy(),
                        time.monotonic(),
                    )

                self.assertEqual(result.kind, outcome)
                self.assertTrue(
                    any(call[1:3] == ["rm", "--force"] for call in calls)
                )

    @staticmethod
    def _run_reduced(source: str, expected: object) -> code_runner.RunnerResult:
        with tempfile.TemporaryDirectory() as raw:
            solution = Path(raw) / "solution.py"
            solution.write_text(source, encoding="utf-8")
            return code_runner.run_python_tests(
                solution,
                function_name="solve",
                test_cases=[{"input": [], "expected": expected}],
                reduced_isolation=True,
            )

    @staticmethod
    def _deep_tagged_value(depth: int) -> dict[str, object]:
        value: dict[str, object] = {"t": "none"}
        for _ in range(depth):
            value = {"t": "list", "v": [value]}
        return value

    @staticmethod
    def _response_line(request_id: str, value: object) -> bytes:
        payload = {
            "version": code_runner.PROTOCOL_VERSION,
            "request_id": request_id,
            "status": "value",
            "value": value,
        }
        return (
            code_runner.PROTOCOL_PREFIX + json.dumps(payload, separators=(",", ":"))
        ).encode()


if __name__ == "__main__":
    unittest.main()
