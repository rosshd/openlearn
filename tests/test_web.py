from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from openlearn import cli, code_runner, data_management
from openlearn import config
from openlearn import interview_prep
from openlearn import providers
from openlearn import tutor_service
from openlearn.web import create_app
from openlearn.web.app import PlaceholderServices
from openlearn.web.schemas import PlacementRequest, TutorSubmissionRequest
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


def test_placement_request_preserves_the_durable_draft_limit() -> None:
    text = "x" * (interview_prep.DRAFT_MAX_LENGTH - 1)

    request = PlacementRequest(action="save_draft", stage="reasoning", text=text)

    assert request.text == text


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


def create_tool_course() -> str:
    cli.cmd_new(
        argparse.Namespace(topic="Tool Course", goal="Learn with optional tools"),
        output_func=lambda _text: None,
    )
    return "tool-course"


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
            "title": "Distributed Systems",
            "goal": "Explain replication tradeoffs clearly.",
            "experience": "I know basic Python.",
            "template_id": None,
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


def test_interview_course_placement_draft_resumes_and_finishes_before_lesson(
    client: TestClient,
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Technical Interview Prep",
            "goal": "Practice interview reasoning.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    )

    assert created.status_code == 200
    body = created.json()
    assert body["state"] == "placement_recommended"
    assert body["placement_url"].endswith(f"/courses/{body['slug']}/placement")
    assert tutor_service.course_revision(body["slug"]) == 0

    placement = client.get(body["placement_url"])
    assert "No editor or code execution" in placement.text
    token = placement.cookies.get("openlearn_csrf", token)
    started = client.post(
        f"/api/courses/{body['slug']}/placement",
        headers={"x-csrf-token": token},
        json={"action": "start"},
    )
    assert started.status_code == 200
    assert started.json()["next_stage"] == "clarification"
    started_page = client.get(body["placement_url"])
    assert "first_unique_window(text, width)" in started_page.text
    assert "Step 1 of 2" in started_page.text
    assert "Keep it to 1-2 sentences" in started_page.text
    assert 'rows="4"' in started_page.text
    assert 'rows="10"' not in started_page.text

    saved = client.post(
        f"/api/courses/{body['slug']}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "save_draft",
            "stage": "clarification",
            "text": "What are the input constraints?\nShould I discuss edge cases?",
            "expected_updated_at": started.json()["updated_at"],
        },
    )
    assert saved.status_code == 200
    restarted_client = TestClient(create_app(testing=True))
    resumed = restarted_client.get(body["placement_url"])
    restarted_token = resumed.cookies["openlearn_csrf"]
    assert "What are the input constraints?" in resumed.text
    assert "Should I discuss edge cases?" in resumed.text
    assert "Draft saved locally" in resumed.text
    assert 'href="http://testserver/dashboard">Pause and resume later</a>' in resumed.text
    assert restarted_client.get("/dashboard").status_code == 200
    resumed_again = restarted_client.get(body["placement_url"])
    assert "What are the input constraints?" in resumed_again.text

    submitted = restarted_client.post(
        f"/api/courses/{body['slug']}/placement",
        headers={"x-csrf-token": restarted_token},
        json={
            "action": "submit",
            "stage": "clarification",
            "submission_id": str(uuid4()),
        },
    )
    assert submitted.status_code == 200
    assert submitted.json()["next_stage"] == "reasoning"
    assert submitted.json()["draft"] is None
    assert submitted.json()["feedback"]["title"] == "Good clarification habit"
    reasoning_page = restarted_client.get(body["placement_url"])
    assert "Interviewer contract" in reasoning_page.text
    assert "Step 2 of 2" in reasoning_page.text
    assert "Good clarification habit" in reasoning_page.text
    reasoning_saved = restarted_client.post(
        f"/api/courses/{body['slug']}/placement",
        headers={"x-csrf-token": restarted_token},
        json={
            "action": "save_draft",
            "stage": "reasoning",
            "text": (
                "Use a sliding window with a set and test invalid widths and duplicates. "
                "The approach takes O(n) time."
            ),
            "expected_updated_at": submitted.json()["updated_at"],
        },
    )
    assert reasoning_saved.status_code == 200
    finished = restarted_client.post(
        f"/api/courses/{body['slug']}/placement",
        headers={"x-csrf-token": restarted_token},
        json={
            "action": "submit",
            "stage": "reasoning",
            "submission_id": str(uuid4()),
        },
    )
    assert finished.status_code == 200
    assert finished.json()["status"] == "provisional"
    assert "initialization_url" not in finished.json()
    completed_page = restarted_client.get(body["placement_url"])
    assert "Your reasoning snapshot" in completed_page.text
    assert "Named an approach and data structure" in completed_page.text
    assert "Improve next:" in completed_page.text
    assert "Continue to lesson" in completed_page.text

    continued = restarted_client.get(
        f"/courses/{body['slug']}", follow_redirects=False
    )
    assert continued.status_code == 303
    assert "/initializing/" in continued.headers["location"]
    operation_id = continued.headers["location"].rsplit("/", 1)[-1]
    wait_for_operation(
        restarted_client,
        body["slug"],
        operation_id,
        "committed",
    )

    profile = interview_prep.load_profile(cli.interview_profile_path(body["slug"]))
    assert profile["placement"]["evidence_refs"]
    assert profile["placement"]["draft"] is None


def test_dashboard_groups_discoverable_learning_practice_and_settings_paths(
    client: TestClient,
) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "/courses/new" in response.text
    assert "/quick-learn" in response.text
    assert "/review" in response.text
    assert "/progress" in response.text
    assert "/setup" in response.text
    assert "/data" in response.text


def test_data_page_is_read_only_and_data_mutations_require_csrf(client: TestClient) -> None:
    before = cli.project_home().exists()
    page = client.get("/data")

    assert page.status_code == 200
    assert "durable files" in page.text
    assert cli.project_home().exists() is before
    rejected = client.post("/api/data", json={"action": "reset"})
    assert rejected.status_code == 403


def test_data_controls_backup_refuse_reset_and_match_cli_summary(
    client: TestClient, tmp_path: Path
) -> None:
    cli.cmd_new(
        argparse.Namespace(topic="Data Course", goal="Keep a verified backup"),
        output_func=lambda _text: None,
    )
    output: list[str] = []
    cli.cmd_data(argparse.Namespace(data_action="inventory"), output_func=output.append)
    cli_summary = json.loads(output[0])
    web_summary = OpenLearnWebServices().data_summary()
    assert cli_summary["files"] == web_summary["files"]
    assert cli_summary["bytes"] == web_summary["bytes"]

    page = client.get("/data")
    assert "Create verified backup" in page.text
    assert "Restore or move a verified backup" in page.text
    assert "Reset or delete local data" in page.text
    token = page.cookies["openlearn_csrf"]
    archive = tmp_path.parent / f"{tmp_path.name}-web-backup.olbackup"
    backup = client.post(
        "/api/data",
        headers={"x-csrf-token": token},
        json={"action": "backup", "archive": str(archive)},
    )
    assert backup.status_code == 200
    assert archive.exists()

    destination = tmp_path.parent / f"{tmp_path.name}-moved-home"
    moved = client.post(
        "/api/data",
        headers={"x-csrf-token": token},
        json={
            "action": "move",
            "archive": str(archive),
            "destination": str(destination),
            "confirmation": data_management.MOVE_CONFIRMATION,
        },
    )
    assert moved.status_code == 200
    assert moved.json()["home"] == str(destination)
    assert moved.json()["source"] == str(tmp_path)
    assert moved.json()["cleanup_required"] is True
    assert moved.json()["source_retained"] is True
    assert cli.topic_path("data-course").exists()
    assert (destination / "learning-topics" / "data-course.md").exists()

    refused = client.post(
        "/api/data",
        headers={"x-csrf-token": token},
        json={"action": "reset", "archive": str(archive), "confirmation": "wrong"},
    )
    assert refused.status_code == 400
    assert cli.topic_path("data-course").exists()

    malformed = client.post(
        "/api/data", headers={"x-csrf-token": token}, json={"action": "move", "archive": ""}
    )
    assert malformed.status_code == 400
    missing = client.post(
        "/api/data",
        headers={"x-csrf-token": token},
        json={"action": "restore", "archive": str(tmp_path / "missing.olbackup"), "destination": str(tmp_path.parent / "restore")},
    )
    assert missing.status_code == 422
    assert str(tmp_path / "missing.olbackup") not in missing.text


def test_interview_placement_defer_and_restart_remain_resumable(client: TestClient) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Technical Interview Prep",
            "goal": "Practice interview reasoning.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]

    deferred = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "defer"},
    )
    assert deferred.status_code == 200
    assert deferred.json()["status"] == "deferred"
    assert "operation_id" not in deferred.json()
    continued = client.get(f"/courses/{slug}", follow_redirects=False)
    assert continued.status_code == 303
    assert "/initializing/" in continued.headers["location"]
    operation_id = continued.headers["location"].rsplit("/", 1)[-1]
    assert wait_for_operation(client, slug, operation_id)["state"] == "committed"

    started = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "start"},
    ).json()
    saved = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "save_draft",
            "stage": started["next_stage"],
            "text": "A draft to discard",
            "expected_updated_at": started["updated_at"],
        },
    ).json()
    restarted = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "restart"},
    )
    assert restarted.status_code == 200
    assert restarted.json()["attempt_id"] != saved["attempt_id"]
    assert restarted.json()["draft"] is None
    assert restarted.json()["next_stage"] == "clarification"


def test_non_interview_course_rejects_placement_mutations(client: TestClient) -> None:
    cli.cmd_new(
        argparse.Namespace(topic="Systems Design", goal="Practice architecture tradeoffs"),
        output_func=lambda _text: None,
    )
    token = csrf(client, "/dashboard")

    response = client.post(
        "/api/courses/systems-design/placement",
        headers={"x-csrf-token": token},
        json={"action": "start"},
    )

    assert response.status_code == 404
    assert not cli.interview_profile_path("systems-design").exists()


def test_completed_placement_setup_return_starts_pending_first_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    cli.clear_config_cache()

    class ToggleProviderServices(OpenLearnWebServices):
        ready = False

        def provider_status(self) -> dict[str, object]:
            return {"ready": self.ready, "managed": False, "providers": []}

    services = ToggleProviderServices()
    offline = TestClient(create_app(services=services, testing=True))
    token = csrf(offline, "/courses/new")
    created = offline.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Technical Interview Prep",
            "goal": "Practice interview reasoning.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    deferred = offline.post(
        f"/api/courses/{created['slug']}/placement",
        headers={"x-csrf-token": token},
        json={"action": "defer"},
    )
    assert deferred.status_code == 200
    setup_required = offline.get(
        f"/courses/{created['slug']}", follow_redirects=False
    )
    assert setup_required.status_code == 303
    assert setup_required.headers["location"].endswith(
        f"/setup?next=%2Fcourses%2F{created['slug']}"
    )
    skipped = offline.post(
        f"/api/courses/{created['slug']}/placement",
        headers={"x-csrf-token": token},
        json={"action": "skip"},
    )
    assert skipped.status_code == 200
    assert skipped.json()["setup_url"].endswith(
        f"/setup?next=%2Fcourses%2F{created['slug']}"
    )
    assert tutor_service.course_revision(created["slug"]) == 0

    services.ready = True
    returned = offline.get(f"/courses/{created['slug']}", follow_redirects=False)
    assert returned.status_code == 303
    assert "/initializing/" in returned.headers["location"]
    operation_id = returned.headers["location"].rsplit("/", 1)[-1]
    assert wait_for_operation(offline, created["slug"], operation_id)["state"] == "committed"


def test_review_grading_and_detailed_progress_are_actionable(client: TestClient) -> None:
    cli.cmd_new(
        argparse.Namespace(topic="Review Course", goal="Practice durable recall"),
        output_func=lambda _text: None,
    )
    topic = cli.read_topic("review-course")
    metadata = dict(topic.metadata)
    metadata["current_focus"] = "Replication tradeoffs"
    metadata["weak_spots"] = ["Leader election"]
    metadata["review_due"] = [
        {"concept": "Leader election", "due": "2020-01-01", "difficulty": "hard"}
    ]
    cli.write_topic(topic.path, metadata, topic.body)

    progress = client.get("/progress")
    review = client.get("/review")
    assert "Replication tradeoffs" in progress.text
    assert "uncertain" in progress.text
    assert "Leader election" in review.text

    token = review.cookies.get("openlearn_csrf", csrf(client, "/review"))
    graded = client.post(
        "/api/review",
        headers={"x-csrf-token": token},
        json={
            "slug": "review-course",
            "concept": "Leader election",
            "due": "2020-01-01",
            "result": "easy",
        },
    )
    assert graded.status_code == 200
    assert graded.json() == {"ok": True}
    stale = client.post(
        "/api/review",
        headers={"x-csrf-token": token},
        json={
            "slug": "review-course",
            "concept": "Leader election",
            "due": "2020-01-01",
            "result": "easy",
        },
    )
    assert stale.status_code == 409


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


def test_saved_provider_is_validated_before_interview_placement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    cli.clear_config_cache()
    config.save_provider_configuration(
        base_url="https://openrouter.ai/api/v1",
        model="google/gemini-2.5-flash-lite",
        api_key="already-saved-key",
        verified=False,
    )
    monkeypatch.setattr(
        providers,
        "validate_provider",
        lambda *_args, **_kwargs: providers.ValidationResult(
            providers.ValidationStatus.VALID
        ),
    )
    monkeypatch.setattr(
        providers,
        "validate_provider_model",
        lambda *_args, **_kwargs: providers.ValidationResult(
            providers.ValidationStatus.VALID
        ),
    )
    saved_key_client = TestClient(create_app(testing=True))
    token = csrf(saved_key_client, "/courses/new")

    created = saved_key_client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Technical Interview Prep",
            "goal": "Practice interview reasoning.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    )

    assert created.status_code == 200
    assert "setup_url" not in created.json()
    assert config.provider_status().verified is True
    assert saved_key_client.get(created.json()["placement_url"]).status_code == 200


def test_interview_setup_happens_before_placement_when_saved_provider_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    cli.clear_config_cache()
    config.save_provider_configuration(
        base_url="https://openrouter.ai/api/v1",
        model="google/gemini-2.5-flash-lite",
        api_key="invalid-saved-key",
        verified=False,
    )
    monkeypatch.setattr(
        providers,
        "validate_provider",
        lambda *_args, **_kwargs: providers.ValidationResult(
            providers.ValidationStatus.REJECTED
        ),
    )
    invalid_key_client = TestClient(create_app(testing=True))
    token = csrf(invalid_key_client, "/courses/new")

    created = invalid_key_client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Technical Interview Prep",
            "goal": "Practice interview reasoning.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    )

    body = created.json()
    expected_suffix = f"/setup?next=%2Fcourses%2F{body['slug']}%2Fplacement"
    assert body["setup_url"].endswith(expected_suffix)
    blocked = invalid_key_client.get(body["placement_url"], follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"].endswith(expected_suffix)
    setup = invalid_key_client.get(blocked.headers["location"])
    assert "API key (already saved)" in setup.text
    assert "Leave this blank to test the saved key" in setup.text


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


def test_environment_managed_provider_requires_explicit_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    monkeypatch.setenv("OPENLEARN_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENLEARN_MODEL", "local-model")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cli.clear_config_cache()
    managed_client = TestClient(create_app(testing=True))

    response = managed_client.get("/", follow_redirects=False)
    assert response.status_code == 200

    monkeypatch.setenv("OPENLEARN_PROVIDER_VERIFIED", "1")
    verified_client = TestClient(create_app(testing=True))
    assert verified_client.get("/", follow_redirects=False).status_code == 200
    setup = managed_client.get("/setup")
    assert "Environment managed" in setup.text
    assert 'data-endpoint="/api/setup"' not in setup.text


def test_unverified_provider_allows_provider_free_course_browsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    for name in (
        "OPENAI_API_KEY",
        "OPENLEARN_BASE_URL",
        "OPENLEARN_MODEL",
        "OPENLEARN_PROVIDER_VERIFIED",
    ):
        monkeypatch.delenv(name, raising=False)
    browsing_client = TestClient(create_app(testing=True))

    assert browsing_client.get("/", follow_redirects=False).status_code == 200
    assert browsing_client.get("/dashboard", follow_redirects=False).status_code == 200
    starters = browsing_client.get("/courses/new", follow_redirects=False)
    assert starters.status_code == 200
    assert "Technical Interview Prep" in starters.text


def test_provider_setup_preserves_safe_model_backed_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    config.save_provider_configuration(
        base_url="https://openrouter.ai/api/v1",
        model="google/gemini-2.5-flash-lite",
        api_key="unverified-key",
        verified=False,
        home=tmp_path,
        environ={},
    )
    setup_client = TestClient(create_app(testing=True))

    redirect = setup_client.get("/courses/example", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"].endswith(
        "/setup?next=%2Fcourses%2Fexample"
    )

    setup = setup_client.get("/setup?next=/courses/example")
    assert 'data-success-url="/courses/example"' in setup.text

    unsafe = setup_client.get("/setup?next=https://attacker.example")
    assert 'data-success-url="/dashboard"' in unsafe.text


def test_managed_unverified_setup_never_claims_provider_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    monkeypatch.setenv("OPENLEARN_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENLEARN_MODEL", "local-model")
    monkeypatch.delenv("OPENLEARN_PROVIDER_VERIFIED", raising=False)

    setup = TestClient(create_app(testing=True)).get("/setup")

    assert "Your provider is ready" not in setup.text
    assert "not yet verified" in setup.text


@pytest.mark.parametrize(
    ("validation_status", "retain_secret"),
    [
        (providers.ValidationStatus.NETWORK_ERROR, True),
        (providers.ValidationStatus.REJECTED, False),
        (providers.ValidationStatus.HTTP_ERROR, False),
    ],
)
def test_setup_retains_secret_only_for_retryable_network_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validation_status: providers.ValidationStatus,
    retain_secret: bool,
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    monkeypatch.setattr(
        providers,
        "validate_provider",
        lambda *_args, **_kwargs: providers.ValidationResult(validation_status),
    )
    setup_client = TestClient(create_app(testing=True))
    token = csrf(setup_client, "/setup")

    response = setup_client.post(
        "/api/setup",
        headers={"x-csrf-token": token},
        json={
            "provider": "openrouter",
            "api_key": "current-page-only-secret",
            "model": "google/gemini-2.5-flash-lite",
            "base_url": "https://openrouter.ai/api/v1",
        },
    )

    assert response.status_code == 422
    assert response.json()["retain_secret"] is retain_secret
    assert "current-page-only-secret" not in response.text


def test_setup_rejects_provider_when_selected_model_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    monkeypatch.setattr(
        providers,
        "validate_provider",
        lambda *_args, **_kwargs: providers.ValidationResult(
            providers.ValidationStatus.VALID
        ),
    )
    monkeypatch.setattr(
        providers,
        "validate_provider_model",
        lambda *_args, **_kwargs: providers.ValidationResult(
            providers.ValidationStatus.HTTP_ERROR, "model_unavailable"
        ),
    )
    setup_client = TestClient(create_app(testing=True))
    token = csrf(setup_client, "/setup")

    response = setup_client.post(
        "/api/setup",
        headers={"x-csrf-token": token},
        json={
            "provider": "openrouter",
            "api_key": "valid-key",
            "model": "missing-model",
            "base_url": "https://openrouter.ai/api/v1",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"] == "That model is not available from this provider."
    assert not (tmp_path / "config.json").exists()


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
    for path in ("/", "/dashboard", "/courses/new"):
        response = unverified_client.get(path, follow_redirects=False)
        assert response.status_code == 200
    response = unverified_client.get("/courses/example", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith(
        "/setup?next=%2Fcourses%2Fexample"
    )

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
    assert first.json()["created"] is True
    assert second.json()["created"] is False
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


def test_course_creation_supports_legacy_adapter_without_entry_mode() -> None:
    class LegacyCourseServices:
        def provider_status(self) -> dict[str, object]:
            return {"ready": True}

        def create_course(self, _request: object) -> dict[str, object]:
            return {
                "ok": True,
                "slug": "legacy-course",
                "operation_id": str(uuid4()),
                "state": "saved",
                "created": True,
            }

    legacy = TestClient(create_app(LegacyCourseServices(), testing=True))
    token = csrf(legacy, "/setup")
    response = legacy.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Legacy Course",
            "goal": "Preserve injected adapter compatibility.",
            "experience": "",
            "template_id": None,
            "submission_id": str(uuid4()),
        },
    )

    assert response.status_code == 202
    assert response.json()["slug"] == "legacy-course"


def test_video_preparation_ignores_out_of_order_responses() -> None:
    javascript = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "openlearn"
        / "web"
        / "static"
        / "openlearn.js"
    ).read_text(encoding="utf-8")
    handler_start = javascript.index(
        'toolSurface?.querySelector("[data-video-form]")?.addEventListener'
    )
    handler_end = javascript.index(
        'toolSurface?.querySelector("[data-video-load]")', handler_start
    )
    handler = javascript[handler_start:handler_end]

    assert "invalidatePreparedVideo();" in handler
    assert "const requestGeneration = videoRequestGeneration;" in handler
    assert handler.index("await requestJson") < handler.index(
        "if (requestGeneration !== videoRequestGeneration) return;"
    ) < handler.index("preparedVideo = descriptor;")
    assert (
        'querySelector("#video-url")?.addEventListener("input", invalidatePreparedVideo)'
        in handler
    )


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


def test_present_response_hides_reasoning_from_existing_lesson_history() -> None:
    kind, blocks = _present_response(
        "The user wants their first lesson.\n"
        "I need to inspect the course metadata.\n</think>\n\n"
        "Lesson: Clarify constraints before coding."
    )

    assert kind == "Lesson"
    assert blocks == [{"kind": "paragraph", "text": "Clarify constraints before coding."}]


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


def test_focus_exposes_optional_dual_surface_without_opening_a_tool(
    client: TestClient,
) -> None:
    slug = create_tool_course()

    response = client.get(f"/courses/{slug}")

    assert response.status_code == 200
    assert 'data-tool-open="code"' in response.text
    assert 'data-tool-open="video"' in response.text
    assert 'data-tool-open="sources"' in response.text
    assert 'data-tool-surface hidden' in response.text


def test_video_tool_validates_locally_and_returns_consent_descriptor(
    client: TestClient,
) -> None:
    slug = create_tool_course()
    token = csrf(client, f"/courses/{slug}")

    valid = client.post(
        f"/api/courses/{slug}/tools/video",
        headers={"x-csrf-token": token},
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )
    invalid = client.post(
        f"/api/courses/{slug}/tools/video",
        headers={"x-csrf-token": token},
        json={"url": "https://example.com/private-video"},
    )

    assert valid.status_code == 200
    assert valid.json()["requires_consent"] is True
    assert valid.json()["embed_url"] == (
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
    )
    assert invalid.status_code == 422
    assert "private-video" not in invalid.text


def test_local_tool_preparation_stays_available_without_a_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENLEARN_API_KEY",
        "OPENLEARN_BASE_URL",
        "OPENLEARN_MODEL",
        "OPENLEARN_PROVIDER",
        "OPENLEARN_PROVIDER_VERIFIED",
        "OPENLEARN_MOCK",
    ):
        monkeypatch.delenv(name, raising=False)
    cli.clear_config_cache()
    slug = create_tool_course()
    offline = TestClient(create_app(testing=True))
    token = csrf(offline, "/courses/new")

    video = offline.post(
        f"/api/courses/{slug}/tools/video",
        headers={"x-csrf-token": token},
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )
    code = offline.get(f"/api/courses/{slug}/tools/code")
    saved = offline.post(
        f"/api/courses/{slug}/tools/code",
        headers={"x-csrf-token": token},
        json={
            "action": "save",
            "source": "print('saved offline')\n",
            "expected_revision": code.json()["revision"],
        },
    )
    imported = offline.post(
        f"/api/courses/{slug}/tools/sources/file",
        headers={"x-csrf-token": token},
        files={"file": ("offline.md", b"Offline source", "text/markdown")},
    )
    turn = offline.post(
        f"/api/courses/{slug}/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "question",
            "text": "This model-backed action stays gated.",
            "submission_id": str(uuid4()),
            "expected_revision": 0,
        },
    )

    assert video.status_code == code.status_code == saved.status_code == imported.status_code == 200
    assert video.json()["requires_consent"] is True
    assert saved.json()["message"] == "Draft saved locally."
    assert [item["label"] for item in imported.json()["imported"]] == ["offline.md"]
    assert turn.status_code == 428
    assert turn.json()["setup_url"].endswith("/setup")


def test_code_tool_saves_recovers_and_rejects_stale_drafts(client: TestClient) -> None:
    slug = create_tool_course()
    token = csrf(client, f"/courses/{slug}")
    initial = client.get(f"/api/courses/{slug}/tools/code").json()

    saved = client.post(
        f"/api/courses/{slug}/tools/code",
        headers={"x-csrf-token": token},
        json={
            "action": "save",
            "source": "print('saved locally')\n",
            "expected_revision": initial["revision"],
        },
    )
    stale = client.post(
        f"/api/courses/{slug}/tools/code",
        headers={"x-csrf-token": token},
        json={
            "action": "save",
            "source": "print('stale')\n",
            "expected_revision": initial["revision"],
        },
    )
    recovered = client.get(f"/api/courses/{slug}/tools/code")

    assert saved.status_code == 200
    assert recovered.json()["source"] == "print('saved locally')\n"
    assert stale.status_code == 409
    assert cli.load_state(slug).get("known", []) == []


def test_code_tool_run_fails_closed_without_secure_runtime(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = create_tool_course()
    token = csrf(client, f"/courses/{slug}")
    initial = client.get(f"/api/courses/{slug}/tools/code").json()
    monkeypatch.setattr(
        code_runner,
        "diagnose_runtime",
        lambda **_kwargs: code_runner.RuntimeDiagnostic(
            None,
            None,
            False,
            False,
            code_runner.DEFAULT_RUNNER_IMAGE,
            "runtime unavailable",
        ),
    )

    response = client.post(
        f"/api/courses/{slug}/tools/code",
        headers={"x-csrf-token": token},
        json={
            "action": "run",
            "source": "print('explicit run')\n",
            "expected_revision": initial["revision"],
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["status"] == "runner_unavailable"
    assert response.json()["result"]["isolation"] is None
    recovered = client.get(f"/api/courses/{slug}/tools/code")
    assert recovered.status_code == 200
    assert recovered.json()["result"]["status"] == "runner_unavailable"
    assert cli.load_state(slug).get("known", []) == []


def test_code_tool_rejects_multibyte_draft_as_invalid_not_missing(
    client: TestClient,
) -> None:
    slug = create_tool_course()
    token = csrf(client, f"/courses/{slug}")
    initial = client.get(f"/api/courses/{slug}/tools/code").json()

    response = client.post(
        f"/api/courses/{slug}/tools/code",
        headers={"x-csrf-token": token},
        json={
            "action": "save",
            "source": "😀" * 20_000,
            "expected_revision": initial["revision"],
        },
    )

    assert response.status_code == 422
    assert "too large" in response.json()["error"]
    assert client.get(f"/api/courses/{slug}/tools/code").status_code == 200


def test_source_tool_imports_uploaded_file_and_folder_with_dedupe(
    client: TestClient, tmp_path: Path
) -> None:
    slug = create_tool_course()
    token = csrf(client, f"/courses/{slug}")
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "README.md").write_text("Folder source", encoding="utf-8")

    uploaded = client.post(
        f"/api/courses/{slug}/tools/sources/file",
        headers={"x-csrf-token": token},
        files={"file": ("lesson.md", b"# Uploaded lesson\n", "text/markdown")},
    )
    duplicate = client.post(
        f"/api/courses/{slug}/tools/sources/file",
        headers={"x-csrf-token": token},
        files={"file": ("lesson.md", b"# Uploaded lesson\n", "text/markdown")},
    )
    folder_result = client.post(
        f"/api/courses/{slug}/tools/sources/folder",
        headers={"x-csrf-token": token},
        json={"path": str(folder)},
    )
    listed = client.get(f"/api/courses/{slug}/tools/sources")

    assert uploaded.status_code == duplicate.status_code == folder_result.status_code == 200
    assert [item["label"] for item in uploaded.json()["imported"]] == ["lesson.md"]
    assert [item["label"] for item in duplicate.json()["skipped"]] == ["lesson.md"]
    assert {item["label"] for item in listed.json()["sources"]} == {
        "lesson.md",
        "README.md",
    }


def test_source_tool_rejects_likely_secrets_without_echoing_or_persisting(
    client: TestClient,
) -> None:
    slug = create_tool_course()
    token = csrf(client, f"/courses/{slug}")
    secret = "sk-proj-" + "a" * 32

    response = client.post(
        f"/api/courses/{slug}/tools/sources/file",
        headers={"x-csrf-token": token},
        files={
            "file": (
                "provider-notes.md",
                f"OPENAI_API_KEY={secret}\n".encode(),
                "text/markdown",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["failed"] == [
        {
            "status": "failed",
            "label": "provider-notes.md",
            "context_file": None,
            "message": "Source may contain a credential or private key.",
        }
    ]
    assert secret not in response.text
    assert list(cli.context_source_files(slug)) == []


def test_source_tool_public_github_route_is_shallow_and_inert(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = create_tool_course()
    token = csrf(client, f"/courses/{slug}")
    commands: list[list[str]] = []

    def fake_clone(command: list[str], clone_dir: Path, _env: dict[str, str]) -> None:
        commands.append(command)
        clone_dir.mkdir(parents=True)
        (clone_dir / "README.md").write_text("Public source", encoding="utf-8")
        (clone_dir / "never-run.py").write_text(
            "raise RuntimeError('must remain inert')",
            encoding="utf-8",
        )

    monkeypatch.setattr(cli, "_bounded_git_clone", fake_clone)
    response = client.post(
        f"/api/courses/{slug}/tools/sources/github",
        headers={"x-csrf-token": token},
        json={"url": "https://github.com/example/learning"},
    )

    assert response.status_code == 200
    assert {item["label"] for item in response.json()["imported"]} == {
        "README.md",
        "never-run.py",
    }
    assert commands[0][:6] == [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "clone",
        "--depth",
        "1",
    ]
    assert "--filter=blob:limit=200000" in commands[0]
    assert cli.load_state(slug).get("known", []) == []
