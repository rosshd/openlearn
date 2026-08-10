from __future__ import annotations

from pathlib import Path

import pytest

from tests.dogfood import support


def test_installed_openlearn_prefers_active_environment_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script_name = "openlearn.exe" if support.sys.platform == "win32" else "openlearn"
    environment_script = scripts / script_name
    environment_script.touch()
    monkeypatch.setattr(support.sysconfig, "get_path", lambda _name: str(scripts))
    monkeypatch.setattr(support.shutil, "which", lambda _name: "/stale/openlearn")

    assert support.installed_openlearn() == environment_script


def test_installed_openlearn_rejects_path_script_outside_active_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = tmp_path / "missing-scripts"
    stale = tmp_path / "stale" / "openlearn"
    stale.parent.mkdir()
    stale.touch()
    environment = tmp_path / "environment"
    environment.mkdir()
    monkeypatch.setattr(support.sysconfig, "get_path", lambda _name: str(scripts))
    monkeypatch.setattr(support.shutil, "which", lambda _name: str(stale))
    monkeypatch.setattr(support.sys, "prefix", str(environment))

    with pytest.raises(AssertionError, match="active Python environment"):
        support.installed_openlearn()
