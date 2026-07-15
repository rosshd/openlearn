from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pexpect
import pytest

from tests.dogfood.missions import _isolated_mock_environment, run_mock_draft_course_mission


def _installed_openlearn() -> Path:
    root = Path(__file__).resolve().parents[2]
    candidates = (
        root / ".venv" / "bin" / "openlearn",
        root.parent.parent / ".venv" / "bin" / "openlearn",
    )
    executable = next((path for path in candidates if path.is_file()), None)
    assert executable is not None, "installed openlearn executable is required"
    return executable


def test_mock_draft_course_mission_uses_public_cli_and_persists_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    unrelated_home = tmp_path / "unrelated-home"
    monkeypatch.setenv("OPENLEARN_HOME", str(unrelated_home))
    run_root = tmp_path / "mission-run"

    outcome = run_mock_draft_course_mission(
        run_root,
        command=(_installed_openlearn(), "menu"),
    )

    manifest_path = run_root / "evidence" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (run_root / "evidence" / "interactions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    input_events = [event["text"] for event in events if event["event"] == "input"]
    output = "".join(
        str(event["text"]) for event in events if event["event"] == "output"
    )

    assert outcome.achieved is True
    assert outcome.result.exit_status == 0
    assert outcome.result.interaction_count == 8
    assert manifest["environment"] == {
        "provider_mode": "mock",
        "openlearn_home": str(run_root / "home"),
        "command": [str(_installed_openlearn()), "menu"],
    }
    assert manifest["outcome"]["achieved"] is True
    assert manifest["outcome"]["interaction_count"] == 8
    assert [frame["label"] for frame in manifest["artifacts"]["frames"]] == [
        "Mission entry",
        "Draft details complete",
        "Mission completion",
    ]
    assert manifest["artifacts"]["final_state"] == "final-state.json"
    assert input_events == [
        "2",
        "1",
        "Terminal Navigation Basics",
        "2",
        "Learn how to navigate a terminal confidently.",
        "b",
        "y",
        "q",
    ]
    assert "New course" in output
    assert "Save this course draft for later?" in output
    assert (run_root / "home" / "learning-topics" / "terminal-navigation-basics.md").is_file()
    assert not unrelated_home.exists()

    topic_text = (
        run_root / "home" / "learning-topics" / "terminal-navigation-basics.md"
    ).read_text(encoding="utf-8")
    metadata_text = topic_text[4:].split("\n---\n", 1)[0]
    metadata = json.loads(metadata_text)
    assert metadata["topic"] == "Terminal Navigation Basics"
    assert metadata["slug"] == "terminal-navigation-basics"
    assert metadata["goal"] == "Learn how to navigate a terminal confidently."


def test_mock_mission_records_failed_outcome_and_final_state(tmp_path: Path) -> None:
    run_root = tmp_path / "failed-mission"

    with pytest.raises(pexpect.EOF):
        run_mock_draft_course_mission(
            run_root,
            command=(sys.executable, "-c", "print('unexpected prompt')"),
        )

    manifest = json.loads(
        (run_root / "evidence" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["outcome"]["achieved"] is False
    assert manifest["outcome"]["summary"].startswith("EOF: mission failed")
    assert manifest["artifacts"]["final_state"] == "final-state.json"
    assert "unexpected prompt" in (
        run_root / "evidence" / "interactions.jsonl"
    ).read_text(encoding="utf-8")


def test_mock_environment_imports_openlearn_from_active_worktree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(("/existing/one", "/existing/two")))
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@example.invalid/db")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "example-access-key")

    env = _isolated_mock_environment(tmp_path / "home")

    source = Path(__file__).resolve().parents[2] / "src"
    assert env["PYTHONPATH"] == str(source)
    assert "DATABASE_URL" not in env
    assert "AWS_ACCESS_KEY_ID" not in env


def test_interrupted_mission_is_finalized_as_failed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tests.dogfood.pty_runner import PtyMissionRunner

    def interrupt(_runner, _pattern, *, timeout=-1):
        del timeout
        raise KeyboardInterrupt

    monkeypatch.setattr(PtyMissionRunner, "expect", interrupt)
    run_root = tmp_path / "interrupted-mission"

    with pytest.raises(KeyboardInterrupt):
        run_mock_draft_course_mission(
            run_root,
            command=(sys.executable, "-c", "import time; time.sleep(30)"),
        )

    manifest = json.loads(
        (run_root / "evidence" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["outcome"]["summary"].startswith("KeyboardInterrupt: mission failed")
