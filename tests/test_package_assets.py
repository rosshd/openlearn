from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import tempfile
import tomllib
import unittest
from unittest import mock
from zipfile import ZipFile


REPOSITORY = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = REPOSITORY / "scripts" / "release_artifacts.py"
SPEC = importlib.util.spec_from_file_location("release_artifacts", RELEASE_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release_artifacts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_artifacts)


def _wheel(path: Path, *, extra: tuple[str, bytes] | None = None) -> None:
    with ZipFile(path, "w") as archive:
        for required in release_artifacts.REQUIRED_PACKAGE_FILES:
            archive.writestr(required, b"safe package content")
        archive.writestr(
            "openlearn-0.7.0.dist-info/METADATA",
            b"Metadata-Version: 2.1\nName: openlearn\nVersion: 0.7.0\n",
        )
        if extra is not None:
            archive.writestr(*extra)


class ReleaseArtifactPolicyTests(unittest.TestCase):
    def test_installed_web_smoke_bootstraps_session_before_namespaced_reads(self) -> None:
        class Response:
            status = 200

            def __init__(self, body: bytes) -> None:
                self.body = body

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return self.body

        class Opener:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def open(self, url: str, *, timeout: int) -> Response:
                self.urls.append(url)
                expected = {
                    "http://127.0.0.1:9123/?access_token=test-token": b"",
                    "http://127.0.0.1:9123/_openlearn/test/setup": b"Connect your tutor",
                    "http://127.0.0.1:9123/_openlearn/test/static/openlearn.css": b"--orange",
                    "http://127.0.0.1:9123/_openlearn/test/static/openlearn.js": b"requestJson",
                    "http://127.0.0.1:9123/_openlearn/test/static/favicon.svg": b"<svg",
                }
                if url not in expected:
                    raise AssertionError(f"unexpected unauthenticated URL: {url}")
                return Response(expected[url])

        class Process:
            stdout = None

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                return None

            def wait(self, *, timeout: int) -> None:
                return None

        opener = Opener()
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            (home / ".web-server.json").write_text(
                json.dumps(
                    {
                        "url_namespace": "/_openlearn/test",
                        "access_token": "test-token",
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(release_artifacts, "_free_loopback_port", return_value=9123),
                mock.patch.object(release_artifacts.subprocess, "Popen", return_value=Process()),
                mock.patch.object(release_artifacts, "build_opener", return_value=opener),
                mock.patch.object(release_artifacts.time, "monotonic", side_effect=(0, 0, 21)),
            ):
                release_artifacts._smoke_web(Path("openlearn"), home)

        self.assertEqual(
            opener.urls[:2],
            [
                "http://127.0.0.1:9123/?access_token=test-token",
                "http://127.0.0.1:9123/_openlearn/test/setup",
            ],
        )

    def test_support_contract_covers_python_and_desktop_platforms(self) -> None:
        metadata = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]

        self.assertEqual(metadata["requires-python"], ">=3.11,<3.14")
        classifiers = set(metadata["classifiers"])
        for version in ("3.11", "3.12", "3.13"):
            self.assertIn(f"Programming Language :: Python :: {version}", classifiers)
        for platform in (
            "Operating System :: MacOS",
            "Operating System :: Microsoft :: Windows",
            "Operating System :: POSIX :: Linux",
        ):
            self.assertIn(platform, classifiers)

    def test_wheel_inspection_requires_assets_and_reads_metadata_version(self) -> None:
        with self.subTest("complete wheel"):
            path = Path(self.id().replace(".", "-"))
            try:
                _wheel(path)
                self.assertEqual(
                    release_artifacts.inspect_artifact(path, sdist=False), "0.7.0"
                )
            finally:
                path.unlink(missing_ok=True)

    def test_distribution_inspection_rejects_private_paths_and_provider_secrets(self) -> None:
        cases = (
            ("learning-topics/private.state.json", b"private learner state"),
            ("openlearn/accidental.py", b"token = 'sk-proj-" + b"a" * 32 + b"'"),
            (".github/workflows/debug.yml", b"development workflow"),
        )
        for index, extra in enumerate(cases):
            with self.subTest(path=extra[0]):
                path = REPOSITORY / f".package-policy-{index}.whl"
                try:
                    _wheel(path, extra=extra)
                    with self.assertRaises(release_artifacts.ReleaseArtifactError):
                        release_artifacts.inspect_artifact(path, sdist=False)
                finally:
                    path.unlink(missing_ok=True)

    def test_release_workflow_promotes_a_successful_exact_commit_candidate(self) -> None:
        workflow = (REPOSITORY / ".github/workflows/release.yml").read_text(encoding="utf-8")

        self.assertIn("release-candidate-${GITHUB_SHA}", workflow)
        self.assertIn("--status success", workflow)
        self.assertIn('workflowName == "Tests"', workflow)
        self.assertIn("release_artifacts.py verify", workflow)
        self.assertNotIn("python -m build", workflow)
        self.assertIn("packages-dir: release-candidate/dist", workflow)

    def test_test_workflow_builds_once_and_fans_in_required_gates(self) -> None:
        workflow = (REPOSITORY / ".github/workflows/tests.yml").read_text(encoding="utf-8")

        self.assertEqual(workflow.count("release_artifacts.py build"), 1)
        self.assertIn('python-version: ["3.11", "3.12", "3.13"]', workflow)
        self.assertIn("os: [ubuntu-latest, windows-latest, macos-latest]", workflow)
        self.assertIn("security-gate", workflow)
        self.assertIn("browser-smoke", workflow)
        self.assertIn("package-smoke", workflow)


def test_primary_action_colors_meet_wcag_aa() -> None:
    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "openlearn"
        / "web"
        / "static"
        / "openlearn.css"
    ).read_text(encoding="utf-8")

    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    pairs = re.findall(
        r"--orange:\s*(#[0-9a-fA-F]{6});\s*\n\s*--action-ink:\s*(#[0-9a-fA-F]{6})",
        css,
    )
    assert len(pairs) >= 2
    for background, foreground in pairs:
        light, dark = sorted((luminance(background), luminance(foreground)), reverse=True)
        assert (light + 0.05) / (dark + 0.05) >= 4.5


def test_web_assets_keep_accessibility_release_guards() -> None:
    repository = Path(__file__).resolve().parents[1]
    css = (repository / "src/openlearn/web/static/openlearn.css").read_text(
        encoding="utf-8"
    )
    javascript = (repository / "src/openlearn/web/static/openlearn.js").read_text(
        encoding="utf-8"
    )
    base = (repository / "src/openlearn/web/templates/base.html").read_text(
        encoding="utf-8"
    )

    assert '@media (max-width: 390px)' in css
    assert '@media (prefers-reduced-motion: reduce)' in css
    assert ".site-nav-groups" in css
    assert "min-height: 2.75rem" in css
    assert 'aria-label="Primary navigation"' in base
    assert 'data-nav-group' in base
    assert "restoreFocus" in javascript
    assert 'setAttribute("aria-expanded"' in javascript


if __name__ == "__main__":
    unittest.main()
