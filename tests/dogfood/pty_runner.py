"""Real-PTY control surface for exploratory terminal missions."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pexpect

from tests.dogfood.evidence import EvidenceRecorder


@dataclass(frozen=True)
class PtyRunResult:
    """Process outcome and interaction metrics for a completed mission."""

    exit_status: int | None
    signal_status: int | None
    interaction_count: int
    elapsed_seconds: float


class _EvidenceLog:
    """File-like pexpect output sink that persists each rendered chunk."""

    def __init__(self, recorder: EvidenceRecorder) -> None:
        self._recorder = recorder

    def write(self, text: str) -> int:
        if text:
            self._recorder.record_output(text)
        return len(text)

    def flush(self) -> None:
        pass


class PtyMissionRunner:
    """Drive a command through keyboard input while recording its real PTY output."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        recorder: EvidenceRecorder,
        timeout: int | float = 5,
        dimensions: tuple[int, int] = (24, 120),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not command:
            raise ValueError("command must not be empty")
        self._command = tuple(command)
        self._env = dict(env)
        self._recorder = recorder
        self._timeout = timeout
        self._dimensions = dimensions
        self._clock = clock
        self._child: pexpect.spawn | None = None
        self._started_at: float | None = None
        self._interaction_count = 0
        self._result: PtyRunResult | None = None

    def start(self) -> None:
        if self._child is not None or self._result is not None:
            raise RuntimeError("mission runner can only be started once")
        self._started_at = self._clock()
        child = pexpect.spawn(
            self._command[0],
            list(self._command[1:]),
            env=self._env,
            dimensions=self._dimensions,
            encoding="utf-8",
            timeout=self._timeout,
        )
        child.logfile_read = _EvidenceLog(self._recorder)
        self._child = child

    def expect(self, pattern: Any, *, timeout: int | float = -1) -> int:
        """Wait for a visible terminal pattern or ``pexpect.EOF``."""
        return self._require_child().expect(pattern, timeout=timeout)

    def send(self, text: str) -> None:
        """Send literal keyboard input and record it before delivery."""
        self._record_input(text)
        self._require_child().send(text)

    def sendline(self, text: str = "") -> None:
        """Send keyboard input followed by Enter and record the entered text."""
        self._record_input(text)
        self._require_child().sendline(text)

    def finish(self) -> PtyRunResult:
        """Collect the status of a process that has already reached EOF."""
        child = self._require_child()
        if child.isalive():
            raise RuntimeError("mission process is still running")
        child.close()
        started_at = self._started_at
        if started_at is None:
            raise RuntimeError("mission runner has not started")
        self._result = PtyRunResult(
            exit_status=child.exitstatus,
            signal_status=child.signalstatus,
            interaction_count=self._interaction_count,
            elapsed_seconds=round(max(0.0, self._clock() - started_at), 6),
        )
        self._child = None
        return self._result

    def close(self) -> None:
        """Force-close a running child so callers can safely use ``finally`` blocks."""
        if self._child is None:
            return
        if self._child.isalive():
            self._child.close(force=True)
        else:
            self._child.close()
        self._child = None

    def _record_input(self, text: str) -> None:
        self._require_child()
        self._recorder.record_input(text)
        self._interaction_count += 1

    def _require_child(self) -> pexpect.spawn:
        if self._child is None:
            raise RuntimeError("mission runner is not running")
        return self._child
