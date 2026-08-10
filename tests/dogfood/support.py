from __future__ import annotations

import shutil
import sys
import sysconfig
from pathlib import Path

import pytest


POSIX_PTY_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="dogfood terminal flows require a POSIX pty",
)


def installed_openlearn() -> Path:
    """Resolve the active editable install without assuming a repo-local virtualenv."""
    script_name = "openlearn.exe" if sys.platform == "win32" else "openlearn"
    environment_script = Path(sysconfig.get_path("scripts")) / script_name
    if environment_script.is_file():
        return environment_script
    executable = shutil.which("openlearn")
    assert executable is not None, "installed openlearn executable is required"
    resolved = Path(executable).resolve()
    environment_root = Path(sys.prefix).resolve()
    assert resolved.is_relative_to(environment_root), (
        "installed openlearn executable must belong to the active Python environment"
    )
    return resolved
