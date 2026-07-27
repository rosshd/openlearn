from __future__ import annotations

import copy
import json
import stat
from pathlib import Path

import pytest

from openlearn import interview_catalog


def _catalog() -> dict[str, object]:
    return interview_catalog.load_catalog_dict()


def _problem(
    catalog: dict[str, object], problem_id: str = "problem.pair-sum-sorted"
) -> dict[str, object]:
    problems = catalog["problems"]
    assert isinstance(problems, list)
    return next(item for item in problems if item["id"] == problem_id)


def test_packaged_catalog_is_valid_and_covers_sources_patterns_and_difficulty() -> None:
    catalog = interview_catalog.load_default_catalog()

    assert {problem.source.source_type for problem in catalog.problems} == {
        "packaged",
        "official_link",
    }
    packaged = [problem for problem in catalog.problems if problem.source.source_type == "packaged"]
    assert {problem.source.rights_basis for problem in packaged} == {
        "openlearn_original"
    }
    assert len({problem.difficulty for problem in packaged}) >= 2
    assert len({problem.primary_skill_ids[0] for problem in packaged}) >= 3
    assert all(problem.checksum.startswith("sha256:") for problem in catalog.problems)


def test_reference_implementations_pass_public_and_hidden_tests() -> None:
    catalog = interview_catalog.load_default_catalog()

    results = interview_catalog.validate_reference_implementations(catalog)

    assert results
    assert all(result.passed for result in results)
    assert {result.visibility for result in results} == {"public", "hidden"}


def test_normal_validation_rejects_wrong_expected_output() -> None:
    raw = _catalog()
    problem = _problem(raw)
    problem["languages"]["python"]["tests"]["hidden"][0]["expected"] = [7, 8]
    problem["checksum"] = interview_catalog.problem_checksum(problem)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    with pytest.raises(interview_catalog.CatalogValidationError, match="reference failed"):
        interview_catalog.validate_catalog(raw)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda raw: _problem(raw)["skills"].append(
                {"skill_id": "pattern.not-real", "role": "supporting"}
            ),
            "unknown skill",
        ),
        (
            lambda raw: _problem(raw)["languages"]["python"]["tests"]["public"][0].update(
                {"expected": float("nan")}
            ),
            "JSON",
        ),
        (
            lambda raw: _problem(raw)["languages"]["python"]["interface"].update(
                {"function": "not valid"}
            ),
            "identifier",
        ),
        (
            lambda raw: _problem(raw).update({"checksum": "sha256:" + ("0" * 64)}),
            "checksum",
        ),
        (
            lambda raw: _problem(raw)["follow_ups"][0].update(
                {"problem_id": "problem.not-real"}
            ),
            "follow-up",
        ),
    ],
)
def test_validator_rejects_invalid_cross_references_and_execution_contracts(
    mutation, message: str
) -> None:
    raw = _catalog()
    mutation(raw)

    with pytest.raises(interview_catalog.CatalogValidationError, match=message):
        interview_catalog.validate_catalog(raw)


def test_validator_rejects_duplicate_ids_and_bad_licenses() -> None:
    duplicate = _catalog()
    duplicate["problems"].append(copy.deepcopy(duplicate["problems"][0]))
    with pytest.raises(interview_catalog.CatalogValidationError, match="duplicate problem"):
        interview_catalog.validate_catalog(duplicate)

    bad_license = _catalog()
    _problem(bad_license)["source"]["license"] = ""
    with pytest.raises(interview_catalog.CatalogValidationError, match="license"):
        interview_catalog.validate_catalog(bad_license)


@pytest.mark.parametrize(
    ("rights_basis", "license_name"),
    [
        ("owned_or_licensed", "Owner permission dated 2026-07-27"),
        ("open_license", "CC-BY-4.0"),
    ],
)
def test_schema_supports_documented_owned_and_open_license_metadata(
    rights_basis: str, license_name: str
) -> None:
    raw = _catalog()
    problem = _problem(raw)
    problem["source"]["rights_basis"] = rights_basis
    problem["source"]["license"] = license_name
    problem["source"]["permission"] = "Fixture proving metadata validation only."
    problem["checksum"] = interview_catalog.problem_checksum(problem)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    catalog = interview_catalog.validate_catalog(raw)

    assert catalog.problem(problem["id"]).source.rights_basis == rights_basis


def test_similarity_flags_are_advisory_and_honor_declared_exclusions() -> None:
    raw = _catalog()
    clone = copy.deepcopy(_problem(raw))
    clone["id"] = "problem.pair-sum-sorted-review-copy"
    clone["revision"] = 1
    clone["near_duplicate_exclusions"] = ["problem.pair-sum-sorted"]
    clone["checksum"] = interview_catalog.problem_checksum(clone)
    raw["problems"].append(clone)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    catalog = interview_catalog.validate_catalog(raw)

    assert interview_catalog.similarity_review_flags(catalog) == ()

    clone["near_duplicate_exclusions"] = []
    clone["checksum"] = interview_catalog.problem_checksum(clone)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)
    catalog = interview_catalog.validate_catalog(raw)
    flags = interview_catalog.similarity_review_flags(catalog, threshold=0.8)
    assert flags[0].problem_ids == (
        "problem.pair-sum-sorted",
        "problem.pair-sum-sorted-review-copy",
    )


def test_revision_reference_is_exact_and_survives_catalog_updates() -> None:
    first = interview_catalog.load_default_catalog()
    problem = first.problem("problem.pair-sum-sorted")
    reference = interview_catalog.attempt_problem_reference(first, problem)
    raw = _catalog()
    raw["catalog_revision"] = 2
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)
    second = interview_catalog.validate_catalog(raw)

    assert reference == {
        "catalog_id": "openlearn-interview",
        "catalog_revision": 1,
        "problem_id": problem.problem_id,
        "problem_revision": problem.revision,
        "problem_checksum": problem.checksum,
    }
    assert reference["catalog_revision"] != second.catalog_revision


def test_official_link_workspace_contains_only_link_metadata_and_scaffold(
    tmp_path: Path,
) -> None:
    catalog = interview_catalog.load_default_catalog()
    problem = catalog.problem("external.leetcode.two-sum")
    workspace = tmp_path / "learner-workspace"

    with pytest.raises(PermissionError, match="confirmation"):
        interview_catalog.create_official_link_workspace(
            problem, workspace, learner_confirmed=False
        )

    created = interview_catalog.create_official_link_workspace(
        problem, workspace, learner_confirmed=True
    )
    readme = (created / "README.md").read_text(encoding="utf-8")
    solution = (created / "solution.py").read_text(encoding="utf-8")

    assert problem.source.url in readme
    assert "Open the official page" in readme
    assert "statement" not in readme.lower()
    assert "def solve" in solution
    assert stat.S_IMODE(created.stat().st_mode) == 0o700
    assert stat.S_IMODE((created / "README.md").stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        interview_catalog.create_official_link_workspace(
            problem, workspace, learner_confirmed=True
        )


def test_private_entries_load_separately_and_never_change_packaged_catalog(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    entry = {
        "id": "private.my-problem",
        "revision": 1,
        "title": "My private prompt",
    }
    (private / "mine.json").write_text(json.dumps(entry), encoding="utf-8")

    loaded = interview_catalog.load_private_entries(private)
    packaged = interview_catalog.load_default_catalog()

    assert loaded == (entry,)
    assert all(problem.problem_id != entry["id"] for problem in packaged.problems)
