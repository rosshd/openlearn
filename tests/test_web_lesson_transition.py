"""Stable lesson processing with readable committed feedback."""

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from openlearn.web import create_app
from openlearn.web.app import PlaceholderServices
from openlearn.web.services import _present_response
from openlearn import cli
from openlearn.web.services import OpenLearnWebServices


STATIC = Path(__file__).resolve().parents[1] / "src/openlearn/web/static"
FEEDBACK = "Your answer correctly distinguishes measurement error from the true value."
NEXT_LESSON = "Compare repeated measurements before deciding how much noise is present."
browser_only = pytest.mark.skipif(
    os.environ.get("OPENLEARN_BROWSER_TEST") != "1",
    reason="requires installed Playwright browser",
)


def focus_document(*, answer=False, committed=False, resumed=False):
    class FocusServices(PlaceholderServices):
        def provider_status(self):
            return {"ready": True, "managed": False, "providers": []}

        def focus(self, slug):
            body = (
                f"**Feedback:**\n{FEEDBACK}\n\n{NEXT_LESSON}"
                if committed else "Lesson: Read this explanation at your own pace."
            )
            kind, blocks = _present_response(body)
            course = {
                "slug": slug, "title": "Measurements", "revision": 2 if committed else 1,
                "move": {
                    "kind": kind, "title": "Repeated measurements" if committed else "Noise",
                    "blocks": blocks, "prompt": "What stays fixed?" if answer and not committed else "",
                },
                "requires_response": answer and not committed,
                "progress": {"percent": 10, "summary": "One step"},
            }
            if resumed and not committed:
                course["operation"] = {
                    "id": "main-operation", "state": "generating", "actions": [],
                }
            return course

    with TestClient(create_app(FocusServices(), testing=True)) as client:
        response = client.get("/courses/transition")
    assert response.status_code == 200
    return response.text.replace("http://testserver", "http://localhost")


def test_focus_has_small_accessible_status_and_no_streaming_surface():
    html = focus_document(answer=True)
    assert "data-tutor-stream-preview" not in html
    assert "Thinking through your answer" not in html
    assert 'class="operation-slot"' in html
    assert 'data-operation-indicator aria-hidden="true" hidden' in html
    assert 'role="status" aria-live="polite" aria-atomic="true"' in html
    final = focus_document(committed=True)
    assert FEEDBACK in final
    assert NEXT_LESSON in final


def test_saved_feedback_is_rendered_again_on_each_focus_read(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    cli.cmd_new(
        argparse.Namespace(topic="Measurements", goal="Understand measurement noise"),
        output_func=lambda _text: None,
    )
    cli.append_session(
        cli.read_topic("measurements"), "chat", "The true value stays fixed.",
        f"**Feedback:**\n{FEEDBACK}\n\n{NEXT_LESSON}",
    )
    for _ in range(2):
        view = OpenLearnWebServices().focus("measurements")
        text = " ".join(str(block.get("text", "")) for block in view["move"]["blocks"])
        assert FEEDBACK in text
        assert NEXT_LESSON in text


@browser_only
@pytest.mark.parametrize("intent", ["next", "answer", "skip", "practice"])
@pytest.mark.parametrize("width,reduced", [(320, False), (390, False), (1440, False), (390, True)])
def test_pending_turn_is_compact_and_feedback_stays_on_result(intent, width, reduced):
    from playwright.sync_api import expect, sync_playwright

    initial = focus_document(answer=intent == "answer")
    final = focus_document(committed=True)
    # Exercise all existing navigation intents without changing the product template.
    if intent in {"skip", "practice"}:
        initial = initial.replace('data-navigation-intent="next"', f'data-navigation-intent="{intent}"')
    state = {"committed": False, "posts": 0, "polls": 0, "loads": 0}
    errors = []
    unexpected = []
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.emulate_media(reduced_motion="reduce" if reduced else "no-preference")
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route_request(route):
            path = urlsplit(route.request.url).path
            if path == "/courses/transition":
                state["loads"] += 1
                route.fulfill(body=final if state["committed"] else initial, content_type="text/html")
            elif path.startswith("/static/"):
                route.fulfill(path=str(STATIC / Path(path).name))
            elif path.endswith("/turns"):
                state["posts"] += 1
                assert route.request.post_data_json["intent"] == intent
                route.fulfill(json={"state": "saved", "operation_id": "main-operation"})
            elif path.endswith("/operations/main-operation"):
                state["polls"] += 1
                route.fulfill(json={
                    "state": "committed" if state["committed"] else ["saved", "judging", "generating", "validating"][(state["polls"] - 1) % 4],
                    "message_kind": "answer" if intent == "answer" else "navigation",
                    "preview_text": "TEMPORARY TEXT THAT MUST NOT FLASH. " * 100,
                })
            else:
                unexpected.append(route.request.url)
                route.abort()

        page.route("**/*", route_request)
        page.goto("http://localhost/courses/transition")
        if intent == "answer":
            page.locator("#learner-response").fill("The true value stays fixed.")
            button = page.locator("[data-composer-submit]")
        else:
            button = page.locator(f'[data-navigation-intent="{intent}"]')
        button.scroll_into_view_if_needed()
        card = page.locator("[data-current-move]")
        before = card.bounding_box()
        content = card.inner_text()
        button.click()
        expect(page.locator("[data-operation-indicator]")).to_be_visible()
        expect(page.locator("span[data-operation-message]")).to_have_text(
            "Checking answer…" if intent == "answer" else "Preparing…",
        )
        page.wait_for_function(
            "() => performance.getEntriesByType('resource').filter(entry => entry.name.endsWith('/operations/main-operation')).length >= 4",
        )
        assert state["polls"] >= 4
        assert state["posts"] == 1
        assert card.inner_text() == content
        after = card.bounding_box()
        for dimension in ("x", "y", "width", "height"):
            assert abs(before[dimension] - after[dimension]) < 1
        assert card.get_attribute("aria-busy") == "true"
        expect(page.locator(".operation-state")).not_to_be_focused()
        expect(page.get_by_text("TEMPORARY TEXT THAT MUST NOT FLASH.", exact=False)).to_have_count(0)
        expect(page.get_by_text("Thinking through your answer", exact=False)).to_have_count(0)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        button.evaluate("node => node.click()")
        assert state["posts"] == 1
        if intent == "answer":
            assert page.locator("#learner-response").input_value() == "The true value stays fixed."
            expect(page.locator("[data-move-prompt]")).to_be_visible()
        if reduced:
            assert page.locator("[data-operation-indicator]").evaluate(
                "node => getComputedStyle(node).animationName"
            ) == "none"
        artifact_dir = os.environ.get("OPENLEARN_QA_ARTIFACT_DIR")
        if artifact_dir and intent == "next" and not reduced:
            page.screenshot(path=str(Path(artifact_dir) / f"processing-{width}.png"), full_page=True)
        state["committed"] = True
        expect(page.get_by_text(FEEDBACK, exact=True)).to_be_visible()
        expect(page.get_by_text(NEXT_LESSON, exact=True)).to_be_visible()
        assert state["loads"] == 2
        page.wait_for_timeout(750)
        assert state["loads"] == 2
        expect(page.get_by_text(FEEDBACK, exact=True)).to_be_visible()
        expect(page.locator("[data-operation-indicator]")).to_be_hidden()
        assert not errors
        assert not unexpected
        browser.close()


@browser_only
@pytest.mark.parametrize("failure", ["retryable_error", "conflict", "network"])
def test_failed_turn_restores_controls_and_preserves_the_answer(failure):
    from playwright.sync_api import expect, sync_playwright

    html = focus_document(answer=True)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route_request(route):
            path = urlsplit(route.request.url).path
            if path == "/courses/transition":
                route.fulfill(body=html, content_type="text/html")
            elif path.startswith("/static/"):
                route.fulfill(path=str(STATIC / Path(path).name))
            elif path.endswith("/turns"):
                if failure == "network":
                    route.abort()
                else:
                    route.fulfill(json={"state": "saved", "operation_id": "main-operation"})
            else:
                route.fulfill(json={"state": failure, "error": "Provider unavailable.", "show_provider_recovery": True})

        page.route("**/*", route_request)
        page.goto("http://localhost/courses/transition")
        page.locator("#learner-response").fill("My answer is preserved.")
        page.locator("[data-composer-submit]").click()
        expect(page.locator(".operation-state")).to_have_class("operation-state error")
        expect(page.locator("#learner-response")).to_be_enabled()
        assert page.locator("#learner-response").input_value() == "My answer is preserved."
        expect(page.locator("[data-move-prompt]")).to_be_visible()
        expect(page.locator("[data-operation-indicator]")).to_be_hidden()
        assert page.locator("[data-current-move]").get_attribute("aria-busy") == "false"
        assert page.locator("[data-navigation-intent]").evaluate_all("nodes => nodes.every(node => !node.disabled)")
        if failure == "retryable_error":
            expect(page.locator("[data-provider-recovery]")).to_be_visible()
        assert not errors
        browser.close()


@browser_only
def test_resumed_operation_uses_neutral_status_and_explicit_handoff():
    from playwright.sync_api import expect, sync_playwright

    html = focus_document(resumed=True)
    state = {"done": False}
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()

        def route_request(route):
            path = urlsplit(route.request.url).path
            if path == "/courses/transition":
                route.fulfill(body=html, content_type="text/html")
            elif path.startswith("/static/"):
                route.fulfill(path=str(STATIC / Path(path).name))
            else:
                route.fulfill(json={"state": "committed" if state["done"] else "generating", "preview_text": "Never flash this response."})

        page.route("**/*", route_request)
        page.goto("http://localhost/courses/transition")
        expect(page.locator("span[data-operation-message]")).to_have_text("Processing…")
        expect(page.locator(".operation-state")).to_be_visible()
        expect(page.locator("[data-operation-indicator]")).to_be_visible()
        state["done"] = True
        expect(page.get_by_role("button", name="Show next lesson")).to_be_visible()
        expect(page.locator("[data-operation-indicator]")).to_be_hidden()
        expect(page.get_by_text("Never flash this response.", exact=False)).to_have_count(0)
        expect(page.get_by_role("heading", name="Noise", exact=True)).to_be_visible()
        browser.close()


@browser_only
@pytest.mark.parametrize("answer", [False, True])
@pytest.mark.parametrize("immediate", [False, True])
def test_active_chat_defers_result_navigation_for_both_commit_paths(answer, immediate):
    from playwright.sync_api import expect, sync_playwright

    html = focus_document(answer=answer)
    state = {"main_done": False, "side_done": False, "side_posts": 0, "loads": 0}
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()

        def route_request(route):
            path = urlsplit(route.request.url).path
            if path == "/courses/transition":
                state["loads"] += 1
                route.fulfill(body=html, content_type="text/html")
            elif path.startswith("/static/"):
                route.fulfill(path=str(STATIC / Path(path).name))
            elif path.endswith("/turns"):
                payload = route.request.post_data_json
                if payload["intent"] == "question":
                    state["side_posts"] += 1
                    route.fulfill(json={"state": "saved", "operation_id": "side-operation"})
                elif immediate:
                    # Hold the POST until Chat is active to exercise an immediate commit safely.
                    state["main_route"] = route
                else:
                    route.fulfill(json={"state": "saved", "operation_id": "main-operation"})
            elif path.endswith("/operations/side-operation"):
                route.fulfill(json={"state": "committed" if state["side_done"] else "generating"})
            elif path.endswith("/operations/main-operation"):
                route.fulfill(json={
                    "state": "committed" if state["main_done"] else "generating",
                    "message_kind": "answer" if answer else "navigation",
                })
            elif path.endswith("/chat"):
                route.fulfill(json={"revision": 2, "conversation": []})
            else:
                route.abort()

        page.route("**/*", route_request)
        page.goto("http://localhost/courses/transition")
        if answer:
            page.locator("#learner-response").fill("The true value stays fixed.")
            page.locator("[data-composer-submit]").click()
        else:
            page.locator('[data-navigation-intent="next"]').click()
        page.locator('[data-tool-open="chat"]').first.click()
        page.locator("#chat-question").fill("Explain this without leaving my lesson.")
        page.locator("[data-chat-submit]").click()
        expect(page.locator("[data-chat-submit]")).to_be_disabled()
        assert state["side_posts"] == 1
        if immediate:
            state["main_route"].fulfill(json={
                "state": "committed", "message_kind": "answer" if answer else "navigation",
            })
        else:
            state["main_done"] = True
        handoff = page.get_by_role("button", name="Show next lesson")
        expect(handoff).to_be_disabled()
        assert state["loads"] == 1
        expect(page.get_by_role("heading", name="Noise", exact=True)).to_be_visible()
        expect(page.locator("[data-operation-indicator]")).to_be_hidden()
        state["side_done"] = True
        expect(page.locator("[data-chat-status]")).to_have_text("Answered. Your lesson is still open.")
        expect(handoff).to_be_enabled()
        assert state["loads"] == 1
        browser.close()
