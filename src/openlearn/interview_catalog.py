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
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from importlib.resources.abc import Traversable
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from openlearn.interview_skills import InterviewSkillGraph, load_default_graph

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


class CatalogValidationError(ValueError):
    """Catalog data is unsafe, inconsistent, or incomplete."""


@dataclass(frozen=True)
class ProblemSource:
    source_type: Literal["packaged", "official_link"]
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
    title: str
    delivery: str
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
    problems: tuple[CatalogProblem, ...]
    checksum: str

    def problem(self, problem_id: str, revision: int | None = None) -> CatalogProblem:
        for problem in self.problems:
            if problem.problem_id == problem_id and (
                revision is None or problem.revision == revision
            ):
                return problem
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


def _validate_source(raw: object, delivery: str) -> ProblemSource:
    fields = {"type", "rights_basis", "url", "license", "attribution", "permission"}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise CatalogValidationError("problem source fields are invalid")
    source_type = raw.get("type")
    rights_basis = raw.get("rights_basis")
    if source_type not in SOURCE_TYPES or source_type != delivery:
        raise CatalogValidationError("problem source type does not match delivery")
    if rights_basis not in RIGHTS_BASES:
        raise CatalogValidationError("problem rights basis is unsupported")
    if source_type == "official_link" and rights_basis != "official_link_only":
        raise CatalogValidationError("official links require official_link_only rights")
    if source_type == "packaged" and rights_basis == "official_link_only":
        raise CatalogValidationError("packaged content requires redistribution rights")
    url = _text(raw.get("url"), "source url")
    if not url.startswith("https://"):
        raise CatalogValidationError("source url must use https")
    license_name = _text(raw.get("license"), "source license")
    if rights_basis == "openlearn_original" and license_name != "AGPL-3.0-or-later":
        raise CatalogValidationError("openlearn-original source license is invalid")
    if rights_basis == "open_license" and license_name not in OPEN_LICENSES:
        raise CatalogValidationError("open-license source requires a supported SPDX license")
    return ProblemSource(
        source_type=source_type,
        rights_basis=rights_basis,
        url=url,
        license=license_name,
        attribution=_text(raw.get("attribution"), "source attribution"),
        permission=_text(raw.get("permission"), "source permission"),
    )


def _validate_test(raw: object, label: str) -> ProblemTest:
    if not isinstance(raw, dict) or set(raw) != {"args", "kwargs", "expected"}:
        raise CatalogValidationError(f"{label} fields are invalid")
    args = raw.get("args")
    kwargs = raw.get("kwargs")
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise CatalogValidationError(f"{label} args and kwargs are invalid")
    _canonical(raw)
    return ProblemTest(tuple(args), MappingProxyType(dict(kwargs)), raw.get("expected"))


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
    functions = [
        node
        for node in parsed_starter.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if (
        len(functions) != 1
        or functions[0].name != function
        or tuple(argument.arg for argument in functions[0].args.args) != parameters
        or functions[0].args.vararg is not None
        or functions[0].args.kwarg is not None
    ):
        raise CatalogValidationError(
            f"{label} starter code does not match its interface"
        )
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
        "problems",
        "catalog_checksum",
    }
    if set(raw) != fields or raw.get("schema_version") != 1:
        raise CatalogValidationError("catalog fields or schema version are invalid")
    graph = graph or load_default_graph()
    graph_id = _text(raw.get("graph_id"), "graph_id")
    graph_version = _text(raw.get("graph_version"), "graph_version")
    if (graph_id, graph_version) != (graph.graph_id, graph.graph_version):
        raise CatalogValidationError("catalog references an unavailable skill graph")
    values = raw.get("problems")
    if not isinstance(values, list) or not values:
        raise CatalogValidationError("catalog problems must be a non-empty list")
    problems: list[CatalogProblem] = []
    ids: set[str] = set()
    raw_by_id: dict[str, dict[str, object]] = {}
    problem_fields = {
        "id",
        "revision",
        "title",
        "delivery",
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
        if problem_id in ids:
            raise CatalogValidationError(f"duplicate problem id: {problem_id}")
        ids.add(problem_id)
        raw_by_id[problem_id] = value
        delivery = value.get("delivery")
        if delivery not in SOURCE_TYPES:
            raise CatalogValidationError(f"{problem_id} delivery is invalid")
        source = _validate_source(value.get("source"), delivery)
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
            examples.append(MappingProxyType(dict(example)))
        if delivery == "packaged" and not examples:
            raise CatalogValidationError(f"{problem_id} requires an original example")
        if delivery == "official_link" and examples:
            raise CatalogValidationError("official link entries cannot package examples")
        complexity = value.get("complexity")
        if not isinstance(complexity, dict) or set(complexity) != {"time", "space"}:
            raise CatalogValidationError(f"{problem_id} complexity fields are invalid")
        normalized_complexity = {
            name: _text(expression, f"{problem_id} {name} complexity")
            for name, expression in complexity.items()
        }
        if delivery == "packaged" and not all(
            expression.startswith("O(") and expression.endswith(")")
            for expression in normalized_complexity.values()
        ):
            raise CatalogValidationError(f"{problem_id} complexity must use O(...) notation")
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
        problems.append(
            CatalogProblem(
                problem_id=problem_id,
                revision=_positive_int(value.get("revision"), f"{problem_id} revision"),
                title=_text(value.get("title"), f"{problem_id} title"),
                delivery=delivery,
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
                constraints=_text_tuple(
                    value.get("constraints"), f"{problem_id} constraints"
                ),
                examples=tuple(examples),
                edge_families=_text_tuple(
                    value.get("edge_families"),
                    f"{problem_id} edge_families",
                    nonempty=delivery == "packaged",
                ),
                references=_text_tuple(
                    value.get("references"), f"{problem_id} references"
                ),
                complexity=MappingProxyType(normalized_complexity),
                misconceptions=_text_tuple(
                    value.get("misconceptions"), f"{problem_id} misconceptions"
                ),
                hints=_text_tuple(value.get("hints"), f"{problem_id} hints"),
                follow_ups=tuple(follow_ups),
                checksum=checksum,
            )
        )
    for problem in problems:
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
        if problem.checksum != problem_checksum(raw_by_id[problem.problem_id]):
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
                            *case.arguments, **dict(case.keyword_arguments)
                        )
                    except Exception as exc:
                        raise CatalogValidationError(
                            f"{problem.problem_id} reference raised in "
                            f"{visibility} test {index}"
                        ) from exc
                    if actual != case.expected:
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


def load_private_entries(directory: Path) -> tuple[dict[str, object], ...]:
    directory = Path(directory)
    if not directory.exists():
        return ()
    if not directory.is_dir() or directory.is_symlink():
        raise CatalogValidationError("private catalog path must be a real directory")
    entries: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink() or path.stat().st_size > MAX_PRIVATE_ENTRY_BYTES:
            raise CatalogValidationError("private catalog entry is unsafe or too large")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CatalogValidationError(f"private entry is unreadable: {path.name}") from exc
        if not isinstance(value, dict):
            raise CatalogValidationError(f"private entry must be an object: {path.name}")
        entries.append(value)
    return tuple(entries)


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
