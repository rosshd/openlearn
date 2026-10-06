"""Browser review journeys bridged to the real ASGI services in an isolated home."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
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
            "explanation": "Check that you allowed valid outliers and distinguished noise.",
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
def test_real_service_preparation_offline_relearning_and_completion(review_fixture, width):
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

        page.reload()
        page.get_by_role("button", name="Show Answer", exact=True).wait_for()
        assert len(generations) == 1
        page.locator("[data-review-question]").click()
        page.keyboard.press("Space")
        page.locator("[data-review-answer]").wait_for()
        assert ANSWER in page.locator("[data-review-panel]").inner_text()
        assert page.locator("[data-review-grade]").count() == 4
        assert cli.read_topic("review-integration").metadata["review_due"][0]["due"] == cli.today()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        screenshots = Path(__file__).resolve().parents[1] / ".artifacts" / "qa"
        screenshots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshots / f"review-answer-{width}.png"), full_page=True)

        page.keyboard.press("1")
        page.get_by_role("heading", name="Your next card returns soon").wait_for()
        first = cli.read_topic("review-integration").metadata["review_due"][0]
        assert datetime.fromisoformat(first["relearn_at"]) == clock[0] + timedelta(minutes=1)
        dashboard = client.get("/dashboard?course=review-integration")
        assert "Start focused review" in dashboard.text
        assert "/review?course=review-integration" in dashboard.text
        page.reload()
        page.get_by_role("heading", name="Your next card returns soon").wait_for()
        assert cli.read_topic("review-integration").metadata["review_due"][0]["relearn_at"] == first["relearn_at"]
        assert len(generations) == 1

        clock[0] += timedelta(seconds=61)
        page.reload()
        page.get_by_role("button", name="Show Answer", exact=True).click()
        page.locator("[data-review-grade='good']").click()
        page.get_by_role("heading", name="Review complete", exact=True).wait_for()
        assert client.get("/api/review/session?course=review-integration").json()["count"] == 0
        topic = cli.read_topic("review-integration")
        assert topic.metadata["review_due"][0]["due"] == "2026-10-10"
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
        first.get_by_role("button", name="Show Answer", exact=True).click()
        second = context.new_page()
        second.goto("http://127.0.0.1/review?course=review-integration")
        second.get_by_role("button", name="Show Answer", exact=True).click()
        first.locator("[data-review-grade='good']").click()
        first.get_by_role("heading", name="Review complete", exact=True).wait_for()
        second.locator("[data-review-grade='easy']").click()
        second.get_by_role("button", name="Refresh review", exact=True).wait_for()
        assert cli.read_topic("review-integration").metadata["review_due"][0]["due"] == "2026-10-10"
        second.get_by_role("button", name="Refresh review", exact=True).click()
        second.get_by_role("heading", name="Review complete", exact=True).wait_for()
        context.close()
        browser.close()
