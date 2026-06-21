from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

try:
    import pytest
except ImportError:  # pytest is a dev/slow-lane dep; skip under plain `unittest`
    raise unittest.SkipTest("pytest not installed (slow eval lane only)")

from openlearn import cli
from openlearn.models import Topic


pytestmark = pytest.mark.slow

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "move_eval_scenarios.json"
MOVE_EVAL_THRESHOLD = 0.75


def load_scenarios() -> list[dict[str, object]]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [item for item in data if isinstance(item, dict)]


def scenario_ids() -> list[str]:
    return [str(item.get("id") or index) for index, item in enumerate(load_scenarios())]


def require_real_model() -> str:
    if os.environ.get("OPENLEARN_MOCK") in {"1", "true", "yes"}:
        pytest.skip("move policy evals are skipped in OPENLEARN_MOCK mode")
    api_key = cli.configured_openai_api_key()
    if not api_key:
        pytest.skip("move policy evals require an OpenAI-compatible API key")
    return api_key


def run_tutor_turn(scenario: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    api_key = require_real_model()
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", api_key)
    cli._CONFIG_CACHE = None
    metadata = scenario.get("metadata")
    if not isinstance(metadata, dict):
        raise AssertionError("scenario metadata must be an object")
    topic = Topic(
        slug=str(metadata.get("slug") or "move-eval"),
        path=tmp_path / "learning-topics" / "move-eval.md",
        metadata=metadata,
        body=str(scenario.get("body") or ""),
    )
    return call_openai_or_skip(
        cli.configured_model(),
        cli.system_prompt(topic),
        str(scenario.get("learner_message") or ""),
    )


def call_openai_or_skip(model: str, system: str, user: str) -> str:
    try:
        return cli.call_openai(model, system, user)
    except cli.OpenLearnError as exc:
        message = str(exc)
        if "OpenAI request failed" in message or "API key is required" in message:
            pytest.skip(f"real model provider unavailable: {message}")
        raise


def judge_response(scenario: dict[str, object], tutor_response: str) -> dict[str, object]:
    rubric = scenario.get("rubric")
    if not isinstance(rubric, list):
        raise AssertionError("scenario rubric must be a list")
    rubric_text = "\n".join(
        f"- {item}" for item in rubric if isinstance(item, str) and item.strip()
    )
    judge_prompt = (
        "Evaluate whether this AI tutor response satisfies the rubric. "
        "Return only JSON with keys pass (boolean), score (0-1), and reason (short string).\n\n"
        f"Scenario: {scenario.get('name')}\n"
        f"Learner message: {scenario.get('learner_message')}\n"
        f"Rubric:\n{rubric_text}\n\n"
        f"Tutor response:\n{tutor_response}"
    )
    raw = call_openai_or_skip(
        cli.configured_model(),
        "You are a strict evaluator for tutoring-policy conformance.",
        judge_prompt,
    )
    judged = cli.parse_metadata_update(raw)
    if not isinstance(judged, dict):
        raise AssertionError(f"move-eval judge did not return JSON: {raw}")
    return judged


@pytest.mark.parametrize("scenario", load_scenarios(), ids=scenario_ids())
def test_move_policy_scenario(
    scenario: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tutor_response = run_tutor_turn(scenario, tmp_path, monkeypatch)
    judged = judge_response(scenario, tutor_response)
    score = judged.get("score")
    passed = judged.get("pass")
    assert isinstance(score, (int, float)), f"move-eval judge omitted score: {judged}"
    assert passed is True and float(score) >= MOVE_EVAL_THRESHOLD, (
        f"Scenario {scenario.get('id')} failed with score {score}: "
        f"{judged.get('reason')}\nTutor response:\n{tutor_response}"
    )
