"""Bounded lifecycle for route-free terminal exploration."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from tests.dogfood.artifacts import EvidenceBundle
from tests.dogfood.codex_driver import (
    MAX_PRIOR_ACTIONS,
    CodexDecision,
    CodexDecisionError,
    DecisionContext,
    DecisionSource,
)
from tests.dogfood.pty_runner import PtyMissionRunner


@dataclass(frozen=True)
class ExplorerLimits:
    """All independent bounds enforced by one explorer run."""

    max_turns: int
    max_elapsed_seconds: float
    observation_chars: int
    quiet_interval: float
    observation_timeout: float

    def __post_init__(self) -> None:
        if self.max_turns <= 0:
            raise ValueError("max_turns must be positive")
        if (
            self.max_elapsed_seconds <= 0
            or self.observation_chars <= 0
            or self.quiet_interval <= 0
            or self.observation_timeout <= 0
        ):
            raise ValueError("explorer limits must be positive")


@dataclass(frozen=True)
class ExplorerResult:
    """Public terminal state produced by a finalized explorer run."""

    status: str
    achieved: bool
    summary: str
    turns: int
    elapsed_seconds: float


class Explorer:
    """Ask for one decision at a time and apply it only to the public PTY."""

    def __init__(
        self,
        *,
        runner: PtyMissionRunner,
        bundle: EvidenceBundle,
        decision_source: DecisionSource,
        persona: str,
        goal: str,
        outcome_check: Callable[[str], bool],
        limits: ExplorerLimits,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        source_kind = decision_source.source_kind
        if source_kind not in {"fake", "codex"}:
            raise ValueError("decision source must identify source_kind as fake or codex")
        self.runner = runner
        self._bundle = bundle
        self._decision_source = decision_source
        self._source_kind = source_kind
        self._persona = persona
        self._goal = goal
        self._outcome_check = outcome_check
        self._limits = limits
        self._clock = clock
        self._started_at: float | None = None
        self._turns = 0
        self._history = ""
        self._observation_chars_seen = 0
        self._prior_actions: list[str] = []
        self._finalized = False

    def run(self) -> ExplorerResult:
        """Run until one explicit terminal state and finalize evidence exactly once."""
        self._started_at = self._clock()
        try:
            self.runner.start()
            while True:
                if self._elapsed() >= self._limits.max_elapsed_seconds:
                    return self._fail("elapsed_exhausted", "Explorer elapsed-time budget exhausted.")
                observation = self.runner.observe(
                    quiet_interval=self._limits.quiet_interval,
                    timeout=min(
                        self._limits.observation_timeout,
                        max(0.000001, self._seconds_remaining()),
                    ),
                )
                self._observation_chars_seen += len(observation.text)
                self._history = (self._history + observation.text)[
                    -self._limits.observation_chars :
                ]
                if observation.has_unsupported_controls:
                    return self._fail(
                        "unsupported_terminal_controls",
                        "PTY output used controls unsupported by line-oriented observations.",
                    )
                if self._outcome_check(self._history):
                    return self._complete_achieved()
                if observation.settled_by == "eof":
                    return self._fail("pty_eof", "PTY exited before the learner goal was achieved.")
                if self._elapsed() >= self._limits.max_elapsed_seconds:
                    return self._fail("elapsed_exhausted", "Explorer elapsed-time budget exhausted.")
                if self._turns >= self._limits.max_turns:
                    return self._fail("turn_exhausted", "Explorer turn budget exhausted.")

                bounded, truncated, original_chars = self._bounded_observation()
                turns_remaining = self._limits.max_turns - self._turns
                seconds_remaining = self._seconds_remaining()
                context = DecisionContext(
                    persona=self._persona,
                    goal=self._goal,
                    observation=bounded,
                    prior_actions=tuple(self._prior_actions[-MAX_PRIOR_ACTIONS:]),
                    turns_remaining=turns_remaining,
                    seconds_remaining=seconds_remaining,
                )
                decision = self._decision_source.decide(context)
                self._turns += 1
                self._bundle.record_decision(
                    observation_id=f"observation-{self._turns:04d}",
                    observation=bounded,
                    observation_truncated=truncated,
                    observation_original_chars=original_chars,
                    decision=decision,
                    source_kind=self._source_kind,
                    turns_remaining=turns_remaining,
                    seconds_remaining=seconds_remaining,
                    prior_actions=context.prior_actions,
                    elapsed_seconds=self._elapsed(),
                    provenance=self._decision_provenance(),
                )
                if self._elapsed() >= self._limits.max_elapsed_seconds:
                    return self._fail(
                        "elapsed_exhausted",
                        "Explorer elapsed-time budget exhausted.",
                    )
                if decision.action == "stop":
                    assert decision.reason is not None
                    return self._fail("stopped", f"Explorer stopped: {decision.reason}")
                self._dispatch(decision)
                self._prior_actions.append(_summarize_action(decision))
        except KeyboardInterrupt:
            self._finalize_failure("KeyboardInterrupt: explorer interrupted.")
            raise
        except (Exception, SystemExit) as error:
            if isinstance(error, CodexDecisionError):
                summary = f"CodexDecisionError: {error}"
            else:
                summary = f"{type(error).__name__}: explorer failed."
            self._finalize_failure(summary)
            if isinstance(error, SystemExit):
                raise
            return ExplorerResult(
                status="decision_failed",
                achieved=False,
                summary=summary,
                turns=self._turns,
                elapsed_seconds=round(self._elapsed(), 6),
            )
        finally:
            self.runner.close()

    def _dispatch(self, decision: CodexDecision) -> None:
        if decision.action == "submit_text":
            assert decision.text is not None
            self.runner.sendline(decision.text)
            return
        assert decision.action == "press_key" and decision.key is not None
        if decision.key == "enter":
            self.runner.sendline()
            return
        keys = {
            "escape": "\x1b",
            "backspace": "\x7f",
            "up": "\x1b[A",
            "down": "\x1b[B",
            "left": "\x1b[D",
            "right": "\x1b[C",
            "ctrl_c": "\x03",
        }
        self.runner.send(keys[decision.key])

    def _bounded_observation(self) -> tuple[str, bool, int]:
        original_chars = self._observation_chars_seen
        truncated = original_chars > self._limits.observation_chars
        return (
            self._history,
            truncated,
            original_chars,
        )

    def _complete_achieved(self) -> ExplorerResult:
        result = self.runner.terminate()
        summary = "Learner-visible goal achieved."
        self._bundle.capture_final_state()
        self._bundle.complete(result, achieved=True, summary=summary)
        self._finalized = True
        return ExplorerResult(
            status="achieved",
            achieved=True,
            summary=summary,
            turns=self._turns,
            elapsed_seconds=round(self._elapsed(), 6),
        )

    def _fail(self, status: str, summary: str) -> ExplorerResult:
        self._finalize_failure(summary)
        return ExplorerResult(
            status=status,
            achieved=False,
            summary=summary,
            turns=self._turns,
            elapsed_seconds=round(self._elapsed(), 6),
        )

    def _finalize_failure(self, summary: str) -> None:
        if self._finalized:
            return
        self.runner.close()
        self._bundle.capture_final_state()
        self._bundle.fail(summary)
        self._finalized = True

    def _decision_provenance(self) -> Mapping[str, object] | None:
        provenance = self._decision_source.last_provenance
        return provenance if isinstance(provenance, Mapping) else None

    def _elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, self._clock() - self._started_at)

    def _seconds_remaining(self) -> float:
        return max(0.0, self._limits.max_elapsed_seconds - self._elapsed())


def _summarize_action(decision: CodexDecision) -> str:
    if decision.action == "submit_text":
        assert decision.text is not None
        return f"submit_text:{decision.text}"
    if decision.action == "press_key":
        assert decision.key is not None
        return f"press_key:{decision.key}"
    assert decision.reason is not None
    return f"stop:{decision.reason}"
