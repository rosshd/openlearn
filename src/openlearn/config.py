from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from platformdirs import user_data_dir

from openlearn.constants import CONFIG_FILE, DEFAULT_BASE_URL, DEFAULT_MODEL
from openlearn.home_lock import home_lifecycle_lock


class ConfigError(ValueError):
    """Raised when the local openlearn configuration is malformed."""


def _select_lock_primitives():
    if os.name == "nt":
        from msvcrt import LK_LOCK, LK_UNLCK, locking

        def lock(handle) -> None:
            while True:
                try:
                    handle.seek(0)
                    locking(handle.fileno(), LK_LOCK, 1)
                    return
                except OSError:
                    time.sleep(0.05)

        def unlock(handle) -> None:
            handle.seek(0)
            locking(handle.fileno(), LK_UNLCK, 1)

    else:
        import fcntl

        def lock(handle) -> None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

        def unlock(handle) -> None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    return lock, unlock


_LOCK, _UNLOCK = _select_lock_primitives()
_LOCK_DEPTHS = threading.local()


@contextmanager
def _config_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    key = str(lock_path.resolve())
    depths = getattr(_LOCK_DEPTHS, "values", None)
    if depths is None:
        depths = {}
        _LOCK_DEPTHS.values = depths
    if depths.get(key, 0):
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
        return
    with lock_path.open("w", encoding="utf-8") as handle:
        _LOCK(handle)
        depths[key] = 1
        try:
            yield
        finally:
            depths.pop(key, None)
            _UNLOCK(handle)


@contextmanager
def _config_lock(path: Path):
    with home_lifecycle_lock(path.parent), _config_file_lock(path):
        yield


@dataclass(frozen=True)
class ProviderCredentials:
    """Effective provider values for trusted application services only."""

    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)
    verified: bool = False


@dataclass(frozen=True)
class ProviderStatus:
    """Secret-free provider state suitable for terminal or HTTP responses."""

    base_url: str
    model: str
    key_required: bool
    key_configured: bool
    verified: bool
    managed_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "key_required": self.key_required,
            "key_configured": self.key_configured,
            "verified": self.verified,
            "managed_fields": list(self.managed_fields),
        }


def project_home(
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    configured = env.get("OPENLEARN_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    current = (Path.cwd() if cwd is None else cwd).resolve()
    if (current / "learning-topics").exists():
        return current
    return Path(user_data_dir("openlearn", appauthor=False)).expanduser().resolve()


def config_path(*, home: Path | None = None, environ: Mapping[str, str] | None = None) -> Path:
    return (project_home(environ=environ) if home is None else home.resolve()) / CONFIG_FILE


def read_config(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Read configuration without a process cache so other interfaces stay visible."""
    path = config_path(home=home, environ=environ)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"invalid config file: {path}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"invalid config file: {path}: expected object")
    return dict(raw)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            temporary = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass


def write_config(
    values: Mapping[str, object],
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> None:
    path = config_path(home=home, environ=environ)
    with _config_lock(path):
        _write_text_atomic(path, json.dumps(dict(values), indent=2, sort_keys=True) + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass


def update_config(
    values: Mapping[str, object],
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    path = config_path(home=home, environ=environ)
    with _config_lock(path):
        updated = read_config(home=home, environ=environ)
        updated.update(values)
        write_config(updated, home=home, environ=environ)
    return updated


def mutate_config(
    mutation: Callable[[dict[str, object]], None],
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    recover_malformed: bool = False,
) -> dict[str, object]:
    """Apply one atomic read-modify-write operation to local configuration."""
    path = config_path(home=home, environ=environ)
    with _config_lock(path):
        try:
            updated = read_config(home=home, environ=environ)
        except ConfigError:
            if not recover_malformed:
                raise
            updated = {}
        mutation(updated)
        write_config(updated, home=home, environ=environ)
    return updated


def base_url_allows_keyless_requests(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


def effective_provider_credentials(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProviderCredentials:
    env = os.environ if environ is None else environ
    saved = read_config(home=home, environ=env)
    raw_base_url = env.get("OPENLEARN_BASE_URL") or saved.get("base_url") or DEFAULT_BASE_URL
    raw_model = env.get("OPENLEARN_MODEL") or saved.get("model") or DEFAULT_MODEL
    raw_key = env.get("OPENAI_API_KEY") or saved.get("openai_api_key") or saved.get("api_key")
    if not isinstance(raw_base_url, str) or not raw_base_url.strip():
        raise ConfigError("provider base URL must be a non-empty string")
    if not isinstance(raw_model, str) or not raw_model.strip():
        raise ConfigError("provider model must be a non-empty string")
    api_key = raw_key.strip() if isinstance(raw_key, str) and raw_key.strip() else None
    verified = bool(saved.get("provider_verified"))
    if any(env.get(name) for name in ("OPENLEARN_BASE_URL", "OPENLEARN_MODEL", "OPENAI_API_KEY")):
        # Environment-managed credentials cannot inherit a validation assertion made
        # for a different saved configuration.
        verified = bool(env.get("OPENLEARN_PROVIDER_VERIFIED") in {"1", "true", "yes"})
    return ProviderCredentials(
        base_url=raw_base_url.strip().rstrip("/"),
        model=raw_model.strip(),
        api_key=api_key,
        verified=verified,
    )


def configured_model(
    config: Mapping[str, object] | None = None,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    env = os.environ if environ is None else environ
    value = env.get("OPENLEARN_MODEL") or (config or read_config(home=home, environ=env)).get(
        "model"
    )
    return value if isinstance(value, str) and value else DEFAULT_MODEL


def configured_extractor_model(
    tutor_model: str,
    config: Mapping[str, object] | None = None,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    env = os.environ if environ is None else environ
    value = env.get("OPENLEARN_EXTRACTOR_MODEL") or (
        config or read_config(home=home, environ=env)
    ).get("extractor_model")
    return value if isinstance(value, str) and value else tutor_model


def configured_base_url(
    config: Mapping[str, object] | None = None,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    env = os.environ if environ is None else environ
    value = env.get("OPENLEARN_BASE_URL") or (
        config or read_config(home=home, environ=env)
    ).get("base_url")
    return value.rstrip("/") if isinstance(value, str) and value else DEFAULT_BASE_URL


def configured_openai_api_key(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    return effective_provider_credentials(home=home, environ=environ).api_key


def provider_is_configured(
    config: Mapping[str, object] | None = None,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    require_verified: bool = False,
) -> bool:
    env = os.environ if environ is None else environ
    if config is None:
        credentials = effective_provider_credentials(home=home, environ=env)
    else:
        saved_key = config.get("openai_api_key") or config.get("api_key")
        api_key = env.get("OPENAI_API_KEY") or (
            saved_key if isinstance(saved_key, str) else None
        )
        credentials = ProviderCredentials(
            base_url=configured_base_url(config, home=home, environ=env),
            model=configured_model(config, home=home, environ=env),
            api_key=api_key,
            verified=bool(config.get("provider_verified")),
        )
    credentials_present = bool(credentials.api_key) or base_url_allows_keyless_requests(
        credentials.base_url
    )
    return credentials_present and (credentials.verified or not require_verified)


def clear_config_cache() -> None:
    """Compatibility no-op: configuration reads are intentionally uncached."""


def provider_status(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProviderStatus:
    env = os.environ if environ is None else environ
    credentials = effective_provider_credentials(home=home, environ=env)
    managed = tuple(
        field
        for field, name in (
            ("base_url", "OPENLEARN_BASE_URL"),
            ("model", "OPENLEARN_MODEL"),
            ("api_key", "OPENAI_API_KEY"),
        )
        if env.get(name)
    )
    return ProviderStatus(
        base_url=credentials.base_url,
        model=credentials.model,
        key_required=not base_url_allows_keyless_requests(credentials.base_url),
        key_configured=credentials.api_key is not None,
        verified=credentials.verified,
        managed_fields=managed,
    )


def save_provider_configuration(
    *,
    base_url: str,
    model: str,
    api_key: str | None,
    verified: bool,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProviderStatus:
    normalized_url = base_url.strip().rstrip("/")
    if not normalized_url.startswith(("http://", "https://")):
        raise ConfigError("provider base URL must start with http:// or https://")
    if not model.strip():
        raise ConfigError("provider model is required")
    if not api_key and not base_url_allows_keyless_requests(normalized_url):
        raise ConfigError("an API key is required for a non-local provider")
    values: dict[str, object] = {
        "base_url": normalized_url,
        "model": model.strip(),
        "provider_verified": bool(verified),
    }
    path = config_path(home=home, environ=environ)
    with _config_lock(path):
        try:
            existing = read_config(home=home, environ=environ)
        except ConfigError:
            existing = {}
        existing.pop("openai_api_key", None)
        existing.pop("api_key", None)
        if api_key:
            existing["openai_api_key"] = api_key.strip()
        existing.update(values)
        write_config(existing, home=home, environ=environ)
    return provider_status(home=home, environ=environ)
