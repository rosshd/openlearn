"""Opt-in Codex-driven draft-course dogfood missions."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from tests.dogfood.artifacts import EvidenceBundle, MissionMetadata
from tests.dogfood.codex_driver import CodexExecDecisionSource, DecisionSource
from tests.dogfood.explorer import Explorer, ExplorerLimits, ExplorerResult
from tests.dogfood.missions import _isolated_mock_environment, _saved_draft_matches
from tests.dogfood.pty_runner import PtyMissionRunner

COURSE_NAME = "Practical Git Basics"
COURSE_GOAL = "Learn how to inspect changes and make a safe commit."
COURSE_SLUG = "practical-git-basics"
PUBLIC_GOAL = f"Create and save a course named {COURSE_NAME!r} with goal {COURSE_GOAL!r}."


class CodexMissionVariant(str, Enum):
    """Learner route variants exercised by the opt-in smoke run."""

    DIRECT = "direct"
    ERROR_PRONE = "error-prone"


@dataclass(frozen=True)
class CodexMissionOutcome:
    """Finalized result and private artifact locations for one mission."""

    run_root: Path
    home: Path
    evidence: Path
    achieved: bool
    result: ExplorerResult


def run_codex_draft_course_mission(
    run_root: Path,
    *,
    command: Sequence[str | Path],
    decision_source: DecisionSource,
    variant: CodexMissionVariant,
) -> CodexMissionOutcome:
    """Drive one isolated draft mission with externally supplied decisions."""
    normalized_command = tuple(str(part) for part in command)
    if not normalized_command:
        raise ValueError("command must not be empty")
    run_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    run_root.chmod(0o700)
    home = run_root / "home"
    home.mkdir(mode=0o700)
    evidence = run_root / "evidence"
    persona = _persona(variant)
    bundle = EvidenceBundle(
        evidence,
        MissionMetadata(
            persona=persona,
            mission=PUBLIC_GOAL,
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
    explorer = Explorer(
        runner=runner,
        bundle=bundle,
        decision_source=decision_source,
        persona=persona,
        goal=PUBLIC_GOAL,
        outcome_check=lambda output: (
            "Save this course draft for later?" in output
            and _route_matches(output, variant)
            and verify_single_matching_draft(home)
        ),
        limits=ExplorerLimits(
            max_turns=12 if variant is CodexMissionVariant.DIRECT else 14,
            max_elapsed_seconds=300,
            observation_chars=12_000,
            quiet_interval=0.08,
            observation_timeout=2,
        ),
    )
    result = explorer.run()
    achieved = result.achieved and verify_single_matching_draft(home)
    return CodexMissionOutcome(
        run_root=run_root,
        home=home,
        evidence=evidence,
        achieved=achieved,
        result=result,
    )


def verify_single_matching_draft(home: Path) -> bool:
    """Verify the hidden state predicate without exposing it to the explorer."""
    topics = home / "learning-topics"
    drafts = sorted(topics.glob("*.md")) if topics.is_dir() else []
    if len(drafts) != 1:
        return False
    return _saved_draft_matches(
        drafts[0],
        course_name=COURSE_NAME,
        course_slug=COURSE_SLUG,
        course_goal=COURSE_GOAL,
    )


def run_live_codex_missions(
    output_root: Path,
    *,
    command: Sequence[str | Path],
    codex_home: Path,
    codex_executable: str = "codex",
    model: str | None = None,
) -> tuple[CodexMissionOutcome, ...]:
    """Run both live variants under a new private parent directory."""
    output_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    output_root.chmod(0o700)
    outcomes = []
    for variant in CodexMissionVariant:
        run_root = output_root / variant.value
        codex_workspace = output_root / f"{variant.value}-codex-workspace"
        codex_workspace.mkdir(mode=0o700)
        source = CodexExecDecisionSource(
            codex_home=codex_home,
            isolated_directory=codex_workspace,
            executable=codex_executable,
            model=model,
            timeout_seconds=45,
        )
        outcomes.append(
            run_codex_draft_course_mission(
                run_root,
                command=command,
                decision_source=source,
                variant=variant,
            )
        )
    return tuple(outcomes)


def _persona(variant: CodexMissionVariant) -> str:
    if variant is CodexMissionVariant.DIRECT:
        return "A careful terminal beginner who chooses the shortest visible route."
    return (
        "A terminal beginner who makes one plausible recoverable mistake when a visible "
        "choice is unclear, then reads the feedback and corrects course."
    )


def _route_matches(output: str, variant: CodexMissionVariant) -> bool:
    recovery_feedback = "Choose a number, or q to quit." in output
    return recovery_feedback if variant is CodexMissionVariant.ERROR_PRONE else not recovery_feedback


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run opt-in Codex terminal dogfood")
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--openlearn", default=".venv/bin/openlearn")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--model")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    outcomes = run_live_codex_missions(
        args.output_root,
        command=(args.openlearn, "menu"),
        codex_home=args.codex_home,
        codex_executable=args.codex,
        model=args.model,
    )
    for outcome in outcomes:
        print(
            f"{outcome.run_root.name}: status={outcome.result.status} "
            f"achieved={str(outcome.achieved).lower()} evidence={outcome.evidence}"
        )
    return 0 if all(outcome.achieved for outcome in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
