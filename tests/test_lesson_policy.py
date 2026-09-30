"""Isolated coverage for the shared first-lesson boundary."""

from argparse import Namespace
from pathlib import Path

import pytest

from openlearn import cli, lesson_policy


OUTLINE = "Units:\n1. Foundations (2 slides)\nConcepts: Definitions; Examples"
VALID_LESSON = (
    "**Lesson:**\nA definition gives a term one precise meaning."
    "\n\nFor example, a triangle is a shape with three straight sides."
    "\n<!-- covered: Definitions -->"
)


@pytest.fixture
def course(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> cli.Topic:
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    cli.clear_config_cache()
    cli.cmd_new(Namespace(topic="Policy fixture", goal="Learn definitions"), output_func=lambda _: None)
    topic = cli.read_topic("policy-fixture")
    cli.save_course_started(topic, "Accepted outline", OUTLINE)
    monkeypatch.setattr(cli, "maybe_suggest_videos", lambda *_args: None)
    try:
        yield cli.read_topic(topic.slug)
    finally:
        cli.clear_config_cache()


@pytest.mark.parametrize("prompt", [
    lesson_policy.COURSE_INITIALIZATION_PROMPT,
    lesson_policy.first_lesson_prompt(OUTLINE),
    "Start teaching unit 1 from this accepted course plan. Historical saved wording",
])
def test_initialization_recognizes_current_and_saved_prompts(prompt: str) -> None:
    assert lesson_policy.is_course_initialization_prompt(prompt)


@pytest.mark.parametrize("prompt", [None, 2, "continue", "Explain definitions"])
def test_initialization_does_not_recognize_regular_turns(prompt: object) -> None:
    assert not lesson_policy.is_course_initialization_prompt(prompt)


@pytest.mark.parametrize("answer", [
    "**Next:** Press Enter to continue.",
    "**Check:** Which is correct?\nA) One\nB) Two\n<!-- answer: B -->",
    "**Lesson:**\nA definition gives a term meaning.",
    VALID_LESSON.replace("Definitions", "Off-topic concept"),
    VALID_LESSON + '<!-- openlearn-action: {"action": "start_coding_drill"} -->',
])
def test_policy_replaces_invalid_first_lesson(course: cli.Topic, answer: str) -> None:
    guarded = lesson_policy.enforce_first_lesson_response(
        course.metadata, lesson_policy.first_lesson_prompt(OUTLINE), answer
    )
    assert lesson_policy.first_lesson_response_is_valid(guarded)
    assert cli.extract_covered_concepts(guarded) == ["Definitions"]
    assert "Check:" not in guarded
    assert "Next:" not in guarded
    assert cli.extract_answer_key(guarded) == ""


def test_policy_preserves_exact_valid_response(course: cli.Topic) -> None:
    assert lesson_policy.first_lesson_response_is_valid(VALID_LESSON)
    assert lesson_policy.enforce_first_lesson_response(
        course.metadata, lesson_policy.first_lesson_prompt(OUTLINE), VALID_LESSON
    ) == VALID_LESSON
    assert cli.first_lesson_prompt is lesson_policy.first_lesson_prompt
    assert cli.first_lesson_response_is_valid is lesson_policy.first_lesson_response_is_valid


def test_policy_leaves_regular_checks_alone(course: cli.Topic) -> None:
    check = "**Check:** Trace the next step?"
    assert lesson_policy.enforce_first_lesson_response(course.metadata, "Explain definitions", check) == check


@pytest.mark.parametrize("answer", [VALID_LESSON, "**Next:** Press Enter to continue."])
def test_policy_guards_ordinary_sentinel(course: cli.Topic, answer: str) -> None:
    guarded = lesson_policy.enforce_first_lesson_response(
        course.metadata, lesson_policy.COURSE_INITIALIZATION_PROMPT, answer
    )
    assert lesson_policy.first_lesson_response_is_valid(guarded)
    if answer == VALID_LESSON:
        assert guarded == answer


def test_sentinel_generation_does_not_claim_an_accepted_plan() -> None:
    prompt = lesson_policy.initialization_generation_prompt(lesson_policy.COURSE_INITIALIZATION_PROMPT)
    assert "Teach exactly one concept" in prompt
    assert "For example," in prompt
    assert "accepted course plan" not in prompt.lower()
    assert lesson_policy.initialization_generation_prompt(cli.first_lesson_prompt(OUTLINE)) == cli.first_lesson_prompt(OUTLINE)


@pytest.mark.parametrize("answer", [VALID_LESSON, "**Next:** Press Enter to continue."])
def test_cli_startup_guards_before_extraction_and_persistence(
    course: cli.Topic, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    monkeypatch.setattr(cli, "call_openai_with_status", lambda *_args, **_kwargs: answer)
    output: list[str] = []
    cli.teach_first_lesson(course, OUTLINE, "mock", output.append)
    stored = cli.read_topic(course.slug)
    _body, log = cli.split_session_log(stored.body)
    lesson = cli.session_entries(log)[-1]["response"]
    assert lesson_policy.first_lesson_response_is_valid(lesson)
    assert "<!--" not in lesson
    assert "<!--" not in "\n".join(output)
    assert stored.metadata["slide_coverage"] == {"1:1": ["Definitions"]}
    assert "pending_question" not in stored.metadata
    if answer == VALID_LESSON:
        assert lesson == cli.sanitize_model_output(VALID_LESSON)


@pytest.mark.parametrize("answer", [
    VALID_LESSON,
    "**Next:** Press Enter to continue.",
    "**Check:** Which fits?\nA) First\nB) Second\n<!-- answer: B --><!-- focus: Wrong focus -->",
])
def test_ordinary_initialization_preserves_valid_lesson_and_rejects_quiz_metadata(
    course: cli.Topic, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    calls: list[str] = []

    def provider(_model: str, _system: str, prompt: str) -> str:
        calls.append(prompt)
        return answer

    monkeypatch.setattr(cli, "call_openai", provider)
    monkeypatch.setattr(cli, "update_learning_metadata", lambda *_args, **_kwargs: pytest.fail("initialization is not an assessment"))
    response = cli.ask_topic(course.slug, cli.first_lesson_prompt(OUTLINE), output_func=lambda _: None)
    assert len(calls) == 1
    assert lesson_policy.first_lesson_response_is_valid(response)
    assert "<!--" not in response
    stored = cli.read_topic(course.slug)
    assert "pending_question" not in stored.metadata
    assert stored.metadata["current_focus"] == "Foundations"
    assert stored.metadata["slide_coverage"] == {"1:1": ["Definitions"]}
    assert not stored.metadata.get("concept_attempts")
    assert not stored.metadata.get("known")
    assert not stored.metadata.get("srs")
    if answer == VALID_LESSON:
        assert response == cli.sanitize_model_output(VALID_LESSON)


@pytest.mark.parametrize("prompt", [cli.first_lesson_prompt(OUTLINE), lesson_policy.COURSE_INITIALIZATION_PROMPT])
def test_override_initialization_uses_same_guard(course: cli.Topic, prompt: str) -> None:
    response = cli.ask_topic(
        course.slug, prompt, output_func=lambda _: None,
        generated_answer_override="**Check:** Which fits?\nA) First\nB) Second\n<!-- answer: A -->",
    )
    assert lesson_policy.first_lesson_response_is_valid(response)
    assert "pending_question" not in cli.read_topic(course.slug).metadata


@pytest.mark.parametrize("prompt", [cli.first_lesson_prompt(OUTLINE), lesson_policy.COURSE_INITIALIZATION_PROMPT])
def test_first_lesson_preview_waits_for_the_guard(
    course: cli.Topic, monkeypatch: pytest.MonkeyPatch, prompt: str
) -> None:
    raw = "**Check:** Which fits?\nA) First\nB) Second\n<!-- answer: A -->"
    monkeypatch.setattr(cli, "call_openai", lambda *_args: raw)
    preview: list[str] = []
    output: list[str] = []
    metadata: list[cli.TutorResponseMetadata] = []
    answer = cli.generate_validated_tutor_answer(
        course, prompt, "mock",
        output_func=output.append, stream_sink=preview.append,
        response_metadata_sink=metadata.append,
    )
    assert lesson_policy.first_lesson_response_is_valid(answer)
    assert "Check:" not in "\n".join(preview + output)
    assert "<!--" not in "\n".join(preview + output)
    assert metadata[-1].answer_key == ""
    assert metadata[-1].covered_concepts == ("Definitions",)


def test_sentinel_side_chat_override_keeps_regular_check(
    course: cli.Topic, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = "**Check:** Trace the next step?"
    monkeypatch.setattr(cli, "enforce_first_lesson_response", lambda *_args: pytest.fail("side chat must not initialize the course"))
    assert cli.ask_topic(
        course.slug, lesson_policy.COURSE_INITIALIZATION_PROMPT,
        output_func=lambda _: None, generated_answer_override=check,
        session_kind=cli.SIDE_CHAT_SESSION_KIND, message_kind_override="question",
    ) == check


def test_interview_sentinel_uses_target_fallback_without_ordinary_guard(
    course: cli.Topic, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openlearn import interview_curriculum

    check = "**Check:** Produce the next step?"
    monkeypatch.setattr(cli, "system_prompt", lambda *_args, **_kwargs: "target rules")
    monkeypatch.setattr(cli, "call_openai", lambda *_args: "**Next:** Continue.")
    monkeypatch.setattr(cli, "enforce_first_lesson_response", lambda *_args: pytest.fail("interview target owns its first lesson"))
    monkeypatch.setattr(interview_curriculum, "target_response_error", lambda *_args: "invalid target")
    monkeypatch.setattr(interview_curriculum, "deterministic_target_fallback", lambda *_args: check)
    target = {"skill_label": "Definitions", "depth_mode": "learn"}
    assert cli.generate_validated_tutor_answer(
        course, lesson_policy.COURSE_INITIALIZATION_PROMPT, "mock",
        output_func=lambda _: None, interview_target=target,
    ) == check
    assert cli.ask_topic(
        course.slug, lesson_policy.COURSE_INITIALIZATION_PROMPT,
        output_func=lambda _: None, generated_answer_override="**Next:** Continue.",
        interview_target=target, message_kind_override="question",
    ) == check


def test_first_lesson_provider_failure_keeps_accepted_plan_for_retry(
    course: cli.Topic, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = course.path.read_bytes()

    def fail(*_args, **_kwargs):
        raise cli.OpenLearnError("provider unavailable")

    monkeypatch.setattr(cli, "call_openai_with_status", fail)
    with pytest.raises(cli.OpenLearnError, match="provider unavailable"):
        cli.teach_first_lesson(course, OUTLINE, "mock", lambda _: None)
    assert course.path.read_bytes() == before
    monkeypatch.setattr(cli, "call_openai_with_status", lambda *_args, **_kwargs: VALID_LESSON)
    cli.teach_first_lesson(cli.read_topic(course.slug), OUTLINE, "mock", lambda _: None)
    _body, log = cli.split_session_log(cli.read_topic(course.slug).body)
    assert [entry["kind"] for entry in cli.session_entries(log)] == ["course_plan", "lesson"]
