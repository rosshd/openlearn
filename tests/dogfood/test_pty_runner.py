from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pexpect

from tests.dogfood.evidence import EvidenceRecorder
from tests.dogfood.pty_runner import PtyMissionRunner


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
        runner.sendline(private_input)
        runner.expect(pexpect.EOF)
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
    assert "PTY=True" in rendered_output
    assert "MATCH=True" in rendered_output
    assert private_input not in persisted
    assert [event["text"] for event in events if event["event"] == "input"] == [
        "[REDACTED]"
    ]
