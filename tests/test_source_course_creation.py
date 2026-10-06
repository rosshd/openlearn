from __future__ import annotations

from concurrent.futures import wait
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from openlearn import application, cli, source_imports, tutor_service
from openlearn.courses import CALIBRATION_STATE_KEY
from openlearn.web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cli.clear_config_cache()
    with TestClient(create_app(testing=True)) as client:
        yield client
        with tutor_service._FUTURES_GUARD:
            futures = tuple(tutor_service._FUTURES.values())
        if futures:
            assert not wait(futures, timeout=5)[1]


def payload(mode="quick", **changes):
    return {"title": "Synthetic fractions", "goal": "Understand halves", "experience": "New learner",
            "submission_id": str(uuid4()), "mode": mode, "source_kind": "file", **changes}


def post(client, data, files=None, *, json=True):
    token = client.get("/").cookies["openlearn_csrf"]
    return client.post("/courses/from-source", data=data, files=files,
                       headers={"X-CSRF-Token": token, "Accept": "application/json" if json else "text/html"},
                       follow_redirects=False)


def test_dashboard_and_direct_entrypoints_have_real_source_forms(client):
    dashboard = client.get("/").text
    assert 'href="http://testserver/courses/from-source"' in dashboard
    assert "Source course" in dashboard
    for path, mode, title in [("/courses/from-source", "course", "Build a course from your sources."),
                              ("/quick-learn", "quick", "Learn from one source now.")]:
        response = client.get(path)
        assert response.status_code == 200
        assert title in response.text
        assert f'name="mode" value="{mode}"' in response.text
        assert 'name="source_file"' in response.text
        assert 'value="folder"' in response.text and 'value="github"' in response.text
        assert "Starter courses" not in response.text
        assert 'enctype="multipart/form-data"' in response.text


@pytest.mark.parametrize("mode", ["course", "quick"])
def test_source_creation_imports_before_any_tutor_call_and_replays(client, monkeypatch, mode):
    def forbidden(*args, **kwargs):
        pytest.fail("Importing a source must not start a provider/tutor request")
    monkeypatch.setattr(tutor_service, "start_turn", forbidden)
    data = payload(mode)
    files = {"source_file": ("fractions.md", b"A half is one of two equal parts.", "text/markdown")}
    first = post(client, data, files)
    assert first.status_code == 200, first.text
    slug = first.json()["slug"]
    assert first.json()["state"] == "source_ready"
    assert first.json()["focus_url"].endswith(f"/courses/{slug}?tool=chat")
    topic = cli.read_topic(slug)
    assert topic.metadata["web_source_start"] is True
    assert (topic.metadata.get("learning_mode") == "quick") == (mode == "quick")
    assert len(source_imports.list_course_sources(slug)) == 1
    replay = post(client, data, files)
    assert replay.json()["slug"] == slug and replay.json()["created"] is False
    assert len(source_imports.list_course_sources(slug)) == 1
    focus = client.get(f"/courses/{slug}", follow_redirects=False)
    assert focus.status_code == 200
    assert "review and approve" in focus.text
    assert "data-source-mode checked" in focus.text
    assert client.get(f"/courses/{slug}?tool=chat").status_code == 200


def test_bad_upload_retains_form_input_and_can_be_corrected_without_duplicate_course(client):
    data = payload()
    bad = post(client, data, {"source_file": ("keys.txt", b"OPENAI_API_KEY=sk-abcdefghijklmnopqrstuv", "text/plain")})
    assert bad.status_code == 422
    assert "credential" in bad.json()["error"].lower()
    data.update(title="Corrected fractions", goal="Corrected goal", experience="Corrected experience")
    corrected = post(client, data, {"source_file": ("fractions.md", b"Two equal parts make a whole.", "text/markdown")})
    assert corrected.status_code == 200
    assert corrected.json()["created"] is False
    slug = corrected.json()["slug"]
    topic = cli.read_topic(slug)
    assert topic.metadata["topic"] == "Corrected fractions"
    assert topic.metadata["goal"] == "Corrected goal"
    assert "# Corrected fractions" in topic.body
    assert "Corrected goal" in topic.body
    assert cli.load_state(slug)[CALIBRATION_STATE_KEY]["experience"] == "Corrected experience"
    assert topic.metadata["web_source_pending"] is False
    # Successful replay must not mutate a completed creation or its calibration.
    replay = post(client, {**data, "title": "Must not rename", "goal": "Must not replace goal"},
                  {"source_file": ("fractions.md", b"Two equal parts make a whole.", "text/markdown")})
    assert replay.status_code == 200
    assert cli.read_topic(slug).metadata["topic"] == "Corrected fractions"
    assert cli.read_topic(slug).metadata["goal"] == "Corrected goal"
    assert len(list(cli.topics_dir().glob("*.md"))) == 1
    invalid = payload(source_kind="folder", source_value="", title="Kept title", goal="Kept goal")
    fallback = post(client, invalid, json=False)
    assert fallback.status_code == 422
    assert "Kept title" in fallback.text and "Kept goal" in fallback.text
    assert invalid["submission_id"] in fallback.text


def test_folder_and_github_use_existing_bounded_imports(client, tmp_path, monkeypatch):
    folder = tmp_path / "synthetic-source"
    folder.mkdir()
    (folder / "lesson.md").write_text("Fractions are equal parts of a whole.")
    response = post(client, payload("course", source_kind="folder", source_value=str(folder)))
    assert response.status_code == 200, response.text
    assert source_imports.list_course_sources(response.json()["slug"])
    # A GitHub pages URL must retain the existing repository-only rejection.
    rejected = post(client, payload(source_kind="github", source_value="https://example.github.io/course"))
    assert rejected.status_code == 422
    assert "github" in rejected.json()["error"].lower()
    # Valid repository dispatch is tested without a network clone.
    seen = []
    original = source_imports.import_course_source
    def capture(request):
        if isinstance(request.source, source_imports.PublicGitHubSource):
            seen.append(request.source.url)
            return original(source_imports.CourseSourceImportRequest(request.course_slug,
                source_imports.LocalFolderSource(folder)))
        return original(request)
    monkeypatch.setattr(source_imports, "import_course_source", capture)
    imported = post(client, payload(source_kind="github", source_value="https://github.com/example/synthetic-course"))
    assert imported.status_code == 200
    assert seen == ["https://github.com/example/synthetic-course"]


def test_no_javascript_file_submission_reaches_saved_source_course(client):
    response = post(client, payload(), {"source_file": ("lesson.md", b"One half is an equal part.")}, json=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("?tool=chat")
    assert client.get(response.headers["location"]).status_code == 200


def test_source_import_works_without_provider_and_resumes_after_setup(client, monkeypatch):
    services = client.app.state.services
    monkeypatch.setattr(services, "ensure_provider_ready", lambda: {"ready": False})
    created = post(client, payload(), {"source_file": ("source.md", b"An objective is to learn equal parts.")})
    assert created.status_code == 200
    location = created.json()["focus_url"]
    response = client.get(location, follow_redirects=False)
    assert response.status_code == 303
    assert "/setup?next=" in response.headers["location"]
    monkeypatch.setattr(services, "ensure_provider_ready", lambda: {"ready": True})
    resumed = client.get(location)
    assert resumed.status_code == 200
    assert "data-source-mode checked" in resumed.text
    assert len(source_imports.list_course_sources(created.json()["slug"])) == 1


def test_source_creation_keeps_csrf_and_upload_bounds(client, monkeypatch):
    from openlearn.web import routes
    rejected = client.post("/courses/from-source", data=payload())
    assert rejected.status_code == 403
    monkeypatch.setattr(routes, "QUICK_LEARN_MAX_FILE_BYTES", 16)
    oversized = post(client, payload(), {"source_file": ("source.md", b"x" * 17)})
    assert oversized.status_code == 422
    assert "limit" in oversized.json()["error"]
    assert not list(cli.topics_dir().glob("*.md"))


def test_source_course_dashboard_resumes_chat_without_a_plan_blocker(client, monkeypatch):
    created = post(client, payload(), {"source_file": ("fractions.md", b"Two halves make a whole.", "text/markdown")})
    slug = created.json()["slug"]
    dashboard = client.get(f"/dashboard?course={slug}")
    assert "Build this course's learning path before the first lesson." not in dashboard.text
    assert "Continue learning" in dashboard.text
    monkeypatch.setattr(application, "provider_status", lambda: SimpleNamespace(ready=True))
    before = cli.topic_path(slug).read_bytes()
    token = dashboard.cookies["openlearn_csrf"]
    resumed = client.post(f"/courses/{slug}/activate", headers={"x-csrf-token": token}, follow_redirects=False)
    assert resumed.status_code == 303
    assert resumed.headers["location"].endswith(f"/courses/{slug}?tool=chat")
    assert cli.topic_path(slug).read_bytes() == before
    assert source_imports.list_course_sources(slug)
