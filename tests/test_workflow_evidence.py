from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import runpy

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/workflow_evidence.py"
SPEC = importlib.util.spec_from_file_location("workflow_evidence_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


@pytest.fixture
def repository(tmp_path, monkeypatch):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".artifacts/\n")
    (tmp_path / "source.py").write_text("print('fixture')\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "Fixture"], cwd=tmp_path, check=True,
                   capture_output=True)
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    monkeypatch.setattr(evidence, "RECEIPT", tmp_path / ".artifacts/receipt.json")
    return tmp_path


def test_passing_receipt_reuses_only_matching_command_source_and_environment(repository, monkeypatch):
    command = [sys.executable, "-c", "print('gate passed')"]
    assert evidence.check(command) == 0
    assert evidence.reusable(command)
    assert not evidence.reusable([sys.executable, "-c", "print('different command')"])
    monkeypatch.setenv("PYTEST_ADDOPTS", "--some-other-check-mode")
    assert not evidence.reusable(command)
    monkeypatch.delenv("PYTEST_ADDOPTS")
    (repository / "source.py").write_text("print('changed')\n")
    assert not evidence.reusable(command)


def test_receipt_rejects_log_tampering_and_failed_gate(repository):
    command = [sys.executable, "-c", "print('gate passed')"]
    assert evidence.check(command) == 0
    import json
    receipt = json.loads(evidence.RECEIPT.read_text())
    Path(receipt["log"]).write_text("replacement output")
    assert not evidence.reusable(command)
    failure = [sys.executable, "-c", "raise SystemExit(1)"]
    assert evidence.check(failure) == 1
    assert not evidence.reusable(failure)


def test_file_modes_and_new_files_change_candidate(repository):
    original = evidence.candidate()
    if os.name != "nt":
        (repository / "source.py").chmod(0o755)
        assert evidence.candidate() != original
    (repository / "new.py").write_text("new candidate input")
    assert evidence.candidate() != original


def test_gate_rejects_candidate_changed_during_run(repository):
    command = [sys.executable, "-c", "from pathlib import Path; Path('source.py').write_text('changed')"]
    assert evidence.check(command) == 1
    assert not evidence.reusable(command)


def test_qa_environment_drops_provider_and_primary_home_settings(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    module = runpy.run_path(str(SCRIPT.parent / "qa_smoke.py"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-secret")
    monkeypatch.setenv("OPENLEARN_PROVIDER", "live-provider")
    monkeypatch.setenv("OPENLEARN_HOME", "/test-only-primary-home")
    result = module["isolated_environment"](tmp_path)
    assert result["OPENLEARN_HOME"] == str(tmp_path / "fixture")
    assert result["OPENLEARN_MOCK"] == "1"
    assert "OPENAI_API_KEY" not in result
    assert "OPENLEARN_PROVIDER" not in result


@pytest.mark.parametrize("name", ["PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD"])
def test_pytest_plugin_environment_invalidates_receipt(repository, monkeypatch, name):
    monkeypatch.delenv(name, raising=False)
    command = [sys.executable, "-c", "print('gate passed')"]
    assert evidence.check(command) == 0
    assert evidence.reusable(command)
    monkeypatch.setenv(name, "fixture-plugin" if name == "PYTEST_PLUGINS" else "1")
    assert not evidence.reusable(command)
