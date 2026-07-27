from __future__ import annotations

import json
import os
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
            return RuntimeDiagnostic(runtime, executable, False, False, image, str(exc))
        if info.returncode != 0:
            detail = (info.stderr or info.stdout or "runtime service is unavailable").strip()
            return RuntimeDiagnostic(runtime, executable, False, False, image, detail[:500])
        try:
            inspect = run(
                [executable, "image", "inspect", image],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return RuntimeDiagnostic(runtime, executable, True, False, image, str(exc))
        if inspect.returncode != 0:
            return RuntimeDiagnostic(
                runtime,
                executable,
                True,
                False,
                image,
                "pinned runner image is not present locally",
            )
        return RuntimeDiagnostic(
            runtime,
            executable,
            True,
            True,
            image,
            "OCI runtime and pinned runner image are ready",
        )
    return RuntimeDiagnostic(
        None,
        None,
        False,
        False,
        image,
        "Docker or Podman was not found",
    )


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
    with tempfile.TemporaryDirectory(prefix="openlearn-attempt-") as attempt_raw:
        with tempfile.TemporaryDirectory(prefix="openlearn-tests-") as harness_raw:
            attempt = Path(attempt_raw)
            harness_dir = Path(harness_raw)
            attempt.chmod(0o777)
            harness_dir.chmod(0o755)
            copied_solution = attempt / "solution.py"
            shutil.copyfile(solution, copied_solution)
            copied_solution.chmod(0o666)
            harness = harness_dir / "run_tests.py"
            harness.write_text(
                _python_harness(function_name, test_cases),
                encoding="utf-8",
            )
            harness.chmod(0o644)
            if reduced_isolation:
                return _run_reduced(copied_solution, harness, policy)
            diagnostic = diagnose_runtime(preferred=preferred_runtime, image=image)
            if not diagnostic.ready:
                raise RunnerUnavailableError(runtime_setup_guidance(diagnostic))
            assert diagnostic.executable is not None
            assert diagnostic.runtime is not None
            return _run_oci(
                diagnostic.executable,
                diagnostic.runtime,
                image,
                attempt,
                harness_dir,
                policy,
            )


def build_oci_create_command(
    executable: str,
    runtime: str,
    image: str,
    attempt: Path,
    harness: Path,
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
        f"type=bind,src={harness},dst=/opt/openlearn-tests,readonly",
        image,
        "/usr/bin/env",
        "-i",
        "HOME=/tmp",
        "TMPDIR=/tmp",
        "PYTHONDONTWRITEBYTECODE=1",
        "/usr/local/bin/python",
        "-I",
        "/opt/openlearn-tests/run_tests.py",
        "/workspace/solution.py",
    ]


def _run_oci(
    executable: str,
    runtime: str,
    image: str,
    attempt: Path,
    harness: Path,
    policy: ResourcePolicy,
) -> RunnerResult:
    name = f"openlearn-{uuid4().hex}"
    command = build_oci_create_command(
        executable, runtime, image, attempt, harness, name, policy
    )
    started = time.monotonic()
    created = subprocess.run(command, capture_output=True, text=True, check=False)
    if created.returncode != 0:
        return _result(
            "runner_error",
            created.stdout,
            created.stderr,
            created.returncode,
            started,
            "container_create",
            "oci",
            runtime,
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
        if result.returncode == 0 or "no such container" in detail.casefold():
            removed["value"] = True
            return True
        cleanup_detail["value"] = detail[:500] or f"exit {result.returncode}"
        return False

    try:
        process = subprocess.Popen(
            [executable, "start", "--attach", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        outcome, stdout, stderr = _capture_bounded(
            process,
            policy,
            terminate=remove_container,
        )
        exit_code = process.returncode
        if outcome == "timeout":
            kind = "timeout"
            reason = "wall_time"
        elif outcome == "output_limit":
            kind = "output_limit"
            reason = "captured_output"
        elif outcome == "cancelled":
            kind = "cancelled"
            reason = "cancelled"
        elif outcome == "termination_failure":
            kind = "runner_error"
            reason = "termination_failure"
        else:
            kind, reason = _classify_exit(exit_code, stdout, stderr)
    finally:
        cleanup_ok = remove_container()
    if not cleanup_ok:
        kind = "runner_error"
        reason = "container_cleanup"
        stderr = "\n".join(
            part
            for part in (
                stderr,
                f"Could not confirm container removal: {cleanup_detail['value']}",
            )
            if part
        )
    return _result(
        kind,
        stdout,
        stderr,
        exit_code,
        started,
        reason,
        "oci",
        runtime,
    )


def _run_reduced(
    solution: Path,
    harness: Path,
    policy: ResourcePolicy,
) -> RunnerResult:
    started = time.monotonic()
    popen_kwargs: dict[str, object] = {
        "cwd": str(solution.parent),
        "env": {
            "HOME": str(solution.parent),
            "TMPDIR": str(solution.parent),
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
    process = subprocess.Popen(
        [sys.executable, "-I", str(harness), str(solution)],
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

    outcome, stdout, stderr = _capture_bounded(process, policy, terminate=terminate)
    if outcome == "timeout":
        kind, reason = "timeout", "wall_time"
    elif outcome == "output_limit":
        kind, reason = "output_limit", "captured_output"
    elif outcome == "cancelled":
        kind, reason = "cancelled", "cancelled"
    elif outcome == "termination_failure":
        kind, reason = "runner_error", "termination_failure"
    else:
        kind, reason = _classify_exit(process.returncode, stdout, stderr)
    return _result(
        kind,
        stdout,
        stderr,
        process.returncode,
        started,
        reason,
        "reduced",
        None,
    )


def _capture_bounded(
    process: subprocess.Popen[bytes],
    policy: ResourcePolicy,
    *,
    terminate,
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


def _classify_exit(
    exit_code: int | None, stdout: str, stderr: str
) -> tuple[str, str | None]:
    combined = f"{stdout}\n{stderr}"
    if exit_code == 0:
        return "success", None
    if "OPENLEARN_COMPILE_ERROR" in combined:
        return "compile_error", None
    if "OPENLEARN_TEST_FAILURE" in combined:
        return "test_failure", None
    if "OPENLEARN_RUNTIME_ERROR" in combined:
        return "runtime_error", None
    resource_exit_codes = {
        128 + signal.SIGKILL,
        128 + getattr(signal, "SIGXCPU", signal.SIGKILL),
        128 + getattr(signal, "SIGXFSZ", signal.SIGKILL),
        -signal.SIGKILL,
    }
    if exit_code in resource_exit_codes:
        return "resource_limit", "memory_or_process"
    return "runner_error", "unexpected_exit"


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
    values = (
        policy.wall_seconds,
        policy.cpu_seconds,
        policy.memory_bytes,
        policy.process_limit,
        policy.output_bytes,
        policy.file_bytes,
    )
    if any(value <= 0 for value in values):
        raise ValueError("resource limits must be positive")


def _python_harness(function_name: str, test_cases: list[dict[str, object]]) -> str:
    cases = json.dumps(test_cases, ensure_ascii=True, allow_nan=False)
    return f"""\
import importlib.util
import json
import os
import sys
import traceback

os.environ.clear()
os.environ.update({{"HOME": "/tmp", "TMPDIR": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"}})
solution_path = sys.argv[1]
try:
    spec = importlib.util.spec_from_file_location("learner_solution", solution_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
except SyntaxError:
    print("OPENLEARN_COMPILE_ERROR", file=sys.stderr)
    traceback.print_exc()
    raise SystemExit(20)
except BaseException:
    print("OPENLEARN_RUNTIME_ERROR", file=sys.stderr)
    traceback.print_exc()
    raise SystemExit(21)

try:
    function = getattr(module, {function_name!r})
except (AttributeError, TypeError):
    print("OPENLEARN_RUNTIME_ERROR", file=sys.stderr)
    traceback.print_exc()
    raise SystemExit(21)

cases = json.loads({cases!r})
failed = False
for index, case in enumerate(cases, 1):
    try:
        value = case["input"]
        if isinstance(value, list):
            actual = function(*value)
        elif isinstance(value, dict):
            actual = function(**value)
        else:
            actual = function(value)
        assert actual == case["expected"], (
            f"test_case_{{index}}: expected {{case['expected']!r}}, got {{actual!r}}"
        )
        print(f"PASSED test_case_{{index}}")
    except AssertionError:
        failed = True
        print(f"FAILED test_case_{{index}}", file=sys.stderr)
        traceback.print_exc()
    except BaseException:
        print("OPENLEARN_RUNTIME_ERROR", file=sys.stderr)
        print(f"ERROR test_case_{{index}}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(21)
if failed:
    print("OPENLEARN_TEST_FAILURE", file=sys.stderr)
    raise SystemExit(10)
print(f"{{len(cases)}} passed")
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
