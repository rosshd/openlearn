from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile


@unittest.skipUnless(
    os.environ.get("OPENLEARN_PACKAGE_SMOKE") == "1",
    "set OPENLEARN_PACKAGE_SMOKE=1 to build and test the installed wheel",
)
class InstalledWheelSmokeTests(unittest.TestCase):
    def test_installed_wheel_renders_templates_and_serves_static_assets(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            wheelhouse = temp / "wheelhouse"
            target = temp / "installed"
            wheelhouse.mkdir()
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    ".",
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(wheelhouse),
                ],
                cwd=repository,
                check=True,
            )
            wheels = list(wheelhouse.glob("openlearn-*.whl"))
            self.assertEqual(len(wheels), 1)
            wheel = wheels[0]

            with ZipFile(wheel) as archive:
                packaged = set(archive.namelist())
            for expected in (
                "openlearn/web/templates/base.html",
                "openlearn/web/templates/focus.html",
                "openlearn/web/static/openlearn.css",
                "openlearn/web/static/openlearn.js",
                "openlearn/web/static/favicon.svg",
                "openlearn/code_workspace.py",
                "openlearn/source_imports.py",
                "openlearn/video_tools.py",
            ):
                self.assertIn(expected, packaged)

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--target",
                    str(target),
                    str(wheel),
                ],
                cwd=temp,
                check=True,
            )
            smoke = """
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
import openlearn
from openlearn.web.app import PlaceholderServices, create_app

installed = pathlib.Path(sys.argv[1]).resolve()
assert installed in pathlib.Path(openlearn.__file__).resolve().parents
client = TestClient(create_app(PlaceholderServices(), testing=True))
setup = client.get('/setup')
assert setup.status_code == 200
assert 'Connect your tutor' in setup.text
for path, content_type in (
    ('/static/openlearn.css', 'text/css'),
    ('/static/openlearn.js', 'text/javascript'),
    ('/static/favicon.svg', 'image/svg+xml'),
):
    response = client.get(path)
    assert response.status_code == 200, path
    assert content_type in response.headers['content-type'], path
"""
            subprocess.run(
                [sys.executable, "-c", smoke, str(target)],
                cwd=temp,
                check=True,
                env={**os.environ, "OPENLEARN_HOME": str(temp / "home")},
            )


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
