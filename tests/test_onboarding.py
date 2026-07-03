from __future__ import annotations

import sys
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
