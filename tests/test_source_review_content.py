"""Study wrappers do not become curriculum, and later lessons retain budget room."""
import json
import os
from unittest import mock

import pytest

from openlearn import cli, source_context as sources, source_lessons
from tests.test_source_request_budget import large_plan

pytest_plugins = ["tests.test_source_lessons"]

TOPICS = [
    ("Input metrics and Euclid", "Euclid finds a greatest common divisor by repeated remainder operations."),
    ("Growth rates", "Compare logarithmic, linear, quadratic and exponential growth."),
    ("Insertion sort", "Insertion sort inserts a key into a sorted prefix."),
    ("Graph matrices", "An adjacency matrix records edges from its row vertex to its column vertex."),
    ("Halving and binary", "Repeated halving gives a logarithmic number of steps."),
    ("Floyd shortest paths", "Floyd compares a current distance with a route through one new intermediate vertex."),
    ("Matrix chain multiplication", "Choose a split to minimize the cost of multiplying a contiguous matrix chain."),
    ("Longest common subsequence", "The LCS table compares prefixes and extends a matching diagonal."),
]
GUIDE = """# Algorithm Analysis Review
## Exam scope and timing
ADMIN_SCOPE Consult the controlling document for the exam scope.
## Contents and suggested study sequence
ADMIN_CONTENTS Review these concepts and use a formula sheet.
## Using this guide
ADMIN_WORKFLOW Start with diagnostic assessment and study one weak topic at a time.
""" + "\n\n".join(f"# {i} {title}\n{text}\n" for i, (title, text) in enumerate(TOPICS, 1)) + """
# Practice midterm
ADMIN_PRACTICE Ask for a new diagnostic across the confirmed concepts.
# Sources and OpenLearn sessions
ADMIN_SESSIONS Review one weak concept and then use this guide again.
"""


@pytest.mark.parametrize("course", [GUIDE], indirect=True, ids=["study-guide"])
def test_first_plan_selects_subject_sections_across_the_guide(source_course):
    topic, _ = source_course
    context = sources.snapshot(topic, "Continue to the next useful concept.", sources.APPROVED_MODEL, opted_in=True)
    assert "ADMIN_" not in context.prompt
    for title, _ in TOPICS:
        assert title in context.prompt
    assert context.subject_sections
    sources.ensure_unchanged(topic, context, sources.APPROVED_MODEL)


@pytest.mark.parametrize("course", [GUIDE], indirect=True, ids=["study-guide"])
def test_one_source_policy_per_provider_request(source_course):
    topic, _ = source_course
    context = sources.snapshot(topic, "Continue to the next useful concept.", sources.APPROVED_MODEL, opted_in=True)
    for prompt in (sources.tutor_prompt(context, context.model_metadata), sources.judge_prompt(context), source_lessons.planning_preview(context)):
        assert prompt.count(sources.SOURCE_POLICY) == 1


@pytest.mark.parametrize("course", [GUIDE], indirect=True, ids=["study-guide"])
def test_administrative_plan_items_are_not_taught(source_course):
    topic, record = source_course
    context = sources.snapshot(topic, "Continue to the next useful concept.", sources.APPROVED_MODEL, opted_in=True)
    concepts = [
        {"label": "Exam Scope Determination", "objective": "Recognize the controlling exam document.", "source_ids": [record.source_id]},
        {"label": "Euclid's algorithm", "objective": "Trace repeated remainders to find a gcd.", "source_ids": [record.source_id]},
        {"label": "Source Limitations Awareness", "objective": "Recognize the app's provenance limits.", "source_ids": [record.source_id]},
    ]
    with mock.patch.object(cli, "call_openai", return_value=json.dumps({"concepts": concepts})):
        plan = source_lessons.generate_plan(topic, context, sources.APPROVED_MODEL)
    assert [c["label"] for c in plan["concepts"]] == ["Euclid's algorithm"]


@pytest.mark.parametrize("pending", [False, True])
def test_later_continue_trims_optional_context_before_budget_failure(source_course, pending):
    topic, record = source_course
    metadata = dict(topic.metadata)
    source_lessons.apply_plan(metadata, large_plan(record.source_id))
    metadata.update(current_unit=2, slide_coverage={"1:1": ["Algorithm concept 1"], "2:1": ["Algorithm concept 2"]})
    cli.write_text_atomic(topic.path, cli.format_topic(metadata, topic.body))
    for _ in range(2):
        cli.append_session(cli.read_topic(topic.slug), "lesson", "Continue", "**Lesson:**\n" + "Algorithm concept uses useful states and required steps.\n" * 24)
    if pending:
        cli.save_pending_question(cli.read_topic(topic.slug), "**Check:**\nAfter pushing A then B, which stack item is popped first?\nA) A\nB) B", "B")
    topic = cli.read_topic(topic.slug)
    context = sources.snapshot(topic, "Continue to the next useful concept.", sources.APPROVED_MODEL, opted_in=True)
    assert context.excerpts
    assert sources.request_size_bound(context) <= sources.PROMPT_CHAR_LIMIT
    sources.ensure_unchanged(topic, context, sources.APPROVED_MODEL)
    if pending:
        assert context.model_metadata["pending_question"]["question"] == topic.metadata["pending_question"]["question"]
        assert context.model_metadata["pending_question"]["answer_key"] == "B"


@pytest.mark.parametrize("course", [GUIDE], indirect=True, ids=["study-guide"])
def test_later_navigation_does_not_reintroduce_neighboring_admin_lines(source_course):
    topic, record = source_course
    metadata = dict(topic.metadata)
    source_lessons.apply_plan(metadata, {"schema": 1, "concepts": [
        {"label": "Euclid", "objective": "Trace greatest common divisor remainders.", "source_ids": [record.source_id]},
    ]})
    cli.write_text_atomic(topic.path, cli.format_topic(metadata, topic.body))
    context = sources.snapshot(cli.read_topic(topic.slug), "Continue to the next useful concept.", sources.APPROVED_MODEL, opted_in=True)
    assert "ADMIN_" not in context.prompt
    assert "Euclid" in context.prompt


def test_plain_and_procedural_sources_are_not_discarded():
    for text in ("A stack removes its most recently added item first.\n", "# Study workflow\nPractice retrieval.\n## Using this guide\nRead each section.\n"):
        assert source_lessons.subject_sections(text.splitlines(keepends=True)) == ([], set())


@pytest.mark.parametrize("course", [GUIDE.replace("ADMIN_SCOPE Consult the controlling document for the exam scope.", "Excluded by the instructor: topological sort and strongly connected components.")], indirect=True, ids=["instructor-rules"])
def test_subject_selection_retains_instructor_constraints(source_course):
    topic, record = source_course
    for planned in (False, True):
        if planned:
            metadata = dict(topic.metadata)
            source_lessons.apply_plan(metadata, {"schema": 1, "concepts": [
                {"label": "Euclid", "objective": "Trace greatest common divisor remainders.", "source_ids": [record.source_id]},
            ]})
            cli.write_text_atomic(topic.path, cli.format_topic(metadata, topic.body))
            topic = cli.read_topic(topic.slug)
        context = sources.snapshot(topic, "Continue to the next useful concept.", sources.APPROVED_MODEL, opted_in=True)
        assert "Excluded by the instructor: topological sort" in context.prompt
        assert "Untrusted course constraint (not a teaching topic)" in context.prompt
        assert "ADMIN_" not in context.prompt
        assert "Euclid" in context.prompt
        if not planned:
            assert TOPICS[-1][0] in context.prompt
            assert sum(title in context.prompt for title, _ in TOPICS) >= 6
        assert len(context.excerpts) <= sources.EXCERPT_LIMIT
        assert sources.request_size_bound(context) <= sources.PROMPT_CHAR_LIMIT
        sources.ensure_unchanged(topic, context, sources.APPROVED_MODEL)


@pytest.mark.parametrize("course", [GUIDE + ("\nAdditional algorithm exercises and worked traces.\n" * 500)], indirect=True, ids=["full-length-guide"])
@pytest.mark.skipif(os.environ.get("OPENLEARN_BROWSER_TEST") != "1", reason="requires installed browser")
def test_full_guide_third_continue_in_browser(source_course):
    import socket
    import threading
    import time

    import uvicorn
    from playwright.sync_api import expect, sync_playwright
    from openlearn.web.app import create_app

    topic, record = source_course
    concepts = large_plan(record.source_id)["concepts"]
    for concept, (title, _) in zip(concepts, TOPICS):
        concept["label"] = title
    responses = [
        "**Lesson:**\nEuclid repeatedly replaces the larger input with a remainder.\n\nFor example, 30 mod 12 is 6; then 12 mod 6 is 0, so the gcd is 6.\n<!-- covered: Input metrics and Euclid -->",
        "**Lesson:**\nA linear algorithm grows proportionally to its input size.\n\nFor example, doubling an array's length doubles the work of visiting every entry once.\n<!-- covered: Growth rates -->",
        "**Check:**\nInsertion sort keeps a sorted prefix. After inserting 2 into the sorted prefix [1, 4], write the new sorted prefix.",
    ]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    app = create_app(testing=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.02)
    assert server.started
    origin = f"http://127.0.0.1:{port}"
    base = origin + app.state.security.url_namespace
    try:
        with mock.patch.object(cli, "call_openai", return_value=json.dumps({"concepts": concepts})) as planner, mock.patch.object(cli, "call_openai_streaming", side_effect=responses), sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{origin}/?access_token={app.state.security.access_token}")
            page.goto(f"{base}/courses/{topic.slug}")
            for number, fragment in enumerate(("30 mod 12", "doubling an array", "new sorted prefix")):
                page.get_by_role("button", name="Continue", exact=True).click()
                if number == 0:
                    page.locator("[data-source-send]").click()
                expect(page.locator("[data-current-move]")).to_contain_text(fragment, timeout=15000)
                expect(page.locator("[data-source-preview]")).not_to_be_visible()
                assert "too large" not in page.locator("body").inner_text()
                page.reload()
                expect(page.locator("[data-current-move]")).to_contain_text(fragment)
            assert planner.call_count == 1
            assert "ADMIN_" not in planner.call_args.args[1]
            assert not errors
            assert isinstance(cli.read_topic(topic.slug).metadata.get("pending_question"), dict)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize("course", [GUIDE], indirect=True, ids=["rebuild"])
def test_rebuilding_existing_plan_preserves_source_and_history(source_course):
    from tests.test_source_lessons import submit
    from tests.test_source_context import web_request
    from openlearn.web.services import OpenLearnWebServices
    topic, record = source_course
    source_path = cli.topic_context_dir(topic.slug) / record.context_file
    source_before = source_path.read_bytes()
    metadata = dict(topic.metadata)
    source_lessons.apply_plan(metadata, {"schema": 1, "concepts": [
        {"label": "Exam Scope Determination", "objective": "Recognize the study document.", "source_ids": [record.source_id]},
    ]})
    cli.write_text_atomic(topic.path, cli.format_topic(metadata, topic.body))
    cli.append_session(cli.read_topic(topic.slug), "lesson", "Continue", "**Lesson:** Saved earlier study instructions.")
    old_body = cli.read_topic(topic.slug).body
    cli.update_state_atomic(topic.slug, lambda state: state.pop(source_lessons.PLAN_KEY, None))
    # Legacy fixture metadata is in Markdown; a real web plan is dynamic state.
    metadata, body = cli.parse_topic(topic.path.read_text())
    metadata.pop(source_lessons.PLAN_KEY, None)
    cli.write_text_atomic(topic.path, cli.format_topic(metadata, body))
    payload = {"concepts": [{"label": "Euclid", "objective": "Trace gcd remainders.", "source_ids": [record.source_id]}]}
    response = "**Lesson:** Euclid computes a greatest common divisor by repeated remainders.\n\nFor example, 12 mod 8 is 4, then 8 mod 4 is zero, so the gcd is 4.\n<!-- covered: Euclid -->"
    with mock.patch.object(cli, "call_openai", return_value=json.dumps(payload)), mock.patch.object(cli, "call_openai_streaming", return_value=response):
        result = submit(OpenLearnWebServices(), web_request(cli.read_topic(topic.slug), intent="next", text=""), topic.slug)
    assert result.status == "committed", result.error_message
    saved = cli.read_topic(topic.slug)
    assert old_body.strip() in saved.body
    assert source_path.read_bytes() == source_before
    assert source_lessons.plan_concepts(saved.metadata)[0]["label"] == "Euclid"
    assert saved.metadata["current_unit"] == 1


@pytest.mark.parametrize("course", [
    "\n\n# Algorithms review\n## Exam scope\nConsult the instructor.\n## Binary search\nBinary search halves a sorted interval.\n## Dynamic programming\nReuse stored subproblem results.\n",
    "# Binary search\nBinary search halves a sorted interval.\n# Dynamic programming\nReuse stored subproblem results.\n",
], indirect=True, ids=["leading-blanks", "titleless-peer-subjects"])
def test_subject_selection_distinguishes_document_titles_from_first_subject(source_course):
    topic, _ = source_course
    context = sources.snapshot(topic, "Continue to the next useful concept.", sources.APPROVED_MODEL, opted_in=True)
    assert "Binary search halves a sorted interval." in context.prompt
    assert "Reuse stored subproblem results." in context.prompt
    assert context.subject_sections


@pytest.mark.parametrize("rule", [
    "Excluded topics: topological sort and strongly connected components.",
    "Excluded by the instructor:\n- topological sort\n- strongly connected components",
])
def test_complete_exclusion_rules_survive_subject_filtering(source_course, rule):
    topic, record = source_course
    text = GUIDE.replace("ADMIN_SCOPE Consult the controlling document for the exam scope.", rule)
    # Use a fresh registered import so all normal checksum checks still apply.
    from openlearn import source_imports
    original = cli.project_home() / "complete-guide.md"
    original.write_text(text)
    source_imports.import_course_source(source_imports.CourseSourceImportRequest(topic.slug, source_imports.LocalFileSource(original)))
    context = sources.snapshot(cli.read_topic(topic.slug), "Continue to the next useful concept.", sources.APPROVED_MODEL, opted_in=True)
    assert rule in context.prompt
    matching = [excerpt for excerpt in context.excerpts if rule in excerpt.text]
    assert len(matching) == 1
    assert matching[0].text.strip() == rule
    assert "Euclid" in context.prompt
    assert len(context.excerpts) <= sources.EXCERPT_LIMIT
    sources.ensure_unchanged(cli.read_topic(topic.slug), context, sources.APPROVED_MODEL)
