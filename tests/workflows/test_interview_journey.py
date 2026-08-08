from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.skip("pexpect.spawn requires a POSIX pty", allow_module_level=True)

import pexpect


def test_interview_prep_public_cli_journey(spawn_openlearn) -> None:
    templates_dir = Path(__file__).resolve().parents[2] / "src" / "openlearn" / "templates"
    template_choice = next(
        str(index)
        for index, path in enumerate(sorted(templates_dir.glob("*.json")), start=1)
        if path.stem == "technical-interview-prep"
    )
    first = spawn_openlearn.spawn(timeout=10)
    try:
        first.expect("Starter courses")
        first.sendline("s")
        # Rich may insert ANSI styling between the menu number and title.
        first.expect("Technical Interview Prep")
        first.sendline(template_choice)
        first.expect("Placement is a short offline reasoning conversation")
        first.expect("Start placement, defer it, or go back")
        first.sendline("")
        first.expect("Short reasoning placement started")
        first.expect("clarification>")
        first.sendline("Can width exceed the text length?")
        first.expect("Line saved")
        first.expect("clarification>")
        first.sendline("Should I return the zero-based start index?")
        first.expect("Line saved")
        first.expect("clarification>")
        first.sendline("/stop")
        first.expect(r"Placement saved at clarification \(0/2\)")
        first.expect("Continue interview prep")
        first.sendline("q")
        first.expect(pexpect.EOF)
        first.child.close()
        assert first.child.exitstatus == 0
        transcript = first.clean_output
        assert "implementation>" not in transcript
        assert "open your configured editor" not in transcript
        assert "Docker" not in transcript
        assert "Podman" not in transcript
    finally:
        first.close()

    clarification_resume = spawn_openlearn.spawn(
        "resume", "technical-interview-prep", timeout=10
    )
    try:
        clarification_resume.expect("Resumed saved clarification draft with 2 lines")
        clarification_resume.expect("clarification>")
        clarification_resume.sendline("/show")
        clarification_resume.expect("Can width exceed the text length")
        clarification_resume.expect("Should I return the zero-based start index")
        clarification_resume.expect("clarification>")
        clarification_resume.sendline("/done")
        clarification_resume.expect("Clarification saved")
        clarification_resume.expect("reasoning>")
        clarification_resume.sendline("Use a sliding window and a set of seen characters.")
        clarification_resume.expect("Line saved")
        clarification_resume.expect("reasoning>")
        clarification_resume.sendline(
            "Test width one, repeated characters, and no valid window."
        )
        clarification_resume.expect("Line saved")
        clarification_resume.expect("reasoning>")
        clarification_resume.sendline("/stop")
        clarification_resume.expect(r"Placement saved at reasoning \(1/2\)")
        clarification_resume.expect(pexpect.EOF)
        clarification_resume.child.close()
        assert clarification_resume.child.exitstatus == 0
    finally:
        clarification_resume.close()

    reasoning_resume = spawn_openlearn.spawn(
        "resume", "technical-interview-prep", timeout=10
    )
    try:
        reasoning_resume.expect("Resumed saved reasoning draft with 2 lines")
        reasoning_resume.expect("reasoning>")
        reasoning_resume.sendline("The scan is O(n) time and O(width) space.")
        reasoning_resume.expect("Line saved")
        reasoning_resume.expect("reasoning>")
        reasoning_resume.sendline("/done")
        reasoning_resume.expect("Reasoning saved")
        reasoning_resume.expect("course-start passport")
        reasoning_resume.expect("Starting route:")
        reasoning_resume.expect("First activity:")
        reasoning_resume.expect("Coding fluency was not observed")
        reasoning_resume.expect("Starting your named first activity now")
        reasoning_resume.expect("Course outline")
        reasoning_resume.expect("Is this an acceptable course outline")
        reasoning_resume.sendline("y")
        reasoning_resume.expect("First lesson")
        reasoning_resume.expect("Sliding Window Foundations")
        reasoning_resume.expect(pexpect.EOF)
        reasoning_resume.child.close()
        assert reasoning_resume.child.exitstatus == 0
        transcript = reasoning_resume.clean_output
        assert "reasoning-placement-v3" not in transcript
        assert "mastery" not in transcript.lower()
    finally:
        reasoning_resume.close()

    status = spawn_openlearn.run(
        "interview", "placement", "technical-interview-prep", "status"
    )
    assert "Placement: provisional" in status.stdout
    assert "evidence 2/2" in status.stdout

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
        "Implement and test a complete solution without autocomplete."
    )
    assert len(profile["placement"]["evidence_refs"]) == 2

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
