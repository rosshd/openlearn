from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openlearn import onboarding


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status


class ProviderPresetTests(unittest.TestCase):
    def test_presets_capture_first_run_provider_defaults(self) -> None:
        self.assertEqual(
            onboarding.PROVIDER_PRESETS["openai"],
            onboarding.ProviderPreset(
                name="OpenAI",
                base_url="https://api.openai.com/v1",
                default_model="gpt-4.1-mini",
                key_required=True,
            ),
        )
        self.assertEqual(
            onboarding.PROVIDER_PRESETS["ollama"],
            onboarding.ProviderPreset(
                name="Ollama",
                base_url="http://localhost:11434/v1",
                default_model="llama3.1",
                key_required=False,
            ),
        )
        self.assertIsNone(onboarding.PROVIDER_PRESETS["anthropic-compatible"].base_url)
        self.assertIsNone(onboarding.PROVIDER_PRESETS["custom"].default_model)


class ProviderValidationTests(unittest.TestCase):
    def test_gets_models_with_bearer_key_and_ten_second_timeout(self) -> None:
        calls: list[tuple[object, int]] = []

        def opener(request: object, timeout: int) -> FakeResponse:
            calls.append((request, timeout))
            return FakeResponse(200)

        result = onboarding.validate_provider(
            "https://api.example.com/v1/",
            "secret-key",
            opener=opener,
        )

        self.assertEqual(result.status, onboarding.ValidationStatus.VALID)
        request, timeout = calls[0]
        self.assertEqual(request.full_url, "https://api.example.com/v1/models")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")
        self.assertEqual(timeout, 10)

    def test_maps_unauthorized_and_forbidden_to_rejected(self) -> None:
        for status in (401, 403):
            with self.subTest(status=status):

                def opener(request: object, timeout: int) -> FakeResponse:
                    raise HTTPError(
                        request.full_url,
                        status,
                        "rejected",
                        hdrs=None,
                        fp=None,
                    )

                result = onboarding.validate_provider(
                    "https://api.example.com/v1",
                    "bad-key",
                    opener=opener,
                )

                self.assertEqual(result.status, onboarding.ValidationStatus.REJECTED)

    def test_maps_connection_failure_to_network_error(self) -> None:
        def opener(_request: object, timeout: int) -> FakeResponse:
            raise URLError("connection refused")

        result = onboarding.validate_provider(
            "http://localhost:11434/v1",
            "",
            opener=opener,
        )

        self.assertEqual(result.status, onboarding.ValidationStatus.NETWORK_ERROR)
        self.assertIn("connection refused", result.detail)


class ValidatedKeyPromptTests(unittest.TestCase):
    def test_reads_valid_key_with_hidden_getpass_prompt(self) -> None:
        with patch.object(onboarding.getpass, "getpass", return_value="secret-key") as hidden_input:
            result = onboarding.prompt_for_validated_key(
                onboarding.PROVIDER_PRESETS["openai"],
                "https://api.openai.com/v1",
                output_func=lambda _text: None,
                validator=lambda _base_url, _api_key: onboarding.ValidationResult(
                    onboarding.ValidationStatus.VALID
                ),
            )

        self.assertEqual(result, "secret-key")
        hidden_input.assert_called_once_with("API key (hidden): ")

    def test_retries_rejected_key_three_times_then_exits_cleanly(self) -> None:
        keys = iter(["bad-one", "bad-two", "bad-three"])
        output: list[str] = []
        validation_calls: list[tuple[str, str]] = []

        def validator(base_url: str, api_key: str) -> onboarding.ValidationResult:
            validation_calls.append((base_url, api_key))
            return onboarding.ValidationResult(onboarding.ValidationStatus.REJECTED)

        result = onboarding.prompt_for_validated_key(
            onboarding.PROVIDER_PRESETS["openai"],
            "https://api.openai.com/v1",
            key_input_func=lambda _prompt: next(keys),
            output_func=output.append,
            validator=validator,
        )

        self.assertIsNone(result)
        self.assertEqual(
            validation_calls,
            [
                ("https://api.openai.com/v1", "bad-one"),
                ("https://api.openai.com/v1", "bad-two"),
                ("https://api.openai.com/v1", "bad-three"),
            ],
        )
        self.assertEqual(output.count("Key rejected by provider."), 3)
        self.assertIn("Key rejected after 3 attempts", output[-1])

    def test_network_error_can_save_ollama_without_a_key(self) -> None:
        prompts: list[str] = []
        output: list[str] = []

        def key_input(prompt: str) -> str:
            prompts.append(prompt)
            return ""

        result = onboarding.prompt_for_validated_key(
            onboarding.PROVIDER_PRESETS["ollama"],
            "http://localhost:11434/v1",
            input_func=lambda _prompt: "yes",
            key_input_func=key_input,
            output_func=output.append,
            validator=lambda _base_url, _api_key: onboarding.ValidationResult(
                onboarding.ValidationStatus.NETWORK_ERROR,
                "connection refused",
            ),
        )

        self.assertEqual(result, "")
        self.assertEqual(prompts, ["API key (optional, hidden): "])
        self.assertTrue(any("connection refused" in line for line in output))


if __name__ == "__main__":
    unittest.main()
