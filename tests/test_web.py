from __future__ import annotations

from pathlib import Path
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from openlearn import cli
from openlearn import config
from openlearn import providers
from openlearn import tutor_service
from openlearn.web import create_app
from openlearn.web.app import PlaceholderServices
from openlearn.web.schemas import TutorSubmissionRequest
from openlearn.web.services import (
    COURSE_INITIALIZATION_PROMPT,
    OpenLearnWebServices,
    _present_response,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cli.clear_config_cache()
    return TestClient(create_app(testing=True))


def csrf(client: TestClient, path: str = "/") -> str:
    response = client.get(path, follow_redirects=False)
    return response.cookies["openlearn_csrf"]


def wait_for_operation(
    client: TestClient, slug: str, operation_id: str, *terminal_states: str
) -> dict[str, object]:
    expected = set(terminal_states or ("committed", "retryable_error", "conflict"))
    for _attempt in range(200):
        response = client.get(f"/api/courses/{slug}/operations/{operation_id}")
        body = response.json()
        if body["state"] in expected:
            return body
        time.sleep(0.01)
    raise AssertionError(f"operation {operation_id} did not reach {sorted(expected)}")


def test_default_web_app_runs_setup_dashboard_course_and_tutor_flow(
    client: TestClient,
) -> None:
    assert "Pick up where you left off" in client.get("/").text
    assert "Pick up where you left off" in client.get("/dashboard").text

    new_course = client.get("/courses/new")
    assert "Technical Interview Prep" in new_course.text
    token = new_course.cookies["openlearn_csrf"]
    create = client.post(
        "/api/courses",
        headers={"origin": "http://testserver", "x-csrf-token": token},
        json={
            "title": "Technical Interview Prep",
            "goal": "Solve interview problems clearly and efficiently.",
            "experience": "I know basic Python.",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    )

    assert create.status_code == 202
    slug = create.json()["slug"]
    operation_id = create.json()["operation_id"]
    initialization_url = create.json()["initialization_url"]
    assert operation_id in initialization_url
    assert create.json()["state"] in {"saved", "generating", "committed"}
    wait_for_operation(client, slug, operation_id, "committed")
    initialized = client.get(initialization_url, follow_redirects=False)
    assert initialized.status_code == 303
    assert initialized.headers["location"].endswith(f"/courses/{slug}")
    focus = client.get(f"/courses/{slug}")
    assert focus.status_code == 200
    assert "Your next learning move" in focus.text
    assert "placement test" not in focus.text.lower()
    assert "**Lesson:**" not in focus.text

    revision = int(focus.text.split('data-revision="', 1)[1].split('"', 1)[0])
    turn = client.post(
        f"/api/courses/{slug}/turns",
        headers={"origin": "http://testserver", "x-csrf-token": token},
        json={
            "intent": "question",
            "text": "Can you explain the tradeoff with an example?",
            "submission_id": str(uuid4()),
            "expected_revision": revision,
        },
    )

    assert turn.status_code == 202
    operation_id = turn.json()["operation_id"]
    assert wait_for_operation(client, slug, operation_id)["state"] == "committed"
    history = client.get(f"/courses/{slug}/history", headers={"accept": "application/json"})
    assert history.status_code == 200
    assert len(history.json()["items"]) == 2
    assert COURSE_INITIALIZATION_PROMPT not in history.text
    assert any(item["title"] == "First lesson" for item in history.json()["items"])


def test_mock_setup_persists_secret_without_echoing_it(client: TestClient) -> None:
    token = csrf(client, "/setup")
    response = client.post(
        "/api/setup",
        headers={"origin": "http://testserver", "x-csrf-token": token},
        json={
            "provider": "openrouter",
            "api_key": "test-secret-key",
            "model": "google/gemini-2.5-flash-lite",
        },
    )

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert "test-secret-key" not in response.text

    revisit = client.post(
        "/api/setup",
        headers={"origin": "http://testserver", "x-csrf-token": token},
        json={
            "provider": "openrouter",
            "api_key": "",
            "model": "a-different-model",
            "base_url": "https://openrouter.ai/api/v1",
        },
    )
    assert revisit.status_code == 200
    assert cli.configured_openai_api_key() == "test-secret-key"


def test_stale_revision_returns_conflict(client: TestClient) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Concurrency",
            "goal": "Understand safe shared state.",
            "experience": "",
            "template_id": None,
            "submission_id": str(uuid4()),
        },
    ).json()
    wait_for_operation(client, created["slug"], created["operation_id"], "committed")

    response = client.post(
        f"/api/courses/{created['slug']}/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "answer",
            "text": "My response",
            "submission_id": str(uuid4()),
            "expected_revision": 0,
        },
    )

    assert response.status_code == 409
    assert response.json()["state"] == "conflict"


def test_focus_recovers_saved_turn_for_explicit_retry(client: TestClient) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Recovery",
            "goal": "Resume without retyping.",
            "experience": "",
            "template_id": None,
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    wait_for_operation(client, slug, created["operation_id"], "committed")
    operation_id = str(uuid4())
    saved_text = "My saved explanation"
    state = cli.load_state(slug)
    internal = state.setdefault("_openlearn_internal", {})
    internal["active_turn"] = {
        "submission_id": operation_id,
        "status": "generating",
        "expected_revision": 1,
        "prompt": saved_text,
        "updated_at": "2026-08-07T12:00:00+00:00",
    }
    state["pending_learner_prompt"] = saved_text
    cli.save_state(slug, state)

    focus = client.get(f"/courses/{slug}")

    assert focus.status_code == 200
    assert f'data-operation-id="{operation_id}"' in focus.text
    assert 'data-operation-state="retryable_error"' in focus.text
    assert f'value="{operation_id}"' in focus.text
    assert saved_text in focus.text


def test_invalid_course_template_is_a_validation_error(client: TestClient) -> None:
    token = csrf(client, "/courses/new")
    response = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Unknown template",
            "goal": "Verify input handling.",
            "experience": "",
            "template_id": "does-not-exist",
            "submission_id": str(uuid4()),
        },
    )

    assert response.status_code == 422
    assert response.json()["ok"] is False


def test_invalid_setup_payload_never_echoes_secret(client: TestClient) -> None:
    token = csrf(client, "/setup")
    secret = "never-echo-this-" + "x" * 4096
    response = client.post(
        "/api/setup",
        headers={"x-csrf-token": token},
        json={
            "provider": "openrouter",
            "api_key": secret,
            "model": "google/gemini-2.5-flash-lite",
        },
    )

    assert response.status_code == 400
    assert secret not in response.text


def test_environment_managed_provider_skips_setup_dead_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    monkeypatch.setenv("OPENLEARN_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENLEARN_MODEL", "local-model")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cli.clear_config_cache()
    managed_client = TestClient(create_app(testing=True))

    assert managed_client.get("/", follow_redirects=False).status_code == 200
    setup = managed_client.get("/setup")
    assert "Environment managed" in setup.text
    assert 'data-endpoint="/api/setup"' not in setup.text


def test_fresh_setup_defaults_to_consistent_openrouter_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENLEARN_API_KEY",
        "OPENLEARN_BASE_URL",
        "OPENLEARN_MODEL",
        "OPENLEARN_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    cli.clear_config_cache()

    status = OpenLearnWebServices().provider_status()
    setup = TestClient(create_app(testing=True)).get("/setup")

    assert status["selected_provider"] == "openrouter"
    assert status["form_base_url"] == "https://openrouter.ai/api/v1"
    assert status["form_model"] == "google/gemini-2.5-flash-lite"
    assert '<option value="openrouter"' in setup.text
    assert 'value="google/gemini-2.5-flash-lite"' in setup.text
    assert 'value="https://openrouter.ai/api/v1"' in setup.text


def test_setup_renders_every_provider_preset_with_sync_metadata(client: TestClient) -> None:
    setup = client.get("/setup")

    for preset in providers.PROVIDER_PRESETS.values():
        assert f'value="{preset.slug}"' in setup.text
        assert f'data-default-base-url="{preset.base_url or ""}"' in setup.text
        assert f'data-default-model="{preset.default_model or ""}"' in setup.text


def test_malformed_provider_config_renders_recovery_and_can_be_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    secret = "broken-secret-never-rendered"
    (tmp_path / "config.json").write_text(
        f'{{"openai_api_key": "{secret}"', encoding="utf-8"
    )
    recovery_client = TestClient(create_app(testing=True))

    setup = recovery_client.get("/setup")

    assert setup.status_code == 200
    assert "saved provider settings are unreadable" in setup.text
    assert secret not in setup.text
    token = setup.cookies["openlearn_csrf"]
    saved = recovery_client.post(
        "/api/setup",
        headers={"x-csrf-token": token},
        json={
            "provider": "ollama",
            "api_key": "",
            "model": "llama3.1",
            "base_url": "http://localhost:11434/v1",
        },
    )

    assert saved.status_code == 200
    assert saved.json()["ready"] is True
    assert config.read_config()["model"] == "llama3.1"


def test_unverified_setup_stays_on_setup_and_blocks_teaching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENLEARN_API_KEY",
        "OPENLEARN_BASE_URL",
        "OPENLEARN_MODEL",
        "OPENLEARN_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        providers,
        "validate_provider",
        lambda *_args, **_kwargs: providers.ValidationResult(
            providers.ValidationStatus.NETWORK_ERROR
        ),
    )
    cli.clear_config_cache()
    unverified_client = TestClient(create_app(testing=True))
    token = csrf(unverified_client, "/setup")

    saved = unverified_client.post(
        "/api/setup",
        headers={"x-csrf-token": token},
        json={
            "provider": "openrouter",
            "api_key": "saved-but-unverified-secret",
            "model": "google/gemini-2.5-flash-lite",
            "base_url": "https://openrouter.ai/api/v1",
            "save_unverified": True,
        },
    )

    assert saved.status_code == 200
    assert saved.json()["ok"] is True
    assert saved.json()["ready"] is False
    assert saved.json()["requires_validation"] is True
    assert "saved-but-unverified-secret" not in saved.text
    for path in ("/", "/dashboard", "/courses/new", "/courses/example"):
        response = unverified_client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].endswith("/setup")

    create = unverified_client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Must not be created",
            "goal": "Teaching is unavailable until validation succeeds.",
            "experience": "",
            "template_id": None,
            "submission_id": str(uuid4()),
        },
    )
    turn = unverified_client.post(
        "/api/courses/example/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "answer",
            "text": "Must not be submitted",
            "submission_id": str(uuid4()),
            "expected_revision": 0,
        },
    )

    for response in (create, turn):
        assert response.status_code == 428
        assert response.json()["state"] == "setup_required"
        assert response.json()["setup_url"].endswith("/setup")
    assert not (tmp_path / "learning-topics" / "must-not-be-created.md").exists()


def test_course_creation_is_idempotent_across_initialization_replay(
    client: TestClient,
) -> None:
    token = csrf(client, "/courses/new")
    submission_id = str(uuid4())
    payload = {
        "title": "Idempotent Initialization",
        "goal": "Start exactly one first lesson.",
        "experience": "Some prior practice.",
        "template_id": None,
        "submission_id": submission_id,
    }

    first = client.post("/api/courses", headers={"x-csrf-token": token}, json=payload)
    second = client.post("/api/courses", headers={"x-csrf-token": token}, json=payload)

    assert first.status_code == second.status_code == 202
    assert first.json()["slug"] == second.json()["slug"]
    assert first.json()["operation_id"] == second.json()["operation_id"]
    slug = first.json()["slug"]
    operation_id = first.json()["operation_id"]
    final = wait_for_operation(client, slug, operation_id)
    assert final["state"] == "committed", (final, cli.load_state(slug))
    topic = cli.read_topic(slug)
    _context, log = cli.split_session_log(topic.body)
    entries = cli.session_entries(log)
    assert len(entries) == 1
    assert entries[0]["prompt"] == "Start my first lesson."
    assert "Do not run a placement test" not in topic.body
    assert len(list(cli.topics_dir().glob("idempotent-initialization*.md"))) == 1


def test_initialization_failure_preserves_course_and_retries_same_operation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = csrf(client, "/courses/new")
    original_ask_topic = cli.ask_topic

    def fail_provider(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(cli, "ask_topic", fail_provider)
    create = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Recoverable Initialization",
            "goal": "Retry without recreating the course.",
            "experience": "",
            "template_id": None,
            "submission_id": str(uuid4()),
        },
    )

    assert create.status_code == 202
    slug = create.json()["slug"]
    operation_id = create.json()["operation_id"]
    failed = wait_for_operation(client, slug, operation_id, "retryable_error")
    assert failed["state"] == "retryable_error"
    initialization = client.get(create.json()["initialization_url"])
    assert initialization.status_code == 200
    assert "Retry first lesson" in initialization.text
    assert "provider unavailable" not in initialization.text
    assert "Begin the course now" not in initialization.text
    assert cli.topic_path(slug).exists()

    monkeypatch.setattr(cli, "ask_topic", original_ask_topic)
    retried = client.post(
        f"/api/courses/{slug}/initialization/{operation_id}/retry",
        headers={"x-csrf-token": token},
        json={},
    )

    assert retried.status_code == 202
    assert retried.json()["operation_id"] == operation_id
    assert wait_for_operation(client, slug, operation_id, "committed")["state"] == "committed"
    topic = cli.read_topic(slug)
    _context, log = cli.split_session_log(topic.body)
    assert len(cli.session_entries(log)) == 1
    assert "Begin the course now" not in client.get(f"/courses/{slug}").text


def test_focus_renders_safe_structured_lesson_blocks() -> None:
    class RichFocusServices(PlaceholderServices):
        def provider_status(self) -> dict[str, object]:
            return {"ready": True, "managed": False, "providers": []}

        def focus(self, slug: str) -> dict[str, object]:
            _kind, blocks = _present_response(
                "**Lesson:**\n\n- First idea\n- Second idea\n\n"
                "1. Plan\n2. Check\n\n```python\nprint('<script>')\n```"
            )
            return {
                "slug": slug,
                "title": "Readable lesson",
                "current_unit": "Foundations",
                "revision": 1,
                "saved_state": "Saved locally",
                "move": {
                    "kind": "Lesson",
                    "title": "Your next learning move",
                    "blocks": blocks,
                    "prompt": "Explain the plan.",
                    "position": "Step 1",
                },
                "progress": {"percent": 10, "summary": "One step complete."},
                "feedback": None,
                "operation": None,
                "saved_response": "",
            }

    response = TestClient(create_app(RichFocusServices(), testing=True)).get(
        "/courses/readable-lesson"
    )

    assert response.status_code == 200
    assert "<ul>" in response.text
    assert "<ol>" in response.text
    assert '<pre><code data-language="python">' in response.text
    assert "&lt;script&gt;" in response.text
    assert "print('<script>')" not in response.text


def test_web_turn_uses_current_provider_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    config.save_provider_configuration(
        base_url="http://localhost:11434/v1",
        model="new-current-model",
        api_key=None,
        verified=True,
    )
    captured: dict[str, object] = {}

    def start_turn(_slug: str, _text: str, **kwargs: object) -> tutor_service.TutorTurnResult:
        captured.update(kwargs)
        return tutor_service.TutorTurnResult(
            submission_id=str(kwargs["submission_id"]),
            status="saved",
            input_status="saved",
            message_kind="question",
            move=None,
        )

    monkeypatch.setattr(tutor_service, "start_turn", start_turn)
    submission_id = str(uuid4())
    OpenLearnWebServices().submit_turn(
        "existing-course",
        TutorSubmissionRequest(
            intent="question",
            text="Why?",
            submission_id=submission_id,
            expected_revision=3,
        ),
    )

    assert captured["model"] == "new-current-model"


def test_history_service_pages_all_session_entries(client: TestClient) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Long History",
            "goal": "Keep every prior move reachable.",
            "experience": "",
            "template_id": None,
            "submission_id": str(uuid4()),
        },
    ).json()
    wait_for_operation(client, created["slug"], created["operation_id"], "committed")
    for number in range(11):
        cli.append_session(
            cli.read_topic(created["slug"]),
            "lesson",
            f"Learner prompt {number}",
            f"- Move {number}a\n- Move {number}b",
        )

    first = client.get(
        f"/courses/{created['slug']}/history?page=1",
        headers={"accept": "application/json"},
    ).json()
    second = client.get(
        f"/courses/{created['slug']}/history?page=2",
        headers={"accept": "application/json"},
    ).json()

    assert len(first["items"]) == 10
    assert first["has_more"] is True
    assert len(second["items"]) == 2
    assert second["has_more"] is False
    assert second["items"][0]["blocks"][0]["kind"] == "unordered_list"


def test_initialization_refresh_recovers_orphaned_saved_operation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = csrf(client, "/courses/new")

    def persist_without_worker(
        slug: str,
        prompt: str,
        *,
        submission_id: str,
        **_kwargs: object,
    ) -> tutor_service.TutorTurnResult:
        state = cli.load_state(slug)
        internal = state.setdefault("_openlearn_internal", {})
        internal.setdefault("schema_version", 1)
        internal.setdefault("course_revision", 0)
        internal.setdefault("turn_results", {})
        internal["active_turn"] = {
            "submission_id": submission_id,
            "status": "saved",
            "expected_revision": 0,
            "prompt": prompt,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        state["pending_learner_prompt"] = prompt
        cli.save_state(slug, state)
        return tutor_service.TutorTurnResult(
            submission_id=submission_id,
            status="saved",
            input_status="saved",
            message_kind="question",
            move=None,
        )

    monkeypatch.setattr(tutor_service, "start_turn", persist_without_worker)
    create = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Restart Recovery",
            "goal": "Recover an interrupted first lesson.",
            "experience": "",
            "template_id": None,
            "submission_id": str(uuid4()),
        },
    )

    slug = create.json()["slug"]
    operation_id = create.json()["operation_id"]
    refreshed = client.get(create.json()["initialization_url"])

    assert refreshed.status_code == 200
    assert f'data-operation-id="{operation_id}"' in refreshed.text
    assert 'data-operation-state="retryable_error"' in refreshed.text
    assert "Retry first lesson" in refreshed.text
    assert "Begin the course now" not in refreshed.text
    focus = client.get(f"/courses/{slug}", follow_redirects=False)
    assert focus.status_code == 303
    assert operation_id in focus.headers["location"]
