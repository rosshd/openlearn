from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Callable, Protocol, Self
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    base_url: str | None
    default_model: str | None
    key_required: bool


PROVIDER_PRESETS = {
    "openai": ProviderPreset(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4.1-mini",
        key_required=True,
    ),
    "anthropic-compatible": ProviderPreset(
        name="Anthropic-compatible",
        base_url=None,
        default_model=None,
        key_required=True,
    ),
    "ollama": ProviderPreset(
        name="Ollama",
        base_url="http://localhost:11434/v1",
        default_model="llama3.1",
        key_required=False,
    ),
    "custom": ProviderPreset(
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


class UrlResponse(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def getcode(self) -> int: ...


UrlOpener = Callable[..., UrlResponse]


def validate_provider(
    base_url: str,
    api_key: str,
    *,
    opener: UrlOpener = urlopen,
) -> ValidationResult:
    request = Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        response = opener(request, timeout=10)
        with response:
            status = response.getcode()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            return ValidationResult(ValidationStatus.REJECTED)
        return ValidationResult(ValidationStatus.HTTP_ERROR, str(exc))
    except (URLError, TimeoutError) as exc:
        return ValidationResult(ValidationStatus.NETWORK_ERROR, str(exc))

    if status == 200:
        return ValidationResult(ValidationStatus.VALID)
    if status in {401, 403}:
        return ValidationResult(ValidationStatus.REJECTED)
    return ValidationResult(ValidationStatus.HTTP_ERROR, f"HTTP {status}")
