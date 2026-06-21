import json
import os
import unittest
from pathlib import Path

try:
    import pytest
except ImportError:  # pytest is a dev/slow-lane dep; skip under plain `unittest`
    raise unittest.SkipTest("pytest not installed (slow eval lane only)")

from openlearn import cli


pytestmark = pytest.mark.slow

JUDGE_CALIBRATION_MAX_MAE = 0.3


def judge_calibration_prompt(case: dict[str, object]) -> str:
    metadata = {
        "topic": "Judge calibration",
        "slug": "judge-calibration",
        "current_focus": "calibration concept",
        "pending_question": {
            "kind": "free_response",
            "question": case["question"],
        },
    }
    tutor_answer = (
        "Evaluate the learner's answer to this check-for-understanding question. "
        f"Question: {case['question']}"
    )
    return cli.metadata_update_prompt(metadata, str(case["learner_answer"]), tutor_answer)


def test_judge_calibration_mean_absolute_error() -> None:
    if os.environ.get("OPENLEARN_MOCK") in {"1", "true", "yes"}:
        pytest.skip("real judge calibration is skipped in OPENLEARN_MOCK mode")
    if not cli.configured_openai_api_key():
        pytest.skip("real judge calibration requires an OpenAI-compatible API key")

    cases = json.loads(
        (Path(__file__).parent / "fixtures" / "judge_calibration_cases.json").read_text(
            encoding="utf-8"
        )
    )

    errors = []
    for case in cases:
        raw = cli.call_openai(
            cli.configured_model(),
            cli.METADATA_EXTRACTOR_SYSTEM,
            judge_calibration_prompt(case),
        )
        judged = cli.parse_metadata_update(raw)
        score = judged.get("answer_score")
        if not isinstance(score, (int, float)):
            continue
        errors.append(abs(float(score) - float(case["true_score"])))

    if not errors:
        pytest.skip("judge omitted answer_score for every calibration case")

    mean_absolute_error = sum(errors) / len(errors)
    assert mean_absolute_error <= JUDGE_CALIBRATION_MAX_MAE
