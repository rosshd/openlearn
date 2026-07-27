from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
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
            return _supervise_test_cases(
                copied_solution,
                worker_dir,
                test_cases,
                policy,
                started=started,
                reduced_isolation=reduced_isolation,
                diagnostic=diagnostic,
                image=image,
            )


def _supervise_test_cases(
    solution: Path,
    worker_dir: Path,
    test_cases: list[dict[str, object]],
    policy: ResourcePolicy,
    *,
    started: float,
    reduced_isolation: bool,
    diagnostic: RuntimeDiagnostic | None,
    image: str,
) -> RunnerResult:
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    last_exit_code: int | None = 0
    failed = False
    remaining_output = policy.output_bytes
    deadline = started + policy.wall_seconds
    isolation = "reduced" if reduced_isolation else "oci"
    runtime = None if diagnostic is None else diagnostic.runtime
    for index, case in enumerate(test_cases, 1):
        remaining_wall = deadline - time.monotonic()
        if remaining_wall <= 0:
            return _result(
                "timeout",
                "\n".join(stdout_parts),
                "\n".join(stderr_parts),
                last_exit_code,
                started,
                "wall_time",
                isolation,
                runtime,
            )
        request_id = uuid4().hex
        request = (
            json.dumps(
                {
                    "version": 1,
                    "request_id": request_id,
                    "input": case["input"],
                },
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        if remaining_output <= 0:
            return _result(
                "output_limit",
                "\n".join(stdout_parts),
                "\n".join(stderr_parts),
                last_exit_code,
                started,
                "captured_output",
                isolation,
                runtime,
            )
        call_policy = replace(
            policy,
            wall_seconds=remaining_wall,
            output_bytes=remaining_output,
        )
        if reduced_isolation:
            call = _run_reduced_call(
                solution,
                worker_dir / "call_worker.py",
                call_policy,
                request,
                request_id,
            )
        else:
            assert diagnostic is not None
            assert diagnostic.executable is not None
            assert diagnostic.runtime is not None
            call = _run_oci(
                diagnostic.executable,
                diagnostic.runtime,
                image,
                solution.parent,
                worker_dir,
                call_policy,
                request,
                request_id,
            )
        last_exit_code = call.exit_code
        remaining_output -= len((call.stdout + call.stderr).encode())
        if call.stderr.strip():
            stderr_parts.append(call.stderr.strip())
        if call.kind != "value":
            kind = call.kind
            if kind == "protocol_error":
                kind = "runtime_error"
            return _result(
                kind,
                "\n".join(stdout_parts),
                "\n".join(stderr_parts),
                call.exit_code,
                started,
                call.limit_reason,
                isolation,
                runtime,
            )
        if call.value == case["expected"]:
            stdout_parts.append(f"PASSED test_case_{index}")
        else:
            failed = True
            stderr_parts.append(
                f"FAILED test_case_{index}: expected {case['expected']!r}, "
                f"got {call.value!r}"
            )
    if failed:
        return _result(
            "test_failure",
            "\n".join(stdout_parts),
            "\n".join(stderr_parts),
            10,
            started,
            None,
            isolation,
            runtime,
        )
    stdout_parts.append(f"{len(test_cases)} passed")
    return _result(
        "success",
        "\n".join(stdout_parts),
        "",
        0,
        started,
        None,
        isolation,
        runtime,
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
    policy: ResourcePolicy,
    request: bytes,
    request_id: str,
) -> _CallResult:
    name = f"openlearn-{uuid4().hex}"
    command = build_oci_create_command(
        executable, runtime, image, attempt, worker, name, policy
    )
    removed = {"value": False}
    cleanup_detail = {"value": ""}

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
        return False

    call_started = time.monotonic()
    try:
        created = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=min(OCI_CREATE_TIMEOUT_SECONDS, policy.wall_seconds),
        )
    except KeyboardInterrupt:
        cleanup_ok = remove_container()
        return _call_result(
            "cancelled" if cleanup_ok else "runner_error",
            stderr=cleanup_detail["value"],
            exit_code=None,
            limit_reason="cancelled" if cleanup_ok else "container_cleanup",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        cleanup_ok = remove_container()
        detail = str(exc)
        if not cleanup_ok:
            detail = f"{detail}\nCould not confirm container removal: {cleanup_detail['value']}"
        return _call_result(
            "runner_error",
            stderr=detail,
            exit_code=None,
            limit_reason="container_create" if cleanup_ok else "container_cleanup",
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
        return _call_result(
            "runner_error",
            stdout=created.stdout,
            stderr=stderr,
            exit_code=created.returncode,
            limit_reason="container_create" if cleanup_ok else "container_cleanup",
        )

    remaining_wall = policy.wall_seconds - (time.monotonic() - call_started)
    if remaining_wall <= 0:
        cleanup_ok = remove_container()
        return _call_result(
            "timeout" if cleanup_ok else "runner_error",
            stderr=cleanup_detail["value"],
            exit_code=None,
            limit_reason="wall_time" if cleanup_ok else "container_cleanup",
        )
    execution_policy = replace(policy, wall_seconds=remaining_wall)
    try:
        try:
            process = subprocess.Popen(
                [executable, "start", "--attach", "--interactive", name],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            outcome, stdout, stderr = _capture_bounded(
                process,
                execution_policy,
                terminate=remove_container,
                stdin_data=request,
            )
            exit_code = process.returncode
        except KeyboardInterrupt:
            cleanup_ok = remove_container()
            return _call_result(
                "cancelled" if cleanup_ok else "runner_error",
                stderr=cleanup_detail["value"],
                exit_code=None,
                limit_reason="cancelled" if cleanup_ok else "container_cleanup",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            cleanup_ok = remove_container()
            detail = str(exc)
            if not cleanup_ok:
                detail = (
                    f"{detail}\nCould not confirm container removal: "
                    f"{cleanup_detail['value']}"
                )
            return _call_result(
                "runner_error",
                stderr=detail,
                exit_code=None,
                limit_reason="container_start" if cleanup_ok else "container_cleanup",
            )
    finally:
        cleanup_ok = remove_container()
    if not cleanup_ok:
        stderr = "\n".join(
            part
            for part in (
                stderr,
                f"Could not confirm container removal: {cleanup_detail['value']}",
            )
            if part
        )
        return _call_result(
            "runner_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="container_cleanup",
        )
    return _interpret_call(
        outcome,
        stdout,
        stderr,
        exit_code,
        request_id=request_id,
        oci=True,
    )


def _run_reduced_call(
    solution: Path,
    worker: Path,
    policy: ResourcePolicy,
    request: bytes,
    request_id: str,
) -> _CallResult:
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
            os.killpg(process.pid, signal.SIGKILL)
        elif os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            process.kill()

    outcome, stdout, stderr = _capture_bounded(
        process,
        policy,
        terminate=terminate,
        stdin_data=request,
    )
    return _interpret_call(
        outcome,
        stdout,
        stderr,
        process.returncode,
        request_id=request_id,
        oci=False,
    )


def _capture_bounded(
    process: subprocess.Popen[bytes],
    policy: ResourcePolicy,
    *,
    terminate,
    stdin_data: bytes | None = None,
) -> tuple[str | None, str, str]:
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    retained = {"value": 0}
    observed = {"value": 0}
    lock = threading.Lock()
    overflow = threading.Event()

    def drain(label: str, stream) -> None:
        while True:
            chunk = stream.read(16_384)
            if not chunk:
                return
            with lock:
                observed["value"] += len(chunk)
                remaining = max(0, policy.output_bytes - retained["value"])
                if remaining:
                    kept = chunk[:remaining]
                    chunks[label].append(kept)
                    retained["value"] += len(kept)
                if observed["value"] > policy.output_bytes:
                    overflow.set()

    threads = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    if stdin_data is not None and process.stdin is not None:
        try:
            process.stdin.write(stdin_data)
            process.stdin.close()
        except BrokenPipeError:
            pass
    deadline = time.monotonic() + policy.wall_seconds
    outcome = None
    try:
        while process.poll() is None:
            if overflow.is_set():
                outcome = "output_limit"
                terminate()
                break
            if time.monotonic() >= deadline:
                outcome = "timeout"
                terminate()
                break
            time.sleep(0.02)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            outcome = "termination_failure"
            process.kill()
            process.wait(timeout=5)
    except KeyboardInterrupt:
        outcome = "cancelled"
        terminate()
        process.wait(timeout=5)
    finally:
        for thread in threads:
            thread.join(timeout=2)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    if overflow.is_set() and outcome is None:
        outcome = "output_limit"
    suffix = b"\n[openLearn output truncated at configured limit]\n" if overflow.is_set() else b""
    stdout = b"".join(chunks["stdout"])
    stderr = b"".join(chunks["stderr"]) + suffix
    return (
        outcome,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _interpret_call(
    outcome: str | None,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    *,
    request_id: str,
    oci: bool,
) -> _CallResult:
    if outcome == "timeout":
        return _call_result(
            "timeout",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="wall_time",
        )
    if outcome == "output_limit":
        return _call_result(
            "output_limit",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="captured_output",
        )
    if outcome == "cancelled":
        return _call_result(
            "cancelled",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="cancelled",
        )
    if outcome == "termination_failure":
        return _call_result(
            "runner_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="termination_failure",
        )
    resource_exit_codes = {
        128 + signal.SIGKILL,
        128 + getattr(signal, "SIGXCPU", signal.SIGKILL),
        128 + getattr(signal, "SIGXFSZ", signal.SIGKILL),
        -signal.SIGKILL,
    }
    if exit_code in resource_exit_codes:
        return _call_result(
            "resource_limit",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="memory_or_process",
        )
    if oci and exit_code == 125:
        return _call_result(
            "runner_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="container_start",
        )
    if exit_code != 0:
        return _call_result(
            "runtime_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="abrupt_learner_exit",
        )
    if not stdout:
        return _call_result(
            "runtime_error",
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="abrupt_learner_exit",
        )
    lines = stdout.splitlines()
    if len(lines) != 1 or not lines[0].startswith(PROTOCOL_PREFIX):
        return _call_result(
            "protocol_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="invalid_worker_protocol",
        )
    try:
        payload = json.loads(lines[0][len(PROTOCOL_PREFIX) :])
    except (json.JSONDecodeError, TypeError):
        return _call_result(
            "protocol_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="invalid_worker_protocol",
        )
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("request_id") != request_id
        or payload.get("status") not in {"value", "compile_error", "runtime_error"}
    ):
        return _call_result(
            "protocol_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="invalid_worker_protocol",
        )
    status = payload["status"]
    if status == "value" and "value" not in payload:
        return _call_result(
            "protocol_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            limit_reason="invalid_worker_protocol",
        )
    detail = payload.get("detail")
    if isinstance(detail, str) and detail:
        stderr = "\n".join(part for part in (stderr, detail[:4_000]) if part)
    trusted_exit_code = {
        "compile_error": 20,
        "runtime_error": 21,
    }.get(status, exit_code)
    return _call_result(
        status,
        value=payload.get("value"),
        stderr=stderr,
        exit_code=trusted_exit_code,
    )


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
    encoded = json.dumps(test_cases, ensure_ascii=True, allow_nan=False)
    if len(encoded) > 64_000:
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
solution_path = sys.argv[1]

def emit(status, *, value=None, detail=None):
    payload = {{
        "version": 1,
        "request_id": request_id,
        "status": status,
    }}
    if status == "value":
        payload["value"] = value
    if detail:
        payload["detail"] = detail
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    sys.stdout.write(prefix + encoded + "\\n")
    sys.stdout.flush()

try:
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536:
        raise ValueError("request frame is too large")
    request = json.loads(raw)
    if (
        not isinstance(request, dict)
        or request.get("version") != 1
        or not isinstance(request.get("request_id"), str)
        or "input" not in request
    ):
        raise ValueError("invalid request frame")
    request_id = request["request_id"]
except BaseException:
    traceback.print_exc(file=sys.stderr)
    raise SystemExit(22)

try:
    spec = importlib.util.spec_from_file_location("learner_solution", solution_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
except SyntaxError:
    emit("compile_error", detail=traceback.format_exc())
    raise SystemExit(0)
except BaseException:
    emit("runtime_error", detail=traceback.format_exc())
    raise SystemExit(0)

try:
    function = getattr(module, {function_name!r})
except (AttributeError, TypeError):
    emit("runtime_error", detail=traceback.format_exc())
    raise SystemExit(0)

try:
    value = request["input"]
    if isinstance(value, list):
        actual = function(*value)
    elif isinstance(value, dict):
        actual = function(**value)
    else:
        actual = function(value)
    emit("value", value=actual)
except BaseException:
    emit("runtime_error", detail=traceback.format_exc())
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
