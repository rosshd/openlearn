"""Explicit, local math presentation leaves storage and ordinary text alone."""
import os
import hashlib
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
STATIC = Path(__file__).resolve().parents[1] / "src/openlearn/web/static"
MATH_FONT = STATIC / "vendor/stix/STIXTwoMath-Regular.woff2"


def test_bundled_math_font_and_license_match_pinned_upstream():
    font = MATH_FONT.read_bytes()
    assert font[:4] == b"wOF2"
    assert hashlib.sha256(font).hexdigest() == "094191335def3f0452c81ec0713cfc2f29bb6af8cecbf79b60881fbf2db97562"
    license_text = (STATIC / "vendor/stix/OFL.txt").read_bytes()
    assert hashlib.sha256(license_text).hexdigest() == "0c8825913b60d858aacdb33c4ca6660a7d64b0d6464702efbb19313f5765861a"


@pytest.mark.parametrize("display", [False, True])
@pytest.mark.parametrize("tex", [r"n_1+n_2+\cdots+n_m", r"\sum_{i=1}^{m} n_i"])
def test_indexed_sum_source_survives_presentation(display, tex):
    source = "\\[\n" + tex + "\n\\]" if display else "Use \\(" + tex + "\\) here."
    _, blocks = _present_response(source)
    if display:
        assert blocks == [{"kind": "math", "text": tex}]
    else:
        assert blocks[0]["parts"][1] == {"kind": "math", "text": tex}


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
    assert blocks[0] == {"kind": "takeaway", "text": r"Cost is $5 and $10. Array [[1, 2], [3, 4]]. Code \(x\) stays code."}
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
    assert blocks == [{"kind": "takeaway", "text": r"Missing \(close and \[end"}]


@pytest.mark.parametrize("source", ["Before\n\\[\nx+1", "\\[\\]", "\\[\n\n\\]"])
def test_incomplete_or_empty_display_stays_text(source):
    _, blocks = _present_response(source)
    assert blocks
    assert all(block["kind"] in {"paragraph", "takeaway"} for block in blocks)


def test_display_after_paragraph_and_inline_list_math():
    _, blocks = _present_response("Before\n\\[x+1\\]\n\n- First \\(x_1\\)\n- Plain item")
    assert blocks[0] == {"kind": "takeaway", "text": "Before"}
    assert blocks[1] == {"kind": "math", "text": "x+1"}
    assert blocks[2]["item_parts"] == [[{"kind": "text", "text": "First "}, {"kind": "math", "text": "x_1"}], None]


def test_math_parts_do_not_repeat_section_label_and_keep_subscripts():
    kind, blocks = _present_response(r"**Lesson:** Use \(x_{i_j}\) for this component.")
    assert kind == "Lesson"
    assert blocks[0]["parts"][0]["text"] == "Use "
    assert blocks[0]["parts"][1]["text"] == "x_{i_j}"


def test_authored_emphasis_keeps_math_parts_and_safe_text():
    _, blocks = _present_response(r"**Lesson:** **Remember** \(x_{i_j}\) and **\(y_1\)** <script>x</script>.")
    block = blocks[0]
    assert [part["text"] for part in block["parts"] if part["kind"] == "math"] == ["x_{i_j}", "y_1"]
    assert [segment["text"] for segment in block["inline"] if segment["strong"]] == ["Remember", r"\(y_1\)"]
    strong_math = next(segment for segment in block["inline"] if segment["strong"] and "parts" in segment)
    assert strong_math["parts"] == [{"kind": "math", "text": "y_1"}]


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
        font = client.get("/static/vendor/stix/STIXTwoMath-Regular.woff2")
    assert response.status_code == 200
    assert response.text.count("data-math-expression") == 4
    assert "/static/vendor/katex/katex.min.js" in response.text
    assert "cdn.jsdelivr" not in response.text
    assert "style-src 'self';" in response.headers["content-security-policy"]
    assert "font-src 'self';" in response.headers["content-security-policy"]
    assert font.status_code == 200
    assert font.headers["content-type"] == "font/woff2"
    assert font.content == MATH_FONT.read_bytes()


@pytest.fixture
def math_browser():
    if os.environ.get("OPENLEARN_BROWSER_TEST") != "1":
        pytest.skip("set OPENLEARN_BROWSER_TEST=1 for isolated browser checks")
    playwright = pytest.importorskip("playwright.sync_api")
    with TestClient(create_app(MathServices(), testing=True)) as client:
        response = client.get("/courses/synthetic-matrix")
    static = STATIC
    _, blocks = _present_response(SOURCE)
    requests, errors = [], []
    with playwright.sync_playwright() as runtime:
        engine = os.environ.get("OPENLEARN_BROWSER_ENGINE", "chromium")
        assert engine in {"chromium", "firefox", "webkit"}
        browser = getattr(runtime, engine).launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.emulate_media(reduced_motion="reduce")
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
                    mime = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2"}
                    route.fulfill(path=str(file), content_type=mime.get(file.suffix, "application/octet-stream"))
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
        yield page, playwright.expect
        browser.close()
    assert errors == []
    assert all(urlsplit(url).hostname == "math.test" for url in requests)


@pytest.mark.parametrize("environment", ["pmatrix", "bmatrix"])
@pytest.mark.parametrize("entries, columns", [(r"4&-2\\-3&1", 2), (r"1\\2", 1)])
@pytest.mark.parametrize("display", [False, True])
def test_matrix_fences_span_rows(math_browser, environment, entries, columns, display):
    page, expect = math_browser
    tex = rf"\begin{{{environment}}}{entries}\end{{{environment}}}"
    page.evaluate("""({tex, display}) => {
        const node = document.createElement(display ? 'div' : 'span');
        node.id = 'matrix-fence-check';
        document.querySelector('[data-move-content]').append(node);
        OpenLearnMath.render(node, tex, display);
    }""", {"tex": tex, "display": display})
    target = page.locator("#matrix-fence-check")
    expect(target.locator("math mtable")).to_have_count(1)
    expect(target.locator("mtr")).to_have_count(2)
    for row in target.locator("mtr").all():
        expect(row.locator("mtd")).to_have_count(columns)
    geometry = target.evaluate("""node => {
        const table = node.querySelector('mtable').getBoundingClientRect();
        return {
            table: {top: table.top, bottom: table.bottom, height: table.height},
            fences: [...node.querySelectorAll('mo[fence="true"]')].map(fence => {
                const box = fence.getBoundingClientRect();
                return {top: box.top, bottom: box.bottom, height: box.height};
            }),
        };
    }""")
    assert len(geometry["fences"]) == 2
    for fence in geometry["fences"]:
        assert fence["height"] >= geometry["table"]["height"] * 0.9
        assert fence["top"] <= geometry["table"]["top"] + 2
        assert fence["bottom"] >= geometry["table"]["bottom"] - 2
    assert page.evaluate("mathCsp") == []


def test_browser_math_consistent_offline_and_responsive(math_browser):
    page, expect = math_browser
    expect(page.locator("[data-move-content] math")).to_have_count(4)
    assert page.locator("math mfrac").count() == 2
    assert page.locator("math mtr").count() == 4
    inline_math = page.locator("[data-math-display='false']").first
    assert inline_math.evaluate("node => getComputedStyle(node).verticalAlign") == "baseline"
    assert inline_math.evaluate("node => getComputedStyle(node).overflowX") == "visible"
    assert inline_math.evaluate("node => getComputedStyle(node).overflowY") == "visible"
    display_math = page.locator("[data-math-display='true']").first
    assert display_math.evaluate("node => getComputedStyle(node).overflowX") == "auto"
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


def test_math_uses_bundled_font_without_system_math_font(math_browser):
    page, expect = math_browser
    expect(page.locator("[data-move-content] math")).to_have_count(4)
    assert page.evaluate('document.fonts.check(\'16px "OpenLearn Math"\')')
    assert page.locator("math").first.evaluate("node => getComputedStyle(node).fontFamily") == '"OpenLearn Math", math'
    if os.environ.get("OPENLEARN_BROWSER_ENGINE", "chromium") == "chromium":
        session = page.context.new_cdp_session(page)
        session.send("DOM.enable")
        session.send("CSS.enable")
        session.send("Page.setFontFamilies", {"fontFamilies": {"math": "Times"}})
        page.locator("math").first.evaluate("node => node.getBoundingClientRect()")
        document = session.send("DOM.getDocument")
        node = session.send("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": "math mo[fence]"})
        fonts = session.send("CSS.getPlatformFontsForNode", {"nodeId": node["nodeId"]})["fonts"]
        assert fonts and all(font["isCustomFont"] for font in fonts)
    assert "OpenLearn Math" not in page.locator("[data-move-content] p").first.evaluate("node => getComputedStyle(node).fontFamily")
    assert page.evaluate("mathCsp") == []


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_failed_math_font_keeps_readable_source_in_all_surfaces(math_browser, failure):
    page, expect = math_browser

    def fail(route):
        if failure == "missing":
            route.fulfill(status=404)
        else:
            route.fulfill(body=b"not a font", content_type="font/woff2")

    page.route("**/*.woff2", fail)
    with page.expect_response("**/*.woff2"):
        page.reload()
    expect(page.locator("[data-move-content] math")).to_have_count(0)
    expect(page.locator("[data-move-content] .math-fallback")).to_have_count(4)
    assert page.locator("[data-math-display='true'] code").text_content() == INVERSE
    for display in (False, True):
        for tex in (r"n_1+n_2+\cdots+n_m", r"\sum_{i=1}^{m} n_i"):
            page.evaluate("""({tex, display}) => {
                const target = document.createElement(display ? 'div' : 'span');
                target.className = 'failed-indexed-sum';
                document.querySelector('[data-move-content]').append(target);
                OpenLearnMath.render(target, tex, display);
            }""", {"tex": tex, "display": display})
            fallback = page.locator(".failed-indexed-sum").last
            expect(fallback.locator("code")).to_have_text(tex)
            expect(fallback.locator("math")).to_have_count(0)
    page.get_by_role("button", name="Chat", exact=True).click()
    expect(page.locator("[data-chat-conversation] .math-fallback")).to_have_count(4)
    expect(page.locator("[data-chat-conversation] math")).to_have_count(0)
    page.get_by_role("button", name="History", exact=True).click()
    expect(page.locator("#history-drawer .math-fallback")).to_have_count(4)
    expect(page.locator("#history-drawer math")).to_have_count(0)
    assert page.evaluate("mathCsp") == []


def test_delayed_font_keeps_source_then_renders_latest_content(math_browser):
    page, expect = math_browser
    pending = []
    page.route("**/*.woff2", lambda route: pending.append(route))
    with page.expect_request("**/*.woff2"):
        page.reload(wait_until="domcontentloaded")
    expect(page.locator("[data-move-content] .math-fallback")).to_have_count(4)
    target = page.locator("[data-math-expression]").first
    target.evaluate("node => OpenLearnMath.render(node, 'y+2', true)")
    assert len(pending) == 1
    pending[0].fulfill(path=str(MATH_FONT), content_type="font/woff2")
    expect(target.locator("math")).to_have_count(1)
    assert target.locator("annotation").text_content() == "y+2"
    assert page.evaluate("mathCsp") == []


@pytest.mark.parametrize("display", [False, True])
@pytest.mark.parametrize("tex, script", [(r"n_1+n_2+\cdots+n_m", "msub"), (r"\sum_{i=1}^{m} n_i", "sum")])
def test_indexed_sums_render_with_local_font(math_browser, display, tex, script):
    page, expect = math_browser
    page.evaluate("""({tex, display}) => {
        const target = document.createElement(display ? 'div' : 'span');
        target.id = 'indexed-sum';
        document.querySelector('[data-move-content]').append(target);
        OpenLearnMath.render(target, tex, display);
    }""", {"tex": tex, "display": display})
    target = page.locator("#indexed-sum")
    expect(target.locator("math")).to_have_count(1)
    assert target.locator("annotation").text_content() == tex
    if script == "msub":
        expect(target.locator("msub")).to_have_count(3)
        assert "⋯" in target.locator("math mo").all_text_contents()
        for subscript in target.locator("msub").all():
            assert subscript.evaluate("node => node.children[1].getBoundingClientRect().bottom > node.children[0].getBoundingClientRect().bottom")
    else:
        limits = target.locator("munderover" if display else "msubsup").first
        expect(limits).to_have_count(1)
        assert limits.locator("mo").first.text_content() == "∑"
        assert limits.evaluate("node => node.children[1].getBoundingClientRect().bottom > node.children[0].getBoundingClientRect().bottom")
        assert limits.evaluate("node => node.children[2].getBoundingClientRect().top < node.children[0].getBoundingClientRect().top")
    assert page.evaluate("mathCsp") == []


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


def test_wide_inline_math_scrolls_without_widening_page(math_browser):
    page, expect = math_browser
    tex = "+".join([r"\frac{x}{2}"] * 12)
    page.evaluate("""tex => {
        const paragraph = document.createElement('p');
        const node = document.createElement('span');
        node.id = 'wide-inline-check';
        paragraph.append('Before ', node, ' after.');
        document.querySelector('[data-move-content]').append(paragraph);
        OpenLearnMath.render(node, tex, false);
    }""", tex)
    page.set_viewport_size({"width": 320, "height": 800})
    target = page.locator("#wide-inline-check")
    expect(target.locator("math")).to_have_count(1)
    expect(target).to_have_class("math-expression math-inline-scroll")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert target.evaluate("node => node.scrollWidth > node.clientWidth")
    target.evaluate("node => node.scrollLeft = node.scrollWidth")
    assert target.evaluate("node => node.scrollLeft > 0")
    page.set_viewport_size({"width": 1800, "height": 800})
    expect(target).to_have_class("math-expression")
    assert target.evaluate("node => getComputedStyle(node).overflowY") == "visible"
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
