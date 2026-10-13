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
