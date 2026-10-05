"""Flexible, source-preserving text lesson presentation."""

import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from openlearn.web import create_app
from openlearn.web.app import PlaceholderServices
from openlearn.web.services import _plain_text, _present_response


SHORT = (
    "**Lesson:**\nNoise is **random error** in a measurement.\n\n"
    "It scatters readings around the true value.\n\n"
    "For example, a scale reads 1.015 g, 0.990 g, and 1.013 g for the same 1 g standard."
)
LONG = (
    "In data quality, noise is the random error mixed into a measurement, the "
    "unpredictable part that scatters your values around. It carries no real signal, "
    "so the goal is to reduce or remove it. For example, if a scale reads 1.015 g, "
    "0.990 g, and 1.013 g for the same 1 g standard, the small wobble around 1 g "
    "is noise. The true value is not changing; only your readings are jittering."
)
MIXED = (
    "**Lesson:**\nCompare repeated measurements.\n\n"
    "- Keep the object fixed.\n- Record every reading.\n\n"
    "1. Compare the values.\n2. Report their spread.\n\n"
    "```python\nprint('<script>', 'a long code line ' * 30)\n```\n\n"
    "The source does not state the sensor accuracy."
)
STATIC = Path(__file__).resolve().parents[1] / "src/openlearn/web/static"


def focus_html(body=SHORT, *, blocks_marker=True):
    class FocusServices(PlaceholderServices):
        def provider_status(self):
            return {"ready": True, "managed": False, "providers": []}

        def focus(self, slug):
            kind, blocks = _present_response(body)
            move = {"kind": kind, "title": "Noise definition", "position": "Step 1"}
            if blocks_marker is True:
                move["blocks"] = blocks
            else:
                move["content"] = body
                if blocks_marker is None:
                    move["blocks"] = None
            return {
                "slug": slug, "title": "CS 4267 Measurement Review " * 4,
                "current_unit": "Data quality", "revision": 1,
                "move": move, "progress": {"percent": 10, "summary": "One step"},
            }

    with TestClient(create_app(FocusServices(), testing=True)) as client:
        response = client.get("/courses/presentation")
    assert response.status_code == 200
    return response.text


def test_short_lead_and_long_legacy_paragraph_keep_exact_words():
    _, short = _present_response(SHORT)
    assert [block["kind"] for block in short] == ["takeaway", "paragraph", "example"]
    assert short[0]["text"] == "Noise is random error in a measurement."
    assert short[-1]["text"].startswith("For example, a scale")
    _, long = _present_response("**Lesson:**\n" + LONG)
    assert long == [{"kind": "paragraph", "text": LONG}]
    assert 'class="slide-takeaway"' not in focus_html(LONG)
    assert 'class="slide-example"' not in focus_html(LONG)


@pytest.mark.parametrize("body", [
    "**Lesson:**\nUse **small samples** carefully.",
    "Lesson: Use __small samples__ carefully.",
    "**Lesson**: **Small samples** need care.",
    "*Lesson:* Use **small samples** carefully.",
    "Lesson **one**: Use **small samples** carefully.",
    "Lesson: Use `**literal markers**` and **small samples**.",
    "Lesson: Preserve **<img src=x onerror=alert(1)>** as text.",
    "Lesson: Preserve unmatched **markers and *italics*.",
])
def test_optional_emphasis_never_changes_plain_text(body):
    _, blocks = _present_response(body)
    expected = _plain_text(body).split(":", 1)[1].strip()
    assert blocks[0]["text"] == expected
    if blocks[0].get("inline"):
        assert "".join(part["text"] for part in blocks[0]["inline"]) == expected


def test_optional_emphasis_is_escaped_in_server_html():
    html = focus_html("Lesson: Preserve **<img src=x onerror=alert(1)>** as text.")
    assert "<strong>&lt;img src=x onerror=alert(1)&gt;</strong>" in html
    assert "<img src=x" not in html
    assert "<strong>random error</strong>" in focus_html()


def test_mixed_content_keeps_order_and_no_example_is_required():
    _, blocks = _present_response(MIXED)
    assert [block["kind"] for block in blocks] == [
        "takeaway", "unordered_list", "ordered_list", "code", "paragraph",
    ]
    assert blocks[1]["items"] == ["Keep the object fixed.", "Record every reading."]
    assert blocks[2]["items"] == ["Compare the values.", "Report their spread."]
    assert blocks[3]["text"] == "print('<script>', 'a long code line ' * 30)"
    html = focus_html(MIXED)
    assert html.index("Keep the object fixed.") < html.index("Compare the values.")
    assert html.index("Report their spread.") < html.index("&lt;script&gt;")
    assert html.index("&lt;script&gt;") < html.index("source does not state")
    assert 'class="slide-example"' not in html


@pytest.mark.parametrize("blocks_marker", [False, None])
def test_legacy_content_without_blocks_renders(blocks_marker):
    assert LONG in focus_html(LONG, blocks_marker=blocks_marker)


@pytest.mark.skipif(os.environ.get("OPENLEARN_BROWSER_TEST") != "1",
                    reason="requires installed Playwright browser")
@pytest.mark.parametrize("body,name", [(SHORT, "short"), (LONG, "legacy"), (MIXED, "mixed")])
def test_varied_text_layout_and_chat_remain_readable(body, name):
    from playwright.sync_api import expect, sync_playwright

    html = focus_html(body).replace("http://testserver", "http://localhost")
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page(reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            assert route.request.url.startswith("http://localhost/")
            path = route.request.url.split("localhost", 1)[1]
            if path.startswith("/static/"):
                asset = STATIC / path.split("/static/", 1)[1]
                content_type = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}[asset.suffix]
                route.fulfill(path=str(asset), content_type=content_type)
            elif path.startswith("/api/"):
                route.fulfill(json={"ok": True, "conversation": [], "revision": 1})
            else:
                route.fulfill(body=html, content_type="text/html")

        page.route("**/*", serve)
        page.goto("http://localhost/courses/presentation")
        assert not errors, errors
        assert page.evaluate("typeof appendPresentationBlocks") == "function"
        rail = page.locator(".tool-rail")
        chat = rail.get_by_role("button", name="Chat", exact=True)
        expect(rail.locator(".rail-caption")).to_have_text("Lesson tools")
        for theme in ("light", "dark"):
            page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
            for width in (320, 390, 860, 1440, 1920):
                page.set_viewport_size({"width": width, "height": 1000})
                for opened in (False, True):
                    if opened:
                        chat.focus()
                        page.keyboard.press("Enter")
                        expect(chat).to_have_attribute("aria-expanded", "true")
                        expect(page.locator('[data-tool-panel="chat"]')).to_be_visible()
                    expect(page.locator(".move-surface")).to_be_visible()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
                    ), (name, width, theme, opened)
                    styles = page.locator(".move-content > :first-child").evaluate(
                        "node => ({size: parseFloat(getComputedStyle(node).fontSize), "
                        "weight: Number(getComputedStyle(node).fontWeight)})"
                    )
                    if name == "legacy":
                        assert styles["size"] <= 20 and styles["weight"] <= 400
                    if width == 1440 and theme == "dark" and not opened:
                        page.screenshot(path=f"/tmp/openlearn-110-{name}.png", full_page=True)
                    if opened:
                        page.get_by_role("button", name="Close learning tool").click()
                        expect(chat).to_have_attribute("aria-expanded", "false")
                        expect(chat).to_be_focused()

        # Saved chat/history blocks use the same optional escaped emphasis.
        page.evaluate("""() => {
          const target = document.createElement('div');
          target.id = 'saved-blocks';
          document.body.append(target);
          appendPresentationBlocks(target, [{kind: 'paragraph',
            inline: [{strong: true, text: '<script>unsafe</script>'},
                     {strong: false, text: ' remains text.'}]}]);
        }""")
        expect(page.locator("#saved-blocks strong")).to_have_text("<script>unsafe</script>")
        assert page.locator("#saved-blocks script").count() == 0
        browser.close()
