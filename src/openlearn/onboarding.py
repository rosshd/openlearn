from __future__ import annotations

import getpass
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
InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]
KeyInputFunc = Callable[[str], str]
ProviderValidator = Callable[[str, str], ValidationResult]


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


def prompt_for_validated_key(
    preset: ProviderPreset,
    base_url: str,
    *,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    key_input_func: KeyInputFunc | None = None,
    validator: ProviderValidator = validate_provider,
) -> str | None:
    key_input = key_input_func or getpass.getpass
    prompt = "API key (hidden): " if preset.key_required else "API key (optional, hidden): "

    for attempt in range(1, 4):
        api_key = key_input(prompt).strip()
        if not api_key and preset.key_required:
            output_func("API key is required.")
            continue

        output_func("Testing connection...")
        result = validator(base_url, api_key)
        if result.status is ValidationStatus.VALID:
            output_func("Connection successful.")
            return api_key
        if result.status is ValidationStatus.REJECTED:
            output_func("Key rejected by provider.")
            if attempt < 3:
                output_func("Please try again.")
            continue
        if result.status is ValidationStatus.NETWORK_ERROR:
            detail = f" ({result.detail})" if result.detail else ""
            output_func(f"Could not reach the provider{detail}.")
            save_anyway = input_func("Save this configuration anyway? [y/N]: ").strip().lower()
            if save_anyway in {"y", "yes"}:
                return api_key
            output_func("Configuration not saved.")
            return None

        detail = f": {result.detail}" if result.detail else ""
        output_func(f"Provider validation failed{detail}.")
        return None

    output_func(
        "Key rejected after 3 attempts. Run 'openlearn config set-key' when you have a valid key."
    )
    return None
