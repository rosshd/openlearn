"""Representative mock-mode missions driven through the public terminal UI."""

from __future__ import annotations

import json
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
_ALLOWED_ENVIRONMENT_KEYS = ("LANG", "LC_ALL", "LC_CTYPE", "PATH", "TMPDIR")


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
        sensitive_values=(),
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
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        runner.close()
        if home.is_dir():
            bundle.capture_final_state()
        bundle.fail(f"{type(error).__name__}: mission failed before completion")
        raise
    finally:
        runner.close()

    expected_topic = home / "learning-topics" / f"{COURSE_SLUG}.md"
    achieved = result.exit_status == 0 and _saved_draft_matches(expected_topic)
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
        key: os.environ[key]
        for key in _ALLOWED_ENVIRONMENT_KEYS
        if key in os.environ
    }
    env.update(
        {
            "HOME": str(home),
            "OPENLEARN_HOME": str(home),
            "OPENLEARN_MOCK": "1",
            "TERM": "xterm-256color",
            "COLUMNS": "120",
            "LINES": "24",
            "PYTHONPATH": _worktree_pythonpath(),
        }
    )
    env.pop("OPENLEARN_BASE_URL", None)
    env.pop("OPENLEARN_MODEL", None)
    return env


def _worktree_pythonpath() -> str:
    source = Path(__file__).resolve().parents[2] / "src"
    return str(source)


def _saved_draft_matches(
    path: Path,
    *,
    course_name: str = COURSE_NAME,
    course_slug: str = COURSE_SLUG,
    course_goal: str = COURSE_GOAL,
) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return False
    try:
        metadata_text, body = text[4:].split("\n---\n", 1)
        metadata = json.loads(metadata_text)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return (
        path.name == f"{course_slug}.md"
        and metadata.get("topic") == course_name
        and metadata.get("slug") == course_slug
        and metadata.get("goal") == course_goal
        and metadata.get("course_started") is False
        and f"# {course_name}" in body
        and f"## Current Goal\n\n{course_goal}" in body
    )
