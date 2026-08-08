from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from openlearn.config import (
    ConfigError,
    effective_provider_credentials,
    provider_status,
    read_config,
    save_provider_configuration,
    write_config,
)


def test_provider_configuration_precedence_and_uncached_reads(tmp_path: Path) -> None:
    write_config(
        {
            "base_url": "https://saved.example/v1",
            "model": "saved-model",
            "openai_api_key": "saved-secret",
        },
        home=tmp_path,
    )
    environment = {
        "OPENLEARN_BASE_URL": "https://env.example/v1/",
        "OPENLEARN_MODEL": "env-model",
        "OPENAI_API_KEY": "env-secret",
    }
    credentials = effective_provider_credentials(home=tmp_path, environ=environment)
    assert credentials.base_url == "https://env.example/v1"
    assert credentials.model == "env-model"
    assert credentials.api_key == "env-secret"
    assert "env-secret" not in repr(credentials)

    (tmp_path / "config.json").write_text('{"model": "changed"}\n', encoding="utf-8")
    assert read_config(home=tmp_path)["model"] == "changed"


def test_provider_status_is_serializable_without_a_key(tmp_path: Path) -> None:
    save_provider_configuration(
        base_url="https://openrouter.ai/api/v1",
        model="google/gemini-2.5-flash-lite",
        api_key="sk-or-secret",
        verified=True,
        home=tmp_path,
        environ={},
    )
    status = provider_status(home=tmp_path, environ={})
    serialized = json.dumps(status.as_dict())
    assert status.key_configured is True
    assert status.key_required is True
    assert status.verified is True
    assert "sk-or-secret" not in serialized
    assert "api_key" not in status.as_dict()


def test_provider_save_is_whole_atomic_private_and_preserves_unknown_values(
    tmp_path: Path,
) -> None:
    write_config({"editor": ["nvim"], "api_key": "old"}, home=tmp_path)
    save_provider_configuration(
        base_url="http://localhost:11434/v1",
        model="llama3.1",
        api_key=None,
        verified=True,
        home=tmp_path,
        environ={},
    )
    saved = read_config(home=tmp_path)
    assert saved["editor"] == ["nvim"]
    assert "api_key" not in saved
    assert "openai_api_key" not in saved
    assert stat.S_IMODE((tmp_path / "config.json").stat().st_mode) == 0o600
    assert not list(tmp_path.glob("tmp*"))


def test_non_local_provider_requires_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="API key"):
        save_provider_configuration(
            base_url="https://provider.example/v1",
            model="model",
            api_key=None,
            verified=False,
            home=tmp_path,
            environ={},
        )


def test_environment_managed_status_marks_fields_without_exposing_values(tmp_path: Path) -> None:
    status = provider_status(
        home=tmp_path,
        environ={
            "OPENLEARN_BASE_URL": "http://localhost:11434/v1",
            "OPENLEARN_MODEL": "local-model",
        },
    )
    assert status.managed_fields == ("base_url", "model")
    assert status.key_required is False


def test_invalid_config_does_not_echo_file_contents(tmp_path: Path) -> None:
    secret = "never-echo-this"
    (tmp_path / "config.json").write_text(f'{{"api_key": "{secret}"', encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        read_config(home=tmp_path)
    assert secret not in str(caught.value)


def test_valid_provider_setup_replaces_malformed_config(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text('{"api_key": "broken"', encoding="utf-8")

    status = save_provider_configuration(
        base_url="http://localhost:11434/v1",
        model="llama3.1",
        api_key=None,
        verified=True,
        home=tmp_path,
        environ={},
    )

    assert status.verified is True
    assert read_config(home=tmp_path) == {
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.1",
        "provider_verified": True,
    }
