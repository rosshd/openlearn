"""The actual review shell, CSS and controller against the frozen HTTP contract."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from openlearn.web import create_app
from openlearn.web.app import PlaceholderServices


STATIC = Path(__file__).resolve().parents[1] / "src/openlearn/web/static"
ARTIFACTS = Path(__file__).resolve().parents[1] / ".artifacts/review-ui"
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
        self.prepare_failure = None
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
            route.fulfill(json={"items": self.items, "count": len(self.items), "server_time": self.session_time or timestamp()})
        elif path.startswith("/api/review"):
            body = route.request.post_data_json
            self.calls.append((path, body))
            assert route.request.headers["x-csrf-token"]
            if path.endswith("/prepare"):
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
                        "ok": True, "answer": self.answer, "explanation": "Being unusual alone does not make an observation noise.\n\nA valid observation may still lie far from the overall pattern.",
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
    expect(review_page.get_by_role("heading", name="Answer", exact=True)).to_be_visible()
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
    expect(review_page.get_by_role("heading", name="Prepare this review card")).to_be_visible()
    assert app.calls == []
    review_page.get_by_role("button", name="Prepare card", exact=True).click()
    expect(review_page.get_by_role("heading", name="More course material is needed")).to_be_focused()
    expect(review_page.get_by_role("link", name="Open course")).to_be_visible()
    app.prepare_failure = {"ok": False, "state": "preparation_failed", "error": "Provider unavailable."}
    review_page.get_by_role("button", name="Retry preparation").click()
    expect(review_page.get_by_role("heading", name="Could not prepare this card")).to_be_visible()
    app.prepare_failure = None
    review_page.get_by_role("button", name="Retry preparation").click()
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
    expect(review_page.get_by_role("heading", name="Could not confirm this rating was saved")).to_be_focused()
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
    expect(review_page.get_by_role("heading", name="This card changed in another tab")).to_be_focused()
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
    expect(review_page.get_by_role("heading", name="Could not reveal this answer")).to_be_focused()
    assert app.posts("") == []
    app.reveal_failure = None
    review_page.get_by_role("button", name="Retry reveal").click()
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
