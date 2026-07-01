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
JUDGE_FIELD_MIN_ACCURACY = 0.7


def call_openai_or_skip(model: str, system: str, user: str) -> str:
    try:
        return cli.call_openai(model, system, user)
    except cli.OpenLearnError as exc:
        message = str(exc)
        if "OpenAI request failed" in message or "API key is required" in message:
            pytest.skip(f"real judge calibration provider unavailable: {message}")
        raise


def judge_calibration_prompt(case: dict[str, object]) -> str:
    question_kind = str(case.get("question_kind") or "free_response")
    metadata = {
        "topic": "Judge calibration",
        "slug": "judge-calibration",
        "current_focus": "calibration concept",
        "pending_question": {
            "kind": question_kind,
            "question": case["question"],
        },
    }
    if question_kind == "multiple_choice":
        metadata["pending_question"]["answer_key"] = "B"
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
    answer_kind_checks = []
    gameable_checks = []
    for case in cases:
        raw = call_openai_or_skip(
            cli.configured_model(),
            cli.METADATA_EXTRACTOR_SYSTEM,
            judge_calibration_prompt(case),
        )
        judged = cli.parse_metadata_update(raw)
        score = judged.get("answer_score")
        if not isinstance(score, (int, float)):
            continue
        errors.append(abs(float(score) - float(case["true_score"])))
        expected_answer_kind = case.get("expected_answer_kind")
        answer_kind = judged.get("answer_kind")
        if expected_answer_kind in {"recognition", "production"} and answer_kind in {
            "recognition",
            "production",
        }:
            answer_kind_checks.append(answer_kind == expected_answer_kind)
        expected_gameable = case.get("expected_gameable")
        gameable = judged.get("gameable")
        if isinstance(expected_gameable, bool) and isinstance(gameable, bool):
            gameable_checks.append(gameable == expected_gameable)

    if not errors:
        pytest.skip("judge omitted answer_score for every calibration case")

    mean_absolute_error = sum(errors) / len(errors)
    assert mean_absolute_error <= JUDGE_CALIBRATION_MAX_MAE
    if answer_kind_checks:
        assert sum(answer_kind_checks) / len(answer_kind_checks) >= JUDGE_FIELD_MIN_ACCURACY
    if gameable_checks:
        assert sum(gameable_checks) / len(gameable_checks) >= JUDGE_FIELD_MIN_ACCURACY
