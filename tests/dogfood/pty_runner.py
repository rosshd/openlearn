"""Real-PTY control surface for exploratory terminal missions."""

from __future__ import annotations

import re
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


@dataclass(frozen=True)
class PtyObservation:
    """One sanitized, route-free span drained from the PTY."""

    text: str
    settled_by: str
    has_unsupported_controls: bool


_UNSUPPORTED_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@A-HJ-LMPSTXbdfgrsu]")


class _EvidenceLog:
    """File-like pexpect output sink that persists each rendered chunk."""

    def __init__(self, recorder: EvidenceRecorder) -> None:
        self._recorder = recorder
        self._raw_chunks: list[str] = []
        self._pending_chunks: list[str] = []
        self._observation_chunk_offset = 0

    def write(self, text: str) -> int:
        if text:
            self._raw_chunks.append(text)
            self._pending_chunks.append(text)
        return len(text)

    def flush(self) -> None:
        pass

    @property
    def rendered_output(self) -> str:
        return self._recorder.sanitize_terminal("".join(self._raw_chunks))

    def flush_output(self) -> None:
        """Persist complete output spans so redaction crosses PTY read boundaries."""
        if self._pending_chunks:
            self._recorder.record_output("".join(self._pending_chunks))
            self._pending_chunks.clear()

    def drain_raw_observation(self) -> tuple[str, bool]:
        """Return only unread PTY chunks plus the cumulative control status."""
        observation = "".join(self._raw_chunks[self._observation_chunk_offset :])
        self._observation_chunk_offset = len(self._raw_chunks)
        return observation, _has_unsupported_controls("".join(self._raw_chunks))


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
        self._evidence_log: _EvidenceLog | None = None

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
        evidence_log = _EvidenceLog(self._recorder)
        child.logfile_read = evidence_log
        self._evidence_log = evidence_log
        self._child = child

    @property
    def rendered_output(self) -> str:
        """Return the sanitized terminal output rendered so far."""
        if self._evidence_log is None:
            raise RuntimeError("mission runner has not started")
        return self._evidence_log.rendered_output

    def expect(self, pattern: Any, *, timeout: int | float = -1) -> int:
        """Wait for a visible terminal pattern or ``pexpect.EOF``."""
        return self._require_child().expect(pattern, timeout=timeout)

    def observe(
        self,
        *,
        quiet_interval: float,
        timeout: float,
    ) -> PtyObservation:
        """Drain output until quiet, EOF, or a hard observation deadline."""
        if quiet_interval <= 0 or timeout <= 0:
            raise ValueError("observation intervals must be positive")
        child = self._require_child()
        evidence_log = self._evidence_log
        if evidence_log is None:
            raise RuntimeError("mission runner has not started")
        started_at = self._clock()
        settled_by = "timeout"
        saw_output = False
        while True:
            remaining = timeout - max(0.0, self._clock() - started_at)
            if remaining <= 0:
                break
            read_timeout = min(quiet_interval, remaining)
            try:
                child.read_nonblocking(size=4096, timeout=read_timeout)
                saw_output = True
            except pexpect.TIMEOUT:
                if saw_output:
                    settled_by = "quiet" if read_timeout == quiet_interval else "timeout"
                    break
            except pexpect.EOF:
                settled_by = "eof"
                break

        if settled_by != "eof" and not child.isalive():
            settled_by = "eof"
        raw_observation, has_unsupported_controls = evidence_log.drain_raw_observation()
        self._flush_output()
        return PtyObservation(
            text=self._recorder.sanitize_terminal(raw_observation),
            settled_by=settled_by,
            has_unsupported_controls=has_unsupported_controls,
        )

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
        self._flush_output()
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

    def terminate(self) -> PtyRunResult:
        """Stop and reap the child, returning deterministic process metrics."""
        child = self._require_child()
        self._flush_output()
        if child.isalive():
            child.close(force=True)
        else:
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
        self._flush_output()
        if self._child.isalive():
            self._child.close(force=True)
        else:
            self._child.close()
        self._child = None

    def _record_input(self, text: str) -> None:
        self._require_child()
        self._flush_output()
        self._recorder.record_input(text)
        self._interaction_count += 1

    def _flush_output(self) -> None:
        if self._evidence_log is not None:
            self._evidence_log.flush_output()

    def _require_child(self) -> pexpect.spawn:
        if self._child is None:
            raise RuntimeError("mission runner is not running")
        return self._child


def _has_unsupported_controls(text: str) -> bool:
    if _UNSUPPORTED_CSI.search(text):
        return True
    normalized = text.replace("\r\n", "")
    return "\r" in normalized or "\x08" in text
