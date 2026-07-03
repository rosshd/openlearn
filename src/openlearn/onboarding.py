from __future__ import annotations

import getpass
from argparse import Namespace
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
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


class OnboardingDestination(Enum):
    QUICK_LEARN = "quick_learn"
    VIM_STARTER = "vim_starter"
    MENU = "menu"


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
ConfigCommand = Callable[[Namespace], int]


def prompt_for_provider(
    *,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> ProviderPreset:
    presets = list(PROVIDER_PRESETS.values())
    output_func("Provider:")
    for index, preset in enumerate(presets, start=1):
        output_func(f"  {index}. {preset.name}")

    while True:
        choice = input_func("Provider [1]: ").strip() or "1"
        if choice.isdigit() and 1 <= int(choice) <= len(presets):
            return presets[int(choice) - 1]
        output_func(f"Choose a provider from 1 to {len(presets)}.")


def prompt_for_base_url(
    preset: ProviderPreset,
    *,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> str:
    if preset.base_url is not None:
        return preset.base_url

    while True:
        base_url = input_func("Base URL: ").strip().rstrip("/")
        if base_url.startswith(("https://", "http://")):
            return base_url
        output_func("Base URL must start with https:// or http://.")


def prompt_for_model(
    preset: ProviderPreset,
    *,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> str:
    prompt = (
        f"Model [{preset.default_model}]: "
        if preset.default_model is not None
        else "Model: "
    )
    while True:
        model = input_func(prompt).strip()
        if model:
            return model
        if preset.default_model is not None:
            return preset.default_model
        output_func("Model is required.")


def prompt_for_destination(
    *,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> OnboardingDestination:
    destinations = (
        ("Quick Learn a file", OnboardingDestination.QUICK_LEARN),
        ("Start the vim starter course", OnboardingDestination.VIM_STARTER),
        ("Open the menu", OnboardingDestination.MENU),
    )
    output_func("What would you like to do first?")
    for index, (label, _destination) in enumerate(destinations, start=1):
        output_func(f"  {index}. {label}")

    while True:
        choice = input_func("Choose [1]: ").strip() or "1"
        if choice.isdigit() and 1 <= int(choice) <= len(destinations):
            return destinations[int(choice) - 1][1]
        output_func(f"Choose an option from 1 to {len(destinations)}.")


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


def persist_configuration(
    api_key: str,
    model: str,
    base_url: str,
    *,
    set_key_func: ConfigCommand | None = None,
    set_model_func: ConfigCommand | None = None,
    set_base_url_func: ConfigCommand | None = None,
) -> None:
    if set_key_func is None or set_model_func is None or set_base_url_func is None:
        from openlearn import cli

        set_key_func = set_key_func or cli.cmd_config_set_key
        set_model_func = set_model_func or cli.cmd_config_set_model
        set_base_url_func = set_base_url_func or cli.cmd_config_set_base_url

    if api_key:
        set_key_func(Namespace(api_key=api_key))
    set_model_func(Namespace(model=model))
    set_base_url_func(Namespace(base_url=base_url))


def launch_destination(
    destination: OnboardingDestination,
    *,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> None:
    if destination is OnboardingDestination.MENU:
        return

    from openlearn import cli

    if destination is OnboardingDestination.QUICK_LEARN:
        source = input_func("File to learn: ").strip()
        if not source:
            output_func("No file selected. Opening the menu.")
            return
        context = cli.read_pending_context(Path(source), output_func)
        topic_name = Path(context.filename).stem.replace("-", " ").replace("_", " ").strip()
        cli.cmd_new(
            Namespace(
                topic=topic_name or "Quick Learn",
                goal=f"Learn from {context.filename}",
                mastery_profile=None,
                template=None,
            ),
            output_func=output_func,
        )
        active_topic = cli.get_active_topic()
        if active_topic is None:
            output_func("Could not create the Quick Learn course.")
            return
        saved = cli.write_context_text(
            active_topic,
            context.filename,
            context.text,
        )
        cli.summarize_pending_contexts(active_topic, [saved], output_func)
    else:
        cli.cmd_new(
            Namespace(
                topic="Vim",
                goal="",
                mastery_profile=None,
                template="vim",
            ),
            output_func=output_func,
        )

    cli.start_course(input_func=input_func, output_func=output_func)
    if not cli.active_topic_needs_course_start(cli.get_active_topic()):
        cli.run_repl(input_func=input_func, output_func=output_func, show_intro=False)


def run_onboarding(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    output_func("Welcome to openlearn.")
    preset = prompt_for_provider(input_func=input_func, output_func=output_func)
    base_url = prompt_for_base_url(
        preset,
        input_func=input_func,
        output_func=output_func,
    )
    api_key = prompt_for_validated_key(
        preset,
        base_url,
        input_func=input_func,
        output_func=output_func,
    )
    if api_key is None:
        return False

    model = prompt_for_model(
        preset,
        input_func=input_func,
        output_func=output_func,
    )
    persist_configuration(api_key, model, base_url)
    destination = prompt_for_destination(
        input_func=input_func,
        output_func=output_func,
    )
    launch_destination(
        destination,
        input_func=input_func,
        output_func=output_func,
    )
    return True
