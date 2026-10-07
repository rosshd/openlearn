"""Provider-mocked review-evaluation harness tests; no slow/live calls."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

import pytest

from openlearn import cli, config
from tests.evals import review_cards as evaluation

GOOD_CARDS = {
    "noise-outlier-overlap": {
        "question": "Does noise imply an outlier, or an outlier imply noise?",
        "answer": "Neither implies the other. Small measurement errors may lie in the usual range. An extreme observation may be valid rather than noise.",
        "explanation": "A large erroneous measurement can be both noisy and an outlier, so overlap does not make the categories identical.",
    },
    "precision-bias-numbers": {
        "question": "For the Synthetic sensor experiment, which sensor is more precise and what is each sensor's bias?",
        "answer": "Sensor A is more precise because its standard deviation is 0.2 ms rather than 2.0 ms. Its bias is +4.0 ms against the 100.0 ms reference. Sensor B is less precise but has 0.0 ms bias for these stated means.",
        "explanation": "Stable repeated measurements can still be systematically offset from the reference.",
    },
    "normalization-context": {
        "question": "How does this min-max transform work, and what limits its range and fitting assumptions?",
        "answer": "Use x' = (x - min_train) / (max_train - min_train) for a nonconstant training range. Its training extrema map to 0 and 1. Fit on training data and reuse on held-out data; outside-range values may fall outside [0, 1]. A constant range needs another policy.",
        "explanation": "Fitting extrema with held-out observations leaks evaluation information into preprocessing.",
    },
    "classifier-precision-recall": {
        "question": "With TP=30, FP=10, FN=20, how do classifier precision and recall differ in this screening example?",
        "answer": "Precision is TP/(TP+FP), the true-positive fraction of positive predictions: 30/40 = 75%. Recall is TP/(TP+FN), the detected fraction of actual positives: 30/50 = 60%. These are binary classification metrics in this disease-screening example.",
        "explanation": "A false negative is a missed disease case, so recall is sensitive to missed detections.",
    },
    "bfs-shortest-path": {
        "question": "Why does BFS find shortest unweighted paths, and why can unequal weights break its cost guarantee?",
        "answer": "A FIFO queue explores layers by edge count, giving a minimum-edge path. This minimizes cost in unweighted or equal-edge-cost graphs. With unequal weights, a path with fewer edges can cost more.",
        "explanation": "One edge costing 10 can be worse than two edges costing 1 each, for a total of 2.",
    },
    "idempotency-retry": {
        "question": "What happens on identical same-key retries, and on reusing that key with different input after a timeout?",
        "answer": "The same key and identical canonical request replay the original committed receipt without duplicate effects. Different input under that key conflicts. A timeout does not prove the operation was uncommitted; an identical retry can recover its receipt. A new logical operation needs a new key.",
        "explanation": "Atomic storage binds the request hash and receipt to the operation, so receipt recovery cannot become another logical operation.",
    },
}


def judgment(failed=()):
    return {"criteria": {key: key not in failed for key in evaluation.CRITERIA},
            "scores": {key: 0.3 if key in failed else 0.9 for key in evaluation.CRITERIA},
            "reason": "The requested reason is absent." if failed else "The answer addresses the question with supported context."}


class MockProvider:
    def __init__(self, *, pass_bad_calibrations=False, judge_error=None, generation_errors=None,
                 wrong_refusal=False, unsupported_generation=False, quality_failure=False):
        self.fixtures = evaluation.load_fixtures()
        self.records = []
        self.pass_bad_calibrations = pass_bad_calibrations
        self.judge_error = judge_error
        self.generation_errors = generation_errors or {}
        self.wrong_refusal = wrong_refusal
        self.unsupported_generation = unsupported_generation
        self.quality_failure = quality_failure

    def __call__(self, model, system, user, **kwargs):
        assert kwargs["max_attempts"] == 1
        payload = json.loads(user)
        self.records.append({"model": model, "system": system, "payload": payload})
        if model == "judge-model":
            assert "expected_pass" not in payload
            assert "expected_failed_criteria" not in payload
            assert "calibration_cards" not in payload
            if isinstance(self.judge_error, Exception):
                raise self.judge_error
            if self.judge_error:
                return self.judge_error
            card = payload["card"]
            calibration_cards = self.fixtures["calibration_cards"]
            # Independent mocked observations, not fixture expected labels.
            failed = []
            if not self.pass_bad_calibrations and card["answer"] == calibration_cards[1]["card"]["answer"]:
                failed = ["answer_completeness"]
            if not self.pass_bad_calibrations and card["answer"] == calibration_cards[2]["card"]["answer"]:
                failed = ["source_fidelity"]
            if self.quality_failure and card["question"] == GOOD_CARDS["noise-outlier-overlap"]["question"]:
                failed = ["question_focus"]
            return json.dumps(judgment(failed))
        assert model == "generator-model"
        case = next(case for case in self.fixtures["cases"] if case["concept"] == payload["concept"])
        error = self.generation_errors.get(case["id"])
        if isinstance(error, Exception):
            raise error
        if error:
            return error
        if case["expected_insufficient_source"] and not self.unsupported_generation:
            return '{"insufficient_source": true}'
        if self.wrong_refusal and case["id"] == "data-quality-desirability":
            return '{"insufficient_source": true}'
        if case["id"] == "data-quality-desirability":
            card = deepcopy(self.fixtures["calibration_cards"][0]["card"])
        else:
            card = deepcopy(GOOD_CARDS.get(case["id"], GOOD_CARDS["normalization-context"]))
        card["sources"] = [{"label": case["sources"][0]["label"], "excerpt": case["sources"][0]["text"]}]
        return json.dumps(card)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    learner_home = tmp_path / "learner-home"
    learner_home.mkdir()
    topics = learner_home / "learning-topics"
    topics.mkdir()
    (topics / "private.md").write_text("Private learner material must never enter the evaluator.")
    monkeypatch.setenv("OPENLEARN_HOME", str(learner_home))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    cli.clear_config_cache()
    yield tmp_path, learner_home
    cli.clear_config_cache()


def run_mock(tmp_path, provider, **kwargs):
    return evaluation.run_evaluation(tmp_path / "run", generator_model="generator-model",
                                     judge_model="judge-model", provider=provider, **kwargs)


def test_versioned_topics_and_calibration_cover_the_required_distinctions():
    fixtures = evaluation.load_fixtures()
    assert len(fixtures["cases"]) == 8
    assert sum(case["expected_insufficient_source"] for case in fixtures["cases"]) == 1
    assert {case["id"] for case in fixtures["cases"]} == {
        "data-quality-desirability", "noise-outlier-overlap", "precision-bias-numbers",
        "normalization-context", "classifier-precision-recall", "bfs-shortest-path",
        "idempotency-retry", "insufficient-normalization",
    }
    assert len(fixtures["calibration_cards"]) == 3
    for case in fixtures["cases"]:
        assert case["retrieval_goal"] and case["sources"] and case["quality_rubric"]
    assert "ACTUAL" in evaluation.JUDGE_SYSTEM
    assert "hidden sources" in evaluation.JUDGE_SYSTEM
    assert "fail question_focus" in evaluation.JUDGE_SYSTEM


def test_mocked_run_calibrates_and_binds_truthful_native_deck(isolated):
    tmp_path, learner_home = isolated
    before = {path.relative_to(learner_home): path.read_bytes() for path in learner_home.rglob("*") if path.is_file()}
    provider = MockProvider()
    report = run_mock(tmp_path, provider)
    assert report["mode"] == "mock"
    assert report["passed"] and report["calibration_passed"] and report["evidence_complete"]
    assert report["actual_calls"] == len(provider.records) == 18
    assert report["fixture_sha256"] == sha256(evaluation.FIXTURE_PATH.read_bytes()).hexdigest()
    assert len(report["source_binding"]["head"]) == 40
    assert len(report["rubric_sha256"]) == 64
    assert report["source_binding"]["generator_source_sha256"] == sha256(Path(evaluation.review_cards.__file__).read_bytes()).hexdigest()
    assert report["cases"][-1]["status"] == "expected_refusal"
    deck_path = tmp_path / "run/home/learning-topics/review-evaluation.md"
    metadata, body = cli.parse_topic(deck_path.read_text())
    assert "Synthetic" in body
    actual_cards = [case["generated_card"] for case in report["cases"] if case["generated_card"]]
    assert [item["review_card"] for item in metadata["review_due"]] == actual_cards
    assert len(actual_cards) == 7
    assert len({card["card_id"] for card in actual_cards}) == 7
    assert all(card["prepared_at"] >= report["started_at"] for card in actual_cards)
    assert json.loads((tmp_path / "run/report.json").read_text()) == report
    assert "Mode: mock" in (tmp_path / "run/summary.md").read_text()
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "run").stat().st_mode) == 0o700
        assert stat.S_IMODE(deck_path.stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "run/report.json").stat().st_mode) == 0o600
    after = {path.relative_to(learner_home): path.read_bytes() for path in learner_home.rglob("*") if path.is_file()}
    assert before == after
    assert os.environ["OPENLEARN_HOME"] == str(learner_home)
    assert os.environ["OPENLEARN_MOCK"] == "1"
    assert not (tmp_path / "run/home/config.json").exists()


def test_bad_calibrations_are_actually_judged_and_taint_otherwise_passing_run(isolated):
    tmp_path, _ = isolated
    report = run_mock(tmp_path, MockProvider(pass_bad_calibrations=True))
    assert not report["passed"] and not report["calibration_passed"]
    assert report["evidence_complete"]
    assert [row["status"] for row in report["calibration"]] == ["matched", "mismatch", "mismatch"]
    assert all(case["passed"] for case in report["cases"])
    assert report["calibration"][1]["judgment"]["passed"] is True
    assert report["calibration"][1]["expected_pass"] is False


@pytest.mark.parametrize("judge_error", ["not JSON", json.dumps({"criteria": {}, "scores": {}, "reason": "bad"}), cli.OpenLearnError("secret-provider-token")])
def test_malformed_or_failed_judge_never_yields_green_and_keeps_actual_generated_cards(isolated, judge_error):
    tmp_path, _ = isolated
    report = run_mock(tmp_path, MockProvider(judge_error=judge_error))
    assert not report["passed"] and not report["calibration_passed"] and not report["evidence_complete"]
    assert all(row["status"] == "judge_error" for row in report["calibration"])
    assert report["cases"][0]["status"] == "judge_error"
    metadata, _ = cli.parse_topic((tmp_path / "run/home/learning-topics/review-evaluation.md").read_text())
    assert len(metadata["review_due"]) == 7
    assert "secret-provider-token" not in (tmp_path / "run/report.json").read_text()


def test_partial_generation_failures_and_genuine_expected_refusal_are_distinct(isolated):
    tmp_path, _ = isolated
    report = run_mock(tmp_path, MockProvider(generation_errors={
        "normalization-context": cli.OpenLearnError("private transport error"),
        "insufficient-normalization": "not JSON",
    }))
    by_id = {case["id"]: case for case in report["cases"]}
    assert by_id["normalization-context"]["status"] == "generation_error"
    assert by_id["insufficient-normalization"]["status"] == "generation_error"
    assert not by_id["insufficient-normalization"]["passed"]
    assert not report["passed"]
    metadata, _ = cli.parse_topic((tmp_path / "run/home/learning-topics/review-evaluation.md").read_text())
    assert len(metadata["review_due"]) == 6
    assert "private transport error" not in (tmp_path / "run/report.json").read_text()


def test_unexpected_refusal_and_generated_insufficient_case_fail_honestly(isolated):
    tmp_path, _ = isolated
    report = run_mock(tmp_path, MockProvider(wrong_refusal=True, unsupported_generation=True))
    assert report["cases"][0]["status"] == "unexpected_refusal"
    assert report["cases"][-1]["status"] == "unexpected_generation"
    assert not report["passed"]
    metadata, _ = cli.parse_topic((tmp_path / "run/home/learning-topics/review-evaluation.md").read_text())
    assert len(metadata["review_due"]) == 7
    assert metadata["review_due"][-1]["review_card"] == report["cases"][-1]["generated_card"]


def test_quality_failures_remain_in_native_deck(isolated):
    tmp_path, _ = isolated
    report = run_mock(tmp_path, MockProvider(quality_failure=True))
    assert report["calibration_passed"]
    assert not report["passed"]
    assert report["cases"][1]["status"] == "quality_fail"
    metadata, _ = cli.parse_topic((tmp_path / "run/home/learning-topics/review-evaluation.md").read_text())
    assert metadata["review_due"][1]["review_card"] == report["cases"][1]["generated_card"]


def test_call_ceiling_records_incomplete_cases_without_sending_extra_requests(isolated):
    tmp_path, _ = isolated
    provider = MockProvider()
    report = run_mock(tmp_path, provider, max_calls=4)
    assert report["actual_calls"] == len(provider.records) == 4
    assert report["calibration_passed"]
    assert not report["passed"] and not report["evidence_complete"]
    assert report["cases"][0]["generated_card"]
    assert all(case["status"] == "budget_exhausted" for case in report["cases"])
    metadata, _ = cli.parse_topic((tmp_path / "run/home/learning-topics/review-evaluation.md").read_text())
    assert len(metadata["review_due"]) == 1


@pytest.mark.parametrize("max_calls", [0, 25, True])
def test_invalid_call_limits_fail_before_provider_or_artifact_mutation(isolated, max_calls):
    tmp_path, _ = isolated
    provider = MockProvider()
    with pytest.raises(ValueError):
        run_mock(tmp_path, provider, max_calls=max_calls)
    assert not provider.records and not (tmp_path / "run").exists()


def test_selected_case_repair_keeps_blinded_calibration_and_uses_five_calls(isolated):
    tmp_path, _ = isolated
    provider = MockProvider()
    report = run_mock(tmp_path, provider, max_calls=6, case_ids=["noise-outlier-overlap"])
    assert report["actual_calls"] == 5
    assert report["selected_case_ids"] == ["noise-outlier-overlap"]
    assert report["passed"]
    assert len(report["calibration"]) == 3 and len(report["cases"]) == 1


def test_private_roots_cannot_be_reused_or_overlap_learner_home(isolated):
    tmp_path, learner_home = isolated
    provider = MockProvider()
    existing = tmp_path / "run"
    existing.mkdir()
    with pytest.raises(ValueError, match="must be new"):
        run_mock(tmp_path, provider)
    for forbidden in (learner_home / "nested", learner_home, tmp_path):
        with pytest.raises(ValueError):
            evaluation.run_evaluation(forbidden, generator_model="generator-model",
                                      judge_model="judge-model", provider=provider)
    assert not provider.records
    (tmp_path / "dangling").symlink_to(tmp_path / "not-created")
    with pytest.raises(ValueError):
        evaluation.run_evaluation(tmp_path / "dangling", generator_model="generator-model",
                                  judge_model="judge-model", provider=provider)
    assert not (tmp_path / "not-created").exists()


def test_cli_is_opt_in_and_live_cannot_use_mock_or_same_model(isolated, capsys):
    tmp_path, _ = isolated
    assert evaluation.main([str(tmp_path / "run"), "--judge-model", "judge-model"]) == 2
    assert "requires --live" in capsys.readouterr().out
    assert not (tmp_path / "run").exists()
    with pytest.raises(ValueError, match="mock provider"):
        evaluation.run_evaluation(tmp_path / "run", live=True, judge_model="judge-model")
    with pytest.raises(ValueError, match="distinct"):
        evaluation.run_evaluation(tmp_path / "run", generator_model="generator-model",
                                  judge_model="GENERATOR-MODEL", provider=MockProvider())
    with pytest.raises(ValueError, match="opt-in"):
        evaluation.run_evaluation(tmp_path / "run", generator_model="generator-model", judge_model="judge-model")


def test_fixture_hash_changes_with_authoritative_source_revision(isolated):
    tmp_path, _ = isolated
    fixtures = evaluation.load_fixtures()
    fixtures["cases"][0]["sources"][0]["text"] += " This synthetic amendment is intentionally versioned by its content hash."
    path = tmp_path / "revised.json"
    path.write_text(json.dumps(fixtures))
    report = run_mock(tmp_path, MockProvider(), fixture_path=path)
    assert report["fixture_sha256"] == sha256(path.read_bytes()).hexdigest()
    assert report["fixture_sha256"] != sha256(evaluation.FIXTURE_PATH.read_bytes()).hexdigest()


def test_configured_secrets_are_never_copied_into_reports_or_exception_text(isolated):
    tmp_path, _ = isolated
    secret = "fixture-private-provider-credential"
    provider = MockProvider(judge_error=cli.OpenLearnError("Provider said " + secret))
    report = run_mock(tmp_path, provider, credentials=config.ProviderCredentials("https://example.invalid", "generator-model", secret))
    assert not report["passed"]
    assert secret not in (tmp_path / "run/report.json").read_text()
    assert secret not in (tmp_path / "run/summary.md").read_text()
    assert not (tmp_path / "run/home/config.json").exists()


@pytest.mark.parametrize("change", ["boolean-score", "numeric-criterion", "missing", "extra", "nan", "out-of-range", "blank-reason"])
def test_judge_schema_is_strict_and_does_not_accept_false_green_defaults(change):
    value = judgment()
    if change == "boolean-score":
        value["scores"]["source_fidelity"] = True
    elif change == "numeric-criterion":
        value["criteria"]["answer_completeness"] = 1
    elif change == "missing":
        value["criteria"].pop("question_focus")
    elif change == "extra":
        value["scores"]["invented_dimension"] = 0.9
    elif change == "nan":
        value["scores"]["source_fidelity"] = float("nan")
    elif change == "out-of-range":
        value["scores"]["source_fidelity"] = 1.1
    else:
        value["reason"] = "  "
    with pytest.raises(ValueError):
        evaluation.parse_judgment(json.dumps(value))


def test_low_score_cannot_be_offset_by_true_booleans_or_other_dimensions():
    value = judgment()
    value["scores"]["answer_completeness"] = 0.1
    assert evaluation.parse_judgment(json.dumps(value))["passed"] is False
