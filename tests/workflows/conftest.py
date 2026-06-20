from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pexpect
import pytest


ANSI_RE = re.compile(
    r"(?:\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~])"
)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text).replace("\r", "")


@dataclass
class OpenLearnProcess:
    child: pexpect.spawn
    log: io.StringIO

    @property
    def clean_output(self) -> str:
        return strip_ansi(self.log.getvalue())

    def expect(self, pattern: str, timeout: int | float = 5) -> int:
        return self.child.expect(pattern, timeout=timeout)

    def sendline(self, text: str = "") -> None:
        self.child.sendline(text)

    def send(self, text: str) -> None:
        self.child.send(text)

    def sendcontrol(self, char: str) -> None:
        self.child.sendcontrol(char)

    def close(self) -> None:
        if self.child.isalive():
            self.child.close(force=True)


@dataclass
class OpenLearnRunner:
    command: str
    base_args: list[str]
    env: dict[str, str]

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.command, *self.base_args, *args],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )

    def spawn(self, *args: str, timeout: int | float = 5) -> OpenLearnProcess:
        log = io.StringIO()
        child = pexpect.spawn(
            self.command,
            [*self.base_args, *args],
            env=self.env,
            dimensions=(24, 120),
            encoding="utf-8",
            timeout=timeout,
        )
        child.logfile_read = log
        return OpenLearnProcess(child=child, log=log)

    def create_topic(self, name: str = "workflow") -> None:
        self.run("new", name, "--goal", "Workflow smoke test")


@pytest.fixture
def spawn_openlearn(tmp_path: Path) -> OpenLearnRunner:
    root = Path(__file__).resolve().parents[2]
    script = root / ".venv" / "bin" / "openlearn"
    if script.exists():
        command = str(script)
        base_args: list[str] = []
    else:
        command = sys.executable
        base_args = ["-c", "from openlearn.cli import main; raise SystemExit(main())"]

    env = os.environ.copy()
    env["OPENLEARN_HOME"] = str(tmp_path)
    env["OPENLEARN_MOCK"] = "1"
    env["PYTHONPATH"] = (
        str(root / "src")
        if not env.get("PYTHONPATH")
        else f"{root / 'src'}{os.pathsep}{env['PYTHONPATH']}"
    )
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("COLUMNS", "120")
    env.setdefault("LINES", "24")

    if not script.exists() and shutil.which(command) is None:
        pytest.fail("Could not find an openlearn executable for workflow tests")

    return OpenLearnRunner(command=command, base_args=base_args, env=env)
