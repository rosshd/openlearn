"""Course source permission is durable, scoped, and never inferred."""
from unittest import mock
import os

import pytest

from openlearn import cli, source_context, source_imports, tutor_service
from openlearn.web import source_consent
from openlearn.web.services import OpenLearnWebServices
from tests.test_source_context import web_request

pytest_plugins = ["tests.test_source_context"]


def test_course_approval_survives_turns_and_reload(course):
    topic, _ = course
    service = OpenLearnWebServices()
    first = web_request(topic)
    preview = service.preview_source_turn(topic.slug, first)
    assert preview["course_approved"] is False
    first.source_approval = preview["approval"]
    first.source_course_approval = True
    with mock.patch.object(tutor_service, "start_turn", side_effect=RuntimeError("authorized")) as start:
        with pytest.raises(RuntimeError, match="authorized"):
            service.submit_turn(topic.slug, first)
    start.assert_called_once()
    assert source_consent.approved(topic.slug)
    cli.clear_config_cache()
    for intent in ("next", "answer", "question"):
        request = web_request(cli.read_topic(topic.slug), intent=intent)
        assert service.preview_source_turn(topic.slug, request)["course_approved"] is True
        with mock.patch.object(tutor_service, "start_turn", side_effect=RuntimeError("authorized")):
            with pytest.raises(RuntimeError, match="authorized"):
                service.submit_turn(topic.slug, request)


def test_unapproved_flag_does_not_bypass_server(course):
    topic, _ = course
    request = web_request(topic)
    request.source_course_approval = True
    with mock.patch.object(tutor_service, "start_turn") as start:
        assert OpenLearnWebServices().submit_turn(topic.slug, request)["state"] == "conflict"
    start.assert_not_called()
    assert not source_consent.approved(topic.slug)


@pytest.mark.parametrize("change", ["source", "new_source", "provider", "model", "version", "revoke"])
def test_course_permission_invalidates_on_scope_change(course, monkeypatch, change):
    topic, record = course
    assert source_consent.grant(topic.slug, source_consent.scope(topic.slug))
    if change == "source":
        (cli.topic_context_dir(topic.slug) / record.context_file).write_text("Changed source content.")
    elif change == "new_source":
        path = cli.project_home() / "other.md"
        path.write_text("Another required stack rule.")
        source_imports.import_course_source(source_imports.CourseSourceImportRequest(topic.slug, source_imports.LocalFileSource(path)))
    elif change == "provider":
        monkeypatch.setenv("OPENLEARN_BASE_URL", "https://example.invalid/v1")
    elif change == "model":
        monkeypatch.setenv("OPENLEARN_MODEL", "different-model")
    elif change == "version":
        monkeypatch.setattr(source_consent, "VERSION", 2)
    else:
        source_consent.revoke(topic.slug)
    assert not source_consent.approved(topic.slug)


def test_stale_or_cross_course_scope_cannot_grant(course):
    topic, record = course
    scope = source_consent.scope(topic.slug)
    path = cli.topic_context_dir(topic.slug) / record.context_file
    path.write_text("Changed content.")
    assert not source_consent.grant(topic.slug, scope)
    assert not source_consent.approved(topic.slug)


def test_preview_and_cancel_never_grant(course):
    topic, _ = course
    before = cli.load_state(topic.slug)
    with mock.patch.object(cli, "call_openai") as provider:
        OpenLearnWebServices().preview_source_turn(topic.slug, web_request(topic))
    provider.assert_not_called()
    assert cli.load_state(topic.slug) == before
    assert not source_consent.approved(topic.slug)


def test_http_creation_requires_explicit_permission_and_stale_replay_cannot_regrant(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from openlearn.web.app import create_app
    from uuid import uuid4
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    monkeypatch.setenv("OPENLEARN_BASE_URL", source_context.APPROVED_BASE_URL)
    monkeypatch.setenv("OPENLEARN_MODEL", source_context.APPROVED_MODEL)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cli.clear_config_cache()
    with TestClient(create_app(testing=True)) as client:
        token = client.get("/quick-learn").cookies["openlearn_csrf"]
        headers = {"X-CSRF-Token": token, "Accept": "application/json"}
        data = {"title": "Stack source", "goal": "Learn stacks", "submission_id": str(uuid4()),
                "mode": "quick", "source_kind": "file"}
        files = {"source_file": ("notes.md", b"A required stack rule is last in, first out.")}
        with mock.patch.object(tutor_service, "start_turn") as start:
            response = client.post("/courses/from-source", data=data, files=files, headers=headers)
            assert response.status_code == 422
            assert not cli.topic_path("stack-source").exists()
            data["source_course_approval"] = "true"
            response = client.post("/courses/from-source", data=data, files=files, headers=headers)
            assert response.status_code == 200
            assert source_consent.approved("stack-source")
            start.assert_not_called()
        assert client.delete("/api/courses/stack-source/source-consent").status_code == 403
        assert source_consent.approved("stack-source")
        assert client.delete("/api/courses/stack-source/source-consent", headers=headers).json()["ok"]
        assert not source_consent.approved("stack-source")
        assert client.post("/courses/from-source", data=data, files=files, headers=headers).json()["created"] is False
        assert not source_consent.approved("stack-source")
    cli.clear_config_cache()


@pytest.mark.parametrize("entry", ["quick-learn", "courses/new"])
@pytest.mark.skipif(os.environ.get("OPENLEARN_BROWSER_TEST") != "1", reason="requires installed browser")
def test_creation_then_successive_continue_never_reopens_permission(course, entry):
    import json
    import socket
    import threading
    import time
    import uvicorn
    from playwright.sync_api import sync_playwright, expect
    from openlearn.web.app import create_app
    from tests.test_source_lessons import lesson
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    app = create_app(testing=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.02)
    assert server.started
    origin = f"http://127.0.0.1:{port}"
    base = origin + app.state.security.url_namespace
    def planner(*args, **kwargs):
        identity = source_imports.list_course_sources("approved-stack")[0].source_id
        return json.dumps({"concepts": [
            {"label": "Stack order", "objective": "Explain last-in, first-out.", "source_ids": [identity]},
            {"label": "Stack operations", "objective": "Predict pushes and pops.", "source_ids": [identity]},
        ]})
    try:
        with mock.patch.object(cli, "call_openai", side_effect=planner) as plans, mock.patch.object(
            cli, "call_openai_streaming", side_effect=[lesson(), lesson("Stack operations"),
                "**Check:**\nAfter pushing red then blue, which item is removed first?"]
        ), sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{origin}/?access_token={app.state.security.access_token}")
            page.goto(f"{base}/{entry}")
            page.get_by_label("Course name", exact=True).fill("Approved stack")
            page.get_by_label("Your goal", exact=True).fill("Understand stack operations")
            page.get_by_label("Source file", exact=True).set_input_files({
                "name": "stack.md", "mimeType": "text/markdown",
                "buffer": b"The required stack rule is last-in, first-out.\nPush adds an item; pop removes the last item.\n",
            })
            submit = page.get_by_role("button", name="Create Quick Learn" if entry == "quick-learn" else "Create course", exact=True)
            submit.click()
            expect(page.locator("[data-form-error]")).to_contain_text("Approve source use")
            assert not cli.topic_path("approved-stack").exists()
            page.locator("[data-creation-consent-check]").check()
            for theme in ("light", "dark"):
                page.evaluate("theme => setTheme(theme)", theme)
                for width in (390, 1280):
                    page.set_viewport_size({"width": width, "height": 900})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            submit.click()
            page.wait_for_url("**/courses/approved-stack?tool=chat")
            assert source_consent.approved("approved-stack")
            for fragment in ("blue plate", "Stack operations", "which item"):
                page.get_by_role("button", name="Continue", exact=True).click()
                expect(page.locator("[data-current-move]")).to_contain_text(fragment, timeout=10000)
                expect(page.locator("[data-source-preview]")).not_to_be_visible()
                page.reload()
                expect(page.locator("[data-current-move]")).to_contain_text(fragment)
            assert plans.call_count == 1
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_permission_does_not_transfer_to_another_course(course):
    import argparse
    topic, _ = course
    original_scope = source_consent.scope(topic.slug)
    cli.cmd_new(argparse.Namespace(topic="Other stack", goal="Learn stacks"), output_func=lambda _: None)
    assert not source_consent.grant("other-stack", original_scope)
    assert not source_consent.approved("other-stack")


def test_permission_record_is_not_in_model_context(course):
    topic, _ = course
    assert source_consent.grant(topic.slug, source_consent.scope(topic.slug))
    preview = OpenLearnWebServices().preview_source_turn(topic.slug, web_request(topic))
    assert preview["course_approved"]
    assert source_consent.KEY not in preview["preview"]
    assert "approved_at" not in preview["preview"]
    assert "consent_scope" not in preview
    saved = cli.load_state(topic.slug)["_openlearn_internal"][source_consent.KEY]
    assert saved["scope"] not in preview["preview"]


def test_first_remembered_request_still_commits(course):
    from concurrent.futures import ThreadPoolExecutor
    from tests.test_source_lessons import plan, lesson
    topic, record = course
    metadata, body = cli.parse_topic(topic.path.read_text())
    metadata["web_source_start"] = True
    cli.write_text_atomic(topic.path, cli.format_topic(metadata, body))
    request = web_request(cli.read_topic(topic.slug), intent="next", text="")
    service = OpenLearnWebServices()
    request.source_approval = service.preview_source_turn(topic.slug, request)["approval"]
    request.source_course_approval = True
    with mock.patch.object(cli, "call_openai", return_value=plan(record)), mock.patch.object(
        cli, "call_openai_streaming", return_value=lesson()
    ), ThreadPoolExecutor(max_workers=1) as executor, mock.patch.object(tutor_service, "_EXECUTOR", executor):
        service.submit_turn(topic.slug, request)
    result = tutor_service.operation_status(topic.slug, request.submission_id)
    assert result.status == "committed", result.error_message


def test_permission_scope_uses_the_same_read_budget_as_source_selection(course, monkeypatch):
    topic, record = course
    directory = cli.topic_context_dir(topic.slug)
    private = directory / "aa-profile.md"
    private.write_text("x" * 90)
    monkeypatch.setattr(cli, "context_source_files", lambda slug: [private, directory / record.context_file])
    monkeypatch.setattr(source_context, "TOTAL_READ_BYTE_LIMIT", 100)
    assert source_consent.grant(topic.slug, source_consent.scope(topic.slug))
    # Excluded profile files must not consume the fingerprint budget and hide
    # a later eligible file that source selection can still send.
    (directory / record.context_file).write_text("A required stack rule is first in, first out.\n")
    assert not source_consent.approved(topic.slug)


def test_revocation_invalidates_an_unused_approval_token(course):
    topic, _ = course
    service = OpenLearnWebServices()
    request = web_request(topic)
    request.source_approval = service.preview_source_turn(topic.slug, request)["approval"]
    request.source_course_approval = True
    source_consent.revoke(topic.slug)
    with mock.patch.object(tutor_service, "start_turn") as start:
        assert service.submit_turn(topic.slug, request)["state"] == "conflict"
    start.assert_not_called()
    assert not source_consent.approved(topic.slug)
