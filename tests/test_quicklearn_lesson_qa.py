"""Regression checks for the first Quick Learn lesson's human QA findings."""

from argparse import Namespace
import os
from pathlib import Path

import pytest

from openlearn import cli, lesson_policy
from openlearn.web.services import _present_response


OUTLINE = "Units:\n1. Data quality (2 slides)\nConcepts: Noise; Outliers"
LESSON = (
    "**Lesson:**\nNoise is small random variation around a measurement.\n\n"
    "For example, a scale may read 70, 71, and 69 grams for the same object."
    "\n<!-- covered: Noise -->"
)
AUDIT = (
    "\n\nSource excerpts provided:\n"
    "- classwork.pdf | source_id=file:abc | checksum=abc | lines 1-12\n\n"
    "Only bounded extracted text was supplied; full source availability is not guaranteed."
)


@pytest.fixture
def course(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENLEARN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENLEARN_MOCK", "1")
    for name in (
        "OPENAI_API_KEY", "OPENLEARN_API_KEY", "ANTHROPIC_API_KEY",
        "OPENLEARN_PROVIDER", "OPENLEARN_MODEL", "OPENLEARN_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    cli.clear_config_cache()
    cli.cmd_new(Namespace(topic="Data quality QA", goal="Understand noise"),
                output_func=lambda _: None)
    topic = cli.read_topic("data-quality-qa")
    cli.save_course_started(topic, "Accepted plan", OUTLINE)
    monkeypatch.setattr(cli, "maybe_suggest_videos", lambda *_args: None)
    try:
        yield cli.read_topic(topic.slug)
    finally:
        cli.clear_config_cache()


@pytest.mark.parametrize("bad", [
    LESSON.replace("Noise is", "Let's continue with noise, which is"),
    LESSON.replace("Noise is small random variation around a measurement.",
                   "I can't supply the graded response without an instructor answer key."),
    LESSON + AUDIT,
    LESSON.replace("Noise is", "Noise in classwork.pdf "
                   "(source_id=file:abc, checksum=abc, lines 1-12) is"),
    LESSON.replace("<!-- covered: Noise -->", "<!-- covered: Outliers -->"),
])
def test_first_lesson_rejects_continuation_refusal_audits_and_wrong_target(course, bad):
    with pytest.raises(lesson_policy.FirstLessonUnavailable):
        lesson_policy.enforce_first_lesson_response(
            course.metadata, cli.first_lesson_prompt(OUTLINE), bad,
        )


def test_first_lesson_retries_observed_refusal_before_persisting(course, monkeypatch):
    invalid = (
        "Next: Let's continue with noise.\n\n"
        "Noise is ordinary scatter around a signal.\n\n"
        "I can't supply the graded response without an instructor answer key.\n\n"
        "Action: Tell me a criterion for identifying an outlier." + AUDIT
    )
    answers = iter([invalid, LESSON])
    calls = []

    def provider(_model, system, prompt, **_kwargs):
        calls.append((system, prompt))
        return next(answers)

    monkeypatch.setattr(cli, "call_openai_with_status", provider)
    cli.teach_first_lesson(course, OUTLINE, "mock", lambda _: None)
    topic = cli.read_topic(course.slug)
    _, log = cli.split_session_log(topic.body)
    entries = cli.session_entries(log)
    assert len(calls) == 2
    assert entries[-1]["response"] == cli.sanitize_model_output(LESSON)
    assert topic.metadata["slide_coverage"] == {"1:1": ["Noise"]}
    assert not topic.metadata.get("pending_question")
    assert "missing instructor answer key is not a reason" in calls[0][0]
    assert "Do not frame this as continuing" in calls[0][1]


def test_source_audit_footer_is_hidden_in_saved_lesson_presentation():
    kind, blocks = _present_response(LESSON + AUDIT)
    assert kind == "Lesson"
    assert [block["kind"] for block in blocks] == ["takeaway", "example"]
    assert "classwork.pdf" not in str(blocks)
    assert "bounded extracted" not in str(blocks)


def test_source_audit_filter_preserves_real_missing_source_facts_and_code():
    body = (
        "**Lesson:**\nThe source does not state the sensor accuracy.\n\n"
        "For example, compare repeat measurements before claiming an exact error.\n\n"
        "```text\nSource excerpts provided:\nchecksum=abc\n```"
    )
    kind, blocks = _present_response(body)
    assert kind == "Lesson"
    assert blocks[0]["text"] == "The source does not state the sensor accuracy."
    assert blocks[-1]["kind"] == "code"
    assert "checksum=abc" in blocks[-1]["text"]


def test_source_audit_filter_does_not_truncate_inline_provenance_explanation():
    body = "Source provenance: the record of where a measurement came from."
    assert cli.sanitize_model_output(body) == body


def test_source_provenance_heading_keeps_missing_fact_caveat_and_example():
    body = (
        "**Lesson:**\nSensor accuracy is the maximum expected measurement error.\n\n"
        "Source provenance:\nThe supplied source does not state the sensor accuracy, "
        "so an exact error bound is unknown.\n\n"
        "For example, compare repeat measurements before claiming an exact error."
    )
    assert cli.sanitize_model_output(body) == body
    _, blocks = _present_response(body)
    assert "exact error bound is unknown" in str(blocks)
    assert blocks[-1]["kind"] == "example"


def test_source_audit_records_do_not_hide_later_lesson_prose():
    body = LESSON + AUDIT + (
        "\n\nThe supplied source does not state the sensor accuracy.\n\n"
        "**Example:**\nCompare repeat measurements before claiming an exact error."
    )
    visible = cli.sanitize_model_output(body)
    assert "source_id=" not in visible
    assert "bounded extracted" not in visible
    assert "does not state the sensor accuracy" in visible
    assert "Compare repeat measurements" in visible


def test_source_audit_recognizer_preserves_educational_code():
    from openlearn.text import has_source_audit_metadata

    assert not has_source_audit_metadata("Use `source_id=file:abc` to select a record.")
    assert not has_source_audit_metadata("```text\nsource_id=file:abc\nchecksum=abc\n```")
    assert not has_source_audit_metadata("A checksum detects accidental changes.")


def test_generation_and_regular_tutor_prompts_separate_teaching_from_official_answers(course):
    for prompt in (cli.generation_system_prompt(course, current_plan=OUTLINE),
                   cli.system_prompt(course)):
        assert "missing instructor answer key is not a reason" in prompt
        assert "Never invent official answers" in prompt
        assert "source IDs, checksums, and extraction line ranges" in prompt


def test_first_lesson_rejects_empty_example_label():
    assert not lesson_policy.first_lesson_response_is_valid(
        "**Lesson:**\nNoise is ordinary measurement scatter.\n\nFor example, ."
    )


@pytest.mark.skipif(os.environ.get("OPENLEARN_BROWSER_TEST") != "1",
                    reason="requires installed Playwright browser")
def test_lesson_card_layout_at_desktop_and_mobile_widths(course):
    from fastapi.testclient import TestClient
    from playwright.sync_api import sync_playwright
    from openlearn.web import create_app

    topic = cli.read_topic(course.slug)
    metadata = dict(topic.metadata)
    metadata["topic"] = "Quick Learn: CS_4267_Classwork0831_Completed_" * 3
    metadata["current_focus"] = metadata["topic"]
    cli.write_topic(topic.path, metadata, topic.body)
    cli.append_session(cli.read_topic(topic.slug), "lesson", "Start my first lesson.",
                       cli.sanitize_model_output(LESSON + AUDIT))
    with TestClient(create_app(testing=True)) as client:
        html = client.get(f"/courses/{topic.slug}").text
    stylesheet = Path(__file__).resolve().parents[1] / "src/openlearn/web/static/openlearn.css"
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        page.set_content(html)
        page.add_style_tag(path=str(stylesheet))
        for width in (320, 760, 860, 1100, 1920):
            page.set_viewport_size({"width": width, "height": 900})
            for tool in (None, "chat"):
                page.locator("[data-focus-shell]").evaluate(
                    "(shell, tool) => tool ? shell.dataset.toolActive = tool : "
                    "delete shell.dataset.toolActive", tool,
                )
                # Wait for the real stylesheet's column transition to settle.
                page.wait_for_timeout(800)
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= "
                    "document.documentElement.clientWidth"
                ), (width, tool, page.evaluate("""() => [...document.querySelectorAll('*')]
                    .filter(node => node.getBoundingClientRect().right > innerWidth)
                    .slice(0, 12).map(node => [node.tagName, node.className,
                                              node.getBoundingClientRect().width])"""))
                geometry = page.locator(".move-surface").evaluate("""card => {
                    const body = card.querySelector('.move-content').getBoundingClientRect();
                    const bounds = card.getBoundingClientRect();
                    return {width: bounds.width, left: body.left - bounds.left,
                            right: bounds.right - body.right};
                }""")
                assert geometry["width"] <= 704
                assert abs(geometry["left"] - geometry["right"]) <= 1
                assert page.locator(".slide-example").count() == 1
        browser.close()
