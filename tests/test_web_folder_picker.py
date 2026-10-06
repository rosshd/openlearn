from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openlearn.web import folder_picker


def test_native_selection_preserves_spaces_and_only_returns_a_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "Learning notes é"
    folder.mkdir()
    calls = []

    def run(command: list[str], **kwargs: object) -> object:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=str(folder) + "/\n")

    monkeypatch.setattr(folder_picker.sys, "platform", "darwin")
    monkeypatch.setattr(folder_picker.subprocess, "run", run)
    assert folder_picker.pick_folder() == str(folder) + "/"
    assert calls[0][0][0] == "/usr/bin/osascript"
    assert calls[0][1]["timeout"] == 120
    assert "shell" not in calls[0][1]


@pytest.mark.parametrize("platform,code,output", [("darwin", 0, "\n"), ("win32", 0, ""), ("linux", 1, "")])
def test_cancellation_does_not_select_or_import(platform: str, code: int, output: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(folder_picker.sys, "platform", platform)
    monkeypatch.setattr(folder_picker.shutil, "which", lambda _name: "/usr/bin/zenity")
    monkeypatch.setattr(folder_picker.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=code, stdout=output))
    assert folder_picker.pick_folder() is None


def test_busy_picker_is_rejected_without_starting_another_process() -> None:
    folder_picker._PICKER_LOCK.acquire()
    try:
        with pytest.raises(folder_picker.FolderPickerError, match="already open"):
            folder_picker.pick_folder()
    finally:
        folder_picker._PICKER_LOCK.release()


@pytest.mark.parametrize("output", ["relative/path\n", "/missing-openlearn-folder\n", "x" * 2049])
def test_invalid_native_selection_is_recoverable(output: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(folder_picker.sys, "platform", "darwin")
    monkeypatch.setattr(folder_picker.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=output))
    with pytest.raises(folder_picker.FolderPickerError, match="unavailable"):
        folder_picker.pick_folder()


@pytest.mark.parametrize("failure", [OSError(), subprocess.TimeoutExpired("picker", 120)])
def test_native_failure_releases_picker_and_retains_manual_fallback(failure: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(folder_picker.subprocess, "run", fail)
    monkeypatch.setattr(folder_picker.sys, "platform", "darwin")
    with pytest.raises(folder_picker.FolderPickerError, match="path"):
        folder_picker.pick_folder()
    assert not folder_picker._PICKER_LOCK.locked()


def test_unsupported_host_retains_manual_path_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(folder_picker.sys, "platform", "linux")
    monkeypatch.setattr(folder_picker.shutil, "which", lambda _name: None)
    with pytest.raises(folder_picker.FolderPickerError, match="Paste or type"):
        folder_picker.pick_folder()
    assert not folder_picker._PICKER_LOCK.locked()
