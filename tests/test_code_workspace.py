from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from openlearn import code_runner
from openlearn.code_workspace import (
    DEFAULT_SOURCE,
    MAX_RESULT_BYTES,
    MAX_SOURCE_BYTES,
    CodeWorkspace,
    CodeWorkspaceConflictError,
    CodeWorkspaceError,
)


def runner_result(
    *,
    kind: str = "success",
    stdout: str = "",
    stderr: str = "",
) -> code_runner.RunnerResult:
    return code_runner.RunnerResult(
        kind=kind,  # type: ignore[arg-type]
        stdout=stdout,
        stderr=stderr,
        exit_code=0 if kind == "success" else 1,
        signal=None,
        duration_seconds=0.01,
        limit_reason="wall_time" if kind == "timeout" else None,
        isolation="reduced",
        runtime=None,
        protections=("wall-time-limit", "output-limit"),
    )


class CodeWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "learning-topics"
        self.root.mkdir()
        (self.root / "python.md").write_text("# Python\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_edit_run_revise_reset_and_refresh_recovery(self) -> None:
        seen: list[str] = []

        def fake_runner(path: Path, **_kwargs) -> code_runner.RunnerResult:
            seen.append(path.read_text(encoding="utf-8"))
            return runner_result(stdout="first run\n")

        workspace = CodeWorkspace(self.root, runner=fake_runner)
        initial = workspace.load("python")
        self.assertEqual(initial.source, DEFAULT_SOURCE)

        saved = workspace.save(
            "python",
            "print('iteration one')\n",
            expected_revision=initial.revision,
        )
        result = workspace.run(
            "python",
            expected_revision=saved.revision,
            reduced_isolation=True,
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.stdout, "first run\n")
        self.assertEqual(seen, ["print('iteration one')\n"])

        refreshed = CodeWorkspace(self.root, runner=fake_runner).load("python")
        self.assertEqual(refreshed, saved)
        revised = workspace.save(
            "python",
            "print('iteration two')\n",
            expected_revision=refreshed.revision,
        )
        reset = workspace.reset("python", expected_revision=revised.revision)
        self.assertEqual(reset.source, DEFAULT_SOURCE)
        self.assertEqual(CodeWorkspace(self.root).load("python"), reset)

    def test_explicit_reduced_run_captures_stdout_and_runtime_outcomes(self) -> None:
        workspace = CodeWorkspace(
            self.root,
            policy=code_runner.ResourcePolicy(
                wall_seconds=0.2,
                cpu_seconds=2,
                memory_bytes=128 * 1024 * 1024,
                process_limit=8,
                output_bytes=4096,
                file_bytes=4096,
            ),
        )
        cases = (
            ("print('hello workspace')\n", "success", "hello workspace", False),
            ("def broken(:\n    pass\n", "compile_error", "SyntaxError", False),
            ("raise RuntimeError('boom')\n", "runtime_error", "boom", False),
            ("print('x' * 8192)\n", "output_limit", "x", False),
            ("while True:\n    pass\n", "timeout", "", True),
        )
        revision = workspace.load("python").revision
        for source, status, expected_output, timed_out in cases:
            with self.subTest(status=status):
                draft = workspace.save(
                    "python",
                    source,
                    expected_revision=revision,
                )
                revision = draft.revision
                result = workspace.run(
                    "python",
                    expected_revision=revision,
                    reduced_isolation=True,
                )
                self.assertEqual(result.status, status)
                self.assertEqual(result.timed_out, timed_out)
                self.assertEqual(result.draft_revision, revision)
                self.assertIn(expected_output, result.stdout + result.stderr)
                if status in {"compile_error", "runtime_error"}:
                    self.assertNotEqual(result.exit_code, 0)

    def test_fast_dual_stream_output_is_stopped_at_a_bounded_queue(self) -> None:
        workspace = CodeWorkspace(
            self.root,
            policy=code_runner.ResourcePolicy(
                wall_seconds=1.0,
                cpu_seconds=2,
                memory_bytes=128 * 1024 * 1024,
                process_limit=8,
                output_bytes=4096,
                file_bytes=4096,
            ),
        )
        initial = workspace.load("python")
        saved = workspace.save(
            "python",
            (
                "import os\n"
                "chunk = b'x' * 4096\n"
                "while True:\n"
                "    os.write(1, chunk)\n"
                "    os.write(2, chunk)\n"
            ),
            expected_revision=initial.revision,
        )

        result = workspace.run(
            "python",
            expected_revision=saved.revision,
            reduced_isolation=True,
        )

        self.assertEqual(result.status, "output_limit")
        self.assertLessEqual(
            len(result.stdout.encode()) + len(result.stderr.encode()),
            workspace.policy.output_bytes,
        )
        self.assertLess(result.duration_seconds, 1.5)

    def test_last_run_result_recovers_and_reset_clears_it(self) -> None:
        workspace = CodeWorkspace(
            self.root,
            runner=lambda _path, **_kwargs: runner_result(stdout="persisted output\n"),
        )
        initial = workspace.load("python")
        saved = workspace.save(
            "python", "print('persisted')\n", expected_revision=initial.revision
        )

        result = workspace.run("python", expected_revision=saved.revision)
        recovered = CodeWorkspace(self.root).load_last_result("python")

        self.assertEqual(recovered, result)
        workspace.reset("python", expected_revision=saved.revision)
        self.assertIsNone(CodeWorkspace(self.root).load_last_result("python"))

    def test_state_keeps_draft_and_result_atomic_during_concurrent_save(self) -> None:
        workspace = CodeWorkspace(
            self.root,
            runner=lambda _path, **_kwargs: runner_result(stdout="  exact output  \n\n"),
        )
        initial = workspace.load("python")
        saved = workspace.save(
            "python", "print('first')\n", expected_revision=initial.revision
        )
        first_result = workspace.run("python", expected_revision=saved.revision)
        read_started = threading.Barrier(2)
        release_read = threading.Event()
        writer_started = threading.Event()
        original_read_result = workspace._read_result
        observed = []
        second_saved = []

        def paused_read_result(path: Path):
            result = original_read_result(path)
            read_started.wait(timeout=2)
            release_read.wait(timeout=2)
            return result

        def read_state() -> None:
            observed.append(workspace.state("python"))

        def save_second() -> None:
            writer_started.set()
            second_saved.append(
                workspace.save(
                    "python",
                    "print('second')\n",
                    expected_revision=saved.revision,
                )
            )

        with mock.patch.object(workspace, "_read_result", side_effect=paused_read_result):
            reader = threading.Thread(target=read_state)
            reader.start()
            read_started.wait(timeout=2)
            writer = threading.Thread(target=save_second)
            writer.start()
            writer_started.wait(timeout=2)
            self.assertTrue(writer.is_alive())
            release_read.set()
            reader.join(timeout=2)
            writer.join(timeout=2)

        self.assertEqual(observed[0].draft, saved)
        self.assertEqual(observed[0].result, first_result)
        self.assertEqual(observed[0].result.draft_revision, observed[0].draft.revision)
        self.assertEqual(second_saved[0].source, "print('second')\n")

    def test_concurrent_stale_saves_are_atomic_and_one_wins(self) -> None:
        workspace = CodeWorkspace(self.root)
        initial = workspace.load("python")
        barrier = threading.Barrier(3)
        saved: list[str] = []
        errors: list[BaseException] = []

        def write(source: str) -> None:
            barrier.wait()
            try:
                result = workspace.save(
                    "python",
                    source,
                    expected_revision=initial.revision,
                )
                saved.append(result.source)
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=write, args=("print('one')\n",)),
            threading.Thread(target=write, args=("print('two')\n",)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(len(saved), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CodeWorkspaceConflictError)
        self.assertEqual(workspace.load("python").source, saved[0])

    def test_loading_and_saving_never_executes_imported_source(self) -> None:
        marker = Path(self.temporary.name) / "executed"
        source = (
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n"
        )
        workspace = CodeWorkspace(self.root)
        workspace.save("python", source)

        recovered = CodeWorkspace(self.root).load("python")

        self.assertEqual(recovered.source, source)
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / "python.state.json").exists())
        self.assertFalse((self.root / "python.events.jsonl").exists())

    def test_course_and_owned_path_validation_fail_closed(self) -> None:
        workspace = CodeWorkspace(self.root)
        for slug in ("../python", "Python", "python/other", ""):
            with self.subTest(slug=slug), self.assertRaises(CodeWorkspaceError):
                workspace.load(slug)
        with self.assertRaisesRegex(CodeWorkspaceError, "course not found"):
            workspace.load("missing")

        unsafe = self.root / "drills"
        unsafe.symlink_to(Path(self.temporary.name), target_is_directory=True)
        with self.assertRaisesRegex(CodeWorkspaceError, "unsafe"):
            workspace.load("python")

    def test_source_and_result_are_bounded(self) -> None:
        workspace = CodeWorkspace(
            self.root,
            runner=lambda _path, **_kwargs: runner_result(
                stdout="x" * (MAX_RESULT_BYTES + 100),
                stderr="y" * (MAX_RESULT_BYTES + 100),
            ),
        )
        with self.assertRaisesRegex(CodeWorkspaceError, "too large"):
            workspace.save("python", "x" * (MAX_SOURCE_BYTES + 1))

        result = workspace.run("python", reduced_isolation=True)

        self.assertLessEqual(len(result.stdout.encode()), MAX_RESULT_BYTES)
        self.assertLessEqual(len(result.stderr.encode()), MAX_RESULT_BYTES)
        self.assertTrue(result.stdout.endswith("[output truncated]"))
        self.assertEqual(workspace.load_last_result("python"), result)

    def test_stale_run_is_rejected_before_runner_invocation(self) -> None:
        runner_called = False

        def fail_if_called(_path: Path, **_kwargs) -> code_runner.RunnerResult:
            nonlocal runner_called
            runner_called = True
            return runner_result()

        workspace = CodeWorkspace(self.root, runner=fail_if_called)
        stale = workspace.load("python")
        workspace.save("python", "print('new')\n")

        with self.assertRaises(CodeWorkspaceConflictError):
            workspace.run(
                "python",
                expected_revision=stale.revision,
                reduced_isolation=True,
            )
        self.assertFalse(runner_called)

    def test_runner_unavailable_is_structured(self) -> None:
        def unavailable(_path: Path, **_kwargs) -> code_runner.RunnerResult:
            raise code_runner.RunnerUnavailableError("install an OCI runtime")

        result = CodeWorkspace(self.root, runner=unavailable).run("python")

        self.assertEqual(result.status, "runner_unavailable")
        self.assertEqual(result.limit_reason, "runner_unavailable")
        self.assertEqual(
            result.stderr,
            "Secure code execution needs Docker or Podman. Install or start one, then run openlearn doctor.",
        )
        self.assertNotIn("install an OCI runtime", result.stderr)

    def test_script_oci_command_preserves_runner_protections(self) -> None:
        command = code_runner.build_oci_script_command(
            "/usr/bin/docker",
            "docker",
            code_runner.DEFAULT_RUNNER_IMAGE,
            Path("/tmp/attempt"),
            "openlearn-fixed",
            code_runner.ResourcePolicy(),
        )

        self.assertEqual(command[:2], ["/usr/bin/docker", "create"])
        self.assertEqual(command[command.index("--pull") + 1], "never")
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertIn("--read-only", command)
        self.assertEqual(command[command.index("--cap-drop") + 1], "ALL")
        self.assertIn("readonly", command[command.index("--mount") + 1])
        self.assertNotIn("/var/run/docker.sock", " ".join(command))


if __name__ == "__main__":
    unittest.main()
