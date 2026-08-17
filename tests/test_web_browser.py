from __future__ import annotations

import json
import os
import argparse
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from openlearn import application, cli, interview_prep, tutor_service
from openlearn.web.services import OpenLearnWebServices


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


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


def _show_new_revision(page, previous: int) -> None:
    page.get_by_role("button", name="Show next lesson").click()
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
        "PYTHONPATH": str(SOURCE_ROOT),
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
                first.get_by_label("Senior+", exact=True).check(force=True)
                first.get_by_label("Coding + system design", exact=True).check(force=True)
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
                    first.get_by_text("Requirements and Interfaces", exact=True)
                ).to_be_visible()
                playwright.expect(first.get_by_text("Tutor feedback", exact=True)).to_have_count(0)
                playwright.expect(first.get_by_text("Workshop this outline", exact=True)).to_have_count(0)
                playwright.expect(first.get_by_role("button", name="Change course outline")).to_be_visible()
                first.get_by_role("button", name="Change course outline").click()
                first.get_by_label("Interview mix").select_option("coding")
                first.get_by_role("button", name="Preview changes").click()
                preview_heading = first.get_by_role(
                    "heading", name="Review changed course outline"
                )
                playwright.expect(preview_heading).to_be_visible()
                assert preview_heading.evaluate(
                    "heading => heading === document.activeElement"
                )
                playwright.expect(
                    first.get_by_text("Requirements and Interfaces", exact=True)
                ).to_have_count(0)
                playwright.expect(first.locator("[data-outline-list]")).to_contain_text(
                    "locked"
                )
                first.get_by_role("button", name="Keep current outline").click()
                playwright.expect(
                    first.get_by_text("Requirements and Interfaces", exact=True)
                ).to_be_visible()
                assert first.get_by_role(
                    "button", name="Change course outline"
                ).evaluate("button => button === document.activeElement")
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
                playwright.expect(
                    first.get_by_role("heading", name="Arrays and strings")
                ).to_be_visible()
                playwright.expect(first.get_by_text("Press Enter to continue", exact=True)).to_have_count(0)
                passive_revision = _revision(first)
                if first.locator("#learner-response").count():
                    first.locator("#learner-response").fill(
                        "I would clarify the input and output, then state the invariant."
                    )
                    with first.expect_response(
                        lambda response: response.url.endswith("/turns")
                    ) as first_saved:
                        first.locator("#learner-response").press("Control+Enter")
                    assert first_saved.value.status == 202
                else:
                    first.locator("body").press("Enter")
                _show_new_revision(first, passive_revision)

                lesson_title = first.locator("#move-title").inner_text()
                first.get_by_role("button", name="Chat", exact=True).click()
                playwright.expect(first.locator('[data-tool-panel="chat"]')).to_be_visible()
                playwright.expect(first.locator(".focus-column")).to_be_visible()
                first.locator("#chat-question").fill("Can you explain that another way?")
                with first.expect_response(
                    lambda response: response.url.endswith("/turns")
                ) as chat_saved:
                    first.locator("[data-chat-form]").get_by_role(
                        "button", name="Ask tutor"
                    ).click()
                assert chat_saved.value.status == 202
                playwright.expect(first.locator(".chat-exchange")).to_have_count(1)
                playwright.expect(first.locator("#chat-question")).to_have_value("")
                assert first.locator("#move-title").inner_text() == lesson_title
                assert first.locator("[data-tutor-stream-preview]").is_hidden()
                assert "tool=chat" in first.url
                first.get_by_role("button", name="Close learning tool").click()

                slug = first.locator("[data-focus-shell]").get_attribute("data-course-slug")
                monkeypatch.setenv("OPENLEARN_HOME", str(home))
                cli.clear_config_cache()
                topic = cli.read_topic(slug)
                check = "**Check:**\nWhat invariant would you maintain for this hash-map step?"
                cli.append_session(topic, "lesson", "Browser response check", check)
                cli.save_pending_question(
                    cli.read_topic(slug),
                    check,
                    "",
                    question_text="What invariant would you maintain for this hash-map step?",
                )
                pending_state = cli.load_state(slug)
                canonical = pending_state["interview_curriculum"]
                check_target = canonical["committed_check_target"]
                pending_state["pending_question"]["curriculum_target"] = check_target[
                    "skill_ref"
                ]
                pending_state["pending_question"]["curriculum_evidence_kind"] = (
                    check_target["evidence_kind"]
                )
                pending_question = pending_state["pending_question"]
                cli.update_state_atomic(
                    slug,
                    lambda state: state.__setitem__(
                        "pending_question", pending_question
                    ),
                )
                topic_path = cli.topic_path(slug)
                with cli.file_lock(topic_path):
                    raw_metadata, body = cli.parse_topic(
                        topic_path.read_text(encoding="utf-8")
                    )
                    raw_metadata["pending_question"] = pending_question
                    cli.write_text_atomic(
                        topic_path, cli.format_topic(raw_metadata, body)
                    )
                first.reload()
                composer_submit = first.locator("[data-composer-submit]")
                playwright.expect(composer_submit).to_contain_text("Send answer")
                first.emulate_media(reduced_motion="no-preference")
                first.set_viewport_size({"width": 1280, "height": 800})
                initial_revision = _revision(first)
                focus_url = first.url

                theme = first.locator("[data-theme-toggle]")
                playwright.expect(theme).to_be_visible()
                theme.click()
                assert first.locator("html").get_attribute("data-theme") == "dark"
                first.reload()
                assert first.locator("html").get_attribute("data-theme") == "dark"

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
                navigation = first.get_by_role("navigation", name="Openlearn navigation")
                playwright.expect(navigation).to_be_visible()
                playwright.expect(navigation.get_by_text("Tutor", exact=True)).to_be_visible()
                playwright.expect(navigation.get_by_text("Data", exact=True)).to_be_visible()
                assert not first.locator(".focus-column").is_visible()
                first.get_by_role("button", name="Close learning tool").click()
                _assert_no_page_overflow(first)
                progress_button = first.get_by_role("button", name="Progress", exact=True)
                progress_button.focus()
                progress_button.press("Enter")
                assert progress_button.get_attribute("aria-expanded") == "true"
                playwright.expect(first.locator("#progress-drawer")).to_be_visible()
                playwright.expect(first.locator("#progress-drawer")).to_contain_text(
                    "accepted route skills covered once"
                )
                assert first.locator("#progress-drawer progress").count() == 1
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
                _show_new_revision(first, initial_revision)
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
                _show_new_revision(stale, refreshed_revision)
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            cli.clear_config_cache()


def test_real_browser_restored_historical_chat_draft_submits_without_advancing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    home = tmp_path / "openlearn-home"
    monkeypatch.setenv("OPENLEARN_HOME", str(home))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENLEARN_API_KEY",
        "OPENLEARN_BASE_URL",
        "OPENLEARN_MODEL",
        "OPENLEARN_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    cli.clear_config_cache()
    cli.cmd_new(
        argparse.Namespace(
            topic="Historical Draft Browser",
            goal="Prepare for technical interviews",
            mastery_profile="efficient",
            template=None,
            interview_prep=True,
        ),
        output_func=lambda _text="": None,
    )
    slug = "historical-draft-browser"
    application.prepare_interview_curriculum(slug, boundary="resume")
    application.accept_interview_curriculum(
        slug, action="skip", submission_id=str(uuid4())
    )
    initialized = OpenLearnWebServices()._start_course_initialization(
        slug, str(uuid4())
    )
    assert "operation_id" in initialized, initialized
    operation_id = str(initialized["operation_id"])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        operation = tutor_service.operation_status(slug, operation_id)
        if operation is not None and operation.status == "committed":
            break
        if operation is not None and operation.status in {"conflict", "retryable_error"}:
            raise AssertionError(operation.error_message or operation.status)
        time.sleep(0.02)
    else:
        raise AssertionError("first lesson did not finish")

    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "OPENLEARN_HOME": str(home),
        "OPENLEARN_MOCK": "1",
        "PYTHONPATH": str(SOURCE_ROOT),
    }
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
                page = browser.new_page()
                page.goto(bootstrap_url)
                page.goto(f"{app_url}/courses/{slug}")
                page.locator("[data-focus-shell]").wait_for(state="visible")
                historical_title = page.locator("#move-title").inner_text()
                historical_id = page.locator(
                    'input[name="source_lesson_id"]'
                ).input_value()
                historical_revision = _revision(page)

                page.get_by_role("button", name="Chat", exact=True).click()
                draft = "Explain the exact lesson I had open before continuing."
                page.locator("#chat-question").fill(draft)
                assert page.locator(
                    'input[name="source_lesson_revision"]'
                ).input_value() == str(historical_revision)

                if page.locator("#learner-response").count():
                    page.locator("#learner-response").fill(
                        "I would state the invariant and trace one example."
                    )
                    page.locator("#learner-response").press("Control+Enter")
                else:
                    page.get_by_role("button", name="Close learning tool").click()
                    page.get_by_role("button", name="Continue", exact=True).click()
                playwright.expect(
                    page.get_by_role("button", name="Show next lesson")
                ).to_be_visible()
                page.get_by_role("button", name="Show next lesson").click()
                page.wait_for_function(
                    "previous => Number(document.querySelector('[data-focus-shell]').dataset.revision) > previous",
                    arg=historical_revision,
                )
                current_revision = _revision(page)
                assert page.locator("#move-title").inner_text() != historical_title

                page.reload()
                page.locator("[data-focus-shell]").wait_for(state="visible")
                page.get_by_role("button", name="Chat", exact=True).click()
                playwright.expect(page.locator("#chat-question")).to_have_value(draft)
                assert page.locator(
                    'input[name="source_lesson_id"]'
                ).input_value() == historical_id
                assert page.locator(
                    'input[name="source_lesson_revision"]'
                ).input_value() == str(historical_revision)

                with page.expect_response(
                    lambda response: response.url.endswith("/turns")
                ) as submitted:
                    page.locator("[data-chat-form]").get_by_role(
                        "button", name="Ask tutor"
                    ).click()
                assert submitted.value.status == 202
                playwright.expect(page.locator(".chat-exchange")).to_have_count(1)
                playwright.expect(page.locator(".chat-source-label")).to_have_text(
                    f"About: {historical_title}"
                )
                assert _revision(page) == current_revision
                assert tutor_service.course_revision(slug) == current_revision
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            cli.clear_config_cache()


def test_progression_action_locks_every_competing_control_until_handled() -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    javascript = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "openlearn"
        / "web"
        / "static"
        / "openlearn.js"
    )
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        page.set_content(
            """
            <meta name="csrf-token" content="test-token">
            <main data-focus-shell data-course-slug="technical-interview-prep"
                  data-operation-id="00000000-0000-4000-8000-000000000001">
              <button data-progression-action="resume">Resume</button>
              <button data-progression-action="cancel">Cancel</button>
              <button data-navigation-intent="next">Continue</button>
              <button data-navigation-intent="skip">Skip</button>
              <button data-navigation-intent="practice">Practice</button>
              <p data-operation-state hidden tabindex="-1">
                <span data-operation-message></span>
              </p>
            </main>
            """
        )
        page.evaluate(
            """
            window.progressionFetches = 0;
            window.fetch = () => {
              window.progressionFetches += 1;
              return new Promise((resolve) => { window.resolveProgression = resolve; });
            };
            void 0;
            """
        )
        page.add_script_tag(path=str(javascript))

        page.evaluate(
            """
            setTimeout(
              () => document.querySelector('[data-progression-action="resume"]').click(),
              0,
            );
            void 0;
            """
        )
        page.wait_for_function("() => window.progressionFetches === 1")
        controls = page.locator(
            "[data-progression-action], [data-navigation-intent]"
        )
        assert controls.evaluate_all("items => items.every(item => item.disabled)")
        page.locator('[data-progression-action="cancel"]').evaluate(
            "button => button.click()"
        )
        assert page.evaluate("window.progressionFetches") == 1
        page.evaluate(
            """
            window.resolveProgression(new Response(
              JSON.stringify({state: "busy", error: "Another action is active."}),
              {status: 200, headers: {"Content-Type": "application/json"}},
            ));
            """
        )
        playwright.expect(page.locator("[data-operation-message]")).to_have_text(
            "Another action is active."
        )
        assert controls.evaluate_all("items => items.every(item => !item.disabled)")

        concurrent = browser.new_page()
        concurrent.set_content(
            """
            <meta name="csrf-token" content="test-token">
            <main data-focus-shell data-course-slug="technical-interview-prep"
                  data-operation-id="main-operation" data-operation-state="generating"
                  data-revision="2">
              <article data-current-move>
                <div data-move-content>Main lesson</div>
                <div data-tutor-stream-preview hidden>
                  <p data-tutor-stream-text></p>
                </div>
              </article>
              <p data-operation-state hidden tabindex="-1">
                <span data-operation-message></span>
              </p>
              <div data-chat-conversation></div>
              <form data-chat-form>
                <input name="submission_id" value="side-submission">
                <input name="expected_revision" value="2">
                <input name="intent" value="question">
                <textarea name="text" required>Explain this</textarea>
                <button type="submit">Ask tutor</button>
                <p data-chat-status></p>
              </form>
            </main>
            """
        )
        concurrent.evaluate(
            """
            window.mainPolls = 0;
            Object.defineProperty(window.crypto, "randomUUID", {
              configurable: true,
              value: () => "00000000-0000-4000-8000-000000000099",
            });
            window.fetch = async (url, options = {}) => {
              if (url.includes("/turns") && options.method === "POST") {
                return new Response(JSON.stringify({
                  state: "saved", operation_id: "side-operation",
                }), {status: 200, headers: {"Content-Type": "application/json"}});
              }
              if (url.includes("/operations/side-operation")) {
                return new Promise((resolve) => { window.resolveSideOperation = resolve; });
              }
              if (url.endsWith("/chat")) {
                return new Response(JSON.stringify({
                  conversation: [{
                    source_lesson_title: "Main lesson",
                    question: "Explain this",
                    blocks: [],
                  }],
                  revision: 4,
                }), {
                  status: 200, headers: {"Content-Type": "application/json"},
                });
              }
              window.mainPolls += 1;
              return new Response(JSON.stringify(window.mainPolls === 1
                ? {state: "generating", preview_text: "MAIN PREVIEW"}
                : {state: "committed", preview_text: "MAIN PREVIEW"}), {
                status: 200, headers: {"Content-Type": "application/json"},
              });
            };
            void 0;
            """
        )
        concurrent.add_script_tag(path=str(javascript))
        playwright.expect(
            concurrent.locator("[data-tutor-stream-text]")
        ).to_contain_text("MAIN PREVIEW")
        concurrent.get_by_role("button", name="Ask tutor").click()
        playwright.expect(
            concurrent.get_by_role("button", name="Show next lesson")
        ).to_be_disabled()
        assert concurrent.locator('textarea[name="text"]').input_value() == "Explain this"
        assert concurrent.locator("[data-move-content]").inner_text() == "Main lesson"
        concurrent.wait_for_function(
            "() => typeof window.resolveSideOperation === 'function'"
        )
        concurrent.evaluate(
            """
            window.resolveSideOperation(new Response(JSON.stringify({
              state: "committed", preview_text: "SIDE ANSWER",
            }), {status: 200, headers: {"Content-Type": "application/json"}}));
            """
        )
        playwright.expect(concurrent.locator("[data-chat-status]")).to_have_text(
            "Answered. Your lesson is still open."
        )
        playwright.expect(
            concurrent.get_by_role("button", name="Show next lesson")
        ).to_be_enabled()
        assert concurrent.locator('textarea[name="text"]').input_value() == ""
        assert "SIDE ANSWER" not in concurrent.locator(
            "[data-tutor-stream-text]"
        ).inner_text()
        assert "MAIN PREVIEW" in concurrent.locator(
            "[data-tutor-stream-text]"
        ).inner_text()
        browser.close()


def test_stream_preview_is_separate_smooth_and_bounded() -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    static_dir = (
        Path(__file__).resolve().parents[1] / "src" / "openlearn" / "web" / "static"
    )
    javascript = static_dir / "openlearn.js"
    stylesheet = static_dir / "openlearn.css"
    long_preview = " ".join(f"token-{index}" for index in range(900))
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 800})
        page.set_content(
            """
            <meta name="csrf-token" content="test-token">
            <main data-focus-shell data-course-slug="technical-interview-prep"
                  data-operation-id="main-operation" data-operation-state="generating"
                  data-revision="2">
              <article class="move-surface" data-current-move>
                <h1>Current lesson remains readable</h1>
                <div class="move-content" data-move-content>
                  <p>Keep this lesson visible while the next response is generated.</p>
                </div>
                <div class="tutor-stream-preview" data-tutor-stream-preview hidden>
                  <p class="stream-label">Tutor response</p>
                  <div data-tutor-stream-text></div>
                </div>
              </article>
              <p data-operation-state hidden tabindex="-1">
                <span data-operation-message></span>
              </p>
            </main>
            """
        )
        page.add_style_tag(path=str(stylesheet))
        page.evaluate(
            """preview => {
              window.previewMutations = 0;
              new MutationObserver((records) => {
                window.previewMutations += records.length;
              }).observe(document.querySelector('[data-tutor-stream-text]'), {
                childList: true,
                characterData: true,
                subtree: true,
              });
              window.previewPoll = 0;
              window.fetch = async () => {
                window.previewPoll += 1;
                return new Response(JSON.stringify({
                  state: window.previewPoll === 1 ? 'generating' : 'committed',
                  preview_text: preview,
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              };
            }""",
            long_preview,
        )
        page.add_script_tag(path=str(javascript))

        playwright.expect(page.get_by_text("Current lesson remains readable")).to_be_visible()
        playwright.expect(page.locator("[data-tutor-stream-preview]")).to_be_visible()
        playwright.expect(
            page.get_by_role("button", name="Show next lesson")
        ).to_be_visible(timeout=6_000)
        preview_box = page.locator("[data-tutor-stream-preview]").bounding_box()
        assert preview_box and 140 <= preview_box["height"] <= 370
        assert page.locator("[data-current-move]").evaluate(
            "surface => surface.style.height === ''"
        )
        assert page.evaluate("window.previewMutations") < 180

        reduced = browser.new_page(viewport={"width": 1100, "height": 800})
        reduced.emulate_media(reduced_motion="reduce")
        reduced.set_content(
            """
            <meta name="csrf-token" content="test-token">
            <main data-focus-shell data-course-slug="technical-interview-prep"
                  data-operation-id="reduced-operation" data-operation-state="generating"
                  data-revision="2">
              <article class="move-surface" data-current-move>
                <h1>Long current lesson</h1>
                <div class="move-content" data-move-content>
                  <p>Existing explanation.</p><p>Existing example.</p>
                  <p>Existing tradeoffs.</p><p>Existing constraints.</p>
                  <p>Existing walkthrough.</p><p>Existing summary.</p>
                </div>
                <div class="tutor-stream-preview" data-tutor-stream-preview hidden>
                  <p class="stream-label">Tutor response</p>
                  <div data-tutor-stream-text></div>
                </div>
              </article>
              <p data-operation-state hidden tabindex="-1">
                <span data-operation-message></span>
              </p>
            </main>
            """
        )
        reduced.add_style_tag(path=str(stylesheet))
        reduced.evaluate(
            """
            window.fetch = async () => new Response(JSON.stringify({
              state: 'committed', preview_text: 'A short next lesson.',
            }), {status: 200, headers: {'Content-Type': 'application/json'}});
            """
        )
        reduced.add_script_tag(path=str(javascript))
        playwright.expect(
            reduced.get_by_role("button", name="Show next lesson")
        ).to_be_visible()
        region = reduced.locator("[data-tutor-stream-preview]")
        assert region.evaluate("node => getComputedStyle(node).transitionDuration") == "0s"
        first_height = reduced.locator("[data-current-move]").bounding_box()["height"]
        reduced.wait_for_timeout(300)
        second_height = reduced.locator("[data-current-move]").bounding_box()["height"]
        assert abs(first_height - second_height) < 2
        assert region.bounding_box()["height"] <= 180
        browser.close()


def test_next_lesson_handoff_restores_unsent_chat_draft() -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    javascript = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "openlearn"
        / "web"
        / "static"
        / "openlearn.js"
    )
    document = """
      <meta name="csrf-token" content="test-token">
      <main data-focus-shell data-course-slug="technical-interview-prep"
            data-operation-id="main-operation" data-operation-state="generating"
            data-revision="2">
        <article data-current-move>
          <div data-move-content>Current lesson</div>
          <div data-tutor-stream-preview hidden><div data-tutor-stream-text></div></div>
        </article>
        <p data-operation-state hidden tabindex="-1"><span data-operation-message></span></p>
        <div data-chat-conversation></div>
        <form data-chat-form>
          <input name="submission_id" value="side-submission">
          <input name="expected_revision" value="2">
          <input name="intent" value="question">
          <input name="source_lesson_id" value="old-lesson">
          <input name="source_lesson_title" value="Old lesson title">
          <input name="source_lesson_revision" value="2">
          <textarea name="text" required></textarea>
          <button type="submit">Ask tutor</button>
          <p data-chat-status></p>
        </form>
      </main>
      <script>
        window.fetch = async () => new Response(JSON.stringify({
          state: 'committed', preview_text: 'Next lesson preview',
        }), {status: 200, headers: {'Content-Type': 'application/json'}});
      </script>
      <script src="/openlearn.js"></script>
    """
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        page.route(
            "http://openlearn.test/openlearn.js",
            lambda route: route.fulfill(path=str(javascript)),
        )
        page.route(
            "http://openlearn.test/focus",
            lambda route: route.fulfill(body=document, content_type="text/html"),
        )
        page.goto("http://openlearn.test/focus")
        draft = "Keep this question about the old lesson."
        page.locator('textarea[name="text"]').fill(draft)
        playwright.expect(
            page.get_by_role("button", name="Show next lesson")
        ).to_be_visible()
        page.get_by_role("button", name="Show next lesson").click()
        playwright.expect(page.locator('textarea[name="text"]')).to_have_value(draft)
        assert page.locator('input[name="source_lesson_id"]').input_value() == "old-lesson"
        assert page.locator('input[name="source_lesson_title"]').input_value() == (
            "Old lesson title"
        )
        browser.close()


def test_late_chat_refresh_cannot_replace_newer_conversation() -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    javascript = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "openlearn"
        / "web"
        / "static"
        / "openlearn.js"
    )
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        document = """
            <meta name="csrf-token" content="test-token">
            <button data-tool-open="chat">Chat</button>
            <main data-focus-shell data-course-slug="technical-interview-prep" data-revision="2">
              <aside data-tool-surface hidden>
                <h2 data-tool-title></h2><button data-tool-close>Close</button>
                <section data-tool-panel="chat" hidden>
                  <div data-chat-conversation></div>
                  <form data-chat-form>
                    <input name="submission_id" value="side-submission">
                    <input name="expected_revision" value="2">
                    <input name="intent" value="question">
                    <textarea name="text" required>Newest question</textarea>
                    <button type="submit">Ask tutor</button>
                    <p data-chat-status></p>
                  </form>
                </section>
              </aside>
            </main>
            """
        page.route(
            "http://openlearn.test/focus",
            lambda route: route.fulfill(body=document, content_type="text/html"),
        )
        page.goto("http://openlearn.test/focus")
        page.evaluate(
            """
            window.chatGets = 0;
            Object.defineProperty(window.crypto, 'randomUUID', {
              configurable: true,
              value: () => '00000000-0000-4000-8000-000000000099',
            });
            window.fetch = async (url, options = {}) => {
              const requestUrl = typeof url === 'string' ? url : (url?.url || '');
              if (requestUrl.includes('/turns') && options.method === 'POST') {
                return new Response(JSON.stringify({
                  state: 'saved', operation_id: 'side-operation',
                }), {status: 200, headers: {'Content-Type': 'application/json'}});
              }
              if (requestUrl.includes('/operations/side-operation')) {
                return new Response(JSON.stringify({state: 'committed'}), {
                  status: 200, headers: {'Content-Type': 'application/json'},
                });
              }
              window.chatGets += 1;
              if (window.chatGets === 1) {
                return new Promise((resolve) => { window.resolveOldChat = resolve; });
              }
              return new Response(JSON.stringify({
                revision: 2,
                course_revision: 2,
                chat_revision: 2,
                conversation: [{
                  source_lesson_title: 'New lesson',
                  question: 'Newest question',
                  blocks: [{kind: 'paragraph', text: 'Newest answer'}],
                }],
              }), {status: 200, headers: {'Content-Type': 'application/json'}});
            };
            void 0;
            """
        )
        page.add_script_tag(path=str(javascript))
        page.evaluate(
            """
            setTimeout(
              () => document.querySelector('[data-tool-open="chat"]').click(),
              0,
            );
            void 0;
            """
        )
        page.wait_for_function("() => typeof window.resolveOldChat === 'function'")
        page.evaluate(
            """
            setTimeout(
              () => document.querySelector('[data-chat-form]').requestSubmit(),
              0,
            );
            void 0;
            """
        )
        playwright.expect(page.locator("[data-chat-conversation]")).to_contain_text(
            "Newest answer"
        )
        assert page.locator('input[name="expected_revision"]').input_value() == "2"
        page.evaluate(
            """
            window.resolveOldChat(new Response(JSON.stringify({
              revision: 3,
              course_revision: 3,
              chat_revision: 1,
              conversation: [{
                source_lesson_title: 'Old lesson',
                question: 'Old question',
                blocks: [{kind: 'paragraph', text: 'Stale answer'}],
              }],
            }), {status: 200, headers: {'Content-Type': 'application/json'}}));
            """
        )
        page.wait_for_timeout(100)
        assert "Newest answer" in page.locator("[data-chat-conversation]").inner_text()
        assert "Stale answer" not in page.locator("[data-chat-conversation]").inner_text()
        assert page.locator("[data-focus-shell]").get_attribute("data-revision") == "3"
        assert page.locator("[data-focus-shell]").get_attribute(
            "data-chat-revision"
        ) == "2"
        browser.close()


def test_real_browser_course_library_preview_history_responsive_and_no_js(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    home = tmp_path / "library-home"
    monkeypatch.setenv("OPENLEARN_HOME", str(home))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    cli.clear_config_cache()
    active = application.create_course(
        application.CourseCreationRequest(name="Active Course", goal="Keep learning")
    ).course
    preview = application.create_course(
        application.CourseCreationRequest(name="Preview Course", goal="Inspect first")
    ).course
    application.activate_course(active.slug)

    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "OPENLEARN_HOME": str(home),
        "OPENLEARN_MOCK": "1",
        "PYTHONPATH": str(SOURCE_ROOT),
    }
    command = f"from openlearn.web.launcher import run; run(port={port}, open_browser=False)"
    log_path = tmp_path / "openlearn-library-web.log"
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
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                page = context.new_page()
                page.goto(bootstrap_url)
                page.goto(f"{app_url}/dashboard?course={active.slug}")
                page.evaluate("localStorage.setItem('openlearn-theme', 'dark')")
                page.reload()
                _assert_no_page_overflow(page)
                workspace = page.locator("[data-course-workspace]")
                playwright.expect(workspace).to_be_visible()
                assert workspace.bounding_box()["width"] >= 1100
                assert workspace.evaluate(
                    "element => getComputedStyle(element).borderTopWidth"
                ) == "1px"
                playwright.expect(page.locator(".course-controls-panel")).to_be_visible()
                playwright.expect(
                    page.get_by_role("link", name="Change course outline")
                ).to_be_visible()
                assert page.locator(".course-library-dashboard h1").evaluate(
                    "heading => parseFloat(getComputedStyle(heading).fontSize) <= 40"
                )

                preview_row = page.locator(f'[data-course-slug="{preview.slug}"]')
                preview_row.click()
                page.wait_for_url(f"**/dashboard?course={preview.slug}")
                assert page.locator("[data-selected-course]").get_attribute(
                    "data-active-course"
                ) == active.slug
                assert preview_row.evaluate("row => row === document.activeElement")
                playwright.expect(page.locator("[data-live-region]")).to_contain_text(
                    "Preview Course preview updated"
                )

                page.go_back()
                playwright.expect(
                    page.locator(f'[data-course-slug="{active.slug}"][aria-current="true"]')
                ).to_be_visible()
                page.go_forward()
                playwright.expect(
                    page.locator(f'[data-course-slug="{preview.slug}"][aria-current="true"]')
                ).to_be_visible()
                page.reload()
                assert page.locator("[data-selected-course]").get_attribute(
                    "data-active-course"
                ) == active.slug

                page.set_viewport_size({"width": 760, "height": 900})
                _assert_no_page_overflow(page)
                page.locator("html").evaluate(
                    "element => element.style.fontSize = '150%'"
                )
                _assert_no_page_overflow(page)
                page.set_viewport_size({"width": 320, "height": 720})
                _assert_no_page_overflow(page)
                jump = page.get_by_role("link", name="View selected course preview")
                playwright.expect(jump).to_be_visible()
                jump.click()
                assert page.locator("#selected-course-preview").evaluate(
                    "preview => preview === document.activeElement"
                )

                page.emulate_media(reduced_motion="reduce")
                assert page.locator(".course-row").first.evaluate(
                    "row => getComputedStyle(row).transitionDuration === '0s'"
                )

                no_js = browser.new_context(
                    java_script_enabled=False,
                    viewport={"width": 320, "height": 720},
                ).new_page()
                no_js.goto(bootstrap_url)
                no_js.goto(f"{app_url}/dashboard?course={active.slug}")
                no_js.locator(f'[data-course-slug="{preview.slug}"]').click()
                no_js.wait_for_url(f"**/dashboard?course={preview.slug}")
                assert no_js.locator("[data-selected-course]").get_attribute(
                    "data-active-course"
                ) == active.slug
                _assert_no_page_overflow(no_js)
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_dashboard_ignores_stale_preview_and_follow_up_responses() -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    javascript = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "openlearn"
        / "web"
        / "static"
        / "openlearn.js"
    )
    shell = """
      <main data-selected-course="{slug}">
        <a href="http://openlearn.test/dashboard?course=alpha" data-course-preview-link
           data-course-slug="alpha" data-course-title="Alpha">Alpha</a>
        <a href="http://openlearn.test/dashboard?course=beta" data-course-preview-link
           data-course-slug="beta" data-course-title="Beta">Beta</a>
        <section data-course-preview>{slug}</section>
        <section data-follow-up-panel>
          <form data-follow-up-form data-endpoint="/api/follow-up">
            <input name="action" value="generate">
            <input name="submission_id" value="00000000-0000-4000-8000-000000000001">
            <textarea name="interests">Graphs</textarea>
            <button type="submit">Propose</button>
          </form>
          <p data-follow-up-status hidden></p>
        </section>
      </main>
      <p data-live-region></p>
    """
    document = f"""
      <meta name="csrf-token" content="test-token">
      {shell.format(slug="initial")}
      <script>
        window.dashboardDocuments = {{
          alpha: {json.dumps(shell.format(slug="alpha"))},
          beta: {json.dumps(shell.format(slug="beta"))},
        }};
        window.fetch = async (url, options = {{}}) => {{
          const requestUrl = String(url);
          if (options.method === 'POST') {{
            return new Promise((resolve) => {{ window.resolveFollowUp = resolve; }});
          }}
          const slug = new URL(requestUrl).searchParams.get('course');
          if (slug === 'alpha' && !window.alphaImmediate) {{
            return new Promise((resolve) => {{ window.resolveAlpha = resolve; }});
          }}
          return new Response(window.dashboardDocuments[slug], {{status: 200}});
        }};
      </script>
    """
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.route(
            "http://openlearn.test/dashboard**",
            lambda route: route.fulfill(body=document, content_type="text/html"),
        )
        page.goto("http://openlearn.test/dashboard?course=initial")
        page.add_script_tag(path=str(javascript))
        assert page_errors == []

        page.evaluate("document.querySelector('[data-course-slug=alpha]').click()")
        page.wait_for_function("() => typeof window.resolveAlpha === 'function'")
        page.evaluate("document.querySelector('[data-course-slug=beta]').click()")
        playwright.expect(page.locator("[data-selected-course]")).to_have_attribute(
            "data-selected-course", "beta"
        )
        page.evaluate(
            "window.resolveAlpha(new Response(window.dashboardDocuments.alpha, {status: 200}))"
        )
        page.wait_for_timeout(50)
        assert page.locator("[data-selected-course]").get_attribute(
            "data-selected-course"
        ) == "beta"
        assert "course=beta" in page.url

        page.locator("[data-follow-up-form]").evaluate("form => form.requestSubmit()")
        page.wait_for_function("() => typeof window.resolveFollowUp === 'function'")
        page.evaluate("window.alphaImmediate = true")
        page.evaluate("document.querySelector('[data-course-slug=alpha]').click()")
        playwright.expect(page.locator("[data-selected-course]")).to_have_attribute(
            "data-selected-course", "alpha"
        )
        page.evaluate(
            """
            window.resolveFollowUp(new Response(JSON.stringify({
              ok: true,
              state: 'ready',
              course_slug: 'beta',
            }), {status: 200, headers: {'Content-Type': 'application/json'}}))
            """
        )
        page.wait_for_timeout(50)
        assert page.locator("[data-selected-course]").get_attribute(
            "data-selected-course"
        ) == "alpha"
        assert "course=alpha" in page.url
        playwright.expect(page.locator("[data-live-region]")).to_contain_text(
            "current course preview was kept"
        )
        browser.close()


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
        "PYTHONPATH": str(SOURCE_ROOT),
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
                page.goto(f"{app_url}/dashboard")
                page.set_viewport_size({"width": 1280, "height": 800})
                empty_library = page.locator("[data-empty-course-library]")
                playwright.expect(empty_library).to_be_visible()
                assert page.locator(".empty-preview").count() == 0
                starter_tiles = empty_library.locator(".starter-tile")
                assert starter_tiles.count() >= 3
                assert starter_tiles.first.bounding_box()["width"] >= 220
                assert starter_tiles.first.evaluate(
                    "element => getComputedStyle(element).textDecorationLine"
                ) == "none"
                playwright.expect(page.locator("[data-theme-toggle]")).to_be_visible()
                assert page.locator(".local-status").count() == 0
                _assert_no_page_overflow(page)
                page.goto(f"{app_url}/setup")
                page.set_viewport_size({"width": 320, "height": 720})
                _assert_no_page_overflow(page)
                playwright.expect(
                    page.get_by_role("navigation", name="Openlearn navigation")
                ).to_be_visible()
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
                    playwright.expect(
                        page.get_by_role("navigation", name="Openlearn navigation")
                    ).to_be_visible()
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
