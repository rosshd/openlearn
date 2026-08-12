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

from openlearn import cli, interview_prep


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


def _assert_no_page_overflow(page) -> None:
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


def test_real_browser_course_polling_theme_conflict_and_keyboard_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
                        "google/gemini-3.1-flash-lite",
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
                first.get_by_text("Advanced connection details", exact=True).click()
                base_url_field.fill("http://localhost:9911/v1")
                provider.select_option("openai")
                assert model.input_value() == "gpt-4.1-mini"
                assert base_url_field.input_value() == "https://api.openai.com/v1"
                provider.select_option("custom")
                assert model.input_value() == "my-deliberate-model"
                assert base_url_field.input_value() == "http://localhost:9911/v1"

                first.set_viewport_size({"width": 320, "height": 720})
                first.goto(f"{app_url}/courses/new")
                _assert_no_page_overflow(first)

                first.locator('[data-template-id="technical-interview-prep"]').click()
                first.locator("#course-title").focus()
                first.locator("#course-title").press("Enter")
                assert first.locator("#goal").evaluate("field => field === document.activeElement")
                first.locator("#goal").press("Enter")
                assert first.locator("#experience").evaluate("field => field === document.activeElement")
                first.locator("#experience").fill(
                    "I know basic Python and want interview practice."
                )
                first.locator("#experience").press("Enter")
                first.wait_for_url("**/courses/*/placement")
                assert "/placement" in first.url
                first.get_by_role("button", name="Start quick placement").click()
                playwright.expect(
                    first.get_by_role("heading", name="What are you preparing for?")
                ).to_be_visible()
                assert first.get_by_text("first_unique_window", exact=False).count() == 0
                _assert_no_page_overflow(first)
                first.locator('input[name="target_level"][value="senior"]').check()
                first.locator('input[name="interview_focus"][value="balanced"]').check()
                first.emulate_media(reduced_motion="reduce")
                first.get_by_role("button", name="Start rapid questions").click()
                seen_topics: set[str] = set()
                while first.locator("[data-confidence-complete]").is_hidden():
                    question = first.locator("[data-confidence-question]:visible")
                    topic_id = question.get_attribute("data-topic-id")
                    seen_topics.add(topic_id)
                    rating = 1 if topic_id == "sliding_window" else 5 if topic_id == "trees" else 3
                    question.locator(f'[data-confidence-rating="{rating}"]').click()
                assert "sliding_window" in seen_topics
                assert "capacity_estimation" in seen_topics
                first.get_by_role("button", name="Review or change answers").click()
                assert first.locator("[data-review-topic]:visible").count() == len(
                    interview_prep.confidence_topics_for_focus("balanced")
                )
                first.get_by_role("button", name="Build my course outline").click()
                playwright.expect(
                    first.get_by_role("heading", name="Your suggested course outline")
                ).to_be_visible()
                playwright.expect(
                    first.get_by_text("Requirements, Scale, and Interfaces", exact=True)
                ).to_be_visible()
                playwright.expect(first.get_by_text("Tutor feedback", exact=True)).to_have_count(0)
                playwright.expect(first.get_by_text("Workshop this outline", exact=True)).to_have_count(0)
                playwright.expect(first.get_by_role("button", name="Change course outline")).to_be_visible()
                assert first.url.endswith("/placement")
                _assert_no_page_overflow(first)
                first.get_by_role("button", name="Confirm course outline").click()
                first.wait_for_function(
                    "() => !window.location.pathname.endsWith('/placement')"
                )
                _assert_no_page_overflow(first)
                if "/initializing/" in first.url:
                    assert first.locator(".build-indicator span").first.evaluate(
                        "dot => getComputedStyle(dot).animationName === 'none'"
                    )
                first.locator("[data-focus-shell]").wait_for(state="visible")
                playwright.expect(first.get_by_text("Before writing code", exact=False)).to_be_visible()
                playwright.expect(first.get_by_text("Press Enter to continue", exact=True)).to_have_count(0)
                assert first.locator("#learner-response").count() == 0
                passive_revision = _revision(first)
                first.locator("body").press("Enter")
                _wait_for_new_revision(first, passive_revision)

                lesson_title = first.locator("#move-title").inner_text()
                first.get_by_role("button", name="Chat", exact=True).click()
                playwright.expect(first.locator('[data-tool-panel="chat"]')).to_be_visible()
                playwright.expect(first.locator(".focus-column")).to_be_visible()
                first.locator("#chat-question").fill("Can you explain that another way?")
                first.locator("#chat-question").press("Control+Enter")
                playwright.expect(first.locator(".chat-exchange")).to_have_count(1)
                playwright.expect(first.locator("#chat-question")).to_have_value("")
                assert first.locator("#move-title").inner_text() == lesson_title
                assert "tool=chat" in first.url
                first.get_by_role("button", name="Close learning tool").click()

                slug = first.locator("[data-focus-shell]").get_attribute("data-course-slug")
                monkeypatch.setenv("OPENLEARN_HOME", str(home))
                cli.clear_config_cache()
                topic = cli.read_topic(slug)
                check = "**Check:**\nWhich Vim mode runs commands like dd?"
                cli.append_session(topic, "lesson", "Browser response check", check)
                cli.save_pending_question(
                    cli.read_topic(slug),
                    check,
                    "",
                    question_text="Which Vim mode runs commands like dd?",
                )
                first.reload()
                composer_submit = first.locator("[data-composer-submit]")
                playwright.expect(composer_submit).to_contain_text("Send answer")
                first.emulate_media(reduced_motion="no-preference")
                first.set_viewport_size({"width": 1280, "height": 800})
                initial_revision = _revision(first)
                focus_url = first.url

                theme = first.locator("[data-theme-toggle]")
                theme.click()
                assert first.locator("html").get_attribute("data-theme") == "light"
                first.reload()
                assert first.locator("html").get_attribute("data-theme") == "light"

                first.locator("#learner-response").fill("Keep this unsent draft while tools open.")
                closed_lesson_x = first.locator(".focus-column").bounding_box()["x"]
                first.get_by_role("button", name="Code").click()
                assert "tool=code" in first.url
                first.wait_for_timeout(40)
                opening_lesson_x = first.locator(".focus-column").bounding_box()["x"]
                assert abs(opening_lesson_x - closed_lesson_x) < 40
                first.wait_for_function(
                    "() => !document.querySelector('[data-tool-surface]').dataset.motion"
                )
                shell_box = first.locator("[data-focus-shell]").bounding_box()
                lesson_box = first.locator(".focus-column").bounding_box()
                tool_box = first.locator("[data-tool-surface]").bounding_box()
                assert shell_box and shell_box["width"] >= 1180
                assert lesson_box and lesson_box["width"] >= 480
                assert tool_box and tool_box["width"] >= lesson_box["width"]
                _assert_no_page_overflow(first)
                first.set_viewport_size({"width": 1024, "height": 800})
                shell_box = first.locator("[data-focus-shell]").bounding_box()
                lesson_box = first.locator(".focus-column").bounding_box()
                tool_box = first.locator("[data-tool-surface]").bounding_box()
                assert shell_box and shell_box["width"] >= 950
                assert lesson_box and lesson_box["width"] >= 380
                assert tool_box and tool_box["width"] >= lesson_box["width"]
                _assert_no_page_overflow(first)
                first.set_viewport_size({"width": 800, "height": 800})
                assert not first.locator(".focus-column").is_visible()
                assert first.locator("[data-tool-surface]").bounding_box()["width"] >= 630
                _assert_no_page_overflow(first)
                first.set_viewport_size({"width": 1280, "height": 800})
                first.locator("[data-code-draft]").fill("print('browser workspace')\n")
                with first.expect_response(
                    lambda response: response.url.endswith("/tools/code")
                    and response.request.method == "POST"
                ) as code_saved:
                    first.get_by_role("button", name="Save", exact=True).click()
                assert code_saved.value.status == 200
                first.locator("[data-code-draft]").fill("print('unsaved draft')\n")
                first.once("dialog", lambda dialog: dialog.dismiss())
                first.get_by_role("button", name="Video").click()
                playwright.expect(first.locator('[data-tool-panel="code"]')).to_be_visible()
                assert first.locator("[data-code-draft]").input_value() == (
                    "print('unsaved draft')\n"
                )
                assert "tool=code" in first.url

                first.once("dialog", lambda dialog: dialog.accept())
                first.get_by_role("button", name="Video").click()
                playwright.expect(first.locator('[data-tool-panel="video"]')).to_be_visible()
                assert "tool=video" in first.url
                first.go_back()
                playwright.expect(first.locator('[data-tool-panel="code"]')).to_be_visible()
                playwright.expect(first.locator("[data-code-draft]")).to_have_value(
                    "print('browser workspace')\n"
                )
                first.go_forward()
                playwright.expect(first.locator('[data-tool-panel="video"]')).to_be_visible()
                first.go_back()
                playwright.expect(first.locator('[data-tool-panel="code"]')).to_be_visible()

                first.locator("[data-code-draft]").fill("print('close guard')\n")
                first.once("dialog", lambda dialog: dialog.dismiss())
                first.get_by_role("button", name="Close learning tool").click()
                playwright.expect(first.locator('[data-tool-panel="code"]')).to_be_visible()
                first.once("dialog", lambda dialog: dialog.accept())
                open_lesson_x = first.locator(".focus-column").bounding_box()["x"]
                first.get_by_role("button", name="Close learning tool").click()
                first.wait_for_timeout(40)
                closing_lesson_x = first.locator(".focus-column").bounding_box()["x"]
                assert abs(closing_lesson_x - open_lesson_x) < 40
                assert "tool=" not in first.url
                assert first.locator("[data-tool-surface]").get_attribute("aria-hidden") == "true"
                assert first.locator("[data-tool-surface]").get_attribute("inert") == ""
                assert first.get_by_role("button", name="Code").evaluate(
                    "button => button === document.activeElement"
                )
                assert first.locator("#learner-response").input_value() == (
                    "Keep this unsent draft while tools open."
                )

                first.get_by_role("button", name="Code").click()
                playwright.expect(first.locator('[data-tool-panel="code"]')).to_be_visible()
                first.wait_for_function(
                    "() => !document.querySelector('[data-tool-surface]').dataset.motion"
                )
                assert first.locator("[data-tool-surface]").get_attribute("aria-hidden") is None
                assert first.locator("[data-tool-surface]").get_attribute("inert") is None
                assert first.locator("[data-tool-surface]").get_attribute("data-motion") is None
                first.get_by_role("button", name="Close learning tool").click()
                progress_button = first.get_by_role("button", name="Progress", exact=True)
                progress_button.click()
                playwright.expect(first.locator("#progress-drawer")).to_be_visible()
                first.locator("body").press("Escape")
                playwright.expect(first.locator("#progress-drawer")).to_be_hidden()
                assert progress_button.get_attribute("aria-expanded") == "false"

                first.emulate_media(reduced_motion="reduce")
                first.get_by_role("button", name="Code").click()
                assert first.locator("[data-tool-surface]").get_attribute("data-motion") is None
                first.get_by_role("button", name="Close learning tool").click()
                assert first.locator("[data-tool-surface]").is_hidden()
                assert first.locator("[data-tool-surface]").get_attribute("data-motion") is None
                first.emulate_media(reduced_motion="no-preference")

                first.get_by_role("button", name="Video").click()
                first.locator("#video-url").fill("https://youtu.be/dQw4w9WgXcQ")
                first.get_by_role("button", name="Prepare video").click()
                playwright.expect(first.locator("[data-video-consent]")).to_be_visible()
                assert first.locator("[data-video-frame] iframe").count() == 0
                first.locator("#video-url").fill("https://example.com/not-youtube")
                playwright.expect(first.locator("[data-video-consent]")).to_be_hidden()
                assert first.locator("[data-video-frame] iframe").count() == 0
                first.get_by_role("button", name="Prepare video").click()
                playwright.expect(first.locator("[data-tool-status]")).to_contain_text(
                    "valid supported YouTube"
                )
                playwright.expect(first.locator("[data-video-consent]")).to_be_hidden()
                first.locator("#video-url").fill("https://youtu.be/dQw4w9WgXcQ")
                first.get_by_role("button", name="Prepare video").click()
                playwright.expect(first.locator("[data-video-consent]")).to_be_visible()
                context.route("https://www.youtube-nocookie.com/**", lambda route: route.abort())
                first.get_by_role("button", name="Load video").click()
                assert first.locator("[data-video-frame] iframe").count() == 1
                first.locator("#video-url").fill("https://youtu.be/abcdefghijk")
                playwright.expect(first.locator("[data-video-consent]")).to_be_hidden()
                assert first.locator("[data-video-frame] iframe").count() == 0
                first.get_by_role("button", name="Close learning tool").click()
                assert first.get_by_role("button", name="Video").evaluate(
                    "button => button === document.activeElement"
                )

                first.get_by_role("button", name="Sources").click()
                assert "tool=sources" in first.url
                first.reload()
                playwright.expect(first.locator('[data-tool-panel="sources"]')).to_be_visible()
                first.locator("#source-file").set_input_files(
                    {
                        "name": "browser-notes.md",
                        "mimeType": "text/markdown",
                        "buffer": b"# Browser source\n",
                    }
                )
                first.get_by_role("button", name="Import file").click()
                playwright.expect(first.locator("[data-source-results]")).to_contain_text(
                    "browser-notes.md"
                )
                first.set_viewport_size({"width": 320, "height": 720})
                _assert_no_page_overflow(first)
                navigation = first.get_by_role("navigation", name="Primary navigation")
                playwright.expect(navigation).to_be_visible()
                assert navigation.get_by_text("Learning", exact=True).is_visible()
                assert navigation.get_by_text("Practice", exact=True).is_visible()
                assert navigation.get_by_text("Settings", exact=True).is_visible()
                assert not first.locator(".focus-column").is_visible()
                first.get_by_role("button", name="Close learning tool").click()
                _assert_no_page_overflow(first)
                progress_button = first.get_by_role("button", name="Progress", exact=True)
                progress_button.focus()
                progress_button.press("Enter")
                assert progress_button.get_attribute("aria-expanded") == "true"
                playwright.expect(first.locator("#progress-drawer")).to_be_visible()
                playwright.expect(first.locator("#progress-drawer")).to_contain_text(
                    "Progress will appear after your first learning check."
                )
                assert first.locator("#progress-drawer progress").count() == 0
                first.emulate_media(reduced_motion="reduce")
                first.get_by_role("button", name="Close progress").press("Enter")
                assert progress_button.get_attribute("aria-expanded") == "false"
                assert first.locator("#progress-drawer").is_hidden()
                assert first.locator("#progress-drawer").get_attribute("data-motion") is None
                assert progress_button.evaluate(
                    "button => button === document.activeElement"
                )
                first.emulate_media(reduced_motion="no-preference")
                first.set_viewport_size({"width": 1280, "height": 800})

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
                playwright.expect(first.locator("[data-current-move]")).to_be_visible()

                stale.locator("#learner-response").fill("This tab still has an old revision.")
                with stale.expect_response(
                    lambda response: response.url.endswith("/turns")
                ) as conflict:
                    stale.locator("#learner-response").press("Control+Enter")
                assert conflict.value.status == 409
                assert "course changed" in stale.locator("[data-operation-state]").inner_text()

                retry_question = "What is the average lookup cost of a hash map?"
                topic = cli.read_topic(slug)
                retry_check = f"**Check:**\n{retry_question}"
                cli.append_session(topic, "lesson", "Retry check", retry_check)
                cli.save_pending_question(
                    cli.read_topic(slug),
                    retry_check,
                    "",
                    question_text=retry_question,
                )
                stale.reload()
                refreshed_revision = _revision(stale)
                assert refreshed_revision > initial_revision
                stale.locator("#learner-response").fill(
                    "Average lookup is constant time when hashing is well distributed."
                )
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
            cli.clear_config_cache()


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
                page.set_viewport_size({"width": 320, "height": 720})
                _assert_no_page_overflow(page)
                assert page.get_by_role("navigation", name="Primary navigation").is_visible()
                page.locator("#provider").select_option("custom")
                page.locator("#api-key").fill("browser-secret-must-clear")
                page.locator("#model").fill("offline-model")
                page.get_by_text("Advanced connection details", exact=True).click()
                page.locator("#base-url").fill("http://127.0.0.1:1/v1")
                page.get_by_role("button", name="Test and save").click()

                playwright.expect(page.locator("[data-form-error]")).to_contain_text(
                    "could not be reached"
                )
                assert page.locator("[data-form-error]").evaluate(
                    "summary => summary === document.activeElement"
                )
                assert page.url.endswith("/setup")
                assert page.locator("#api-key").input_value() == (
                    "browser-secret-must-clear"
                )
                assert page.locator("#model").input_value() == "offline-model"
                assert page.locator("#base-url").input_value() == "http://127.0.0.1:1/v1"

                page.locator('input[name="save_unverified"]').check()
                page.get_by_role("button", name="Test and save").click()
                playwright.expect(page.locator("[data-form-status]")).to_contain_text(
                    "teaching stays locked"
                )
                assert page.locator("#api-key").input_value() == ""
                assert not page.locator('input[name="save_unverified"]').is_checked()

                page.goto(f"{app_url}/courses/new")
                assert page.url.endswith("/courses/new")
                playwright.expect(
                    page.get_by_text("Technical Interview Prep", exact=True).first
                ).to_be_visible()
                _assert_no_page_overflow(page)
                for path in ("/dashboard", "/progress", "/data"):
                    page.goto(f"{app_url}{path}")
                    _assert_no_page_overflow(page)
                    assert page.get_by_role(
                        "navigation", name="Primary navigation"
                    ).is_visible()
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
