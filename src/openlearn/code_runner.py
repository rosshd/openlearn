from __future__ import annotations

import contextlib
import json
import math
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4


DEFAULT_RUNNER_IMAGE = (
    "docker.io/library/python:3.13.5-slim-bookworm@"
    "sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419"
)
SUPPORTED_RUNTIMES = ("docker", "podman")
OCI_CREATE_TIMEOUT_SECONDS = 15
PROTOCOL_PREFIX = "OPENLEARN_CALL_RESULT_V1 "
PROTOCOL_VERSION = 1
PROTOCOL_MAX_FRAME_BYTES = 64 * 1024
VALUE_MAX_DEPTH = 64
VALUE_MAX_ITEMS = 4096


@dataclass(frozen=True)
class ResourcePolicy:
    wall_seconds: float = 8.0
    cpu_seconds: int = 4
    memory_bytes: int = 128 * 1024 * 1024
    process_limit: int = 32
    output_bytes: int = 64 * 1024
    file_bytes: int = 4 * 1024 * 1024


@dataclass(frozen=True)
class RuntimeDiagnostic:
    runtime: str | None
    executable: str | None
    runtime_ready: bool
    image_ready: bool
    image: str
    detail: str

    @property
    def ready(self) -> bool:
        return self.runtime_ready and self.image_ready


@dataclass(frozen=True)
class RunnerResult:
    kind: Literal[
        "success",
        "test_failure",
        "compile_error",
        "runtime_error",
        "timeout",
        "output_limit",
        "resource_limit",
        "cancelled",
        "runner_error",
    ]
    stdout: str
    stderr: str
    exit_code: int | None
    signal: int | None
    duration_seconds: float
    limit_reason: str | None
    isolation: Literal["oci", "reduced"]
    runtime: str | None
    protections: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.kind == "success"


class RunnerUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class _CallResult:
    kind: str
    value: object | None
    stdout: str
    stderr: str
    exit_code: int | None
    limit_reason: str | None


@dataclass(frozen=True)
class _UnsupportedValue:
    type_name: str


def diagnose_runtime(
    *,
    preferred: str | None = None,
    image: str = DEFAULT_RUNNER_IMAGE,
    run=subprocess.run,
) -> RuntimeDiagnostic:
    if "@sha256:" not in image:
        return RuntimeDiagnostic(
            None,
            None,
            False,
            False,
            image,
            "runner image is not pinned by digest",
        )
    candidates = (preferred,) if preferred else SUPPORTED_RUNTIMES
    diagnostics: list[RuntimeDiagnostic] = []
    for runtime in candidates:
        if runtime not in SUPPORTED_RUNTIMES:
            return RuntimeDiagnostic(
                None,
                None,
                False,
                False,
                image,
                f"unsupported OCI runtime: {runtime}",
            )
        executable = shutil.which(runtime)
        if executable is None:
            continue
        try:
            info = run(
                [executable, "info"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            diagnostic = RuntimeDiagnostic(
                runtime, executable, False, False, image, str(exc)
            )
            diagnostics.append(diagnostic)
            if preferred:
                return diagnostic
            continue
        if info.returncode != 0:
            detail = (info.stderr or info.stdout or "runtime service is unavailable").strip()
            diagnostic = RuntimeDiagnostic(
                runtime, executable, False, False, image, detail[:500]
            )
            diagnostics.append(diagnostic)
            if preferred:
                return diagnostic
            continue
        try:
            inspect = run(
                [executable, "image", "inspect", image],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            diagnostic = RuntimeDiagnostic(
                runtime, executable, True, False, image, str(exc)
            )
            diagnostics.append(diagnostic)
            if preferred:
                return diagnostic
            continue
        if inspect.returncode != 0:
            diagnostic = RuntimeDiagnostic(
                runtime,
                executable,
                True,
                False,
                image,
                "pinned runner image is not present locally",
            )
            diagnostics.append(diagnostic)
            if preferred:
                return diagnostic
            continue
        return RuntimeDiagnostic(
            runtime,
            executable,
            True,
            True,
            image,
            "OCI runtime and pinned runner image are ready",
        )
    if diagnostics:
        selected = next(
            (diagnostic for diagnostic in diagnostics if diagnostic.runtime_ready),
            diagnostics[0],
        )
        detail = "; ".join(
            f"{diagnostic.runtime}: {diagnostic.detail}"
            for diagnostic in diagnostics
        )
        return RuntimeDiagnostic(
            selected.runtime,
            selected.executable,
            selected.runtime_ready,
            selected.image_ready,
            image,
            detail[:1_000],
        )
    return RuntimeDiagnostic(None, None, False, False, image, "Docker or Podman was not found")


def runtime_setup_guidance(diagnostic: RuntimeDiagnostic) -> str:
    if diagnostic.runtime is None:
        return (
            "Secure code execution requires Docker or Podman. Install and start one, "
            "then run 'openlearn doctor'. openLearn never installs a runtime automatically."
        )
    if not diagnostic.runtime_ready:
        return (
            f"{diagnostic.runtime} is installed but not ready: {diagnostic.detail}. "
            "Start its service or desktop application, then run 'openlearn doctor'."
        )
    if not diagnostic.image_ready:
        return (
            "The pinned runner image is not available offline. Review the image, then "
            f"explicitly acquire it with:\n  {diagnostic.runtime} pull {diagnostic.image}\n"
            "openLearn never pulls runner images during /check."
        )
    return "Secure OCI code execution is ready."


def run_python_tests(
    solution: Path,
    *,
    function_name: str,
    test_cases: list[dict[str, object]],
    policy: ResourcePolicy | None = None,
    reduced_isolation: bool = False,
    preferred_runtime: str | None = None,
    image: str = DEFAULT_RUNNER_IMAGE,
) -> RunnerResult:
    policy = policy or ResourcePolicy()
    _validate_request(solution, function_name, test_cases, policy)
    started = time.monotonic()
    diagnostic = None
    if not reduced_isolation:
        diagnostic = diagnose_runtime(preferred=preferred_runtime, image=image)
        if not diagnostic.ready:
            raise RunnerUnavailableError(runtime_setup_guidance(diagnostic))
        assert diagnostic.executable is not None
        assert diagnostic.runtime is not None
    with tempfile.TemporaryDirectory(prefix="openlearn-attempt-") as attempt_raw:
        with tempfile.TemporaryDirectory(prefix="openlearn-worker-") as worker_raw:
            attempt = Path(attempt_raw)
            worker_dir = Path(worker_raw)
            attempt.chmod(0o777)
            worker_dir.chmod(0o755)
            copied_solution = attempt / "solution.py"
            shutil.copyfile(solution, copied_solution)
            copied_solution.chmod(0o666)
            worker = worker_dir / "call_worker.py"
            worker.write_text(_python_worker(function_name), encoding="utf-8")
            worker.chmod(0o644)
            if reduced_isolation:
                return _run_reduced(
                    copied_solution,
                    worker,
                    test_cases,
                    policy,
                    started,
                )
            assert diagnostic is not None
            assert diagnostic.executable is not None
            assert diagnostic.runtime is not None
            return _run_oci(
                diagnostic.executable,
                diagnostic.runtime,
                image,
                attempt,
                worker_dir,
                test_cases,
                policy,
                started,
            )


def run_python_script(
    source: Path,
    *,
    policy: ResourcePolicy | None = None,
    reduced_isolation: bool = False,
    preferred_runtime: str | None = None,
    image: str = DEFAULT_RUNNER_IMAGE,
) -> RunnerResult:
    """Run one Python script under the same bounded isolation contract as drills."""
    policy = policy or ResourcePolicy()
    _validate_request(
        source,
        "_openlearn_validate_policy",
        [{"input": [], "expected": None}],
        policy,
    )
    started = time.monotonic()
    diagnostic = None
    if not reduced_isolation:
        diagnostic = diagnose_runtime(preferred=preferred_runtime, image=image)
        if not diagnostic.ready:
            raise RunnerUnavailableError(runtime_setup_guidance(diagnostic))
        assert diagnostic.executable is not None
        assert diagnostic.runtime is not None
    with tempfile.TemporaryDirectory(prefix="openlearn-script-") as attempt_raw:
        attempt = Path(attempt_raw)
        attempt.chmod(0o777)
        copied_source = attempt / "workspace.py"
        shutil.copyfile(source, copied_source)
        copied_source.chmod(0o666)
        if reduced_isolation:
            return _run_script_reduced(copied_source, policy, started)
        assert diagnostic is not None
        assert diagnostic.executable is not None
        assert diagnostic.runtime is not None
        return _run_script_oci(
            diagnostic.executable,
            diagnostic.runtime,
            image,
            attempt,
            policy,
            started,
        )


def build_oci_create_command(
    executable: str,
    runtime: str,
    image: str,
    attempt: Path,
    worker: Path,
    name: str,
    policy: ResourcePolicy,
) -> list[str]:
    if runtime not in SUPPORTED_RUNTIMES:
        raise ValueError(f"unsupported OCI runtime: {runtime}")
    if "@sha256:" not in image:
        raise ValueError("runner image must be pinned by digest")
    return [
        executable,
        "create",
        "--interactive",
        "--name",
        name,
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65532:65532",
        "--workdir",
        "/workspace",
        "--memory",
        str(policy.memory_bytes),
        "--memory-swap",
        str(policy.memory_bytes),
        "--cpus",
        "1",
        "--pids-limit",
        str(policy.process_limit),
        "--ulimit",
        f"cpu={policy.cpu_seconds}:{policy.cpu_seconds}",
        "--ulimit",
        f"fsize={policy.file_bytes}:{policy.file_bytes}",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=16777216",
        "--mount",
        f"type=bind,src={attempt},dst=/workspace,rw",
        "--mount",
        f"type=bind,src={worker},dst=/opt/openlearn-worker,readonly",
        image,
        "/usr/bin/env",
        "-i",
        "HOME=/tmp",
        "TMPDIR=/tmp",
        "PYTHONDONTWRITEBYTECODE=1",
        "/usr/local/bin/python",
        "-I",
        "/opt/openlearn-worker/call_worker.py",
        "/workspace/solution.py",
    ]


def _run_oci(
    executable: str,
    runtime: str,
    image: str,
    attempt: Path,
    worker: Path,
    test_cases: list[dict[str, object]],
    policy: ResourcePolicy,
    started: float,
) -> RunnerResult:
    name = f"openlearn-{uuid4().hex}"
    command = build_oci_create_command(
        executable, runtime, image, attempt, worker, name, policy
    )
    removed = {"value": False}
    cleanup_detail = {"value": ""}
    cleanup_failed = {"value": False}

    def remove_container() -> bool:
        if removed["value"]:
            return True
        try:
            result = subprocess.run(
                [executable, "rm", "--force", name],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            cleanup_detail["value"] = str(exc)
            cleanup_failed["value"] = True
            return False
        detail = (result.stderr or result.stdout or "").strip()
        missing_container = any(
            marker in detail.casefold()
            for marker in (
                "no such container",
                "no container with name or id",
                "does not exist",
            )
        )
        if result.returncode == 0 or missing_container:
            removed["value"] = True
            return True
        cleanup_detail["value"] = detail[:500] or f"exit {result.returncode}"
        cleanup_failed["value"] = True
        return False

    remaining_before_create = started + policy.wall_seconds - time.monotonic()
    if remaining_before_create <= 0:
        return _result(
            "timeout",
            "",
            "",
            None,
            started,
            "wall_time",
            "oci",
            runtime,
        )
    try:
        created = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=min(OCI_CREATE_TIMEOUT_SECONDS, remaining_before_create),
        )
    except KeyboardInterrupt:
        cleanup_ok = remove_container()
        return _result(
            "cancelled" if cleanup_ok else "runner_error",
            "",
            cleanup_detail["value"],
            None,
            started,
            "cancelled" if cleanup_ok else "container_cleanup",
            "oci",
            runtime,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        cleanup_ok = remove_container()
        detail = str(exc)
        if not cleanup_ok:
            detail = f"{detail}\nCould not confirm container removal: {cleanup_detail['value']}"
        return _result(
            "runner_error",
            "",
            detail,
            None,
            started,
            "container_create" if cleanup_ok else "container_cleanup",
            "oci",
            runtime,
        )
    if created.returncode != 0:
        cleanup_ok = remove_container()
        stderr = created.stderr
        if not cleanup_ok:
            stderr = "\n".join(
                part
                for part in (
                    stderr,
                    f"Could not confirm container removal: {cleanup_detail['value']}",
                )
                if part
            )
        return _result(
            "runner_error",
            created.stdout,
            stderr,
            created.returncode,
            started,
            "container_create" if cleanup_ok else "container_cleanup",
            "oci",
            runtime,
        )

    remaining_wall = started + policy.wall_seconds - time.monotonic()
    if remaining_wall <= 0:
        cleanup_ok = remove_container()
        return _result(
            "timeout" if cleanup_ok else "runner_error",
            "",
            cleanup_detail["value"],
            None,
            started,
            "wall_time" if cleanup_ok else "container_cleanup",
            "oci",
            runtime,
        )
    try:
        try:
            process = subprocess.Popen(
                [executable, "start", "--attach", "--interactive", name],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            session_result = _supervise_process(
                process,
                test_cases,
                policy,
                started=started,
                isolation="oci",
                runtime=runtime,
                terminate=remove_container,
            )
        except KeyboardInterrupt:
            cleanup_ok = remove_container()
            return _result(
                "cancelled" if cleanup_ok else "runner_error",
                "",
                cleanup_detail["value"],
                None,
                started,
                "cancelled" if cleanup_ok else "container_cleanup",
                "oci",
                runtime,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            cleanup_ok = remove_container()
            detail = str(exc)
            if not cleanup_ok:
                detail = (
                    f"{detail}\nCould not confirm container removal: "
                    f"{cleanup_detail['value']}"
                )
            return _result(
                "runner_error",
                "",
                detail,
                None,
                started,
                "container_start" if cleanup_ok else "container_cleanup",
                "oci",
                runtime,
            )
    finally:
        cleanup_ok = remove_container()
    if not cleanup_ok or cleanup_failed["value"]:
        return _result(
            "runner_error",
            session_result.stdout,
            "\n".join(
                part
                for part in (
                    session_result.stderr,
                    f"Could not confirm container removal: {cleanup_detail['value']}",
                )
                if part
            ),
            session_result.exit_code,
            started,
            "container_cleanup",
            "oci",
            runtime,
        )
    return session_result


def _run_reduced(
    solution: Path,
    worker: Path,
    test_cases: list[dict[str, object]],
    policy: ResourcePolicy,
    started: float,
) -> RunnerResult:
    popen_kwargs: dict[str, object] = {
        "cwd": str(solution.parent),
        "env": {
            "HOME": str(solution.parent),
            "TMPDIR": str(solution.parent),
            "PATH": os.defpath,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
        popen_kwargs["preexec_fn"] = _posix_limits(policy)
    elif os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        [sys.executable, "-I", str(worker), str(solution)],
        **popen_kwargs,
    )

    def terminate() -> None:
        if process.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                pass
        elif os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            process.kill()

    return _supervise_process(
        process,
        test_cases,
        policy,
        started=started,
        isolation="reduced",
        runtime=None,
        terminate=terminate,
    )


def build_oci_script_command(
    executable: str,
    runtime: str,
    image: str,
    attempt: Path,
    name: str,
    policy: ResourcePolicy,
) -> list[str]:
    """Build a fail-closed OCI command for an explicitly requested script run."""
    if runtime not in SUPPORTED_RUNTIMES:
        raise ValueError(f"unsupported OCI runtime: {runtime}")
    if "@sha256:" not in image:
        raise ValueError("runner image must be pinned by digest")
    return [
        executable,
        "create",
        "--name",
        name,
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65532:65532",
        "--workdir",
        "/workspace",
        "--memory",
        str(policy.memory_bytes),
        "--memory-swap",
        str(policy.memory_bytes),
        "--cpus",
        "1",
        "--pids-limit",
        str(policy.process_limit),
        "--ulimit",
        f"cpu={policy.cpu_seconds}:{policy.cpu_seconds}",
        "--ulimit",
        f"fsize={policy.file_bytes}:{policy.file_bytes}",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=16777216",
        "--mount",
        f"type=bind,src={attempt},dst=/workspace,readonly",
        image,
        "/usr/bin/env",
        "-i",
        "HOME=/tmp",
        "TMPDIR=/tmp",
        "PYTHONDONTWRITEBYTECODE=1",
        "/usr/local/bin/python",
        "-I",
        "/workspace/workspace.py",
    ]


def _run_script_oci(
    executable: str,
    runtime: str,
    image: str,
    attempt: Path,
    policy: ResourcePolicy,
    started: float,
) -> RunnerResult:
    name = f"openlearn-{uuid4().hex}"
    command = build_oci_script_command(
        executable,
        runtime,
        image,
        attempt,
        name,
        policy,
    )
    removed = False
    cleanup_detail = ""

    def remove_container() -> bool:
        nonlocal removed, cleanup_detail
        if removed:
            return True
        try:
            result = subprocess.run(
                [executable, "rm", "--force", name],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            cleanup_detail = str(exc)
            return False
        detail = (result.stderr or result.stdout or "").strip()
        missing = any(
            marker in detail.casefold()
            for marker in (
                "no such container",
                "no container with name or id",
                "does not exist",
            )
        )
        if result.returncode == 0 or missing:
            removed = True
            return True
        cleanup_detail = detail[:500] or f"exit {result.returncode}"
        return False

    remaining = started + policy.wall_seconds - time.monotonic()
    if remaining <= 0:
        return _result("timeout", "", "", None, started, "wall_time", "oci", runtime)
    try:
        created = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=min(OCI_CREATE_TIMEOUT_SECONDS, remaining),
        )
    except KeyboardInterrupt:
        cleanup_ok = remove_container()
        return _result(
            "cancelled" if cleanup_ok else "runner_error",
            "",
            cleanup_detail,
            None,
            started,
            "cancelled" if cleanup_ok else "container_cleanup",
            "oci",
            runtime,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        cleanup_ok = remove_container()
        detail = str(exc)
        if not cleanup_ok:
            detail = f"{detail}\nCould not confirm container removal: {cleanup_detail}"
        return _result(
            "runner_error",
            "",
            detail,
            None,
            started,
            "container_create" if cleanup_ok else "container_cleanup",
            "oci",
            runtime,
        )
    if created.returncode != 0:
        cleanup_ok = remove_container()
        detail = created.stderr
        if not cleanup_ok:
            detail = "\n".join(
                part
                for part in (detail, f"Could not confirm container removal: {cleanup_detail}")
                if part
            )
        return _result(
            "runner_error",
            created.stdout,
            detail,
            created.returncode,
            started,
            "container_create" if cleanup_ok else "container_cleanup",
            "oci",
            runtime,
        )

    remaining = started + policy.wall_seconds - time.monotonic()
    if remaining <= 0:
        cleanup_ok = remove_container()
        return _result(
            "timeout" if cleanup_ok else "runner_error",
            "",
            cleanup_detail,
            None,
            started,
            "wall_time" if cleanup_ok else "container_cleanup",
            "oci",
            runtime,
        )
    try:
        process = subprocess.Popen(
            [executable, "start", "--attach", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        result = _supervise_script_process(
            process,
            policy,
            started=started,
            isolation="oci",
            runtime=runtime,
            terminate=remove_container,
        )
    except KeyboardInterrupt:
        cleanup_ok = remove_container()
        return _result(
            "cancelled" if cleanup_ok else "runner_error",
            "",
            cleanup_detail,
            None,
            started,
            "cancelled" if cleanup_ok else "container_cleanup",
            "oci",
            runtime,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        cleanup_ok = remove_container()
        detail = str(exc)
        if not cleanup_ok:
            detail = f"{detail}\nCould not confirm container removal: {cleanup_detail}"
        return _result(
            "runner_error",
            "",
            detail,
            None,
            started,
            "container_start" if cleanup_ok else "container_cleanup",
            "oci",
            runtime,
        )
    cleanup_ok = remove_container()
    if cleanup_ok:
        return result
    return _result(
        "runner_error",
        result.stdout,
        "\n".join(
            part
            for part in (result.stderr, f"Could not confirm container removal: {cleanup_detail}")
            if part
        ),
        result.exit_code,
        started,
        "container_cleanup",
        "oci",
        runtime,
    )


def _run_script_reduced(
    source: Path,
    policy: ResourcePolicy,
    started: float,
) -> RunnerResult:
    popen_kwargs: dict[str, object] = {
        "cwd": str(source.parent),
        "env": {
            "HOME": str(source.parent),
            "TMPDIR": str(source.parent),
            "PATH": os.defpath,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
        popen_kwargs["preexec_fn"] = _posix_limits(policy)
    elif os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen([sys.executable, "-I", str(source)], **popen_kwargs)

    def terminate() -> bool:
        if process.poll() is not None:
            return True
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                pass
        elif os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            process.kill()
        return True

    return _supervise_script_process(
        process,
        policy,
        started=started,
        isolation="reduced",
        runtime=None,
        terminate=terminate,
    )


def _supervise_script_process(
    process: subprocess.Popen[bytes],
    policy: ResourcePolicy,
    *,
    started: float,
    isolation: Literal["oci", "reduced"],
    runtime: str | None,
    terminate,
) -> RunnerResult:
    assert process.stdout is not None
    assert process.stderr is not None
    # Keep host-side buffering bounded as well as the returned output.  Without
    # backpressure, a fast child can fill an unbounded Queue before the
    # supervisor observes the output limit.
    events: queue.Queue[tuple[str, bytes | None]] = queue.Queue(maxsize=16)
    output = {"stdout": bytearray(), "stderr": bytearray()}
    total_bytes = 0
    streams_closed = 0
    deadline = started + policy.wall_seconds
    stop_reading = threading.Event()

    def drain(label: str, stream) -> None:
        read = getattr(stream, "read1", stream.read)
        while not stop_reading.is_set():
            chunk = read(4096)
            if not chunk:
                while not stop_reading.is_set():
                    try:
                        events.put((label, None), timeout=0.05)
                        break
                    except queue.Full:
                        continue
                return
            while not stop_reading.is_set():
                try:
                    events.put((label, chunk), timeout=0.05)
                    break
                except queue.Full:
                    continue

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    kind: str | None = None
    reason: str | None = None
    while streams_closed < len(readers):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            kind, reason = "timeout", "wall_time"
            break
        try:
            label, chunk = events.get(timeout=min(0.05, remaining))
        except queue.Empty:
            if process.poll() is not None and all(not reader.is_alive() for reader in readers):
                break
            continue
        if chunk is None:
            streams_closed += 1
            continue
        available = max(0, policy.output_bytes - total_bytes)
        output[label].extend(chunk[:available])
        total_bytes += len(chunk)
        if total_bytes > policy.output_bytes:
            kind, reason = "output_limit", "captured_output"
            break

    if kind is not None:
        stop_reading.set()
        terminate()
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        stop_reading.set()
        terminate()
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            process.wait(timeout=1)
    stop_reading.set()
    for reader in readers:
        reader.join(timeout=1)
    for stream in (process.stdout, process.stderr):
        with contextlib.suppress(OSError):
            stream.close()

    stdout = output["stdout"].decode("utf-8", errors="replace")
    stderr = output["stderr"].decode("utf-8", errors="replace")
    if kind is None:
        if process.returncode == 0:
            kind = "success"
        else:
            classified, reason = _classify_process_exit(process.returncode, isolation)
            kind = (
                "compile_error"
                if classified == "runtime_error" and "SyntaxError:" in stderr
                else classified
            )
    return _result(
        kind,
        stdout,
        stderr,
        process.returncode,
        started,
        reason,
        isolation,
        runtime,
    )


def _supervise_process(
    process: subprocess.Popen[bytes],
    test_cases: list[dict[str, object]],
    policy: ResourcePolicy,
    *,
    started: float,
    isolation: Literal["oci", "reduced"],
    runtime: str | None,
    terminate,
) -> RunnerResult:
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    events: queue.Queue[tuple[str, bytes | None]] = queue.Queue()
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    raw_bytes = 0
    derived_bytes = 0
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    failed = False
    deadline = started + policy.wall_seconds

    def drain(label: str, stream) -> None:
        read = getattr(stream, "read1", stream.read)
        while True:
            chunk = read(4096)
            if not chunk:
                events.put((label, None))
                return
            events.put((label, chunk))

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    def collect(timeout: float) -> bool:
        nonlocal raw_bytes
        try:
            label, chunk = events.get(timeout=timeout)
        except queue.Empty:
            return False
        if chunk is None:
            return False
        available = max(0, policy.output_bytes - raw_bytes - derived_bytes)
        if label == "stdout":
            stdout_buffer.extend(chunk)
        else:
            stderr_buffer.extend(chunk[:available])
        raw_bytes += len(chunk)
        return raw_bytes + derived_bytes > policy.output_bytes

    def stop(
        kind: str,
        reason: str | None,
        *,
        exit_code: int | None = None,
    ) -> RunnerResult:
        terminate()
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass
        for reader in readers:
            reader.join(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        raw_stderr = stderr_buffer.decode("utf-8", errors="replace").strip()
        combined_stderr = "\n".join(
            part for part in (raw_stderr, *stderr_parts) if part
        )
        reported_exit_code = exit_code
        if reported_exit_code is None:
            reported_exit_code = {
                "compile_error": 20,
                "runtime_error": 21,
                "test_failure": 10,
            }.get(kind, process.returncode)
        return _result(
            kind,
            "\n".join(stdout_parts),
            combined_stderr,
            reported_exit_code,
            started,
            reason,
            isolation,
            runtime,
        )

    def charge(text: str, destination: list[str]) -> bool:
        nonlocal derived_bytes
        encoded_size = len(text.encode("utf-8"))
        if raw_bytes + derived_bytes + encoded_size > policy.output_bytes:
            return False
        derived_bytes += encoded_size
        destination.append(text)
        return True

    def write_frame(payload: dict[str, object]) -> tuple[str | None, bytes | None]:
        try:
            frame = (
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode()
        except (TypeError, ValueError, RecursionError):
            return "invalid_worker_protocol", None
        if len(frame) > PROTOCOL_MAX_FRAME_BYTES:
            return "invalid_worker_protocol", None
        outcome: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)

        def write() -> None:
            try:
                remaining = memoryview(frame)
                while remaining:
                    written = process.stdin.write(remaining)
                    if not isinstance(written, int) or written <= 0:
                        raise BrokenPipeError("worker stdin accepted no data")
                    remaining = remaining[written:]
                process.stdin.flush()
            except BaseException as exc:
                outcome.put(exc)
            else:
                outcome.put(None)

        threading.Thread(target=write, daemon=True).start()
        while outcome.empty():
            if raw_bytes + derived_bytes > policy.output_bytes:
                return "output_limit", None
            if time.monotonic() >= deadline:
                return "timeout", None
            if process.poll() is not None:
                return "abrupt_learner_exit", None
            if collect(0.02):
                return "output_limit", None
        error = outcome.get()
        if error is not None:
            return (
                "abrupt_learner_exit"
                if isinstance(error, (BrokenPipeError, OSError))
                else "invalid_worker_protocol",
                None,
            )
        if raw_bytes + derived_bytes > policy.output_bytes:
            return "output_limit", None
        while b"\n" not in stdout_buffer:
            if raw_bytes + derived_bytes > policy.output_bytes:
                return "output_limit", None
            if time.monotonic() >= deadline:
                return "timeout", None
            if process.poll() is not None and events.empty():
                return "abrupt_learner_exit", None
            if collect(0.02):
                return "output_limit", None
        line, _, remainder = stdout_buffer.partition(b"\n")
        stdout_buffer.clear()
        stdout_buffer.extend(remainder)
        while not events.empty():
            collect(0)
            if raw_bytes + derived_bytes > policy.output_bytes:
                return "output_limit", None
        if stdout_buffer:
            return "invalid_worker_protocol", None
        if raw_bytes + derived_bytes > policy.output_bytes:
            return "output_limit", None
        return None, bytes(line)

    try:
        for index, case in enumerate(test_cases, 1):
            request_id = uuid4().hex
            try:
                tagged_input = _encode_tagged(case["input"])
            except (TypeError, ValueError, RecursionError):
                return stop("runner_error", "invalid_test_input")
            write_error, line = write_frame(
                {
                    "version": PROTOCOL_VERSION,
                    "request_id": request_id,
                    "input": tagged_input,
                }
            )
            if write_error is not None:
                if write_error == "abrupt_learner_exit":
                    kind, reason = _classify_process_exit(
                        process.returncode,
                        isolation,
                    )
                    return stop(kind, reason)
                kind = (
                    write_error
                    if write_error in {"timeout", "output_limit"}
                    else "runtime_error"
                )
                reason = {
                    "timeout": "wall_time",
                    "output_limit": "captured_output",
                }.get(write_error, write_error)
                return stop(kind, reason)
            assert line is not None
            call = _decode_response(line, request_id)
            if call.kind != "value":
                if call.kind == "unsupported":
                    failed = True
                    rendered = f"<unsupported {call.limit_reason or 'value'}>"
                    expected = _bounded_repr(case["expected"])
                    feedback = (
                        f"FAILED test_case_{index}: expected {expected}, got {rendered}"
                    )
                    if not charge(feedback, stderr_parts):
                        return stop("output_limit", "captured_output")
                    continue
                kind = "runtime_error" if call.kind == "protocol_error" else call.kind
                if call.stderr and not charge(call.stderr, stderr_parts):
                    return stop("output_limit", "captured_output")
                return stop(kind, call.limit_reason, exit_code=call.exit_code)
            if call.value == case["expected"]:
                if not charge(f"PASSED test_case_{index}", stdout_parts):
                    return stop("output_limit", "captured_output")
            else:
                failed = True
                feedback = (
                    f"FAILED test_case_{index}: expected "
                    f"{_bounded_repr(case['expected'])}, got "
                    f"{_bounded_repr(call.value)}"
                )
                if not charge(feedback, stderr_parts):
                    return stop("output_limit", "captured_output")

        close_id = uuid4().hex
        write_error, line = write_frame(
            {
                "version": PROTOCOL_VERSION,
                "request_id": close_id,
                "close": True,
            }
        )
        if write_error is not None:
            if write_error == "abrupt_learner_exit":
                kind, reason = _classify_process_exit(process.returncode, isolation)
                return stop(kind, reason)
            kind = (
                write_error
                if write_error in {"timeout", "output_limit"}
                else "runtime_error"
            )
            reason = {
                "timeout": "wall_time",
                "output_limit": "captured_output",
            }.get(write_error, write_error)
            return stop(kind, reason)
        if line is None or not _valid_close_response(line, close_id):
            return stop("runtime_error", "invalid_worker_protocol")
        while process.poll() is None:
            if time.monotonic() >= deadline:
                return stop("timeout", "wall_time")
            if collect(0.02):
                return stop("output_limit", "captured_output")
        while not events.empty():
            collect(0)
            if raw_bytes + derived_bytes > policy.output_bytes:
                return stop("output_limit", "captured_output")
        if stdout_buffer:
            return stop("runtime_error", "invalid_worker_protocol")
        if process.returncode != 0:
            return stop("runtime_error", "abrupt_learner_exit")
    except KeyboardInterrupt:
        return stop("cancelled", "cancelled")

    if failed:
        return stop("test_failure", None, exit_code=10)
    if not charge(f"{len(test_cases)} passed", stdout_parts):
        return stop("output_limit", "captured_output")
    return stop("success", None, exit_code=0)


def _classify_process_exit(
    exit_code: int | None,
    isolation: Literal["oci", "reduced"],
) -> tuple[str, str]:
    resource_signals = {
        value
        for name in ("SIGKILL", "SIGXCPU", "SIGXFSZ")
        if (value := getattr(signal, name, None)) is not None
    }
    resource_exit_codes = {
        code
        for value in resource_signals
        for code in (128 + value, -value)
    }
    if isolation == "oci":
        resource_exit_codes.add(137)
    if exit_code in resource_exit_codes:
        return "resource_limit", "memory_or_process"
    if isolation == "oci" and exit_code == 125:
        return "runner_error", "container_start"
    return "runtime_error", "abrupt_learner_exit"


def _encode_tagged(value: object, *, _depth: int = 0, _items: list[int] | None = None):
    if _depth > VALUE_MAX_DEPTH:
        raise ValueError("value nesting is too deep")
    items = [0] if _items is None else _items
    items[0] += 1
    if items[0] > VALUE_MAX_ITEMS:
        raise ValueError("value contains too many items")
    if value is None:
        return {"t": "none"}
    if isinstance(value, bool):
        return {"t": "bool", "v": value}
    if isinstance(value, int):
        return {"t": "int", "v": str(value)}
    if isinstance(value, float):
        return {"t": "float", "v": value.hex()}
    if isinstance(value, str):
        return {"t": "str", "v": value}
    if isinstance(value, bytes):
        return {"t": "bytes", "v": value.hex()}
    if isinstance(value, list):
        return {
            "t": "list",
            "v": [
                _encode_tagged(item, _depth=_depth + 1, _items=items)
                for item in value
            ],
        }
    if isinstance(value, tuple):
        return {
            "t": "tuple",
            "v": [
                _encode_tagged(item, _depth=_depth + 1, _items=items)
                for item in value
            ],
        }
    if isinstance(value, dict):
        return {
            "t": "dict",
            "v": [
                [
                    _encode_tagged(key, _depth=_depth + 1, _items=items),
                    _encode_tagged(item, _depth=_depth + 1, _items=items),
                ]
                for key, item in value.items()
            ],
        }
    raise TypeError(type(value).__name__)


def _decode_tagged(
    payload: object,
    *,
    _depth: int = 0,
    _items: list[int] | None = None,
) -> object:
    if _depth > VALUE_MAX_DEPTH:
        raise ValueError("value nesting is too deep")
    items = [0] if _items is None else _items
    items[0] += 1
    if items[0] > VALUE_MAX_ITEMS:
        raise ValueError("value contains too many items")
    if not isinstance(payload, dict) or not isinstance(payload.get("t"), str):
        raise ValueError("invalid tagged value")
    tag = payload["t"]
    if tag == "none" and set(payload) == {"t"}:
        return None
    if tag == "bool" and set(payload) == {"t", "v"} and isinstance(payload["v"], bool):
        return payload["v"]
    if tag == "int" and set(payload) == {"t", "v"} and isinstance(payload["v"], str):
        text = payload["v"]
        if not text or len(text) > 4_300 or (
            text[0] == "-" and (len(text) == 1 or not text[1:].isdigit())
        ) or (text[0] != "-" and not text.isdigit()):
            raise ValueError("invalid integer")
        decoded = int(text)
        if str(decoded) != text:
            raise ValueError("noncanonical integer")
        return decoded
    if tag == "float" and set(payload) == {"t", "v"} and isinstance(payload["v"], str):
        decoded = float.fromhex(payload["v"])
        if decoded.hex() != payload["v"]:
            raise ValueError("noncanonical float")
        return decoded
    if tag == "str" and set(payload) == {"t", "v"} and isinstance(payload["v"], str):
        return payload["v"]
    if tag == "bytes" and set(payload) == {"t", "v"} and isinstance(payload["v"], str):
        decoded = bytes.fromhex(payload["v"])
        if decoded.hex() != payload["v"]:
            raise ValueError("noncanonical bytes")
        return decoded
    if tag in {"list", "tuple"} and set(payload) == {"t", "v"}:
        values = payload["v"]
        if not isinstance(values, list):
            raise ValueError("invalid sequence")
        decoded = [
            _decode_tagged(item, _depth=_depth + 1, _items=items)
            for item in values
        ]
        return decoded if tag == "list" else tuple(decoded)
    if tag == "dict" and set(payload) == {"t", "v"}:
        pairs = payload["v"]
        if not isinstance(pairs, list):
            raise ValueError("invalid mapping")
        result = {}
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("invalid mapping pair")
            key = _decode_tagged(pair[0], _depth=_depth + 1, _items=items)
            item = _decode_tagged(pair[1], _depth=_depth + 1, _items=items)
            try:
                if key in result:
                    raise ValueError("duplicate mapping key")
                result[key] = item
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid mapping key") from exc
        return result
    raise ValueError("unknown tagged value")


def _decode_response(line: bytes, request_id: str) -> _CallResult:
    if len(line) > PROTOCOL_MAX_FRAME_BYTES or not line.startswith(
        PROTOCOL_PREFIX.encode()
    ):
        return _call_result("protocol_error", limit_reason="invalid_worker_protocol")
    try:
        payload = json.loads(line[len(PROTOCOL_PREFIX) :])
        if not isinstance(payload, dict):
            raise ValueError("response must be an object")
        if (
            type(payload.get("version")) is not int
            or payload["version"] != PROTOCOL_VERSION
        ):
            raise ValueError("wrong protocol version")
        if payload.get("request_id") != request_id:
            raise ValueError("wrong request id")
        status = payload.get("status")
        if status == "value":
            if set(payload) != {"version", "request_id", "status", "value"}:
                raise ValueError("invalid value response schema")
            return _call_result("value", value=_decode_tagged(payload["value"]))
        if status in {"compile_error", "runtime_error", "unsupported"}:
            if set(payload) != {"version", "request_id", "status", "detail"}:
                raise ValueError("invalid error response schema")
            detail = payload["detail"]
            if not isinstance(detail, str) or len(detail.encode()) > 4_000:
                raise ValueError("invalid error detail")
            return _call_result(
                status,
                stderr=detail if status != "unsupported" else "",
                exit_code={"compile_error": 20, "runtime_error": 21}.get(status, 10),
                limit_reason=detail if status == "unsupported" else None,
            )
        raise ValueError("invalid response status")
    except (
        json.JSONDecodeError,
        TypeError,
        ValueError,
        RecursionError,
        OverflowError,
    ):
        return _call_result("protocol_error", limit_reason="invalid_worker_protocol")


def _valid_close_response(line: bytes, request_id: str) -> bool:
    if len(line) > PROTOCOL_MAX_FRAME_BYTES or not line.startswith(
        PROTOCOL_PREFIX.encode()
    ):
        return False
    try:
        payload = json.loads(line[len(PROTOCOL_PREFIX) :])
        if not isinstance(payload, dict):
            return False
    except (
        json.JSONDecodeError,
        TypeError,
        ValueError,
        RecursionError,
        OverflowError,
    ):
        return False
    return type(payload.get("version")) is int and payload == {
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "status": "closed",
    }


def _bounded_repr(value: object) -> str:
    try:
        rendered = repr(value)
    except (ValueError, RecursionError):
        return "<unrenderable value>"
    encoded = rendered.encode("utf-8", errors="replace")
    if len(encoded) <= PROTOCOL_MAX_FRAME_BYTES:
        return rendered
    return encoded[:PROTOCOL_MAX_FRAME_BYTES].decode("utf-8", errors="ignore")


def _call_result(
    kind: str,
    value: object | None = None,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    limit_reason: str | None = None,
) -> _CallResult:
    return _CallResult(
        kind=kind,
        value=value,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        limit_reason=limit_reason,
    )


def _result(
    kind,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    started: float,
    limit_reason: str | None,
    isolation,
    runtime: str | None,
) -> RunnerResult:
    protections = (
        (
            "oci-container",
            "network-disabled",
            "read-only-root",
            "non-root",
            "capabilities-dropped",
            "no-new-privileges",
            "attempt-only-host-mount",
            "resource-limits",
            "process-tree-removal",
        )
        if isolation == "oci"
        else (
            "scrubbed-environment",
            "wall-time-limit",
            "output-limit",
            "best-effort-resource-limits",
            "best-effort-process-tree-kill",
        )
    )
    return RunnerResult(
        kind=kind,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        signal=-exit_code if isinstance(exit_code, int) and exit_code < 0 else None,
        duration_seconds=max(0.0, time.monotonic() - started),
        limit_reason=limit_reason,
        isolation=isolation,
        runtime=runtime,
        protections=protections,
    )


def _validate_request(
    solution: Path,
    function_name: str,
    test_cases: list[dict[str, object]],
    policy: ResourcePolicy,
) -> None:
    if not solution.is_file() or solution.suffix != ".py":
        raise ValueError("solution must be an existing Python file")
    if not function_name.isidentifier():
        raise ValueError("function name must be a Python identifier")
    if not test_cases or len(test_cases) > 64:
        raise ValueError("test bundle must contain 1-64 cases")
    if any(
        not isinstance(case, dict) or set(case) != {"input", "expected"}
        for case in test_cases
    ):
        raise ValueError("each test case must contain exactly input and expected")
    try:
        encoded = json.dumps(
            _encode_tagged(test_cases),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError("test bundle contains an unsupported value") from exc
    if len(encoded) > PROTOCOL_MAX_FRAME_BYTES:
        raise ValueError("test bundle is too large")
    if (
        isinstance(policy.wall_seconds, bool)
        or not isinstance(policy.wall_seconds, (int, float))
        or not math.isfinite(policy.wall_seconds)
        or not 0.05 <= policy.wall_seconds <= 300
    ):
        raise ValueError("wall_seconds must be a finite number from 0.05 to 300")
    integer_limits = {
        "cpu_seconds": (policy.cpu_seconds, 1, 300),
        "memory_bytes": (policy.memory_bytes, 16 * 1024 * 1024, 2 * 1024**3),
        "process_limit": (policy.process_limit, 1, 512),
        "output_bytes": (policy.output_bytes, 1_024, 4 * 1024**2),
        "file_bytes": (policy.file_bytes, 1_024, 256 * 1024**2),
    }
    for label, (value, minimum, maximum) in integer_limits.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            raise ValueError(
                f"{label} must be an integer from {minimum} to {maximum}"
            )


def _python_worker(function_name: str) -> str:
    return f"""\
import importlib.util
import json
import os
import sys
import traceback

os.environ.clear()
os.environ.update({{"HOME": "/tmp", "TMPDIR": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"}})
prefix = {PROTOCOL_PREFIX!r}
version = {PROTOCOL_VERSION}
max_frame = {PROTOCOL_MAX_FRAME_BYTES}
max_depth = {VALUE_MAX_DEPTH}
max_items = {VALUE_MAX_ITEMS}
solution_path = sys.argv[1]

def emit(request_id, status, *, value=None, detail=None):
    payload = {{
        "version": version,
        "request_id": request_id,
        "status": status,
    }}
    if status == "value":
        payload["value"] = value
    if detail:
        payload["detail"] = detail.encode("utf-8")[:4000].decode(
            "utf-8", errors="ignore"
        )
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    if len((prefix + encoded + "\\n").encode()) > max_frame:
        if status != "value":
            raise ValueError("response frame is too large")
        payload = {{
            "version": version,
            "request_id": request_id,
            "status": "unsupported",
            "detail": "encoded value is too large",
        }}
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    sys.stdout.write(prefix + encoded + "\\n")
    sys.stdout.flush()

def decode_tagged(payload, depth=0, items=None):
    if depth > max_depth:
        raise ValueError("value nesting is too deep")
    items = [0] if items is None else items
    items[0] += 1
    if items[0] > max_items:
        raise ValueError("value contains too many items")
    if not isinstance(payload, dict) or not isinstance(payload.get("t"), str):
        raise ValueError("invalid tagged value")
    tag = payload["t"]
    if tag == "none" and set(payload) == {{"t"}}:
        return None
    if tag == "bool" and set(payload) == {{"t", "v"}} and isinstance(payload["v"], bool):
        return payload["v"]
    if tag == "int" and set(payload) == {{"t", "v"}} and isinstance(payload["v"], str):
        text = payload["v"]
        if not text or len(text) > 4300 or (
            text[0] == "-" and (len(text) == 1 or not text[1:].isdigit())
        ) or (text[0] != "-" and not text.isdigit()):
            raise ValueError("invalid integer")
        decoded = int(text)
        if str(decoded) != text:
            raise ValueError("noncanonical integer")
        return decoded
    if tag == "float" and set(payload) == {{"t", "v"}} and isinstance(payload["v"], str):
        decoded = float.fromhex(payload["v"])
        if decoded.hex() != payload["v"]:
            raise ValueError("noncanonical float")
        return decoded
    if tag == "str" and set(payload) == {{"t", "v"}} and isinstance(payload["v"], str):
        return payload["v"]
    if tag == "bytes" and set(payload) == {{"t", "v"}} and isinstance(payload["v"], str):
        decoded = bytes.fromhex(payload["v"])
        if decoded.hex() != payload["v"]:
            raise ValueError("noncanonical bytes")
        return decoded
    if tag in {{"list", "tuple"}} and set(payload) == {{"t", "v"}}:
        values = payload["v"]
        if not isinstance(values, list):
            raise ValueError("invalid sequence")
        decoded = [decode_tagged(item, depth + 1, items) for item in values]
        return decoded if tag == "list" else tuple(decoded)
    if tag == "dict" and set(payload) == {{"t", "v"}}:
        pairs = payload["v"]
        if not isinstance(pairs, list):
            raise ValueError("invalid mapping")
        result = {{}}
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("invalid mapping pair")
            key = decode_tagged(pair[0], depth + 1, items)
            item = decode_tagged(pair[1], depth + 1, items)
            if key in result:
                raise ValueError("duplicate mapping key")
            result[key] = item
        return result
    raise ValueError("unknown tagged value")

def encode_tagged(value, depth=0, items=None):
    if depth > max_depth:
        raise ValueError("value nesting is too deep")
    items = [0] if items is None else items
    items[0] += 1
    if items[0] > max_items:
        raise ValueError("value contains too many items")
    if value is None:
        return {{"t": "none"}}
    if isinstance(value, bool):
        return {{"t": "bool", "v": value}}
    if isinstance(value, int):
        return {{"t": "int", "v": str(value)}}
    if isinstance(value, float):
        return {{"t": "float", "v": value.hex()}}
    if isinstance(value, str):
        return {{"t": "str", "v": value}}
    if isinstance(value, bytes):
        return {{"t": "bytes", "v": value.hex()}}
    if isinstance(value, list):
        return {{"t": "list", "v": [encode_tagged(item, depth + 1, items) for item in value]}}
    if isinstance(value, tuple):
        return {{"t": "tuple", "v": [encode_tagged(item, depth + 1, items) for item in value]}}
    if isinstance(value, dict):
        return {{
            "t": "dict",
            "v": [
                [encode_tagged(key, depth + 1, items), encode_tagged(item, depth + 1, items)]
                for key, item in value.items()
            ],
        }}
    raise TypeError(type(value).__name__)

try:
    spec = importlib.util.spec_from_file_location("learner_solution", solution_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
except SyntaxError:
    load_status = "compile_error"
    load_detail = traceback.format_exc()
    function = None
except BaseException:
    load_status = "runtime_error"
    load_detail = traceback.format_exc()
    function = None
else:
    try:
        function = getattr(module, {function_name!r})
        if not callable(function):
            raise TypeError("target is not callable")
    except (AttributeError, TypeError):
        load_status = "runtime_error"
        load_detail = traceback.format_exc()
        function = None
    else:
        load_status = None
        load_detail = None

while True:
    try:
        raw = sys.stdin.buffer.readline(max_frame + 1)
        if not raw or len(raw) > max_frame or not raw.endswith(b"\\n"):
            raise ValueError("invalid request frame")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        request_id = request.get("request_id")
        if (
            not isinstance(request_id, str)
            or type(request.get("version")) is not int
            or request["version"] != version
        ):
            raise ValueError("invalid request envelope")
        if set(request) == {{"version", "request_id", "close"}}:
            if request["close"] is not True:
                raise ValueError("invalid close request")
            emit(request_id, "closed")
            raise SystemExit(0)
        if set(request) != {{"version", "request_id", "input"}}:
            raise ValueError("invalid call request")
        value = decode_tagged(request["input"])
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError, OverflowError):
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(22)

    if load_status is not None:
        emit(request_id, load_status, detail=load_detail[:4000])
        continue
    try:
        if isinstance(value, list):
            actual = function(*value)
        elif isinstance(value, dict):
            actual = function(**value)
        else:
            actual = function(value)
    except BaseException:
        emit(request_id, "runtime_error", detail=traceback.format_exc()[:4000])
        continue
    try:
        encoded = encode_tagged(actual)
        emit(request_id, "value", value=encoded)
    except (TypeError, ValueError, RecursionError, OverflowError):
        emit(request_id, "unsupported", detail=type(actual).__name__)
"""


def _posix_limits(policy: ResourcePolicy):
    def apply() -> None:
        import resource

        limits = [
            (resource.RLIMIT_CPU, policy.cpu_seconds),
            (resource.RLIMIT_FSIZE, policy.file_bytes),
        ]
        if hasattr(resource, "RLIMIT_AS"):
            limits.append((resource.RLIMIT_AS, policy.memory_bytes))
        if hasattr(resource, "RLIMIT_NPROC"):
            limits.append((resource.RLIMIT_NPROC, policy.process_limit))
        for kind, requested in limits:
            try:
                _soft, hard = resource.getrlimit(kind)
                effective = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
                resource.setrlimit(kind, (effective, effective))
            except (OSError, ValueError):
                continue

    return apply
