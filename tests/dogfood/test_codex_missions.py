from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

import tests.dogfood.codex_missions as codex_missions
from tests.dogfood.codex_driver import CodexDecision, CodexDecisionError, DecisionContext
from tests.dogfood.codex_missions import (
    COURSE_GOAL,
    COURSE_NAME,
    COURSE_SLUG,
    CodexMissionVariant,
    CodexMissionOutcome,
    run_codex_draft_course_mission,
    run_live_codex_missions,
    verify_single_matching_draft,
)
from tests.dogfood.explorer import ExplorerResult


class FakeDecisionSource:
    source_kind = "fake"

    def __init__(self, decisions: list[CodexDecision | BaseException]) -> None:
        self._decisions = decisions
        self.contexts: list[DecisionContext] = []
        self.last_provenance = {
            "source_kind": "fake",
            "cli_version": None,
            "model": None,
            "invocation_fingerprint": "fake-sequence-v1",
            "schema_fingerprint": "fake-schema-v1",
            "process_status": 0,
            "duration_seconds": 0.0,
            "event_counts": {"fake.decision": 1},
        }

    def decide(self, context: DecisionContext) -> CodexDecision:
        self.contexts.append(context)
        value = self._decisions.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _installed_openlearn() -> Path:
    root = Path(__file__).resolve().parents[2]
    candidates = (
        root / ".venv" / "bin" / "openlearn",
        root.parent.parent / ".venv" / "bin" / "openlearn",
    )
    executable = next((path for path in candidates if path.is_file()), None)
    assert executable is not None, "installed openlearn executable is required"
    return executable


def _direct_decisions() -> list[CodexDecision]:
    return [
        CodexDecision("submit_text", text="2"),
        CodexDecision("submit_text", text="1"),
        CodexDecision("submit_text", text=COURSE_NAME),
        CodexDecision("submit_text", text="2"),
        CodexDecision("submit_text", text=COURSE_GOAL),
        CodexDecision("submit_text", text="b"),
        CodexDecision("submit_text", text="y"),
    ]


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _topic_text(name: str, slug: str, goal: str) -> str:
    metadata = {
        "topic": name,
        "slug": slug,
        "goal": goal,
        "course_started": False,
    }
    return (
        "---\n"
        + json.dumps(metadata)
        + "\n---\n\n"
        + f"# {name}\n\n## Current Goal\n\n{goal}\n"
    )


@pytest.mark.parametrize(
    ("variant", "prefix", "expected_turns"),
    [
        (CodexMissionVariant.DIRECT, [], 7),
        (CodexMissionVariant.ERROR_PRONE, ["99"], 8),
    ],
)
def test_fake_source_composes_public_pty_mission_and_preserves_route(
    tmp_path: Path,
    variant: CodexMissionVariant,
    prefix: list[str],
    expected_turns: int,
) -> None:
    source = FakeDecisionSource(
        [CodexDecision("submit_text", text=value) for value in prefix]
        + _direct_decisions()
    )

    outcome = run_codex_draft_course_mission(
        tmp_path / variant.value,
        command=(_installed_openlearn(), "menu"),
        decision_source=source,
        variant=variant,
    )

    manifest = json.loads(
        (outcome.evidence / "manifest.json").read_text(encoding="utf-8")
    )
    decisions = _read_jsonl(outcome.evidence / "decisions.jsonl")
    interactions = _read_jsonl(outcome.evidence / "interactions.jsonl")
    entered = [event["text"] for event in interactions if event["event"] == "input"]

    assert outcome.achieved is True
    assert outcome.result.status == "achieved"
    assert outcome.result.turns == expected_turns
    assert entered[: len(prefix)] == prefix
    assert [record["source_kind"] for record in decisions] == ["fake"] * expected_turns
    assert all(record["provenance"]["source_kind"] == "fake" for record in decisions)
    assert manifest["status"] == "completed"
    assert manifest["outcome"]["achieved"] is True
    assert verify_single_matching_draft(outcome.home) is True
    assert all("exactly one" not in context.goal.lower() for context in source.contexts)
    assert all(COURSE_SLUG not in context.goal for context in source.contexts)
    assert all("draft for later" in context.goal.lower() for context in source.contexts)
    assert all("without starting" in context.goal.lower() for context in source.contexts)
    assert all("99" not in context.persona for context in source.contexts)

    output = "".join(
        str(event["text"]) for event in interactions if event["event"] == "output"
    )
    if variant is CodexMissionVariant.ERROR_PRONE:
        assert "Choose a number, or q to quit." in output
        assert entered[0:2] == ["99", "2"]
    else:
        assert "Choose a number, or q to quit." not in output


@pytest.mark.parametrize(
    ("variant", "prefix"),
    [
        (CodexMissionVariant.DIRECT, ["99"]),
        (CodexMissionVariant.ERROR_PRONE, []),
    ],
)
def test_goal_completion_is_not_rejected_by_unexpected_route_variation(
    tmp_path: Path,
    variant: CodexMissionVariant,
    prefix: list[str],
) -> None:
    source = FakeDecisionSource(
        [CodexDecision("submit_text", text=value) for value in prefix]
        + _direct_decisions()
    )

    outcome = run_codex_draft_course_mission(
        tmp_path / variant.value,
        command=(_installed_openlearn(), "menu"),
        decision_source=source,
        variant=variant,
    )

    assert outcome.achieved is True
    assert outcome.result.status == "achieved"


def test_hidden_verifier_rejects_missing_duplicate_and_mismatched_drafts(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    topics = home / "learning-topics"
    topics.mkdir(parents=True)
    assert verify_single_matching_draft(home) is False

    expected = topics / f"{COURSE_SLUG}.md"
    expected.write_text(_topic_text(COURSE_NAME, COURSE_SLUG, COURSE_GOAL), encoding="utf-8")
    assert verify_single_matching_draft(home) is True

    (topics / "duplicate.md").write_text(
        _topic_text(COURSE_NAME, COURSE_SLUG, COURSE_GOAL), encoding="utf-8"
    )
    assert verify_single_matching_draft(home) is False

    (topics / "duplicate.md").unlink()
    expected.rename(topics / "wrong-name.md")
    assert verify_single_matching_draft(home) is False


@pytest.mark.parametrize(
    "topic_text",
    [
        _topic_text("Wrong", COURSE_SLUG, COURSE_GOAL),
        _topic_text(COURSE_NAME, "wrong-slug", COURSE_GOAL),
        _topic_text(COURSE_NAME, COURSE_SLUG, "Wrong goal"),
        _topic_text(COURSE_NAME, COURSE_SLUG, COURSE_GOAL).replace(
            '"course_started": false', '"course_started": true'
        ),
        _topic_text(COURSE_NAME, COURSE_SLUG, COURSE_GOAL).replace(
            f"# {COURSE_NAME}", "# Wrong heading"
        ),
        _topic_text(COURSE_NAME, COURSE_SLUG, COURSE_GOAL).replace(
            "## Current Goal", "## Missing Goal Heading"
        ),
        "not front matter",
    ],
)
def test_hidden_verifier_rejects_each_mismatched_public_field(
    tmp_path: Path,
    topic_text: str,
) -> None:
    topics = tmp_path / "home" / "learning-topics"
    topics.mkdir(parents=True)
    (topics / f"{COURSE_SLUG}.md").write_text(topic_text, encoding="utf-8")

    assert verify_single_matching_draft(tmp_path / "home") is False


def test_mission_refuses_existing_root_and_uses_private_directories(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        run_codex_draft_course_mission(
            existing,
            command=(sys.executable, "-c", "print('unused')"),
            decision_source=FakeDecisionSource([]),
            variant=CodexMissionVariant.DIRECT,
        )
    with pytest.raises(FileExistsError):
        run_live_codex_missions(
            existing,
            command=(sys.executable, "-c", "print('unused')"),
            codex_home=tmp_path / "codex-home",
        )

    outcome = run_codex_draft_course_mission(
        tmp_path / "private-run",
        command=(_installed_openlearn(), "menu"),
        decision_source=FakeDecisionSource(_direct_decisions()),
        variant=CodexMissionVariant.DIRECT,
    )
    for directory in (outcome.run_root, outcome.home, outcome.evidence):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for path in outcome.evidence.rglob("*"):
        expected = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected


def test_live_wrapper_composes_both_isolated_variants_without_starting_codex(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_calls: list[dict[str, object]] = []
    mission_calls: list[tuple[Path, tuple[str | Path, ...], object, CodexMissionVariant]] = []

    class RecordingSource:
        def __init__(self, **kwargs) -> None:
            source_calls.append(kwargs)

    def fake_mission(
        run_root: Path,
        *,
        command,
        decision_source,
        variant: CodexMissionVariant,
    ) -> CodexMissionOutcome:
        run_root.mkdir(mode=0o700)
        home = run_root / "home"
        evidence = run_root / "evidence"
        home.mkdir()
        evidence.mkdir()
        mission_calls.append((run_root, tuple(command), decision_source, variant))
        return CodexMissionOutcome(
            run_root=run_root,
            home=home,
            evidence=evidence,
            achieved=True,
            result=ExplorerResult("achieved", True, "done", 1, 0.1),
        )

    monkeypatch.setattr(codex_missions, "CodexExecDecisionSource", RecordingSource)
    monkeypatch.setattr(codex_missions, "run_codex_draft_course_mission", fake_mission)

    outcomes = run_live_codex_missions(
        tmp_path / "live",
        command=("openlearn", "menu"),
        codex_home=tmp_path / "codex-home",
        codex_executable="custom-codex",
        model="gpt-test",
    )

    assert [outcome.run_root.name for outcome in outcomes] == ["direct", "error-prone"]
    assert [call[3] for call in mission_calls] == list(CodexMissionVariant)
    assert all(call[1] == ("openlearn", "menu") for call in mission_calls)
    assert [Path(call["isolated_directory"]).name for call in source_calls] == [
        "direct-codex-workspace",
        "error-prone-codex-workspace",
    ]
    assert all(call["executable"] == "custom-codex" for call in source_calls)
    assert all(call["model"] == "gpt-test" for call in source_calls)


def test_codex_failure_is_finalized_with_actionable_summary(tmp_path: Path) -> None:
    source = FakeDecisionSource(
        [CodexDecisionError("Codex decision process exited with status 1")]
    )
    outcome = run_codex_draft_course_mission(
        tmp_path / "failed",
        command=(sys.executable, "-c", "import time; print('Menu> ', flush=True); time.sleep(30)"),
        decision_source=source,
        variant=CodexMissionVariant.DIRECT,
    )

    manifest = json.loads(
        (outcome.evidence / "manifest.json").read_text(encoding="utf-8")
    )
    assert outcome.achieved is False
    assert outcome.result.status == "decision_failed"
    assert manifest["status"] == "failed"
    assert "exited with status 1" in manifest["outcome"]["summary"]
    assert manifest["artifacts"]["final_state"] == "final-state.json"
