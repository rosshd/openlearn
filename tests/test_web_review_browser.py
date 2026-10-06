"""The actual review shell, CSS and controller against the frozen HTTP contract."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from openlearn.web import create_app
from openlearn.web.app import PlaceholderServices


STATIC = Path(__file__).resolve().parents[1] / "src/openlearn/web/static"
ARTIFACTS = Path(__file__).resolve().parents[1] / ".artifacts/review-feedback"
ANSWER = "An outlier can be a valid observation. Noise obscures the signal."
QUESTION = "How does an outlier differ from noise, and can an outlier be valid?"
pytestmark = pytest.mark.skipif(
    os.environ.get("OPENLEARN_BROWSER_TEST") != "1",
    reason="set OPENLEARN_BROWSER_TEST=1 after installing Chromium",
)


def timestamp(delta=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat()


def card(concept="Outliers and noise", **values):
    return {
        "slug": "measurements", "course": "Measurements", "concept": concept,
        "due": "2026-10-06", "card_id": f"card-{concept}", "content_version": 1,
        "review_revision": "occurrence-1", "state": "question", "question": QUESTION,
        "relearn_at": None, **values,
    }


def document(items):
    class ReviewServices(PlaceholderServices):
        def due_reviews(self, slug=None):
            return {"items": items, "count": len(items), "server_time": timestamp()}

    with TestClient(create_app(ReviewServices(), testing=True)) as client:
        response = client.get("/review?course=measurements")
    assert response.status_code == 200
    return response.text


class ReviewBrowser:
    def __init__(self, page, items):
        self.page = page
        self.items = deepcopy(items)
        self.calls = []
        self.errors = []
        self.unexpected = []
        self.reveal_failure = None
        self.answer = ANSWER
        self.explanation = "Being unusual alone does not make an observation noise.\n\nA valid observation may still lie far from the overall pattern."
        self.prepare_failure = None
        self.prepare_hold = False
        self.prepare_route = None
        self.session_failure = None
        self.grade_failure = None
        self.grade_hold = None
        self.grade_route = None
        self.grade_item = None
        self.session_time = None
        self.session_hold = None
        self.session_route = None
        self.receipt = None
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.route("**/*", self.route)

    def route(self, route):
        path = urlsplit(route.request.url).path
        if path == "/review":
            route.fulfill(body=document(self.items), content_type="text/html")
        elif path.startswith("/static/"):
            route.fulfill(path=str(STATIC / Path(path).name))
        elif path == "/api/review/session":
            self.calls.append((path, None))
            if self.session_hold:
                self.session_route = route
                return
            if self.session_failure:
                route.fulfill(json=self.session_failure)
                return
            route.fulfill(json={"items": self.items, "count": len(self.items), "server_time": self.session_time or timestamp()})
        elif path.startswith("/api/review"):
            body = route.request.post_data_json
            self.calls.append((path, body))
            assert route.request.headers["x-csrf-token"]
            if path.endswith("/prepare"):
                if self.prepare_hold:
                    self.prepare_route = route
                    return
                if self.prepare_failure:
                    route.fulfill(json=self.prepare_failure)
                else:
                    prepared = card(body["concept"])
                    self.items = [prepared if item["concept"] == body["concept"] else item for item in self.items]
                    route.fulfill(json={"ok": True, "item": prepared})
            elif path.endswith("/reveal"):
                if self.reveal_failure:
                    route.fulfill(status=409 if self.reveal_failure.get("state") == "conflict" else 200, json=self.reveal_failure)
                else:
                    route.fulfill(json={
                        "ok": True, "answer": self.answer, "explanation": self.explanation,
                        "sources": [{"label": "Course notes <script>", "excerpt": "Outliers can be valid. <img src=x onerror=alert(1)>"}],
                        "reveal_token": "reveal-1", "ratings": [
                            {"result": result, "label": result.title(), "interval": interval}
                            for result, interval in [("again", "1 minute"), ("hard", "2 days"), ("good", "4 days"), ("easy", "7 days")]
                        ],
                    })
            else:
                if self.grade_hold:
                    self.grade_route = route
                    return
                if self.grade_failure:
                    route.fulfill(status=409 if self.grade_failure.get("state") == "conflict" else 200, json=self.grade_failure)
                else:
                    self.commit(route, body)
        else:
            self.unexpected.append(path)
            route.abort()

    def commit(self, route, body):
        if self.receipt is None:
            self.items = [item for item in self.items if item["concept"] != body["concept"]]
            if self.grade_item:
                self.items.append(self.grade_item)
            self.receipt = {
                "ok": True, "state": "committed", "submission_id": body["submission_id"],
                "next_due": "2026-10-10", "relearn_at": self.grade_item["relearn_at"] if self.grade_item else None,
                "item": self.grade_item,
            }
        route.fulfill(json=self.receipt)

    def open(self):
        self.page.goto("http://localhost/review?course=measurements")

    def posts(self, suffix):
        return [body for path, body in self.calls if path == f"/api/review{suffix}"]


@pytest.fixture
def review_page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1000}, reduced_motion="reduce")
        yield page
        browser.close()


def test_question_reveal_keyboard_safe_sources_and_semantic_completion(review_page):
    from playwright.sync_api import expect

    app = ReviewBrowser(review_page, [card(answer=ANSWER, sources=[{"excerpt": "INITIAL SOURCE MUST BE OMITTED"}])])
    app.answer += "\n\n<img src=x onerror=alert(1)>"
    app.open()
    expect(review_page.locator("[data-review-question]")).to_have_text(QUESTION)
    assert ANSWER not in review_page.content()
    assert "INITIAL SOURCE MUST BE OMITTED" not in review_page.content()
    expect(review_page.locator("[data-review-grade]")).to_have_count(0)
    expect(review_page.get_by_role("button", name="Show answer and explanation", exact=True)).to_have_attribute("aria-keyshortcuts", "Space")
    review_page.keyboard.press("Enter")
    assert app.calls == []  # No global tutor navigation on this page.
    review_page.keyboard.press("Space")
    expect(review_page.locator("[data-review-answer]")).to_be_focused()
    expect(review_page.locator("[data-review-grade]")).to_have_count(4)
    expect(review_page.get_by_text("4 days", exact=True)).to_be_visible()
    assert review_page.get_by_role("heading", name="Answer", exact=True).count() == 0
    assert review_page.get_by_role("heading", name="Explanation", exact=True).count() == 0
    expect(review_page.get_by_role("group", name="Answer", exact=True)).to_be_visible()
    expect(review_page.locator(".review-explanation")).to_have_count(2)
    expect(review_page.locator(".review-answer-text")).to_have_count(2)
    assert review_page.locator(".review-answer img, .review-answer script").count() == 0
    expect(review_page.get_by_text("<img src=x onerror=alert(1)>", exact=True)).to_be_visible()
    assert review_page.locator(".review-answer-text").first.evaluate("p => parseFloat(getComputedStyle(p).lineHeight) / parseFloat(getComputedStyle(p).fontSize)") >= 1.65
    assert review_page.locator(".review-sources, .review-hint, .review-rating-meaning").count() == 0
    assert "Check your recall" not in review_page.locator("[data-review-panel]").inner_text()
    assert "Course notes <script>" not in review_page.content()
    assert "Rate what you recalled" not in review_page.locator("[data-review-panel]").inner_text()
    for index, result in enumerate(("again", "hard", "good", "easy"), start=1):
        control = review_page.locator(f"[data-review-grade='{result}']")
        expect(control).to_have_attribute("aria-keyshortcuts", str(index))
        assert "Shortcut" in control.get_attribute("aria-label")
        assert control.get_attribute("title") == control.get_attribute("aria-label")
    review_page.locator("[data-review-answer]").focus()
    review_page.evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key:'3', repeat:true, bubbles:true}))")
    assert app.posts("") == []
    review_page.keyboard.press("3")
    expect(review_page.get_by_role("heading", name="Review complete")).to_be_focused()
    expect(review_page.locator("[data-review-summary]")).to_have_text("0 remaining · 1 reviewed")
    assert app.posts("")[0]["result"] == "good"
    assert app.errors == app.unexpected == []


@pytest.mark.parametrize("width", [375, 1280])
def test_mobile_desktop_layout_and_long_content(review_page, width):
    from playwright.sync_api import expect

    review_page.set_viewport_size({"width": width, "height": 1000})
    app = ReviewBrowser(review_page, [card(question=QUESTION + " LongWord" * 50, course="Course" * 25)])
    app.open()
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    expect(review_page.locator("[data-review-answer]")).to_be_visible()
    assert review_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    dimensions = review_page.locator("[data-review-grade]").evaluate_all("buttons => buttons.map(b => ({width:b.offsetWidth,height:b.offsetHeight,left:b.offsetLeft,top:b.offsetTop}))")
    assert all(value["height"] >= 44 for value in dimensions)
    assert (dimensions[0]["top"] == dimensions[2]["top"]) == (width > 600)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    review_page.screenshot(path=str(ARTIFACTS / f"review-revealed-{width}.png"), full_page=True)
    assert app.errors == app.unexpected == []


def test_native_button_keys_and_editable_shortcuts(review_page):
    from playwright.sync_api import expect

    app = ReviewBrowser(review_page, [card()])
    app.open()
    review_page.evaluate("document.querySelector('[data-review-question]').insertAdjacentHTML('afterend', '<input aria-label=notes>')")
    review_page.get_by_role("textbox", name="notes").fill("Space")
    review_page.keyboard.press("Space")
    assert app.calls == []
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).focus()
    review_page.keyboard.press("Enter")
    expect(review_page.locator("[data-review-answer]")).to_be_visible()
    review_page.locator("[data-review-grade='hard']").focus()
    review_page.keyboard.press("1")
    assert app.posts("") == []
    review_page.keyboard.press("Space")
    expect(review_page.get_by_role("heading", name="Review complete")).to_be_visible()
    assert app.posts("")[0]["result"] == "hard"


def test_prepare_failure_retry_and_skip_do_not_complete(review_page):
    from playwright.sync_api import expect

    app = ReviewBrowser(review_page, [card(state="needs_preparation", question="", card_id="")])
    app.prepare_failure = {"ok": False, "state": "needs_source", "error": "Saved notes do not support a card."}
    app.open()
    expect(review_page.get_by_role("heading", name="Outliers and noise", exact=True)).to_be_visible()
    assert app.calls == []
    review_page.get_by_role("button", name="Prepare card", exact=True).click()
    expect(review_page.locator("[data-review-status]")).to_have_text("More course material is needed.")
    expect(review_page.get_by_role("link", name="Open course")).to_be_visible()
    app.prepare_failure = {"ok": False, "state": "preparation_failed", "error": "Provider unavailable."}
    review_page.get_by_role("button", name="Try again").click()
    expect(review_page.locator("[data-review-status]")).to_have_text("Could not prepare this card.")
    app.prepare_failure = None
    review_page.get_by_role("button", name="Try again").click()
    expect(review_page.locator("[data-review-question]")).to_have_text(QUESTION)
    review_page.get_by_role("button", name="Skip for now").click()
    expect(review_page.get_by_role("heading", name="Reviews remain due")).to_be_focused()
    expect(review_page.locator("[data-review-summary]")).to_have_text("1 remaining · 0 reviewed · 1 skipped")
    assert app.posts("") == []
    review_page.get_by_role("button", name="Try skipped cards again").click()
    expect(review_page.locator("[data-review-question]")).to_be_focused()


def test_uncertain_receipt_retries_exact_payload_and_locks_other_rating(review_page):
    from playwright.sync_api import expect

    app = ReviewBrowser(review_page, [card()])
    app.open()
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    app.grade_failure = {"ok": True, "state": "saved"}  # HTTP success is not a receipt.
    review_page.locator("[data-review-grade='easy']").click()
    expect(review_page.locator("[data-review-status]")).to_have_text("Could not confirm this rating was saved.")
    expect(review_page.locator("[data-review-answer]")).to_be_visible()
    for button in review_page.locator("[data-review-grade]").all():
        expect(button).to_be_disabled()
    expect(review_page.get_by_role("button", name="Skip for now")).to_be_disabled()
    app.grade_failure = None
    review_page.get_by_role("button", name="Retry saving").click()
    expect(review_page.get_by_role("heading", name="Review complete")).to_be_visible()
    assert app.posts("")[0] == app.posts("")[1]
    assert json.dumps(app.posts("")[0]) == json.dumps(app.posts("")[1])
    expect(review_page.locator("[data-review-summary]")).to_have_text("0 remaining · 1 reviewed")


def test_single_flight_reveal_save_and_question_after_advancement(review_page):
    from playwright.sync_api import expect

    app = ReviewBrowser(review_page, [card(), card("Signal")])
    app.open()
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).dblclick()
    expect(review_page.locator("[data-review-answer]")).to_be_visible()
    assert len(app.posts("/reveal")) == 1
    app.grade_hold = True
    review_page.locator("[data-review-grade='good']").click()
    expect(review_page.locator("[data-review-panel]")).to_have_attribute("aria-busy", "true")
    review_page.keyboard.press("4")
    assert len(app.posts("")) == 1
    expect(review_page.get_by_role("button", name="Skip for now")).to_be_disabled()
    app.commit(app.grade_route, app.posts("")[0])
    expect(review_page.locator("[data-review-question]")).to_be_focused()
    expect(review_page.locator("[data-review-answer]")).to_have_count(0)
    expect(review_page.locator("[data-review-summary]")).to_have_text("1 remaining · 1 reviewed")


def test_stale_occurrence_refresh_never_reapplies_rating(review_page):
    from playwright.sync_api import expect

    app = ReviewBrowser(review_page, [card()])
    app.open()
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    app.grade_failure = {"ok": False, "state": "conflict", "error": "Occurrence changed."}
    review_page.locator("[data-review-grade='good']").click()
    expect(review_page.locator("[data-review-status]")).to_have_text("This card changed in another tab.")
    app.items = [card(review_revision="occurrence-2", question="A new occurrence?")]
    review_page.get_by_role("button", name="Refresh review").click()
    expect(review_page.locator("[data-review-question]")).to_have_text("A new occurrence?")
    assert len(app.posts("")) == 1
    expect(review_page.locator("[data-review-grade]")).to_have_count(0)


def test_again_wait_reload_clock_reconciliation_and_skipped_cards(review_page):
    from playwright.sync_api import expect

    app = ReviewBrowser(review_page, [card()])
    app.open()
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    app.grade_item = card(state="waiting", relearn_at=timestamp(60), review_revision="occurrence-2")
    review_page.locator("[data-review-grade='again']").click()
    expect(review_page.get_by_role("heading", name="Your next card returns soon")).to_be_focused()
    expect(review_page.locator("[data-review-summary]")).to_have_text("1 remaining · 1 reviewed · 1 returns soon")
    review_page.reload()
    expect(review_page.get_by_role("heading", name="Your next card returns soon")).to_be_visible()
    assert len(app.posts("/reveal")) == 1
    assert len(app.posts("")) == 1
    app.items[0] = card(review_revision="occurrence-2")
    review_page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(review_page.locator("[data-review-question]")).to_have_text(QUESTION)
    review_page.get_by_role("button", name="Skip for now").click()
    expect(review_page.get_by_role("heading", name="Reviews remain due")).to_be_visible()


def test_wait_timer_uses_server_clock_and_keeps_waiting_skipped_summary(review_page):
    from playwright.sync_api import expect

    future = timestamp(3600)
    app = ReviewBrowser(review_page, [card(state="waiting", relearn_at=future), card("Other")])
    review_page.clock.install()
    app.open()
    review_page.get_by_role("button", name="Skip for now").click()
    expect(review_page.get_by_role("heading", name="Your next card returns soon")).to_be_visible()
    expect(review_page.locator("[data-review-summary]")).to_have_text("2 remaining · 0 reviewed · 1 returns soon · 1 skipped")
    app.session_time = timestamp(3599)
    review_page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(review_page.locator("[data-review-countdown]")).to_have_text("Returns in 0:01")
    app.items[0] = card(review_revision="occurrence-2")
    review_page.clock.fast_forward(2000)
    expect(review_page.locator("[data-review-question]")).to_have_text(QUESTION)
    assert len([path for path, _ in app.calls if path.endswith("/session")]) == 2


def test_reveal_failure_retry_and_initially_empty_are_distinct(review_page):
    from playwright.sync_api import expect

    app = ReviewBrowser(review_page, [])
    app.open()
    expect(review_page.get_by_role("heading", name="Nothing is due")).to_be_visible()
    app.items = [card()]
    review_page.reload()
    app.reveal_failure = {"ok": False, "error": "Local storage unavailable."}
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    expect(review_page.locator("[data-review-status]")).to_have_text("Could not show this answer.")
    assert app.posts("") == []
    app.reveal_failure = None
    review_page.get_by_role("button", name="Try again").click()
    expect(review_page.locator("[data-review-answer]")).to_be_visible()


@pytest.mark.parametrize("width", [375, 1280])
def test_typical_card_question_and_answer_screenshots(review_page, width):
    from playwright.sync_api import expect

    review_page.set_viewport_size({"width": width, "height": 900})
    app = ReviewBrowser(review_page, [card()])
    app.open()
    review_page.evaluate("setTheme('dark')")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    review_page.screenshot(path=str(ARTIFACTS / f"review-question-typical-{width}.png"), full_page=True)
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    expect(review_page.locator("[data-review-answer]")).to_be_visible()
    assert review_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    review_page.screenshot(path=str(ARTIFACTS / f"review-revealed-typical-{width}.png"), full_page=True)
    assert app.errors == app.unexpected == []


def test_late_session_response_cannot_replace_reloaded_question(review_page):
    from playwright.sync_api import expect

    app = ReviewBrowser(review_page, [card(state="waiting", relearn_at=timestamp(60))])
    app.open()
    app.session_hold = True
    review_page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(review_page.locator("[data-review-panel]")).to_have_attribute("aria-busy", "true")
    old_route = app.session_route
    old_item = deepcopy(app.items[0])
    app.items = [card(question="New occurrence after reload?", review_revision="occurrence-2")]
    review_page.reload()
    expect(review_page.locator("[data-review-question]")).to_have_text("New occurrence after reload?")
    old_route.fulfill(json={"items": [old_item], "count": 1, "server_time": timestamp()})
    expect(review_page.locator("[data-review-question]")).to_have_text("New occurrence after reload?")
    assert app.errors == app.unexpected == []


LONG_QUESTION = (
    "According to the course material, how should you distinguish an observation that lies far "
    "from the overall pattern from unwanted variation in measurements, and why can that "
    "unusual recorded observation still represent a valid result rather than noise?"
)
LONG_COURSE = "CS 4267 Machine Learning Classwork 0831 Review: Data Quality and Measurement"
LONG_ANSWER = (
    "An outlier is an observation that lies unusually far from the overall pattern in a dataset, "
    "but that distance alone does not make it incorrect. Noise is unwanted variation or error "
    "that makes the underlying signal harder to see. A rare event, unusual participant, or "
    "extreme measurement may still be valid, so investigate the observation and its context "
    "before deciding whether it should be treated as noise."
)
LONG_EXPLANATION = (
    "Noise is random variation mixed into a measurement, so repeated readings can "
    "scatter even when the underlying quantity stays constant. For example, readings "
    "of the same 1 g standard might be 1.015 g, 0.990 g, and 1.013 g. Only the "
    "readings vary, not the true value. An outlier, by contrast, may represent a real "
    "rare event such as unusually high sales during a holiday rush. Being unusual "
    "alone does not establish that the observation is noise or a measurement error. "
    "The distinction depends on what generated the data and whether the unusual "
    "observation carries useful information. This final sentence must remain readable "
    "above the review controls."
)


@pytest.mark.parametrize("width,height", [(1280, 800), (375, 812)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_long_revealed_card_keeps_actions_visible_and_text_faithful(review_page, width, height, theme):
    from playwright.sync_api import expect

    assert len(LONG_QUESTION.split()) == 38
    assert len(LONG_ANSWER.split()) >= 65
    assert len(LONG_EXPLANATION.split()) >= 100
    review_page.set_viewport_size({"width": width, "height": height})
    app = ReviewBrowser(review_page, [card(question=LONG_QUESTION, course=LONG_COURSE)])
    app.answer, app.explanation = LONG_ANSWER, LONG_EXPLANATION
    app.open()
    review_page.evaluate("theme => setTheme(theme)", theme)
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    expect(review_page.locator("[data-review-answer]")).to_be_focused()
    footer = review_page.locator("[data-review-actions]")
    assert footer.evaluate("element => getComputedStyle(element).position") == "sticky"
    for control in footer.locator("button").all():
        bounds = control.bounding_box()
        assert bounds["height"] >= 44
        assert 0 <= bounds["x"] and bounds["x"] + bounds["width"] <= width
        assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= height
    title_bounds = review_page.locator("[data-review-answer]").bounding_box()
    header_bounds = review_page.locator(".site-header").bounding_box()
    assert title_bounds["y"] >= header_bounds["y"] + header_bounds["height"]
    assert title_bounds["y"] + title_bounds["height"] <= footer.bounding_box()["y"]
    assert review_page.locator("[data-review-prose='answer']").text_content() == LONG_ANSWER
    assert review_page.locator("[data-review-prose='explanation']").text_content() == LONG_EXPLANATION
    assert review_page.locator(".review-answer-text").count() >= 2
    assert review_page.locator(".review-explanation").count() >= 3
    first_answer = review_page.locator(".review-answer-text").first.bounding_box()
    assert first_answer["y"] + first_answer["height"] <= footer.bounding_box()["y"]
    colors = review_page.locator(".review-answer-text, .review-explanation").evaluate_all(
        "paragraphs => paragraphs.map(p => ({foreground:getComputedStyle(p).color, background:getComputedStyle(p.closest('.review-panel')).backgroundColor}))"
    )
    def luminance(color):
        values = [int(value) / 255 for value in re.findall(r"\d+", color)[:3]]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in values]
        return sum(value * coefficient for value, coefficient in zip(linear, (0.2126, 0.7152, 0.0722)))
    for pair in colors:
        low, high = sorted([luminance(pair["foreground"]), luminance(pair["background"])])
        assert (high + 0.05) / (low + 0.05) >= 4.5
    assert review_page.locator(".review-course").text_content() == LONG_COURSE
    assert review_page.locator(".review-course").evaluate("p => getComputedStyle(p).textTransform") == "none"
    assert review_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    review_page.screenshot(path=str(ARTIFACTS / f"long-reveal-{theme}-{width}x{height}.png"))
    review_page.locator(".review-explanation").last.evaluate("p => p.scrollIntoView({block:'end'})")
    last = review_page.locator(".review-explanation").last.bounding_box()
    assert last["y"] + last["height"] <= footer.bounding_box()["y"]
    review_page.screenshot(path=str(ARTIFACTS / f"long-final-sentence-{theme}-{width}x{height}.png"))
    assert app.errors == app.unexpected == []


@pytest.mark.parametrize("text", [
    "  A reading is 1.015 g, not 0.990 g. A later reading is 1.013 g.\nThis existing line stays intact.\n\nThe unit is g.  ",
    "Dr. Dawson uses e.g. and i.e. with U.S. measurements. " + LONG_ANSWER,
    "Use x = 3.14 and y = x^2.\n```python\nreturn {'mass': 1.015}\n```\n\nThe final line remains.",
    "<img src=x onerror=alert(1)> <script>throw 'unsafe'</script>\n\n" + LONG_EXPLANATION,
    LONG_ANSWER + " " + LONG_ANSWER,
])
def test_saved_paragraphs_preserve_every_character_and_remain_plain_text(review_page, text):
    from playwright.sync_api import expect

    app = ReviewBrowser(review_page, [card()])
    app.answer = app.explanation = text
    app.open()
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    expect(review_page.locator("[data-review-answer]")).to_be_focused()
    assert review_page.locator("[data-review-prose='answer']").text_content() == text
    assert review_page.locator("[data-review-prose='explanation']").text_content() == text
    assert review_page.locator(".review-answer img, .review-answer script, .review-answer pre, .review-answer code").count() == 0
    assert app.errors == app.unexpected == []


def test_height_resize_and_enlarged_text_use_ordinary_flow_without_stale_clearance(review_page):
    from playwright.sync_api import expect

    review_page.set_viewport_size({"width": 375, "height": 812})
    app = ReviewBrowser(review_page, [card(question=LONG_QUESTION, course=LONG_COURSE)])
    app.answer, app.explanation = LONG_ANSWER, LONG_EXPLANATION
    app.open()
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    footer = review_page.locator("[data-review-actions]")
    assert footer.evaluate("element => getComputedStyle(element).position") == "sticky"
    review_page.set_viewport_size({"width": 375, "height": 430})
    review_page.wait_for_function("getComputedStyle(document.querySelector('[data-review-actions]')).position === 'static' && document.querySelector('[data-review-panel]').style.getPropertyValue('--review-dock-height') === '0px'")
    review_page.set_viewport_size({"width": 375, "height": 812})
    review_page.wait_for_function("getComputedStyle(document.querySelector('[data-review-actions]')).position === 'sticky' && parseFloat(document.querySelector('[data-review-panel]').style.getPropertyValue('--review-dock-height')) > 0")
    review_page.evaluate("document.documentElement.style.fontSize = '28px'")
    review_page.wait_for_function("getComputedStyle(document.querySelector('[data-review-actions]')).position === 'static' && document.querySelector('[data-review-panel]').style.getPropertyValue('--review-dock-height') === '0px'")
    assert review_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    review_page.get_by_role("button", name="Skip for now", exact=True).scroll_into_view_if_needed()
    expect(review_page.get_by_role("button", name="Skip for now", exact=True)).to_be_in_viewport()
    review_page.screenshot(path=str(ARTIFACTS / "enlarged-text-375x812.png"))
    review_page.evaluate("document.documentElement.style.fontSize = ''")
    review_page.get_by_role("button", name="Skip for now", exact=True).click()
    expect(review_page.get_by_role("heading", name="Reviews remain due")).to_be_focused()
    assert review_page.locator("[data-review-panel]").evaluate("panel => panel.style.getPropertyValue('--review-dock-height')") == "0px"
    assert app.errors == app.unexpected == []


def test_320px_short_screen_has_no_nested_scroll_or_unreachable_controls(review_page):
    from playwright.sync_api import expect

    review_page.set_viewport_size({"width": 320, "height": 480})
    app = ReviewBrowser(review_page, [card(question=LONG_QUESTION, course=LONG_COURSE)])
    app.answer, app.explanation = LONG_ANSWER, LONG_EXPLANATION
    app.open()
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    assert review_page.locator("[data-review-actions]").evaluate("element => getComputedStyle(element).position") == "static"
    assert review_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert review_page.locator("[data-review-panel]").evaluate("panel => !['auto','scroll'].includes(getComputedStyle(panel).overflowY)")
    for control in review_page.locator("[data-review-actions] button").all():
        control.scroll_into_view_if_needed()
        expect(control).to_be_in_viewport()
        assert control.bounding_box()["height"] >= 44
    review_page.screenshot(path=str(ARTIFACTS / "short-320x480.png"))
    assert app.errors == app.unexpected == []


@pytest.mark.parametrize("width,height,theme", [(1280, 800, "light"), (375, 812, "dark")])
def test_concise_card_layout_and_keyboard_skip_stay_usable(review_page, width, height, theme):
    from playwright.sync_api import expect

    review_page.set_viewport_size({"width": width, "height": height})
    app = ReviewBrowser(review_page, [card()])
    app.open()
    review_page.evaluate("theme => setTheme(theme)", theme)
    review_page.locator("[data-review-question]").focus()
    review_page.keyboard.press("Space")
    expect(review_page.locator("[data-review-answer]")).to_be_focused()
    review_page.screenshot(path=str(ARTIFACTS / f"concise-reveal-{theme}-{width}x{height}.png"))
    skip = review_page.get_by_role("button", name="Skip for now", exact=True)
    skip.focus()
    review_page.keyboard.press("Enter")
    expect(review_page.get_by_role("heading", name="Reviews remain due")).to_be_focused()
    assert app.posts("") == []
    assert app.errors == app.unexpected == []


def preparation_error(page, *, missing=False):
    page.clock.install()
    app = ReviewBrowser(page, [card(state="needs_preparation", question=None)])
    app.prepare_failure = {
        "ok": False, "state": "needs_source" if missing else "preparation_failed",
        "code": "missing_source" if missing else "unknown",
        "error": "PRIVATE credential <img src=x onerror=alert(1)> provider traceback",
    }
    app.open()
    # Freeze time before the action so assertion work cannot consume toast time.
    page.clock.pause_at(datetime.now(timezone.utc) + timedelta(seconds=1))
    page.get_by_role("button", name="Prepare card", exact=True).click()
    return app


def test_notice_expires_but_single_primary_and_safe_status_remain(review_page):
    from playwright.sync_api import expect

    app = preparation_error(review_page)
    toast = review_page.locator("[data-review-toast]")
    retry = review_page.get_by_role("button", name="Try again", exact=True)
    expect(toast).to_have_text("!Could not prepare this card.×")
    expect(retry).to_be_focused()
    expect(review_page.get_by_role("heading", level=2)).to_have_text("Outliers and noise")
    expect(review_page.get_by_role("heading", level=1)).to_have_text("Review")
    assert review_page.locator("[data-review-message]").count() == 0
    assert review_page.get_by_role("link", name="Open course").count() == 0
    assert "PRIVATE credential" not in review_page.content()
    assert review_page.locator("[data-live-region]").text_content() == "Preparing question and answer."
    persistent = review_page.locator("[data-review-recovery]").text_content()
    review_page.clock.run_for(7999)
    expect(toast).to_be_visible()
    review_page.clock.run_for(1)
    expect(toast).to_have_count(0)
    assert review_page.locator("[data-review-recovery]").text_content() == persistent
    expect(retry).to_be_enabled()
    assert retry.count() == 1
    app.prepare_hold = True
    retry.click()
    expect(review_page.get_by_role("button", name="Preparing…", exact=True)).to_be_disabled()
    assert review_page.locator("[data-review-status]").text_content() == ""
    review_page.wait_for_function("document.querySelector('[data-review-primary]').disabled")
    assert app.prepare_route is not None
    app.prepare_route.fulfill(json={"ok": True, "item": card()})
    expect(review_page.locator("[data-review-question]")).to_have_text(QUESTION)
    review_page.clock.run_for(10000)
    expect(toast).to_have_count(0)
    assert app.errors == app.unexpected == []


@pytest.mark.parametrize("pause", ["hover", "focus", "hidden"])
def test_notice_timer_pauses_and_resumes_with_remaining_duration(review_page, pause):
    from playwright.sync_api import expect

    app = preparation_error(review_page)
    toast = review_page.locator("[data-review-toast]")
    expect(toast).to_be_visible()
    review_page.clock.run_for(3000)
    if pause == "hover":
        toast.hover()
    elif pause == "focus":
        review_page.get_by_role("button", name="Dismiss notification").focus()
    else:
        review_page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))")
    review_page.clock.run_for(15000)
    expect(toast).to_be_visible()
    if pause == "hover":
        review_page.mouse.move(0, 0)
    elif pause == "focus":
        review_page.get_by_role("button", name="Try again", exact=True).focus()
    else:
        review_page.evaluate("Object.defineProperty(document, 'hidden', {configurable:true, get:()=>false}); document.dispatchEvent(new Event('visibilitychange'))")
    review_page.clock.run_for(4000)
    expect(toast).to_be_visible()
    review_page.clock.run_for(1100)
    expect(toast).to_have_count(0)
    assert app.errors == app.unexpected == []


def test_dismiss_restores_retry_and_repeated_failures_do_not_stack_or_keep_timers(review_page):
    from playwright.sync_api import expect

    app = preparation_error(review_page, missing=True)
    toast = review_page.locator("[data-review-toast]")
    expect(review_page.get_by_role("link", name="Open course")).to_be_visible()
    close = review_page.get_by_role("button", name="Dismiss notification")
    close.focus()
    review_page.keyboard.press("Enter")
    expect(toast).to_have_count(0)
    expect(review_page.get_by_role("button", name="Try again", exact=True)).to_be_focused()
    review_page.get_by_role("button", name="Try again", exact=True).click()
    expect(toast).to_have_count(1)
    review_page.clock.run_for(4000)
    review_page.get_by_role("button", name="Try again", exact=True).click()
    expect(toast).to_have_count(1)
    expect(review_page.locator("[data-review-status]")).to_have_count(1)
    expect(review_page.get_by_role("button", name="Try again", exact=True)).to_have_count(1)
    review_page.clock.run_for(5000)
    expect(toast).to_be_visible()  # The obsolete first timer cannot expire this notice.
    review_page.get_by_role("button", name="Skip for now", exact=True).click()
    expect(toast).to_have_count(0)
    expect(review_page.locator("[data-review-recovery]")).to_have_count(0)
    review_page.clock.run_for(20000)
    expect(toast).to_have_count(0)
    assert app.errors == app.unexpected == []


def test_notice_fades_without_motion_reduction(review_page):
    from playwright.sync_api import expect

    review_page.emulate_media(reduced_motion="no-preference")
    preparation_error(review_page)
    toast = review_page.locator("[data-review-toast]")
    expect(toast).to_be_visible()
    review_page.clock.run_for(8000)
    expect(toast).to_have_class("review-toast is-leaving")
    review_page.clock.run_for(160)
    expect(toast).to_have_count(0)
    expect(review_page.get_by_role("button", name="Try again", exact=True)).to_be_enabled()


def test_uncertain_rating_retry_survives_expiry_with_exact_payload_and_locked_actions(review_page):
    from playwright.sync_api import expect

    review_page.clock.install()
    app = ReviewBrowser(review_page, [card()])
    app.grade_failure = {"ok": False, "error": "PRIVATE ambiguous response"}
    app.open()
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    review_page.locator("[data-review-grade='good']").click()
    retry = review_page.get_by_role("button", name="Retry saving", exact=True)
    expect(retry).to_be_enabled()
    original = deepcopy(app.posts("")[0])
    review_page.clock.run_for(10000)
    expect(review_page.locator("[data-review-toast]")).to_have_count(0)
    expect(retry).to_be_focused()
    assert retry.count() == 1
    for control in review_page.locator("[data-review-grade], [data-review-actions] button").all():
        expect(control).to_be_disabled()
    assert "PRIVATE ambiguous response" not in review_page.content()
    review_page.locator("[data-review-answer]").focus()
    review_page.keyboard.press("4")
    review_page.keyboard.press("Space")
    assert app.posts("") == [original]
    assert review_page.get_by_role("button", name="Dismiss notification").count() == 0
    app.grade_failure = None
    retry.click()
    expect(review_page.get_by_role("heading", name="Review complete")).to_be_visible()
    assert app.posts("") == [original, original]
    assert review_page.locator("[data-review-recovery], [data-review-toast]").count() == 0
    assert app.errors == app.unexpected == []


def test_stale_refresh_failure_reuses_one_recovery_and_dismiss_restores_it(review_page):
    from playwright.sync_api import expect

    review_page.clock.install()
    app = ReviewBrowser(review_page, [card()])
    app.reveal_failure = {"ok": False, "state": "conflict", "error": "PRIVATE stale"}
    app.session_failure = {"ok": False, "error": "PRIVATE refresh"}
    app.open()
    review_page.get_by_role("button", name="Show answer and explanation", exact=True).click()
    expect(review_page.get_by_role("button", name="Refresh review", exact=True)).to_be_enabled()
    review_page.get_by_role("button", name="Refresh review", exact=True).click()
    expect(review_page.get_by_role("button", name="Retry refresh", exact=True)).to_be_enabled()
    expect(review_page.locator("[data-review-toast]")).to_have_count(1)
    close = review_page.get_by_role("button", name="Dismiss notification")
    close.focus()
    review_page.keyboard.press("Enter")
    expect(review_page.get_by_role("button", name="Retry refresh", exact=True)).to_be_focused()
    review_page.get_by_role("button", name="Retry refresh", exact=True).click()
    review_page.clock.run_for(10000)
    expect(review_page.locator("[data-review-toast]")).to_have_count(0)
    expect(review_page.locator("[data-review-status]")).to_have_text("This card changed. Refresh could not be completed.")
    assert review_page.locator("[data-review-recovery]").count() == 1
    app.session_failure = None
    review_page.get_by_role("button", name="Retry refresh", exact=True).click()
    expect(review_page.locator("[data-review-question]")).to_have_text(QUESTION)
    assert "PRIVATE" not in review_page.content()
    assert app.errors == app.unexpected == []


@pytest.mark.parametrize("width,height,theme", [(1280, 800, "light"), (375, 812, "dark"), (320, 480, "light")])
def test_preparation_notice_and_expired_recovery_viewports(review_page, width, height, theme):
    from playwright.sync_api import expect

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    review_page.set_viewport_size({"width": width, "height": height})
    review_page.clock.install()
    app = ReviewBrowser(review_page, [card(state="needs_preparation", question=None)])
    app.open()
    review_page.evaluate("theme => setTheme(theme)", theme)
    review_page.screenshot(path=str(ARTIFACTS / f"preparation-{theme}-{width}x{height}.png"))
    app.prepare_failure = {"ok": False, "state": "preparation_failed", "error": "PRIVATE"}
    review_page.get_by_role("button", name="Prepare card", exact=True).click()
    toast = review_page.locator("[data-review-toast]")
    expect(toast).to_be_visible()
    box = toast.bounding_box()
    header = review_page.locator(".site-header").bounding_box()
    recovery = review_page.locator("[data-review-recovery]").bounding_box()
    assert box["y"] >= max(0, header["y"] + header["height"])
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert box["y"] + box["height"] <= recovery["y"] or box["y"] >= recovery["y"] + recovery["height"]
    close = review_page.get_by_role("button", name="Dismiss notification").bounding_box()
    assert close["width"] >= 44 and close["height"] >= 44
    assert review_page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    review_page.screenshot(path=str(ARTIFACTS / f"failure-notice-{theme}-{width}x{height}.png"))
    review_page.clock.run_for(10000)
    expect(toast).to_have_count(0)
    review_page.screenshot(path=str(ARTIFACTS / f"expired-recovery-{theme}-{width}x{height}.png"))
    expect(review_page.get_by_role("button", name="Try again", exact=True)).to_be_enabled()
    assert app.errors == app.unexpected == []
