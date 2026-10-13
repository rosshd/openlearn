"""Approved source turns reserve execution overhead and retain safe failures."""

import json
import os
from dataclasses import replace
from unittest import mock

import pytest

from openlearn import cli, source_context as sources, source_lessons, tutor_service
from openlearn.web.services import OpenLearnWebServices
from tests.test_source_context import web_request
from tests.test_source_lessons import lesson, submit

pytest_plugins = ["tests.test_source_lessons"]


def large_plan(source_id):
    return {"schema": 1, "concepts": [
        {"label": f"Algorithm concept {number}",
         "objective": ("Explain algorithm steps and compare their running time using a small worked example. " * 2).strip(),
         "source_ids": [source_id]}
        for number in range(1, 13)
    ]}


def test_approved_plan_growth_fits_generation_and_repair(source_course):
    topic, record = source_course
    context = sources.snapshot(topic, "Continue to the next useful concept.", sources.APPROVED_MODEL, opted_in=True)
    context = replace(context, prompt=("Algorithm analysis compares running time and memory.\n" * 200)[:8000])
    with pytest.raises(sources.SourceRequestBudgetError):
        sources.request_preview(context)
    metadata = dict(topic.metadata)
    source_lessons.apply_plan(metadata, large_plan(record.source_id))
    metadata["_source_teaching"] = True
    system = sources.tutor_prompt(context, metadata)
    repair = cli.lesson_policy.first_lesson_repair_prompt(context.user)
    assert len(system) + len(repair) <= sources.PROMPT_CHAR_LIMIT


def test_source_validation_has_specific_safe_category():
    metadata = {}
    source_lessons.apply_plan(metadata, large_plan("file:" + "a" * 16))
    with pytest.raises(cli.OpenLearnError) as caught:
        source_lessons.enforce_lesson(metadata, "The classwork asks for a solution.")
    code, message = tutor_service._turn_failure(caught.value)
    assert code == "source_lesson_invalid"
    assert "source lesson" in message


@pytest.mark.parametrize("course", ["\n".join(
    f"Algorithm analysis required concept {n}: " + "running time and memory. " * 16
    for n in range(30)
)], indirect=True)
@pytest.mark.parametrize("repair", [False, True])
def test_large_source_turn_commits_full_plan(source_course, repair):
    topic, record = source_course
    payload = large_plan(record.source_id)
    calls = []
    context = sources.snapshot(topic, "Continue to the next useful concept.", sources.APPROVED_MODEL, opted_in=True)
    assert "omitted to reserve room" in context.prompt
    sources.ensure_unchanged(topic, context, sources.APPROVED_MODEL)

    def planner(model, system, user, **kwargs):
        calls.append((system, user))
        return json.dumps({"concepts": payload["concepts"]})

    def tutor(model, system, user, **kwargs):
        calls.append((system, user))
        if repair and len(calls) == 2:
            return "**Next:** Continue."
        return lesson("Algorithm concept 1")

    with mock.patch.object(cli, "call_openai", side_effect=planner), mock.patch.object(cli, "call_openai_streaming", side_effect=tutor):
        result = submit(OpenLearnWebServices(), web_request(topic, intent="next", text=""), topic.slug)
    assert result.status == "committed", result.error_message
    assert len(calls) == (3 if repair else 2)
    assert all(context.prompt in system for system, _user in calls)
    assert all(len(system) + len(user) <= sources.PROMPT_CHAR_LIMIT for system, user in calls)
    assert cli.read_topic(topic.slug).metadata["source_learning_plan"] == payload


@pytest.mark.parametrize("kind", ["file", "folder", "github", "url"])
@pytest.mark.parametrize("misses", [1000000, 1.2345678901234567e-300])
def test_maximum_plan_fields_and_repair_are_reserved(source_course, kind, misses):
    topic, _ = source_course
    context = sources.snapshot(topic, cli.lesson_policy.COURSE_INITIALIZATION_PROMPT, sources.APPROVED_MODEL, opted_in=True)
    payload = {"schema": 1, "concepts": [
        {"label": '"' * source_lessons.LABEL_CHAR_LIMIT if n == 11 else
         chr(65 + n) + '"' * (source_lessons.LABEL_CHAR_LIMIT - 1),
         "objective": '"' * source_lessons.OBJECTIVE_CHAR_LIMIT,
         "source_ids": [kind + ":" + "a" * 64] * sources.EXCERPT_LIMIT}
        for n in range(source_lessons.MAX_CONCEPTS)
    ]}
    source_lessons.validate_concepts(payload["concepts"])
    metadata = dict(topic.metadata)
    source_lessons.apply_plan(metadata, payload)
    metadata.update(_source_teaching=True, current_unit=12, current_slide=12,
                    current_turn_message_kind="answer", last_answer_status="needs_work",
                    consecutive_misses=misses,
                    last_answer_score=1.2345678901234567e-300,
                    difficulty=1.2345678901234567e-300,
                    consecutive_correct=1.2345678901234567e-300,
                    pending_remediation={"stage": "deferred", "deferred_review_due": "2026-10-12\x0012:00:00.123456789+00:00"})
    metadata["slide_coverage"] = {f"{n}:1": [c["label"]] for n, c in enumerate(payload["concepts"], 1)}
    assert sources._scoped_state(metadata)["pending_remediation"] == metadata["pending_remediation"]
    bound = sources.request_size_bound(context)
    assert bound <= sources.PROMPT_CHAR_LIMIT
    context = replace(context, prompt=context.prompt + "x" * (sources.PROMPT_CHAR_LIMIT - bound))
    bound = sources.request_size_bound(context)
    assert bound == sources.PROMPT_CHAR_LIMIT
    system = sources.tutor_prompt(context, metadata)
    for initializing in (False, True):
        for require_check, forbid_check in ((True, False), (False, True), (False, False)):
            user = sources.generation_user(
                context.user, initializing=initializing, repair=True,
                candidate="x\n" + "x" * (sources.REPAIR_DRAFT_CHAR_LIMIT - 2),
                require_check=require_check, forbid_check=forbid_check,
                forbid_choice_claim=True, reference_error=True,
            )
            assert len(system) + len(user) <= bound
    assert "source_learning_plan" not in system
    assert payload["concepts"][0]["label"] not in sources._scoped_state(metadata)["selected_source_concept"]["label"]
    assert sources._scoped_state(metadata)["source_concept_count"] == 12


@pytest.mark.parametrize("failure,expected", [
    ("plan", "source_plan_invalid"),
    ("lesson", "source_lesson_invalid"),
    ("budget", "source_request_budget"),
])
def test_safe_failure_survives_reload_and_retry(source_course, failure, expected):
    topic, record = source_course
    before = topic.path.read_bytes()
    service = OpenLearnWebServices()
    request = web_request(topic, intent="next", text="")
    payload = json.dumps({"concepts": large_plan(record.source_id)["concepts"]})
    planner_result = "not JSON: PRIVATE PROVIDER RESPONSE" if failure == "plan" else payload
    with mock.patch.object(cli, "call_openai", return_value=planner_result), mock.patch.object(cli, "call_openai_streaming", return_value="**Next:** Continue."):
        if failure == "budget":
            with mock.patch.object(source_lessons, "generate_plan", side_effect=sources.SourceRequestBudgetError()):
                result = submit(service, request, topic.slug)
        else:
            result = submit(service, request, topic.slug)
    assert result.status == "retryable_error"
    assert result.error_code == expected
    assert "PRIVATE" not in result.error_message
    assert topic.path.read_bytes() == before
    assert tutor_service.course_revision(topic.slug) == 0
    saved = cli.load_state(topic.slug)["_openlearn_internal"]
    assert saved["last_turn_error"]["code"] == expected
    assert saved["active_turn"] is None
    assert cli.load_pending_learner_prompt(topic.slug)
    cli.clear_config_cache()
    assert tutor_service.operation_status(topic.slug, request.submission_id).error_code == expected
    with mock.patch.object(cli, "call_openai", return_value=payload) as planner, mock.patch.object(cli, "call_openai_streaming", return_value=lesson("Algorithm concept 1")) as tutor:
        retried = submit(service, request, topic.slug)
        replay = service.submit_turn(topic.slug, request)
    assert retried.status == "committed", retried.error_message
    assert replay["state"] == "conflict"  # The old approval is stale after commit.
    assert planner.call_count == tutor.call_count == 1
    assert tutor_service.course_revision(topic.slug) == 1


def test_unfittable_preview_sends_nothing(source_course):
    topic, _ = source_course
    with mock.patch.object(sources, "PROMPT_CHAR_LIMIT", 100), mock.patch.object(cli, "call_openai") as planner, mock.patch.object(cli, "call_openai_streaming") as tutor:
        preview = OpenLearnWebServices().preview_source_turn(topic.slug, web_request(topic, intent="next", text=""))
    assert not preview["ok"]
    assert "too large" in str(preview)
    planner.assert_not_called()
    tutor.assert_not_called()


@pytest.mark.skipif(os.environ.get("OPENLEARN_BROWSER_TEST") != "1", reason="requires installed Playwright browser")
def test_source_failure_browser_reload_and_retry(source_course):
    import socket
    import threading
    import time
    from pathlib import Path

    import uvicorn
    from playwright.sync_api import expect, sync_playwright

    from openlearn.web import create_app

    topic, record = source_course
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    app = create_app(testing=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    origin = f"http://127.0.0.1:{port}"
    base = origin + app.state.security.url_namespace
    try:
        payload = json.dumps({"concepts": large_plan(record.source_id)["concepts"]})
        with mock.patch.object(cli, "call_openai", side_effect=["invalid", payload]), mock.patch.object(cli, "call_openai_streaming", return_value=lesson("Algorithm concept 1")), sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"{origin}/?access_token={app.state.security.access_token}")
            page.goto(f"{base}/courses/{topic.slug}")
            page.get_by_role("button", name="Continue", exact=True).click()
            page.locator("[data-source-send]").click()
            message = page.locator("span[data-operation-message]")
            expect(message).to_contain_text("usable concept sequence", timeout=10000)
            page.reload()
            expect(message).to_contain_text("usable concept sequence")
            for width in (1280, 390):
                page.set_viewport_size({"width": width, "height": 900})
                expect(message).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                directory = Path(".artifacts/source-budget")
                directory.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(directory / f"error-{width}.png"), full_page=True)
            page.get_by_role("button", name="Continue", exact=True).click()
            page.locator("[data-source-send]").click()
            expect(page.locator("[data-current-move]")).to_contain_text("blue plate", timeout=10000)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_arbitrary_topic_projection_field_is_not_transmitted(source_course):
    topic, record = source_course
    context = sources.snapshot(topic, "Continue to the next useful concept.", sources.APPROVED_MODEL, opted_in=True)
    metadata = {**topic.metadata, "selected_source_concept": {
        "label": "PRIVATE_PROJECTION", "objective": "PRIVATE_OBJECTIVE", "source_ids": [record.source_id],
    }}
    assert "PRIVATE_PROJECTION" not in sources.tutor_prompt(context, metadata)
    assert "PRIVATE_OBJECTIVE" not in sources.tutor_prompt(context, metadata)
