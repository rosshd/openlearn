"""Explicit, local math presentation leaves storage and ordinary text alone."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from openlearn.web import create_app
from openlearn.web.services import _present_response
from tests.test_web import PlaceholderServices


INVERSE = r"A^{-1}=\frac{1}{-2}\begin{pmatrix}4&-2\\-3&1\end{pmatrix}"
SOURCE = (
    "**Lesson:**\nFor A with rows (1, 2) and (3, 4), an inverse reverses its transformation.\n\n"
    "\\[\n" + INVERSE + "\n\\]\n\n"
    r"The fraction \(\frac{a+b}{2}\) means half the sum." + "\n\n"
    r"The column vector \(x=\begin{pmatrix}1\\2\end{pmatrix}\) has two components." + "\n\n"
    r"The equation \(Ax=b\) relates the input vector to its output."
)


def test_explicit_math_blocks_preserve_tex_and_explanation():
    kind, blocks = _present_response(SOURCE)
    assert kind == "Lesson"
    assert blocks[0]["text"] == "For A with rows (1, 2) and (3, 4), an inverse reverses its transformation."
    assert blocks[1] == {"kind": "math", "text": INVERSE}
    assert blocks[2]["parts"][1] == {"kind": "math", "text": r"\frac{a+b}{2}"}
    assert blocks[2]["parts"][0]["text"] == "The fraction "
    assert blocks[2]["parts"][2]["text"] == " means half the sum."


def test_math_never_inferred_from_currency_code_or_arrays():
    _, blocks = _present_response(
        "**Lesson:**\nCost is $5 and $10. Array [[1, 2], [3, 4]]. "
        r"Code `\(x\)` stays code." + "\n\n```python\n" + r"value = '\[x\]'" + "\n```"
    )
    assert blocks[0] == {"kind": "paragraph", "text": r"Cost is $5 and $10. Array [[1, 2], [3, 4]]. Code \(x\) stays code."}
    assert blocks[1]["kind"] == "code"
    assert blocks[1]["text"] == r"value = '\[x\]'"


@pytest.mark.parametrize("ticks", ["`", "``", "```"])
def test_inline_code_delimiter_runs_shield_math(ticks):
    _, blocks = _present_response("Use " + ticks + r"\(x\)" + ticks + " as literal code.")
    assert "parts" not in blocks[0]
    assert r"\(x\)" in blocks[0]["text"]


def test_multi_tick_code_keeps_inner_backtick_and_does_not_hide_later_math():
    _, blocks = _present_response(r"Code ``\(x\) and `tick` `` stays literal; \(y\) is math.")
    assert [part["text"] for part in blocks[0]["parts"] if part["kind"] == "math"] == ["y"]


def test_unclosed_delimiters_stay_readable():
    _, blocks = _present_response(r"**Lesson:** Missing \(close and \[end")
    assert blocks == [{"kind": "paragraph", "text": r"Missing \(close and \[end"}]


@pytest.mark.parametrize("source", ["Before\n\\[\nx+1", "\\[\\]", "\\[\n\n\\]"])
def test_incomplete_or_empty_display_stays_text(source):
    _, blocks = _present_response(source)
    assert blocks
    assert all(block["kind"] == "paragraph" for block in blocks)


def test_display_after_paragraph_and_inline_list_math():
    _, blocks = _present_response("Before\n\\[x+1\\]\n\n- First \\(x_1\\)\n- Plain item")
    assert blocks[0] == {"kind": "paragraph", "text": "Before"}
    assert blocks[1] == {"kind": "math", "text": "x+1"}
    assert blocks[2]["item_parts"] == [[{"kind": "text", "text": "First "}, {"kind": "math", "text": "x_1"}], None]


def test_math_parts_do_not_repeat_section_label_and_keep_subscripts():
    kind, blocks = _present_response(r"**Lesson:** Use \(x_{i_j}\) for this component.")
    assert kind == "Lesson"
    assert blocks[0]["parts"][0]["text"] == "Use "
    assert blocks[0]["parts"][1]["text"] == "x_{i_j}"


class MathServices(PlaceholderServices):
    def provider_status(self):
        return {"ready": True, "managed": False, "providers": []}

    def focus(self, slug):
        _, blocks = _present_response(SOURCE)
        return {
            "slug": slug, "title": "Synthetic matrix", "current_unit": "Inverse matrices",
            "revision": 0, "saved_state": "Saved locally", "feedback": None,
            "move": {"kind": "Lesson", "title": "Matrix inverse", "blocks": blocks, "position": "Step 1"},
            "progress": {"percent": 0, "summary": "No demonstrated mastery", "has_concepts": False},
        }


def test_server_math_is_escaped_and_assets_are_local():
    with TestClient(create_app(MathServices(), testing=True)) as client:
        response = client.get("/courses/synthetic-matrix")
    assert response.status_code == 200
    assert response.text.count("data-math-expression") == 4
    assert "/static/vendor/katex/katex.min.js" in response.text
    assert "cdn.jsdelivr" not in response.text
    assert "style-src 'self';" in response.headers["content-security-policy"]


@pytest.fixture
def math_browser():
    if os.environ.get("OPENLEARN_BROWSER_TEST") != "1":
        pytest.skip("set OPENLEARN_BROWSER_TEST=1 for isolated browser checks")
    playwright = pytest.importorskip("playwright.sync_api")
    with TestClient(create_app(MathServices(), testing=True)) as client:
        response = client.get("/courses/synthetic-matrix")
    static = Path(__file__).resolve().parents[1] / "src/openlearn/web/static"
    _, blocks = _present_response(SOURCE)
    requests, errors = [], []
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("window.mathCsp=[];document.addEventListener('securitypolicyviolation',e=>mathCsp.push(e.violatedDirective));")

        def handle(route):
            parsed = urlsplit(route.request.url)
            requests.append(route.request.url)
            if parsed.hostname != "math.test":
                route.abort()
            elif parsed.path == "/courses/synthetic-matrix":
                route.fulfill(body=response.text.replace("http://testserver", "https://math.test"),
                              headers={"content-type": "text/html", "content-security-policy": response.headers["content-security-policy"]})
            elif parsed.path.startswith("/static/"):
                file = static / parsed.path.removeprefix("/static/")
                if file.is_file():
                    route.fulfill(path=str(file), content_type="text/javascript" if file.suffix == ".js" else "text/css" if file.suffix == ".css" else "image/svg+xml")
                else:
                    route.fulfill(status=404)
            elif parsed.path.endswith("/chat"):
                route.fulfill(json={"revision": 0, "course_revision": 0, "chat_revision": 0, "conversation": [{"question": "Explain the inverse", "blocks": blocks}]})
            elif parsed.path.endswith("/history"):
                route.fulfill(json={"items": [{"title": "Saved inverse lesson", "kind": "Lesson", "blocks": blocks}], "has_more": False})
            else:
                route.fulfill(status=404)

        page.route("**/*", handle)
        page.goto("https://math.test/courses/synthetic-matrix")
        page.emulate_media(reduced_motion="reduce")
        yield page, playwright.expect
        browser.close()
    assert errors == []
    assert all(urlsplit(url).hostname == "math.test" for url in requests)


def test_browser_math_consistent_offline_and_responsive(math_browser):
    page, expect = math_browser
    expect(page.locator("[data-move-content] math")).to_have_count(4)
    assert page.locator("math mfrac").count() == 2
    assert page.locator("math mtr").count() == 4
    page.get_by_role("button", name="Chat", exact=True).click()
    expect(page.locator("[data-chat-conversation] math")).to_have_count(4)
    assert page.locator(".chat-tutor .math-expression span").count() == 0
    assert page.locator(".chat-tutor .math-expression").last.evaluate("node => getComputedStyle(node).textTransform") == "none"
    page.get_by_role("button", name="History", exact=True).click()
    expect(page.locator("#history-drawer math")).to_have_count(4)
    page.keyboard.press("Escape")
    for width in (1280, 320):
        page.set_viewport_size({"width": width, "height": 800})
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert page.evaluate("mathCsp") == []
    page.locator("[data-math-display='true']").first.focus()
    page.keyboard.press("ArrowRight")
    assert page.locator("[data-math-display='true']").first.evaluate("node=>node===document.activeElement")


def test_missing_vendor_and_wide_math_remain_readable(math_browser):
    page, expect = math_browser
    tex = "+".join(["x"] * 150)
    page.evaluate("tex=>{const node=document.createElement('div');document.querySelector('[data-move-content]').append(node);OpenLearnMath.render(node,tex,true);}", tex)
    page.set_viewport_size({"width": 320, "height": 800})
    expect(page.locator(".math-display").last.locator("math")).to_have_count(1)
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    page.get_by_role("button", name="Chat", exact=True).click()
    expect(page.locator("[data-chat-conversation] math")).to_have_count(4)
    page.evaluate("tex => appendPresentationBlocks(document.querySelector('.chat-tutor'), [{kind:'math',text:tex}])", tex)
    assert page.locator(".chat-tutor .math-display").last.evaluate("node => node.clientWidth <= node.parentElement.clientWidth")
    page.get_by_role("button", name="History", exact=True).click()
    expect(page.locator("#history-drawer math")).to_have_count(4)
    page.evaluate("tex => appendPresentationBlocks(document.querySelector('.history-item'), [{kind:'math',text:tex}])", tex)
    assert page.locator(".history-item .math-display").last.evaluate("node => node.clientWidth <= node.parentElement.clientWidth")
    page.keyboard.press("Escape")
    page.evaluate("() => { delete window.katex; const node=document.createElement('div');document.querySelector('[data-move-content]').append(node);OpenLearnMath.render(node,'x+1',true); }")
    expect(page.locator(".math-fallback").last).to_be_visible()
    assert page.locator(".math-fallback").last.locator("code").text_content() == "x+1"
    assert page.evaluate("mathCsp") == []


def test_math_text_cannot_create_html(math_browser):
    page, expect = math_browser
    tex = r'\text{<img src="https://external.invalid/x" onerror="alert(1)">}'
    page.evaluate("tex=>{const node=document.createElement('div');document.querySelector('[data-move-content]').append(node);OpenLearnMath.render(node,tex,true);}", tex)
    target = page.locator(".math-display").last
    expect(target).to_be_visible()
    assert target.locator("img,script,a,[style],[onclick]").count() == 0
    assert "<img" in target.text_content()
    assert page.evaluate("mathCsp") == []


@pytest.mark.parametrize("tex", [r"\frac{1}", r"\href{javascript:alert(1)}{x}", r"\htmlStyle{position:fixed}{x}", r"\includegraphics{https://external.invalid/x}", r"\def\a{\a}\a", r"\mathbf{x}", r"\vec{x}", "x" * 2001])
def test_unsupported_or_unsafe_math_has_visible_text_fallback(math_browser, tex):
    page, expect = math_browser
    page.evaluate("tex=>{const node=document.createElement('div');document.querySelector('[data-move-content]').append(node);OpenLearnMath.render(node,tex,true);}", tex)
    fallback = page.locator(".math-fallback").last
    expect(fallback).to_be_visible()
    assert fallback.locator("code").text_content() == tex
    assert fallback.locator("math,img,script,a,[style],[onclick]").count() == 0
    assert page.evaluate("mathCsp") == []
