from __future__ import annotations

import re
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
ISSUE_FORM = REPOSITORY / ".github" / "ISSUE_TEMPLATE" / "factory_work.yml"
PULL_REQUEST_TEMPLATE = REPOSITORY / ".github" / "pull_request_template.md"
ACTIVE_INSTRUCTIONS = (
    REPOSITORY / "AGENTS.md",
    REPOSITORY / "docs" / "AGENT_RUNS.md",
    REPOSITORY / ".github" / "copilot-instructions.md",
    *sorted((REPOSITORY / ".claude" / "skills").glob("*/SKILL.md")),
)
RETIRED_WORKFLOW_TERMS = (
    "background loop",
    "captain",
    "fleet",
    "gnhf",
    "treehouse",
    "no-mistakes",
    "herdr",
    "firstmate",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class RepositoryContractTests(unittest.TestCase):
    def test_required_factory_artifacts_exist(self) -> None:
        for path in (*ACTIVE_INSTRUCTIONS, ISSUE_FORM, PULL_REQUEST_TEMPLATE):
            with self.subTest(path=path.relative_to(REPOSITORY)):
                self.assertTrue(path.is_file())

    def test_factory_issue_form_records_dispatch_contract(self) -> None:
        form = read(ISSUE_FORM)
        required_labels = (
            "Outcome",
            "Acceptance checks",
            "Constraints",
            "Non-goals",
            "Evidence",
            "Risk",
            "Permissions",
            "Dependencies",
            "Verification",
        )

        self.assertTrue(form.startswith("name: Factory work\n"))
        self.assertIn("body:\n", form)
        items = form.split("\n  - type: ")[1:]
        for label in required_labels:
            with self.subTest(label=label):
                matches = [item for item in items if f"      label: {label}\n" in item]
                self.assertEqual(len(matches), 1)
                self.assertIn("    validations:\n      required: true", matches[0])
        ids = re.findall(r"(?m)^    id: ([a-z0-9_-]+)$", form)
        self.assertEqual(len(ids), len(set(ids)))

    def test_pull_request_template_records_factory_evidence(self) -> None:
        template = read(PULL_REQUEST_TEMPLATE).casefold()
        required_terms = (
            "linked issue",
            "owner task id",
            "worktree",
            "start sha",
            "head sha",
            "make check",
            "exact tested sha",
            "independent review",
            "exact reviewed sha",
            "risk level",
            "github `test` check",
            "after merge",
            "rollback",
        )

        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, template)

    def test_agent_docs_route_work_through_factory_path(self) -> None:
        instructions = "\n".join(read(path) for path in ACTIVE_INSTRUCTIONS[:2]).casefold()
        required_terms = (
            "one github issue",
            "one codex owner task",
            "one managed worktree",
            "make check",
            "one bounded independent review",
            "pull request",
            "exact reviewed head",
            "explicit authorization",
            "after merge",
        )

        for term in required_terms:
            with self.subTest(term=term):
                self.assertIn(term, instructions)

    def test_active_instructions_do_not_route_to_retired_workflows(self) -> None:
        for path in ACTIVE_INSTRUCTIONS:
            instructions = read(path).casefold()
            for term in RETIRED_WORKFLOW_TERMS:
                with self.subTest(path=path.relative_to(REPOSITORY), term=term):
                    self.assertNotIn(term, instructions)

    def test_make_check_is_canonical_and_review_is_only_evidence(self) -> None:
        makefile = read(REPOSITORY / "Makefile")
        agent_map = read(REPOSITORY / "AGENTS.md")
        runbook = read(REPOSITORY / "docs" / "AGENT_RUNS.md")

        self.assertRegex(makefile, r"(?m)^check: lint unit pytest smoke e2e$")
        self.assertRegex(makefile, r"(?m)^review:$")
        self.assertIn("`make check` is the one canonical local gate.", agent_map)
        self.assertIn("`make review` is an optional evidence collector.", agent_map)
        self.assertIn("running it is not an independent review", runbook)


if __name__ == "__main__":
    unittest.main()
