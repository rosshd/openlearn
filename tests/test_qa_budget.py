from __future__ import annotations

import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from urllib.error import URLError

import pytest

from openlearn import cli, config, lesson_policy, providers, qa_budget, tutor_service
from openlearn.config import ProviderCredentials


MODEL = "deepseek/deepseek-v4.1-flash"
BASE_URL = "https://openrouter.ai/api/v1"


@pytest.fixture
def budget_env(tmp_path, monkeypatch):
    pricing = {
        "base_url": BASE_URL,
        "model": MODEL,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "pricing_source": "https://openrouter.ai/api/v1/models",
        "bounds_source": "https://openrouter.ai/docs/api/reference/parameters",
        "input_usd_per_million": "0.3",
        "output_usd_per_million": "1.2",
        "request_usd": "0",
        "context_tokens": 131072,
        "max_output_tokens": 1600,
        "input_framing_tokens": 256,
        "input_tokens_per_utf8_byte": 1,
        "max_tokens_includes_reasoning": True,
        "reasoning_disabled_supported": True,
    }
    pricing_path = tmp_path / "operator-pricing.json"
    pricing_path.write_text(json.dumps(pricing), encoding="utf-8")
    ledger_path = tmp_path / "batch-ledger.json"
    monkeypatch.setenv("OPENLEARN_QA_BUDGET", "1")
    monkeypatch.setenv("OPENLEARN_QA_PRICING", str(pricing_path))
    monkeypatch.setenv("OPENLEARN_QA_LEDGER", str(ledger_path))
    monkeypatch.setenv("OPENLEARN_QA_MAX_CALLS", "12")
    monkeypatch.setenv("OPENLEARN_QA_MAX_USD", "0.05")
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path / "synthetic-home"))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    monkeypatch.setattr(cli, "_DRY_RUN", False)
    monkeypatch.setattr(cli, "configured_base_url", lambda: BASE_URL)
    monkeypatch.setattr(cli, "configured_openai_api_key", lambda: "synthetic-test-key")
    return pricing_path, ledger_path, pricing


def guard():
    return qa_budget.from_environment(lock=cli.file_lock, write=cli.write_text_atomic)


def payload(user="synthetic prompt", max_tokens=1600):
    return {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user}],
    }


def ledger(path):
    return json.loads(path.read_text(encoding="utf-8"))


def response(usage=None):
    body = {"choices": [{"message": {"content": "Synthetic reply."}}]}
    if usage is not None:
        body["usage"] = usage
    return BytesIO(json.dumps(body).encode("utf-8"))


USAGE = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}


@pytest.mark.parametrize("name,value", [
    ("OPENLEARN_QA_MAX_CALLS", "13"),
    ("OPENLEARN_QA_MAX_CALLS", "0"),
    ("OPENLEARN_QA_MAX_USD", "0.050001"),
    ("OPENLEARN_QA_MAX_USD", "NaN"),
    ("OPENLEARN_QA_MAX_USD", "-1"),
    ("OPENLEARN_QA_BUDGET", "true"),
    ("OPENLEARN_QA_LEDGER", "relative.json"),
])
def test_invalid_or_expanded_limits_stop_before_network(budget_env, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "urlopen", lambda *_a, **_k: pytest.fail("network opened"))
    with pytest.raises(cli.ProviderRequestError) as error:
        cli.call_openai(MODEL, "system", "user")
    assert error.value.category == "qa_budget_stop"
    assert not budget_env[1].exists()


@pytest.mark.parametrize("key,value", [
    ("model", "unknown-model"),
    ("input_usd_per_million", "NaN"),
    ("output_usd_per_million", "0"),
    ("request_usd", "0.01"),
    ("context_tokens", True),
    ("input_tokens_per_utf8_byte", 2),
    ("max_tokens_includes_reasoning", False),
    ("reasoning_disabled_supported", False),
    ("input_bound_mode", "unknown"),
    ("verified_at", (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()),
])
def test_unknown_pricing_or_bounds_fail_closed(budget_env, key, value):
    path, _, pricing = budget_env
    pricing[key] = value
    path.write_text(json.dumps(pricing), encoding="utf-8")
    with pytest.raises(qa_budget.QABudgetStop):
        guard()


def test_explicit_opt_in_disabled_and_mock_leave_no_ledger(budget_env, monkeypatch):
    monkeypatch.delenv("OPENLEARN_QA_BUDGET")
    monkeypatch.setenv("OPENLEARN_QA_MAX_USD", "invalid")
    assert guard() is None
    monkeypatch.setattr(cli, "urlopen", lambda *_a, **_k: response())
    assert cli.call_openai("normal-model", "system", "user") == "Synthetic reply."
    monkeypatch.setenv("OPENLEARN_QA_BUDGET", "1")
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    cli.call_openai(MODEL, "system", "user")
    assert not budget_env[1].exists()


def test_request_boundary_resume_and_no_sensitive_ledger(budget_env, monkeypatch):
    _, path, _ = budget_env
    monkeypatch.setenv("OPENLEARN_QA_MAX_CALLS", "1")
    first = guard()
    first.reserve(BASE_URL, payload("private-source synthetic-test-key"))
    with pytest.raises(qa_budget.QABudgetStop, match="ceiling"):
        guard().reserve(BASE_URL, payload())
    stored = path.read_text(encoding="utf-8")
    assert "private-source" not in stored
    assert "synthetic-test-key" not in stored
    assert "messages" not in stored
    assert len(ledger(path)["attempts"]) == 1
    monkeypatch.setenv("OPENLEARN_QA_MAX_CALLS", "2")
    with pytest.raises(qa_budget.QABudgetStop, match="does not match"):
        guard().reserve(BASE_URL, payload())
    assert path.read_text(encoding="utf-8") == stored


def test_exact_dollar_boundary_and_settlement(budget_env, monkeypatch):
    _, path, pricing = budget_env
    request = payload()
    input_bound = len(json.dumps(request["messages"], ensure_ascii=False).encode()) + 256
    amount = (Decimal(input_bound) * Decimal("0.3") + 1600 * Decimal("1.2")) / 1_000_000
    monkeypatch.setenv("OPENLEARN_QA_MAX_USD", str(amount))
    budget = guard()
    attempt = budget.reserve(BASE_URL, request)
    with pytest.raises(qa_budget.QABudgetStop):
        budget.reserve(BASE_URL, request)
    budget.settle(attempt, {**USAGE, "completion_tokens_details": {"reasoning_tokens": 5}})
    assert ledger(path)["attempts"][0]["charged_usd"] == "0.000027"
    assert ledger(path)["attempts"][0]["status"] == "confirmed"
    assert pricing["request_usd"] == "0"


@pytest.mark.parametrize("usage", [
    None, {}, {"prompt_tokens": 10, "completion_tokens": 20},
    {**USAGE, "total_tokens": 31}, {**USAGE, "prompt_tokens": True},
    {**USAGE, "completion_tokens_details": {"reasoning_tokens": 21}},
])
def test_unknown_usage_retains_reservation(budget_env, usage):
    budget = guard()
    attempt = budget.reserve(BASE_URL, payload())
    before = ledger(budget_env[1])
    budget.settle(attempt, usage)
    assert ledger(budget_env[1]) == before


def test_bound_overrun_retains_reservation_and_stops(budget_env):
    budget = guard()
    attempt = budget.reserve(BASE_URL, payload())
    before = ledger(budget_env[1])
    with pytest.raises(qa_budget.QABudgetStop, match="exceeds reservation"):
        budget.settle(attempt, {
            "prompt_tokens": 200000, "completion_tokens": 20, "total_tokens": 200020,
        })
    assert ledger(budget_env[1])["attempts"] == before["attempts"]
    assert ledger(budget_env[1])["stopped"] is True
    with pytest.raises(qa_budget.QABudgetStop, match="inconsistent"):
        guard().reserve(BASE_URL, payload())


@pytest.mark.parametrize("base,body", [
    ("https://unknown.example/v1", payload()),
    (BASE_URL, {**payload(), "model": "unknown"}),
    (BASE_URL, payload(max_tokens=1601)),
    (BASE_URL, payload("x" * 132000)),
])
def test_unverified_request_does_not_reserve(budget_env, base, body):
    with pytest.raises(qa_budget.QABudgetStop):
        guard().reserve(base, body)
    assert not budget_env[1].exists()


def reserve_once(_index):
    try:
        guard().reserve(BASE_URL, payload())
        return True
    except qa_budget.QABudgetStop:
        return False


def test_threads_cannot_overreserve(budget_env, monkeypatch):
    monkeypatch.setenv("OPENLEARN_QA_MAX_CALLS", "3")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve_once, range(12)))
    assert sum(results) == 3
    assert len(ledger(budget_env[1])["attempts"]) == 3


def test_processes_share_resume_ledger(budget_env, monkeypatch):
    monkeypatch.setenv("OPENLEARN_QA_MAX_CALLS", "3")
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(reserve_once, range(6)))
    assert sum(results) == 3
    assert len(ledger(budget_env[1])["attempts"]) == 3


def test_explicit_retry_is_counted_and_unknown_charge_kept(budget_env, monkeypatch):
    calls = []

    def opener(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise URLError("synthetic failure")
        return response(USAGE)

    monkeypatch.setattr(cli, "urlopen", opener)
    with pytest.raises(cli.ProviderRequestError) as error:
        cli.call_openai(MODEL, "system", "user", retry_sleep=lambda _: pytest.fail("auto retry"))
    assert error.value.category == "provider_unavailable"
    assert len(calls) == 1
    assert cli.call_openai(MODEL, "system", "user", retry_sleep=lambda _: None) == (
        "Synthetic reply."
    )
    attempts = ledger(budget_env[1])["attempts"]
    assert [a["status"] for a in attempts] == ["reserved", "confirmed"]
    assert attempts[0]["reserved_usd"] == attempts[0]["charged_usd"]
    assert len(calls) == 2


def test_retry_refused_before_second_post(budget_env, monkeypatch):
    calls = []
    monkeypatch.setenv("OPENLEARN_QA_MAX_CALLS", "1")

    def opener(*_args, **_kwargs):
        calls.append(1)
        raise URLError("synthetic failure")

    monkeypatch.setattr(cli, "urlopen", opener)
    with pytest.raises(cli.ProviderRequestError) as error:
        cli.call_openai(MODEL, "system", "user", retry_sleep=lambda _: None)
    assert error.value.category == "provider_unavailable"
    with pytest.raises(cli.ProviderRequestError) as error:
        cli.call_openai(MODEL, "system", "user", retry_sleep=lambda _: None)
    assert error.value.category == "qa_budget_stop"
    assert len(calls) == 1


@pytest.mark.parametrize("include_usage", [False, True])
def test_stream_accounts_actual_terminal_usage(budget_env, monkeypatch, include_usage):
    captured = []

    def opener(request, **_kwargs):
        captured.append(json.loads(request.data))
        events = [{"choices": [{"delta": {"content": "Synthetic reply."}}]}]
        if include_usage:
            events.append({"choices": [], "usage": USAGE})
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return BytesIO((body + "data: [DONE]\n\n").encode())

    monkeypatch.setattr(cli, "urlopen", opener)
    assert cli.call_openai_streaming(MODEL, "system", "user", output_func=lambda _: None) == (
        "Synthetic reply."
    )
    assert captured[0]["stream_options"] == {"include_usage": True}
    assert captured[0]["reasoning"] == {"enabled": False, "exclude": True}
    assert captured[0]["provider"] == {
        "allow_fallbacks": False,
        "require_parameters": True,
        "max_price": {"prompt": 0.3, "completion": 1.2, "request": 0},
    }
    attempt = ledger(budget_env[1])["attempts"][0]
    assert attempt["status"] == ("confirmed" if include_usage else "reserved")


def test_corrupt_resume_refuses_reset(budget_env):
    path = budget_env[1]
    path.write_text("{corrupt", encoding="utf-8")
    with pytest.raises(qa_budget.QABudgetStop, match="cannot be read"):
        guard().reserve(BASE_URL, payload())
    assert path.read_text(encoding="utf-8") == "{corrupt"


def test_inconsistent_reported_charge_stops_future_requests(budget_env):
    budget = guard()
    attempt = budget.reserve(BASE_URL, payload())
    with pytest.raises(qa_budget.QABudgetStop, match="exceeds reservation"):
        budget.settle(attempt, {**USAGE, "cost": "0.04"})
    assert ledger(budget_env[1])["stopped"] is True
    with pytest.raises(qa_budget.QABudgetStop, match="inconsistent"):
        guard().reserve(BASE_URL, payload())


def test_transports_share_one_request_ceiling(budget_env, monkeypatch):
    monkeypatch.setenv("OPENLEARN_QA_MAX_CALLS", "2")
    monkeypatch.setattr(cli, "urlopen", lambda *_a, **_k: response(USAGE))
    cli.call_openai(MODEL, "system", "user")
    credentials = ProviderCredentials(BASE_URL, MODEL, "synthetic-test-key")
    assert providers.chat_completion(
        credentials, system="system", user="user", opener=lambda *_a, **_k: response(USAGE),
    ) == "Synthetic reply."
    with pytest.raises(providers.ProviderBudgetError) as error:
        providers.chat_completion(
            credentials, system="system", user="user",
            opener=lambda *_a, **_k: pytest.fail("network opened after shared ceiling"),
        )
    assert error.value.category == "qa_budget_stop"
    assert len(ledger(budget_env[1])["attempts"]) == 2


def test_direct_transport_retry_refused_before_second_post(budget_env, monkeypatch):
    monkeypatch.setenv("OPENLEARN_QA_MAX_CALLS", "1")
    calls = []

    def opener(*_a, **_k):
        calls.append(1)
        raise URLError("synthetic failure")

    credentials = ProviderCredentials(BASE_URL, MODEL, "synthetic-test-key")
    with pytest.raises(providers.ProviderError, match="provider_unreachable"):
        providers.chat_completion(
            credentials, system="system", user="user", opener=opener,
            retry_sleep=lambda _: pytest.fail("auto retry"),
        )
    with pytest.raises(providers.ProviderBudgetError):
        providers.chat_completion(
            credentials,
            system="system", user="user", opener=opener, retry_sleep=lambda _: None,
        )
    assert len(calls) == 1
    assert ledger(budget_env[1])["attempts"][0]["status"] == "reserved"


def test_stream_retry_refused_before_second_post(budget_env, monkeypatch):
    monkeypatch.setenv("OPENLEARN_QA_MAX_CALLS", "1")
    calls = []

    def opener(*_a, **_k):
        calls.append(1)
        raise URLError("synthetic failure")

    monkeypatch.setattr(cli, "urlopen", opener)
    with pytest.raises(cli.ProviderRequestError) as error:
        cli.call_openai_streaming(
            MODEL, "system", "user", output_func=lambda _: None, retry_sleep=lambda _: None,
        )
    assert error.value.category == "provider_unavailable"
    with pytest.raises(cli.ProviderRequestError) as error:
        cli.call_openai_streaming(
            MODEL, "system", "user", output_func=lambda _: None,
            retry_sleep=lambda _: pytest.fail("auto retry"),
        )
    assert error.value.category == "qa_budget_stop"
    assert len(calls) == 1
    assert ledger(budget_env[1])["attempts"][0]["status"] == "reserved"


def test_budget_stop_remains_distinct_from_learner_or_judge_failure():
    stop = cli.ProviderRequestError("qa_budget_stop", "Live QA budget stopped.")
    wrapped = lesson_policy.FirstLessonUnavailable("synthetic wrapper")
    wrapped.__cause__ = stop
    category, message = tutor_service._turn_failure(wrapped)
    assert category == "qa_budget_stop"
    assert "Your response is saved" in message
    assert "Review the batch" in message
    assert "retry" not in message.lower()


def test_follow_up_budget_stop_is_not_generic_provider_failure(monkeypatch):
    credentials = ProviderCredentials(BASE_URL, MODEL, "synthetic-test-key")
    monkeypatch.setattr(config, "effective_provider_credentials", lambda: credentials)

    def stopped(*_args, **_kwargs):
        raise providers.ProviderBudgetError("Live QA budget stopped.")

    monkeypatch.setattr(providers, "chat_completion", stopped)
    monkeypatch.setattr(
        tutor_service, "_finish_claimed_follow_up_record", lambda _r, _t, outcome: outcome,
    )
    result = tutor_service._generate_follow_up_record(
        {"source_title": "Synthetic", "source_goal": "Synthetic", "weak_areas": [], "interests": []},
        "synthetic-claim",
    )
    assert result["error_code"] == "qa_budget_stop"
    assert "retry" not in result["error_message"].lower()


def test_placement_budget_stop_does_not_become_a_learner_miss(tmp_path, monkeypatch):
    stop = cli.ProviderRequestError("qa_budget_stop", "Live QA budget stopped.")

    def stopped(*_args, **_kwargs):
        raise stop

    monkeypatch.setattr(cli, "call_openai", stopped)
    topic = cli.Topic("synthetic", tmp_path / "synthetic.md", {}, "")
    with pytest.raises(cli.ProviderRequestError) as caught:
        cli.placement_evaluation(topic, MODEL, 1, "Explain a stack", "synthetic answer", [])
    assert caught.value is stop
    assert topic.metadata == {}


def test_extractor_budget_stop_is_not_swallowed(tmp_path, monkeypatch):
    stop = cli.ProviderRequestError("qa_budget_stop", "Live QA budget stopped.")

    def stopped(*_args, **_kwargs):
        raise stop

    monkeypatch.setattr(cli, "call_openai_judgment", stopped)
    monkeypatch.setattr(cli, "configured_extractor_model", lambda _model: MODEL)
    topic = cli.Topic("synthetic", tmp_path / "synthetic.md", {}, "")
    with pytest.raises(cli.ProviderRequestError) as caught:
        cli.update_learning_metadata(topic, "synthetic question", "synthetic reply", MODEL)
    assert caught.value is stop
    assert topic.metadata == {}


def test_context_window_bound_needs_no_tokenizer_attestation(budget_env):
    pricing_path, ledger_path, pricing = budget_env
    pricing.update(
        input_bound_mode="context_window", context_tokens=1048576,
        input_usd_per_million="0.015543", output_usd_per_million="0.396",
    )
    pricing.pop("input_framing_tokens")
    pricing.pop("input_tokens_per_utf8_byte")
    pricing_path.write_text(json.dumps(pricing), encoding="utf-8")
    budget = guard()
    first = budget.reserve(BASE_URL, payload())
    attempts = ledger(ledger_path)["attempts"]
    assert attempts[0]["input_token_bound"] == 1048576
    assert attempts[0]["reserved_usd"] == "0.016931616768"
    budget.reserve(BASE_URL, payload())
    with pytest.raises(qa_budget.QABudgetStop, match="ceiling"):
        budget.reserve(BASE_URL, payload())
    budget.settle(first, USAGE)
    assert ledger(ledger_path)["attempts"][0]["charged_usd"] == "0.00000807543"
    assert budget.reserve(BASE_URL, payload())
