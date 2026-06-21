import json
import unittest
from pathlib import Path

try:
    import pytest
except ImportError:  # pytest is a dev/slow-lane dep; skip under plain `unittest`
    raise unittest.SkipTest("pytest not installed (slow eval lane only)")

from openlearn import cli


pytestmark = pytest.mark.slow


def _fixture_judge_score(question: str, learner_answer: str) -> float:
    answer = learner_answer.lower()
    if "normal" in answer or "current line" in answer or "searches forward" in answer:
        return 1.0
    if "binds a name" in answer or "repeat a block" in answer or "stops the recursive" in answer:
        return 1.0
    if "character" in answer:
        return 0.4
    if "permanently stores" in answer or "base case" in question.lower() and "calls itself" in answer:
        return 0.3
    if "run faster" in answer:
        return 0.2
    return 0.0


def test_judge_calibration_mean_absolute_error(monkeypatch) -> None:
    cases = json.loads(
        (Path(__file__).parent / "fixtures" / "judge_calibration_cases.json").read_text(
            encoding="utf-8"
        )
    )

    def fake_call_openai(_model: str, _system: str, user: str) -> str:
        payload = json.loads(user)
        score = _fixture_judge_score(payload["question"], payload["learner_answer"])
        status = "correct" if score >= 0.8 else "partial" if score >= 0.3 else "needs_work"
        return json.dumps({"last_answer_status": status, "answer_score": score})

    monkeypatch.setattr(cli, "call_openai", fake_call_openai)
    errors = []
    for case in cases:
        raw = cli.call_openai("eval-judge", cli.METADATA_EXTRACTOR_SYSTEM, json.dumps(case))
        judged = cli.parse_metadata_update(raw)
        errors.append(abs(float(judged["answer_score"]) - float(case["true_score"])))

    mean_absolute_error = sum(errors) / len(errors)
    assert mean_absolute_error <= 0.15
