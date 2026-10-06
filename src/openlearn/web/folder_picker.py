"""Optional native folder selection for the local web interface."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from threading import Lock

_PICKER_LOCK = Lock()
_TIMEOUT_SECONDS = 120


class FolderPickerError(RuntimeError):
    pass


def _picker_command() -> list[str]:
    if sys.platform == "darwin":
        return [
            "/usr/bin/osascript", "-e",
            'activate\ntry\nreturn POSIX path of (choose folder with prompt "Choose a source folder for openlearn")'
            '\non error number -128\nreturn ""\nend try',
        ]
    if sys.platform == "win32":
        return [
            "powershell.exe", "-NoProfile", "-STA", "-Command",
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$picker = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$picker.Description = 'Choose a source folder for openlearn'; "
            "$picker.ShowNewFolderButton = $false; "
            "if ($picker.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { "
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "[Console]::WriteLine($picker.SelectedPath) }; $picker.Dispose()",
        ]
    if executable := shutil.which("zenity"):
        return [executable, "--file-selection", "--directory", "--title=Choose a source folder for openlearn"]
    raise FolderPickerError("Folder browsing is unavailable here. Paste or type the folder path instead.")


def pick_folder() -> str | None:
    """Return only the explicitly chosen path; cancellation has no side effects."""
    if not _PICKER_LOCK.acquire(blocking=False):
        raise FolderPickerError("A folder chooser is already open. Finish or cancel it first.")
    try:
        try:
            result = subprocess.run(
                _picker_command(), capture_output=True, text=True, encoding="utf-8",
                timeout=_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise FolderPickerError("Folder selection timed out. Try Browse again or enter the path.") from error
        except OSError as error:
            raise FolderPickerError("The folder chooser could not open. Paste or type the path instead.") from error
        if result.returncode == 1 and sys.platform not in {"darwin", "win32"}:
            return None  # Zenity cancellation.
        if result.returncode:
            raise FolderPickerError("The folder chooser could not open. Paste or type the path instead.")
        value = result.stdout.rstrip("\r\n")
        if not value:
            return None
        if len(value) > 2048 or not Path(value).is_absolute() or not Path(value).is_dir():
            raise FolderPickerError("The selected folder is unavailable. Choose another folder or enter its path.")
        return value
    finally:
        _PICKER_LOCK.release()
