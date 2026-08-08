"""Verified whole-home backup and lifecycle operations for learner-owned data.

The module deliberately owns the storage policy used by every Openlearn surface.
It works only on an explicitly resolved Openlearn home and never follows links
while inventorying, archiving, restoring, moving, resetting, or deleting data.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


MANIFEST_VERSION = 1
RESET_CONFIRMATION = "RESET OPENLEARN DATA"
DELETE_CONFIRMATION = "DELETE OPENLEARN HOME"
MOVE_CONFIRMATION = "MOVE OPENLEARN HOME"
CREDENTIAL_CONFIRMATION = "INCLUDE SAVED CREDENTIALS"
MAX_ARCHIVE_ENTRY_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_RATIO = 100
_CREDENTIAL_KEYS = frozenset({"api_key", "openai_api_key"})


def confirmation_phrases() -> dict[str, str]:
    """Return the exact confirmation phrases shared by presentation adapters."""
    return {
        "credentials": CREDENTIAL_CONFIRMATION,
        "move": MOVE_CONFIRMATION,
        "reset": RESET_CONFIRMATION,
        "delete": DELETE_CONFIRMATION,
    }


class DataManagementError(RuntimeError):
    """A whole-home operation could not be completed safely."""


class UnsafeHomeError(DataManagementError):
    """The requested operation could target more than the resolved Openlearn home."""


class ArchiveSafetyError(DataManagementError):
    """An archive is malformed or unsafe to inspect or extract."""


class BackupVerificationError(DataManagementError):
    """A destructive operation was refused because its backup is not current and valid."""


class CompatibilityError(DataManagementError):
    """The manifest version is unsupported and requires recovery rather than mutation."""


class CredentialScopeError(DataManagementError):
    """Saved credentials require their own explicit confirmation."""


@dataclass(frozen=True)
class InventoryEntry:
    relative_path: str
    category: str
    size: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "category": self.category,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class HomeInventory:
    home: Path
    entries: tuple[InventoryEntry, ...]
    excluded_paths: tuple[str, ...]
    credentials_present: bool
    manifest_version: int = MANIFEST_VERSION

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    def summary(self) -> dict[str, object]:
        return {
            "home": str(self.home),
            "manifest_version": self.manifest_version,
            "files": len(self.entries),
            "bytes": self.total_bytes,
            "credentials_present": self.credentials_present,
            "excluded": list(self.excluded_paths),
        }


@dataclass(frozen=True)
class BackupResult:
    archive: Path
    inventory: HomeInventory
    credentials_included: bool
    warning: str | None


@dataclass(frozen=True)
class RestoreResult:
    home: Path
    inventory: HomeInventory


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _assert_safe_home(
    home: Path, *, must_exist: bool = False, destructive: bool = False
) -> Path:
    resolved = _resolved(home)
    forbidden = {Path(resolved.anchor), Path.home().resolve()}
    if resolved in forbidden:
        raise UnsafeHomeError("refusing to operate on a broad or repository directory")
    if destructive and (resolved == Path.cwd().resolve() or (resolved / "pyproject.toml").is_file()):
        raise UnsafeHomeError("refusing destructive operation on a repository directory")
    if must_exist and not resolved.is_dir():
        raise UnsafeHomeError("Openlearn home does not exist or is not a directory")
    if resolved.exists() and not resolved.is_dir():
        raise UnsafeHomeError("Openlearn home must be a directory")
    return resolved


def _assert_safe_destination(destination: Path, source: Path | None = None) -> Path:
    resolved = _assert_safe_home(destination)
    if source is not None and (
        resolved.is_relative_to(source) or source.is_relative_to(resolved)
    ):
        raise UnsafeHomeError("destination must not contain or contain the Openlearn home")
    return resolved


def _relative(path: Path, home: Path) -> str:
    try:
        return path.relative_to(home).as_posix()
    except ValueError as exc:
        raise UnsafeHomeError("path escapes the resolved Openlearn home") from exc


def _excluded(relative: str) -> bool:
    name = PurePosixPath(relative).name
    parts = PurePosixPath(relative).parts
    if (parts and parts[0] == "artifacts") or any(
        part in {".staging", ".openlearn-staging"} for part in parts
    ):
        return True
    if relative == ".web-server.json":
        return True
    if name in {".DS_Store", ".gitkeep"}:
        return True
    if name.endswith((".lock", ".lease", ".socket", ".tmp")):
        return True
    return False


def _category(relative: str) -> str | None:
    parts = PurePosixPath(relative).parts
    if relative == "config.json":
        return "configuration"
    if relative == "state.json":
        return "state"
    if relative == "tui_history.txt":
        return "history"
    if not parts or parts[0] != "learning-topics":
        return None
    if len(parts) == 2 and parts[1].endswith(".md"):
        return "course"
    if len(parts) == 2 and parts[1].endswith(".state.json"):
        return "state"
    if len(parts) == 2 and parts[1].endswith(".events.jsonl"):
        return "events"
    if len(parts) == 2 and parts[1].endswith(".interview.json"):
        return "interview"
    if len(parts) >= 2 and parts[1] == "interview-attempts":
        return "attempt"
    if len(parts) >= 2 and parts[1] == "drills":
        return "drill"
    if len(parts) >= 3 and parts[2] == "context":
        return "context"
    return "course_data"


def _config_has_credentials(content: bytes) -> bool:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and any(
        isinstance(value.get(key), str) and bool(value[key]) for key in _CREDENTIAL_KEYS
    )


def _archive_bytes(relative: str, content: bytes, *, include_credentials: bool) -> bytes:
    if relative != "config.json" or include_credentials:
        return content
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataManagementError(
            "config.json cannot be exported safely; repair it before creating a backup"
        ) from exc
    if not isinstance(value, dict):
        raise DataManagementError("config.json cannot be exported safely; expected an object")
    redacted = {key: item for key, item in value.items() if key not in _CREDENTIAL_KEYS}
    return (json.dumps(redacted, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_regular_file(path: Path, home: Path) -> bytes:
    status = path.lstat()
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise UnsafeHomeError(f"unsupported file type in Openlearn home: {_relative(path, home)}")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(home):
        raise UnsafeHomeError("path escapes the resolved Openlearn home")
    return path.read_bytes()


def _regular_file_digest(path: Path, home: Path) -> tuple[int, str]:
    status = path.lstat()
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise UnsafeHomeError(f"unsupported file type in Openlearn home: {_relative(path, home)}")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(home):
        raise UnsafeHomeError("path escapes the resolved Openlearn home")
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def inventory_home(home: Path) -> HomeInventory:
    """Return the stable, complete inventory for a resolved Openlearn home."""

    resolved = _assert_safe_home(home)
    if not resolved.exists():
        return HomeInventory(resolved, (), (), False)
    entries: list[InventoryEntry] = []
    excluded: list[str] = []
    credentials_present = False
    for current, directories, files in os.walk(resolved, topdown=True, followlinks=False):
        current_path = Path(current)
        at_home_root = current_path == resolved
        safe_directories: list[str] = []
        for name in sorted(directories):
            candidate = current_path / name
            relative = _relative(candidate, resolved)
            if at_home_root and name != "learning-topics":
                excluded.append(f"{relative}/")
                continue
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise UnsafeHomeError(f"unsupported directory type in Openlearn home: {relative}")
            if _excluded(relative):
                excluded.append(f"{relative}/")
            else:
                safe_directories.append(name)
        directories[:] = safe_directories
        for name in sorted(files):
            candidate = current_path / name
            relative = _relative(candidate, resolved)
            if _excluded(relative):
                excluded.append(relative)
                continue
            category = _category(relative)
            if category is None:
                excluded.append(relative)
                continue
            size, digest = _regular_file_digest(candidate, resolved)
            if relative == "config.json":
                if size > MAX_ARCHIVE_ENTRY_BYTES:
                    raise UnsafeHomeError("config.json exceeds the supported size limit")
                content = _read_regular_file(candidate, resolved)
                credentials_present = _config_has_credentials(content)
            entries.append(InventoryEntry(relative, category, size, digest))
    return HomeInventory(
        resolved,
        tuple(sorted(entries, key=lambda entry: entry.relative_path)),
        tuple(sorted(excluded)),
        credentials_present,
    )


def _manifest_for_inventory(
    inventory: HomeInventory, *, include_credentials: bool
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for entry in inventory.entries:
        if entry.relative_path == "config.json" and not include_credentials:
            content = _read_regular_file(inventory.home / entry.relative_path, inventory.home)
            if len(content) != entry.size or _sha256(content) != entry.sha256:
                raise DataManagementError(
                    "Openlearn data changed while the backup was being created; retry the backup"
                )
            archived = _archive_bytes(
                entry.relative_path, content, include_credentials=False
            )
            entries.append(
                {
                    "path": entry.relative_path,
                    "category": entry.category,
                    "size": len(archived),
                    "sha256": _sha256(archived),
                }
            )
        else:
            entries.append(entry.as_dict())
    return {
        "manifest_version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "credentials_included": include_credentials,
        "entries": entries,
        "excluded_paths": list(inventory.excluded_paths),
    }


def _write_archive(
    archive: Path, inventory: HomeInventory, *, include_credentials: bool
) -> dict[str, object]:
    archive.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{archive.name}.", suffix=".tmp", dir=archive.parent
    )
    try:
        os.close(descriptor)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as value:
            total = 0
            manifest_entries: list[dict[str, object]] = []
            for entry in inventory.entries:
                source = inventory.home / entry.relative_path
                if entry.relative_path == "config.json" and not include_credentials:
                    source_content = _read_regular_file(source, inventory.home)
                    if len(source_content) != entry.size or _sha256(source_content) != entry.sha256:
                        raise DataManagementError(
                            "Openlearn data changed while the backup was being created; retry the backup"
                        )
                    archived = _archive_bytes(
                        entry.relative_path, source_content, include_credentials=False
                    )
                    if len(archived) > MAX_ARCHIVE_ENTRY_BYTES:
                        raise ArchiveSafetyError("Openlearn data exceeds safe backup limits")
                    value.writestr(f"data/{entry.relative_path}", archived)
                    archived_size = len(archived)
                    archived_hash = _sha256(archived)
                else:
                    status = source.lstat()
                    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
                        raise UnsafeHomeError(
                            f"unsupported file type in Openlearn home: {entry.relative_path}"
                        )
                    if not source.resolve(strict=True).is_relative_to(inventory.home):
                        raise UnsafeHomeError("path escapes the resolved Openlearn home")
                    digest = hashlib.sha256()
                    archived_size = 0
                    with source.open("rb") as input_handle, value.open(
                        f"data/{entry.relative_path}", "w"
                    ) as output_handle:
                        while chunk := input_handle.read(1024 * 1024):
                            archived_size += len(chunk)
                            total_candidate = total + archived_size
                            if (
                                archived_size > MAX_ARCHIVE_ENTRY_BYTES
                                or total_candidate > MAX_ARCHIVE_BYTES
                            ):
                                raise ArchiveSafetyError(
                                    "Openlearn data exceeds safe backup limits"
                                )
                            digest.update(chunk)
                            output_handle.write(chunk)
                    archived_hash = digest.hexdigest()
                    if archived_size != entry.size or archived_hash != entry.sha256:
                        raise DataManagementError(
                            "Openlearn data changed while the backup was being created; retry the backup"
                        )
                total += archived_size
                if total > MAX_ARCHIVE_BYTES:
                    raise ArchiveSafetyError("Openlearn data exceeds safe backup limits")
                manifest_entries.append(
                    {
                        "path": entry.relative_path,
                        "category": entry.category,
                        "size": archived_size,
                        "sha256": archived_hash,
                    }
                )
            manifest = {
                "manifest_version": MANIFEST_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "credentials_included": include_credentials,
                "entries": manifest_entries,
                "excluded_paths": list(inventory.excluded_paths),
            }
            value.writestr(
                "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            )
        os.replace(temporary, archive)
        try:
            archive.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return manifest


def create_backup(
    home: Path,
    archive: Path,
    *,
    include_credentials: bool = False,
    credential_confirmation: str | None = None,
) -> BackupResult:
    """Create and verify a manifest-backed private archive without mutating the home."""

    source = _assert_safe_home(home, must_exist=True)
    target = _resolved(archive)
    if target.is_relative_to(source):
        raise UnsafeHomeError("backup archive must not be written inside the Openlearn home")
    if include_credentials and credential_confirmation != CREDENTIAL_CONFIRMATION:
        raise CredentialScopeError("including saved credentials requires explicit credential confirmation")
    inventory = inventory_home(source)
    try:
        _write_archive(target, inventory, include_credentials=include_credentials)
    except OSError as exc:
        raise DataManagementError("backup archive could not be created") from exc
    try:
        _inspect_archive(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return BackupResult(
        target,
        inventory,
        include_credentials,
        "Saved credentials are included in this archive." if include_credentials else None,
    )


def _safe_archive_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or path.parts[0] == ".":
        raise ArchiveSafetyError("archive contains an unsafe path")
    return path


def _inspect_archive(
    archive: Path, *, extract_to: Path | None = None
) -> dict[str, object]:
    try:
        with zipfile.ZipFile(archive) as value:
            infos = value.infolist()
            names: set[str] = set()
            total = 0
            for info in infos:
                _safe_archive_name(info.filename)
                if info.filename in names:
                    raise ArchiveSafetyError("archive contains duplicate paths")
                names.add(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ArchiveSafetyError("archive contains a symbolic link")
                if info.is_dir() or info.file_size > MAX_ARCHIVE_ENTRY_BYTES:
                    raise ArchiveSafetyError("archive contains an unsupported or oversized entry")
                total += info.file_size
                if total > MAX_ARCHIVE_BYTES or (
                    info.file_size and (not info.compress_size or info.file_size / info.compress_size > MAX_ARCHIVE_RATIO)
                ):
                    raise ArchiveSafetyError("archive exceeds safe extraction limits")
            if "manifest.json" not in names:
                raise ArchiveSafetyError("archive does not contain a manifest")
            try:
                manifest = json.loads(value.read("manifest.json"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ArchiveSafetyError("archive manifest is unreadable") from exc
            if not isinstance(manifest, dict):
                raise ArchiveSafetyError("archive manifest is invalid")
            version = manifest.get("manifest_version")
            if version != MANIFEST_VERSION:
                raise CompatibilityError(
                    "unsupported backup manifest; keep the original home, create a compatible backup, and use backup and recovery tooling"
                )
            raw_entries = manifest.get("entries")
            if not isinstance(raw_entries, list):
                raise ArchiveSafetyError("archive manifest entries are invalid")
            manifest_paths: set[str] = set()
            for entry in raw_entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    raise ArchiveSafetyError("archive manifest entries are invalid")
                relative = entry["path"]
                _safe_archive_name(relative)
                if relative in manifest_paths:
                    raise ArchiveSafetyError("archive manifest contains duplicate paths")
                manifest_paths.add(relative)
                member = f"data/{relative}"
                if member not in names:
                    raise ArchiveSafetyError("archive data does not match its manifest")
                digest = hashlib.sha256()
                extracted_size = 0
                output_handle = None
                temporary: str | None = None
                if extract_to is not None:
                    target = extract_to / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    descriptor, temporary = tempfile.mkstemp(
                        prefix=f".{target.name}.", dir=target.parent
                    )
                    output_handle = os.fdopen(descriptor, "wb")
                try:
                    with value.open(member) as input_handle:
                        while chunk := input_handle.read(1024 * 1024):
                            extracted_size += len(chunk)
                            digest.update(chunk)
                            if output_handle is not None:
                                output_handle.write(chunk)
                    if output_handle is not None:
                        output_handle.flush()
                        os.fsync(output_handle.fileno())
                finally:
                    if output_handle is not None:
                        output_handle.close()
                if (
                    entry.get("size") != extracted_size
                    or entry.get("sha256") != digest.hexdigest()
                ):
                    raise ArchiveSafetyError("archive data does not match its manifest")
                if extract_to is not None:
                    assert temporary is not None
                    os.replace(temporary, extract_to / relative)
            if names != {"manifest.json", *(f"data/{path}" for path in manifest_paths)}:
                raise ArchiveSafetyError("archive contains files outside its manifest")
            return manifest
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveSafetyError("backup archive cannot be read safely") from exc


def verify_backup(home: Path, archive: Path, *, include_credentials: bool = False) -> BackupResult:
    """Verify archive integrity and prove it exactly matches the current requested scope."""

    source = _assert_safe_home(home, must_exist=True)
    try:
        manifest = _inspect_archive(_resolved(archive))
    except DataManagementError as exc:
        if isinstance(exc, CompatibilityError):
            raise
        raise BackupVerificationError("backup verification failed; no learner data was changed") from exc
    if manifest.get("credentials_included") is not include_credentials:
        raise BackupVerificationError("backup scope does not match the requested operation")
    inventory = inventory_home(source)
    expected = _manifest_for_inventory(
        inventory, include_credentials=include_credentials
    )
    comparable = ("manifest_version", "credentials_included", "entries", "excluded_paths")
    if any(manifest.get(key) != expected.get(key) for key in comparable):
        raise BackupVerificationError("backup is not a verified copy of the current Openlearn home")
    return BackupResult(
        _resolved(archive),
        inventory,
        include_credentials,
        "Saved credentials are included in this archive." if include_credentials else None,
    )


def _staging_path(parent: Path, purpose: str) -> Path:
    return parent / f".openlearn-{purpose}-{uuid.uuid4().hex}"


def restore_backup(archive: Path, home: Path) -> RestoreResult:
    """Preflight then atomically restore a backup into an empty or absent home."""

    target = _assert_safe_destination(home)
    if target.exists() and any(target.iterdir()):
        raise UnsafeHomeError("restore target must be absent or empty")
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = _staging_path(parent, "restore")
    try:
        staging.mkdir(mode=0o700)
        manifest = _inspect_archive(_resolved(archive), extract_to=staging)
        restored = inventory_home(staging)
        expected_entries = manifest["entries"]
        if [entry.as_dict() for entry in restored.entries] != expected_entries:
            raise ArchiveSafetyError("restored data does not match the archive manifest")
        if target.exists():
            target.rmdir()
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return RestoreResult(
        target,
        HomeInventory(
            home=target,
            entries=restored.entries,
            excluded_paths=restored.excluded_paths,
            credentials_present=restored.credentials_present,
        ),
    )


def _copy_regular_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_handle, target.open("xb") as output_handle:
        shutil.copyfileobj(input_handle, output_handle)
        output_handle.flush()
        os.fsync(output_handle.fileno())


def _remove_inventory_entries(
    home: Path, entries: tuple[InventoryEntry, ...], *, include_credentials: bool
) -> None:
    """Remove only files that were covered by the immediately verified backup."""
    removable = tuple(
        entry
        for entry in entries
        if include_credentials or entry.relative_path != "config.json"
    )
    for entry in removable:
        path = home / entry.relative_path
        try:
            content = _read_regular_file(path, home)
        except (FileNotFoundError, OSError) as exc:
            raise BackupVerificationError(
                "Openlearn data changed after backup verification; no further data was removed"
            ) from exc
        if len(content) != entry.size or _sha256(content) != entry.sha256:
            raise BackupVerificationError(
                "Openlearn data changed after backup verification; no data was removed"
            )
    for entry in removable:
        (home / entry.relative_path).unlink()
    for current, directories, _files in os.walk(home, topdown=False):
        for name in directories:
            candidate = Path(current) / name
            if candidate.is_dir() and not candidate.is_symlink():
                try:
                    candidate.rmdir()
                except OSError:
                    pass


def reset_home(
    home: Path,
    backup: Path,
    *,
    confirmation: str,
    include_credentials: bool = False,
    credential_confirmation: str | None = None,
) -> HomeInventory:
    """Remove learner data only after verifying an exact, scope-matched backup."""

    source = _assert_safe_home(home, must_exist=True, destructive=True)
    if confirmation != RESET_CONFIRMATION:
        raise DataManagementError("reset requires explicit confirmation")
    if include_credentials and credential_confirmation != CREDENTIAL_CONFIRMATION:
        raise CredentialScopeError("resetting saved credentials requires explicit credential confirmation")
    verified = verify_backup(source, backup, include_credentials=include_credentials)
    _remove_inventory_entries(
        source, verified.inventory.entries, include_credentials=include_credentials
    )
    return inventory_home(source)


def delete_home(
    home: Path,
    backup: Path,
    *,
    confirmation: str,
    include_credentials: bool = False,
    credential_confirmation: str | None = None,
) -> None:
    """Delete the full resolved home after backup, scope, and confirmation preflight."""

    source = _assert_safe_home(home, must_exist=True, destructive=True)
    if confirmation != DELETE_CONFIRMATION:
        raise DataManagementError("deletion requires explicit confirmation")
    verified = verify_backup(source, backup, include_credentials=include_credentials)
    inventory = verified.inventory
    if inventory.credentials_present and not include_credentials:
        raise CredentialScopeError("full deletion with saved credentials requires separate credential scope confirmation")
    if include_credentials and credential_confirmation != CREDENTIAL_CONFIRMATION:
        raise CredentialScopeError("deleting saved credentials requires explicit credential confirmation")
    _remove_inventory_entries(source, verified.inventory.entries, include_credentials=True)


def move_home(
    home: Path,
    destination: Path,
    backup: Path,
    *,
    confirmation: str,
    include_credentials: bool = False,
    credential_confirmation: str | None = None,
) -> Path:
    """Stage a verified full copy, atomically switch it into place, then remove the source."""

    source = _assert_safe_home(home, must_exist=True, destructive=True)
    target = _assert_safe_destination(destination, source)
    if confirmation != MOVE_CONFIRMATION:
        raise DataManagementError("move requires explicit confirmation")
    verified = verify_backup(source, backup, include_credentials=include_credentials)
    inventory = verified.inventory
    if inventory.credentials_present and not include_credentials:
        raise CredentialScopeError("moving saved credentials requires separate credential scope confirmation")
    if include_credentials and credential_confirmation != CREDENTIAL_CONFIRMATION:
        raise CredentialScopeError("moving saved credentials requires explicit credential confirmation")
    if target.exists():
        raise UnsafeHomeError("move destination must not already exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = _staging_path(target.parent, "move")
    try:
        staging.mkdir(mode=0o700)
        for entry in inventory.entries:
            _copy_regular_file(source / entry.relative_path, staging / entry.relative_path)
        staged = inventory_home(staging)
        if tuple(entry.as_dict() for entry in staged.entries) != tuple(
            entry.as_dict() for entry in inventory.entries
        ):
            raise BackupVerificationError("staged move does not match the original Openlearn home")
        os.replace(staging, target)
        _remove_inventory_entries(source, verified.inventory.entries, include_credentials=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target
