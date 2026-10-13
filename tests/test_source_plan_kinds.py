"""Imported folder and repository identities survive the source lesson workflow."""

import json
import os
from unittest import mock

import pytest

from openlearn import cli, source_context, source_imports, source_lessons
from openlearn.web.services import OpenLearnWebServices
from tests.test_source_context import web_request
from tests.test_source_lessons import lesson, plan, submit
from tests.test_source_lessons import test_source_lesson_browser_journey as source_browser_journey

pytest_plugins = ["tests.test_source_lessons"]


@pytest.fixture(params=["file", "folder", "github"])
def imported_course(source_course, tmp_path, request):
    topic, original = source_course
    metadata, body = cli.parse_topic(topic.path.read_text())
    metadata["course_sources"] = []
    topic.path.write_text(cli.format_topic(metadata, body))
    for name in (original.context_file, original.summary_file):
        (cli.topic_context_dir(topic.slug) / name).unlink()
    folder = tmp_path / "synthetic-sources"
    folder.mkdir()
    text = "Stacks remove the last item pushed first.\nPushing adds an item to the top.\n"
    notes = folder / "stacks.md"
    notes.write_text(text)

    def clone(_command, destination, _env):
        destination.mkdir(parents=True)
        (destination / "stacks.md").write_text(text)

    source = {
        "file": source_imports.LocalFileSource(notes),
        "folder": source_imports.LocalFolderSource(folder),
        "github": source_imports.PublicGitHubSource("https://github.com/example/learning"),
    }[request.param]
    with mock.patch.object(cli, "_bounded_git_clone", side_effect=clone):
        imported = source_imports.import_course_source(source_imports.CourseSourceImportRequest(
            topic.slug, source, source_context.APPROVED_MODEL,
        ))
    assert len(imported.sources) == 1
    record = imported.sources[0]
    assert record.kind == request.param
    return cli.read_topic(topic.slug), record


@pytest.mark.parametrize("source_form", ["canonical", "checksum"])
def test_first_and_later_source_lessons_accept_imported_identities(imported_course, source_form):
    topic, record = imported_course
    service = OpenLearnWebServices()
    payload = json.loads(plan(record))
    if source_form == "checksum":
        for concept in payload["concepts"]:
            concept["source_ids"] = [record.checksum]
    with mock.patch.object(cli, "call_openai", return_value=json.dumps(payload)) as planner, mock.patch.object(
        cli, "call_openai_streaming", side_effect=[lesson(), lesson("Stack operations")],
    ):
        first = submit(service, web_request(topic, intent="next", text=""), topic.slug)
        assert first.status == "committed", first.error_message
        assert record.source_id in planner.call_args.args[1]
        saved = cli.read_topic(topic.slug)
        assert source_lessons.plan_concepts(saved.metadata)[0]["source_ids"] == [record.source_id]
        second = submit(service, web_request(saved, intent="next", text=""), topic.slug)
    assert second.status == "committed", second.error_message
    planner.assert_called_once()
    saved = cli.read_topic(topic.slug)
    assert saved.metadata["current_unit"] == 2
    assert saved.metadata["slide_coverage"] == {"1:1": ["Stack order"], "2:1": ["Stack operations"]}


def test_unselected_source_is_still_rejected_atomically(imported_course):
    topic, record = imported_course
    payload = json.loads(plan(record))
    payload["concepts"][0]["source_ids"] = [record.kind + ":" + "0" * 16]
    before = topic.path.read_bytes()
    with mock.patch.object(cli, "call_openai", return_value=json.dumps(payload)), mock.patch.object(
        cli, "call_openai_streaming",
    ) as tutor:
        result = submit(OpenLearnWebServices(), web_request(topic, intent="next", text=""), topic.slug)
    assert result.status == "retryable_error"
    assert result.error_code == "source_plan_invalid"
    assert "unrecognized source" in result.error_message
    assert topic.path.read_bytes() == before
    assert cli.load_pending_learner_prompt(topic.slug)
    tutor.assert_not_called()


@pytest.mark.parametrize("identity", [
    "folder:" + "a" * 15,
    "github:" + "a" * 65,
    "folder:" + "G" * 16,
    "unsupported:" + "a" * 16,
])
def test_invalid_source_identity_is_rejected_even_if_listed(identity):
    concepts = [{"label": "Stack order", "objective": "Explain stack order.", "source_ids": [identity]}]
    with pytest.raises(source_lessons.PlanValidationError, match="Invalid source concept plan"):
        source_lessons.validate_concepts(concepts, {identity})


def test_legacy_url_source_plan_remains_readable():
    identity = "url:" + "a" * 16
    concepts = [{"label": "Stack order", "objective": "Explain stack order.", "source_ids": [identity]}]
    assert source_lessons.plan_concepts({"source_learning_plan": {"schema": 1, "concepts": concepts}}) == concepts


@pytest.mark.skipif(os.environ.get("OPENLEARN_BROWSER_TEST") != "1", reason="opt-in browser lane")
def test_imported_source_browser_journey(imported_course):
    source_browser_journey(imported_course, "next", "")


def test_missing_source_does_not_block_usable_imports(imported_course):
    topic, record = imported_course
    metadata, body = cli.parse_topic(topic.path.read_text())
    missing = dict(metadata["course_sources"][0])
    missing.update(context_file="missing-source.txt", source_id="file:" + "0" * 16, checksum="0" * 16)
    metadata["course_sources"].append(missing)
    topic.path.write_text(cli.format_topic(metadata, body))
    topic = cli.read_topic(topic.slug)
    context = source_context.snapshot(
        topic, "Continue to the next useful concept.", source_context.APPROVED_MODEL, opted_in=True,
    )
    assert record.source_id in context.ledger
    assert "missing, unsafe or over budget" in context.ledger
    assert missing["source_id"] not in context.ledger
    with mock.patch.object(cli, "call_openai", return_value=plan(record)), mock.patch.object(
        cli, "call_openai_streaming", return_value=lesson(),
    ):
        result = submit(OpenLearnWebServices(), web_request(topic, intent="next", text=""), topic.slug)
    assert result.status == "committed", result.error_message
