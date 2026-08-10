from __future__ import annotations

import ipaddress
import json
import os
import random
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Protocol, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from openlearn import __version__, config
from openlearn.config import (
    ProviderCredentials,
    ProviderStatus,
    base_url_allows_keyless_requests,
    save_provider_configuration,
)

MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ProviderPreset:
    slug: str
    name: str
    base_url: str | None
    default_model: str | None
    key_required: bool
    setup_url: str | None = None
    recommendation: str | None = None


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "openrouter": ProviderPreset(
        slug="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="google/gemini-3.1-flash-lite",
        key_required=True,
        setup_url="https://openrouter.ai/keys",
        recommendation="recommended - inexpensive models from many providers",
    ),
    "openai": ProviderPreset(
        slug="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4.1-mini",
        key_required=True,
    ),
    "anthropic-compatible": ProviderPreset(
        slug="anthropic-compatible",
        name="Anthropic-compatible",
        base_url=None,
        default_model=None,
        key_required=True,
    ),
    "ollama": ProviderPreset(
        slug="ollama",
        name="Ollama",
        base_url="http://localhost:11434/v1",
        default_model="llama3.1",
        key_required=False,
    ),
    "custom": ProviderPreset(
        slug="custom",
        name="Custom OpenAI-compatible provider",
        base_url=None,
        default_model=None,
        key_required=True,
    ),
}


class ValidationStatus(Enum):
    VALID = "valid"
    REJECTED = "rejected"
    NETWORK_ERROR = "network_error"
    HTTP_ERROR = "http_error"


@dataclass(frozen=True)
class ValidationResult:
    status: ValidationStatus
    detail: str = ""


class ProviderError(RuntimeError):
    """A provider failed without exposing credentials or response bodies."""


class ProviderConfigurationError(ValueError):
    """A provider setup result cannot be persisted safely."""


class UrlResponse(Protocol):
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...
    def getcode(self) -> int: ...
    def read(self, amount: int = -1) -> bytes: ...


UrlOpener = Callable[..., UrlResponse]


class _RejectRedirects(HTTPRedirectHandler):
    """Keep credentials on the exact provider origin chosen by the user."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_DEFAULT_OPENER = build_opener(_RejectRedirects()).open


def _normalized_provider_url(value: str) -> str | None:
    if not value or value != value.strip() or _CONTROL_CHARACTERS.search(value):
        return None
    if "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or username is not None
        or password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
        or _CONTROL_CHARACTERS.search(hostname)
        or any(character.isspace() for character in hostname)
    ):
        return None
    if parsed.scheme == "http" and not _hostname_is_loopback(hostname):
        return None
    return value.rstrip("/")


def _hostname_is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _api_key_is_safe(api_key: str | None) -> bool:
    return api_key is None or _CONTROL_CHARACTERS.search(api_key) is None


def _read_provider_response(response: UrlResponse) -> bytes:
    data = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    if len(data) > MAX_PROVIDER_RESPONSE_BYTES:
        raise ProviderError("invalid_provider_response")
    return data


def preset_for_base_url(base_url: str) -> ProviderPreset:
    normalized = base_url.strip().rstrip("/")
    for preset in PROVIDER_PRESETS.values():
        if preset.base_url and preset.base_url.rstrip("/") == normalized:
            return preset
    return ProviderPreset(
        slug="environment",
        name="OpenAI-compatible provider",
        base_url=normalized,
        default_model=None,
        key_required=not base_url_allows_keyless_requests(normalized),
    )


def provider_status(*, home=None, environ=None) -> ProviderStatus:
    """Return the canonical secret-free provider state."""
    return config.provider_status(home=home, environ=environ)


def _ensure_locally_managed(field: str, environ: Mapping[str, str] | None) -> None:
    env = os.environ if environ is None else environ
    variable = {
        "api_key": "OPENAI_API_KEY",
        "model": "OPENLEARN_MODEL",
        "base_url": "OPENLEARN_BASE_URL",
    }[field]
    if env.get(variable):
        raise ProviderConfigurationError(f"environment_managed_{field}")


def _mutate_saved_provider(
    mutation: Callable[[dict[str, object]], None], *, home=None, environ=None
) -> ProviderStatus:
    config.mutate_config(mutation, home=home, environ=environ)
    return provider_status(home=home, environ=environ)


def set_saved_api_key(api_key: str, *, home=None, environ=None) -> ProviderStatus:
    _ensure_locally_managed("api_key", environ)
    value = api_key.strip()
    if not value or not _api_key_is_safe(value):
        raise ProviderConfigurationError("invalid_api_key")

    def mutation(saved: dict[str, object]) -> None:
        saved.pop("api_key", None)
        saved["openai_api_key"] = value
        saved["provider_verified"] = False

    return _mutate_saved_provider(mutation, home=home, environ=environ)


def set_saved_model(model: str, *, home=None, environ=None) -> ProviderStatus:
    _ensure_locally_managed("model", environ)
    value = model.strip()
    if not value or _CONTROL_CHARACTERS.search(value):
        raise ProviderConfigurationError("invalid_model")

    def mutation(saved: dict[str, object]) -> None:
        saved["model"] = value
        saved["provider_verified"] = False

    return _mutate_saved_provider(mutation, home=home, environ=environ)


def set_saved_base_url(base_url: str, *, home=None, environ=None) -> ProviderStatus:
    _ensure_locally_managed("base_url", environ)
    value = _normalized_provider_url(base_url)
    if value is None:
        raise ProviderConfigurationError("invalid_base_url")

    def mutation(saved: dict[str, object]) -> None:
        saved["base_url"] = value
        saved["provider_verified"] = False

    return _mutate_saved_provider(mutation, home=home, environ=environ)


def remove_saved_api_key(*, home=None, environ=None) -> ProviderStatus:
    _ensure_locally_managed("api_key", environ)

    def mutation(saved: dict[str, object]) -> None:
        saved.pop("openai_api_key", None)
        saved.pop("api_key", None)
        saved["provider_verified"] = False

    return _mutate_saved_provider(mutation, home=home, environ=environ)


def validate_provider(
    base_url: str,
    api_key: str | None,
    *,
    opener: UrlOpener = _DEFAULT_OPENER,
    timeout_seconds: int = 10,
) -> ValidationResult:
    normalized = _normalized_provider_url(base_url)
    if normalized is None:
        return ValidationResult(ValidationStatus.HTTP_ERROR, "invalid_base_url")
    if not _api_key_is_safe(api_key):
        return ValidationResult(ValidationStatus.REJECTED, "invalid_api_key")
    if not api_key and not base_url_allows_keyless_requests(normalized):
        return ValidationResult(ValidationStatus.REJECTED, "key_required")
    endpoint = "key" if normalized == PROVIDER_PRESETS["openrouter"].base_url else "models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        request = Request(f"{normalized}/{endpoint}", headers=headers, method="GET")
        response = opener(request, timeout=timeout_seconds)
        with response:
            status = response.getcode()
    except HTTPError as exc:
        status = exc.code
        exc.close()
        if status in {401, 403}:
            return ValidationResult(ValidationStatus.REJECTED, "credential_rejected")
        return ValidationResult(ValidationStatus.HTTP_ERROR, f"http_{status}")
    except (URLError, TimeoutError, OSError):
        return ValidationResult(ValidationStatus.NETWORK_ERROR, "provider_unreachable")
    except (ValueError, UnicodeError):
        return ValidationResult(ValidationStatus.HTTP_ERROR, "invalid_provider_request")
    if status == 200:
        return ValidationResult(ValidationStatus.VALID)
    if status in {401, 403}:
        return ValidationResult(ValidationStatus.REJECTED, "credential_rejected")
    return ValidationResult(ValidationStatus.HTTP_ERROR, f"http_{status}")


def validate_provider_model(
    base_url: str,
    api_key: str | None,
    model: str,
    *,
    opener: UrlOpener = _DEFAULT_OPENER,
    timeout_seconds: int = 10,
) -> ValidationResult:
    """Confirm that the selected provider advertises the requested model."""
    normalized = _normalized_provider_url(base_url)
    selected_model = model.strip()
    if normalized is None:
        return ValidationResult(ValidationStatus.HTTP_ERROR, "invalid_base_url")
    if not selected_model or _CONTROL_CHARACTERS.search(selected_model):
        return ValidationResult(ValidationStatus.HTTP_ERROR, "invalid_model")
    if not _api_key_is_safe(api_key):
        return ValidationResult(ValidationStatus.REJECTED, "invalid_api_key")
    if not api_key and not base_url_allows_keyless_requests(normalized):
        return ValidationResult(ValidationStatus.REJECTED, "key_required")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        request = Request(f"{normalized}/models", headers=headers, method="GET")
        response = opener(request, timeout=timeout_seconds)
        with response:
            status = response.getcode()
            payload = _read_provider_response(response) if status == 200 else b""
    except HTTPError as exc:
        status = exc.code
        exc.close()
        if status in {401, 403}:
            return ValidationResult(ValidationStatus.REJECTED, "credential_rejected")
        return ValidationResult(ValidationStatus.HTTP_ERROR, f"http_{status}")
    except (URLError, TimeoutError, OSError):
        return ValidationResult(ValidationStatus.NETWORK_ERROR, "provider_unreachable")
    except (TypeError, ValueError, UnicodeError, ProviderError):
        return ValidationResult(ValidationStatus.HTTP_ERROR, "invalid_model_catalog")
    if status in {401, 403}:
        return ValidationResult(ValidationStatus.REJECTED, "credential_rejected")
    if status != 200:
        return ValidationResult(ValidationStatus.HTTP_ERROR, f"http_{status}")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return ValidationResult(ValidationStatus.HTTP_ERROR, "invalid_model_catalog")
    entries = decoded.get("data") if isinstance(decoded, dict) else None
    if not isinstance(entries, list):
        return ValidationResult(ValidationStatus.HTTP_ERROR, "invalid_model_catalog")
    identifiers = {
        entry.get("id")
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    if selected_model not in identifiers:
        return ValidationResult(ValidationStatus.HTTP_ERROR, "model_unavailable")
    return ValidationResult(ValidationStatus.VALID)


def persist_validation_result(
    *,
    base_url: str,
    model: str,
    api_key: str | None,
    validation: ValidationResult,
    allow_unverified: bool = False,
    home=None,
    environ=None,
) -> ProviderStatus:
    """Persist a valid setup, or an explicitly accepted unreachable setup."""
    if validation.status is ValidationStatus.VALID:
        verified = True
    elif validation.status is ValidationStatus.NETWORK_ERROR and allow_unverified:
        verified = False
    elif validation.status is ValidationStatus.NETWORK_ERROR:
        raise ProviderConfigurationError("unverified_provider_requires_explicit_confirmation")
    elif validation.status is ValidationStatus.REJECTED:
        raise ProviderConfigurationError("rejected_provider_credentials")
    else:
        raise ProviderConfigurationError("provider_validation_failed")
    return save_provider_configuration(
        base_url=base_url,
        model=model,
        api_key=api_key,
        verified=verified,
        home=home,
        environ=environ,
    )


def _is_transient(exc: HTTPError | URLError | TimeoutError) -> bool:
    return not isinstance(exc, HTTPError) or exc.code == 429 or 500 <= exc.code <= 599


def chat_completion(
    credentials: ProviderCredentials,
    *,
    system: str,
    user: str,
    opener: UrlOpener = _DEFAULT_OPENER,
    max_tokens: int = 1600,
    timeout_seconds: int = 60,
    max_attempts: int = 3,
    retry_sleep: Callable[[float], object] = time.sleep,
    retry_jitter: Callable[[float, float], float] = random.uniform,
) -> str:
    normalized_url = _normalized_provider_url(credentials.base_url)
    if normalized_url is None:
        raise ProviderError("invalid_provider_url")
    if not _api_key_is_safe(credentials.api_key):
        raise ProviderError("invalid_provider_credentials")
    if not credentials.api_key and not base_url_allows_keyless_requests(normalized_url):
        raise ProviderError("provider_api_key_required")
    payload: Mapping[str, object] = {
        "model": credentials.model,
        "max_tokens": max_tokens,
        "include_reasoning": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"openLearn/{__version__}",
    }
    if credentials.api_key:
        headers["Authorization"] = f"Bearer {credentials.api_key}"
    try:
        request = Request(
            f"{normalized_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
    except (TypeError, ValueError, UnicodeError):
        raise ProviderError("invalid_provider_request") from None
    data: object = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = opener(request, timeout=timeout_seconds)
            with response:
                data = json.loads(_read_provider_response(response).decode("utf-8"))
            break
        except (HTTPError, URLError, TimeoutError) as exc:
            if isinstance(exc, HTTPError):
                exc.close()
            if not _is_transient(exc) or attempt == max_attempts:
                code = f"http_{exc.code}" if isinstance(exc, HTTPError) else "provider_unreachable"
                raise ProviderError(code) from None
            delay = 0.5 * (2 ** (attempt - 1)) + retry_jitter(0.0, 0.25)
            retry_sleep(delay)
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            raise ProviderError("invalid_provider_response") from None
    if not isinstance(data, dict):
        raise ProviderError("invalid_provider_response")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("invalid_provider_response")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ProviderError("invalid_provider_response")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ProviderError("empty_provider_response")
    return content.strip()
