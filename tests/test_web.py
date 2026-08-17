from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from types import SimpleNamespace
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from openlearn import application, cli, code_runner, courses, data_management
from openlearn import config
from openlearn import interview_prep
from openlearn import providers
from openlearn import tutor_service
from openlearn.application import CourseProgress
from openlearn.web import create_app
from openlearn.web.app import PlaceholderServices
from openlearn.web.schemas import PlacementRequest, TutorSubmissionRequest
from openlearn.web.services import (
    COURSE_INITIALIZATION_PROMPT,
    OpenLearnWebServices,
    _course_initialization_prompt,
    _focus_progress,
    _plain_text,
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


def confidence_ratings(focus: str = "coding", **overrides: int) -> dict[str, int]:
    ratings = {
        topic_id: 3
        for topic_id, _label in interview_prep.confidence_topics_for_focus(focus)
    }
    ratings.update(overrides)
    return ratings


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
    empty_dashboard = client.get("/").text
    assert "Choose your first course" in empty_dashboard
    assert "Technical Interview Prep" in empty_dashboard
    assert "New course" in empty_dashboard

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
    assert "Current lesson" in focus.text
    assert "Press Enter to continue" not in focus.text
    assert 'id="learner-response"' not in focus.text
    assert 'data-tool-open="chat"' in focus.text
    assert "placement test" not in focus.text.lower()
    assert "**Lesson:**" not in focus.text
    assert "Your courses" in client.get("/dashboard").text
    dashboard_html = client.get("/dashboard").text
    dashboard_intro, courses_panel = dashboard_html.split("data-course-workspace", 1)
    assert "New course" not in dashboard_intro
    assert "New course" in courses_panel
    assert "new-course-menu" in courses_panel
    assert 'class="library-toolbar"' not in courses_panel
    assert courses_panel.index('class="course-list"') < courses_panel.index(
        'class="new-course-menu"'
    )
    course_heading = courses_panel.split("</div>", 2)[0]
    assert "<span>1</span>" not in course_heading
    assert 'class="course-tool"' not in courses_panel

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
    resumed_focus = client.get(f"/courses/{slug}")
    assert 'id="learner-response"' not in resumed_focus.text
    chat = client.get(f"/api/courses/{slug}/chat").json()
    assert chat["conversation"][0]["question"] == (
        "Can you explain the tradeoff with an example?"
    )
    assert chat["conversation"][0]["blocks"]
    assert chat["conversation"][0]["source_lesson_id"].startswith("lesson_")
    assert chat["conversation"][0]["source_lesson_title"]
    assert chat["revision"] == chat["course_revision"] == revision
    assert chat["chat_revision"] == 1
    history = client.get(f"/courses/{slug}/history", headers={"accept": "application/json"})
    assert history.status_code == 200
    assert len(history.json()["items"]) == 1
    assert COURSE_INITIALIZATION_PROMPT not in history.text
    assert any(item["title"] == "First lesson" for item in history.json()["items"])


def test_interview_course_confidence_placement_resumes_and_builds_first_lesson(
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
    assert "Start quick placement" in placement.text
    assert "Skip placement" in placement.text
    assert "Defer and decide later" not in placement.text
    assert "first_unique_window" not in placement.text
    token = placement.cookies.get("openlearn_csrf", token)
    started = client.post(
        f"/api/courses/{body['slug']}/placement",
        headers={"x-csrf-token": token},
        json={"action": "start"},
    )
    assert started.status_code == 200
    assert started.json()["next_stage"] == "confidence"
    assert started.json()["lifecycle_version"] == interview_prep.PLACEMENT_V4
    started_page = client.get(body["placement_url"])
    assert "Start rapid questions" in started_page.text
    assert "How confident are you with sliding window?" in started_page.text
    assert "How confident are you with capacity estimation?" in started_page.text
    assert "Sliding window" in started_page.text
    assert "Coding + system design" in started_page.text
    assert "Review or change your answers" in started_page.text

    saved = client.post(
        f"/api/courses/{body['slug']}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "save_confidence",
            "role_family": "backend",
            "target_level": "senior",
            "interview_focus": "balanced",
            "ratings": confidence_ratings(
                "balanced", sliding_window=1, arrays_hashing=4, trees=5
            ),
        },
    )
    assert saved.status_code == 200
    assert saved.json()["next_stage"] == "outline"
    restarted_client = TestClient(create_app(testing=True))
    resumed = restarted_client.get(body["placement_url"])
    restarted_token = resumed.cookies["openlearn_csrf"]
    assert "Your suggested course outline" in resumed.text
    assert "Requirements and Interfaces" in resumed.text
    assert "Reliability" in resumed.text
    assert "Sequence Patterns" in resumed.text
    assert "Linear Foundations" in resumed.text
    assert "locked" in resumed.text
    assert "Interview habit" in resumed.text
    assert "Interview Communication and Problem Framing" not in resumed.text
    assert "Timed and Behavioral Interview Practice" not in resumed.text
    assert "Workshop this outline" not in resumed.text
    assert "Confirm course outline" in resumed.text
    assert "Change course outline" in resumed.text
    assert "Tutor feedback" not in resumed.text
    assert 'aria-label="Back to dashboard"' in resumed.text
    assert restarted_client.get("/dashboard").status_code == 200
    outline = saved.json()["outline"]
    finished = restarted_client.post(
        f"/api/courses/{body['slug']}/placement",
        headers={"x-csrf-token": restarted_token},
        json={
            "action": "confirm_outline",
            "outline": outline,
        },
    )
    assert finished.status_code == 202
    assert finished.json()["status"] == "provisional"
    assert "/initializing/" in finished.json()["initialization_url"]
    wait_for_operation(restarted_client, body["slug"], finished.json()["operation_id"])
    lesson = restarted_client.get(f"/courses/{body['slug']}")
    assert lesson.status_code == 200
    assert "Press Enter to continue" not in lesson.text
    canonical = cli.load_state(body["slug"])["interview_curriculum"]
    assert canonical["cursor"]["skill_ref"]["skill_id"] == "concept.arrays-strings"
    assert "concept.arrays-strings" in canonical["evidence"]["exposed"]
    assert "Your next learning move" not in lesson.text

    profile = interview_prep.load_profile(cli.interview_profile_path(body["slug"]))
    assert profile["placement"]["survey"]["ratings"]["sliding_window"] == 1
    assert profile["placement"]["result"]["mastery_update_applied"] is False
    assert profile["placement"]["result"]["patterns_marked_known"] == []
    assert cli.read_topic(body["slug"]).metadata["course_started"] is True
    history = restarted_client.get(
        f"/courses/{body['slug']}/history", headers={"accept": "application/json"}
    )
    assert history.json()["items"][0]["title"] == "First lesson"


def test_confirmed_confidence_outline_retries_course_plan_save(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Retryable Interview Prep",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "start"},
    )
    saved = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "save_confidence",
            "role_family": "general SWE",
            "target_level": "entry",
            "interview_focus": "coding",
            "ratings": confidence_ratings(),
        },
    ).json()
    from openlearn import courses

    real_checkpoint = courses._route_acceptance_checkpoint

    def fail_after_profile(stage: str) -> None:
        if stage == "after_profile":
            raise cli.OpenLearnError("simulated route acceptance interruption")

    monkeypatch.setattr(courses, "_route_acceptance_checkpoint", fail_after_profile)
    submission_id = str(uuid4())
    first = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "confirm_outline",
            "outline": saved["outline"],
            "submission_id": submission_id,
        },
    )
    assert first.status_code == 422
    assert interview_prep.load_profile(cli.interview_profile_path(slug))["placement"][
        "status"
    ] == "provisional"

    monkeypatch.setattr(courses, "_route_acceptance_checkpoint", real_checkpoint)
    retried = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "confirm_outline",
            "outline": saved["outline"],
            "submission_id": submission_id,
        },
    )

    assert retried.status_code == 202
    assert cli.read_topic(slug).metadata["course_started"] is True


def test_skipped_confidence_placement_persists_canonical_route_before_initialization(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Skipped Interview Placement",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    observed: dict[str, object] = {}

    def observe_initialization(slug: str) -> dict[str, object]:
        observed["canonical"] = cli.load_state(slug).get("interview_curriculum")
        observed["allocation"] = interview_prep.load_profile(
            cli.interview_profile_path(slug)
        ).get("curriculum_allocation")
        return {
            "ok": True,
            "slug": slug,
            "operation_id": str(uuid4()),
            "state": "saved",
        }

    monkeypatch.setattr(
        OpenLearnWebServices,
        "start_course_initialization",
        staticmethod(observe_initialization),
    )
    response = client.post(
        f"/api/courses/{created['slug']}/placement",
        headers={"x-csrf-token": token},
        json={"action": "skip", "submission_id": str(uuid4())},
    )

    assert response.status_code == 202
    canonical = observed["canonical"]
    allocation = observed["allocation"]
    assert isinstance(canonical, dict)
    assert isinstance(allocation, dict)
    assert canonical["route_fingerprint"] == allocation["route"]["route_fingerprint"]
    assert canonical["cursor"]["skill_ref"]["skill_id"] == "concept.arrays-strings"
    saved_profile = interview_prep.load_profile(cli.interview_profile_path(created["slug"]))
    survey = saved_profile["placement"]["survey"]
    assert isinstance(survey, dict)
    assert set(survey["ratings"]) == {
        topic_id
        for topic_id, _label in interview_prep.confidence_topics_for_focus("coding")
    }
    assert set(survey["ratings"].values()) == {1}


def test_outline_editor_exposes_every_bounded_change_and_previews_empty_optionals(
    client: TestClient,
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Bounded Maker Bench",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    started = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "start"},
    )
    assert started.status_code == 200
    saved = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "save_confidence",
            "role_family": "backend",
            "target_level": "entry",
            "interview_focus": "coding",
            "ratings": confidence_ratings(),
        },
    )
    assert saved.status_code == 200

    page = client.get(f"/courses/{slug}/placement")
    for field in (
        'name="interview_date"',
        'name="weekly_minutes"',
        'name="session_minutes"',
        'name="pacing_posture_override"',
        'name="rating_arrays_hashing"',
        'name="optional_skill_ids"',
    ):
        assert field in page.text
    assert ">Preview changes<" in page.text
    assert "Confirm changes and continue" not in page.text

    default_preview = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "preview_outline"},
    ).json()
    empty_preview = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "preview_outline", "optional_skill_ids": []},
    ).json()
    assert default_preview["optional_choices"]
    assert any(item["selected"] for item in default_preview["optional_choices"])
    assert empty_preview["selected_optional_skill_ids"] == []
    assert all(not item["selected"] for item in empty_preview["optional_choices"])
    assert all(
        item["requirement"] == "required" for item in empty_preview["route"]["skills"]
    )
    assert empty_preview["route_fingerprint"] != default_preview["route_fingerprint"]


def test_outline_editor_can_clear_standard_pacing_to_date_recommended(
    client: TestClient,
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Pacing Override Interview Prep",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "start"},
    )
    saved = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "save_confidence",
            "role_family": "backend",
            "target_level": "entry",
            "interview_focus": "coding",
            "ratings": confidence_ratings(),
        },
    ).json()
    interview_date = (date.today() + timedelta(days=5)).isoformat()
    confirmed = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "confirm_outline",
            "outline": saved["outline"],
            "interview_date": interview_date,
            "pacing_posture_override": "standard",
            "submission_id": str(uuid4()),
        },
    )
    assert confirmed.status_code == 202
    profile = interview_prep.load_profile(cli.interview_profile_path(slug))
    assert profile["curriculum_allocation"]["pacing_posture_override"] == "standard"

    recommended = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "preview_outline", "pacing_posture_override": None},
    )

    assert recommended.status_code == 200
    preview = recommended.json()
    assert preview["route"]["recommended_pacing_posture"] == "accelerated"
    assert preview["route"]["pacing_posture"] == "accelerated"


@pytest.mark.parametrize("action", ["change_outline", "confirm_outline", "skip"])
def test_route_acceptance_conflict_is_projected_as_http_conflict(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": f"Conflicting {action}",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]

    def conflict(*_args: object, **_kwargs: object) -> object:
        raise courses.RouteAcceptanceConflictError("course changed during acceptance")

    monkeypatch.setattr(application, "accept_interview_curriculum", conflict)
    response = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": action,
            "outline": "Saved outline",
            "submission_id": str(uuid4()),
            "expected_revision": tutor_service.course_revision(slug),
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "state": "conflict",
        "error": "course changed during acceptance",
    }


def test_stale_route_revision_is_a_real_http_conflict(client: TestClient) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Stale Route HTTP Conflict",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()

    response = client.post(
        f"/api/courses/{created['slug']}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "skip",
            "submission_id": str(uuid4()),
            "expected_revision": 99,
        },
    )

    assert response.status_code == 409
    assert response.json()["state"] == "conflict"


def test_dashboard_reuses_lightweight_interview_card_without_parsing_sessions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Dashboard Interview Card",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    application.accept_interview_curriculum(
        slug, action="skip", submission_id=str(uuid4())
    )
    monkeypatch.setattr(
        application,
        "interview_learning_card",
        lambda _slug: pytest.fail("dashboard must reuse the snapshot card projection"),
    )
    monkeypatch.setattr(
        application,
        "interview_learning",
        lambda _slug: pytest.fail("dashboard must not build a full lesson projection"),
    )
    monkeypatch.setattr(
        cli,
        "session_entries",
        lambda _log: pytest.fail("dashboard cards must not parse session history"),
    )
    original_read_text = Path.read_text

    def reject_transcript_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == cli.topic_path(slug):
            pytest.fail("dashboard cards must not read the Markdown transcript")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_transcript_read)
    monkeypatch.setattr(
        cli,
        "parse_topic",
        lambda _text: pytest.fail("dashboard cards must not parse the Markdown transcript"),
    )

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Dashboard Interview Card" in response.text


def test_chat_returns_both_revisions_from_one_recovery_fenced_snapshot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = application.create_course(
        application.CourseCreationRequest(name="Chat Snapshot", goal="Learn safely")
    )
    slug = created.course.slug

    def set_revisions(state: dict[str, object]) -> None:
        internal = state.get("_openlearn_internal")
        internal = dict(internal) if isinstance(internal, dict) else {}
        internal["course_revision"] = 2
        internal["side_chat_revision"] = 4
        state["_openlearn_internal"] = internal

    cli.update_state_atomic(slug, set_revisions)
    monkeypatch.setattr(
        cli,
        "read_topic",
        lambda _slug: pytest.fail("chat must use the recovery-fenced snapshot"),
    )
    monkeypatch.setattr(
        tutor_service,
        "course_revision",
        lambda _slug: pytest.fail("chat must not read course revision separately"),
    )

    response = client.get(f"/api/courses/{slug}/chat")

    assert response.status_code == 200
    assert response.json() == {
        "conversation": [],
        "revision": 2,
        "course_revision": 2,
        "chat_revision": 4,
    }


def test_later_outline_change_preserves_evidence_and_rehomes_ineligible_cursor(
    client: TestClient,
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Route Change Interview Prep",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    skipped = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "skip", "submission_id": str(uuid4())},
    )
    wait_for_operation(client, slug, skipped.json()["operation_id"])
    state = cli.load_state(slug)
    canonical = state["interview_curriculum"]
    dp = next(
        item
        for item in canonical["route"]["skills"]
        if item["skill_ref"]["skill_id"] == "pattern.dynamic-programming"
    )
    canonical["cursor"] = {
        "unit_id": dp["unit_id"],
        "section_id": dp["section_id"],
        "skill_ref": dp["skill_ref"],
        "instruction_status": "covered",
    }
    canonical["evidence"]["answer_evidence"] = [
        {"evidence_id": "kept", "skill_ref": dp["skill_ref"], "status": "correct"}
    ]
    state["interview_curriculum"] = canonical
    cli.write_text_atomic(
        cli.topic_state_path(slug), json.dumps(state, indent=2, sort_keys=True) + "\n"
    )
    revision = tutor_service.course_revision(slug)
    changed = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "change_outline",
            "interview_focus": "system_design",
            "submission_id": str(uuid4()),
            "expected_revision": revision,
        },
    )

    assert changed.status_code == 200
    updated = cli.load_state(slug)["interview_curriculum"]
    assert updated["route_id"] == "system-design"
    assert updated["cursor"]["skill_ref"]["skill_id"] == "system.requirements-scope"
    assert updated["evidence"]["answer_evidence"][0]["evidence_id"] == "kept"
    assert "pattern.dynamic-programming" in updated["route_history"][-1][
        "out_of_route_skill_ids"
    ]
    assert changed.json()["receipt"]["cursor_decision"] == (
        "earliest-eligible-unmet-prerequisite"
    )


@pytest.mark.parametrize(
    "checkpoint", ["after_profile", "after_state", "after_topic", "after_event"]
)
def test_route_acceptance_recovers_every_publication_checkpoint_on_read(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, checkpoint: str
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": f"Checkpoint {checkpoint}",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    submission_id = str(uuid4())

    def interrupt(stage: str) -> None:
        if stage == checkpoint:
            raise cli.OpenLearnError(f"interrupt {checkpoint}")

    monkeypatch.setattr(courses, "_route_acceptance_checkpoint", interrupt)
    failed = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "skip", "submission_id": submission_id},
    )
    assert failed.status_code == 422
    monkeypatch.setattr(courses, "_route_acceptance_checkpoint", lambda _stage: None)

    restarted = TestClient(create_app(testing=True))
    assert restarted.get(f"/courses/{slug}/placement").status_code == 200
    state = cli.load_state(slug)
    receipt_id = f"route_{submission_id.replace('-', '')}"
    assert list(state["_interview_route_receipts"]) == [receipt_id]
    events = cli.load_event_log(cli.topic_events_path(slug))
    assert sum(event.get("event_id") == f"{receipt_id}:0" for event in events) == 1
    assert not cli.interview_route_journal_path(slug).exists()


def test_metadata_repair_recovers_pending_route_acceptance_instead_of_deleting_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Repair Pending Route",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    submission_id = str(uuid4())

    def interrupt(stage: str) -> None:
        if stage == "after_state":
            raise cli.OpenLearnError("interrupt after_state")

    monkeypatch.setattr(courses, "_route_acceptance_checkpoint", interrupt)
    failed = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "skip", "submission_id": submission_id},
    )
    assert failed.status_code == 422
    assert cli.interview_route_journal_path(slug).exists()
    monkeypatch.setattr(courses, "_route_acceptance_checkpoint", lambda _stage: None)

    cli.repair_topic_metadata(slug)

    receipt_id = f"route_{submission_id.replace('-', '')}"
    state = cli.load_state(slug)
    assert list(state["_interview_route_receipts"]) == [receipt_id]
    assert cli.read_topic(slug).metadata["course_started"] is True
    assert not cli.interview_route_journal_path(slug).exists()


def test_route_acceptance_rejects_submission_payload_collision(client: TestClient) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Route Collision",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    submission_id = str(uuid4())
    first = application.accept_interview_curriculum(
        created["slug"], action="skip", submission_id=submission_id
    )

    with pytest.raises(courses.RouteAcceptanceConflictError, match="already used"):
        application.accept_interview_curriculum(
            created["slug"],
            action="change",
            changes={"interview_focus": "system_design"},
            submission_id=submission_id,
            expected_revision=int(first["receipt"]["final_revision"]),
        )


def test_lost_skip_response_retry_keeps_one_revision_receipt_and_attempt(
    client: TestClient,
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Lost Skip Response",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    submission_id = str(uuid4())
    first = application.accept_interview_curriculum(
        created["slug"], action="skip", submission_id=submission_id
    )
    attempt_id = first["profile"]["placement"]["attempt_id"]
    retried = application.accept_interview_curriculum(
        created["slug"], action="skip", submission_id=submission_id
    )

    assert retried["replayed"] is True
    assert retried["receipt"] == first["receipt"]
    assert retried["profile"]["placement"]["attempt_id"] == attempt_id
    assert tutor_service.course_revision(created["slug"]) == 1
    assert len(cli.load_state(created["slug"])["_interview_route_receipts"]) == 1


def test_route_acceptance_race_checks_revision_before_profile_write(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Route Race",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    profile_path = cli.interview_profile_path(slug)
    topic_path = cli.topic_path(slug)
    before_profile = profile_path.read_bytes()
    before_topic = topic_path.read_bytes()

    def publish_competing_progress(stage: str) -> None:
        if stage != "after_journal":
            return
        state = cli._load_state_unlocked(slug)
        internal = state.setdefault("_openlearn_internal", {})
        assert isinstance(internal, dict)
        internal["course_revision"] = 1
        internal["schema_version"] = 1
        internal.setdefault("turn_results", {})
        cli.write_text_atomic(
            cli.topic_state_path(slug), json.dumps(state, indent=2, sort_keys=True) + "\n"
        )

    monkeypatch.setattr(courses, "_route_acceptance_checkpoint", publish_competing_progress)
    with pytest.raises(courses.RouteAcceptanceConflictError, match="course changed"):
        application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4()), expected_revision=0
        )

    assert profile_path.read_bytes() == before_profile
    assert topic_path.read_bytes() == before_topic
    state = cli._load_state_unlocked(slug)
    assert state["_openlearn_internal"]["course_revision"] == 1
    assert "interview_curriculum" not in state
    assert not cli.interview_route_journal_path(slug).exists()


def test_route_acceptance_rejects_tampered_journal_before_recovery(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Tampered Route Journal",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]

    def interrupt(stage: str) -> None:
        if stage == "after_journal":
            raise cli.OpenLearnError("leave journal for validation")

    monkeypatch.setattr(courses, "_route_acceptance_checkpoint", interrupt)
    with pytest.raises(cli.OpenLearnError, match="leave journal"):
        application.accept_interview_curriculum(
            slug, action="skip", submission_id=str(uuid4()), expected_revision=0
        )
    journal_path = cli.interview_route_journal_path(slug)
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["receipt"]["payload_hash"] = "0" * 64
    cli.write_text_atomic(journal_path, json.dumps(journal, indent=2, sort_keys=True) + "\n")

    with pytest.raises(cli.OpenLearnError, match="invalid identity"):
        courses.recover_interview_route_acceptance(slug)

    assert journal_path.exists()


def test_dashboard_groups_discoverable_learning_practice_and_settings_paths(
    client: TestClient,
) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "/courses/new" in response.text
    assert "/quick-learn" in response.text
    assert "/review" not in response.text
    assert "/progress" in response.text
    assert "/setup" in response.text
    assert "/data" in response.text


def test_dashboard_previews_course_without_activation_and_continue_activates(
    client: TestClient,
) -> None:
    first = application.create_course(
        application.CourseCreationRequest(name="Active Course", goal="Keep learning")
    ).course
    second = application.create_course(
        application.CourseCreationRequest(name="Preview Course", goal="Inspect first")
    ).course
    application.activate_course(first.slug)

    preview = client.get(f"/dashboard?course={second.slug}")

    assert preview.status_code == 200
    assert f'data-selected-course="{second.slug}"' in preview.text
    assert f'data-active-course="{first.slug}"' in preview.text
    assert cli.get_active_topic() == first.slug
    token = preview.cookies["openlearn_csrf"]

    continued = client.post(
        f"/courses/{second.slug}/activate",
        headers={"x-csrf-token": token},
        follow_redirects=False,
    )

    assert continued.status_code == 303
    assert cli.get_active_topic() == second.slug


def test_dashboard_hides_empty_review_and_shows_course_path_and_management(
    client: TestClient,
) -> None:
    course = application.create_course(
        application.CourseCreationRequest(name="Path Course", goal="See what comes next")
    ).course

    response = client.get(f"/dashboard?course={course.slug}")

    assert response.status_code == 200
    assert "0 due" not in response.text
    assert 'data-course-workspace' in response.text
    assert 'data-course-coverage' in response.text
    assert 'class="course-controls-panel"' in response.text
    assert "View full course path" in response.text
    assert f'/courses/{course.slug}/settings' in response.text
    assert f'/courses/{course.slug}/delete' in response.text
    assert "Change course outline" in response.text
    assert "View progress" in response.text
    assert "Quick Learn" in response.text
    assert "Settings and local data" not in response.text


def test_dashboard_offers_resume_for_a_persisted_pending_follow_up(
    client: TestClient,
) -> None:
    course = application.create_course(
        application.CourseCreationRequest(
            name="Completed Course", goal="Build a focused next step"
        )
    ).course
    topic = cli.read_topic(course.slug)
    metadata = dict(topic.metadata)
    metadata["course_completed"] = True
    metadata["course_units"] = [
        {"unit": 1, "title": "Unit 1: Foundations", "slide_count": 1}
    ]
    cli.write_topic(topic.path, metadata, topic.body)
    submission_id = str(uuid4())
    generation = cli.current_topic_generation(course.slug)
    assert generation is not None
    tutor_service._reserve_follow_up_record(
        {
            "schema_version": tutor_service._FOLLOW_UP_SCHEMA_VERSION,
            "source_slug": course.slug,
            "source_generation": generation,
            "source_title": course.card.title,
            "source_goal": course.card.goal,
            "submission_id": submission_id,
            "payload_hash": tutor_service._follow_up_payload_hash(
                course.slug, generation, "Go deeper", ()
            ),
            "state": "pending",
            "interests": "Go deeper",
            "weak_areas": [],
            "title": "",
            "goal": "",
            "error_code": None,
            "error_message": None,
            "created_slug": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    response = client.get(
        f"/dashboard?course={course.slug}&proposal={submission_id}"
    )

    assert response.status_code == 200
    assert 'name="action" value="retry"' in response.text
    assert "Resume proposal" in response.text


def test_unknown_follow_up_status_uses_stable_not_found_envelope(
    client: TestClient,
) -> None:
    course = application.create_course(
        application.CourseCreationRequest(name="Proposal Status", goal="Track proposals")
    ).course
    token = csrf(client, "/dashboard")

    response = client.post(
        f"/api/courses/{course.slug}/follow-up",
        headers={"x-csrf-token": token},
        json={"action": "status", "submission_id": str(uuid4())},
    )

    assert response.status_code == 404
    assert response.json() == {
        "ok": False,
        "missing": True,
        "state": "missing",
        "error": "Follow-up proposal not found.",
    }


def test_empty_dashboard_embeds_varied_starters_and_creation_choices(
    client: TestClient,
) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 200
    assert response.text.index("Technical Interview Prep") < response.text.index(
        "Computer Networking"
    )
    assert "Starter course" in response.text
    assert "Custom course" in response.text
    assert "Quick Learn" in response.text
    assert "Choose a starting point" not in response.text
    assert 'data-empty-course-library' in response.text
    assert "Your next course starts here." not in response.text
    assert 'aria-label="Openlearn navigation"' in response.text
    assert 'data-theme-toggle' in response.text
    assert 'class="local-status"' not in response.text
    assert 'class="utilities-menu"' not in response.text


def test_course_settings_preview_confirm_and_permanent_deletion(
    client: TestClient,
) -> None:
    created = application.create_course(
        application.CourseCreationRequest(name="Managed Course", goal="Original goal")
    ).course
    settings = client.get(f"/courses/{created.slug}/settings")
    token = settings.cookies["openlearn_csrf"]

    preview = client.post(
        f"/courses/{created.slug}/settings/preview",
        headers={"x-csrf-token": token},
        data={
            "title": "Managed Course Renamed",
            "goal": "Updated goal",
            "difficulty": "deep",
            "weekly_minutes": "180",
            "session_minutes": "45",
            "outline": "",
        },
    )

    assert preview.status_code == 200
    assert "Confirm changes" in preview.text
    submission_id = str(uuid4())
    confirmation_data = {
        "title": "Managed Course Renamed",
        "goal": "Updated goal",
        "difficulty": "deep",
        "weekly_minutes": "180",
        "session_minutes": "45",
        "outline": "",
        "expected_payload_hash": preview.text.split(
            'name="expected_payload_hash" value="', 1
        )[1].split('"', 1)[0],
        "submission_id": submission_id,
    }
    confirmed = client.post(
        f"/courses/{created.slug}/settings/confirm",
        headers={"x-csrf-token": token},
        data=confirmation_data,
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    assert application.course(created.slug).card.title == "Managed Course Renamed"
    revision = cli.load_state(created.slug)["_openlearn_internal"]["course_revision"]
    replayed = client.post(
        f"/courses/{created.slug}/settings/confirm",
        headers={"x-csrf-token": token},
        data=confirmation_data,
        follow_redirects=False,
    )
    assert replayed.status_code == 303
    assert cli.load_state(created.slug)["_openlearn_internal"]["course_revision"] == revision

    deletion = client.get(f"/courses/{created.slug}/delete")
    assert deletion.status_code == 200
    assert "Back up all local data" in deletion.text
    assert "course content" in deletion.text
    assert "Type the exact course name" not in deletion.text
    assert "Type the exact course ID" not in deletion.text
    assert 'name="confirmation" value="delete"' in deletion.text
    deletion_data = {
        "confirmation": "delete",
        "confirmation_slug": created.slug,
        "confirmation_title": "Managed Course Renamed",
        "topic_generation": deletion.text.split(
            'name="topic_generation" value="', 1
        )[1].split('"', 1)[0],
    }
    unchecked = client.post(
        f"/courses/{created.slug}/delete",
        headers={
            "cookie": f"openlearn_csrf={token}",
            "origin": "http://testserver",
        },
        data={key: value for key, value in deletion_data.items() if key != "confirmation"},
        follow_redirects=False,
    )
    assert unchecked.status_code == 422
    assert cli.topic_path(created.slug).exists()
    deleted = client.post(
        f"/courses/{created.slug}/delete",
        headers={
            "cookie": f"openlearn_csrf={token}",
            "origin": "http://testserver",
        },
        data=deletion_data,
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert not cli.topic_path(created.slug).exists()
    replayed_deletion = client.post(
        f"/courses/{created.slug}/delete",
        headers={"x-csrf-token": token},
        data=deletion_data,
        follow_redirects=False,
    )
    assert replayed_deletion.status_code == 303


def test_interview_settings_can_clear_date_without_clearing_focus(
    client: TestClient,
) -> None:
    created = application.create_course(
        application.CourseCreationRequest(
            name="Interview Date Settings",
            template_id="technical-interview-prep",
        )
    ).course
    application.accept_interview_curriculum(
        created.slug, action="skip", submission_id=str(uuid4())
    )
    seeded = application.preview_course_settings(
        created.slug,
        application.CourseSettingsChange(
            interview_fields={
                "interview_date": "2026-09-15",
                "interview_focus": "balanced",
            }
        ),
    )
    application.confirm_course_settings(seeded, submission_id=str(uuid4()))
    profile = interview_prep.load_profile(cli.interview_profile_path(created.slug))
    values = profile["profile"]
    assert isinstance(values, dict)

    settings = client.get(f"/courses/{created.slug}/settings")
    token = settings.cookies["openlearn_csrf"]
    form = {
        "title": created.card.title,
        "goal": created.card.goal,
        "difficulty": created.mastery_profile,
        "weekly_minutes": str(values["weekly_minutes"]),
        "session_minutes": str(values["session_minutes"]),
        "outline": "",
        "role_family": "",
        "target_level": "",
        "interview_date": "",
        "interview_focus": "",
    }
    preview = client.post(
        f"/courses/{created.slug}/settings/preview",
        headers={"x-csrf-token": token},
        data=form,
    )
    assert preview.status_code == 200
    payload_hash = preview.text.split(
        'name="expected_payload_hash" value="', 1
    )[1].split('"', 1)[0]
    confirmed = client.post(
        f"/courses/{created.slug}/settings/confirm",
        headers={"x-csrf-token": token},
        data={
            **form,
            "expected_payload_hash": payload_hash,
            "submission_id": str(uuid4()),
        },
        follow_redirects=False,
    )

    assert confirmed.status_code == 303
    saved = interview_prep.load_profile(cli.interview_profile_path(created.slug))
    assert saved["profile"]["interview_date"] == ""
    assert saved["placement"]["survey"]["interview_focus"] == "balanced"


def test_course_creation_has_a_no_javascript_form_fallback(client: TestClient) -> None:
    page = client.get("/courses/new?template=technical-interview-prep")
    token = page.cookies["openlearn_csrf"]

    created = client.post(
        "/courses/new",
        headers={"x-csrf-token": token},
        data={
            "title": "No JavaScript Interview Prep",
            "goal": "Prepare for a coding interview.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    assert "/placement" in created.headers["location"] or "/setup" in created.headers["location"]


def test_starter_courses_prioritize_variety_and_use_bounded_horizontal_browsing(
    client: TestClient,
) -> None:
    page = client.get("/courses/new")

    assert page.text.index("Technical Interview Prep") < page.text.index("Computer Networking")
    assert page.text.index("Computer Networking") < page.text.index(">Vim<")
    assert 'data-starter-track tabindex="0"' in page.text
    assert 'aria-label="More starter courses"' in page.text


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


def test_interview_placement_restart_discards_confidence_answers(client: TestClient) -> None:
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

    client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "start"},
    ).json()
    saved = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={
            "action": "save_confidence",
            "role_family": "frontend",
            "target_level": "entry",
            "interview_focus": "coding",
            "ratings": confidence_ratings(graphs=1),
        },
    ).json()
    restarted = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "restart"},
    )
    assert restarted.status_code == 200
    assert restarted.json()["attempt_id"] != saved["attempt_id"]
    assert restarted.json()["survey"] is None
    assert restarted.json()["next_stage"] == "confidence"

    rejected_defer = client.post(
        f"/api/courses/{slug}/placement",
        headers={"x-csrf-token": token},
        json={"action": "defer"},
    )
    assert rejected_defer.status_code == 400


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


def test_interview_setup_precedes_placement_and_returns_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    cli.clear_config_cache()

    class ToggleProviderServices(OpenLearnWebServices):
        ready = False

        def provider_status(self) -> dict[str, object]:
            return {"ready": self.ready, "managed": False, "providers": []}

        def ensure_provider_ready(self) -> dict[str, object]:
            return self.provider_status()

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
    expected_placement_path = f"/courses/{created['slug']}/placement"
    assert created["setup_url"].endswith(
        f"/setup?next=%2Fcourses%2F{created['slug']}%2Fplacement"
    )
    placement = offline.get(expected_placement_path, follow_redirects=False)
    assert placement.status_code == 303
    assert placement.headers["location"].endswith(
        f"/setup?next=%2Fcourses%2F{created['slug']}%2Fplacement"
    )
    blocked = offline.post(
        f"/api/courses/{created['slug']}/placement",
        headers={"x-csrf-token": token},
        json={"action": "start"},
    )
    assert blocked.status_code == 428
    assert blocked.json()["setup_url"].endswith(
        f"/setup?next=%2Fcourses%2F{created['slug']}%2Fplacement"
    )
    assert tutor_service.course_revision(created["slug"]) == 0

    services.ready = True
    returned = offline.get(expected_placement_path)
    assert returned.status_code == 200
    assert "Start quick placement" in returned.text
    skipped = offline.post(
        f"/api/courses/{created['slug']}/placement",
        headers={"x-csrf-token": token},
        json={"action": "skip"},
    )
    assert skipped.status_code == 202
    assert "/initializing/" in skipped.json()["initialization_url"]
    operation_id = str(skipped.json()["operation_id"])
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


def test_setup_masks_api_keys_without_a_password_input(client: TestClient) -> None:
    setup = client.get("/setup").text

    api_key_markup = setup.split('id="api-key"', 1)[1].split(">", 1)[0]
    assert 'type="text"' in api_key_markup
    assert 'autocomplete="off"' in api_key_markup
    assert "data-secret-toggle" in setup
    assert 'type="password"' not in api_key_markup


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


def test_invalid_saved_provider_routes_to_setup_before_placement(
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
    placement = invalid_key_client.get(body["placement_url"], follow_redirects=False)
    assert placement.status_code == 303
    assert placement.headers["location"].endswith(expected_suffix)
    started = invalid_key_client.post(
        f"/api/courses/{body['slug']}/placement",
        headers={"x-csrf-token": token},
        json={"action": "start"},
    )
    assert started.status_code == 428
    assert started.json()["setup_url"].endswith(expected_suffix)
    setup = invalid_key_client.get(body["setup_url"])
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
    question = "What makes this tradeoff useful?"
    topic = cli.read_topic(slug)
    cli.append_session(topic, "lesson", "Recovery check", f"**Check:**\n{question}")
    cli.save_pending_question(
        cli.read_topic(slug),
        f"**Check:**\n{question}",
        "",
        question_text=question,
    )
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


def test_non_interview_side_chat_does_not_require_curriculum_source_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "General Course Chat",
            "goal": "Learn a general topic.",
            "experience": "",
            "template_id": None,
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    wait_for_operation(client, slug, created["operation_id"], "committed")
    captured: dict[str, object] = {}

    def start_turn(*args: object, **kwargs: object) -> tutor_service.TutorTurnResult:
        captured.update(kwargs)
        return tutor_service.TutorTurnResult(
            submission_id=str(kwargs["submission_id"]),
            status="saved",
            input_status="saved",
            message_kind="question",
            move=None,
        )

    monkeypatch.setattr(tutor_service, "start_turn", start_turn)
    response = client.post(
        f"/api/courses/{slug}/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "question",
            "text": "Can you clarify this lesson?",
            "submission_id": str(uuid4()),
            "expected_revision": tutor_service.course_revision(slug),
        },
    )

    assert response.status_code == 202
    assert captured["source_lesson_id"] is None
    assert captured["source_lesson_title"] is None
    assert captured["source_lesson_revision"] is None


def test_interview_side_chat_accepts_legacy_absent_source_tuple(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Legacy Interview Chat",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    application.accept_interview_curriculum(
        slug, action="skip", submission_id=str(uuid4())
    )
    captured: dict[str, object] = {}

    def start_turn(*_args: object, **kwargs: object) -> tutor_service.TutorTurnResult:
        captured.update(kwargs)
        return tutor_service.TutorTurnResult(
            submission_id=str(kwargs["submission_id"]),
            status="saved",
            input_status="saved",
            message_kind="question",
            move=None,
        )

    monkeypatch.setattr(tutor_service, "start_turn", start_turn)
    response = client.post(
        f"/api/courses/{slug}/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "question",
            "text": "Explain this lesson.",
            "submission_id": str(uuid4()),
            "expected_revision": tutor_service.course_revision(slug),
        },
    )

    assert response.status_code == 202
    assert captured["source_lesson_id"] is None
    assert captured["source_lesson_title"] is None
    assert captured["source_lesson_revision"] is None


@pytest.mark.parametrize(
    "source_fields",
    [
        {"source_lesson_id": "lesson_one"},
        {"source_lesson_title": "Arrays"},
        {"source_lesson_revision": 0},
        {"source_lesson_id": "lesson_one", "source_lesson_title": "Arrays"},
    ],
)
def test_interview_side_chat_rejects_partial_source_tuple(
    client: TestClient, source_fields: dict[str, object]
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Partial Interview Chat",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    application.accept_interview_curriculum(
        slug, action="skip", submission_id=str(uuid4())
    )
    response = client.post(
        f"/api/courses/{slug}/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "question",
            "text": "Explain this lesson.",
            "submission_id": str(uuid4()),
            "expected_revision": tutor_service.course_revision(slug),
            **source_fields,
        },
    )

    assert response.status_code == 409
    assert "incomplete" in response.json()["error"].lower()


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


def test_managed_provider_validation_is_cached_for_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENLEARN_MOCK", raising=False)
    monkeypatch.setenv("OPENLEARN_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENLEARN_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "managed-test-key")
    monkeypatch.delenv("OPENLEARN_PROVIDER_VERIFIED", raising=False)
    cli.clear_config_cache()
    calls = {"provider": 0, "model": 0}

    def validate_provider(*_args: object) -> providers.ValidationResult:
        calls["provider"] += 1
        return providers.ValidationResult(providers.ValidationStatus.VALID)

    def validate_model(*_args: object) -> providers.ValidationResult:
        calls["model"] += 1
        return providers.ValidationResult(providers.ValidationStatus.VALID)

    monkeypatch.setattr(providers, "validate_provider", validate_provider)
    monkeypatch.setattr(providers, "validate_provider_model", validate_model)
    services = OpenLearnWebServices()

    assert services.ensure_provider_ready()["ready"] is True
    assert services.ensure_provider_ready()["ready"] is True
    assert calls == {"provider": 1, "model": 1}


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
    assert status["form_model"] == "google/gemini-3.1-flash-lite"
    assert '<option value="openrouter"' in setup.text
    assert 'value="google/gemini-3.1-flash-lite"' in setup.text
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


def test_skip_placement_supports_legacy_one_argument_adapter() -> None:
    class LegacyPlacementServices:
        def provider_status(self) -> dict[str, object]:
            return {"ready": True}

        def ensure_provider_ready(self) -> dict[str, object]:
            return {"ready": True}

        def placement(self, slug: str) -> dict[str, object]:
            return {"slug": slug, "missing": False}

        def interview_placement_exists(self, _slug: str) -> bool:
            return True

        def skip_placement(self, slug: str) -> dict[str, object]:
            return {"slug": slug, "status": "provisional"}

        def start_course_initialization(self, _slug: str) -> dict[str, object]:
            return {"operation_id": "legacy-init"}

    legacy = TestClient(create_app(LegacyPlacementServices(), testing=True))
    token = csrf(legacy, "/setup")
    response = legacy.post(
        "/api/courses/legacy-course/placement",
        headers={"x-csrf-token": token},
        json={"action": "skip", "submission_id": str(uuid4())},
    )

    assert response.status_code == 202
    assert response.json()["slug"] == "legacy-course"
    assert response.json()["operation_id"] == "legacy-init"


def test_skip_placement_does_not_hide_adapter_internal_type_error() -> None:
    class BrokenPlacementServices:
        def provider_status(self) -> dict[str, object]:
            return {"ready": True}

        def ensure_provider_ready(self) -> dict[str, object]:
            return {"ready": True}

        def skip_placement(self, _slug: str, _request: object) -> dict[str, object]:
            raise TypeError("service implementation bug")

    broken = TestClient(create_app(BrokenPlacementServices(), testing=True))
    token = csrf(broken, "/setup")
    with pytest.raises(TypeError, match="service implementation bug"):
        broken.post(
            "/api/courses/legacy-course/placement",
            headers={"x-csrf-token": token},
            json={"action": "skip", "submission_id": str(uuid4())},
        )


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


def test_outline_change_is_previewed_before_confirm_and_retries_one_submission() -> None:
    javascript = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "openlearn"
        / "web"
        / "static"
        / "openlearn.js"
    ).read_text(encoding="utf-8")

    preview_start = javascript.index("async function previewPlacementOutline")
    confirm_start = javascript.index(
        'querySelector("[data-accept-outline-preview]")', preview_start
    )
    preview_handler = javascript[preview_start:confirm_start]
    assert 'action: "preview_outline"' in preview_handler
    assert 'action: "change_outline"' not in preview_handler
    assert "pendingOutlineChange = values" in preview_handler
    assert 'stablePlacementSubmission("change")' in javascript[confirm_start:]
    assert "window.sessionStorage.getItem(key)" in javascript
    assert "window.sessionStorage.setItem(key" in javascript
    assert "clearStablePlacementSubmission(action)" in javascript
    assert 'values.get("interview_date")' in javascript
    assert 'values.get("weekly_minutes")' in javascript
    assert 'values.get("session_minutes")' in javascript
    assert 'name.startsWith("rating_")' in javascript
    assert 'values.getAll("optional_skill_ids")' in javascript
    assert "updateOutlineConfidenceFields" in javascript
    assert "renderOutlineItems(result.outline_items)" in preview_handler
    assert 'querySelector("[data-outline-preview-heading], button")?.focus()' in (
        preview_handler
    )


def test_side_chat_polling_has_no_lesson_preview_sink() -> None:
    javascript = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "openlearn"
        / "web"
        / "static"
        / "openlearn.js"
    ).read_text(encoding="utf-8")
    poll_start = javascript.index("async function pollOperation")
    poll_end = javascript.index("function clearTurnComposer", poll_start)
    chat_start = javascript.index('chatForm?.addEventListener("submit"')
    chat_end = javascript.index(
        'chatForm?.querySelector("textarea")?.addEventListener', chat_start
    )

    assert "renderTutorPreview(preview)" in javascript[poll_start:poll_end]
    assert "await waitForOperation(result.operation_id" in javascript[chat_start:chat_end]
    assert "renderTutorPreview" not in javascript[chat_start:chat_end]
    assert '"[data-progression-action], [data-navigation-intent]"' in javascript


def test_completed_tutor_stream_keeps_card_visible_and_resizes_preview_only() -> None:
    repository = Path(__file__).resolve().parents[1]
    javascript = (repository / "src/openlearn/web/static/openlearn.js").read_text(
        encoding="utf-8"
    )
    css = (repository / "src/openlearn/web/static/openlearn.css").read_text(
        encoding="utf-8"
    )

    assert "data-stream-complete" not in javascript
    assert "data-stream-complete" not in css
    assert "move-arrive" not in css
    assert "measureTutorPreviewHeight" in javascript
    assert "clone = region.cloneNode(true)" in javascript
    assert "tutorPreviewHeightCache.size > 8" in javascript
    assert "data-stream-open" in css


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


def test_interview_initialization_retry_adopts_exact_canonical_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = str(uuid4())
    projection = SimpleNamespace(
        operation=SimpleNamespace(
            submission_id=operation_id,
            state="provider-error",
        ),
        revision=4,
    )
    resumed = tutor_service.TutorTurnResult(
        submission_id=operation_id,
        status="generating",
        input_status="generating",
        message_kind="lesson",
        move=None,
    )
    monkeypatch.setattr(
        "openlearn.web.services._initialization_id_for_slug",
        lambda _slug: operation_id,
    )
    monkeypatch.setattr(application, "interview_learning", lambda _slug: projection)
    monkeypatch.setattr(
        application,
        "resume_interview_progression",
        lambda slug, *, model=None: resumed,
    )
    monkeypatch.setattr(
        tutor_service,
        "start_turn",
        lambda *_args, **_kwargs: pytest.fail(
            "interview retry must not reserve a replacement target"
        ),
    )

    result = OpenLearnWebServices().retry_course_initialization(
        "technical-interview-prep", operation_id
    )

    assert result["state"] == "generating"
    assert result["operation_id"] == operation_id


def test_cancelling_saved_progression_does_not_require_provider_setup() -> None:
    client = TestClient(create_app(services=PlaceholderServices(), testing=True))
    token = csrf(client, "/dashboard")

    response = client.post(
        "/api/courses/technical-interview-prep/progression",
        headers={"x-csrf-token": token},
        json={"action": "cancel", "operation_id": str(uuid4())},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "missing"


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
                "progress": {
                    "percent": 10,
                    "summary": "One step complete.",
                    "has_concepts": True,
                },
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
    assert "10% complete" in response.text
    assert 'value="10"' in response.text
    assert "{'percent':" not in response.text


def test_focus_progress_uses_an_empty_state_until_concepts_are_tracked() -> None:
    progress = _focus_progress(CourseProgress(known=0, total=0, percent=0))

    assert progress == {
        "percent": 0,
        "summary": "Progress will appear after your first learning check.",
        "has_concepts": False,
    }


def test_interview_focus_uses_curriculum_labels_not_turn_steps(
    client: TestClient,
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Curriculum Position UI",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    application.accept_interview_curriculum(
        slug, action="skip", submission_id=str(uuid4())
    )
    assert _course_initialization_prompt(slug) == COURSE_INITIALIZATION_PROMPT
    initialized = OpenLearnWebServices().start_course_initialization(slug)
    wait_for_operation(client, slug, initialized["operation_id"])
    state = cli.load_state(slug)
    canonical = state["interview_curriculum"]
    first, second = canonical["route"]["skills"][:2]
    canonical["evidence"]["exposed"] = [first["skill_ref"]["skill_id"]]
    operation_id = str(uuid4())
    canonical["active_operation"] = {
        "submission_id": operation_id,
        "status": "reserved",
        "target": second,
        "reason": "uncovered_required",
        "rollback": {
            "cursor": {
                "present": True,
                "value": {
                    "unit_id": first["unit_id"],
                    "section_id": first["section_id"],
                    "skill_ref": first["skill_ref"],
                    "instruction_status": "covered",
                },
            }
        },
    }
    canonical["cursor"] = {
        "unit_id": second["unit_id"],
        "section_id": second["section_id"],
        "skill_ref": second["skill_ref"],
        "instruction_status": "reserved",
    }
    state["interview_curriculum"] = canonical
    state["_openlearn_internal"]["active_turn"] = {
        "submission_id": operation_id,
        "status": "reserved",
        "owner_pid": __import__("os").getpid(),
    }
    cli.write_text_atomic(
        cli.topic_state_path(slug), json.dumps(state, indent=2, sort_keys=True) + "\n"
    )
    cli.append_session(
        cli.read_topic(slug),
        "next",
        "Teach the first concept.",
        "**Lesson:** Keep this committed lesson visible.",
    )

    page = client.get(f"/courses/{slug}")

    assert page.status_code == 200
    assert "Keep this committed lesson visible" in page.text
    assert first["section_label"] in page.text
    assert second["section_label"] in page.text
    assert "Next target" in page.text
    assert "Step " not in page.text


def test_historical_visible_lesson_question_is_answered_after_another_lesson_commits(
    client: TestClient,
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Bound Side Chat",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    application.accept_interview_curriculum(
        slug, action="skip", submission_id=str(uuid4())
    )
    initialized = OpenLearnWebServices().start_course_initialization(slug)
    wait_for_operation(client, slug, initialized["operation_id"])
    first_page = client.get(f"/courses/{slug}")
    source_id = first_page.text.split('name="source_lesson_id" value="', 1)[1].split(
        '"', 1
    )[0]
    source_title = first_page.text.split(
        'name="source_lesson_title" value="', 1
    )[1].split('"', 1)[0]
    source_revision = int(
        first_page.text.split('name="source_lesson_revision" value="', 1)[1].split(
            '"', 1
        )[0]
    )

    advanced = client.post(
        f"/api/courses/{slug}/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "next",
            "text": "",
            "submission_id": str(uuid4()),
            "expected_revision": source_revision,
        },
    )
    assert advanced.status_code == 202
    wait_for_operation(client, slug, advanced.json()["operation_id"])
    current_revision = tutor_service.course_revision(slug)

    historical_question = client.post(
        f"/api/courses/{slug}/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "question",
            "text": "Explain the lesson I still have open.",
            "submission_id": str(uuid4()),
            "expected_revision": current_revision,
            "source_lesson_id": source_id,
            "source_lesson_title": source_title,
            "source_lesson_revision": source_revision,
        },
    )

    assert historical_question.status_code == 202
    historical_result = wait_for_operation(
        client, slug, historical_question.json()["operation_id"]
    )
    assert historical_result["state"] == "committed"
    assert tutor_service.course_revision(slug) == current_revision
    conversation = client.get(f"/api/courses/{slug}/chat").json()["conversation"]
    assert conversation[-1]["source_lesson_id"] == source_id
    assert conversation[-1]["source_lesson_title"] == source_title
    assert cli.load_state(slug).get("pending_learner_prompt") != (
        "Explain the lesson I still have open."
    )

    fabricated = client.post(
        f"/api/courses/{slug}/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "question",
            "text": "Explain a fabricated old lesson.",
            "submission_id": str(uuid4()),
            "expected_revision": current_revision,
            "source_lesson_id": source_id,
            "source_lesson_title": "Fabricated title",
            "source_lesson_revision": source_revision,
        },
    )
    assert fabricated.status_code == 409
    assert "visible lesson changed" in fabricated.json()["error"].lower()


def test_passive_interview_lesson_offers_skip_without_awarding_readiness(
    client: TestClient,
) -> None:
    token = csrf(client, "/courses/new")
    created = client.post(
        "/api/courses",
        headers={"x-csrf-token": token},
        json={
            "title": "Passive Interview Lesson",
            "goal": "Prepare for interviews.",
            "experience": "",
            "template_id": "technical-interview-prep",
            "submission_id": str(uuid4()),
        },
    ).json()
    slug = created["slug"]
    application.accept_interview_curriculum(
        slug, action="skip", submission_id=str(uuid4())
    )
    initialized = OpenLearnWebServices().start_course_initialization(slug)
    wait_for_operation(client, slug, initialized["operation_id"])
    page = client.get(f"/courses/{slug}")
    assert 'data-navigation-intent="next"' in page.text
    assert 'data-navigation-intent="skip"' in page.text
    assert 'data-tool-open="chat"' in page.text
    before = cli.load_state(slug)["interview_curriculum"]
    ready_before = list(before["evidence"]["ready"])
    cursor_id = before["cursor"]["skill_ref"]["skill_id"]

    skipped = client.post(
        f"/api/courses/{slug}/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "skip",
            "text": "",
            "submission_id": str(uuid4()),
            "expected_revision": tutor_service.course_revision(slug),
        },
    )
    assert skipped.status_code == 202
    wait_for_operation(client, slug, skipped.json()["operation_id"])
    after = cli.load_state(slug)["interview_curriculum"]
    assert after["evidence"]["ready"] == ready_before
    assert cursor_id not in after["evidence"]["ready"]
    assert any(item["skill_id"] == cursor_id for item in after["deferred"])


def test_focus_progress_clamps_invalid_internal_percentages() -> None:
    assert _focus_progress(CourseProgress(known=1, total=1, percent=140))["percent"] == 100
    assert _focus_progress(CourseProgress(known=0, total=1, percent=-20))["percent"] == 0


def test_present_response_hides_reasoning_from_existing_lesson_history() -> None:
    kind, blocks = _present_response(
        "The user wants their first lesson.\n"
        "I need to inspect the course metadata.\n</think>\n\n"
        "Lesson: Clarify constraints before coding."
    )

    assert kind == "Lesson"
    assert blocks == [{"kind": "paragraph", "text": "Clarify constraints before coding."}]


def test_present_response_leaves_terminal_advance_cue_to_web_controls() -> None:
    kind, blocks = _present_response(
        "**Lesson:**\nA sliding window reuses work.\n\n"
        "**Next:**\nPress Enter to continue, or type what you want more help with."
    )

    assert kind == "Lesson"
    assert blocks == [{"kind": "paragraph", "text": "A sliding window reuses work."}]


def test_plain_text_removes_inline_markdown_markers() -> None:
    assert _plain_text("Use *indices* and `left_pointer`.") == "Use indices and left_pointer."


def test_focus_renders_a_pending_check_once(client: TestClient) -> None:
    cli.cmd_new(
        argparse.Namespace(topic="Single Check", goal="Avoid duplicate questions"),
        output_func=lambda _text: None,
    )
    topic = cli.read_topic("single-check")
    question = "What output should the function return when no match exists?"
    metadata = dict(topic.metadata)
    metadata["pending_question"] = {
        "kind": "free_response",
        "question": question,
    }
    cli.write_topic(topic.path, metadata, topic.body)
    cli.append_session(
        cli.read_topic(topic.slug),
        "chat",
        "Ready",
        f"**Check:**\n{question}",
    )

    view = OpenLearnWebServices().focus(topic.slug)

    assert view["move"]["kind"] == "Check"
    assert view["requires_response"] is True
    assert view["move"]["prompt"] == question
    assert sum(
        question in str(block.get("text", ""))
        for block in view["move"]["blocks"]
    ) == 0

    page = client.get(f"/courses/{topic.slug}")
    assert 'id="learner-response"' in page.text
    assert "Send answer" in page.text
    assert "Response intent" not in page.text


def test_focus_separates_feedback_from_the_pending_check(client: TestClient) -> None:
    cli.cmd_new(
        argparse.Namespace(topic="Feedback Check", goal="Separate tutor feedback"),
        output_func=lambda _text: None,
    )
    topic = cli.read_topic("feedback-check")
    question = "Explain why two equal values at distinct indices are valid."
    metadata = dict(topic.metadata)
    metadata["pending_question"] = {
        "kind": "free_response",
        "question": question,
    }
    cli.write_topic(topic.path, metadata, topic.body)
    cli.append_session(
        cli.read_topic(topic.slug),
        "chat",
        "Ready",
        "**Feedback:**\nGood attention to the index constraint.\n\n"
        f"**Check:**\n{question}",
    )

    view = OpenLearnWebServices().focus(topic.slug)

    assert view["move"]["kind"] == "Feedback"
    assert view["move"]["prompt"] == question
    assert view["move"]["blocks"] == [
        {"kind": "paragraph", "text": "Good attention to the index constraint."}
    ]
    page = client.get(f"/courses/{topic.slug}")
    assert page.text.count(question) == 1


def test_focus_uses_template_concept_when_legacy_course_has_no_saved_focus(
    client: TestClient,
) -> None:
    cli.cmd_new(
        argparse.Namespace(topic="Legacy Interview", goal="Learn interview reasoning"),
        output_func=lambda _text: None,
    )
    topic = cli.read_topic("legacy-interview")
    metadata = dict(topic.metadata)
    metadata["current_focus"] = ""
    metadata["template_units"] = [
        "Unit 1: Interview Problem Solving - clarification and examples"
    ]
    cli.write_topic(topic.path, metadata, topic.body)
    cli.append_session(
        cli.read_topic(topic.slug),
        "chat",
        "Continue to the next useful concept.",
        "**Lesson:**\nTrace one concrete example before coding.",
    )

    view = OpenLearnWebServices().focus(topic.slug)

    assert view["current_unit"] == "Interview Problem Solving"
    assert view["move"]["title"] == "Interview Problem Solving"
    assert view["move"]["kind"] == "Current lesson"


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


def test_operation_status_exposes_safe_preview_and_recovery_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = tutor_service.TutorTurnResult(
        submission_id=str(uuid4()),
        status="generating",
        input_status="saved",
        message_kind="answer",
        move=None,
        error_code="provider_unavailable",
        preview="Lesson: A partial explanation",
    )
    monkeypatch.setattr(tutor_service, "operation_status", lambda *_args: result)

    status = OpenLearnWebServices().operation_status("existing-course", result.submission_id)

    assert status["preview_text"] == "A partial explanation"
    assert status["error_code"] == "provider_unavailable"
    assert status["show_provider_recovery"] is True
    assert "move" not in status


def test_committed_operation_status_uses_final_move_as_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = tutor_service.TutorTurnResult(
        submission_id=str(uuid4()),
        status="committed",
        input_status="saved",
        message_kind="answer",
        move=tutor_service.TutorMove(
            move_id=str(uuid4()),
            kind="feedback",
            content="Feedback: Final complete response",
            prompt="",
            revision=3,
            action_kind="continue",
            history_summary="",
        ),
    )
    monkeypatch.setattr(tutor_service, "operation_status", lambda *_args: result)

    status = OpenLearnWebServices().operation_status("existing-course", result.submission_id)

    assert status["preview_text"] == "Final complete response"


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
