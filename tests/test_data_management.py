from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from openlearn import cli, data_management


def _write_persistent_file_after_signal(
    home: str,
    relative: str,
    content: str,
    start: multiprocessing.synchronize.Event,
    attempting: multiprocessing.synchronize.Event,
    finished: multiprocessing.synchronize.Event,
) -> None:
    os.environ["OPENLEARN_HOME"] = home
    start.wait(timeout=10)
    attempting.set()
    cli.write_text_atomic(Path(home) / relative, content)
    finished.set()


def _write_complete_home(home: Path) -> dict[str, bytes]:
    files = {
        "config.json": b'{"base_url":"https://example.test/v1","model":"model","api_key":"secret-key"}\n',
        "state.json": b'{"active_topic":"python"}\n',
        "tui_history.txt": b"status python\n",
        "learning-topics/python.md": b"# Python\n\n---\n{}\n---\n",
        "learning-topics/python.state.json": b'{"revision":2}\n',
        "learning-topics/python.events.jsonl": b'{"event":"started"}\n',
        "learning-topics/.python.turn-commit.json": b'{"recovery":"pending"}\n',
        "learning-topics/python.interview.json": b'{"placement":{}}\n',
        "learning-topics/python/context/notes.md": b"# Notes\n",
        "learning-topics/interview-attempts/python/attempt.json": b'{"id":"attempt"}\n',
        "learning-topics/drills/python/attempt/solution.py": b"print('ok')\n",
    }
    for relative, content in files.items():
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (home / "learning-topics" / ".python.md.lock").write_text("lock", encoding="utf-8")
    (home / ".web-server.json").write_text("lease", encoding="utf-8")
    (home / ".web-server.lock").write_text("lock", encoding="utf-8")
    (home / "artifacts").mkdir()
    (home / "artifacts" / "temporary.txt").write_text("ignored", encoding="utf-8")
    return files


def _persistent_bytes(home: Path) -> dict[str, bytes]:
    return {
        entry.relative_path: (home / entry.relative_path).read_bytes()
        for entry in data_management.inventory_home(home).entries
    }


def test_inventory_classifies_complete_home_and_excludes_ephemeral_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    expected = _write_complete_home(home)

    inventory = data_management.inventory_home(home)

    assert {entry.relative_path for entry in inventory.entries} == set(expected)
    assert inventory.manifest_version == data_management.MANIFEST_VERSION
    assert inventory.credentials_present is True
    assert {entry.category for entry in inventory.entries} >= {
        "configuration",
        "course",
        "state",
        "events",
        "interview",
        "context",
        "attempt",
        "drill",
    }
    assert set(inventory.excluded_paths) == {
        "artifacts/",
        "learning-topics/.python.md.lock",
        ".web-server.json",
        ".web-server.lock",
    }


def test_default_backup_redacts_key_and_credential_backup_is_explicit_and_private(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _write_complete_home(home)

    default_backup = data_management.create_backup(home, tmp_path / "default.olbackup")
    with zipfile.ZipFile(default_backup.archive) as archive:
        config = archive.read("data/config.json")
        assert b"secret-key" not in config
        assert b"model" in config
        assert json.loads(archive.read("manifest.json"))["credentials_included"] is False

    with pytest.raises(data_management.CredentialScopeError):
        data_management.create_backup(
            home, tmp_path / "credentials.olbackup", include_credentials=True
        )
    credential_backup = data_management.create_backup(
        home,
        tmp_path / "credentials.olbackup",
        include_credentials=True,
        credential_confirmation=data_management.CREDENTIAL_CONFIRMATION,
    )
    with zipfile.ZipFile(credential_backup.archive) as archive:
        assert b"secret-key" in archive.read("data/config.json")
    if os.name != "nt":
        assert stat.S_IMODE(credential_backup.archive.stat().st_mode) == 0o600


def test_verified_backup_restore_round_trip_is_byte_equivalent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_complete_home(source)
    before = _persistent_bytes(source)
    archive = data_management.create_backup(
        source,
        tmp_path / "backup.olbackup",
        include_credentials=True,
        credential_confirmation=data_management.CREDENTIAL_CONFIRMATION,
    ).archive
    restored = tmp_path / "restored"

    result = data_management.restore_backup(archive, restored)

    assert result.home == restored.resolve()
    assert _persistent_bytes(restored) == before


def test_default_restore_omits_only_saved_credentials(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_complete_home(source)
    expected = _persistent_bytes(source)
    expected["config.json"] = (
        json.dumps(
            {"base_url": "https://example.test/v1", "model": "model"},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    archive = data_management.create_backup(source, tmp_path / "backup.olbackup").archive
    restored = tmp_path / "restored"

    data_management.restore_backup(archive, restored)

    assert _persistent_bytes(restored) == expected


def test_backup_creation_enforces_entry_limit_without_leaving_an_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write_complete_home(home)
    (home / "learning-topics" / "large.md").write_bytes(b"x" * 257)
    archive = tmp_path / "oversized.olbackup"
    monkeypatch.setattr(data_management, "MAX_ARCHIVE_ENTRY_BYTES", 256)

    with pytest.raises(data_management.ArchiveSafetyError):
        data_management.create_backup(home, archive)

    assert not archive.exists()


@pytest.mark.parametrize(
    "member",
    [
        "../escape",
        "/absolute",
        "data/../../escape",
        ".",
        "./manifest.json",
        "data//course.md",
        "data/course.md/",
        "C:/Windows/win.ini",
        "C:relative.txt",
        r"data\course.md",
        r"..\escape",
        r"\\server\share\secret",
        "data/nul\x00suffix",
    ],
)
def test_restore_rejects_malicious_archive_paths_before_mutation(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "malicious.olbackup"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr(member, b"bad")
    target = tmp_path / "target"

    with pytest.raises(data_management.ArchiveSafetyError):
        data_management.restore_backup(archive, target)

    assert not target.exists()


def test_restore_maps_canonical_posix_members_to_native_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_complete_home(source)
    archive = data_management.create_backup(source, tmp_path / "backup.olbackup").archive
    target = tmp_path / "target"

    data_management.restore_backup(archive, target)

    native = target.joinpath("learning-topics", "python", "context", "notes.md")
    assert native.is_file()
    assert native.resolve().is_relative_to(target.resolve())


def test_extraction_target_containment_is_enforced_independently(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()

    with pytest.raises(data_management.ArchiveSafetyError, match="escapes staging"):
        data_management._safe_extraction_target(
            staging, PurePosixPath("..", "escape")
        )


def test_restore_rejects_symlink_duplicate_and_oversized_entries_before_mutation(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "malicious.olbackup"
    link = zipfile.ZipInfo("data/link")
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("manifest.json", json.dumps({"manifest_version": 1, "entries": []}))
        value.writestr("data/duplicate", b"one")
        value.writestr("data/duplicate", b"two")
        value.writestr(link, b"target")
    target = tmp_path / "target"

    with pytest.raises(data_management.ArchiveSafetyError):
        data_management.restore_backup(archive, target)

    assert not target.exists()


def test_backup_verification_failure_prevents_reset_delete_and_move(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_complete_home(home)
    before = _persistent_bytes(home)
    broken = tmp_path / "broken.olbackup"
    broken.write_bytes(b"not an archive")

    for operation in (
        lambda: data_management.reset_home(
            home, broken, confirmation=data_management.RESET_CONFIRMATION
        ),
        lambda: data_management.delete_home(
            home, broken, confirmation=data_management.DELETE_CONFIRMATION
        ),
        lambda: data_management.move_home(
            home,
            tmp_path / "moved",
            broken,
            confirmation=data_management.MOVE_CONFIRMATION,
        ),
    ):
        with pytest.raises(data_management.BackupVerificationError):
            operation()
        assert _persistent_bytes(home) == before
    assert not (tmp_path / "moved").exists()


def test_unsupported_manifest_stops_restore_before_mutation_with_recovery_guidance(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "future.olbackup"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("manifest.json", json.dumps({"manifest_version": 999, "entries": []}))
    target = tmp_path / "target"

    with pytest.raises(data_management.CompatibilityError, match="backup and recovery"):
        data_management.restore_backup(archive, target)

    assert not target.exists()


def test_move_failure_preserves_source_and_removes_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _write_complete_home(home)
    before = _persistent_bytes(home)
    backup = data_management.create_backup(
        home,
        tmp_path / "backup.olbackup",
        include_credentials=True,
        credential_confirmation=data_management.CREDENTIAL_CONFIRMATION,
    ).archive

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated copy failure")

    monkeypatch.setattr(data_management, "_copy_regular_file", fail_copy)
    with pytest.raises(OSError, match="simulated copy failure"):
        data_management.move_home(
            home,
            tmp_path / "moved",
            backup,
            confirmation=data_management.MOVE_CONFIRMATION,
            include_credentials=True,
            credential_confirmation=data_management.CREDENTIAL_CONFIRMATION,
        )

    assert _persistent_bytes(home) == before
    assert not list(tmp_path.glob(".openlearn-move-*"))


def test_move_publishes_verified_copy_and_retains_source_for_explicit_cleanup(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _write_complete_home(home)
    sentinel = home / "unrelated-sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    backup = data_management.create_backup(
        home,
        tmp_path / "backup.olbackup",
        include_credentials=True,
        credential_confirmation=data_management.CREDENTIAL_CONFIRMATION,
    ).archive

    before = _persistent_bytes(home)
    result = data_management.move_home(
        home,
        tmp_path / "moved",
        backup,
        confirmation=data_management.MOVE_CONFIRMATION,
        include_credentials=True,
        credential_confirmation=data_management.CREDENTIAL_CONFIRMATION,
    )

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert _persistent_bytes(home) == before
    assert result.destination == (tmp_path / "moved").resolve()
    assert result.source == home.resolve()
    assert result.cleanup_required is True
    assert result.source_retained is True
    assert (tmp_path / "moved" / "learning-topics" / "python.md").exists()


def test_move_never_runs_post_promotion_source_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write_complete_home(home)
    before = _persistent_bytes(home)
    backup = data_management.create_backup(
        home,
        tmp_path / "backup.olbackup",
        include_credentials=True,
        credential_confirmation=data_management.CREDENTIAL_CONFIRMATION,
    ).archive

    def forbidden_cleanup(*_args: object, **_kwargs: object) -> None:
        raise OSError("source cleanup must be explicit")

    monkeypatch.setattr(data_management, "_remove_inventory_entries", forbidden_cleanup)
    result = data_management.move_home(
        home,
        tmp_path / "moved",
        backup,
        confirmation=data_management.MOVE_CONFIRMATION,
        include_credentials=True,
        credential_confirmation=data_management.CREDENTIAL_CONFIRMATION,
    )

    assert result.cleanup_required
    assert _persistent_bytes(home) == before
    assert _persistent_bytes(result.destination) == before


def test_destructive_operations_refuse_repo_root_without_touching_sentinel(tmp_path: Path) -> None:
    home = tmp_path / "legacy-project"
    _write_complete_home(home)
    (home / "pyproject.toml").write_text("[project]\nname='sentinel'\n", encoding="utf-8")
    sentinel = home / "unrelated-sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    unrelated = home / "src" / "private-source.txt"
    unrelated.parent.mkdir()
    unrelated.write_text("never archive this", encoding="utf-8")
    backup = tmp_path / "backup.olbackup"

    assert data_management.inventory_home(home).entries
    backup_result = data_management.create_backup(home, tmp_path / "repo-backup.olbackup")
    assert backup_result.archive.is_file()
    with zipfile.ZipFile(backup_result.archive) as archive_value:
        assert "data/src/private-source.txt" not in archive_value.namelist()
        assert "src/" in json.loads(archive_value.read("manifest.json"))["excluded_paths"]
    for operation in (
        lambda: data_management.reset_home(home, backup, confirmation=data_management.RESET_CONFIRMATION),
        lambda: data_management.delete_home(home, backup, confirmation=data_management.DELETE_CONFIRMATION),
        lambda: data_management.move_home(home, tmp_path / "moved", backup, confirmation=data_management.MOVE_CONFIRMATION),
    ):
        with pytest.raises(data_management.UnsafeHomeError):
            operation()
        assert sentinel.read_text(encoding="utf-8") == "keep"


def test_files_created_after_backup_verification_are_never_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write_complete_home(home)
    backup = data_management.create_backup(home, tmp_path / "backup.olbackup").archive
    original_verify = data_management.verify_backup
    late_file = home / "learning-topics" / "late.md"

    def verify_then_write(*args: object, **kwargs: object) -> data_management.BackupResult:
        result = original_verify(*args, **kwargs)
        late_file.write_text("# Late\n", encoding="utf-8")
        return result

    monkeypatch.setattr(data_management, "verify_backup", verify_then_write)
    data_management.reset_home(
        home, backup, confirmation=data_management.RESET_CONFIRMATION
    )

    assert late_file.read_text(encoding="utf-8") == "# Late\n"


def test_files_changed_after_backup_verification_are_never_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write_complete_home(home)
    backup = data_management.create_backup(home, tmp_path / "backup.olbackup").archive
    original_verify = data_management.verify_backup
    changed = home / "learning-topics" / "python.md"

    def verify_then_change(*args: object, **kwargs: object) -> data_management.BackupResult:
        result = original_verify(*args, **kwargs)
        changed.write_text("# Changed after verification\n", encoding="utf-8")
        return result

    monkeypatch.setattr(data_management, "verify_backup", verify_then_change)

    with pytest.raises(data_management.BackupVerificationError):
        data_management.reset_home(
            home, backup, confirmation=data_management.RESET_CONFIRMATION
        )

    assert changed.read_text(encoding="utf-8") == "# Changed after verification\n"
    assert (home / "state.json").exists()


def test_writer_cannot_commit_between_reset_verification_and_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write_complete_home(home)
    backup = data_management.create_backup(home, tmp_path / "backup.olbackup").archive
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    attempting = context.Event()
    finished = context.Event()
    writer = context.Process(
        target=_write_persistent_file_after_signal,
        args=(
            str(home),
            "learning-topics/python.md",
            "# Written after reset\n",
            start,
            attempting,
            finished,
        ),
    )
    original_remove = data_management._remove_inventory_entries

    def checkpoint_remove(*args: object, **kwargs: object) -> None:
        start.set()
        assert attempting.wait(timeout=10)
        assert not finished.wait(timeout=0.3)
        original_remove(*args, **kwargs)

    monkeypatch.setattr(data_management, "_remove_inventory_entries", checkpoint_remove)
    writer.start()
    try:
        data_management.reset_home(
            home, backup, confirmation=data_management.RESET_CONFIRMATION
        )
        assert finished.wait(timeout=10)
    finally:
        writer.join(timeout=10)
        if writer.is_alive():
            writer.terminate()
            writer.join(timeout=5)

    assert writer.exitcode == 0
    assert (home / "learning-topics" / "python.md").read_text(encoding="utf-8") == (
        "# Written after reset\n"
    )


def test_backup_snapshot_blocks_persistent_writer_until_archive_is_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write_complete_home(home)
    original = (home / "learning-topics" / "python.md").read_text(encoding="utf-8")
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    attempting = context.Event()
    finished = context.Event()
    writer = context.Process(
        target=_write_persistent_file_after_signal,
        args=(
            str(home),
            "learning-topics/python.md",
            "# New generation\n",
            start,
            attempting,
            finished,
        ),
    )
    original_write_archive = data_management._write_archive

    def checkpoint_archive(*args: object, **kwargs: object) -> dict[str, object]:
        start.set()
        assert attempting.wait(timeout=10)
        assert not finished.wait(timeout=0.3)
        return original_write_archive(*args, **kwargs)

    monkeypatch.setattr(data_management, "_write_archive", checkpoint_archive)
    writer.start()
    try:
        result = data_management.create_backup(home, tmp_path / "backup.olbackup")
        assert finished.wait(timeout=10)
    finally:
        writer.join(timeout=10)
        if writer.is_alive():
            writer.terminate()
            writer.join(timeout=5)

    assert writer.exitcode == 0
    with zipfile.ZipFile(result.archive) as archive:
        assert archive.read("data/learning-topics/python.md").decode("utf-8") == original
    assert (home / "learning-topics" / "python.md").read_text(encoding="utf-8") == (
        "# New generation\n"
    )


def test_cli_data_backup_executes_shared_lifecycle_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _write_complete_home(home)
    monkeypatch.setenv("OPENLEARN_HOME", str(home))
    archive = tmp_path / "cli-backup.olbackup"
    output: list[str] = []

    result = cli.cmd_data(
        argparse.Namespace(
            data_action="backup",
            archive=archive,
            include_credentials=False,
            credential_confirmation=None,
        ),
        output_func=output.append,
    )

    assert result == 0
    assert archive.exists()
    assert output == [f"Verified backup: {archive.resolve()}"]
