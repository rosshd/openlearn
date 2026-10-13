"""Source plans survive the real provider formatting boundary without live calls."""

import io
import json
from unittest import mock

import pytest

from openlearn import cli, source_lessons, tutor_service
from openlearn.text import sanitize_json_output
from openlearn.web.services import OpenLearnWebServices
from tests.test_source_context import web_request
from tests.test_source_lessons import lesson, plan, submit

pytest_plugins = ["tests.test_source_lessons"]


@pytest.mark.parametrize("value", [
    "Compare A: linear search and B: binary search.",
    "A) first option; B) second option",
    "Literal <analysis>XML example</analysis> and <!-- answer: A -->",
    "Action: Explain a JSON value without rewriting it.",
    "Quotes: \" and escaped newlines:\n\u03bb",
])
def test_structured_output_preserves_json_values(value):
    raw = json.dumps({"value": value}, ensure_ascii=False, indent=2)
    assert sanitize_json_output(raw) == raw
    assert json.loads(sanitize_json_output(raw)) == {"value": value}


def test_unknown_plan_error_reason_cannot_expose_exception_text():
    error = source_lessons.SourcePlanError("PRIVATE_PROVIDER_CONTENT", reason="PRIVATE_REASON")
    code, message = tutor_service._turn_failure(error)
    assert code == "source_plan_invalid"
    assert "PRIVATE" not in message


@pytest.mark.parametrize("failure", ["extra_shape", "private_shape", "prefix", "filename", "json"])
def test_plan_diagnostics_survive_reload_without_private_values(source_course, failure):
    topic, record = source_course
    payload = json.loads(plan(record))
    expected = {"plan_top_type": "object", "plan_top_fields": ["concepts"], "plan_unknown_field_count": 0}
    if failure == "extra_shape":
        payload["schema"] = 1
        expected.update(plan_reason="shape", plan_top_fields=["concepts", "schema"])
    elif failure == "private_shape":
        payload = {"PRIVATE_FIELD": "PRIVATE_VALUE"}
        expected.update(plan_reason="shape", plan_top_fields=[], plan_unknown_field_count=1)
    elif failure == "json":
        expected = {"plan_reason": "json", "plan_json_line": 1, "plan_json_column": 1}
    else:
        identity = "folder:" + record.checksum if failure == "prefix" else "PRIVATE_FILE.txt"
        payload["concepts"][0]["source_ids"] = [identity]
        expected.update(
            plan_reason="source", plan_field="source_ids", plan_concept_index=1,
            plan_source_index=1, plan_source_count=1, plan_known_source_count=1,
            plan_source_length=len(identity), plan_source_prefix="folder" if failure == "prefix" else "other",
            plan_source_form="identity" if failure == "prefix" else "filename",
            plan_source_issue="unselected" if failure == "prefix" else "malformed",
            plan_checksum_match_count=1 if failure == "prefix" else 0,
        )
    raw = "PRIVATE_RAW_RESPONSE" if failure == "json" else json.dumps(payload)
    request = web_request(topic, intent="next", text="")
    before = topic.path.read_bytes()
    with mock.patch.object(cli, "call_openai", return_value=raw), mock.patch.object(cli, "call_openai_streaming") as tutor:
        result = submit(OpenLearnWebServices(), request, topic.slug)
    assert result.status == "retryable_error"
    assert result.error_code == "source_plan_invalid"
    internal = cli.load_state(topic.slug)["_openlearn_internal"]
    assert internal["turn_results"][request.submission_id]["error_diagnostics"] == expected
    assert internal["last_turn_error"]["diagnostics"] == expected
    cli.clear_config_cache()
    assert "error_diagnostics" not in tutor_service._receipt_dict(tutor_service.operation_status(topic.slug, request.submission_id))
    assert "PRIVATE" not in cli.topic_state_path(topic.slug).read_text()
    assert topic.path.read_bytes() == before
    tutor.assert_not_called()


def test_plan_diagnostics_filter_untrusted_mutations():
    error = source_lessons.SourcePlanError("PRIVATE", reason="source", diagnostics={
        "plan_concept_index": 1, "plan_source_issue": "unselected",
    })
    error.diagnostics.update(raw="PRIVATE", plan_top_type="PRIVATE", plan_source_index=True,
                             plan_top_fields=["concepts", "PRIVATE", {"PRIVATE": "PRIVATE"}])
    assert tutor_service._turn_error_diagnostics(error) == {
        "plan_reason": "source", "plan_source_issue": "unselected",
        "plan_concept_index": 1, "plan_top_fields": ["concepts"],
    }


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "<analysis>PRIVATE_REASONING</analysis>\n{}"])
def test_actual_provider_json_commits_source_lesson(source_course, monkeypatch, wrapper):
    topic, record = source_course
    payload = json.loads(plan(record))
    payload["concepts"][0]["objective"] = "Compare A: linear search and B: binary search."
    raw = wrapper.format(json.dumps(payload, indent=2))
    response = json.dumps({"choices": [{"message": {"content": raw}}]}).encode()
    monkeypatch.setenv("OPENLEARN_MOCK", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    with mock.patch.object(cli, "urlopen", return_value=io.BytesIO(response)) as network, mock.patch.object(cli, "call_openai_streaming", return_value=lesson()):
        result = submit(OpenLearnWebServices(), web_request(topic, intent="next", text=""), topic.slug)
    assert result.status == "committed", result.error_message
    network.assert_called_once()
    assert json.loads(network.call_args.args[0].data)["response_format"] == {"type": "json_object"}
    saved = cli.read_topic(topic.slug)
    assert saved.metadata["source_learning_plan"]["concepts"] == payload["concepts"]
    assert "PRIVATE_REASONING" not in saved.path.read_text()


@pytest.mark.parametrize("failure,fragment", [
    ("json", "not valid JSON"),
    ("shape", "unexpected structure"),
    ("fields", "missing or unexpected fields"),
    ("length", "too long"),
    ("format", "unsupported formatting"),
    ("source", "unrecognized source"),
    ("screened", "did not pass source screening"),
])
def test_safe_plan_reason_survives_reload(source_course, failure, fragment):
    topic, record = source_course
    payload = json.loads(plan(record))
    concept = payload["concepts"][0]
    if failure == "fields":
        concept["PRIVATE_EXTRA_FIELD"] = "PRIVATE_PROVIDER_CONTENT"
    elif failure == "length":
        concept["objective"] = "x" * 241
    elif failure == "format":
        concept["objective"] = "Explain `stack` order."
    elif failure == "source":
        concept["source_ids"] = ["file:" + "0" * 16]
    elif failure == "screened":
        concept["objective"] = "email: private@example.com"
    raw = "PRIVATE_PROVIDER_CONTENT" if failure == "json" else json.dumps(
        {"PRIVATE_PROVIDER_CONTENT": payload} if failure == "shape" else payload,
    )
    before = topic.path.read_bytes()
    request = web_request(topic, intent="next", text="")
    with mock.patch.object(cli, "call_openai", return_value=raw), mock.patch.object(cli, "call_openai_streaming") as tutor:
        result = submit(OpenLearnWebServices(), request, topic.slug)
    assert result.status == "retryable_error"
    assert result.error_code == "source_plan_invalid"
    assert fragment in result.error_message
    cli.clear_config_cache()
    assert tutor_service.operation_status(topic.slug, request.submission_id).error_message == result.error_message
    assert "PRIVATE" not in result.error_message and "private@example.com" not in result.error_message
    assert topic.path.read_bytes() == before
    assert cli.load_pending_learner_prompt(topic.slug)
    assert tutor_service.course_revision(topic.slug) == 0
    tutor.assert_not_called()
