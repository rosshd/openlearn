from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pexpect

from tests.dogfood.evidence import EvidenceRecorder
from tests.dogfood.pty_runner import PtyMissionRunner, _EvidenceLog


def read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_runner_drives_real_pty_and_persists_sanitized_interactions(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "events.jsonl"
    private_input = "mission-private-value"
    recorder = EvidenceRecorder(evidence_path, sensitive_values=[private_input])
    program = (
        "import os; "
        "print(f'PTY={os.isatty(0) and os.isatty(1)}'); "
        "value = input('Prompt> '); "
        f"print('MATCH=' + str(value == {private_input!r}))"
    )
    runner = PtyMissionRunner(
        [sys.executable, "-c", program],
        env={**os.environ, "TERM": "xterm-256color"},
        recorder=recorder,
    )

    try:
        runner.start()
        runner.expect("Prompt> ")
        entry_frame = runner.rendered_output
        runner.sendline(private_input)
        runner.expect(pexpect.EOF)
        completion_frame = runner.rendered_output
        result = runner.finish()
    finally:
        runner.close()

    persisted = evidence_path.read_text(encoding="utf-8")
    events = read_events(evidence_path)
    rendered_output = "".join(
        str(event["text"]) for event in events if event["event"] == "output"
    )

    assert result.exit_status == 0
    assert result.signal_status is None
    assert result.interaction_count == 1
    assert "PTY=True" in entry_frame
    assert "Prompt> " in entry_frame
    assert private_input not in completion_frame
    assert "MATCH=True" in completion_frame
    assert "PTY=True" in rendered_output
    assert "MATCH=True" in rendered_output
    assert private_input not in persisted
    assert [event["text"] for event in events if event["event"] == "input"] == [
        "[REDACTED]"
    ]


def test_output_redaction_crosses_pty_read_boundaries(tmp_path: Path) -> None:
    evidence_path = tmp_path / "events.jsonl"
    recorder = EvidenceRecorder(evidence_path, sensitive_values=())
    log = _EvidenceLog(recorder)

    log.write("credential=sk-test-")
    log.write("12345678")
    log.flush_output()

    persisted = evidence_path.read_text(encoding="utf-8")
    assert "sk-test-12345678" not in persisted
    assert read_events(evidence_path)[0]["text"] == "credential=[REDACTED]"


def test_observe_settles_after_chunked_output_without_screen_pattern(
    tmp_path: Path,
) -> None:
    recorder = EvidenceRecorder(tmp_path / "events.jsonl", sensitive_values=())
    program = (
        "import sys, time; "
        "sys.stdout.write('first'); sys.stdout.flush(); "
        "time.sleep(0.03); "
        "sys.stdout.write(' second'); sys.stdout.flush(); "
        "time.sleep(30)"
    )
    runner = PtyMissionRunner(
        [sys.executable, "-c", program],
        env={**os.environ, "TERM": "xterm-256color"},
        recorder=recorder,
    )

    try:
        runner.start()
        observation = runner.observe(quiet_interval=0.06, timeout=0.5)
    finally:
        runner.close()

    assert observation.text == "first second"
    assert observation.settled_by == "quiet"
    assert observation.has_unsupported_controls is False


def test_observe_distinguishes_eof_and_hard_timeout(tmp_path: Path) -> None:
    recorder = EvidenceRecorder(tmp_path / "eof.jsonl", sensitive_values=())
    eof_runner = PtyMissionRunner(
        [sys.executable, "-c", "print('done')"],
        env=os.environ,
        recorder=recorder,
    )
    try:
        eof_runner.start()
        eof_observation = eof_runner.observe(quiet_interval=0.2, timeout=0.5)
        eof_runner.finish()
    finally:
        eof_runner.close()

    timeout_recorder = EvidenceRecorder(tmp_path / "timeout.jsonl", sensitive_values=())
    timeout_runner = PtyMissionRunner(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env=os.environ,
        recorder=timeout_recorder,
    )
    try:
        timeout_runner.start()
        timeout_observation = timeout_runner.observe(quiet_interval=1, timeout=0.02)
    finally:
        timeout_runner.close()

    assert eof_observation.text == "done\n"
    assert eof_observation.settled_by == "eof"
    assert timeout_observation.text == ""
    assert timeout_observation.settled_by == "timeout"


def test_observe_marks_cursor_addressing_and_rewrite_controls(tmp_path: Path) -> None:
    recorder = EvidenceRecorder(tmp_path / "events.jsonl", sensitive_values=())
    program = "import sys; sys.stdout.write('safe\\x1b[2Jrewrite\\runsafe'); sys.stdout.flush()"
    runner = PtyMissionRunner(
        [sys.executable, "-c", program],
        env=os.environ,
        recorder=recorder,
    )

    try:
        runner.start()
        observation = runner.observe(quiet_interval=0.05, timeout=0.5)
        runner.finish()
    finally:
        runner.close()

    assert observation.has_unsupported_controls is True
