"""Provider setup handoff regressions, with synthetic credentials only."""
from types import SimpleNamespace
from pathlib import Path
import os
from urllib.parse import urlsplit

import pytest
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from openlearn import cli, config, providers
from openlearn.web import create_app
from openlearn.web.services import OpenLearnWebServices


@pytest.fixture
def isolated_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    for name in ("OPENLEARN_MOCK", "OPENAI_API_KEY", "OPENLEARN_API_KEY",
                 "ANTHROPIC_API_KEY", "OPENLEARN_BASE_URL", "OPENLEARN_MODEL",
                 "OPENLEARN_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    cli.clear_config_cache()
    yield tmp_path
    cli.clear_config_cache()


@pytest.mark.parametrize(("validation", "code"), [
    (providers.ValidationResult(providers.ValidationStatus.REJECTED), "provider_credentials"),
    (providers.ValidationResult(providers.ValidationStatus.NETWORK_ERROR), "provider_unavailable"),
    (providers.ValidationResult(providers.ValidationStatus.HTTP_ERROR, "http_429"), "provider_rate_limited"),
    (providers.ValidationResult(providers.ValidationStatus.HTTP_ERROR, "http_404"), "provider_unavailable"),
    (providers.ValidationResult(providers.ValidationStatus.HTTP_ERROR, "model_unavailable"), "provider_unavailable"),
])
def test_validation_preserves_safe_failure_category(isolated_provider, monkeypatch, validation, code):
    service = OpenLearnWebServices()
    monkeypatch.setattr(service, "provider_status", lambda: {
        "ready": False, "key_configured": True, "managed": False,
        "reason": "Test this provider before starting a lesson.",
    })
    monkeypatch.setattr(config, "effective_provider_credentials", lambda: SimpleNamespace(
        base_url="https://provider.invalid/v1", model="synthetic-model", api_key="synthetic-only"))
    monkeypatch.setattr(providers, "validate_provider", lambda *_a, **_k: validation)
    status = service.ensure_provider_ready()
    assert status["ready"] is False
    assert status["error_code"] == code
    assert "synthetic-only" not in str(status)


@pytest.mark.parametrize("code", ["provider_unavailable", "provider_rate_limited"])
def test_transient_creation_gate_does_not_request_credentials(isolated_provider, monkeypatch, code):
    service = OpenLearnWebServices()
    monkeypatch.setattr(service, "ensure_provider_ready", lambda: {
        "ready": False, "error_code": code, "reason": "Temporarily unavailable. Retry."})
    with TestClient(create_app(testing=True, services=service)) as client:
        page = client.get("/courses/new")
        response = client.post("/api/courses", headers={"x-csrf-token": page.cookies["openlearn_csrf"]},
            json={"title": "Synthetic recovery", "goal": "Learn stacks", "experience": "Python",
                  "submission_id": "00000000-0000-4000-8000-000000000001"})
    assert response.status_code == 503
    assert response.json()["state"] == "provider_error"
    assert "setup_url" not in response.json()
    assert not list(isolated_provider.glob("learning-topics/*.md"))


def test_creation_contains_existing_provider_setup_with_empty_key(isolated_provider):
    with TestClient(create_app(testing=True)) as client:
        page = client.get("/courses/new")
    assert "data-provider-setup-dialog" in page.text
    assert 'id="api-key"' in page.text
    assert "Test and save" in page.text


@pytest.mark.parametrize("rejected", [False, True])
def test_missing_or_rejected_setup_is_actionable(isolated_provider, monkeypatch, rejected):
    service = OpenLearnWebServices()
    if rejected:
        monkeypatch.setattr(service, "ensure_provider_ready", lambda: {
            "ready": False, "error_code": "provider_credentials", "reason": "That API key was rejected."})
    with TestClient(create_app(testing=True, services=service)) as client:
        page = client.get("/courses/new")
        response = client.post("/api/courses", headers={"x-csrf-token": page.cookies["openlearn_csrf"]},
            json={"title": "Synthetic recovery", "goal": "Learn stacks", "experience": "Python",
                  "submission_id": "00000000-0000-4000-8000-000000000001"})
    assert response.status_code == 428
    assert response.json()["state"] == "setup_required"
    assert response.json()["setup_url"].endswith("/setup")
    if rejected:
        assert "rejected" in response.json()["error"]
    assert not list(isolated_provider.glob("learning-topics/*.md"))


@pytest.mark.parametrize("code", ["provider_credentials", "provider_unavailable"])
def test_form_post_preserves_input_and_identity(isolated_provider, monkeypatch, code):
    service = OpenLearnWebServices()
    monkeypatch.setattr(service, "ensure_provider_ready", lambda: {
        "ready": False, "error_code": code, "reason": "Synthetic provider failure."})
    with TestClient(create_app(testing=True, services=service)) as client:
        page = client.get("/courses/new")
        response = client.post("/courses/new", headers={"x-csrf-token": page.cookies["openlearn_csrf"]},
            data={"title": "Synthetic recovery", "goal": "Learn <stacks>", "experience": "Some Python",
                  "template_id": "", "submission_id": "00000000-0000-4000-8000-000000000001"})
    assert response.status_code == (428 if code == "provider_credentials" else 503)
    assert 'value="Synthetic recovery"' in response.text
    assert "Learn &lt;stacks&gt;" in response.text
    assert "Some Python</textarea>" in response.text
    assert 'data-uuid value="00000000-0000-4000-8000-000000000001"' in response.text
    assert (('data-provider-recovery-message="Synthetic provider failure."') in response.text) == (code == "provider_credentials")
    assert not list(isolated_provider.glob("learning-topics/*.md"))


@pytest.fixture
def recovery_browser(isolated_provider, monkeypatch, request):
    if os.environ.get("OPENLEARN_BROWSER_TEST") != "1":
        pytest.skip("set OPENLEARN_BROWSER_TEST=1 after installing a Playwright browser")
    playwright = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    mode = getattr(request, "param", "create")
    start_path = "/courses/new"
    service = OpenLearnWebServices()
    if mode != "create":
        start_path = "/courses/synthetic/initializing/00000000-0000-4000-8000-000000000001"
        monkeypatch.setattr(service, "course_initialization", lambda *_args: {
            "slug": "synthetic", "title": "Synthetic recovery", "operation_id": "00000000-0000-4000-8000-000000000001",
            "state": "retryable_error", "error": "Synthetic provider failure.", "error_code": mode,
        })
    with TestClient(create_app(testing=True, services=service)) as client:
        html = client.get(start_path).text.replace("http://testserver", "https://openlearn.test")
    static = Path(__file__).resolve().parents[1] / "src/openlearn/web/static"
    calls = {"create": [], "setup": [], "retry": []}
    responses = {
        "create": (428, {"error": "Test the provider connection.", "state": "setup_required"}),
        "setup": (200, {"ok": True, "ready": True}),
        "retry": (202, {"state": "committed"}),
    }
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def route_request(route):
            path = urlsplit(route.request.url).path
            if path in ("/api/courses", "/api/setup") or path.endswith("/retry"):
                name = "retry" if path.endswith("/retry") else "create" if path == "/api/courses" else "setup"
                calls[name].append(route.request.post_data_json)
                status, body = responses[name]
                if status == 0:
                    responses.setdefault("held", []).append(route)
                else:
                    route.fulfill(status=status, json=body)
            elif path in ("/static/openlearn.js", "/static/openlearn.css"):
                route.fulfill(content_type="text/javascript" if path.endswith(".js") else "text/css",
                              body=(static / Path(path).name).read_text())
            elif path == start_path:
                route.fulfill(content_type="text/html", body=html)
            else:
                route.fulfill(content_type="text/html", body="<h1>Synthetic destination</h1>")
        page.route("**/*", route_request)
        page.goto(f"https://openlearn.test{start_path}")
        if mode == "create":
            page.locator("[data-template-choice]").first.click()
            page.locator("#course-title").fill("Synthetic recovery")
            page.locator("#goal").fill("Learn stacks")
            page.locator("#experience").fill("Some Python")
        try:
            yield page, calls, responses, playwright.expect
        finally:
            browser.close()
            assert errors == []


def test_browser_cancel_refresh_and_single_validated_resume(recovery_browser):
    page, calls, responses, expect = recovery_browser
    identity = page.locator("[name=submission_id]").input_value()
    template = page.locator("[name=template_id]").input_value()
    page.locator(".create-form").evaluate("form => { form.requestSubmit(); form.requestSubmit(); }")
    dialog = page.locator("[data-provider-setup-dialog]")
    expect(dialog).to_be_visible()
    assert len(calls["create"]) == 1
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    assert len(calls["create"]) == 1
    page.reload()
    assert page.locator("#course-title").input_value() == "Synthetic recovery"
    assert page.locator("#goal").input_value() == "Learn stacks"
    assert page.locator("#experience").input_value() == "Some Python"
    assert page.locator("[name=submission_id]").input_value() == identity
    page.locator(".create-form button[type=submit]").click()
    expect(dialog).to_be_visible()
    responses["setup"] = (422, {"error": "That API key was rejected."})
    page.locator("#api-key").fill("synthetic-browser-secret")
    assert "synthetic-browser-secret" not in page.evaluate("JSON.stringify(sessionStorage)")
    dialog.get_by_role("button", name="Test and save").click()
    expect(dialog.locator("[data-form-error]")).to_contain_text("rejected")
    assert page.locator("#api-key").input_value() == ""
    assert len(calls["create"]) == 2
    responses["setup"] = (200, {"ok": True, "ready": False, "message": "Saved unverified."})
    dialog.get_by_role("button", name="Test and save").click()
    expect(dialog.locator("[data-form-status]")).to_contain_text("unverified")
    expect(dialog).to_be_visible()
    assert len(calls["create"]) == 2
    responses["setup"] = (200, {"ok": True, "ready": True})
    responses["create"] = (202, {"ok": True, "initialization_url": "/courses/synthetic/initializing/once"})
    page.set_viewport_size({"width": 320, "height": 720})
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    dialog.locator("form").evaluate("form => { form.requestSubmit(); form.requestSubmit(); }")
    page.wait_for_url("**/courses/synthetic/initializing/once")
    assert len(calls["create"]) == 3
    assert len(calls["setup"]) == 3
    assert all(call["submission_id"] == identity for call in calls["create"])
    assert all(call["template_id"] == template for call in calls["create"])
    assert calls["create"][-1]["experience"] == "Some Python"
    assert page.evaluate("Object.keys(sessionStorage).filter(key => key.startsWith('openlearn-course-draft:')).length") == 0
    page.reload()
    assert len(calls["create"]) == 3


@pytest.mark.parametrize("code", ["provider_rate_limited", "provider_unavailable"])
def test_browser_transient_failure_keeps_draft_without_setup(recovery_browser, code):
    page, calls, responses, expect = recovery_browser
    responses["create"] = (503, {"error": "Temporary provider problem. Retry later.",
                                "state": "provider_error", "error_code": code})
    page.locator(".create-form button[type=submit]").click()
    expect(page.locator(".create-form [data-form-error]")).to_contain_text("Temporary")
    expect(page.locator("[data-provider-setup-dialog]")).not_to_be_visible()
    identity = calls["create"][0]["submission_id"]
    page.reload()
    assert page.locator("[name=submission_id]").input_value() == identity
    assert page.locator("#goal").input_value() == "Learn stacks"
    assert len(calls["create"]) == 1


def test_browser_cancel_during_validation_does_not_resume(recovery_browser):
    page, calls, responses, expect = recovery_browser
    page.locator(".create-form button[type=submit]").click()
    dialog = page.locator("[data-provider-setup-dialog]")
    expect(dialog).to_be_visible()
    responses["setup"] = (0, {})
    dialog.get_by_role("button", name="Test and save").click()
    page.wait_for_function("document.querySelector('[data-provider-recovery-form]').dataset.submitting === 'true'")
    dialog.get_by_role("button", name="Cancel and return to lesson").click()
    responses["held"][0].fulfill(status=200, json={"ok": True, "ready": True})
    page.wait_for_function("document.querySelector('[data-provider-recovery-form]').dataset.submitting !== 'true'")
    expect(dialog).not_to_be_visible()
    assert len(calls["create"]) == 1
    assert page.locator("#goal").input_value() == "Learn stacks"


@pytest.mark.parametrize("recovery_browser", ["provider_credentials"], indirect=True)
def test_browser_rejected_initialization_resumes_same_operation_once(recovery_browser):
    page, calls, responses, expect = recovery_browser
    dialog = page.locator("[data-provider-setup-dialog]")
    expect(dialog).to_be_visible()
    assert page.locator("#provider-recovery-title").evaluate("node => node === document.activeElement")
    page.keyboard.press("Tab")
    assert page.evaluate("document.querySelector('[data-provider-setup-dialog]').contains(document.activeElement)")
    dialog.locator("form").evaluate("form => { form.requestSubmit(); form.requestSubmit(); }")
    page.wait_for_url("**/courses/synthetic")
    assert len(calls["setup"]) == len(calls["retry"]) == 1
    page.reload()
    assert len(calls["retry"]) == 1


@pytest.mark.parametrize("recovery_browser", ["provider_rate_limited", "provider_unavailable", "turn_failure"], indirect=True)
def test_browser_non_auth_initialization_does_not_open_setup(recovery_browser):
    page, calls, responses, expect = recovery_browser
    expect(page.locator("[data-provider-setup-dialog]")).not_to_be_visible()
    expect(page.locator("[data-initialization-retry]")).to_be_visible()
    assert calls["setup"] == calls["retry"] == []
