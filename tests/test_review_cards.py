from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest

from openlearn import cli, review_cards
from openlearn.web.schemas import ReviewGradeRequest, ReviewPrepareRequest, ReviewRevealRequest


NOTES = "Outliers can be valid observations. Noise obscures the signal through unwanted variation."
GENERATED = {"question": "How do outliers differ from noise?", "answer": NOTES,
             "explanation": "Being unusual alone does not make an observation noise.",
             "sources": [{"label": "Course notes", "excerpt": NOTES}]}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENLEARN_MODEL", raising=False)
    cli.clear_config_cache()
    cli.write_topic(cli.topic_path("review-course"), {
        "topic": "Review course", "review_due": [{"concept": "outlier vs noise",
        "due": "2020-01-01", "difficulty": "hard"}], "known": [],
    }, "## Notes\n" + NOTES + "\n")
    yield tmp_path
    cli.clear_config_cache()


def item():
    return review_cards.session(["review-course"])["items"][0]


def prepare(monkeypatch, calls=None):
    def generate(*args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        return json.dumps(GENERATED)
    monkeypatch.setattr(cli, "call_openai", generate)
    current = item()
    result = review_cards.prepare(ReviewPrepareRequest(**current))
    assert result["ok"]
    return result["item"]


def reveal(current):
    result = review_cards.reveal(ReviewRevealRequest(**current))
    assert result["ok"]
    return result


def grade_request(current, revealed, result="good", submission_id=None):
    return ReviewGradeRequest(**current, reveal_token=revealed["reveal_token"],
                              result=result, submission_id=submission_id or str(uuid4()))


def raw():
    return cli.read_topic("review-course").metadata


def test_explicit_preparation_source_validation_and_offline_reload(home, monkeypatch):
    current = item()
    assert current["state"] == "needs_preparation"
    assert current["card_id"] == ""
    assert "answer" not in json.dumps(current)
    calls = []
    saved = prepare(monkeypatch, calls)
    assert "conceptual rationale or example in clear plain paragraphs" in calls[0][0][1]
    assert "no self-rating instructions or checklist" in calls[0][0][1]
    card = raw()["review_due"][0]["review_card"]
    assert card["explanation_kind"] == "conceptual"
    assert card["slug"] == "review-course"
    assert card["concept"] == "outlier vs noise"
    monkeypatch.setattr(cli, "call_openai", lambda *args, **kwargs: pytest.fail("offline called provider"))
    assert item() == saved
    shown = reveal(saved)
    assert shown["answer"] == NOTES
    assert shown["explanation"] == GENERATED["explanation"]
    assert raw()["review_due"][0]["due"] == "2020-01-01"
    assert "review_rating_receipts" not in raw()
    assert item() == saved
    assert "answer" not in json.dumps(item())
    assert review_cards.prepare(ReviewPrepareRequest(**saved))["item"] == saved


@pytest.mark.parametrize("explanation_kind", [None, "recall_checklist"])
def test_legacy_explanation_is_hidden_without_rewriting_saved_card(home, monkeypatch, explanation_kind):
    from copy import deepcopy

    prepare(monkeypatch)
    topic = cli.read_topic("review-course")
    saved = topic.metadata["review_due"][0]["review_card"]
    saved["explanation"] = "Check whether your recall included a valid outlier."
    if explanation_kind is None:
        saved.pop("explanation_kind")
    else:
        saved["explanation_kind"] = explanation_kind
    before = deepcopy(saved)
    cli.write_topic(topic.path, topic.metadata, topic.body)
    current = item()
    monkeypatch.setattr(cli, "call_openai", lambda *args, **kwargs: pytest.fail("legacy card regenerated"))
    shown = reveal(current)
    assert shown["answer"] == before["answer"]
    assert shown["explanation"] == ""
    assert shown["sources"] == before["sources"]
    assert raw()["review_due"][0]["review_card"] == before
    assert item() == current


@pytest.mark.parametrize("response", ["not json", "{}", json.dumps({**GENERATED,
    "sources": [{"label": "Course notes", "excerpt": "This excerpt does not appear in saved material."}]}),
    json.dumps({**GENERATED, "question": ""})])
def test_preparation_failure_remains_due(home, monkeypatch, response):
    before = item()
    monkeypatch.setattr(cli, "call_openai", lambda *args, **kwargs: response)
    result = review_cards.prepare(ReviewPrepareRequest(**before))
    assert not result["ok"]
    assert result["state"] == "preparation_failed"
    assert item() == before


def test_missing_material_and_provider_failure_are_safe(home, monkeypatch):
    topic = cli.read_topic("review-course")
    cli.write_topic(topic.path, topic.metadata, "# outlier vs noise\n")
    monkeypatch.setattr(cli, "call_openai", lambda *args, **kwargs: pytest.fail("no material"))
    before = item()
    result = review_cards.prepare(ReviewPrepareRequest(**before))
    assert result["code"] == "missing_source"
    assert item() == before
    cli.write_topic(topic.path, topic.metadata, NOTES)
    def failed(*args, **kwargs):
        raise cli.OpenLearnError("secret-key https://private.example")
    monkeypatch.setattr(cli, "call_openai", failed)
    result = review_cards.prepare(ReviewPrepareRequest(**item()))
    assert result["state"] == "preparation_failed"
    assert "secret" not in json.dumps(result)
    assert item()["due"] == "2020-01-01"


@pytest.mark.parametrize("rating,days", [("hard", 2), ("good", 4), ("easy", 7)])
def test_correct_ratings_commit_preview_without_mastery(home, monkeypatch, rating, days):
    current = prepare(monkeypatch)
    shown = reveal(current)
    preview = next(entry for entry in shown["ratings"] if entry["result"] == rating)
    expected = (datetime.fromisoformat(cli.today()) + timedelta(days=days)).date().isoformat()
    assert preview["next_due"] == expected
    request = grade_request(current, shown, rating)
    result = review_cards.grade(request)
    assert result["state"] == "committed"
    assert result["next_due"] == preview["next_due"]
    assert result["item"] is None
    assert result["relearn_at"] is None
    metadata = raw()
    assert metadata["known"] == []
    receipt = metadata["review_rating_receipts"][request.submission_id]
    assert receipt["evidence_kind"] == "self_reported_recall"
    assert not cli.topic_events_path("review-course").exists()
    assert not metadata.get("concept_attempts")


def test_again_one_minute_from_commit_resumes_and_known_cleanup_preserves_it(home, monkeypatch):
    clock = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(review_cards, "now", lambda: clock)
    current = prepare(monkeypatch)
    shown = reveal(current)
    preview = shown["ratings"][0]
    assert preview["result"] == "again"
    assert preview["interval_seconds"] == 60
    assert preview["relearn_at"] is None
    clock += timedelta(minutes=5)
    result = review_cards.grade(grade_request(current, shown, "again"))
    assert result["relearn_at"] == (clock + timedelta(minutes=1)).isoformat()
    waiting = item()
    assert waiting["state"] == "waiting"
    assert review_cards.reveal(ReviewRevealRequest(**waiting))["state"] == "conflict"
    metadata = raw()
    metadata["known"] = ["outlier vs noise"]
    cli.remove_known_from_review_lists(metadata)
    cli.normalize_review_due_metadata(metadata)
    assert metadata["review_due"][0]["review_card"]["card_id"] == current["card_id"]
    assert metadata["review_due"][0]["relearn_at"] == result["relearn_at"]
    clock += timedelta(seconds=61)
    resumed = item()
    assert resumed["state"] == "question"
    assert resumed["review_revision"] != current["review_revision"]
    assert resumed["card_id"] == current["card_id"]
    assert review_cards.grade(grade_request(resumed, reveal(resumed), "good"))["item"] is None


def test_reveal_gate_replays_conflicts_and_stale_content(home, monkeypatch):
    current = prepare(monkeypatch)
    fake = {"reveal_token": str(uuid4())}
    assert review_cards.grade(grade_request(current, fake))["state"] == "conflict"
    shown = reveal(current)
    request = grade_request(current, shown)
    result = review_cards.grade(request)
    assert review_cards.grade(request) == result
    changed = request.model_copy(update={"result": "easy"})
    assert review_cards.grade(changed)["state"] == "conflict"
    assert review_cards.grade(grade_request(current, shown))["state"] == "conflict"
    assert len(raw()["review_rating_receipts"]) == 1


def test_edited_content_invalidates_reveal_without_changing_version(home, monkeypatch):
    current = prepare(monkeypatch)
    shown = reveal(current)
    topic = cli.read_topic("review-course")
    topic.metadata["review_due"][0]["review_card"]["answer"] = "Changed answer"
    cli.write_topic(topic.path, topic.metadata, topic.body)
    assert item()["review_revision"] != current["review_revision"]
    assert review_cards.grade(grade_request(current, shown))["state"] == "conflict"


def test_concurrent_raters_commit_once(home, monkeypatch):
    current = prepare(monkeypatch)
    first = reveal(current)
    second = reveal(current)
    requests = [grade_request(current, first, "hard"), grade_request(current, second, "easy")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(review_cards.grade, requests))
    assert sorted(entry["state"] for entry in results) == ["committed", "conflict"]
    assert len(raw()["review_rating_receipts"]) == 1


def test_normalization_and_legacy_schedule_keep_card_invalidate_occurrence(home, monkeypatch):
    current = prepare(monkeypatch)
    shown = reveal(current)
    cli.schedule_review_outcomes("review-course", [(current, "hard")])
    retained = raw()["review_due"][0]
    assert retained["review_card"]["card_id"] == current["card_id"]
    assert "review_reveals" not in retained
    assert review_cards.grade(grade_request(current, shown))["state"] == "conflict"
    assert cli.next_review_due_fixed("missed") == (datetime.fromisoformat(cli.today()) + timedelta(days=1)).date().isoformat()


def test_source_selection_excludes_learner_answers_and_unrelated_courses(home):
    body = "## Session Log\n### 2026-10-06 - chat\n**Prompt**\n" + NOTES + "\n**Response**\nYour answer is wrong.\n"
    assert review_cards.selected_sources("review-course", body, "outlier vs noise distinction") == []
    body += "### 2026-10-06 - lesson\n**Prompt**\nPRIVATE LEARNER TEXT\n**Response**\n" + NOTES
    selected = review_cards.selected_sources("review-course", body, "outlier vs noise distinction")
    assert len(selected) == 1
    assert selected[0]["text"] == NOTES
    assert "PRIVATE" not in json.dumps(selected)


def test_reveal_receipts_bounded_and_expired_receipt_conflicts(home, monkeypatch):
    current = prepare(monkeypatch)
    oldest = reveal(current)
    for _ in range(32):
        reveal(current)
    assert len(raw()["review_due"][0]["review_reveals"]) == 32
    assert review_cards.grade(grade_request(current, oldest))["state"] == "conflict"


def test_prepare_rechecks_race_after_provider(home, monkeypatch):
    before = item()
    def provider(*args, **kwargs):
        cli.schedule_review_outcomes("review-course", [(before, "easy")])
        return json.dumps(GENERATED)
    monkeypatch.setattr(cli, "call_openai", provider)
    assert review_cards.prepare(ReviewPrepareRequest(**before))["state"] == "conflict"
    assert not raw()["review_due"][0].get("review_card")


def test_good_ebisu_outcome_explicit_and_legacy_preserved(home, monkeypatch):
    assert cli.ebisu_initial_halflife("good") == 4.0
    assert cli.EBISU_REVIEW_OUTCOME["good"] == (1, 1)
    assert cli.EBISU_REVIEW_OUTCOME["hard"] == (1, 2)
    assert cli.EBISU_REVIEW_OUTCOME["missed"] == (0, 1)
    assert cli.EBISU_REVIEW_OUTCOME["again"] == (0, 1)


def test_http_contract_csrf_version_gates_and_replay(home, monkeypatch):
    from fastapi.testclient import TestClient
    from openlearn.web import create_app
    with TestClient(create_app(testing=True)) as client:
        monkeypatch.setattr(cli, "call_openai", lambda *args, **kwargs: pytest.fail("GET provider"))
        response = client.get("/api/review/session?course=review-course")
        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["count"] == 1
        assert snapshot["server_time"]
        current = snapshot["items"][0]
        token = response.cookies["openlearn_csrf"]
        headers = {"x-csrf-token": token}
        assert client.post("/api/review/prepare", json=current).status_code == 403
        assert client.get("/api/review/session?course=../secret").status_code == 404
        monkeypatch.setattr(cli, "call_openai", lambda *args, **kwargs: json.dumps(GENERATED))
        prepared = client.post("/api/review/prepare", json=current, headers=headers)
        assert prepared.status_code == 200
        current = prepared.json()["item"]
        assert "answer" not in prepared.text
        monkeypatch.setattr(cli, "call_openai", lambda *args, **kwargs: pytest.fail("offline provider"))
        shown_response = client.post("/api/review/reveal", json=current, headers=headers)
        assert shown_response.status_code == 200
        shown = shown_response.json()
        payload = grade_request(current, shown).model_dump()
        invalid = {**payload, "content_version": 2}
        assert client.post("/api/review", json=invalid, headers=headers).status_code == 409
        accepted = client.post("/api/review", json=payload, headers=headers)
        assert accepted.status_code == 200
        assert accepted.json()["state"] == "committed"
        replay = client.post("/api/review", json=payload, headers=headers)
        assert replay.json() == accepted.json()
        conflict_response = client.post("/api/review", json={**payload, "result": "easy"}, headers=headers)
        assert conflict_response.status_code == 409
        assert client.get("/api/review/session?course=review-course").json()["count"] == 0


def test_write_failure_after_commit_is_replay_safe(home, monkeypatch):
    current = prepare(monkeypatch)
    request = grade_request(current, reveal(current))
    original = review_cards._save
    def interrupted(slug, metadata, body):
        original(slug, metadata, body)
        raise OSError("simulated interruption after durable write")
    monkeypatch.setattr(review_cards, "_save", interrupted)
    with pytest.raises(OSError):
        review_cards.grade(request)
    result = review_cards.grade(request)
    assert result["state"] == "committed"
    assert len(raw()["review_rating_receipts"]) == 1


def test_model_precedence_and_single_bounded_provider_attempt(home, monkeypatch):
    current = item()
    monkeypatch.setenv("OPENLEARN_MODEL", "explicit-env-model")
    def provider(model, system, user, **kwargs):
        assert model == "explicit-env-model"
        assert kwargs["max_attempts"] == 1
        assert kwargs["json_response"]
        assert "question" in system and "sources" in user
        return json.dumps(GENERATED)
    monkeypatch.setattr(cli, "call_openai", provider)
    assert review_cards.prepare(ReviewPrepareRequest(**current))["ok"]


def test_provider_reports_insufficient_material_without_postponing(home, monkeypatch):
    current = item()
    monkeypatch.setattr(cli, "call_openai", lambda *args, **kwargs: '{"insufficient_source":true}')
    result = review_cards.prepare(ReviewPrepareRequest(**current))
    assert result["code"] == "missing_source"
    assert item() == current


def test_string_legacy_normalization_does_not_inherit_review_history(home):
    metadata = {"review_due": [
        {"concept": "previous", "due": "2020-01-01", "last_reviewed": "2019-12-31"},
        "legacy label",
    ]}
    cli.normalize_review_due_metadata(metadata)
    assert "last_reviewed" not in metadata["review_due"][1]
    assert metadata["review_due"][0]["last_reviewed"] == "2019-12-31"


def test_web_chat_teaching_sections_are_sources_without_prompts_or_checks(home):
    response = "**Feedback:**\nLEARNER ASSESSMENT\n\n**Lesson:**\n" + NOTES
    response += "\n\n**Example:**\nA valid unusual observation may still carry useful signal."
    response += "\n\n**Check:**\nPRIVATE RETRIEVAL QUESTION\n\n**Next:**\nPRIVATE NEXT STEP"
    body = "## Session Log\n### 2026-10-06 - chat\n**Prompt**\nPRIVATE LEARNER ANSWER\n**Response**\n" + response
    selected = review_cards.selected_sources("review-course", body, "outlier vs noise distinction")
    assert len(selected) == 1
    assert NOTES in selected[0]["text"]
    assert "valid unusual observation" in selected[0]["text"]
    assert "PRIVATE" not in json.dumps(selected)
    assert "ASSESSMENT" not in json.dumps(selected)


def test_ebisu_preview_and_commit_use_same_updated_good_model(home, monkeypatch):
    from types import SimpleNamespace
    calls = []
    def update(model, successes, total, elapsed):
        calls.append((successes, total, elapsed))
        return [model[0] + successes, model[1] + total - successes, model[2] + successes]
    fake = SimpleNamespace(defaultModel=lambda half: [3.0, 3.0, half],
        updateRecall=update, modelToPercentileDecay=lambda model, threshold: model[2])
    monkeypatch.setattr(cli, "read_config", lambda: {"srs": "ebisu"})
    monkeypatch.setattr(cli, "_load_ebisu", lambda: fake)
    current = prepare(monkeypatch)
    shown = reveal(current)
    preview = next(entry for entry in shown["ratings"] if entry["result"] == "good")
    expected_due = (datetime.fromisoformat(cli.today()) + timedelta(days=5)).date().isoformat()
    assert preview["next_due"] == expected_due
    result = review_cards.grade(grade_request(current, shown))
    assert result["next_due"] == preview["next_due"]
    assert raw()["review_due"][0]["ebisu_model"] == [4.0, 3.0, 5.0]
    assert calls == [(0, 1, 1.0), (1, 2, 1.0), (1, 1, 1.0), (1, 1, 1.0)]


def test_again_remains_actionable_on_browser_dashboard_and_progress(home, monkeypatch):
    from fastapi.testclient import TestClient
    from openlearn.web import create_app
    from openlearn.web.services import OpenLearnWebServices
    current = prepare(monkeypatch)
    review_cards.grade(grade_request(current, reveal(current), "again"))
    assert item()["state"] == "waiting"
    services = OpenLearnWebServices()
    dashboard = services.dashboard(selected_slug="review-course")
    assert dashboard["due_reviews"] == 1
    course = dashboard["courses"][0]
    assert course["review"]["actionable"]
    assert course["review"]["due"] == 1
    assert course["review_due"] == 1
    assert course["review"]["next_retrieval"] == item()["relearn_at"]
    assert services.progress()["courses"][0]["due_reviews"] == 1
    with TestClient(create_app(testing=True)) as client:
        response = client.get("/dashboard?course=review-course")
        assert response.status_code == 200
        assert "Review 1 item" in response.text
        assert "review?course=review-course" in response.text


UNRELATED_MATERIAL = "Linear transformations preserve algebraic structure in vector spaces. " * 90


def _source_preparation(monkeypatch, expected_label):
    calls = []
    def provider(_model, _system, user, **kwargs):
        supplied = json.loads(user)["sources"]
        assert any(source["label"] == expected_label and NOTES in source["text"]
                   for source in supplied)
        assert sum(len(source["text"]) for source in supplied) <= review_cards.SOURCE_LIMIT
        assert all(len(source["text"]) <= review_cards.PER_SOURCE_LIMIT for source in supplied)
        assert "PRIVATE LEARNER" not in user
        assert "PRIVATE FEEDBACK" not in user
        assert kwargs["max_attempts"] == 1
        calls.append(user)
        return json.dumps({**GENERATED, "sources": [{"label": expected_label, "excerpt": NOTES}]})
    monkeypatch.setattr(cli, "call_openai", provider)
    result = review_cards.prepare(ReviewPrepareRequest(**item()))
    assert result["ok"]
    assert len(calls) == 1
    assert raw()["review_due"][0]["review_card"]["sources"] == [
        {"label": expected_label, "excerpt": NOTES}
    ]


def _saved_chat_lessons(texts):
    return "## Session Log\n" + "\n".join(
        f"### 2026-10-06 - chat\n**Prompt**\nPRIVATE LEARNER ANSWER\n"
        f"**Response**\n**Lesson:**\n{text}\n\n**Feedback:**\nPRIVATE FEEDBACK\n"
        for text in texts
    )


def test_source_repair_finds_support_after_first_4000_note_characters(home, monkeypatch):
    body = "## Notes\n" + UNRELATED_MATERIAL + "\n\n" + NOTES
    assert body.index(NOTES) > review_cards.PER_SOURCE_LIMIT
    topic = cli.read_topic("review-course")
    cli.write_topic(topic.path, topic.metadata, body)
    _source_preparation(monkeypatch, "Course notes")


def test_source_repair_prioritizes_fourth_relevant_lesson_over_old_long_lessons(home, monkeypatch):
    body = _saved_chat_lessons([UNRELATED_MATERIAL] * 3 + [NOTES])
    topic = cli.read_topic("review-course")
    cli.write_topic(topic.path, topic.metadata, body)
    selected = review_cards.selected_sources("review-course", body, "outlier vs noise distinction")
    assert selected[0]["label"] == "Saved lesson 4"
    assert NOTES in selected[0]["text"]
    _source_preparation(monkeypatch, "Saved lesson 4")


def test_source_repair_finds_late_import_without_old_teaching_crowding_it_out(home, monkeypatch):
    body = _saved_chat_lessons([UNRELATED_MATERIAL] * 3)
    topic = cli.read_topic("review-course")
    cli.write_topic(topic.path, topic.metadata, body)
    directory = cli.topic_context_dir("review-course")
    directory.mkdir(parents=True, exist_ok=True)
    imported = directory / "material.txt"
    prefix = UNRELATED_MATERIAL * 4
    assert len(prefix) > review_cards.SOURCE_LIMIT
    assert len(prefix) + len(NOTES) < review_cards.SOURCE_SCAN_LIMIT
    imported.write_text(prefix + "\n\n" + NOTES, encoding="utf-8")
    selected = review_cards.selected_sources("review-course", body, "outlier vs noise distinction")
    assert selected[0]["label"] == "material.txt"
    assert NOTES in selected[0]["text"]
    _source_preparation(monkeypatch, "material.txt")


def test_source_selection_bounds_import_reads_skips_symlinks_and_total_prompt(home, monkeypatch):
    directory = cli.topic_context_dir("review-course")
    directory.mkdir(parents=True, exist_ok=True)
    imported = directory / "large.txt"
    imported.write_text((NOTES + "\n") * 1000, encoding="utf-8")
    private_source = home / "other-course.txt"
    private_source.write_text("PRIVATE LEARNER ANSWER " * 10, encoding="utf-8")
    (directory / "linked.txt").symlink_to(private_source)
    original_open = Path.open
    read_sizes = []
    class BoundedImport:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            self.handle.__enter__()
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def read(self, size):
            read_sizes.append(size)
            return self.handle.read(size)
    def tracked_open(path, *args, **kwargs):
        assert path != directory / "linked.txt"
        handle = original_open(path, *args, **kwargs)
        return BoundedImport(handle) if path == imported else handle
    monkeypatch.setattr(Path, "open", tracked_open)
    body = (NOTES + "\n") * 1000 + _saved_chat_lessons([(NOTES + "\n") * 1000] * 3)
    selected = review_cards.selected_sources("review-course", body, "outlier vs noise")
    assert read_sizes == [review_cards.SOURCE_SCAN_LIMIT]
    assert sum(len(source["text"]) for source in selected) <= review_cards.SOURCE_LIMIT
    assert all(len(source["text"]) <= review_cards.PER_SOURCE_LIMIT for source in selected)
    assert "PRIVATE LEARNER" not in json.dumps(selected)


def test_source_selection_prefers_recent_teaching_when_relevance_is_equal(home):
    body = _saved_chat_lessons([UNRELATED_MATERIAL] * 4)
    selected = review_cards.selected_sources("review-course", body, "outlier vs noise")
    assert selected[0]["label"] == "Saved lesson 4"
    assert len(selected) == 3


def test_source_repair_finds_old_relevant_lesson_after_33_new_unrelated_lessons(home, monkeypatch):
    body = _saved_chat_lessons([NOTES] + [UNRELATED_MATERIAL] * 33)
    topic = cli.read_topic("review-course")
    cli.write_topic(topic.path, topic.metadata, body)
    selected = review_cards.selected_sources("review-course", body, "outlier vs noise distinction")
    assert selected[0]["label"] == "Saved lesson 1"
    assert NOTES in selected[0]["text"]
    _source_preparation(monkeypatch, "Saved lesson 1")


BOUNDARY_CONTENT = {
    "question": (
        "For the stated sensor model, how can a 1.5 ms outlier remain valid, "
        "and why must it not automatically be classified as measurement noise?"
    ),
    "answer": (
        "In this sensor model, an outlier is a value far from the pattern, such as a valid "
        "1.5 ms observation from an unusual event. Its rarity alone does not establish error.\n\n"
        "Noise is unwanted measurement variation. The source uses σ² = E[(X − μ)²] to "
        "describe variance, but warns that variance alone cannot determine whether an "
        "individual observation is noise."
    ),
    "explanation": (
        "A rare real event can shift the observed value without corrupting the measurement. "
        "The model therefore requires evidence about the event and measurement process "
        "before discarding it; otherwise, filtering unusual values can erase legitimate "
        "and useful signal from the data."
    ),
}


def _boundary_preparation(home, monkeypatch, field=None, extra_word=False):
    topic = cli.read_topic("review-course")
    material = BOUNDARY_CONTENT["answer"] + "\n\n" + BOUNDARY_CONTENT["explanation"]
    cli.write_topic(topic.path, topic.metadata, "## Notes\n" + material)
    generated = {**BOUNDARY_CONTENT, "sources": [{"label": "Course notes", "excerpt": material}]}
    if extra_word:
        generated[field] += " unexpectedly"
    calls = []
    def provider(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs["max_attempts"] == 1
        return json.dumps(generated)
    monkeypatch.setattr(cli, "call_openai", provider)
    return generated, calls


def test_new_card_accepts_exact_word_limits_without_altering_caveats_or_paragraphs(home, monkeypatch):
    generated, calls = _boundary_preparation(home, monkeypatch)
    assert {key: len(generated[key].split()) for key in BOUNDARY_CONTENT} == {
        "question": 24, "answer": 60, "explanation": 40,
    }
    current = item()
    result = review_cards.prepare(ReviewPrepareRequest(**current))
    assert result["ok"]
    assert len(calls) == 1
    saved = raw()["review_due"][0]["review_card"]
    for field in BOUNDARY_CONTENT:
        assert saved[field] == generated[field]
    assert saved["sources"] == generated["sources"]
    assert saved["content_version"] == 1
    assert saved["explanation_kind"] == "conceptual"
    assert raw()["review_due"][0]["due"] == current["due"]
    shown = reveal(result["item"])
    assert shown["answer"] == generated["answer"]
    assert shown["explanation"] == generated["explanation"]


@pytest.mark.parametrize("field,limit", [("question", 24), ("answer", 60), ("explanation", 40)])
def test_new_card_rejects_one_word_over_limit_without_retry_or_partial_save(home, monkeypatch, field, limit):
    generated, calls = _boundary_preparation(home, monkeypatch, field, extra_word=True)
    assert len(generated[field].split()) == limit + 1
    current = item()
    metadata_before = raw()
    text_before = cli.topic_path("review-course").read_text(encoding="utf-8")
    result = review_cards.prepare(ReviewPrepareRequest(**current))
    assert not result["ok"]
    assert result["state"] == "preparation_failed"
    assert len(calls) == 1
    assert item() == current
    assert raw() == metadata_before
    assert cli.topic_path("review-course").read_text(encoding="utf-8") == text_before
    assert not raw()["review_due"][0].get("review_card")


@pytest.mark.parametrize("field", ["question", "answer", "explanation"])
@pytest.mark.parametrize("invalid", [" \n\t ", None, ["not text"]])
def test_new_card_word_limits_keep_nonempty_string_validation(home, monkeypatch, field, invalid):
    generated, calls = _boundary_preparation(home, monkeypatch)
    generated[field] = invalid
    current = item()
    result = review_cards.prepare(ReviewPrepareRequest(**current))
    assert result["state"] == "preparation_failed"
    assert len(calls) == 1
    assert item() == current


@pytest.mark.parametrize("field,char_limit", [("question", 2000), ("answer", 4000), ("explanation", 2000)])
def test_new_card_retains_character_limit_even_with_one_word(home, monkeypatch, field, char_limit):
    generated, calls = _boundary_preparation(home, monkeypatch)
    generated[field] = "x" * (char_limit + 1)
    assert len(generated[field].split()) == 1
    current = item()
    result = review_cards.prepare(ReviewPrepareRequest(**current))
    assert result["state"] == "preparation_failed"
    assert len(calls) == 1
    assert item() == current


@pytest.mark.parametrize("explanation_kind", ["conceptual", "recall_checklist", None])
def test_saved_over_budget_cards_reveal_and_rate_without_rewriting_content(home, monkeypatch, explanation_kind):
    from copy import deepcopy

    prepare(monkeypatch)
    topic = cli.read_topic("review-course")
    saved = topic.metadata["review_due"][0]["review_card"]
    saved["question"] = " ".join([BOUNDARY_CONTENT["question"]] * 2)
    saved["answer"] = "\n\n".join([BOUNDARY_CONTENT["answer"]] * 2)
    saved["explanation"] = "\n\n".join([BOUNDARY_CONTENT["explanation"]] * 3)
    if explanation_kind is None:
        saved.pop("explanation_kind")
    else:
        saved["explanation_kind"] = explanation_kind
    before_card = deepcopy(saved)
    cli.write_topic(topic.path, topic.metadata, topic.body)
    before_metadata = raw()
    current = item()
    assert current["state"] == "question"
    monkeypatch.setattr(cli, "call_openai", lambda *args, **kwargs: pytest.fail("saved card regenerated"))
    assert review_cards.prepare(ReviewPrepareRequest(**current))["item"] == current
    assert raw() == before_metadata
    shown = reveal(current)
    assert shown["answer"] == before_card["answer"]
    assert shown["explanation"] == (before_card["explanation"] if explanation_kind == "conceptual" else "")
    after_reveal = raw()
    after_reveal["review_due"][0].pop("review_reveals")
    assert after_reveal == before_metadata
    request = grade_request(current, shown, "good")
    result = review_cards.grade(request)
    assert result["state"] == "committed"
    assert review_cards.grade(request) == result
    after_rating = raw()
    assert after_rating["review_due"][0]["review_card"] == before_card
    after_rating.pop("review_rating_receipts")
    scheduled = after_rating["review_due"][0]
    original = before_metadata["review_due"][0]
    for key in ("due", "difficulty", "last_reviewed", "review_revision"):
        if key in original:
            scheduled[key] = original[key]
        else:
            scheduled.pop(key, None)
    assert after_rating == before_metadata
