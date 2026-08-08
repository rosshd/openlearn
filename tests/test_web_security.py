from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from openlearn.web import create_app
from openlearn.web.schemas import canonical_slug
from openlearn.web.security import MAX_REQUEST_BODY, BrowserSecurity


class WebStub:
    def provider_status(self) -> dict[str, Any]:
        return {"ready": True, "managed": False, "model": "safe-model"}

    def configure_provider(self, request: Any) -> dict[str, Any]:
        return {"ok": True, "ready": True, "model": request.model}

    def dashboard(self) -> dict[str, Any]:
        return {
            "active_course": None,
            "due_reviews": 0,
            "courses": [
                {
                    "slug": "safe-course",
                    "title": '<img src=x onerror="alert(1)">',
                    "current_unit": "Foundations",
                    "progress": 10,
                }
            ],
        }

    def course_templates(self) -> list[dict[str, Any]]:
        return []

    def create_course(self, request: Any) -> dict[str, Any]:
        return {"ok": True, "slug": "safe-course"}

    def focus(self, slug: str) -> dict[str, Any]:
        return {
            "slug": slug,
            "title": "Safe course",
            "revision": 1,
            "saved_state": "Saved locally",
            "move": {
                "kind": "Explain",
                "title": "A current move",
                "content": '<script>alert("stored")</script>',
            },
            "progress": {"percent": 10, "summary": "In progress"},
        }

    def submit_turn(self, slug: str, request: Any) -> dict[str, Any]:
        return {"state": "saved", "operation_id": "76ee346c-d4d9-4d38-bbe2-a838d8caa415"}

    def operation_status(self, slug: str, operation_id: str) -> dict[str, Any]:
        return {"state": "committed"}

    def history(self, slug: str, *, page: int) -> dict[str, Any]:
        return {"items": [], "page": page, "has_more": False}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(WebStub(), testing=True))


def test_local_reads_require_launch_capability_and_bootstrap_to_http_only_cookie() -> None:
    security = BrowserSecurity.local(allow_test_client=True)
    protected = TestClient(create_app(WebStub(), security=security))

    denied = protected.get("/dashboard")
    assert denied.status_code == 401
    assert denied.json() == {"error": "local_access_required"}

    bootstrap = protected.get(f"/?access_token={security.access_token}", follow_redirects=False)
    assert bootstrap.status_code == 303
    assert bootstrap.headers["location"] == f"{security.url_namespace}/"
    assert "openlearn_session=" in bootstrap.headers["set-cookie"]
    assert "HttpOnly" in bootstrap.headers["set-cookie"]
    assert f"Path={security.url_namespace}" in bootstrap.headers["set-cookie"]
    assert security.access_token not in bootstrap.headers["location"]

    allowed = protected.get(f"{security.url_namespace}/dashboard")
    assert allowed.status_code == 200
    assert f"{security.url_namespace}/static/openlearn.js" in allowed.text
    assert f"{security.url_namespace}/dashboard" in allowed.text


def test_cookie_authentication_requires_unguessable_path_namespace() -> None:
    security = BrowserSecurity.local(allow_test_client=True)
    protected = TestClient(create_app(WebStub(), security=security))
    protected.get(f"/?access_token={security.access_token}", follow_redirects=False)

    denied = protected.get("/dashboard")
    manually_replayed = protected.get(
        "/dashboard",
        headers={"cookie": f"openlearn_session={security.access_token}"},
    )

    assert denied.status_code == 401
    assert manually_replayed.status_code == 401
    assert protected.get(f"{security.url_namespace}/dashboard").status_code == 200


def test_capability_header_authenticates_health_probe_without_session_cookie() -> None:
    security = BrowserSecurity.local(allow_test_client=True)
    protected = TestClient(create_app(WebStub(), security=security))

    response = protected.get("/health", headers={"x-openlearn-capability": security.access_token})

    assert response.status_code == 200


def test_every_normal_response_has_browser_security_headers(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert "openlearn_csrf=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=Strict" in response.headers["set-cookie"]


def test_rejects_unknown_host_with_security_headers(client: TestClient) -> None:
    response = client.get("/health", headers={"host": "attacker.example"})

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_host"}
    assert response.headers["x-frame-options"] == "DENY"


def test_mutation_requires_launch_csrf_token(client: TestClient) -> None:
    fresh = TestClient(create_app(WebStub(), testing=True))
    response = fresh.post(
        "/api/setup",
        json={"provider": "openrouter", "api_key": "secret", "model": "safe-model"},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "invalid_csrf_token"}
    assert "secret" not in response.text


def test_same_origin_mutation_accepts_cookie_and_never_echoes_key(client: TestClient) -> None:
    setup = client.get("/setup")
    token = setup.cookies["openlearn_csrf"]

    response = client.post(
        "/api/setup",
        headers={"origin": "http://testserver", "x-csrf-token": token},
        json={"provider": "openrouter", "api_key": "top-secret", "model": "safe-model"},
    )

    assert response.status_code == 200
    assert "top-secret" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_cross_origin_mutation_is_rejected_before_route(client: TestClient) -> None:
    setup = client.get("/setup")
    token = setup.cookies["openlearn_csrf"]

    response = client.post(
        "/api/setup",
        headers={"origin": "https://attacker.example", "x-csrf-token": token},
        json={"provider": "openrouter", "api_key": "secret", "model": "safe-model"},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "invalid_origin"}
    assert "secret" not in response.text


def test_different_loopback_port_is_still_cross_origin(client: TestClient) -> None:
    setup = client.get("/setup")
    token = setup.cookies["openlearn_csrf"]

    response = client.post(
        "/api/setup",
        headers={"origin": "http://testserver:8123", "x-csrf-token": token},
        json={"provider": "openrouter", "api_key": "secret", "model": "safe-model"},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "invalid_origin"}


def test_cross_origin_preflight_is_not_enabled(client: TestClient) -> None:
    response = client.options(
        "/api/setup",
        headers={
            "origin": "https://attacker.example",
            "access-control-request-method": "POST",
        },
    )

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_request_body_limit_is_enforced(client: TestClient) -> None:
    client.get("/health")
    response = client.post(
        "/api/setup",
        content=b"x" * (MAX_REQUEST_BODY + 1),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {"error": "request_too_large"}


def test_templates_escape_stored_course_and_tutor_content(client: TestClient) -> None:
    dashboard = client.get("/dashboard")
    focus = client.get("/courses/safe-course")

    assert '<img src=x onerror="alert(1)">' not in dashboard.text
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in dashboard.text
    assert '<script>alert("stored")</script>' not in focus.text
    assert "&lt;script&gt;alert(&#34;stored&#34;)&lt;/script&gt;" in focus.text


@pytest.mark.parametrize(
    "slug",
    [".hidden", "UPPER", "has%2fslash", "a" * 66],
)
def test_course_routes_reject_noncanonical_slugs(client: TestClient, slug: str) -> None:
    response = client.get(f"/courses/{slug}")

    assert response.status_code == 404


@pytest.mark.parametrize("slug", ["..", ".", "has/slash", "has\\slash", "nul\0byte"])
def test_slug_validator_rejects_unsafe_path_values(slug: str) -> None:
    with pytest.raises(ValueError):
        canonical_slug(slug)
