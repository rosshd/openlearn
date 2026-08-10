from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from openlearn.config import ProviderCredentials, read_config, save_provider_configuration, write_config
from openlearn.providers import (
    PROVIDER_PRESETS,
    ProviderError,
    ProviderConfigurationError,
    ValidationStatus,
    MAX_PROVIDER_RESPONSE_BYTES,
    _RejectRedirects,
    chat_completion,
    preset_for_base_url,
    persist_validation_result,
    provider_status,
    remove_saved_api_key,
    set_saved_api_key,
    set_saved_base_url,
    set_saved_model,
    validate_provider,
    validate_provider_model,
    ValidationResult,
)


class Response:
    def __init__(self, status: int = 200, body: object | None = None) -> None:
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def getcode(self) -> int:
        return self.status

    def read(self, _amount: int = -1) -> bytes:
        return json.dumps(self.body).encode("utf-8")


def test_openrouter_validation_uses_key_endpoint_and_bearer_header() -> None:
    captured = {}

    def opener(request, *, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return Response()

    result = validate_provider("https://openrouter.ai/api/v1", "test-key", opener=opener)
    assert result.status is ValidationStatus.VALID
    assert captured == {
        "url": "https://openrouter.ai/api/v1/key",
        "authorization": "Bearer test-key",
        "timeout": 10,
    }


def test_generic_and_local_validation_semantics() -> None:
    urls: list[str] = []

    def opener(request, *, timeout):
        urls.append(request.full_url)
        assert request.get_header("Authorization") is None
        return Response()

    assert validate_provider("http://localhost:11434/v1", None, opener=opener).status is (
        ValidationStatus.VALID
    )
    assert urls == ["http://localhost:11434/v1/models"]
    assert validate_provider("https://hosted.example/v1", None).status is (
        ValidationStatus.REJECTED
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://provider.example/v1",
        "http://192.168.1.25:11434/v1",
    ],
)
def test_plain_http_is_rejected_for_non_loopback_providers(base_url: str) -> None:
    result = validate_provider(
        base_url,
        "private-key",
        opener=lambda *_args, **_kwargs: pytest.fail("insecure provider reached network"),
    )

    assert result.status is ValidationStatus.HTTP_ERROR
    assert result.detail == "invalid_base_url"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.2:11434/v1",
        "http://[::1]:11434/v1",
    ],
)
def test_plain_http_remains_available_for_loopback_providers(base_url: str) -> None:
    result = validate_provider(base_url, "local-key", opener=lambda *_args, **_kwargs: Response())

    assert result.status is ValidationStatus.VALID


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (HTTPError("url", 401, "secret", {}, BytesIO()), ValidationStatus.REJECTED),
        (HTTPError("url", 502, "secret", {}, BytesIO()), ValidationStatus.HTTP_ERROR),
        (URLError("secret network detail"), ValidationStatus.NETWORK_ERROR),
        (TimeoutError("secret timeout detail"), ValidationStatus.NETWORK_ERROR),
    ],
)
def test_validation_outcomes_are_distinct_and_redacted(error: Exception, expected) -> None:
    def opener(*_args, **_kwargs):
        raise error

    result = validate_provider("https://hosted.example/v1", "secret-key", opener=opener)
    assert result.status is expected
    assert "secret" not in result.detail


def test_provider_validation_does_not_follow_redirects_or_forward_key() -> None:
    request = Request(
        "https://provider.example/v1/models",
        headers={"Authorization": "Bearer redirect-secret"},
    )

    redirected = _RejectRedirects().redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://attacker.example/received",
    )

    assert redirected is None


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://provider.example/v1",
        "https:///missing-host",
        "https://user:password@provider.example/v1",
        "https://provider.example/v1?redirect=https://attacker.example",
        "https://provider.example/v1#fragment",
        "https://provider.example/v1\r\nX-Injected: yes",
        "https://provider.example:invalid/v1",
    ],
)
def test_provider_validation_rejects_malformed_urls_without_opening(base_url: str) -> None:
    def opener(*_args, **_kwargs):
        pytest.fail("invalid provider URL reached the network")

    result = validate_provider(base_url, "safe-key", opener=opener)

    assert result.status is ValidationStatus.HTTP_ERROR
    assert result.detail == "invalid_base_url"


def test_provider_validation_rejects_multiline_key_without_exposing_it(caplog) -> None:
    secret = "first-secret-line\nsecond-secret-line"

    def opener(*_args, **_kwargs):
        pytest.fail("invalid API key reached the network")

    result = validate_provider("https://provider.example/v1", secret, opener=opener)

    assert result.status is ValidationStatus.REJECTED
    assert result.detail == "invalid_api_key"
    assert secret not in repr(result)
    assert secret not in caplog.text


def test_presets_keep_openrouter_recommendation_and_local_keyless_behavior() -> None:
    openrouter = PROVIDER_PRESETS["openrouter"]
    assert openrouter.default_model == "google/gemini-3.1-flash-lite"
    assert openrouter.key_required is True
    assert preset_for_base_url("http://127.0.0.1:8000/v1").key_required is False


def test_selected_model_must_exist_in_provider_catalog() -> None:
    def opener(request, *, timeout):
        assert request.full_url == "https://provider.example/v1/models"
        assert timeout == 10
        return Response(body={"data": [{"id": "available-model"}]})

    available = validate_provider_model(
        "https://provider.example/v1", "secret", "available-model", opener=opener
    )
    missing = validate_provider_model(
        "https://provider.example/v1", "secret", "missing-model", opener=opener
    )

    assert available.status is ValidationStatus.VALID
    assert missing.status is ValidationStatus.HTTP_ERROR
    assert missing.detail == "model_unavailable"


def test_validation_result_persistence_requires_explicit_unverified_action(tmp_path) -> None:
    network_failure = ValidationResult(ValidationStatus.NETWORK_ERROR, "provider_unreachable")
    with pytest.raises(ProviderConfigurationError, match="explicit_confirmation"):
        persist_validation_result(
            base_url="https://provider.example/v1",
            model="model",
            api_key="secret",
            validation=network_failure,
            home=tmp_path,
            environ={},
        )
    status = persist_validation_result(
        base_url="https://provider.example/v1",
        model="model",
        api_key="secret",
        validation=network_failure,
        allow_unverified=True,
        home=tmp_path,
        environ={},
    )
    assert status.verified is False


def test_rejected_credentials_cannot_be_persisted(tmp_path) -> None:
    with pytest.raises(ProviderConfigurationError, match="rejected"):
        persist_validation_result(
            base_url="https://provider.example/v1",
            model="model",
            api_key="bad-secret",
            validation=ValidationResult(ValidationStatus.REJECTED),
            home=tmp_path,
            environ={},
        )
    assert not (tmp_path / "config.json").exists()


def test_provider_lifecycle_mutations_invalidate_prior_verification(tmp_path) -> None:
    save_provider_configuration(
        base_url="https://provider.example/v1",
        model="old-model",
        api_key="old-secret",
        verified=True,
        home=tmp_path,
        environ={},
    )

    set_saved_model("new-model", home=tmp_path, environ={})
    assert provider_status(home=tmp_path, environ={}).verified is False

    set_saved_base_url("https://other.example/v1", home=tmp_path, environ={})
    set_saved_api_key("new-secret", home=tmp_path, environ={})
    status = remove_saved_api_key(home=tmp_path, environ={})

    assert status.base_url == "https://other.example/v1"
    assert status.model == "new-model"
    assert status.key_configured is False
    assert status.verified is False
    assert "old-secret" not in json.dumps(status.as_dict())
    assert "new-secret" not in json.dumps(status.as_dict())
    assert read_config(home=tmp_path) == {
        "base_url": "https://other.example/v1",
        "model": "new-model",
        "provider_verified": False,
    }


@pytest.mark.parametrize(
    ("operation", "environment"),
    [
        (lambda home, env: set_saved_api_key("saved", home=home, environ=env), {"OPENAI_API_KEY": "managed"}),
        (lambda home, env: set_saved_model("saved", home=home, environ=env), {"OPENLEARN_MODEL": "managed"}),
        (
            lambda home, env: set_saved_base_url(
                "https://saved.example/v1", home=home, environ=env
            ),
            {"OPENLEARN_BASE_URL": "https://managed.example/v1"},
        ),
        (lambda home, env: remove_saved_api_key(home=home, environ=env), {"OPENAI_API_KEY": "managed"}),
    ],
)
def test_environment_managed_provider_fields_are_immutable(
    tmp_path, operation, environment
) -> None:
    write_config({"editor": ["nvim"]}, home=tmp_path)

    with pytest.raises(ProviderConfigurationError, match="environment_managed"):
        operation(tmp_path, environment)

    assert read_config(home=tmp_path) == {"editor": ["nvim"]}


def test_provider_lifecycle_rejects_unsafe_values_without_mutating_config(tmp_path) -> None:
    write_config({"editor": ["nvim"]}, home=tmp_path)

    with pytest.raises(ProviderConfigurationError, match="invalid_base_url"):
        set_saved_base_url("http://provider.example/v1", home=tmp_path, environ={})
    with pytest.raises(ProviderConfigurationError, match="invalid_api_key"):
        set_saved_api_key("unsafe\nkey", home=tmp_path, environ={})

    assert read_config(home=tmp_path) == {"editor": ["nvim"]}


def test_chat_completion_sends_expected_request_and_returns_text() -> None:
    captured = {}

    def opener(request, *, timeout):
        captured["authorization"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data)
        captured["url"] = request.full_url
        return Response(body={"choices": [{"message": {"content": "  useful reply  "}}]})

    result = chat_completion(
        ProviderCredentials("https://provider.example/v1", "cheap-model", "private-key"),
        system="system only",
        user="active course only",
        opener=opener,
    )
    assert result == "useful reply"
    assert captured["authorization"] == "Bearer private-key"
    assert captured["url"] == "https://provider.example/v1/chat/completions"
    assert captured["payload"]["messages"] == [
        {"role": "system", "content": "system only"},
        {"role": "user", "content": "active course only"},
    ]


def test_chat_completion_retries_transient_failure_without_leaking_secret() -> None:
    attempts = 0
    sleeps: list[float] = []

    def opener(_request, *, timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise URLError("private-key provider detail")
        return Response(body={"choices": [{"message": {"content": "ok"}}]})

    result = chat_completion(
        ProviderCredentials("https://provider.example/v1", "model", "private-key"),
        system="system",
        user="user",
        opener=opener,
        retry_sleep=sleeps.append,
        retry_jitter=lambda _start, _end: 0.0,
    )
    assert result == "ok"
    assert attempts == 2
    assert sleeps == [0.5]


def test_chat_completion_errors_use_safe_codes() -> None:
    def opener(*_args, **_kwargs):
        raise HTTPError("url", 401, "private-key", {}, BytesIO(b"private-key"))

    with pytest.raises(ProviderError) as caught:
        chat_completion(
            ProviderCredentials("https://provider.example/v1", "model", "private-key"),
            system="system",
            user="user",
            opener=opener,
        )
    assert str(caught.value) == "http_401"
    assert "private-key" not in str(caught.value)


def test_chat_completion_rejects_multiline_key_with_safe_error() -> None:
    secret = "private-key\r\nX-Injected: yes"

    with pytest.raises(ProviderError, match="invalid_provider_credentials") as caught:
        chat_completion(
            ProviderCredentials("https://provider.example/v1", "model", secret),
            system="system",
            user="user",
            opener=lambda *_args, **_kwargs: pytest.fail("invalid key reached network"),
        )

    assert secret not in str(caught.value)


def test_chat_completion_rejects_malformed_provider_url_before_opening() -> None:
    with pytest.raises(ProviderError, match="invalid_provider_url"):
        chat_completion(
            ProviderCredentials("https://provider.example/v1?unsafe=yes", "model", "private-key"),
            system="system",
            user="user",
            opener=lambda *_args, **_kwargs: pytest.fail("invalid URL reached network"),
        )


def test_chat_completion_rejects_plain_http_remote_provider_before_opening() -> None:
    with pytest.raises(ProviderError, match="invalid_provider_url"):
        chat_completion(
            ProviderCredentials("http://192.168.1.25:11434/v1", "model", "private-key"),
            system="system",
            user="user",
            opener=lambda *_args, **_kwargs: pytest.fail("insecure provider reached network"),
        )


def test_chat_completion_bounds_provider_response_reads() -> None:
    class OversizedResponse(Response):
        requested_amount: int | None = None

        def read(self, amount: int = -1) -> bytes:
            self.requested_amount = amount
            return b"x" * amount

    response = OversizedResponse()

    with pytest.raises(ProviderError, match="invalid_provider_response"):
        chat_completion(
            ProviderCredentials("https://provider.example/v1", "model", "private-key"),
            system="system",
            user="user",
            opener=lambda *_args, **_kwargs: response,
        )

    assert response.requested_amount == MAX_PROVIDER_RESPONSE_BYTES + 1
