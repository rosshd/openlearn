from __future__ import annotations

import json
from pathlib import Path

from tests.dogfood.missions import run_mock_draft_course_mission


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
