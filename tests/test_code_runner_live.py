from __future__ import annotations

import os
import json
import subprocess
import tempfile
import threading
import time
import types
import unittest
import _thread
from pathlib import Path
from unittest import mock

from openlearn import code_runner


LIVE_ENABLED = os.environ.get("OPENLEARN_RUN_OCI_TESTS") == "1"


@unittest.skipUnless(
    LIVE_ENABLED,
    "set OPENLEARN_RUN_OCI_TESTS=1 for the pre-provisioned OCI lane",
)
class LiveCodeRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtimes = []
        cls.diagnostics = []
        for runtime in code_runner.SUPPORTED_RUNTIMES:
            diagnostic = code_runner.diagnose_runtime(preferred=runtime)
            cls.diagnostics.append(f"{runtime}: {diagnostic.detail}")
            if diagnostic.ready:
                cls.runtimes.append(runtime)
        if not cls.runtimes:
            raise unittest.SkipTest(
                "no pre-provisioned runtime/image is ready; "
                + "; ".join(cls.diagnostics)
            )

    def _run(
        self,
        runtime: str,
        source: str,
        *,
        expected: object = True,
        policy: code_runner.ResourcePolicy | None = None,
    ) -> code_runner.RunnerResult:
        with tempfile.TemporaryDirectory() as raw:
            solution = Path(raw) / "solution.py"
            solution.write_text(source, encoding="utf-8")
            return code_runner.run_python_tests(
                solution,
                function_name="probe",
                test_cases=[{"input": [], "expected": expected}],
                policy=policy,
                preferred_runtime=runtime,
            )

    def test_host_environment_workspace_network_and_pid_boundaries(self) -> None:
        for runtime in self.runtimes:
            with self.subTest(runtime=runtime):
                with mock.patch.dict(
                    os.environ,
                    {"OPENLEARN_LIVE_SECRET": "must-not-leak"},
                    clear=False,
                ):
                    environment = self._run(
                        runtime,
                        "import os\n"
                        "def probe():\n"
                        "    return os.environ.get('OPENLEARN_LIVE_SECRET') is None\n",
                    )
                self.assertTrue(environment.passed, environment)

                with tempfile.TemporaryDirectory() as raw:
                    outside = Path(raw) / "outside-secret"
                    outside.write_text("secret", encoding="utf-8")
                    workspace = self._run(
                        runtime,
                        "from pathlib import Path\n"
                        "def probe():\n"
                        f"    return not Path({str(outside)!r}).exists()\n",
                    )
                self.assertTrue(workspace.passed, workspace)

                network = self._run(
                    runtime,
                    "import socket\n"
                    "def probe():\n"
                    "    try:\n"
                    "        socket.create_connection(('1.1.1.1', 53), timeout=0.5)\n"
                    "    except OSError:\n"
                    "        return True\n"
                    "    return False\n",
                )
                self.assertTrue(network.passed, network)

                process_limit = self._run(
                    runtime,
                    "import subprocess\n"
                    "def probe():\n"
                    "    children = []\n"
                    "    limited = False\n"
                    "    try:\n"
                    "        for _ in range(80):\n"
                    "            children.append(subprocess.Popen([\n"
                    "                '/usr/local/bin/python', '-c',\n"
                    "                'import time; time.sleep(2)'\n"
                    "            ]))\n"
                    "    except OSError:\n"
                    "        limited = True\n"
                    "    finally:\n"
                    "        for child in children:\n"
                    "            child.terminate()\n"
                    "    return limited\n",
                )
                self.assertTrue(process_limit.passed, process_limit)

    def test_memory_cpu_file_output_and_wall_limits(self) -> None:
        for runtime in self.runtimes:
            with self.subTest(runtime=runtime, limit="memory"):
                memory = self._run(
                    runtime,
                    "def probe():\n"
                    "    value = bytearray(256 * 1024 * 1024)\n"
                    "    return len(value)\n",
                    expected=256 * 1024 * 1024,
                )
                self.assertIn(memory.kind, {"resource_limit", "runtime_error"})

            with self.subTest(runtime=runtime, limit="cpu"):
                cpu = self._run(
                    runtime,
                    "def probe():\n"
                    "    while True:\n"
                    "        pass\n",
                )
                self.assertEqual(cpu.kind, "resource_limit")

            with self.subTest(runtime=runtime, limit="file"):
                file_limit = self._run(
                    runtime,
                    "def probe():\n"
                    "    with open('/workspace/large.bin', 'wb') as handle:\n"
                    "        handle.write(b'x' * (8 * 1024 * 1024))\n"
                    "    return True\n",
                )
                self.assertIn(file_limit.kind, {"resource_limit", "runtime_error"})

            with self.subTest(runtime=runtime, limit="output"):
                output = self._run(
                    runtime,
                    "def probe():\n"
                    "    while True:\n"
                    "        print('x' * 4096)\n",
                    policy=code_runner.ResourcePolicy(output_bytes=8 * 1024),
                )
                self.assertEqual(output.kind, "output_limit")

            with self.subTest(runtime=runtime, limit="wall"):
                timeout = self._run(
                    runtime,
                    "import time\n"
                    "def probe():\n"
                    "    time.sleep(30)\n"
                    "    return True\n",
                    policy=code_runner.ResourcePolicy(
                        wall_seconds=0.5,
                        cpu_seconds=4,
                    ),
                )
                self.assertEqual(timeout.kind, "timeout")

    def test_timeout_and_cancellation_remove_the_container_tree(self) -> None:
        for runtime in self.runtimes:
            diagnostic = code_runner.diagnose_runtime(preferred=runtime)
            assert diagnostic.executable is not None
            with self.subTest(runtime=runtime, outcome="timeout"):
                with self._private_fixture() as (attempt, harness):
                    survivor = attempt / "child-survived"
                    (attempt / "solution.py").write_text(
                        "import subprocess\n"
                        "def probe():\n"
                        "    subprocess.Popen([\n"
                        "        '/usr/local/bin/python', '-c',\n"
                        "        \"import pathlib,time; time.sleep(2); \"\n"
                        "        \"pathlib.Path('/workspace/child-survived').write_text('bad')\"\n"
                        "    ])\n"
                        "    while True:\n"
                        "        pass\n",
                        encoding="utf-8",
                    )
                    result = code_runner._run_oci(
                        diagnostic.executable,
                        runtime,
                        code_runner.DEFAULT_RUNNER_IMAGE,
                        attempt,
                        harness,
                        code_runner.ResourcePolicy(
                            wall_seconds=0.5,
                            cpu_seconds=4,
                        ),
                        self._request(),
                        "live-request",
                    )
                    self.assertEqual(result.kind, "timeout")
                    time.sleep(2.2)
                    self.assertFalse(survivor.exists())

            with self.subTest(runtime=runtime, outcome="cancelled"):
                name = f"openlearn-live-cancel-{runtime}"
                with self._private_fixture() as (attempt, harness):
                    with mock.patch.object(
                        code_runner,
                        "uuid4",
                        return_value=types.SimpleNamespace(
                            hex=f"live-cancel-{runtime}"
                        ),
                    ):
                        interrupter = threading.Timer(
                            0.5,
                            _thread.interrupt_main,
                        )
                        interrupter.start()
                        try:
                            result = code_runner._run_oci(
                                diagnostic.executable,
                                runtime,
                                code_runner.DEFAULT_RUNNER_IMAGE,
                                attempt,
                                harness,
                                code_runner.ResourcePolicy(),
                                self._request(),
                                "live-request",
                            )
                        finally:
                            interrupter.cancel()
                    self.assertEqual(result.kind, "cancelled")
                    inspected = subprocess.run(
                        [diagnostic.executable, "container", "inspect", name],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    )
                    self.assertNotEqual(inspected.returncode, 0)

    def test_injected_cleanup_failure_is_reported_and_recoverable(self) -> None:
        real_run = subprocess.run
        for runtime in self.runtimes:
            diagnostic = code_runner.diagnose_runtime(preferred=runtime)
            assert diagnostic.executable is not None
            name = f"openlearn-live-cleanup-{runtime}"
            with self.subTest(runtime=runtime), self._private_fixture() as (
                attempt,
                harness,
            ):
                (attempt / "solution.py").write_text(
                    "def probe():\n"
                    "    return True\n",
                    encoding="utf-8",
                )
                injected = {"value": False}

                def fail_first_remove(args, **kwargs):
                    if (
                        args[0] == diagnostic.executable
                        and args[1:3] == ["rm", "--force"]
                        and not injected["value"]
                    ):
                        injected["value"] = True
                        return subprocess.CompletedProcess(
                            args,
                            1,
                            "",
                            "injected cleanup failure",
                        )
                    return real_run(args, **kwargs)

                try:
                    with mock.patch.object(
                        code_runner,
                        "uuid4",
                        return_value=types.SimpleNamespace(
                            hex=f"live-cleanup-{runtime}"
                        ),
                    ), mock.patch.object(
                        code_runner.subprocess,
                        "run",
                        side_effect=fail_first_remove,
                    ):
                        result = code_runner._run_oci(
                            diagnostic.executable,
                            runtime,
                            code_runner.DEFAULT_RUNNER_IMAGE,
                            attempt,
                            harness,
                            code_runner.ResourcePolicy(),
                            self._request(),
                            "live-request",
                        )
                    self.assertEqual(result.kind, "runner_error")
                    self.assertEqual(result.limit_reason, "container_cleanup")
                    self.assertIn("injected cleanup failure", result.stderr)
                finally:
                    real_run(
                        [diagnostic.executable, "rm", "--force", name],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    )

    @staticmethod
    def _private_fixture():
        class Fixture:
            def __enter__(self):
                self.attempt_temp = tempfile.TemporaryDirectory(
                    prefix="openlearn-live-attempt-"
                )
                self.harness_temp = tempfile.TemporaryDirectory(
                    prefix="openlearn-live-tests-"
                )
                attempt = Path(self.attempt_temp.name)
                harness = Path(self.harness_temp.name)
                attempt.chmod(0o777)
                harness.chmod(0o755)
                solution = attempt / "solution.py"
                solution.write_text(
                    "def probe():\n"
                    "    while True:\n"
                    "        pass\n",
                    encoding="utf-8",
                )
                solution.chmod(0o666)
                runner = harness / "call_worker.py"
                runner.write_text(
                    code_runner._python_worker("probe"),
                    encoding="utf-8",
                )
                runner.chmod(0o644)
                return attempt, harness

            def __exit__(self, *_args):
                self.harness_temp.cleanup()
                self.attempt_temp.cleanup()

        return Fixture()

    @staticmethod
    def _request() -> bytes:
        return (
            json.dumps(
                {
                    "version": 1,
                    "request_id": "live-request",
                    "input": [],
                }
            )
            + "\n"
        ).encode()


if __name__ == "__main__":
    unittest.main()
