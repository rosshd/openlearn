from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from tests.dogfood.artifacts import EvidenceBundle, MissionMetadata
from tests.dogfood.codex_driver import CodexDecision, CodexDecisionError, DecisionContext
from tests.dogfood.explorer import Explorer, ExplorerLimits
from tests.dogfood.pty_runner import PtyMissionRunner


class FakeSource:
    source_kind = "fake"
    last_provenance = None

    def __init__(self, decisions: list[CodexDecision | BaseException]) -> None:
        self.decisions = decisions
        self.contexts: list[DecisionContext] = []

    def decide(self, context: DecisionContext) -> CodexDecision:
        self.contexts.append(context)
        next_value = self.decisions.pop(0)
        if isinstance(next_value, BaseException):
            raise next_value
        return next_value


def make_explorer(
    tmp_path: Path,
    source: FakeSource,
    program: str,
    *,
    limits: ExplorerLimits | None = None,
) -> tuple[Explorer, EvidenceBundle]:
    home = tmp_path / "home"
    home.mkdir(parents=True)
    bundle = EvidenceBundle(
        tmp_path / "evidence",
        MissionMetadata(
            persona="A terminal beginner",
            mission="Reach DONE",
            provider_mode="mock",
            openlearn_home=home,
            command=(sys.executable, "-c", program),
        ),
        sensitive_values=(),
    )
    runner = PtyMissionRunner(
        [sys.executable, "-c", program],
        env={**os.environ, "TERM": "xterm-256color"},
        recorder=bundle.recorder,
    )
    return (
        Explorer(
            runner=runner,
            bundle=bundle,
            decision_source=source,
            persona="A terminal beginner",
            goal="Reach DONE",
            outcome_check=lambda output: "DONE" in output,
            limits=limits
            or ExplorerLimits(
                max_turns=3,
                max_elapsed_seconds=2,
                observation_chars=80,
                quiet_interval=0.03,
                observation_timeout=0.3,
            ),
        ),
        bundle,
    )


def read_manifest(bundle: EvidenceBundle) -> dict[str, object]:
    return json.loads((bundle.root / "manifest.json").read_text(encoding="utf-8"))


def test_explorer_dispatches_text_and_named_keys_and_links_exact_observations(
    tmp_path: Path,
) -> None:
    program = (
        "first=input('Menu> '); print('FIRST=' + first); "
        "second=input('Again> '); print('DONE=' + second)"
    )
    source = FakeSource(
        [
            CodexDecision(action="submit_text", text="hello"),
            CodexDecision(action="press_key", key="enter"),
        ]
    )
    explorer, bundle = make_explorer(tmp_path, source, program)

    result = explorer.run()

    records = [
        json.loads(line)
        for line in (bundle.root / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert result.status == "achieved"
    assert [context.observation for context in source.contexts] == [
        record["observation"] for record in records
    ]
    assert [record["observation_id"] for record in records] == [
        "observation-0001",
        "observation-0002",
    ]
    assert source.contexts[1].prior_actions == ("submit_text:hello",)
    assert read_manifest(bundle)["status"] == "completed"


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("escape", "\x1b"),
        ("backspace", "\x7f"),
        ("up", "\x1b[A"),
        ("down", "\x1b[B"),
        ("left", "\x1b[D"),
        ("right", "\x1b[C"),
        ("ctrl_c", "\x03"),
    ],
)
def test_explorer_dispatches_each_allow_listed_named_key(
    tmp_path: Path,
    monkeypatch,
    key: str,
    expected: str,
) -> None:
    explorer, _bundle = make_explorer(
        tmp_path,
        FakeSource([]),
        "import time; time.sleep(30)",
    )
    sent: list[str] = []
    monkeypatch.setattr(explorer.runner, "send", sent.append)

    explorer._dispatch(CodexDecision(action="press_key", key=key))

    assert sent == [expected]


@pytest.mark.parametrize(
    ("decisions", "max_turns", "expected_status"),
    [
        ([CodexDecision(action="stop", reason="done exploring")], 2, "stopped"),
        ([CodexDecision(action="press_key", key="enter")], 1, "turn_exhausted"),
        ([CodexDecisionError("bad response")], 2, "decision_failed"),
    ],
)
def test_explorer_finalizes_bounded_terminal_states(
    tmp_path: Path,
    decisions: list[CodexDecision | BaseException],
    max_turns: int,
    expected_status: str,
) -> None:
    source = FakeSource(decisions)
    explorer, bundle = make_explorer(
        tmp_path,
        source,
        "import time; print('Menu> ', flush=True); time.sleep(30)",
        limits=ExplorerLimits(
            max_turns=max_turns,
            max_elapsed_seconds=2,
            observation_chars=80,
            quiet_interval=0.02,
            observation_timeout=0.1,
        ),
    )

    result = explorer.run()

    manifest = read_manifest(bundle)
    assert result.status == expected_status
    assert manifest["status"] in {"completed", "failed"}
    assert manifest["outcome"]["summary"] == result.summary


def test_explorer_finalizes_eof_interruption_and_rejects_viewport_controls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    eof_explorer, eof_bundle = make_explorer(tmp_path / "eof", FakeSource([]), "print('bye')")
    assert eof_explorer.run().status == "pty_eof"
    assert read_manifest(eof_bundle)["status"] == "failed"

    control_explorer, control_bundle = make_explorer(
        tmp_path / "control",
        FakeSource([]),
        "import sys; sys.stdout.write('menu\\x1b[2J'); sys.stdout.flush()",
    )
    assert control_explorer.run().status == "unsupported_terminal_controls"
    assert read_manifest(control_bundle)["status"] == "failed"

    interrupted_explorer, interrupted_bundle = make_explorer(
        tmp_path / "interrupt",
        FakeSource([]),
        "import time; time.sleep(30)",
    )

    def interrupt(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(interrupted_explorer.runner, "observe", interrupt)
    with pytest.raises(KeyboardInterrupt):
        interrupted_explorer.run()
    assert read_manifest(interrupted_bundle)["status"] == "failed"


def test_explorer_retains_latest_bounded_observation_and_truncation_metadata(
    tmp_path: Path,
) -> None:
    source = FakeSource([CodexDecision(action="stop", reason="enough")])
    explorer, bundle = make_explorer(
        tmp_path,
        source,
        "print('0123456789' * 20, flush=True); import time; time.sleep(30)",
        limits=ExplorerLimits(
            max_turns=1,
            max_elapsed_seconds=2,
            observation_chars=25,
            quiet_interval=0.02,
            observation_timeout=0.1,
        ),
    )

    explorer.run()

    record = json.loads((bundle.root / "decisions.jsonl").read_text(encoding="utf-8"))
    assert len(source.contexts[0].observation) == 25
    assert source.contexts[0].observation == record["observation"]
    assert record["observation_truncated"] is True
    assert record["observation_original_chars"] == 201


def test_explorer_elapsed_budget_can_expire_before_a_decision(tmp_path: Path) -> None:
    source = FakeSource([])
    explorer, bundle = make_explorer(
        tmp_path,
        source,
        "import time; time.sleep(30)",
        limits=ExplorerLimits(
            max_turns=2,
            max_elapsed_seconds=0.01,
            observation_chars=80,
            quiet_interval=0.1,
            observation_timeout=0.1,
        ),
    )

    result = explorer.run()

    assert result.status == "elapsed_exhausted"
    assert source.contexts == []
    assert read_manifest(bundle)["status"] == "failed"


def test_explorer_does_not_dispatch_a_decision_returned_after_elapsed_budget(
    tmp_path: Path,
) -> None:
    class SlowFakeSource(FakeSource):
        def decide(self, context: DecisionContext) -> CodexDecision:
            self.contexts.append(context)
            time.sleep(0.03)
            return CodexDecision(action="submit_text", text="must-not-be-sent")

    source = SlowFakeSource([])
    explorer, bundle = make_explorer(
        tmp_path,
        source,
        "import time; print('Menu> ', flush=True); time.sleep(30)",
        limits=ExplorerLimits(
            max_turns=2,
            max_elapsed_seconds=0.04,
            observation_chars=80,
            quiet_interval=0.005,
            observation_timeout=0.01,
        ),
    )

    result = explorer.run()

    interactions = (bundle.root / "interactions.jsonl").read_text(encoding="utf-8")
    assert result.status == "elapsed_exhausted"
    assert len(source.contexts) == 1
    assert "must-not-be-sent" not in interactions
