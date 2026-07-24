from __future__ import annotations

import os
import unittest

try:
    import pytest
except ImportError:  # pytest is a dev/slow-lane dep; skip under plain `unittest`
    raise unittest.SkipTest("pytest not installed (slow eval lane only)")

from openlearn import cli
from tests.evals.tutor_behavior import (
    _judge_response,
    calibration_case_prompt,
    load_calibration_cases,
)


pytestmark = pytest.mark.slow


def _independent_judge_model() -> str:
    if os.environ.get("OPENLEARN_MOCK") in {"1", "true", "yes"}:
        pytest.skip("tutor judge calibration is skipped in OPENLEARN_MOCK mode")
    if not cli.configured_openai_api_key():
        pytest.skip("tutor judge calibration requires an OpenAI-compatible API key")
    judge_model = os.environ.get("OPENLEARN_EVAL_JUDGE_MODEL")
    if not judge_model:
        pytest.skip("set OPENLEARN_EVAL_JUDGE_MODEL for tutor judge calibration")
    if judge_model == cli.configured_model():
        pytest.fail("calibration judge model must differ from the tutor model")
    return judge_model


@pytest.mark.parametrize(
    "case",
    load_calibration_cases(),
    ids=lambda case: str(case["name"]),
)
def test_tutor_judge_matches_calibration_fixture(case: dict[str, object]) -> None:
    judge_model = _independent_judge_model()
    try:
        judged = _judge_response(judge_model, calibration_case_prompt(case))
    except cli.OpenLearnError as exc:
        pytest.skip(f"tutor judge calibration provider unavailable: {exc}")

    expected = case["expected"]
    grade = case["grade"]
    if grade == "good":
        assert judged["pass"] is True, judged
    elif grade == "bad":
        assert judged["pass"] is False, judged
    else:
        assert judged["score"] <= expected["max_score"], judged
    assert set(expected["hard_failures"]) <= {
        key for key, failed in judged["hard_failures"].items() if failed
    }
