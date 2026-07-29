from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.skip("pexpect.spawn requires a POSIX pty", allow_module_level=True)

import pexpect


CALIBRATION = (
    "I have completed data structures and algorithims college course months ago, "
    "and have done leetcoding on and off for the past 2 years, but can never stay "
    "consistant. I intern at state farm currently, and have some outside projects "
    "I work on like an AI tutor application and a guitar digital tuning pedal."
)
CLARIFICATION = (
    "How is the input given to us? is it an array or text separated by commas? "
    "what would you like for me to return when I find a solution? "
    "Can I see example inputs and outputs?"
)
PLAN = (
    "I would use a sliding window with a hashmap to keep track of amounts of "
    "charactors. When they are all unique, I would return the result"
)
def test_interview_prep_public_cli_journey(spawn_openlearn, monkeypatch) -> None:
    proc = spawn_openlearn.spawn(
        "new",
        "Leetcode Sweep",
        "--goal",
        "Build consistent coding interview practice",
        "--interview-prep",
        timeout=10,
    )
    try:
        for prompt in (
            "Target role family",
            "Target level",
            "Interview date",
            "Weekly practice minutes",
            "Session minutes",
        ):
            proc.expect(prompt)
            proc.sendline("")
        proc.expect("Create this interview-prep course")
        proc.sendline("y")
        proc.expect("Start offline placement now")
        proc.sendline("y")

        proc.expect("calibration>")
        proc.sendline(CALIBRATION)
        proc.expect("clarification>")
        proc.sendline(CLARIFICATION)
        proc.expect("Python string")
        proc.expect("zero-based start index")
        proc.expect("Examples:")
        proc.expect("plan>")
        proc.sendline(PLAN)
        proc.expect("implementation>")
        proc.sendline("/skip")
        proc.expect("Dependent coding evidence remains uncertain")
        proc.expect("Placement complete")
        proc.expect("Placement: provisional")
        proc.expect("Placement saved. Continue from the main menu to build your course plan.")
        proc.expect(pexpect.EOF)
        proc.child.close()
        assert proc.child.exitstatus == 0
        transcript = proc.clean_output
        assert "must be non-empty" not in transcript
        assert "No previous session yet" not in transcript
        for removed_prompt in (
            "Coding language",
            "Data structures experience",
            "Algorithms experience",
            "Interview experience",
            "Target notes",
            "Accessibility preferences",
        ):
            assert removed_prompt not in transcript
        assert "tests>" not in transcript
        assert "complexity>" not in transcript
        assert "follow_up>" not in transcript
    finally:
        proc.close()

    with monkeypatch.context() as patch:
        for key in ("OPENLEARN_MOCK", "OPENAI_API_KEY", "OPENLEARN_BASE_URL"):
            patch.delitem(spawn_openlearn.env, key, raising=False)
        missing_provider = spawn_openlearn.spawn("resume", "leetcode-sweep", timeout=10)
        try:
            missing_provider.expect("Placement: provisional \\(7/7\\)")
            missing_provider.expect("Model-backed course planning is not configured")
            missing_provider.expect("openlearn config set-key")
            missing_provider.expect("all work is saved")
            missing_provider.expect(pexpect.EOF)
            missing_provider.child.close()
            assert missing_provider.child.exitstatus == 1
            assert "Where you left off" not in missing_provider.clean_output
            assert "No previous session yet" not in missing_provider.clean_output
        finally:
            missing_provider.close()

    course = spawn_openlearn.spawn("resume", "leetcode-sweep", timeout=10)
    try:
        course.expect("Placement: provisional \\(7/7\\)")
        course.expect("Course outline")
        course.expect("Is this an acceptable course outline")
        course.sendline("y")
        course.expect("First lesson")
        course.expect("Normal vs Insert")
        course.expect(pexpect.EOF)
        course.child.close()
        assert course.child.exitstatus == 0
        transcript = course.clean_output
        assert "Run optional placement quiz" not in transcript
        assert "No previous session yet" not in transcript
    finally:
        course.close()

    status = spawn_openlearn.run(
        "interview", "placement", "leetcode-sweep", "status"
    )
    assert "Placement: provisional" in status.stdout
    assert "evidence 7/7" in status.stdout

    home = Path(spawn_openlearn.env["OPENLEARN_HOME"])
    profile = json.loads(
        (home / "learning-topics" / "leetcode-sweep.interview.json").read_text(
            encoding="utf-8"
        )
    )
    assert profile["placement"]["result"]["mastery_update_applied"] is False
    assert len(profile["placement"]["evidence_refs"]) == 7
