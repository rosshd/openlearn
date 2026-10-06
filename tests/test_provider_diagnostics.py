from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from openlearn import cli


def http_error(body: object, headers: dict[str, str] | None = None) -> HTTPError:
    encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HTTPError("https://example.test", 429, "private reason", headers or {}, BytesIO(encoded))


def test_http_error_retains_only_validated_diagnostics():
    error = cli._provider_transport_error(http_error(
        {"error": {"message": "SECRET source and prompt", "metadata": {
            "limit_source": "openrouter_key_limit", "provider_name": "OpenInference",
            "provider_code": "429", "raw": "SECRET", "api_key": "SECRET",
        }}},
        {"Retry-After": "12", "X-RateLimit-Reset": "1790828700",
         "Authorization": "Bearer SECRET", "Set-Cookie": "SECRET"},
    ), api_key="SECRET")
    assert error.category == "provider_rate_limited"
    assert error.diagnostics == {
        "http_status": 429, "retry_after_seconds": 12, "rate_limit_reset": 1790828700,
        "limit_source": "openrouter_key_limit", "provider": "openinference", "provider_code": 429,
    }
    assert "SECRET" not in str(error)
    assert "private reason" not in str(error)


@pytest.mark.parametrize("body", [b"not JSON SECRET", [], {}, {"error": []}])
def test_absent_or_malformed_body_preserves_status(body):
    error = cli._provider_transport_error(http_error(body), api_key="")
    assert error.diagnostics == {"http_status": 429}


@pytest.mark.parametrize("value", ["SECRET", "-1", "NaN", "1.5", "1\r\nSECRET", "9" * 30])
def test_untrusted_headers_and_attribution_are_dropped(value):
    error = cli._provider_transport_error(http_error(
        {"error": {"metadata": {"limit_source": value, "provider_name": value,
                                "provider_code": value}}},
        {"Retry-After": value, "X-RateLimit-Reset": value},
    ), api_key="")
    assert error.diagnostics == {"http_status": 429}


def test_retry_after_http_date_is_normalized_without_raw_text():
    error = cli._provider_transport_error(http_error({}, {
        "Retry-After": "Thu, 01 Oct 2026 04:30:00 GMT",
    }), api_key="")
    assert error.diagnostics == {"http_status": 429, "retry_after_at": "2026-10-01T04:30:00+00:00"}


def test_stream_code_is_not_an_http_status_and_raw_text_is_not_retained():
    error = cli._stream_error({
        "provider": "openai", "prompt": "SECRET",
        "error": {"code": 429, "message": "SECRET", "metadata": {
            "limit_source": "openrouter_in_flight_budget", "provider_code": 503,
        }},
    })
    assert error is not None
    assert error.diagnostics == {
        "stream_error_code": 429, "provider": "openai",
        "limit_source": "openrouter_in_flight_budget", "provider_code": 503,
    }
    assert "SECRET" not in str(error)
    assert "http_status" not in error.diagnostics


@pytest.mark.parametrize("event", [{}, {"error": None}, {"error": []}])
def test_non_error_stream_event(event):
    assert cli._stream_error(event) is None


def test_malformed_stream_metadata_is_not_retained():
    error = cli._stream_error({"provider": "SECRET", "error": {
        "code": "SECRET", "message": "SECRET", "metadata": ["SECRET"],
    }})
    assert error is not None
    assert error.diagnostics == {}
    assert "SECRET" not in str(error)


def test_network_error_has_no_invented_http_status():
    error = cli._provider_transport_error(URLError("offline"), api_key="")
    assert error.diagnostics == {}


def test_stream_retains_actual_transport_status_and_retry_headers():
    error = cli._stream_error({"error": {"code": 429}}, http_status=200,
                             headers={"retry-after": "3", "x-ratelimit-reset": "1790828700"})
    assert error.diagnostics == {
        "http_status": 200, "stream_error_code": 429,
        "retry_after_seconds": 3, "rate_limit_reset": 1790828700,
    }


def test_stream_transport_supplies_observed_metadata(monkeypatch):
    class Response:
        status = 200
        headers = {"Retry-After": "3", "Authorization": "SECRET"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def __iter__(self):
            yield b'data: {"provider":"openai","error":{"code":429,"message":"SECRET"}}\n'

    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    monkeypatch.setattr(cli, "configured_base_url", lambda: "https://example.test")
    monkeypatch.setattr(cli, "configured_openai_api_key", lambda: "synthetic-key")
    monkeypatch.setattr(cli, "_qa_budget_guard", lambda: None)
    monkeypatch.setattr(cli, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(cli.ProviderRequestError) as caught:
        cli.call_openai_streaming("synthetic-model", "synthetic system", "synthetic prompt",
                                 output_func=lambda _line: None)
    assert caught.value.diagnostics == {
        "http_status": 200, "stream_error_code": 429,
        "retry_after_seconds": 3, "provider": "openai",
    }
    assert "SECRET" not in str(caught.value)


def test_http_body_read_failure_does_not_hide_original_status():
    class UnreadableBody(BytesIO):
        def read(self, *_args):
            raise OSError("SECRET")

    raw = HTTPError("https://example.test", 503, "SECRET", None, UnreadableBody())
    error = cli._provider_transport_error(raw, api_key="")
    assert error.diagnostics == {"http_status": 503}
    assert "SECRET" not in str(error)
    assert raw.fp.closed


def test_oversized_http_body_is_not_parsed_or_retained():
    error = cli._provider_transport_error(http_error(b"SECRET" * 20000), api_key="")
    assert error.diagnostics == {"http_status": 429}
    assert "SECRET" not in str(error)


@pytest.mark.parametrize("value", [True, {}, [], None, 1.5])
def test_malformed_diagnostic_values_are_not_accepted(value):
    error = cli.ProviderRequestError("provider_unavailable", "Safe error", diagnostics={
        "http_status": value, "retry_after_seconds": value, "retry_after_at": value,
        "rate_limit_reset": value, "limit_source": value, "provider": value,
        "provider_code": value, "stream_error_code": value, "Authorization": "SECRET",
    })
    assert error.diagnostics == {}
