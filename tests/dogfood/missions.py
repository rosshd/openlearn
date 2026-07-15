"""Representative mock-mode missions driven through the public terminal UI."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pexpect

from tests.dogfood.artifacts import EvidenceBundle, MissionMetadata
from tests.dogfood.pty_runner import PtyMissionRunner, PtyRunResult

COURSE_NAME = "Terminal Navigation Basics"
COURSE_GOAL = "Learn how to navigate a terminal confidently."
COURSE_SLUG = "terminal-navigation-basics"


@dataclass(frozen=True)
class MissionOutcome:
    """Paths and process outcome returned by a completed representative mission."""

    run_root: Path
    home: Path
    evidence: Path
    achieved: bool
    result: PtyRunResult


def run_mock_draft_course_mission(
    run_root: Path,
    *,
    command: Sequence[str | Path],
) -> MissionOutcome:
    """Create and save a course draft through an installed ``openlearn`` CLI."""
    normalized_command = tuple(str(part) for part in command)
    if not normalized_command:
        raise ValueError("command must not be empty")

    run_root.mkdir(parents=True, exist_ok=False)
    home = run_root / "home"
    home.mkdir()
    evidence_root = run_root / "evidence"
    bundle = EvidenceBundle(
        evidence_root,
        MissionMetadata(
            persona="A curious terminal beginner who prefers visible guidance.",
            mission="Create and save a terminal-navigation course draft.",
            provider_mode="mock",
            openlearn_home=home,
            command=normalized_command,
        ),
    )
    runner = PtyMissionRunner(
        normalized_command,
        env=_isolated_mock_environment(home),
        recorder=bundle.recorder,
        timeout=10,
    )

    try:
        runner.start()
        runner.expect("> ")
        bundle.capture_frame("Mission entry", runner.rendered_output)

        runner.sendline("2")
        runner.expect("Choose: ")
        runner.sendline("1")
        runner.expect("Course name: ")
        runner.sendline(COURSE_NAME)
        runner.expect("Choose: ")
        runner.sendline("2")
        runner.expect("Goal: ")
        runner.sendline(COURSE_GOAL)
        runner.expect("Choose: ")
        bundle.capture_frame("Draft details complete", runner.rendered_output)

        runner.sendline("b")
        runner.expect(r"Save this course draft for later\? \[y/N\]: ")
        runner.sendline("y")
        runner.expect("> ")
        bundle.capture_frame("Mission completion", runner.rendered_output)
        runner.sendline("q")
        runner.expect(pexpect.EOF)
        result = runner.finish()
    finally:
        runner.close()

    expected_topic = home / "learning-topics" / f"{COURSE_SLUG}.md"
    achieved = result.exit_status == 0 and expected_topic.is_file()
    bundle.capture_final_state()
    bundle.complete(
        result,
        achieved=achieved,
        summary=(
            "Saved the course draft through the public terminal menu."
            if achieved
            else "The public terminal mission did not save the expected course draft."
        ),
    )
    return MissionOutcome(
        run_root=run_root,
        home=home,
        evidence=evidence_root,
        achieved=achieved,
        result=result,
    )


def _isolated_mock_environment(home: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not _looks_sensitive_environment_key(key)
    }
    env.update(
        {
            "HOME": str(home),
            "OPENLEARN_HOME": str(home),
            "OPENLEARN_MOCK": "1",
            "TERM": "xterm-256color",
            "COLUMNS": "120",
            "LINES": "24",
        }
    )
    env.pop("OPENLEARN_BASE_URL", None)
    env.pop("OPENLEARN_MODEL", None)
    return env


def _looks_sensitive_environment_key(key: str) -> bool:
    normalized = key.upper()
    return any(
        marker in normalized
        for marker in ("API_KEY", "CREDENTIAL", "PASSWORD", "SECRET", "TOKEN")
    )
