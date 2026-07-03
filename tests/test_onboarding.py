from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openlearn import cli, onboarding


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


class ProviderPromptTests(unittest.TestCase):
    def test_defaults_to_openai_and_uses_its_declared_settings(self) -> None:
        output: list[str] = []
        preset = onboarding.prompt_for_provider(
            input_func=lambda _prompt: "",
            output_func=output.append,
        )

        self.assertIs(preset, onboarding.PROVIDER_PRESETS["openai"])
        self.assertTrue(any("Anthropic-compatible" in line for line in output))
        self.assertEqual(
            onboarding.prompt_for_base_url(preset),
            "https://api.openai.com/v1",
        )
        self.assertEqual(
            onboarding.prompt_for_model(preset, input_func=lambda _prompt: ""),
            "gpt-4.1-mini",
        )

    def test_reprompts_invalid_provider_choice(self) -> None:
        choices = iter(["9", "3"])
        output: list[str] = []

        preset = onboarding.prompt_for_provider(
            input_func=lambda _prompt: next(choices),
            output_func=output.append,
        )

        self.assertIs(preset, onboarding.PROVIDER_PRESETS["ollama"])
        self.assertIn("Choose a provider from 1 to 4.", output)

    def test_custom_provider_requires_valid_base_url_and_model(self) -> None:
        base_urls = iter(["example.com/v1", " https://api.example.com/v1/ "])
        models = iter(["", "example-model"])
        output: list[str] = []
        preset = onboarding.PROVIDER_PRESETS["custom"]

        base_url = onboarding.prompt_for_base_url(
            preset,
            input_func=lambda _prompt: next(base_urls),
            output_func=output.append,
        )
        model = onboarding.prompt_for_model(
            preset,
            input_func=lambda _prompt: next(models),
            output_func=output.append,
        )

        self.assertEqual(base_url, "https://api.example.com/v1")
        self.assertEqual(model, "example-model")
        self.assertIn("Base URL must start with https:// or http://.", output)
        self.assertIn("Model is required.", output)


class DestinationPromptTests(unittest.TestCase):
    def test_defaults_to_quick_learn_and_lists_all_destinations(self) -> None:
        output: list[str] = []

        destination = onboarding.prompt_for_destination(
            input_func=lambda _prompt: "",
            output_func=output.append,
        )

        self.assertIs(destination, onboarding.OnboardingDestination.QUICK_LEARN)
        self.assertIn("  1. Quick Learn a file", output)
        self.assertIn("  2. Start the vim starter course", output)
        self.assertIn("  3. Open the menu", output)

    def test_reprompts_invalid_destination_choice(self) -> None:
        choices = iter(["4", "2"])
        output: list[str] = []

        destination = onboarding.prompt_for_destination(
            input_func=lambda _prompt: next(choices),
            output_func=output.append,
        )

        self.assertIs(destination, onboarding.OnboardingDestination.VIM_STARTER)
        self.assertIn("Choose an option from 1 to 3.", output)


class DestinationLaunchTests(unittest.TestCase):
    def test_quick_learn_imports_file_and_starts_lesson(self) -> None:
        context = cli.PendingContext("guide.md", "Guide contents\n")
        saved = Path("/tmp/guide.md")

        with (
            patch.object(cli, "read_pending_context", return_value=context) as read_context,
            patch.object(cli, "cmd_new") as cmd_new,
            patch.object(cli, "get_active_topic", return_value="guide"),
            patch.object(cli, "write_context_text", return_value=saved) as write_context,
            patch.object(cli, "summarize_pending_contexts") as summarize_contexts,
            patch.object(cli, "start_course") as start_course,
            patch.object(cli, "active_topic_needs_course_start", return_value=False),
            patch.object(cli, "run_repl") as run_repl,
        ):
            onboarding.launch_destination(
                onboarding.OnboardingDestination.QUICK_LEARN,
                input_func=lambda _prompt: "/tmp/guide.md",
                output_func=lambda _text: None,
            )

        read_context.assert_called_once_with(Path("/tmp/guide.md"), unittest.mock.ANY)
        new_args = cmd_new.call_args.args[0]
        self.assertEqual(new_args.topic, "guide")
        self.assertEqual(new_args.goal, "Learn from guide.md")
        write_context.assert_called_once_with("guide", "guide.md", "Guide contents\n")
        summarize_contexts.assert_called_once_with("guide", [saved], unittest.mock.ANY)
        start_course.assert_called_once()
        run_repl.assert_called_once()

    def test_vim_starter_uses_template_and_starts_lesson(self) -> None:
        with (
            patch.object(cli, "cmd_new") as cmd_new,
            patch.object(cli, "start_course") as start_course,
            patch.object(cli, "get_active_topic", return_value="vim"),
            patch.object(cli, "active_topic_needs_course_start", return_value=False),
            patch.object(cli, "run_repl") as run_repl,
        ):
            onboarding.launch_destination(
                onboarding.OnboardingDestination.VIM_STARTER,
                input_func=lambda _prompt: "",
                output_func=lambda _text: None,
            )

        new_args = cmd_new.call_args.args[0]
        self.assertEqual(new_args.topic, "Vim")
        self.assertEqual(new_args.template, "vim")
        start_course.assert_called_once()
        run_repl.assert_called_once()

    def test_menu_destination_defers_to_main_menu(self) -> None:
        with patch.object(cli, "cmd_new") as cmd_new:
            onboarding.launch_destination(onboarding.OnboardingDestination.MENU)

        cmd_new.assert_not_called()


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

    def test_omits_authorization_header_when_optional_key_is_empty(self) -> None:
        calls: list[tuple[object, int]] = []

        def opener(request: object, timeout: int) -> FakeResponse:
            calls.append((request, timeout))
            return FakeResponse(200)

        result = onboarding.validate_provider(
            "http://localhost:11434/v1",
            "",
            opener=opener,
        )

        self.assertEqual(result.status, onboarding.ValidationStatus.VALID)
        request, _timeout = calls[0]
        self.assertIsNone(request.get_header("Authorization"))

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


class ConfigurationPersistenceTests(unittest.TestCase):
    def test_uses_existing_config_commands_for_validated_settings(self) -> None:
        calls: list[tuple[str, object]] = []

        onboarding.persist_configuration(
            "secret-key",
            "model-name",
            "https://api.example.com/v1/",
            set_key_func=lambda args: calls.append(("key", args.api_key)) or 0,
            set_model_func=lambda args: calls.append(("model", args.model)) or 0,
            set_base_url_func=lambda args: calls.append(("base_url", args.base_url)) or 0,
        )

        self.assertEqual(
            calls,
            [
                ("key", "secret-key"),
                ("model", "model-name"),
                ("base_url", "https://api.example.com/v1/"),
            ],
        )

    def test_skips_key_command_when_optional_key_is_empty(self) -> None:
        calls: list[str] = []

        onboarding.persist_configuration(
            "",
            "llama3.1",
            "http://localhost:11434/v1",
            set_key_func=lambda _args: calls.append("key") or 0,
            set_model_func=lambda _args: calls.append("model") or 0,
            set_base_url_func=lambda _args: calls.append("base_url") or 0,
        )

        self.assertEqual(calls, ["model", "base_url"])


class OnboardingFlowTests(unittest.TestCase):
    def test_configures_selected_provider_in_step_order(self) -> None:
        calls: list[object] = []
        output: list[str] = []
        preset = onboarding.PROVIDER_PRESETS["openai"]

        with (
            patch.object(
                onboarding,
                "prompt_for_provider",
                side_effect=lambda **_kwargs: calls.append("provider") or preset,
            ),
            patch.object(
                onboarding,
                "prompt_for_base_url",
                side_effect=lambda selected, **_kwargs: (
                    calls.append(("base_url", selected))
                    or "https://api.openai.com/v1"
                ),
            ),
            patch.object(
                onboarding,
                "prompt_for_validated_key",
                side_effect=lambda selected, base_url, **_kwargs: (
                    calls.append(("key", selected, base_url)) or "secret-key"
                ),
            ),
            patch.object(
                onboarding,
                "prompt_for_model",
                side_effect=lambda selected, **_kwargs: (
                    calls.append(("model", selected)) or "gpt-4.1-mini"
                ),
            ),
            patch.object(
                onboarding,
                "persist_configuration",
                side_effect=lambda key, model, base_url: calls.append(
                    ("persist", key, model, base_url)
                ),
            ),
            patch.object(
                onboarding,
                "prompt_for_destination",
                side_effect=lambda **_kwargs: (
                    calls.append("destination")
                    or onboarding.OnboardingDestination.QUICK_LEARN
                ),
            ),
            patch.object(
                onboarding,
                "launch_destination",
                side_effect=lambda destination, **_kwargs: calls.append(
                    ("launch", destination)
                ),
            ),
        ):
            ready = onboarding.run_onboarding(
                input_func=lambda _prompt: "",
                output_func=output.append,
            )

        self.assertTrue(ready)
        self.assertEqual(output, ["Welcome to openlearn."])
        self.assertEqual(
            calls,
            [
                "provider",
                ("base_url", preset),
                ("key", preset, "https://api.openai.com/v1"),
                ("model", preset),
                (
                    "persist",
                    "secret-key",
                    "gpt-4.1-mini",
                    "https://api.openai.com/v1",
                ),
                "destination",
                ("launch", onboarding.OnboardingDestination.QUICK_LEARN),
            ],
        )

    def test_stops_without_model_or_persistence_when_validation_fails(self) -> None:
        preset = onboarding.PROVIDER_PRESETS["openai"]

        with (
            patch.object(onboarding, "prompt_for_provider", return_value=preset),
            patch.object(
                onboarding,
                "prompt_for_base_url",
                return_value="https://api.openai.com/v1",
            ),
            patch.object(onboarding, "prompt_for_validated_key", return_value=None),
            patch.object(onboarding, "prompt_for_model") as prompt_for_model,
            patch.object(onboarding, "persist_configuration") as persist_configuration,
            patch.object(onboarding, "prompt_for_destination") as prompt_for_destination,
            patch.object(onboarding, "launch_destination") as launch_destination,
        ):
            ready = onboarding.run_onboarding(output_func=lambda _text: None)

        self.assertFalse(ready)
        prompt_for_model.assert_not_called()
        persist_configuration.assert_not_called()
        prompt_for_destination.assert_not_called()
        launch_destination.assert_not_called()


class OnboardingTriggerTests(unittest.TestCase):
    def test_bare_invocation_runs_onboarding_before_menu_when_unconfigured(self) -> None:
        calls: list[str] = []

        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(cli, "configured_openai_api_key", return_value=None),
            patch.object(
                onboarding,
                "run_onboarding",
                side_effect=lambda: calls.append("onboarding") or True,
            ),
            patch.object(cli, "run_menu", side_effect=lambda: calls.append("menu") or 0),
        ):
            exit_code = cli.main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, ["onboarding", "menu"])

    def test_configured_environment_skips_onboarding(self) -> None:
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "env-key"}, clear=True),
            patch.object(onboarding, "run_onboarding") as run_onboarding,
            patch.object(cli, "run_menu", return_value=0),
        ):
            exit_code = cli.main([])

        self.assertEqual(exit_code, 0)
        run_onboarding.assert_not_called()

    def test_keyless_local_environment_skips_onboarding(self) -> None:
        with (
            patch.dict(
                "os.environ", {"OPENLEARN_BASE_URL": "http://localhost:11434/v1"}, clear=True
            ),
            patch.object(onboarding, "run_onboarding") as run_onboarding,
            patch.object(cli, "run_menu", return_value=0),
        ):
            exit_code = cli.main([])

        self.assertEqual(exit_code, 0)
        run_onboarding.assert_not_called()

    def test_keyless_local_config_skips_onboarding(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(cli, "configured_openai_api_key", return_value=None),
            patch.object(cli, "configured_base_url", return_value="http://localhost:11434/v1"),
            patch.object(onboarding, "run_onboarding") as run_onboarding,
            patch.object(cli, "run_menu", return_value=0),
        ):
            exit_code = cli.main([])

        self.assertEqual(exit_code, 0)
        run_onboarding.assert_not_called()

    def test_remote_environment_without_key_runs_onboarding(self) -> None:
        calls: list[str] = []

        with (
            patch.dict(
                "os.environ", {"OPENLEARN_BASE_URL": "https://api.example.com/v1"}, clear=True
            ),
            patch.object(
                onboarding,
                "run_onboarding",
                side_effect=lambda: calls.append("onboarding") or True,
            ),
            patch.object(cli, "run_menu", side_effect=lambda: calls.append("menu") or 0),
        ):
            exit_code = cli.main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, ["onboarding", "menu"])

    def test_mock_environment_skips_onboarding(self) -> None:
        with (
            patch.dict("os.environ", {"OPENLEARN_MOCK": "1"}, clear=True),
            patch.object(cli, "configured_openai_api_key", return_value=None),
            patch.object(onboarding, "run_onboarding") as run_onboarding,
            patch.object(cli, "run_menu", return_value=0),
        ):
            exit_code = cli.main([])

        self.assertEqual(exit_code, 0)
        run_onboarding.assert_not_called()

    def test_failed_onboarding_exits_before_menu(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(cli, "configured_openai_api_key", return_value=None),
            patch.object(onboarding, "run_onboarding", return_value=False),
            patch.object(cli, "run_menu") as run_menu,
        ):
            exit_code = cli.main([])

        self.assertEqual(exit_code, 1)
        run_menu.assert_not_called()


if __name__ == "__main__":
    unittest.main()
