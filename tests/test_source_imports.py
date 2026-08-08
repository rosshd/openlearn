from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openlearn import cli, source_imports
from openlearn.constants import QUICK_LEARN_MAX_FILE_BYTES, QUICK_LEARN_MAX_FILES


class CourseSourceImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.previous = {
            name: os.environ.get(name) for name in ("OPENLEARN_HOME", "OPENLEARN_MOCK")
        }
        os.environ["OPENLEARN_HOME"] = self.home.name
        os.environ["OPENLEARN_MOCK"] = "1"
        cli._CONFIG_CACHE = None
        cli.cmd_new(
            argparse.Namespace(topic="Python", goal="Learn Python"), output_func=lambda _: None
        )

    def tearDown(self) -> None:
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        cli._CONFIG_CACHE = None
        self.home.cleanup()

    def import_source(self, source: source_imports.CourseSourceInput):
        request = source_imports.CourseSourceImportRequest("python", source, "test-model")
        return source_imports.import_course_source(request)

    def test_local_file_import_is_durable_and_deduplicated(self) -> None:
        source = Path(self.home.name) / "notes.md"
        source.write_text("# Iterators\nUse lazy traversal.", encoding="utf-8")

        first = self.import_source(source_imports.LocalFileSource(source))
        second = self.import_source(source_imports.LocalFileSource(source))

        self.assertEqual([item.label for item in first.imported], ["notes.md"])
        self.assertEqual([item.label for item in second.skipped], ["notes.md"])
        self.assertEqual(second.skipped[0].message, "Already imported.")
        self.assertEqual(len(first.sources), 1)
        self.assertEqual(source_imports.list_course_sources("python"), first.sources)
        self.assertTrue((cli.topic_context_dir("python") / first.sources[0].context_file).is_file())
        self.assertTrue((cli.topic_context_dir("python") / first.sources[0].summary_file).is_file())

    def test_local_extract_is_deterministic_and_never_contacts_provider(self) -> None:
        source = Path(self.home.name) / "private-notes.md"
        source.write_text("Private local source.\n", encoding="utf-8")

        with (
            mock.patch.object(
                cli,
                "summarize_context_file",
                side_effect=AssertionError("provider summary must not run"),
            ),
            mock.patch.object(
                cli,
                "generate_context_summary",
                side_effect=AssertionError("provider generation must not run"),
            ),
        ):
            result = self.import_source(source_imports.LocalFileSource(source))

        summary = cli.topic_context_dir("python") / result.sources[0].summary_file
        rendered = summary.read_text(encoding="utf-8")
        self.assertIn("created without contacting a model provider", rendered)
        self.assertIn("Private local source.", rendered)

    def test_local_file_rejects_unsafe_names_symlinks_and_size_boundary(self) -> None:
        secret = Path(self.home.name) / ".env.md"
        secret.write_text("API_TOKEN=do-not-return", encoding="utf-8")
        oversized = Path(self.home.name) / "large.txt"
        oversized.write_bytes(b"x" * (QUICK_LEARN_MAX_FILE_BYTES + 1))
        target = Path(self.home.name) / "target.md"
        target.write_text("safe target", encoding="utf-8")
        symlink = Path(self.home.name) / "linked.md"
        try:
            symlink.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")

        secret_result = self.import_source(source_imports.LocalFileSource(secret))
        large_result = self.import_source(source_imports.LocalFileSource(oversized))
        link_result = self.import_source(source_imports.LocalFileSource(symlink))

        self.assertEqual(secret_result.failed[0].message, "Unsupported or unsafe file name.")
        self.assertEqual(large_result.failed[0].message, "File exceeds the import size limit.")
        self.assertEqual(link_result.failed[0].message, "File could not be read safely.")
        rendered = repr((secret_result, large_result, link_result))
        self.assertNotIn("API_TOKEN", rendered)
        self.assertEqual(source_imports.list_course_sources("python"), ())

    def test_file_failure_never_returns_source_contents(self) -> None:
        source = Path(self.home.name) / "lesson.md"
        secret_text = "private lesson text must stay private"
        source.write_text(secret_text, encoding="utf-8")
        request = source_imports.CourseSourceImportRequest(
            "python", source_imports.LocalFileSource(source), "test-model"
        )
        with mock.patch.object(
            source_imports, "_write_local_extract", side_effect=RuntimeError(secret_text)
        ):
            result = source_imports.import_course_source(request)

        self.assertEqual(result.failed[0].message, "Source could not be imported.")
        self.assertNotIn(secret_text, repr(result))
        self.assertEqual(list(cli.topic_context_dir("python").glob("*")), [])

    def test_folder_import_uses_quick_learn_selection_boundaries(self) -> None:
        folder = Path(self.home.name) / "project"
        (folder / "build").mkdir(parents=True)
        (folder / ".private").mkdir()
        (folder / "README.md").write_text("Repository guide", encoding="utf-8")
        (folder / "algorithm.py").write_text("def solve(): return 1", encoding="utf-8")
        (folder / ".hidden.md").write_text("hidden-value", encoding="utf-8")
        (folder / ".env").write_text("SECRET=hidden", encoding="utf-8")
        (folder / "build" / "generated.py").write_text("generated-value", encoding="utf-8")
        (folder / ".private" / "notes.md").write_text("private-value", encoding="utf-8")

        result = self.import_source(source_imports.LocalFolderSource(folder))

        self.assertEqual({item.label for item in result.imported}, {"README.md", "algorithm.py"})
        self.assertEqual({source.label for source in result.sources}, {"README.md", "algorithm.py"})
        saved = "\n".join(
            path.read_text(encoding="utf-8") for path in cli.context_source_files("python")
        )
        self.assertNotIn("SECRET", saved)
        self.assertNotIn("hidden-value", saved)
        self.assertNotIn("generated-value", saved)
        self.assertNotIn("private-value", saved)

    def test_folder_import_reports_file_count_skips_and_deduplicates(self) -> None:
        folder = Path(self.home.name) / "many"
        folder.mkdir()
        for index in range(QUICK_LEARN_MAX_FILES + 1):
            (folder / f"note-{index:02d}.txt").write_text(f"unique note {index}", encoding="utf-8")

        first = self.import_source(source_imports.LocalFolderSource(folder))
        second = self.import_source(source_imports.LocalFolderSource(folder))

        self.assertEqual(len(first.imported), QUICK_LEARN_MAX_FILES)
        self.assertEqual(len(first.skipped), 1)
        self.assertEqual(first.skipped[0].message, "file-count limit")
        self.assertEqual(len(second.imported), 0)
        self.assertEqual(len(second.skipped), QUICK_LEARN_MAX_FILES + 1)
        self.assertEqual(len(second.sources), QUICK_LEARN_MAX_FILES)

    def test_folder_failure_is_structured_and_does_not_echo_path(self) -> None:
        missing = Path(self.home.name) / "private-folder-name" / "missing"

        result = self.import_source(source_imports.LocalFolderSource(missing))

        self.assertEqual(
            result.failed[0].message, "Folder contained no supported, readable sources."
        )
        self.assertNotIn(str(missing), repr(result))

    def test_folder_symlinks_are_inert_and_excluded(self) -> None:
        folder = Path(self.home.name) / "project"
        folder.mkdir()
        (folder / "README.md").write_text("Visible", encoding="utf-8")
        outside = Path(self.home.name) / "outside.md"
        outside.write_text("outside secret", encoding="utf-8")
        link = folder / "linked.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")

        result = self.import_source(source_imports.LocalFolderSource(folder))

        self.assertEqual([item.label for item in result.imported], ["README.md"])
        saved = "\n".join(
            path.read_text(encoding="utf-8") for path in cli.context_source_files("python")
        )
        self.assertNotIn("outside secret", saved)

    def test_public_github_import_is_shallow_inert_and_deduplicated(self) -> None:
        calls: list[tuple[list[str], dict[str, str]]] = []

        def fake_clone(command, clone_dir, env):
            calls.append((command, env))
            clone_dir.mkdir(parents=True)
            (clone_dir / "README.md").write_text("Public guide", encoding="utf-8")
            (clone_dir / "main.py").write_text("raise RuntimeError('never run')", encoding="utf-8")

        source = source_imports.PublicGitHubSource("https://github.com/example/learning.git")
        with mock.patch.object(cli, "_bounded_git_clone", side_effect=fake_clone):
            first = self.import_source(source)
            second = self.import_source(source)

        self.assertEqual({item.label for item in first.imported}, {"README.md", "main.py"})
        self.assertEqual(len(second.skipped), 2)
        command, options = calls[0]
        self.assertEqual(
            command[:6],
            ["git", "-c", "core.hooksPath=/dev/null", "clone", "--depth", "1"],
        )
        self.assertEqual(options["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(options["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(options["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertIn("--single-branch", command)
        self.assertIn("--no-tags", command)
        self.assertIn("--filter=blob:limit=200000", command)
        self.assertNotIn("run", repr(first))

    def test_public_github_clone_timeout_is_sanitized(self) -> None:
        with mock.patch.object(
            cli,
            "_bounded_git_clone",
            side_effect=cli.OpenLearnError("public GitHub repository clone timed out"),
        ):
            result = self.import_source(
                source_imports.PublicGitHubSource("https://github.com/example/learning")
            )

        self.assertEqual(result.failed[0].message, "Repository could not be imported.")
        self.assertNotIn("30 seconds", repr(result))

    def test_public_github_clone_size_limit_is_sanitized(self) -> None:
        with mock.patch.object(
            cli,
            "_bounded_git_clone",
            side_effect=cli.OpenLearnError("public GitHub repository exceeds clone size"),
        ):
            result = self.import_source(
                source_imports.PublicGitHubSource("https://github.com/example/learning")
            )

        self.assertEqual(result.failed[0].message, "Repository could not be imported.")

    def test_folder_discovery_budget_fails_closed_without_echoing_path(self) -> None:
        folder = Path(self.home.name) / "private-large-tree"
        folder.mkdir()
        filenames = [f"note-{index}.txt" for index in range(5000)]

        with mock.patch.object(cli.os, "walk", return_value=[(str(folder), [], filenames)]):
            result = self.import_source(source_imports.LocalFolderSource(folder))

        self.assertEqual(
            result.failed[0].message,
            "Folder contained no supported, readable sources.",
        )
        self.assertNotIn(str(folder), repr(result))

    def test_public_github_rejects_other_urls_without_cloning(self) -> None:
        with mock.patch.object(cli, "_bounded_git_clone") as clone:
            result = self.import_source(
                source_imports.PublicGitHubSource("https://example.com/private/repository")
            )

        clone.assert_not_called()
        self.assertEqual(result.failed[0].message, "Enter a public GitHub repository URL.")

    def test_public_github_clone_failure_is_sanitized(self) -> None:
        error = subprocess.CalledProcessError(
            128,
            ["git", "clone"],
            stderr="fatal: token ghp_private_value cannot access private repository",
        )
        with mock.patch.object(cli, "_bounded_git_clone", side_effect=error):
            result = self.import_source(
                source_imports.PublicGitHubSource("https://github.com/example/learning")
            )

        self.assertEqual(result.failed[0].message, "Repository could not be imported.")
        self.assertNotIn("ghp_private_value", repr(result))


if __name__ == "__main__":
    unittest.main()
