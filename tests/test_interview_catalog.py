from __future__ import annotations

import copy
import json
import os
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


def test_evidence_eligible_records_exactly_match_pinned_graph_contract() -> None:
    catalog = interview_catalog.load_default_catalog()
    graph = interview_catalog.load_default_graph()

    for problem in catalog.problems:
        if problem.evidence_eligible:
            reference = interview_catalog.evidence_problem_reference(catalog, problem)
            canonical = graph.problem(problem.problem_id)
            assert (
                reference["mastery_policy_version"]
                == graph.mastery_policy_version
            )
            assert reference["transfer_family"] == canonical.transfer_family
            assert reference["skills"] == tuple(
                {"skill_id": skill.skill_id, "role": skill.role}
                for skill in canonical.skills
            )
        else:
            with pytest.raises(interview_catalog.CatalogValidationError, match="ineligible"):
                interview_catalog.evidence_problem_reference(catalog, problem)


def test_reference_implementations_pass_public_and_hidden_tests() -> None:
    catalog = interview_catalog.load_default_catalog()

    results = interview_catalog.validate_reference_implementations(catalog)

    assert results
    assert all(result.passed for result in results)
    assert {result.visibility for result in results} == {"public", "hidden"}


def test_normal_validation_rejects_wrong_expected_output() -> None:
    raw = _catalog()
    problem = _problem(raw, "problem.merge-intervals")
    problem["languages"]["python"]["tests"]["hidden"][0]["expected"] = [7, 8]
    problem["checksum"] = interview_catalog.problem_checksum(problem)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    with pytest.raises(interview_catalog.CatalogValidationError, match="reference failed"):
        interview_catalog.validate_catalog(raw)


def test_pair_sum_contract_rejects_ambiguous_exact_output_cases() -> None:
    raw = _catalog()
    problem = _problem(raw)
    problem["languages"]["python"]["tests"]["public"][0] = {
        "args": [[1, 4, 6, 9], 10],
        "kwargs": {},
        "expected": [0, 3],
    }
    problem["checksum"] = interview_catalog.problem_checksum(problem)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    with pytest.raises(interview_catalog.CatalogValidationError, match="unique pair"):
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
    with pytest.raises(interview_catalog.CatalogValidationError, match="duplicate problem revision"):
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

    assert (
        catalog.problem(problem["id"], revision=problem["revision"]).source.rights_basis
        == rights_basis
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("constraints", []),
        ("references", []),
        ("misconceptions", []),
        ("hints", []),
        ("follow_ups", []),
    ],
)
def test_packaged_feedback_and_transfer_fields_are_required(
    field: str, value: object
) -> None:
    raw = _catalog()
    problem = _problem(raw)
    problem[field] = value
    problem["checksum"] = interview_catalog.problem_checksum(problem)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    with pytest.raises(interview_catalog.CatalogValidationError, match=field):
        interview_catalog.validate_catalog(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("constraints", ["Copied constraint"]),
        ("references", ["Copied editorial"]),
        ("misconceptions", ["Copied misconception"]),
        ("hints", ["Copied hint"]),
        ("complexity", {"time": "O(n)", "space": "O(n)"}),
        (
            "follow_ups",
            [{"id": "copied", "prompt": "Copied prompt", "problem_id": "problem.merge-intervals"}],
        ),
    ],
)
def test_official_links_reject_protected_content_fields(
    field: str, value: object
) -> None:
    raw = _catalog()
    external = _problem(raw, "external.leetcode.two-sum")
    external[field] = value
    external["checksum"] = interview_catalog.problem_checksum(external)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)
    with pytest.raises(interview_catalog.CatalogValidationError, match="official link"):
        interview_catalog.validate_catalog(raw)


def test_starter_scaffold_rejects_top_level_execution() -> None:
    raw = _catalog()
    external = _problem(raw, "external.leetcode.two-sum")
    external["languages"]["python"]["starter_code"] = "print('runs on import')\ndef solve():\n    pass\n"
    external["checksum"] = interview_catalog.problem_checksum(external)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)
    with pytest.raises(interview_catalog.CatalogValidationError, match="inert"):
        interview_catalog.validate_catalog(raw)


def test_starter_scaffold_rejects_executable_annotations() -> None:
    raw = _catalog()
    external = _problem(raw, "external.leetcode.two-sum")
    external["languages"]["python"]["starter_code"] = (
        "def solve(value: danger()) -> danger():\n    pass\n"
    )
    external["languages"]["python"]["interface"]["parameters"] = ["value"]
    external["checksum"] = interview_catalog.problem_checksum(external)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    with pytest.raises(interview_catalog.CatalogValidationError, match="inert"):
        interview_catalog.validate_catalog(raw)


@pytest.mark.parametrize(
    "url",
    [
        "https://leetcode.com/problems/two-sum/\nCopied payload",
        "https://leetcode.com/problems/two-sum/\x00payload",
        "https://user:secret@leetcode.com/problems/two-sum/",
        "https://example.com/problems/two-sum/",
        "https://leetcode.com/problems/three-sum/",
        "https://leetcode.com/problems/two-sum/#copied",
    ],
)
def test_official_source_url_is_canonical_and_provider_bound(url: str) -> None:
    raw = _catalog()
    external = _problem(raw, "external.leetcode.two-sum")
    external["source"]["url"] = url
    external["checksum"] = interview_catalog.problem_checksum(external)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    with pytest.raises(interview_catalog.CatalogValidationError, match="source url"):
        interview_catalog.validate_catalog(raw)


def test_official_source_provider_identity_is_pinned() -> None:
    raw = _catalog()
    external = _problem(raw, "external.leetcode.two-sum")
    external["source"]["provider"] = "untrusted"
    external["checksum"] = interview_catalog.problem_checksum(external)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    with pytest.raises(interview_catalog.CatalogValidationError, match="provider"):
        interview_catalog.validate_catalog(raw)


def test_similarity_flags_are_advisory_and_honor_declared_exclusions() -> None:
    raw = _catalog()
    original = _problem(raw)
    comparison = _problem(raw, "problem.merge-intervals")
    comparison["statement"] = original["statement"]
    comparison["near_duplicate_exclusions"] = ["problem.pair-sum-sorted"]
    comparison["checksum"] = interview_catalog.problem_checksum(comparison)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    catalog = interview_catalog.validate_catalog(raw)

    assert interview_catalog.similarity_review_flags(catalog) == ()

    comparison["near_duplicate_exclusions"] = []
    comparison["checksum"] = interview_catalog.problem_checksum(comparison)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)
    catalog = interview_catalog.validate_catalog(raw)
    flags = interview_catalog.similarity_review_flags(catalog, threshold=0.8)
    assert flags[0].problem_ids == (
        "problem.merge-intervals",
        "problem.pair-sum-sorted",
    )


def test_revision_reference_is_exact_and_survives_catalog_updates() -> None:
    first = interview_catalog.load_default_catalog()
    problem = first.problem("problem.pair-sum-sorted", revision=1)
    reference = interview_catalog.attempt_problem_reference(first, problem)
    raw = _catalog()
    raw["catalog_revision"] = 2
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)
    second = interview_catalog.validate_catalog(raw)

    assert reference == {
        "catalog_id": "openlearn-interview",
        "catalog_revision": 2,
        "problem_id": problem.problem_id,
        "problem_revision": problem.revision,
        "problem_checksum": problem.checksum,
    }
    assert interview_catalog.resolve_attempt_problem(second, reference).revision == 1
    older_reference = dict(reference)
    older_reference["catalog_revision"] = 1
    assert interview_catalog.resolve_attempt_problem(second, older_reference).revision == 1


def test_catalog_retains_and_resolves_multiple_immutable_problem_revisions() -> None:
    catalog = interview_catalog.load_default_catalog()

    revision_one = catalog.problem("problem.pair-sum-sorted", revision=1)
    revision_two = catalog.problem("problem.pair-sum-sorted", revision=2)

    assert revision_one.checksum != revision_two.checksum
    assert catalog.problem("problem.pair-sum-sorted") is revision_two
    assert interview_catalog.resolve_attempt_problem(
        catalog, interview_catalog.attempt_problem_reference(catalog, revision_one)
    ) is revision_one
    impossible_reference = interview_catalog.attempt_problem_reference(
        catalog, revision_two
    )
    impossible_reference["catalog_revision"] = 1
    with pytest.raises(interview_catalog.CatalogValidationError, match="predates"):
        interview_catalog.resolve_attempt_problem(catalog, impossible_reference)


@pytest.mark.parametrize(
    ("revision", "introduced_revision", "first_introduced_revision"),
    [
        (3, 2, 1),
        (2, 1, 1),
        (2, 1, 2),
    ],
)
def test_problem_revision_chronology_rejects_gaps_backdating_and_reversal(
    revision: int,
    introduced_revision: int,
    first_introduced_revision: int,
) -> None:
    raw = _catalog()
    first = _problem(raw)
    second = next(
        problem
        for problem in raw["problems"]
        if problem["id"] == "problem.pair-sum-sorted" and problem["revision"] == 2
    )
    second["revision"] = revision
    second["introduced_catalog_revision"] = introduced_revision
    first["introduced_catalog_revision"] = first_introduced_revision
    first["checksum"] = interview_catalog.problem_checksum(first)
    second["checksum"] = interview_catalog.problem_checksum(second)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    with pytest.raises(interview_catalog.CatalogValidationError, match="chronology"):
        interview_catalog.validate_catalog(raw)


def test_validated_nested_values_are_deeply_immutable_and_detached() -> None:
    raw = _catalog()
    catalog = interview_catalog.validate_catalog(raw)
    problem = catalog.problem("problem.pair-sum-sorted", revision=1)
    case = problem.languages["python"].public_tests[0]
    raw["problems"][0]["languages"]["python"]["tests"]["public"][0]["args"][0][0] = 999
    raw["problems"][0]["languages"]["python"]["tests"]["public"][0]["expected"][0] = 999

    assert case.arguments[0][0] == 1
    assert case.expected[0] == 0
    with pytest.raises(TypeError):
        case.expected[0] = 999
    with pytest.raises(TypeError):
        case.keyword_arguments["new"] = True
    with pytest.raises(TypeError):
        problem.examples[0]["input"]["values"] = []


def test_nested_keyword_arguments_are_deeply_frozen() -> None:
    raw = _catalog()
    problem = _problem(raw, "problem.merge-intervals")
    case = problem["languages"]["python"]["tests"]["public"][0]
    case["args"] = []
    case["kwargs"] = {"intervals": [[1, 2]]}
    case["expected"] = [[1, 2]]
    problem["checksum"] = interview_catalog.problem_checksum(problem)
    raw["catalog_checksum"] = interview_catalog.catalog_checksum(raw)

    catalog = interview_catalog.validate_catalog(raw)
    frozen = catalog.problem("problem.merge-intervals", revision=1).languages[
        "python"
    ].public_tests[0]
    case["kwargs"]["intervals"][0][0] = 999

    assert frozen.keyword_arguments["intervals"][0][0] == 1
    with pytest.raises(TypeError):
        frozen.keyword_arguments["intervals"][0][0] = 999




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
    if os.name != "nt":
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


def test_windows_private_loader_rejects_directory_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    real_lstat = os.lstat
    original = real_lstat(private)
    changed_values = list(original)
    changed_values[1] += 1
    changed = os.stat_result(changed_values)
    directory_reads = 0

    def replaced_directory(path: str | os.PathLike[str]) -> os.stat_result:
        nonlocal directory_reads
        if os.fspath(path) == os.fspath(private):
            directory_reads += 1
            return original if directory_reads == 1 else changed
        return real_lstat(path)

    monkeypatch.setattr(interview_catalog.os, "lstat", replaced_directory)

    with pytest.raises(
        interview_catalog.CatalogValidationError,
        match="directory changed during read",
    ):
        interview_catalog.load_private_entries(private, _platform="nt")


def test_private_loader_rejects_symlinks_directories_and_oversize(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    target = private / "target"
    target.write_text("{}", encoding="utf-8")
    (private / "linked.json").symlink_to(target)
    with pytest.raises(interview_catalog.CatalogValidationError, match="symlink"):
        interview_catalog.load_private_entries(private)

    (private / "linked.json").unlink()
    real_directory = private / "real-directory"
    real_directory.mkdir()
    try:
        (private / "linked-directory.json").symlink_to(
            real_directory, target_is_directory=True
        )
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(interview_catalog.CatalogValidationError, match="symlink"):
        interview_catalog.load_private_entries(private)

    (private / "linked-directory.json").unlink()
    (private / "directory.json").mkdir()
    with pytest.raises(interview_catalog.CatalogValidationError, match="regular file"):
        interview_catalog.load_private_entries(private)

    (private / "directory.json").rmdir()
    (private / "large.json").write_bytes(
        b" " * (interview_catalog.MAX_PRIVATE_ENTRY_BYTES + 1)
    )
    with pytest.raises(interview_catalog.CatalogValidationError, match="too large"):
        interview_catalog.load_private_entries(private)


@pytest.mark.skipif(os.name == "nt", reason="POSIX opened-descriptor replacement semantics")
def test_private_entry_read_uses_opened_descriptor_when_path_is_replaced(
    tmp_path: Path,
) -> None:
    path = tmp_path / "entry.json"
    replacement = tmp_path / "replacement.json"
    path.write_text('{"id": "original"}', encoding="utf-8")
    replacement.write_text('{"id": "replacement"}', encoding="utf-8")

    def replacing_opener(candidate: Path, flags: int) -> int:
        descriptor = os.open(candidate, flags)
        candidate.unlink()
        replacement.rename(candidate)
        return descriptor

    assert interview_catalog._read_private_entry_path(
        path, opener=replacing_opener
    ) == {
        "id": "original"
    }


def test_private_entry_forced_no_nofollow_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(target)

    with pytest.raises(interview_catalog.CatalogValidationError, match="symlink"):
        interview_catalog._read_private_entry_path(linked, nofollow_flag=0)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor replacement semantics")
def test_private_directory_replacement_cannot_redirect_opened_directory(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    replacement = tmp_path / "replacement"
    private.mkdir()
    replacement.mkdir()
    (private / "entry.json").write_text('{"id": "original"}', encoding="utf-8")
    (replacement / "entry.json").write_text('{"id": "replacement"}', encoding="utf-8")

    def replacing_listdir(directory_fd: int) -> list[str]:
        names = os.listdir(directory_fd)
        private.rename(tmp_path / "original-directory")
        replacement.rename(private)
        return names

    assert interview_catalog.load_private_entries(
        private, _list_directory=replacing_listdir
    ) == ({"id": "original"},)
