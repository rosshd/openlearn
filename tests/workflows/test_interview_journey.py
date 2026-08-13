from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

if sys.platform == "win32":
    pytest.skip("pexpect.spawn requires a POSIX pty", allow_module_level=True)

import pexpect
from fastapi.testclient import TestClient

from openlearn import application, cli, interview_prep, tutor_service
from openlearn.web import create_app
from openlearn.web.services import OpenLearnWebServices


def test_interview_prep_public_cli_journey(spawn_openlearn) -> None:
    templates_dir = Path(__file__).resolve().parents[2] / "src" / "openlearn" / "templates"
    template_choice = next(
        str(index)
        for index, path in enumerate(sorted(templates_dir.glob("*.json")), start=1)
        if path.stem == "technical-interview-prep"
    )
    first = spawn_openlearn.spawn("cli", timeout=10)
    try:
        first.expect("Starter courses")
        first.sendline("s")
        # Rich may insert ANSI styling between the menu number and title.
        first.expect("Technical Interview Prep")
        first.sendline(template_choice)
        first.expect("Placement is a quick confidence survey")
        first.expect("Start placement, skip it, or go back")
        first.sendline("")
        first.expect("Target role family")
        first.expect(r"Choose \[1\]")
        first.sendline("")
        first.expect("Target level")
        first.expect(r"Choose \[2\]")
        first.sendline("")
        first.expect("Interview mix")
        first.expect(r"Choose \[1\]")
        first.sendline("")
        first.expect("Rapid confidence survey")
        for _topic_id, label in interview_prep.confidence_topics_for_focus("coding"):
            first.expect(label)
            first.sendline("3")
        first.expect("Suggested course outline")
        first.expect("Linear Foundations")
        first.expect("Confirm, change, or leave this course outline for later")
        first.sendline("")
        first.expect("Course outline confirmed")
        first.expect("First technical target: concept.arrays-strings")
        first.expect("Starter courses")
        first.sendline("q")
        first.expect(pexpect.EOF)
        first.child.close()
        assert first.child.exitstatus == 0
        transcript = first.clean_output
        assert "first_unique_window" not in transcript
        assert "open your configured editor" not in transcript
        assert "Docker" not in transcript
        assert "Podman" not in transcript
    finally:
        first.close()

    status = spawn_openlearn.run(
        "interview", "placement", "technical-interview-prep", "status"
    )
    assert "Placement: provisional" in status.stdout
    assert "lifecycle confidence-placement-v4" in status.stdout

    home = Path(spawn_openlearn.env["OPENLEARN_HOME"])
    profile = json.loads(
        (home / "learning-topics" / "technical-interview-prep.interview.json").read_text(
            encoding="utf-8"
        )
    )
    result = profile["placement"]["result"]
    assert result["mastery_update_applied"] is False
    assert result["gaps"]["coding_fluency"]["status"] == "uncertain"
    assert result["passport"]["uncertainty_to_verify"] == (
        "Pattern fluency must be demonstrated in later coding practice."
    )
    state = json.loads(
        (home / "learning-topics" / "technical-interview-prep.state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["interview_curriculum"]["route_id"] == "coding"
    assert state["interview_curriculum"]["cursor"]["skill_ref"]["skill_id"] == (
        "concept.arrays-strings"
    )

    spawn_openlearn.run(
        "new",
        "Ordinary Algorithms",
        "--goal",
        "Learn algorithms without interview setup",
        "--template",
        "algorithms",
    )
    assert not (
        home / "learning-topics" / "ordinary-algorithms.interview.json"
    ).exists()


def test_interview_route_stays_stable_across_maker_bench_cli_and_side_chat(
    spawn_openlearn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = Path(spawn_openlearn.env["OPENLEARN_HOME"])
    monkeypatch.setenv("OPENLEARN_HOME", str(home))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cli.clear_config_cache()

    created = application.create_course(
        application.CourseCreationRequest(
            name="Cross Interface Interview",
            template_id="technical-interview-prep",
            submission_id=str(uuid4()),
        )
    )
    slug = created.course.slug
    accepted = application.accept_interview_curriculum(
        slug,
        action="skip",
        submission_id=str(uuid4()),
    )
    route_fingerprint = accepted["canonical"]["route_fingerprint"]

    client = TestClient(create_app(testing=True))
    token = client.get("/", follow_redirects=False).cookies["openlearn_csrf"]
    initialized = OpenLearnWebServices().start_course_initialization(slug)

    def wait_for(operation_id: str) -> dict[str, object]:
        for _attempt in range(200):
            body = client.get(
                f"/api/courses/{slug}/operations/{operation_id}"
            ).json()
            if body["state"] in {"committed", "retryable_error", "conflict"}:
                return body
            time.sleep(0.01)
        raise AssertionError(f"operation {operation_id} did not finish")

    assert wait_for(str(initialized["operation_id"]))["state"] == "committed"
    first_page = client.get(f"/courses/{slug}")
    assert first_page.status_code == 200
    first_revision = tutor_service.course_revision(slug)
    first_cursor = cli.load_state(slug)["interview_curriculum"]["cursor"]

    advanced = client.post(
        f"/api/courses/{slug}/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "next",
            "text": "",
            "submission_id": str(uuid4()),
            "expected_revision": first_revision,
        },
    )
    assert advanced.status_code == 202
    assert wait_for(advanced.json()["operation_id"])["state"] == "committed"

    cli_status = spawn_openlearn.run("status", slug)
    assert "Linear Foundations / Arrays and Hashing" in cli_status.stdout
    cli_resume = spawn_openlearn.run("resume", slug)
    assert cli_resume.returncode == 0

    lesson_page = client.get(f"/courses/{slug}")
    assert lesson_page.status_code == 200

    def hidden(name: str) -> str:
        match = re.search(
            rf'name="{name}" value="([^"]*)"',
            lesson_page.text,
        )
        assert match is not None
        return match.group(1)

    revision_before_chat = tutor_service.course_revision(slug)
    source_id = hidden("source_lesson_id")
    source_title = hidden("source_lesson_title")
    source_revision = int(hidden("source_lesson_revision"))
    question = client.post(
        f"/api/courses/{slug}/turns",
        headers={"x-csrf-token": token},
        json={
            "intent": "question",
            "text": "Explain this visible lesson with one smaller example.",
            "submission_id": str(uuid4()),
            "expected_revision": revision_before_chat,
            "source_lesson_id": source_id,
            "source_lesson_title": source_title,
            "source_lesson_revision": source_revision,
        },
    )
    assert question.status_code == 202
    assert wait_for(question.json()["operation_id"])["state"] == "committed"
    assert tutor_service.course_revision(slug) == revision_before_chat
    conversation = client.get(f"/api/courses/{slug}/chat").json()["conversation"]
    assert conversation[-1]["source_lesson_id"] == source_id
    assert conversation[-1]["source_lesson_title"] == source_title

    returned = client.get(f"/courses/{slug}")
    assert returned.status_code == 200
    canonical = cli.load_state(slug)["interview_curriculum"]
    assert canonical["route_fingerprint"] == route_fingerprint
    assert canonical["cursor"] != first_cursor
    current_ref = canonical["cursor"]["skill_ref"]
    current_item = next(
        item
        for item in canonical["route"]["skills"]
        if item["skill_ref"] == current_ref
    )
    assert current_item["section_label"] in returned.text
