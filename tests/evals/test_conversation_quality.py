from __future__ import annotations

import json
import sys
import unittest
from argparse import Namespace
from pathlib import Path

try:
    import pytest
except ImportError:  # pytest is a dev/slow-lane dep; skip under plain `unittest`
    raise unittest.SkipTest("pytest not installed (slow eval lane only)")

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytestmark = pytest.mark.slow


def _scenario_ids():
    return [path.stem for path in sorted(SCENARIOS_DIR.glob("*.json"))]


def _scenario_paths():
    return sorted(SCENARIOS_DIR.glob("*.json"))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    from openlearn import cli

    cli._CONFIG_CACHE = None
    yield
    cli._CONFIG_CACHE = None


@pytest.mark.parametrize("scenario_path", _scenario_paths(), ids=_scenario_ids())
def test_conversation_quality(scenario_path):
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCase
    from openlearn import cli

    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    topic_slug = scenario["topic"]
    goal = scenario["goal"]
    cli.cmd_new(Namespace(topic=topic_slug, goal=goal, template=None))

    output: list[str] = []
    last_user_msg = ""
    for turn in scenario["turns"]:
        if turn["role"] == "user":
            last_user_msg = turn["content"]
        elif turn["role"] == "assistant":
            if turn["content"] is not None:
                output.append(str(turn["content"]))
            else:
                cli.ask_topic(
                    topic_slug,
                    last_user_msg,
                    None,
                    output_func=output.append,
                )

    tutor_response = "\n".join(output)
    rubric_text = "\n".join(f"- {item}" for item in scenario["rubric"])

    metric = GEval(
        name=scenario["name"],
        criteria=(f"Evaluate this AI tutor response against these criteria:\n{rubric_text}"),
        evaluation_params=["actual_output"],
        threshold=0.7,
    )
    test_case = LLMTestCase(
        input=last_user_msg,
        actual_output=tutor_response,
    )
    metric.measure(test_case)
    assert metric.score >= 0.7, (
        f"Scenario '{scenario['name']}' failed (score {metric.score:.2f}): {metric.reason}"
    )
