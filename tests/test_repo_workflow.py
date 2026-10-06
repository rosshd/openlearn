from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "repo-workflow"
SCRIPT_CMD = ["bash", SCRIPT.as_posix()] if os.name == "nt" else [str(SCRIPT)]


@unittest.skipIf(os.name == "nt", "repo-workflow is a POSIX shell helper")
class RepoWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        base = Path(self.tempdir.name)
        self.remote = base / "remote.git"
        self.repo = base / "repo"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        subprocess.run(
            ["git", "clone", str(self.remote), str(self.repo)], check=True, capture_output=True
        )
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test User")
        (self.repo / "src/openlearn").mkdir(parents=True)
        (self.repo / "src/openlearn/__init__.py").write_text(
            '__version__ = "0.7.0"\n', encoding="utf-8"
        )
        (self.repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
        self.git("add", ".gitignore", "src/openlearn/__init__.py")
        self.git("commit", "-m", "Initial")
        self.git("branch", "-M", "main")
        self.git("push", "-u", "origin", "main")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def workflow(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["HOME"] = self.tempdir.name
        return subprocess.run(
            [*SCRIPT_CMD, *args],
            cwd=self.repo,
            check=check,
            capture_output=True,
            text=True,
            env=env,
        )

    def workflow_from(
        self, cwd: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["HOME"] = self.tempdir.name
        return subprocess.run(
            [*SCRIPT_CMD, *args],
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_status_reports_divergence_worktrees_and_version(self) -> None:
        result = self.workflow("status")

        self.assertIn("Version: 0.7.0", result.stdout)
        self.assertIn("Main divergence (cached origin/main): behind 0, ahead 0", result.stdout)
        self.assertIn(str(self.repo), result.stdout)

    def test_start_and_finish_clean_merged_worktree(self) -> None:
        result = self.workflow("start", "docs", "workflow")
        worktree = self.repo / ".worktrees/workflow"

        reported_worktree = Path(result.stdout.strip().splitlines()[-1])
        self.assertEqual(reported_worktree.resolve(), worktree.resolve())
        self.assertTrue(worktree.is_dir())
        self.assertEqual(self.git("branch", "--show-current", cwd=worktree).stdout.strip(), "docs/workflow")

        finished = self.workflow("finish", "workflow")

        self.assertIn("Removed", finished.stdout)
        self.assertFalse(worktree.exists())
        branches = self.git("branch", "--format=%(refname:short)").stdout.splitlines()
        self.assertNotIn("docs/workflow", branches)

    def test_finish_refuses_unmerged_branch(self) -> None:
        self.workflow("start", "feat", "unfinished")
        worktree = self.repo / ".worktrees/unfinished"
        (worktree / "change.txt").write_text("change\n", encoding="utf-8")
        self.git("add", "change.txt", cwd=worktree)
        self.git("commit", "-m", "Unmerged", cwd=worktree)

        result = self.workflow("finish", "unfinished", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not merged into main", result.stderr)
        self.assertTrue(worktree.exists())

    def test_finish_must_run_from_root_checkout(self) -> None:
        self.workflow("start", "docs", "wrong-place")
        worktree = self.repo / ".worktrees/wrong-place"

        result = self.workflow_from(worktree, "finish", "wrong-place", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Run this command from the root checkout", result.stderr)
        self.assertTrue(worktree.exists())

    def test_settled_historical_worktrees_do_not_consume_active_capacity(self) -> None:
        self.workflow("start", "docs", "old-one")
        self.workflow("start", "docs", "old-two")
        result = self.workflow("start", "docs", "third")
        self.assertIn("third", result.stdout)
        self.assertIn("settled", self.workflow("status").stdout)

    def test_registered_owners_consume_capacity_and_retirement_releases_it(self) -> None:
        for name in ("first", "second"):
            self.workflow("start", "docs", name)
            self.workflow_from(self.repo / ".worktrees" / name, "register", name,
                               "https://github.com/rosshd/openlearn/issues/114")
        denied = self.workflow("start", "docs", "third", check=False)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("approved limit", denied.stderr)
        self.workflow_from(self.repo / ".worktrees/first", "retire")
        self.workflow("start", "docs", "third")

    def test_unregistered_dirty_worktrees_are_unresolved(self) -> None:
        for name in ("first", "second"):
            self.workflow("start", "docs", name)
            (self.repo / ".worktrees" / name / "owned.txt").write_text("unfinished")
        denied = self.workflow("start", "docs", "third", check=False)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("unresolved", self.workflow("status").stdout)

    def test_child_starts_from_parent_without_touching_dirty_primary(self) -> None:
        self.workflow("start", "feat", "parent")
        parent = self.repo / ".worktrees/parent"
        (parent / "feature.txt").write_text("parent feature")
        self.git("add", "feature.txt", cwd=parent)
        self.git("commit", "-m", "Parent feature", cwd=parent)
        (self.repo / "private.txt").write_text("primary work")
        self.workflow_from(parent, "start", "feat", "child", "feat/parent")
        child = self.repo / ".worktrees/child"
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=parent).stdout,
                         self.git("rev-parse", "HEAD", cwd=child).stdout)
        self.assertEqual((self.repo / "private.txt").read_text(), "primary work")

    def test_finish_refuses_active_registered_owner(self) -> None:
        self.workflow("start", "docs", "active")
        self.workflow_from(self.repo / ".worktrees/active", "register", "owner",
                           "https://github.com/rosshd/openlearn/issues/114")
        denied = self.workflow("finish", "active", check=False)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("active owner", denied.stderr)
        self.assertTrue((self.repo / ".worktrees/active").exists())

    def test_finish_preserves_active_checkout_after_branch_switch(self) -> None:
        self.workflow("start", "docs", "active")
        worktree = self.repo / ".worktrees/active"
        self.workflow_from(worktree, "register", "owner",
                           "https://github.com/rosshd/openlearn/issues/114")
        self.git("switch", "-c", "docs/switched", cwd=worktree)
        denied = self.workflow("finish", "active", check=False)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("active owner", denied.stderr)
        self.assertTrue(worktree.exists())
        self.assertIn("docs/switched", self.git("branch", "--format=%(refname:short)").stdout)

    def test_retired_owner_with_changed_branch_is_unresolved(self) -> None:
        self.workflow("start", "docs", "retired")
        worktree = self.repo / ".worktrees/retired"
        self.workflow_from(worktree, "register", "owner",
                           "https://github.com/rosshd/openlearn/issues/114")
        self.workflow_from(worktree, "retire")
        self.git("switch", "-c", "docs/reused", cwd=worktree)
        self.assertIn("unresolved", self.workflow("status").stdout)

    def test_retired_owner_with_moved_path_is_unresolved(self) -> None:
        self.workflow("start", "docs", "retired")
        worktree = self.repo / ".worktrees/retired"
        self.workflow_from(worktree, "register", "owner",
                           "https://github.com/rosshd/openlearn/issues/114")
        self.workflow_from(worktree, "retire")
        self.git("worktree", "move", str(worktree), str(self.repo / ".worktrees/moved"))
        self.assertIn("unresolved", self.workflow("status").stdout)


if __name__ == "__main__":
    unittest.main()
