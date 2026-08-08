"""Recoverable per-course Python drafts for interactive learning surfaces."""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from openlearn import cli, code_runner

MAX_SOURCE_BYTES = 64 * 1024
MAX_RESULT_BYTES = 32 * 1024
MAX_RESULT_RECORD_BYTES = 12 * MAX_RESULT_BYTES + 8192
WORKSPACE_FILENAME = "workspace.py"
RESULT_FILENAME = "last-run.json"
DEFAULT_SOURCE = """\
# Try a small idea here. Use Save to keep this draft locally for this course.
print("Hello from openlearn")
"""
_SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class CodeWorkspaceError(RuntimeError):
    """Base error for invalid or unavailable course workspaces."""


class CodeWorkspaceConflictError(CodeWorkspaceError):
    """Raised when a stale editor attempts to replace a newer draft."""


class CodeWorkspaceNotFoundError(CodeWorkspaceError):
    """Raised when a requested course does not exist."""


class CodeWorkspaceValidationError(CodeWorkspaceError):
    """Raised when learner-supplied draft data is invalid."""


@dataclass(frozen=True)
class DraftSnapshot:
    course_slug: str
    language: Literal["python"]
    source: str
    revision: str


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    stdout: str
    stderr: str
    timed_out: bool
    exit_code: int | None
    signal: int | None
    duration_seconds: float
    limit_reason: str | None
    isolation: Literal["oci", "reduced"] | None
    runtime: str | None
    protections: tuple[str, ...]
    draft_revision: str


@dataclass(frozen=True)
class WorkspaceSnapshot:
    draft: DraftSnapshot
    result: ExecutionResult | None


Runner = Callable[..., code_runner.RunnerResult]


class CodeWorkspace:
    """Persist and explicitly execute one bounded Python draft per course."""

    def __init__(
        self,
        topics_root: Path,
        *,
        runner: Runner = code_runner.run_python_script,
        policy: code_runner.ResourcePolicy | None = None,
    ) -> None:
        self.topics_root = topics_root.expanduser().absolute()
        self.runner = runner
        self.policy = policy or code_runner.ResourcePolicy(
            wall_seconds=3.0,
            cpu_seconds=2,
            memory_bytes=128 * 1024 * 1024,
            process_limit=16,
            output_bytes=MAX_RESULT_BYTES,
            file_bytes=1024 * 1024,
        )

    def load(self, course_slug: str) -> DraftSnapshot:
        """Return the saved draft, creating the starter draft on first use."""
        return self.state(course_slug).draft

    def state(self, course_slug: str) -> WorkspaceSnapshot:
        """Atomically return the current draft and its matching last result."""
        slug, topic, workspace = self._paths(course_slug)
        result_path = self._result_path(slug)
        with cli.file_lock(topic):
            self._validate_course(slug, topic)
            self._ensure_workspace_parent(slug)
            with cli.file_lock(workspace):
                if not workspace.exists():
                    self._write(workspace, DEFAULT_SOURCE)
                source = self._read(workspace)
            with cli.file_lock(result_path):
                result = self._read_result(result_path)
        draft = self._snapshot(slug, source)
        if result is not None and result.draft_revision != draft.revision:
            result = None
        return WorkspaceSnapshot(draft, result)

    def save(
        self,
        course_slug: str,
        source: str,
        *,
        expected_revision: str | None = None,
    ) -> DraftSnapshot:
        """Atomically save a bounded draft, optionally rejecting stale editors."""
        normalized = self._validate_source(source)
        slug, topic, workspace = self._paths(course_slug)
        with cli.file_lock(topic):
            self._validate_course(slug, topic)
            self._ensure_workspace_parent(slug)
            with cli.file_lock(workspace):
                current = self._read(workspace) if workspace.exists() else DEFAULT_SOURCE
                self._check_revision(current, expected_revision)
                self._write(workspace, normalized)
                if self._revision(current) != self._revision(normalized):
                    self._clear_result(slug)
        return self._snapshot(slug, normalized)

    def reset(
        self,
        course_slug: str,
        *,
        expected_revision: str | None = None,
    ) -> DraftSnapshot:
        """Atomically restore the visible starter draft."""
        snapshot = self.save(
            course_slug,
            DEFAULT_SOURCE,
            expected_revision=expected_revision,
        )
        self._clear_result(snapshot.course_slug)
        return snapshot

    def run(
        self,
        course_slug: str,
        *,
        expected_revision: str | None = None,
        reduced_isolation: bool = False,
        preferred_runtime: str | None = None,
    ) -> ExecutionResult:
        """Explicitly run an immutable snapshot of the current saved draft."""
        draft = self.load(course_slug)
        if expected_revision is not None and draft.revision != expected_revision:
            raise CodeWorkspaceConflictError(
                "the draft changed in another session; reload before running it"
            )
        with tempfile.TemporaryDirectory(prefix="openlearn-workspace-run-") as raw:
            source_path = Path(raw) / WORKSPACE_FILENAME
            source_path.write_text(draft.source, encoding="utf-8")
            try:
                result = self.runner(
                    source_path,
                    policy=self.policy,
                    reduced_isolation=reduced_isolation,
                    preferred_runtime=preferred_runtime,
                )
            except code_runner.RunnerUnavailableError as exc:
                execution = ExecutionResult(
                    status="runner_unavailable",
                    stdout="",
                    stderr=self._bounded(str(exc)),
                    timed_out=False,
                    exit_code=None,
                    signal=None,
                    duration_seconds=0.0,
                    limit_reason="runner_unavailable",
                    isolation=None,
                    runtime=None,
                    protections=(),
                    draft_revision=draft.revision,
                )
            else:
                execution = ExecutionResult(
                    status=result.kind,
                    stdout=self._bounded(result.stdout),
                    stderr=self._bounded(result.stderr),
                    timed_out=result.kind == "timeout",
                    exit_code=result.exit_code,
                    signal=result.signal,
                    duration_seconds=result.duration_seconds,
                    limit_reason=result.limit_reason,
                    isolation=result.isolation,
                    runtime=result.runtime,
                    protections=result.protections,
                    draft_revision=draft.revision,
                )
        self._persist_result(course_slug, execution)
        return execution

    def load_last_result(self, course_slug: str) -> ExecutionResult | None:
        """Return the last result only when it belongs to the current draft."""
        return self.state(course_slug).result

    def path_for(self, course_slug: str) -> Path:
        """Return the validated learner-owned path, creating only its directories."""
        slug, topic, workspace = self._paths(course_slug)
        with cli.file_lock(topic):
            self._validate_course(slug, topic)
            self._ensure_workspace_parent(slug)
        return workspace

    def _paths(self, course_slug: str) -> tuple[str, Path, Path]:
        if (
            not isinstance(course_slug, str)
            or len(course_slug) > 100
            or _SLUG_PATTERN.fullmatch(course_slug) is None
        ):
            raise CodeWorkspaceValidationError("course slug is invalid")
        topic = self.topics_root / f"{course_slug}.md"
        workspace = self.topics_root / "drills" / course_slug / WORKSPACE_FILENAME
        return course_slug, topic, workspace

    def _validate_course(self, slug: str, topic: Path) -> None:
        if self.topics_root.is_symlink() or not self.topics_root.is_dir():
            raise CodeWorkspaceError("learning topics directory is missing or unsafe")
        if (
            not topic.exists()
            or topic.is_symlink()
            or not topic.is_file()
            or (self.topics_root / f".{slug}.deleted.json").exists()
        ):
            raise CodeWorkspaceNotFoundError(f"course not found: {slug}")

    def _ensure_workspace_parent(self, slug: str) -> None:
        drills = self.topics_root / "drills"
        owned = drills / slug
        if drills.exists() and (drills.is_symlink() or not drills.is_dir()):
            raise CodeWorkspaceError("course workspace directory is unsafe")
        drills.mkdir(mode=0o700, exist_ok=True)
        if owned.exists() and (owned.is_symlink() or not owned.is_dir()):
            raise CodeWorkspaceError("course workspace directory is unsafe")
        owned.mkdir(mode=0o700, exist_ok=True)
        if owned.resolve().parent != drills.resolve():
            raise CodeWorkspaceError("course workspace escaped its owned directory")

    def _read(self, workspace: Path) -> str:
        if workspace.is_symlink() or not workspace.is_file():
            raise CodeWorkspaceError("course draft is missing or unsafe")
        size = workspace.stat().st_size
        if size > MAX_SOURCE_BYTES:
            raise CodeWorkspaceValidationError("course draft is too large")
        try:
            source = workspace.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CodeWorkspaceValidationError("course draft must be UTF-8 text") from exc
        return self._validate_source(source)

    def _write(self, workspace: Path, source: str) -> None:
        if workspace.exists() and (workspace.is_symlink() or not workspace.is_file()):
            raise CodeWorkspaceError("course draft is unsafe")
        cli.write_text_atomic(workspace, source)
        try:
            workspace.chmod(0o600)
        except OSError:
            pass

    def _check_revision(self, source: str, expected_revision: str | None) -> None:
        if expected_revision is None:
            return
        if not isinstance(expected_revision, str) or not expected_revision:
            raise CodeWorkspaceValidationError("draft revision is invalid")
        if self._revision(source) != expected_revision:
            raise CodeWorkspaceConflictError(
                "the draft changed in another session; reload before saving"
            )

    @staticmethod
    def _validate_source(source: str) -> str:
        if not isinstance(source, str):
            raise CodeWorkspaceValidationError("course draft must be text")
        if "\x00" in source:
            raise CodeWorkspaceValidationError("course draft cannot contain null bytes")
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise CodeWorkspaceValidationError("course draft is too large")
        return source

    def _result_path(self, slug: str) -> Path:
        return self.topics_root / "drills" / slug / RESULT_FILENAME

    def _clear_result(self, slug: str) -> None:
        result_path = self._result_path(slug)
        with cli.file_lock(result_path):
            result_path.unlink(missing_ok=True)

    def _persist_result(self, slug: str, result: ExecutionResult) -> None:
        topic = self.topics_root / f"{slug}.md"
        workspace = self.topics_root / "drills" / slug / WORKSPACE_FILENAME
        result_path = self._result_path(slug)
        payload = {
            "status": result.status,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "exit_code": result.exit_code,
            "signal": result.signal,
            "duration_seconds": result.duration_seconds,
            "limit_reason": result.limit_reason,
            "isolation": result.isolation,
            "runtime": result.runtime,
            "protections": list(result.protections),
            "draft_revision": result.draft_revision,
        }
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_RESULT_RECORD_BYTES:
            return
        with cli.file_lock(topic):
            self._validate_course(slug, topic)
            with cli.file_lock(workspace):
                current = self._read(workspace)
            if self._revision(current) != result.draft_revision:
                return
            with cli.file_lock(result_path):
                cli.write_text_atomic(result_path, encoded + "\n")
                try:
                    result_path.chmod(0o600)
                except OSError:
                    pass

    def _read_result(self, path: Path) -> ExecutionResult | None:
        if not path.exists():
            return None
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > MAX_RESULT_RECORD_BYTES
        ):
            raise CodeWorkspaceError("saved run result is unsafe")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict):
            return None
        required_strings = ("status", "stdout", "stderr", "draft_revision")
        if any(not isinstance(value.get(key), str) for key in required_strings):
            return None
        duration = value.get("duration_seconds")
        if (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(duration)
            or duration < 0
        ):
            return None
        protections = value.get("protections")
        if not isinstance(protections, list) or not all(
            isinstance(item, str) for item in protections
        ):
            return None
        isolation = value.get("isolation")
        if isolation not in {None, "oci", "reduced"}:
            return None
        optional_strings = ("limit_reason", "runtime")
        if any(
            value.get(key) is not None and not isinstance(value.get(key), str)
            for key in optional_strings
        ):
            return None
        optional_integers = ("exit_code", "signal")
        if any(
            value.get(key) is not None
            and (not isinstance(value.get(key), int) or isinstance(value.get(key), bool))
            for key in optional_integers
        ):
            return None
        if not isinstance(value.get("timed_out"), bool):
            return None
        if len(value["stdout"].encode("utf-8")) > MAX_RESULT_BYTES or len(
            value["stderr"].encode("utf-8")
        ) > MAX_RESULT_BYTES:
            return None
        return ExecutionResult(
            status=value["status"],
            stdout=value["stdout"],
            stderr=value["stderr"],
            timed_out=value["timed_out"],
            exit_code=value.get("exit_code"),
            signal=value.get("signal"),
            duration_seconds=float(duration),
            limit_reason=value.get("limit_reason"),
            isolation=isolation,
            runtime=value.get("runtime"),
            protections=tuple(protections),
            draft_revision=value["draft_revision"],
        )

    @classmethod
    def _snapshot(cls, slug: str, source: str) -> DraftSnapshot:
        return DraftSnapshot(slug, "python", source, cls._revision(source))

    @staticmethod
    def _revision(source: str) -> str:
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    @staticmethod
    def _bounded(value: str) -> str:
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= MAX_RESULT_BYTES:
            return value
        suffix = b"\n[output truncated]"
        return (encoded[: MAX_RESULT_BYTES - len(suffix)] + suffix).decode(
            "utf-8",
            errors="replace",
        )
