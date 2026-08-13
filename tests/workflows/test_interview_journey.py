from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.skip("pexpect.spawn requires a POSIX pty", allow_module_level=True)

import pexpect

from openlearn import interview_prep


def test_interview_prep_public_cli_journey(spawn_openlearn) -> None:
    templates_dir = Path(__file__).resolve().parents[2] / "src" / "openlearn" / "templates"
    template_choice = next(
        str(index)
        for index, path in enumerate(sorted(templates_dir.glob("*.json")), start=1)
        if path.stem == "technical-interview-prep"
    )
    first = spawn_openlearn.spawn("cli", timeout=10)
    try:
        first.expect("Starter courses")
        first.sendline("s")
        # Rich may insert ANSI styling between the menu number and title.
        first.expect("Technical Interview Prep")
        first.sendline(template_choice)
        first.expect("Placement is a quick confidence survey")
        first.expect("Start placement, skip it, or go back")
        first.sendline("")
        first.expect("Target role family")
        first.expect(r"Choose \[1\]")
        first.sendline("")
        first.expect("Target level")
        first.expect(r"Choose \[2\]")
        first.sendline("")
        first.expect("Interview mix")
        first.expect(r"Choose \[1\]")
        first.sendline("")
        first.expect("Rapid confidence survey")
        for _topic_id, label in interview_prep.confidence_topics_for_focus("coding"):
            first.expect(label)
            first.sendline("3")
        first.expect("Suggested course outline")
        first.expect("Linear Foundations")
        first.expect("Confirm, change, or leave this course outline for later")
        first.sendline("")
        first.expect("Course outline confirmed")
        first.expect("First technical target: concept.arrays-strings")
        first.expect("Starter courses")
        first.sendline("q")
        first.expect(pexpect.EOF)
        first.child.close()
        assert first.child.exitstatus == 0
        transcript = first.clean_output
        assert "first_unique_window" not in transcript
        assert "open your configured editor" not in transcript
        assert "Docker" not in transcript
        assert "Podman" not in transcript
    finally:
        first.close()

    status = spawn_openlearn.run(
        "interview", "placement", "technical-interview-prep", "status"
    )
    assert "Placement: provisional" in status.stdout
    assert "lifecycle confidence-placement-v4" in status.stdout

    home = Path(spawn_openlearn.env["OPENLEARN_HOME"])
    profile = json.loads(
        (home / "learning-topics" / "technical-interview-prep.interview.json").read_text(
            encoding="utf-8"
        )
    )
    result = profile["placement"]["result"]
    assert result["mastery_update_applied"] is False
    assert result["gaps"]["coding_fluency"]["status"] == "uncertain"
    assert result["passport"]["uncertainty_to_verify"] == (
        "Pattern fluency must be demonstrated in later coding practice."
    )
    state = json.loads(
        (home / "learning-topics" / "technical-interview-prep.state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["interview_curriculum"]["route_id"] == "coding"
    assert state["interview_curriculum"]["cursor"]["skill_ref"]["skill_id"] == (
        "concept.arrays-strings"
    )

    spawn_openlearn.run(
        "new",
        "Ordinary Algorithms",
        "--goal",
        "Learn algorithms without interview setup",
        "--template",
        "algorithms",
    )
    assert not (
        home / "learning-topics" / "ordinary-algorithms.interview.json"
    ).exists()
