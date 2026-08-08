from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("OPENLEARN_BROWSER_TEST") != "1",
    reason="set OPENLEARN_BROWSER_TEST=1 after installing a Playwright browser",
)


def _free_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until_ready(url: str, process: subprocess.Popen[bytes], home: Path) -> tuple[str, str]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"openlearn web exited before startup with {process.returncode}")
        try:
            record = json.loads((home / ".web-server.json").read_text(encoding="utf-8"))
            request = Request(
                f"{url}/health",
                headers={"X-Openlearn-Capability": record["access_token"]},
            )
            with urlopen(request, timeout=0.25) as response:
                if response.status == 200:
                    return (
                        f"{url}/?access_token={record['access_token']}",
                        f"{url}{record['url_namespace']}",
                    )
        except (OSError, json.JSONDecodeError, KeyError):
            time.sleep(0.05)
    raise AssertionError("openlearn web did not start within 15 seconds")


def _revision(page) -> int:
    return int(page.locator("[data-focus-shell]").get_attribute("data-revision"))


def _wait_for_new_revision(page, previous: int) -> None:
    page.wait_for_function(
        "previous => Number(document.querySelector('[data-focus-shell]').dataset.revision) > previous",
        arg=previous,
    )


def test_real_browser_course_polling_theme_conflict_and_keyboard_submit(
    tmp_path: Path,
) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    home = tmp_path / "openlearn-home"
    environment = {
        **os.environ,
        "OPENLEARN_HOME": str(home),
        "OPENLEARN_MOCK": "1",
    }
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENLEARN_API_KEY",
        "OPENLEARN_BASE_URL",
        "OPENLEARN_MODEL",
        "OPENLEARN_PROVIDER",
    ):
        environment.pop(name, None)
    command = f"from openlearn.web.launcher import run; run(port={port}, open_browser=False)"
    log_path = tmp_path / "openlearn-web.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", command],
            cwd=tmp_path,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            bootstrap_url, app_url = _wait_until_ready(base_url, process, home)
            with playwright.sync_playwright() as runtime:
                browser = runtime.chromium.launch()
                context = browser.new_context()
                first = context.new_page()
                first.goto(bootstrap_url)
                first.goto(f"{app_url}/setup")
                provider = first.locator("#provider")
                model = first.locator("#model")
                base_url_field = first.locator("#base-url")
                expected_presets = {
                    "openrouter": (
                        "google/gemini-2.5-flash-lite",
                        "https://openrouter.ai/api/v1",
                    ),
                    "openai": ("gpt-4.1-mini", "https://api.openai.com/v1"),
                    "anthropic-compatible": ("", ""),
                    "ollama": ("llama3.1", "http://localhost:11434/v1"),
                    "custom": ("", ""),
                }
                for preset, (expected_model, expected_base_url) in expected_presets.items():
                    provider.select_option(preset)
                    assert model.input_value() == expected_model
                    assert base_url_field.input_value() == expected_base_url

                provider.select_option("custom")
                model.fill("my-deliberate-model")
                base_url_field.fill("http://localhost:9911/v1")
                provider.select_option("openai")
                assert model.input_value() == "gpt-4.1-mini"
                assert base_url_field.input_value() == "https://api.openai.com/v1"
                provider.select_option("custom")
                assert model.input_value() == "my-deliberate-model"
                assert base_url_field.input_value() == "http://localhost:9911/v1"

                first.goto(f"{app_url}/courses/new")

                first.locator('[data-template-id="technical-interview-prep"]').click()
                first.locator("#experience").fill(
                    "I know basic Python and want interview practice."
                )
                first.get_by_role("button", name="Build the first lesson").click()
                initial_revision = _revision(first)
                focus_url = first.url

                theme = first.locator("[data-theme-toggle]")
                theme.click()
                assert first.locator("html").get_attribute("data-theme") == "light"
                first.reload()
                assert first.locator("html").get_attribute("data-theme") == "light"

                stale = context.new_page()
                stale.goto(focus_url)
                assert _revision(stale) == initial_revision

                first.locator("#learner-response").fill("I would start with a hash map.")
                with first.expect_response(
                    lambda response: response.url.endswith("/turns")
                ) as saved:
                    first.locator("#learner-response").press("Control+Enter")
                saved_response = saved.value
                assert saved_response.status == 202
                saved_body = saved_response.json()
                assert saved_body["state"] == "saved"
                assert saved_body["operation_id"]
                _wait_for_new_revision(first, initial_revision)

                stale.locator("#learner-response").fill("This tab still has an old revision.")
                with stale.expect_response(
                    lambda response: response.url.endswith("/turns")
                ) as conflict:
                    stale.locator("#learner-response").press("Control+Enter")
                assert conflict.value.status == 409
                assert "course changed" in stale.locator("[data-operation-state]").inner_text()

                stale.reload()
                refreshed_revision = _revision(stale)
                assert refreshed_revision > initial_revision
                stale.locator("#learner-response").fill("After refreshing, keyboard submit works.")
                with stale.expect_response(
                    lambda response: response.url.endswith("/turns")
                ) as retried:
                    stale.locator("#learner-response").press("Control+Enter")
                assert retried.value.status == 202
                _wait_for_new_revision(stale, refreshed_revision)
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_real_browser_unverified_provider_stays_in_setup(
    tmp_path: Path,
) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    home = tmp_path / "unverified-home"
    environment = {
        **os.environ,
        "OPENLEARN_HOME": str(home),
    }
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENLEARN_API_KEY",
        "OPENLEARN_BASE_URL",
        "OPENLEARN_MOCK",
        "OPENLEARN_MODEL",
        "OPENLEARN_PROVIDER",
    ):
        environment.pop(name, None)
    command = f"from openlearn.web.launcher import run; run(port={port}, open_browser=False)"
    log_path = tmp_path / "openlearn-unverified-web.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", command],
            cwd=tmp_path,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            bootstrap_url, app_url = _wait_until_ready(base_url, process, home)
            with playwright.sync_playwright() as runtime:
                browser = runtime.chromium.launch()
                page = browser.new_page()
                page.goto(bootstrap_url)
                page.goto(f"{app_url}/setup")
                page.locator("#provider").select_option("custom")
                page.locator("#api-key").fill("browser-secret-must-clear")
                page.locator("#model").fill("offline-model")
                page.locator("#base-url").fill("http://127.0.0.1:1/v1")
                page.locator('input[name="save_unverified"]').check()
                page.get_by_role("button", name="Test and save").click()

                playwright.expect(page.locator("[data-form-status]")).to_contain_text(
                    "teaching stays locked"
                )
                assert page.url.endswith("/setup")
                assert page.locator("#api-key").input_value() == ""
                assert not page.locator('input[name="save_unverified"]').is_checked()

                page.goto(f"{app_url}/courses/new")
                assert page.url.endswith("/setup")
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
