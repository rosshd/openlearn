from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from openlearn import cli


REGRESSION_DIR = Path(__file__).resolve().parent / "regressions"


def load_regressions() -> list[tuple[str, dict[str, Any]]]:
    cases: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(REGRESSION_DIR.glob("*.json")):
        cases.append((path.name, json.loads(path.read_text(encoding="utf-8"))))
    return cases


class TutorRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.previous_env = {
            name: os.environ.get(name)
            for name in (
                "OPENLEARN_HOME",
                "OPENLEARN_MOCK",
                "OPENLEARN_MODEL",
                "OPENLEARN_BASE_URL",
                "OPENAI_API_KEY",
            )
        }
        os.environ["OPENLEARN_HOME"] = self.home.name
        os.environ["OPENLEARN_MOCK"] = "1"
        os.environ.pop("OPENLEARN_MODEL", None)
        os.environ.pop("OPENLEARN_BASE_URL", None)
        os.environ.pop("OPENAI_API_KEY", None)
        cli._CONFIG_CACHE = None

    def tearDown(self) -> None:
        for name, value in self.previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        cli._CONFIG_CACHE = None
        self.home.cleanup()

    def test_regression_files_exist(self) -> None:
        self.assertGreater(len(load_regressions()), 0)


def replay_regression(testcase: unittest.TestCase, case: dict[str, Any]) -> None:
    name = required_string(case, "name")
    topic_slug = str(case.get("topic") or name)
    goal = str(case.get("goal") or f"Regression test for {name}")
    turns = case.get("turns")
    requirements = case.get("requirements")
    if not isinstance(turns, list) or not turns:
        testcase.fail(f"{name}: turns must be a non-empty list")
    if not isinstance(requirements, list) or not requirements:
        testcase.fail(f"{name}: requirements must be a non-empty list")

    cli.cmd_new(Namespace(topic=topic_slug, goal=goal))
    output: list[str] = []
    last_user_message = ""
    last_response = ""

    for turn in turns:
        if not isinstance(turn, dict):
            testcase.fail(f"{name}: each turn must be an object")
        role = turn.get("role")
        content = turn.get("content")
        if role == "user":
            if not isinstance(content, str):
                testcase.fail(f"{name}: user turn content must be a string")
            last_user_message = content
        elif role == "assistant":
            if content is not None:
                last_response = str(content)
                output.append(last_response)
                continue
            last_response = run_turn(topic_slug, last_user_message, output)
        else:
            testcase.fail(f"{name}: unsupported turn role {role!r}")

    transcript = "\n".join(output)
    for requirement in requirements:
        if not isinstance(requirement, str):
            testcase.fail(f"{name}: requirement must be a string")
        assert_requirement(testcase, name, requirement, transcript)

    assert_state(testcase, name, topic_slug, case.get("state_assertions", {}))


def run_turn(topic_slug: str, message: str, output: list[str]) -> str:
    if not message.strip():
        # Blank REPL input is handled before ask_topic(); this regression checks
        # that conversation replay preserves the next prompt instead of crashing.
        output.append("openlearn> ")
        return "openlearn> "
    if message.startswith("/"):
        start = len(output)
        try:
            cli.handle_repl_command(message[1:], output_func=output.append)
        except cli.OpenLearnError as exc:
            output.append(str(exc))
        output.append("openlearn> ")
        return "\n".join(output[start:])
    response = cli.ask_topic(topic_slug, message, None, output_func=output.append)
    output.append("openlearn")
    return response


def required_string(case: dict[str, Any], key: str) -> str:
    value = case.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AssertionError(f"regression file missing required string field {key!r}")
    return value


def assert_requirement(
    testcase: unittest.TestCase, name: str, requirement: str, transcript: str
) -> None:
    lowered = transcript.casefold()
    prefix = "tutor response must contain "
    negative_prefix = "tutor response must not contain "
    requirement_lower = requirement.casefold()
    if requirement_lower.startswith(negative_prefix):
        expected = requirement[len(negative_prefix) :].strip().casefold()
        testcase.assertNotIn(expected, lowered, f"{name}: {requirement}")
    elif requirement_lower.startswith(prefix):
        expected = requirement[len(prefix) :].strip().casefold()
        testcase.assertIn(expected, lowered, f"{name}: {requirement}")
    else:
        testcase.fail(
            f"{name}: unsupported requirement {requirement!r}; use "
            "'tutor response must contain X' or 'tutor response must not contain Y'"
        )


def assert_state(
    testcase: unittest.TestCase,
    name: str,
    topic_slug: str,
    assertions: object,
) -> None:
    if assertions in (None, {}):
        return
    if not isinstance(assertions, dict):
        testcase.fail(f"{name}: state_assertions must be an object")
    metadata = cli.read_topic(topic_slug).metadata
    for key, expected in assertions.items():
        if key.endswith("_type"):
            metadata_key = key.removesuffix("_type")
            testcase.assertEqual(
                type(metadata.get(metadata_key)).__name__,
                expected,
                f"{name}: metadata field {metadata_key!r} type",
            )
        else:
            testcase.assertEqual(metadata.get(key), expected, f"{name}: metadata field {key!r}")


def make_regression_test(case: dict[str, Any]):
    def test(self: TutorRegressionTests) -> None:
        replay_regression(self, case)

    test.__name__ = f"test_{case.get('name', 'regression')}"
    return test


for _filename, _case in load_regressions():
    setattr(
        TutorRegressionTests,
        f"test_{_case.get('name', _filename.removesuffix('.json'))}",
        make_regression_test(_case),
    )
