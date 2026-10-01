"""Lesson tools remain optional, bounded and consent-gated."""
import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from openlearn import cli
from openlearn.web import create_app
from openlearn.web.services import OpenLearnWebServices


@pytest.fixture
def lesson_page(tmp_path, monkeypatch, request):
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    for name in ("OPENAI_API_KEY", "OPENLEARN_API_KEY", "ANTHROPIC_API_KEY",
                 "OPENLEARN_BASE_URL", "OPENLEARN_MODEL", "OPENLEARN_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    cli.clear_config_cache()
    cli.cmd_new(argparse.Namespace(topic="Synthetic lesson", goal="Learn stacks"), output_func=lambda _: None)
    unit = getattr(request, "param", "Synthetic lesson")
    course = {
        "slug": "synthetic-lesson", "title": "Synthetic lesson", "current_unit": unit,
        "revision": 0, "requires_response": True,
        "move": {"kind": "Lesson", "title": "Stack order", "position": "1 of 2",
                 "blocks": [{"kind": "paragraph", "text": "A stack removes the last item pushed."}],
                 "prompt": "What stays after pushing 4, pushing 7, then popping?"},
        "feedback": {"label": "Tutor note", "content": "Try tracing the top of the stack."},
        "progress": {"has_concepts": False, "summary": "No demonstrated mastery yet."},
    }
    service = OpenLearnWebServices()
    monkeypatch.setattr(service, "focus", lambda *_args: dict(course))
    with TestClient(create_app(testing=True, services=service)) as client:
        initial = client.get("/courses/synthetic-lesson")
        saved = client.post("/api/courses/synthetic-lesson/tools/code",
            headers={"x-csrf-token": initial.cookies["openlearn_csrf"]},
            json={"action": "save", "source": "print('saved user draft')", "expected_revision": None})
        assert saved.status_code == 200
        before = client.get("/api/courses/synthetic-lesson/tools/code").json()
        imported = client.post("/api/courses/synthetic-lesson/tools/sources/file",
            headers={"x-csrf-token": initial.cookies["openlearn_csrf"]},
            files={"file": ("class-notes.md", b"Stack notes. Saved video link: https://youtu.be/abcdefghijk\n", "text/markdown")})
        assert imported.status_code == 200
        html = client.get("/courses/synthetic-lesson").text
        saved_files = {str(path.relative_to(tmp_path)): path.read_bytes()
                       for path in tmp_path.rglob("*") if path.is_file()}
        yield html, client, before, course
        assert {str(path.relative_to(tmp_path)): path.read_bytes()
                for path in tmp_path.rglob("*") if path.is_file()} == saved_files
    cli.clear_config_cache()


def test_only_supported_tools_and_options_control(lesson_page):
    html, client, before, course = lesson_page
    for tool in ("chat", "sources", "options"):
        assert f'data-tool-open="{tool}"' in html
        assert f'data-tool-panel="{tool}"' in html
    for tool in ("code", "video"):
        assert f'data-tool-open="{tool}"' not in html
        assert f'data-tool-panel="{tool}"' not in html
    assert "Dual Surface" not in html
    assert "Off by default. Review and approve each request before it is sent to OpenRouter." not in html
    options = html[html.index('data-tool-panel="options"'):]
    assert "data-source-mode" in options
    assert "/courses/synthetic-lesson/settings" in options
    assert client.get("/api/courses/synthetic-lesson/tools/code").json() == before


@pytest.mark.parametrize("lesson_page", [" synthetic LESSON ", "Hashing"], indirect=True)
def test_title_hides_only_redundant_unit(lesson_page):
    html, client, before, course = lesson_page
    identity = html[html.index('class="focus-identity"'):html.index('class="saved-state"')]
    assert "<strong>Synthetic lesson</strong>" in identity
    assert ("<span>" in identity) == (course["current_unit"] == "Hashing")


@pytest.fixture
def tools_browser(lesson_page):
    if os.environ.get("OPENLEARN_BROWSER_TEST") != "1":
        pytest.skip("set OPENLEARN_BROWSER_TEST=1 after installing a Playwright browser")
    playwright = pytest.importorskip("playwright.sync_api")
    html, client, before, course = lesson_page
    static = Path(__file__).resolve().parents[1] / "src/openlearn/web/static"
    calls = []
    errors = []
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("pageerror", lambda error: errors.append(str(error)))
        def route_request(route):
            path = urlsplit(route.request.url).path
            if path.endswith("openlearn.js") or path.endswith("openlearn.css"):
                route.fulfill(content_type="text/javascript" if path.endswith(".js") else "text/css",
                              body=(static / Path(path).name).read_text())
            elif path == "/courses/synthetic-lesson":
                route.fulfill(content_type="text/html", body=html.replace("http://testserver", "https://openlearn.test"))
            elif path.endswith("/source-preview"):
                calls.append(("preview", route.request.post_data_json))
                route.fulfill(json={"ok": True, "preview": "Screened synthetic excerpt.",
                    "disclosure": "Only this request is approved.", "approval": str(len(calls))})
            elif path.endswith("/turns"):
                calls.append(("turn", route.request.post_data_json))
                route.fulfill(status=422, json={"error": "Synthetic stopped turn. Input retained."})
            elif path.endswith("/chat"):
                route.fulfill(json={"revision": 0, "course_revision": 0, "chat_revision": 0, "conversation": []})
            elif path.endswith("/sources"):
                route.fulfill(json={"sources": []})
            elif path.endswith("/history"):
                route.fulfill(json={"items": [], "has_more": False})
            elif path.endswith("/tools/code") or path.endswith("/tools/video"):
                calls.append(("removed_tool", path))
                route.fulfill(status=404)
            else:
                route.fulfill(status=404)
        page.route("**/*", route_request)
        page.goto("https://openlearn.test/courses/synthetic-lesson")
        page.emulate_media(reduced_motion="reduce")
        try:
            yield page, calls, playwright.expect
        finally:
            browser.close()
            assert errors == []
            assert client.get("/api/courses/synthetic-lesson/tools/code").json() == before


@pytest.mark.parametrize("width", [1440, 1024, 800, 320])
def test_panel_bounded_aligned_and_lesson_preserved(tools_browser, width):
    page, calls, expect = tools_browser
    page.set_viewport_size({"width": width, "height": 800})
    page.locator("#learner-response").fill("Keep this unsent lesson answer.")
    page.locator('[data-tool-open="chat"]').first.click()
    expect(page.locator('[data-tool-panel="chat"]')).to_be_visible()
    expect(page.locator(".focus-column")).to_be_visible()
    lesson = page.locator("[data-current-move]").bounding_box()
    tool = page.locator("[data-tool-surface]").bounding_box()
    assert tool["width"] <= lesson["width"] + 1
    if width > 860:
        assert abs(tool["y"] - lesson["y"]) <= 1
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    page.locator("[data-tool-close]").click()
    expect(page.locator("[data-tool-surface]")).not_to_be_visible()
    assert page.locator('[data-tool-open="chat"]').first.evaluate("node => node === document.activeElement")
    assert page.locator("#learner-response").input_value() == "Keep this unsent lesson answer."


@pytest.mark.parametrize("removed", ["code", "video"])
def test_removed_tool_urls_recover_without_requests(tools_browser, removed):
    page, calls, expect = tools_browser
    page.goto(f"https://openlearn.test/courses/synthetic-lesson?tool={removed}")
    assert "tool=" not in page.url
    expect(page.locator(".focus-column")).to_be_visible()
    expect(page.locator("[data-tool-surface]")).not_to_be_visible()
    assert page.locator('[data-tool-panel="code"], [data-tool-panel="video"]').count() == 0
    assert calls == []


def test_options_keyboard_history_and_stale_selection(tools_browser):
    page, calls, expect = tools_browser
    page.locator("#learner-response").fill("Unsent answer stays here.")
    options = page.locator('[data-tool-open="options"]')
    options.focus()
    page.keyboard.press("Enter")
    expect(page.locator('[data-tool-panel="options"]')).to_be_visible()
    assert not page.locator("[data-source-mode]").is_checked()
    page.keyboard.press("Escape")
    expect(page.locator("[data-tool-surface]")).not_to_be_visible()
    assert options.evaluate("node => node === document.activeElement")
    options.click()
    page.evaluate("history.replaceState({}, '', '?tool=code'); dispatchEvent(new PopStateEvent('popstate'))")
    expect(page.locator("[data-tool-surface]")).not_to_be_visible()
    assert "tool=" not in page.url
    expect(page.locator(".focus-column")).to_be_visible()
    history = page.locator('[data-drawer-toggle="history-drawer"]')
    history.click()
    expect(page.locator("#history-drawer")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#history-drawer")).not_to_be_visible()
    assert history.evaluate("node => node === document.activeElement")
    assert page.locator("#learner-response").input_value() == "Unsent answer stays here."
    assert calls == []


def test_options_toggle_never_reuses_request_approval(tools_browser):
    page, calls, expect = tools_browser
    page.locator('[data-tool-open="options"]').click()
    checkbox = page.locator("[data-source-mode]")
    assert not checkbox.is_checked()
    checkbox.check()
    page.locator("[data-tool-close]").click()
    page.locator('[data-tool-open="options"]').click()
    assert checkbox.is_checked()
    assert calls == []
    page.locator('[data-tool-open="chat"]').first.click()
    page.locator("#chat-question").fill("Explain the stack rule.")
    page.locator("[data-chat-submit]").click()
    expect(page.locator("[data-source-preview]")).to_be_visible()
    page.locator("[data-source-cancel]").click()
    assert len([call for call in calls if call[0] == "turn"]) == 0
    for expected_turns in (1, 2):
        page.locator("[data-chat-submit]").click()
        expect(page.locator("[data-source-preview]")).to_be_visible()
        page.locator("[data-source-send]").click()
        expect(page.locator("[data-chat-status]")).to_contain_text("Synthetic stopped turn")
        assert len([call for call in calls if call[0] == "turn"]) == expected_turns
    turns = [payload for kind, payload in calls if kind == "turn"]
    assert all(turn["source_mode"] is True for turn in turns)
    assert turns[0]["source_approval"] != turns[1]["source_approval"]
    previews = len([call for call in calls if call[0] == "preview"])
    page.reload()
    assert not page.locator("[data-source-mode]").is_checked()
    if page.locator('[data-tool-panel="chat"]').is_hidden():
        page.locator('[data-tool-open="chat"]').first.click()
    page.locator("#chat-question").fill("A new unscreened question.")
    page.locator("[data-chat-submit]").click()
    expect(page.locator("[data-chat-status]")).to_contain_text("Synthetic stopped turn")
    last = [payload for kind, payload in calls if kind == "turn"][-1]
    assert not last.get("source_mode")
    assert "source_approval" not in last
    assert len([call for call in calls if call[0] == "preview"]) == previews
