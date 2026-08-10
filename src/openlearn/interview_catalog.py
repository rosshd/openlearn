"""Versioned, rights-aware interview problem catalog.

Packaged data is immutable application content. Private learner entries are
loaded only from an explicit local directory and never merged into package data.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.resources
import inspect
import json
import os
import re
import stat
import sys
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from importlib.resources.abc import Traversable
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from openlearn.interview_skills import (
    InterviewSkillGraph,
    SkillGraphError,
    load_default_graph,
)

PROBLEM_ID_PATTERN = re.compile(
    r"(?:problem|external)(?:\.[a-z0-9]+(?:-[a-z0-9]+)*)+"
)
CHECKSUM_PATTERN = re.compile(r"sha256:[a-f0-9]{64}")
DIFFICULTIES = ("foundation", "intermediate", "advanced")
SOURCE_TYPES = ("packaged", "official_link")
RIGHTS_BASES = (
    "openlearn_original",
    "owned_or_licensed",
    "open_license",
    "official_link_only",
)
OPEN_LICENSES = frozenset(
    {
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC-BY-4.0",
        "CC0-1.0",
        "MIT",
    }
)
SKILL_ROLES = ("primary", "supporting")
TEST_VISIBILITIES = ("public", "hidden")
MAX_PRIVATE_ENTRY_BYTES = 256_000
OFFICIAL_SOURCE_HOSTS = MappingProxyType({"leetcode": "leetcode.com"})
OFFICIAL_PROBLEM_URLS = MappingProxyType(
    {
        ("leetcode", "external.leetcode.two-sum"): (
            "https://leetcode.com/problems/two-sum/"
        )
    }
)


class CatalogValidationError(ValueError):
    """Catalog data is unsafe, inconsistent, or incomplete."""


@dataclass(frozen=True)
class ProblemSource:
    source_type: Literal["packaged", "official_link"]
    provider: str
    rights_basis: str
    url: str
    license: str
    attribution: str
    permission: str


@dataclass(frozen=True)
class ProblemSkill:
    skill_id: str
    role: Literal["primary", "supporting"]


@dataclass(frozen=True)
class ProblemTest:
    arguments: tuple[object, ...]
    keyword_arguments: Mapping[str, object]
    expected: object


@dataclass(frozen=True)
class LanguageDefinition:
    interface_kind: str
    function: str
    parameters: tuple[str, ...]
    starter_code: str
    reference: str | None
    public_tests: tuple[ProblemTest, ...]
    hidden_tests: tuple[ProblemTest, ...]


@dataclass(frozen=True)
class FollowUp:
    follow_up_id: str
    prompt: str
    problem_id: str


@dataclass(frozen=True)
class CatalogProblem:
    problem_id: str
    revision: int
    introduced_catalog_revision: int
    title: str
    delivery: str
    evidence_eligible: bool
    transfer_family: str | None
    statement: str | None
    source: ProblemSource
    languages: Mapping[str, LanguageDefinition]
    difficulty: str
    expected_minutes: int
    skills: tuple[ProblemSkill, ...]
    prerequisites: tuple[str, ...]
    near_duplicate_exclusions: tuple[str, ...]
    constraints: tuple[str, ...]
    examples: tuple[Mapping[str, object], ...]
    edge_families: tuple[str, ...]
    references: tuple[str, ...]
    complexity: Mapping[str, str]
    misconceptions: tuple[str, ...]
    hints: tuple[str, ...]
    follow_ups: tuple[FollowUp, ...]
    checksum: str

    @property
    def primary_skill_ids(self) -> tuple[str, ...]:
        return tuple(ref.skill_id for ref in self.skills if ref.role == "primary")


@dataclass(frozen=True)
class ProblemCatalog:
    catalog_id: str
    catalog_revision: int
    graph_id: str
    graph_version: str
    mastery_policy_version: str
    problems: tuple[CatalogProblem, ...]
    checksum: str

    def problem(self, problem_id: str, revision: int | None = None) -> CatalogProblem:
        matches = [
            problem
            for problem in self.problems
            if problem.problem_id == problem_id
            and (revision is None or problem.revision == revision)
        ]
        if matches:
            return max(matches, key=lambda problem: problem.revision)
        suffix = "" if revision is None else f" at revision {revision}"
        raise CatalogValidationError(f"unknown catalog problem: {problem_id}{suffix}")


@dataclass(frozen=True)
class ReferenceTestResult:
    problem_id: str
    problem_revision: int
    language: str
    visibility: Literal["public", "hidden"]
    case_index: int
    passed: bool


@dataclass(frozen=True)
class SimilarityReviewFlag:
    problem_ids: tuple[str, str]
    score: float


def catalog_resource() -> Traversable:
    return importlib.resources.files("openlearn").joinpath(
        "interview_problem_catalogs", "openlearn-interview-v1.json"
    )


def load_catalog_dict() -> dict[str, object]:
    try:
        value = json.loads(catalog_resource().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogValidationError("bundled interview catalog is unreadable") from exc
    if not isinstance(value, dict):
        raise CatalogValidationError("interview catalog must be a JSON object")
    return value


def load_default_catalog() -> ProblemCatalog:
    return validate_catalog(load_catalog_dict())


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CatalogValidationError("catalog values must be finite JSON data") from exc


def _deep_freeze(value: object) -> object:
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise CatalogValidationError("catalog JSON object keys must be text")
        return MappingProxyType(
            {key: _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value


def problem_checksum(raw: Mapping[str, object]) -> str:
    payload = dict(raw)
    payload.pop("checksum", None)
    return "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()


def catalog_checksum(raw: Mapping[str, object]) -> str:
    payload = dict(raw)
    payload.pop("catalog_checksum", None)
    return "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()


def _text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise CatalogValidationError(f"{label} must be trimmed text")
    if not value and not allow_empty:
        raise CatalogValidationError(f"{label} must be non-empty text")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CatalogValidationError(f"{label} must be a positive integer")
    return value


def _code(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogValidationError(f"{label} must be non-empty text")
    if "\x00" in value:
        raise CatalogValidationError(f"{label} contains a null byte")
    return value


def _text_tuple(value: object, label: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CatalogValidationError(f"{label} must be a list")
    result = tuple(_text(item, label) for item in value)
    if nonempty and not result:
        raise CatalogValidationError(f"{label} must not be empty")
    if len(result) != len(set(result)):
        raise CatalogValidationError(f"{label} must not contain duplicates")
    return result


def _validate_source(raw: object, delivery: str, problem_id: str) -> ProblemSource:
    fields = {
        "type",
        "provider",
        "rights_basis",
        "url",
        "license",
        "attribution",
        "permission",
    }
    if not isinstance(raw, dict) or set(raw) != fields:
        raise CatalogValidationError("problem source fields are invalid")
    source_type = raw.get("type")
    provider = _text(raw.get("provider"), "source provider")
    rights_basis = raw.get("rights_basis")
    if source_type not in SOURCE_TYPES or source_type != delivery:
        raise CatalogValidationError("problem source type does not match delivery")
    if rights_basis not in RIGHTS_BASES:
        raise CatalogValidationError("problem rights basis is unsupported")
    if source_type == "official_link" and rights_basis != "official_link_only":
        raise CatalogValidationError("official links require official_link_only rights")
    if source_type == "packaged" and rights_basis == "official_link_only":
        raise CatalogValidationError("packaged content requires redistribution rights")
    url = _canonical_https_url(raw.get("url"), "source url")
    if source_type == "official_link":
        expected_host = OFFICIAL_SOURCE_HOSTS.get(provider)
        parsed = urllib.parse.urlsplit(url)
        if expected_host is None or parsed.hostname != expected_host:
            raise CatalogValidationError(
                "source url host does not match official provider"
            )
        if OFFICIAL_PROBLEM_URLS.get((provider, problem_id)) != url:
            raise CatalogValidationError(
                "source url does not match official problem identity"
            )
    license_name = _text(raw.get("license"), "source license")
    if rights_basis == "openlearn_original" and license_name != "AGPL-3.0-or-later":
        raise CatalogValidationError("openlearn-original source license is invalid")
    if rights_basis == "open_license" and license_name not in OPEN_LICENSES:
        raise CatalogValidationError("open-license source requires a supported SPDX license")
    return ProblemSource(
        source_type=source_type,
        provider=provider,
        rights_basis=rights_basis,
        url=url,
        license=license_name,
        attribution=_text(raw.get("attribution"), "source attribution"),
        permission=_text(raw.get("permission"), "source permission"),
    )


def _canonical_https_url(value: object, label: str) -> str:
    url = _text(value, label)
    if not url.isascii() or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in url
    ):
        raise CatalogValidationError(f"{label} must be single-line and control-free")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise CatalogValidationError(f"{label} is malformed") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or port is not None
    ):
        raise CatalogValidationError(
            f"{label} requires HTTPS host without credentials, query, port, or fragment"
        )
    path = parsed.path or "/"
    lowered_path = path.lower()
    if any(encoded in lowered_path for encoded in ("%00", "%0a", "%0d")):
        raise CatalogValidationError(f"{label} contains encoded control data")
    canonical = urllib.parse.urlunsplit(("https", parsed.hostname, path, "", ""))
    if url != canonical:
        raise CatalogValidationError(f"{label} is not canonical")
    return canonical


def _validate_test(raw: object, label: str) -> ProblemTest:
    if not isinstance(raw, dict) or set(raw) != {"args", "kwargs", "expected"}:
        raise CatalogValidationError(f"{label} fields are invalid")
    args = raw.get("args")
    kwargs = raw.get("kwargs")
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise CatalogValidationError(f"{label} args and kwargs are invalid")
    _canonical(raw)
    return ProblemTest(
        cast(tuple[object, ...], _deep_freeze(args)),
        cast(Mapping[str, object], _deep_freeze(kwargs)),
        _deep_freeze(raw.get("expected")),
    )


def _validate_language(raw: object, label: str, delivery: str) -> LanguageDefinition:
    required = {"interface", "starter_code", "reference", "tests"}
    if not isinstance(raw, dict) or set(raw) != required:
        raise CatalogValidationError(f"{label} fields are invalid")
    interface = raw.get("interface")
    if not isinstance(interface, dict) or set(interface) != {
        "kind",
        "function",
        "parameters",
    }:
        raise CatalogValidationError(f"{label} interface fields are invalid")
    if interface.get("kind") != "function":
        raise CatalogValidationError(f"{label} interface kind is unsupported")
    function = _text(interface.get("function"), f"{label} function")
    if not function.isidentifier():
        raise CatalogValidationError(f"{label} function must be an identifier")
    parameters = _text_tuple(
        interface.get("parameters"), f"{label} interface parameters"
    )
    if not all(parameter.isidentifier() for parameter in parameters):
        raise CatalogValidationError(f"{label} parameters must be identifiers")
    starter_code = _code(raw.get("starter_code"), f"{label} starter code")
    try:
        parsed_starter = ast.parse(starter_code)
    except SyntaxError as exc:
        raise CatalogValidationError(f"{label} starter code is invalid") from exc
    functions = [node for node in parsed_starter.body if isinstance(node, ast.FunctionDef)]
    if (
        len(parsed_starter.body) != 1
        or len(functions) != 1
        or functions[0].name != function
        or tuple(argument.arg for argument in functions[0].args.args) != parameters
        or functions[0].args.vararg is not None
        or functions[0].args.kwarg is not None
        or functions[0].args.posonlyargs
        or functions[0].args.kwonlyargs
        or functions[0].args.defaults
        or functions[0].args.kw_defaults
        or functions[0].decorator_list
        or functions[0].returns is not None
        or any(argument.annotation is not None for argument in functions[0].args.args)
        or len(functions[0].body) != 1
        or not isinstance(functions[0].body[0], ast.Pass)
    ):
        raise CatalogValidationError(f"{label} starter code must be an inert interface")
    reference = raw.get("reference")
    tests = raw.get("tests")
    if not isinstance(tests, dict) or set(tests) != set(TEST_VISIBILITIES):
        raise CatalogValidationError(f"{label} tests fields are invalid")
    public = tuple(
        _validate_test(case, f"{label} public test")
        for case in _test_list(tests.get("public"), f"{label} public tests")
    )
    hidden = tuple(
        _validate_test(case, f"{label} hidden test")
        for case in _test_list(tests.get("hidden"), f"{label} hidden tests")
    )
    if delivery == "packaged":
        if not isinstance(reference, str) or not reference.startswith(
            "openlearn.interview_problem_references:"
        ):
            raise CatalogValidationError(f"{label} reference is not trusted package code")
        if not public or not hidden:
            raise CatalogValidationError(f"{label} requires public and hidden tests")
    elif reference is not None or public or hidden:
        raise CatalogValidationError("official link entries cannot package tests or references")
    return LanguageDefinition(
        interface_kind="function",
        function=function,
        parameters=parameters,
        starter_code=starter_code,
        reference=reference,
        public_tests=public,
        hidden_tests=hidden,
    )


def _test_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise CatalogValidationError(f"{label} must be a list")
    return value


def _validate_pair_sum_contract(problem: CatalogProblem) -> None:
    if problem.problem_id != "problem.pair-sum-sorted":
        return
    definition = problem.languages["python"]
    for case in (*definition.public_tests, *definition.hidden_tests):
        if len(case.arguments) != 2 or case.keyword_arguments:
            raise CatalogValidationError("pair-sum tests must pass values and target")
        values, target = case.arguments
        if not isinstance(values, tuple) or not isinstance(target, int):
            raise CatalogValidationError("pair-sum test inputs are invalid")
        pairs = [
            (left, right)
            for left in range(len(values))
            for right in range(left + 1, len(values))
            if isinstance(values[left], int)
            and isinstance(values[right], int)
            and values[left] + values[right] == target
        ]
        expected = tuple(pairs[0]) if len(pairs) == 1 else (-1, -1)
        if len(pairs) > 1:
            raise CatalogValidationError(
                "pair-sum exact-output tests require a unique pair"
            )
        if case.expected != expected:
            raise CatalogValidationError("pair-sum expected output is invalid")
    for example in problem.examples:
        example_input = example.get("input")
        if not isinstance(example_input, Mapping):
            raise CatalogValidationError("pair-sum example input is invalid")
        values = example_input.get("values")
        target = example_input.get("target")
        if not isinstance(values, tuple) or not isinstance(target, int):
            raise CatalogValidationError("pair-sum example input is invalid")
        matches = sum(
            1
            for left in range(len(values))
            for right in range(left + 1, len(values))
            if isinstance(values[left], int)
            and isinstance(values[right], int)
            and values[left] + values[right] == target
        )
        if matches != 1:
            raise CatalogValidationError("pair-sum examples require a unique pair")
        expected_output = example.get("output")
        actual_pair = next(
            (
                (left, right)
                for left in range(len(values))
                for right in range(left + 1, len(values))
                if isinstance(values[left], int)
                and isinstance(values[right], int)
                and values[left] + values[right] == target
            ),
            None,
        )
        if expected_output != actual_pair:
            raise CatalogValidationError("pair-sum example output is invalid")


def validate_catalog(
    raw: Mapping[str, object],
    *,
    graph: InterviewSkillGraph | None = None,
    validate_references: bool = True,
) -> ProblemCatalog:
    fields = {
        "schema_version",
        "catalog_id",
        "catalog_revision",
        "graph_id",
        "graph_version",
        "mastery_policy_version",
        "problems",
        "catalog_checksum",
    }
    if set(raw) != fields or raw.get("schema_version") != 1:
        raise CatalogValidationError("catalog fields or schema version are invalid")
    graph = graph or load_default_graph()
    graph_id = _text(raw.get("graph_id"), "graph_id")
    graph_version = _text(raw.get("graph_version"), "graph_version")
    mastery_policy_version = _text(
        raw.get("mastery_policy_version"), "mastery_policy_version"
    )
    if (graph_id, graph_version, mastery_policy_version) != (
        graph.graph_id,
        graph.graph_version,
        graph.mastery_policy_version,
    ):
        raise CatalogValidationError("catalog references an unavailable skill graph")
    values = raw.get("problems")
    if not isinstance(values, list) or not values:
        raise CatalogValidationError("catalog problems must be a non-empty list")
    problems: list[CatalogProblem] = []
    ids: set[str] = set()
    revisions: set[tuple[str, int]] = set()
    raw_by_revision: dict[tuple[str, int], dict[str, object]] = {}
    problem_fields = {
        "id",
        "revision",
        "introduced_catalog_revision",
        "title",
        "delivery",
        "evidence_eligible",
        "transfer_family",
        "statement",
        "source",
        "languages",
        "difficulty",
        "expected_minutes",
        "skills",
        "prerequisites",
        "near_duplicate_exclusions",
        "constraints",
        "examples",
        "edge_families",
        "references",
        "complexity",
        "misconceptions",
        "hints",
        "follow_ups",
        "checksum",
    }
    known_skills = {skill.skill_id for skill in graph.skills}
    for value in values:
        if not isinstance(value, dict) or set(value) != problem_fields:
            raise CatalogValidationError("problem fields are invalid")
        problem_id = _text(value.get("id"), "problem id")
        if not PROBLEM_ID_PATTERN.fullmatch(problem_id):
            raise CatalogValidationError(f"invalid problem id: {problem_id}")
        revision = _positive_int(value.get("revision"), f"{problem_id} revision")
        introduced_catalog_revision = _positive_int(
            value.get("introduced_catalog_revision"),
            f"{problem_id} introduced_catalog_revision",
        )
        raw_catalog_revision = raw.get("catalog_revision")
        if (
            isinstance(raw_catalog_revision, bool)
            or not isinstance(raw_catalog_revision, int)
            or introduced_catalog_revision > raw_catalog_revision
        ):
            raise CatalogValidationError(
                f"{problem_id} introduced catalog revision is unavailable"
            )
        revision_key = (problem_id, revision)
        if revision_key in revisions:
            raise CatalogValidationError(
                f"duplicate problem revision: {problem_id}@{revision}"
            )
        revisions.add(revision_key)
        ids.add(problem_id)
        raw_by_revision[revision_key] = value
        delivery = value.get("delivery")
        if delivery not in SOURCE_TYPES:
            raise CatalogValidationError(f"{problem_id} delivery is invalid")
        evidence_eligible = value.get("evidence_eligible")
        if not isinstance(evidence_eligible, bool):
            raise CatalogValidationError(f"{problem_id} evidence_eligible must be boolean")
        if evidence_eligible != (delivery == "packaged"):
            raise CatalogValidationError(
                f"{problem_id} evidence eligibility conflicts with delivery"
            )
        transfer_family_value = value.get("transfer_family")
        transfer_family = (
            _text(transfer_family_value, f"{problem_id} transfer_family")
            if evidence_eligible
            else None
        )
        if not evidence_eligible and transfer_family_value is not None:
            raise CatalogValidationError(
                "official link transfer_family must be null and evidence-ineligible"
            )
        source = _validate_source(value.get("source"), delivery, problem_id)
        statement = value.get("statement")
        if delivery == "packaged":
            statement = _text(statement, f"{problem_id} statement")
        elif statement is not None:
            raise CatalogValidationError("official link entries cannot package statements")
        languages_value = value.get("languages")
        if not isinstance(languages_value, dict) or set(languages_value) != {"python"}:
            raise CatalogValidationError(f"{problem_id} languages are invalid")
        languages = {
            language: _validate_language(definition, language, delivery)
            for language, definition in languages_value.items()
        }
        skills_value = value.get("skills")
        if not isinstance(skills_value, list) or not skills_value:
            raise CatalogValidationError(f"{problem_id} skills must be non-empty")
        skills: list[ProblemSkill] = []
        seen_skills: set[str] = set()
        for ref in skills_value:
            if not isinstance(ref, dict) or set(ref) != {"skill_id", "role"}:
                raise CatalogValidationError(f"{problem_id} skill fields are invalid")
            skill_id = _text(ref.get("skill_id"), "skill id")
            role = ref.get("role")
            if skill_id not in known_skills:
                raise CatalogValidationError(f"{problem_id} references unknown skill")
            if role not in SKILL_ROLES or skill_id in seen_skills:
                raise CatalogValidationError(f"{problem_id} has an invalid skill reference")
            seen_skills.add(skill_id)
            skills.append(ProblemSkill(skill_id, role))
        if not any(ref.role == "primary" for ref in skills):
            raise CatalogValidationError(f"{problem_id} requires a primary skill")
        if evidence_eligible:
            try:
                graph_problem = graph.problem(problem_id)
            except SkillGraphError as exc:
                raise CatalogValidationError(
                    f"{problem_id} is absent from the pinned graph"
                ) from exc
            expected_skills = tuple(
                (ref.skill_id, ref.role) for ref in graph_problem.skills
            )
            actual_skills = tuple((ref.skill_id, ref.role) for ref in skills)
            if (
                transfer_family != graph_problem.transfer_family
                or actual_skills != expected_skills
            ):
                raise CatalogValidationError(
                    f"{problem_id} does not match pinned graph evidence contract"
                )
        examples_value = value.get("examples")
        if not isinstance(examples_value, list):
            raise CatalogValidationError(f"{problem_id} examples must be a list")
        examples: list[Mapping[str, object]] = []
        for example in examples_value:
            if not isinstance(example, dict) or set(example) != {
                "input",
                "output",
                "explanation",
            }:
                raise CatalogValidationError(f"{problem_id} example fields are invalid")
            _canonical(example)
            examples.append(cast(Mapping[str, object], _deep_freeze(example)))
        if delivery == "packaged" and not examples:
            raise CatalogValidationError(f"{problem_id} requires an original example")
        if delivery == "official_link" and examples:
            raise CatalogValidationError("official link entries cannot package examples")
        complexity = value.get("complexity")
        if delivery == "official_link":
            if complexity != {}:
                raise CatalogValidationError(
                    "official link entries cannot package complexity guidance"
                )
            normalized_complexity: dict[str, str] = {}
        else:
            if not isinstance(complexity, dict) or set(complexity) != {"time", "space"}:
                raise CatalogValidationError(f"{problem_id} complexity fields are invalid")
            normalized_complexity = {
                name: _text(expression, f"{problem_id} {name} complexity")
                for name, expression in complexity.items()
            }
            if not all(
                expression.startswith("O(") and expression.endswith(")")
                for expression in normalized_complexity.values()
            ):
                raise CatalogValidationError(
                    f"{problem_id} complexity must use O(...) notation"
                )
        follow_ups_value = value.get("follow_ups")
        if not isinstance(follow_ups_value, list):
            raise CatalogValidationError(f"{problem_id} follow_ups must be a list")
        follow_ups: list[FollowUp] = []
        seen_follow_ups: set[str] = set()
        for follow_up in follow_ups_value:
            if not isinstance(follow_up, dict) or set(follow_up) != {
                "id",
                "prompt",
                "problem_id",
            }:
                raise CatalogValidationError(f"{problem_id} follow-up fields are invalid")
            follow_up_id = _text(follow_up.get("id"), "follow-up id")
            if follow_up_id in seen_follow_ups:
                raise CatalogValidationError(f"{problem_id} has duplicate follow-up IDs")
            seen_follow_ups.add(follow_up_id)
            follow_ups.append(
                FollowUp(
                    follow_up_id,
                    _text(follow_up.get("prompt"), "follow-up prompt"),
                    _text(follow_up.get("problem_id"), "follow-up problem_id"),
                )
            )
        checksum = _text(value.get("checksum"), f"{problem_id} checksum")
        if not CHECKSUM_PATTERN.fullmatch(checksum):
            raise CatalogValidationError(f"{problem_id} checksum format is invalid")
        difficulty = value.get("difficulty")
        if difficulty not in DIFFICULTIES:
            raise CatalogValidationError(f"{problem_id} difficulty is invalid")
        constraints = _text_tuple(
            value.get("constraints"),
            f"{problem_id} constraints",
            nonempty=delivery == "packaged",
        )
        references = _text_tuple(
            value.get("references"),
            f"{problem_id} references",
            nonempty=delivery == "packaged",
        )
        misconceptions = _text_tuple(
            value.get("misconceptions"),
            f"{problem_id} misconceptions",
            nonempty=delivery == "packaged",
        )
        hints = _text_tuple(
            value.get("hints"),
            f"{problem_id} hints",
            nonempty=delivery == "packaged",
        )
        if delivery == "packaged" and not follow_ups:
            raise CatalogValidationError(f"{problem_id} follow_ups must not be empty")
        if delivery == "official_link" and any(
            (
                constraints,
                examples,
                value.get("edge_families"),
                references,
                misconceptions,
                hints,
                follow_ups,
            )
        ):
            raise CatalogValidationError(
                "official link entries cannot package protected content fields"
            )
        problems.append(
            CatalogProblem(
                problem_id=problem_id,
                revision=revision,
                introduced_catalog_revision=introduced_catalog_revision,
                title=_text(value.get("title"), f"{problem_id} title"),
                delivery=delivery,
                evidence_eligible=evidence_eligible,
                transfer_family=transfer_family,
                statement=statement,
                source=source,
                languages=MappingProxyType(languages),
                difficulty=difficulty,
                expected_minutes=_positive_int(
                    value.get("expected_minutes"), f"{problem_id} expected_minutes"
                ),
                skills=tuple(skills),
                prerequisites=_text_tuple(
                    value.get("prerequisites"), f"{problem_id} prerequisites"
                ),
                near_duplicate_exclusions=_text_tuple(
                    value.get("near_duplicate_exclusions"),
                    f"{problem_id} near_duplicate_exclusions",
                ),
                constraints=constraints,
                examples=tuple(examples),
                edge_families=_text_tuple(
                    value.get("edge_families"),
                    f"{problem_id} edge_families",
                    nonempty=delivery == "packaged",
                ),
                references=references,
                complexity=MappingProxyType(normalized_complexity),
                misconceptions=misconceptions,
                hints=hints,
                follow_ups=tuple(follow_ups),
                checksum=checksum,
            )
        )
    revisions_by_id: dict[str, list[CatalogProblem]] = {}
    for problem in problems:
        revisions_by_id.setdefault(problem.problem_id, []).append(problem)
    for problem_id, history in revisions_by_id.items():
        ordered = sorted(history, key=lambda problem: problem.revision)
        if [problem.revision for problem in ordered] != list(
            range(1, len(ordered) + 1)
        ) or any(
            current.introduced_catalog_revision
            >= following.introduced_catalog_revision
            for current, following in zip(ordered, ordered[1:])
        ):
            raise CatalogValidationError(
                f"{problem_id} revision chronology is invalid"
            )
    for problem in problems:
        _validate_pair_sum_contract(problem)
        for target in (*problem.prerequisites, *problem.near_duplicate_exclusions):
            if target not in ids or target == problem.problem_id:
                raise CatalogValidationError(
                    f"{problem.problem_id} has an invalid problem link"
                )
        for follow_up in problem.follow_ups:
            if follow_up.problem_id not in ids:
                raise CatalogValidationError(
                    f"{problem.problem_id} has an invalid follow-up link"
                )
        if problem.checksum != problem_checksum(
            raw_by_revision[(problem.problem_id, problem.revision)]
        ):
            raise CatalogValidationError(
                f"{problem.problem_id} checksum does not match content"
            )
    checksum = _text(raw.get("catalog_checksum"), "catalog checksum")
    if not CHECKSUM_PATTERN.fullmatch(checksum) or checksum != catalog_checksum(raw):
        raise CatalogValidationError("catalog checksum does not match content")
    catalog = ProblemCatalog(
        catalog_id=_text(raw.get("catalog_id"), "catalog_id"),
        catalog_revision=_positive_int(raw.get("catalog_revision"), "catalog_revision"),
        graph_id=graph_id,
        graph_version=graph_version,
        mastery_policy_version=mastery_policy_version,
        problems=tuple(problems),
        checksum=checksum,
    )
    if validate_references:
        validate_reference_implementations(catalog)
    return catalog


def _reference_callable(reference: str):
    module_name, separator, name = reference.partition(":")
    if separator != ":" or module_name != "openlearn.interview_problem_references":
        raise CatalogValidationError("reference is outside trusted package code")
    module = importlib.import_module(module_name)
    function = getattr(module, name, None)
    if not callable(function):
        raise CatalogValidationError(f"reference function is unavailable: {reference}")
    return function


def validate_reference_implementations(
    catalog: ProblemCatalog,
) -> tuple[ReferenceTestResult, ...]:
    results: list[ReferenceTestResult] = []
    for problem in catalog.problems:
        for language, definition in problem.languages.items():
            if definition.reference is None:
                continue
            function = _reference_callable(definition.reference)
            signature = inspect.signature(function)
            if tuple(signature.parameters) != definition.parameters:
                raise CatalogValidationError(
                    f"{problem.problem_id} reference does not match its interface"
                )
            for visibility, cases in (
                ("public", definition.public_tests),
                ("hidden", definition.hidden_tests),
            ):
                for index, case in enumerate(cases):
                    try:
                        actual = function(
                            *cast(tuple[object, ...], _deep_thaw(case.arguments)),
                            **cast(dict[str, object], _deep_thaw(case.keyword_arguments)),
                        )
                    except Exception as exc:
                        raise CatalogValidationError(
                            f"{problem.problem_id} reference raised in "
                            f"{visibility} test {index}"
                        ) from exc
                    if _deep_freeze(actual) != case.expected:
                        raise CatalogValidationError(
                            f"{problem.problem_id} reference failed "
                            f"{visibility} test {index}"
                        )
                    results.append(
                        ReferenceTestResult(
                            problem.problem_id,
                            problem.revision,
                            language,
                            cast(Literal["public", "hidden"], visibility),
                            index,
                            True,
                        )
                    )
    return tuple(results)


def attempt_problem_reference(
    catalog: ProblemCatalog, problem: CatalogProblem
) -> dict[str, object]:
    if catalog.problem(problem.problem_id, problem.revision) is not problem:
        raise CatalogValidationError("attempt problem is not from this catalog")
    return {
        "catalog_id": catalog.catalog_id,
        "catalog_revision": catalog.catalog_revision,
        "problem_id": problem.problem_id,
        "problem_revision": problem.revision,
        "problem_checksum": problem.checksum,
    }


def resolve_attempt_problem(
    catalog: ProblemCatalog, reference: Mapping[str, object]
) -> CatalogProblem:
    required = {
        "catalog_id",
        "catalog_revision",
        "problem_id",
        "problem_revision",
        "problem_checksum",
    }
    if set(reference) != required or reference.get("catalog_id") != catalog.catalog_id:
        raise CatalogValidationError("attempt catalog reference is invalid")
    recorded_catalog_revision = reference.get("catalog_revision")
    if (
        isinstance(recorded_catalog_revision, bool)
        or not isinstance(recorded_catalog_revision, int)
        or not 1 <= recorded_catalog_revision <= catalog.catalog_revision
    ):
        raise CatalogValidationError("attempt catalog revision is unavailable")
    problem_id = reference.get("problem_id")
    revision = reference.get("problem_revision")
    if not isinstance(problem_id, str) or isinstance(revision, bool) or not isinstance(
        revision, int
    ):
        raise CatalogValidationError("attempt problem revision is invalid")
    problem = catalog.problem(problem_id, revision)
    if recorded_catalog_revision < problem.introduced_catalog_revision:
        raise CatalogValidationError(
            "attempt predates the problem revision in this catalog"
        )
    if reference.get("problem_checksum") != problem.checksum:
        raise CatalogValidationError("attempt problem checksum does not match")
    return problem


def evidence_problem_reference(
    catalog: ProblemCatalog, problem: CatalogProblem
) -> dict[str, object]:
    if not problem.evidence_eligible or problem.transfer_family is None:
        raise CatalogValidationError("problem is ineligible for mastery evidence")
    exact = catalog.problem(problem.problem_id, problem.revision)
    if exact is not problem:
        raise CatalogValidationError("evidence problem is not from this catalog")
    return {
        "graph_id": catalog.graph_id,
        "graph_version": catalog.graph_version,
        "mastery_policy_version": catalog.mastery_policy_version,
        "problem_id": problem.problem_id,
        "problem_revision": problem.revision,
        "problem_checksum": problem.checksum,
        "transfer_family": problem.transfer_family,
        "skills": tuple(
            {"skill_id": ref.skill_id, "role": ref.role} for ref in problem.skills
        ),
    }


def similarity_review_flags(
    catalog: ProblemCatalog, *, threshold: float = 0.86
) -> tuple[SimilarityReviewFlag, ...]:
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("similarity threshold must be between zero and one")
    flags: list[SimilarityReviewFlag] = []
    packaged = [
        problem
        for problem in catalog.problems
        if problem.source.source_type == "packaged" and problem.statement is not None
    ]
    for index, left in enumerate(packaged):
        for right in packaged[index + 1 :]:
            if left.problem_id == right.problem_id:
                continue
            if (
                right.problem_id in left.near_duplicate_exclusions
                or left.problem_id in right.near_duplicate_exclusions
            ):
                continue
            left_statement = left.statement
            right_statement = right.statement
            if left_statement is None or right_statement is None:
                continue
            score = SequenceMatcher(
                None, left_statement.lower(), right_statement.lower()
            ).ratio()
            if score >= threshold:
                first_id = min(left.problem_id, right.problem_id)
                second_id = max(left.problem_id, right.problem_id)
                flags.append(
                    SimilarityReviewFlag(
                        (first_id, second_id),
                        round(score, 4),
                    )
                )
    return tuple(flags)


def create_official_link_workspace(
    problem: CatalogProblem,
    destination: Path,
    *,
    learner_confirmed: bool,
) -> Path:
    if not learner_confirmed:
        raise PermissionError("explicit learner confirmation is required")
    if problem.delivery != "official_link" or problem.source.source_type != "official_link":
        raise CatalogValidationError("workspace link-out requires an official-link problem")
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination.mkdir(mode=0o700, parents=True)
    language = problem.languages["python"]
    readme = (
        f"# {problem.title}\n\n"
        "Open the official page to read the problem and submit there:\n\n"
        f"{problem.source.url}\n\n"
        "This workspace intentionally contains no external prompt, examples, or tests.\n"
    )
    files = {
        "README.md": readme,
        "solution.py": language.starter_code.rstrip() + "\n",
        "problem-reference.json": json.dumps(
            {
                "problem_id": problem.problem_id,
                "problem_revision": problem.revision,
                "problem_checksum": problem.checksum,
                "official_url": problem.source.url,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    }
    for name, content in files.items():
        path = destination / name
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    return destination


def _decode_private_entry(descriptor: int, name: str) -> dict[str, object]:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise CatalogValidationError("private entry must be a regular file")
    if metadata.st_size > MAX_PRIVATE_ENTRY_BYTES:
        raise CatalogValidationError("private catalog entry is too large")
    chunks: list[bytes] = []
    remaining = MAX_PRIVATE_ENTRY_BYTES + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > MAX_PRIVATE_ENTRY_BYTES:
        raise CatalogValidationError("private catalog entry is too large")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogValidationError(f"private entry is unreadable: {name}") from exc
    if not isinstance(value, dict):
        raise CatalogValidationError(f"private entry must be an object: {name}")
    return value


def _verified_private_open(
    name: str | Path,
    *,
    opener: Callable[[str | Path, int], int],
    lstat_entry: Callable[[str | Path], os.stat_result],
    nofollow_flag: int,
) -> dict[str, object]:
    before = lstat_entry(name)
    if stat.S_ISLNK(before.st_mode):
        raise CatalogValidationError("private catalog entry must not be a symlink")
    if not stat.S_ISREG(before.st_mode):
        raise CatalogValidationError("private entry must be a regular file")
    descriptor = -1
    try:
        descriptor = opener(name, os.O_RDONLY | nofollow_flag)
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise CatalogValidationError("private catalog entry changed during open")
        return _decode_private_entry(descriptor, Path(name).name)
    except CatalogValidationError:
        raise
    except OSError as exc:
        raise CatalogValidationError("private catalog entry is unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_private_entry_path(
    path: Path,
    *,
    opener: Callable[[Path, int], int] | None = None,
    nofollow_flag: int | None = None,
) -> dict[str, object]:
    path = Path(path)
    flag = getattr(os, "O_NOFOLLOW", 0) if nofollow_flag is None else nofollow_flag
    if opener is not None:
        return _verified_private_open(
            path,
            opener=lambda candidate, flags: opener(Path(candidate), flags),
            lstat_entry=lambda candidate: os.lstat(candidate),
            nofollow_flag=flag,
        )
    if os.name == "nt":
        return _verified_private_open(
            path,
            opener=os.open,
            lstat_entry=os.lstat,
            nofollow_flag=flag,
        )
    if (
        not hasattr(os, "O_DIRECTORY")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
    ):
        raise CatalogValidationError(
            "stable private no-follow reads are unsupported on this platform"
        )
    parent_descriptor = -1
    try:
        parent_before = os.lstat(path.parent)
        if stat.S_ISLNK(parent_before.st_mode) or not stat.S_ISDIR(
            parent_before.st_mode
        ):
            raise CatalogValidationError("private catalog parent is unsafe")
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0),
        )
        parent_opened = os.fstat(parent_descriptor)
        if (parent_before.st_dev, parent_before.st_ino) != (
            parent_opened.st_dev,
            parent_opened.st_ino,
        ):
            raise CatalogValidationError("private catalog parent changed during open")
        return _verified_private_open(
            path.name,
            opener=lambda name, flags: os.open(
                name, flags, dir_fd=parent_descriptor
            ),
            lstat_entry=lambda name: os.stat(
                name, dir_fd=parent_descriptor, follow_symlinks=False
            ),
            nofollow_flag=flag,
        )
    except CatalogValidationError:
        raise
    except OSError as exc:
        raise CatalogValidationError("private catalog entry is unsafe") from exc
    finally:
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def read_private_entry(path: Path) -> dict[str, object]:
    return _read_private_entry_path(path)


def _private_entry_names(names: Iterable[object]) -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name in names
            if isinstance(name, str)
            and name.endswith(".json")
            and Path(name).name == name
        )
    )


def load_private_entries(
    directory: Path,
    *,
    _list_directory: Callable[[int], list[str]] | None = None,
    _platform: str | None = None,
) -> tuple[dict[str, object], ...]:
    directory = Path(directory)
    try:
        directory_before = os.lstat(directory)
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise CatalogValidationError("private catalog directory is unsafe") from exc
    if stat.S_ISLNK(directory_before.st_mode) or not stat.S_ISDIR(
        directory_before.st_mode
    ):
        raise CatalogValidationError("private catalog path must be a real directory")
    if (_platform or os.name) == "nt":
        try:
            names = os.listdir(directory)
            entries = tuple(
                _read_private_entry_path(directory / name)
                for name in _private_entry_names(names)
            )
            after = os.lstat(directory)
        except CatalogValidationError:
            raise
        except OSError as exc:
            raise CatalogValidationError("private catalog directory is unsafe") from exc
        if (directory_before.st_dev, directory_before.st_ino) != (
            after.st_dev,
            after.st_ino,
        ):
            raise CatalogValidationError("private catalog directory changed during read")
        return entries
    if (
        not hasattr(os, "O_DIRECTORY")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.listdir not in os.supports_fd
    ):
        raise CatalogValidationError(
            "stable private directory reads are unsupported on this platform"
        )
    directory_descriptor = -1
    try:
        directory_descriptor = os.open(
            directory,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_directory = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(opened_directory.st_mode)
            or (directory_before.st_dev, directory_before.st_ino)
            != (opened_directory.st_dev, opened_directory.st_ino)
        ):
            raise CatalogValidationError("private catalog directory changed during open")
        names = (_list_directory or os.listdir)(directory_descriptor)
        entries = [
            _verified_private_open(
                name,
                opener=lambda candidate, flags: os.open(
                    candidate, flags, dir_fd=directory_descriptor
                ),
                lstat_entry=lambda candidate: os.stat(
                    candidate,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                ),
                nofollow_flag=getattr(os, "O_NOFOLLOW", 0),
            )
            for name in _private_entry_names(names)
        ]
        return tuple(entries)
    except CatalogValidationError:
        raise
    except OSError as exc:
        raise CatalogValidationError("private catalog directory is unsafe") from exc
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _main(arguments: list[str]) -> int:
    if arguments not in ([], ["validate"]):
        print("usage: python -m openlearn.interview_catalog [validate]", file=sys.stderr)
        return 2
    catalog = load_default_catalog()
    flags = similarity_review_flags(catalog)
    print(
        f"validated {len(catalog.problems)} problems at catalog revision "
        f"{catalog.catalog_revision}; {len(flags)} similarity review flag(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
