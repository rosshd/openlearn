"""Browser review journeys bridged to the real ASGI services in an isolated home."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from openlearn import cli, review_cards
from openlearn.web import create_app


pytestmark = pytest.mark.skipif(
    os.environ.get("OPENLEARN_BROWSER_TEST") != "1",
    reason="set OPENLEARN_BROWSER_TEST=1 after installing Chromium",
)

NOTES = "Outliers can be valid observations. Noise obscures a signal."
QUESTION = "Can an outlier be a valid observation, and how does it differ from noise?"
ANSWER = "An outlier can be valid. Noise is unwanted variation that obscures a signal."
EXPLANATION = "Being unusual alone does not make an observation noise."
LONG_QUESTION = (
    "According to the supplied course materials, what distinguishes noise from outliers, "
    "and how can an unusual measurement still be valid rather than being an error "
    "that should automatically be removed from the data?"
)
LONG_ANSWER = (
    "Noise is the random component of a measurement error, the unpredictable scatter "
    "around a value. An outlier is an unusual value that may be valid and can be "
    "interesting to investigate. Noise objects can be outliers, but noise objects are "
    "not always outliers and outliers are not always noise objects. An observation "
    "should therefore be investigated in context before being removed, even when it "
    "lies far from the overall pattern of the data."
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


@pytest.fixture
def review_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    for name in (
        "OPENAI_API_KEY", "OPENLEARN_API_KEY", "ANTHROPIC_API_KEY",
        "OPENLEARN_BASE_URL", "OPENLEARN_MODEL", "OPENLEARN_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    cli.clear_config_cache()
    clock = [datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(review_cards, "now", lambda: clock[0])
    monkeypatch.setattr(cli, "today", lambda: clock[0].date().isoformat())
    cli.cmd_new(
        argparse.Namespace(topic="Review Integration", goal="Compare outliers and noise"),
        output_func=lambda _text: None,
    )
    topic = cli.read_topic("review-integration")
    metadata = dict(topic.metadata)
    metadata["review_due"] = [
        {"concept": "outlier vs noise", "due": cli.today(), "difficulty": "hard"},
    ]
    cli.write_topic(topic.path, metadata, f"## Notes\n{NOTES}\n")
    generations = []

    def generate(*args, **kwargs):
        generations.append((args, kwargs))
        return json.dumps({
            "question": QUESTION, "answer": ANSWER,
            "explanation": EXPLANATION,
            "sources": [{"label": "Course notes", "excerpt": NOTES}],
        })

    monkeypatch.setattr(cli, "call_openai", generate)
    with TestClient(create_app(testing=True)) as client:
        yield client, clock, generations
    cli.clear_config_cache()


def bridge(context, client):
    """Serve actual templates/assets/APIs through TestClient, with no fake API payloads."""
    security = client.app.state.security

    def handle(route):
        request = route.request
        url = urlsplit(request.url)
        headers = {
            key: value for key, value in request.all_headers().items()
            if key.lower() not in {"content-length", "accept-encoding"}
        }
        # Each isolated fixture has its own capability; preserve real namespace
        # and Origin/Host checks while TestClient handles the transport.
        headers["X-Openlearn-Capability"] = security.access_token
        headers["host"] = url.netloc
        path = url.path if url.path.startswith(security.url_namespace) else security.url_namespace + url.path
        response = client.request(
            request.method, path + (f"?{url.query}" if url.query else ""),
            headers=headers, content=request.post_data_buffer, follow_redirects=False,
        )
        route.fulfill(
            status=response.status_code,
            headers={key: value for key, value in response.headers.items()
                     if key.lower() not in {"content-length", "content-encoding"}},
            body=response.content,
        )

    context.route("**/*", handle)


@pytest.mark.parametrize("width", [1280, 375])
@pytest.mark.parametrize("legacy", [False, True])
def test_real_service_preparation_offline_relearning_and_completion(review_fixture, width, legacy):
    playwright = pytest.importorskip("playwright.sync_api")
    client, clock, generations = review_fixture
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": 900})
        bridge(context, client)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1/review?course=review-integration")
        assert page.locator("[data-review-shell]").count(), page.locator("body").inner_text()
        assert not errors, errors
        assert page.get_by_role("button", name="Prepare card", exact=True).count(), page.content()
        assert not generations
        page.get_by_role("button", name="Prepare card", exact=True).click()
        page.locator("[data-review-question]").filter(has_text=QUESTION).wait_for()
        assert len(generations) == 1
        assert ANSWER not in page.content()
        assert page.locator("[data-review-grade]").count() == 0
        assert client.get("/api/review/session?course=review-integration").json()["count"] == 1

        if legacy:
            topic = cli.read_topic("review-integration")
            saved_card = topic.metadata["review_due"][0]["review_card"]
            saved_card.pop("explanation_kind")
            saved_card["explanation"] = "Check your recall against the answer before rating."
            cli.write_topic(topic.path, topic.metadata, topic.body)
        page.reload()
        page.get_by_role("button", name="Show answer and explanation", exact=True).wait_for()
        assert len(generations) == 1
        page.locator("[data-review-question]").click()
        page.keyboard.press("Space")
        page.locator("[data-review-answer]").wait_for()
        assert ANSWER in page.locator("[data-review-panel]").inner_text()
        assert (EXPLANATION in page.locator("[data-review-panel]").inner_text()) is not legacy
        assert page.locator(".review-explanation").count() == (0 if legacy else 1)
        assert "Check your recall" not in page.locator("[data-review-panel]").inner_text()
        assert page.locator(".review-sources, .review-hint, .review-rating-meaning").count() == 0
        assert page.locator("[data-review-grade]").count() == 4
        assert cli.read_topic("review-integration").metadata["review_due"][0]["due"] == cli.today()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        screenshots = Path(__file__).resolve().parents[1] / ".artifacts" / "qa"
        screenshots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshots / f"review-answer-{width}{'-legacy' if legacy else ''}.png"), full_page=True)

        page.keyboard.press("1")
        page.get_by_role("heading", name="Your next card returns soon").wait_for()
        first = cli.read_topic("review-integration").metadata["review_due"][0]
        assert datetime.fromisoformat(first["relearn_at"]) == clock[0] + timedelta(minutes=1)
        dashboard = client.get("/dashboard?course=review-integration")
        assert "Review 1 item" in dashboard.text
        assert "/review?course=review-integration" in dashboard.text
        page.reload()
        page.get_by_role("heading", name="Your next card returns soon").wait_for()
        assert cli.read_topic("review-integration").metadata["review_due"][0]["relearn_at"] == first["relearn_at"]
        assert len(generations) == 1

        clock[0] += timedelta(seconds=61)
        page.reload()
        page.get_by_role("button", name="Show answer and explanation", exact=True).click()
        page.locator("[data-review-grade='good']").click()
        page.get_by_role("heading", name="Review complete", exact=True).wait_for()
        assert client.get("/api/review/session?course=review-integration").json()["count"] == 0
        topic = cli.read_topic("review-integration")
        assert topic.metadata["review_due"][0]["due"] == "2026-10-10"
        if legacy:
            assert topic.metadata["review_due"][0]["review_card"] == saved_card
        assert not topic.metadata.get("known")
        assert not errors
        context.close()
        browser.close()


def test_real_service_two_tabs_cannot_rate_same_occurrence(review_fixture):
    playwright = pytest.importorskip("playwright.sync_api")
    client, _clock, _generations = review_fixture
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        context = browser.new_context()
        bridge(context, client)
        first = context.new_page()
        first.goto("http://127.0.0.1/review?course=review-integration")
        first.get_by_role("button", name="Prepare card", exact=True).click()
        first.get_by_role("button", name="Show answer and explanation", exact=True).click()
        second = context.new_page()
        second.goto("http://127.0.0.1/review?course=review-integration")
        second.get_by_role("button", name="Show answer and explanation", exact=True).click()
        first.locator("[data-review-grade='good']").click()
        first.get_by_role("heading", name="Review complete", exact=True).wait_for()
        second.locator("[data-review-grade='easy']").click()
        second.get_by_role("button", name="Refresh review", exact=True).wait_for()
        assert cli.read_topic("review-integration").metadata["review_due"][0]["due"] == "2026-10-10"
        second.get_by_role("button", name="Refresh review", exact=True).click()
        second.get_by_role("heading", name="Review complete", exact=True).wait_for()
        context.close()
        browser.close()


@pytest.mark.parametrize("width,height", [(1280, 800), (375, 812)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_saved_long_card_readable_and_ratable_without_rewrite(review_fixture, width, height, theme):
    playwright = pytest.importorskip("playwright.sync_api")
    client, _clock, generations = review_fixture
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
        bridge(context, client)
        page = context.new_page()
        page.goto("http://127.0.0.1/review?course=review-integration")
        page.get_by_role("button", name="Prepare card", exact=True).click()
        page.get_by_role("button", name="Show answer and explanation", exact=True).wait_for()
        topic = cli.read_topic("review-integration")
        saved = topic.metadata["review_due"][0]["review_card"]
        saved.update(question=LONG_QUESTION, answer=LONG_ANSWER, explanation=LONG_EXPLANATION)
        before = deepcopy(saved)
        cli.write_topic(topic.path, topic.metadata, topic.body)
        page.reload()
        page.evaluate("theme => setTheme(theme)", theme)
        assert LONG_ANSWER not in page.content()
        page.get_by_role("button", name="Show answer and explanation", exact=True).click()
        page.locator("[data-review-answer]").wait_for()
        assert page.locator("[data-review-question]").inner_text() == LONG_QUESTION
        for selector, expected in ((".review-answer-text", LONG_ANSWER), (".review-explanation", LONG_EXPLANATION)):
            visible = " ".join(page.locator(selector).all_text_contents())
            assert re.sub(r"\s+", " ", visible).strip() == expected
        for control in [*page.locator("[data-review-grade]").all(), page.get_by_role("button", name="Skip for now", exact=True)]:
            rect = control.bounding_box()
            assert rect and rect["y"] >= 0 and rect["y"] + rect["height"] <= height
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        screenshots = Path(__file__).resolve().parents[1] / ".artifacts" / "review-readability"
        screenshots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshots / f"saved-long-{theme}-{width}.png"))
        last = page.locator(".review-explanation").last
        last.evaluate("node => node.scrollIntoView({block: 'end'})")
        rect = last.bounding_box()
        rating = page.locator("[data-review-grade]").first.bounding_box()
        assert rect and rating and rect["y"] >= 0
        assert rect["y"] + rect["height"] <= rating["y"]
        assert cli.read_topic("review-integration").metadata["review_due"][0]["review_card"] == before
        assert len(generations) == 1
        page.locator("[data-review-grade='good']").click()
        page.get_by_role("heading", name="Review complete", exact=True).wait_for()
        assert cli.read_topic("review-integration").metadata["review_due"][0]["review_card"] == before
        assert len(generations) == 1
        context.close()
        browser.close()
