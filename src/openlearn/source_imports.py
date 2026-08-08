from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from openlearn import cli
from openlearn.constants import QUICK_LEARN_MAX_FILE_BYTES
from openlearn.models import PendingContext

SourceKind: TypeAlias = Literal["file", "folder", "github"]
ImportStatus: TypeAlias = Literal["imported", "skipped", "failed"]
COURSE_SOURCES_METADATA_KEY = "course_sources"
LOCAL_EXTRACT_CHAR_LIMIT = 12000
UNSUPPORTED_ARCHIVE_SUFFIXES = frozenset(
    {
        ".7z",
        ".bz2",
        ".gz",
        ".rar",
        ".tar",
        ".tgz",
        ".xz",
        ".zip",
    }
)
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
)
_PROVIDER_SECRET_PATTERN = re.compile(
    r"\b(?:"
    r"sk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r")\b"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|auth(?:orization)?|"
    r"client[_-]?secret|password|private[_-]?key|secret(?:[_-]?key)?)\b"
    r"\s*[:=]\s*(?:Bearer\s+)?[\"']?([^\s\"']{20,})",
    re.IGNORECASE,
)
_SECRET_LOOKALIKE_MARKERS = (
    "${",
    "{{",
    "changeme",
    "dummy",
    "example",
    "placeholder",
    "replace",
    "sample",
    "your-",
    "your_",
)
_SECRET_LOOKALIKE_PREFIXES = ("fake-", "fake_", "test-", "test_")
_LIKELY_SECRET_MESSAGE = "Source may contain a credential or private key."


@dataclass(frozen=True)
class LocalFileSource:
    path: Path
    filename: str | None = None


@dataclass(frozen=True)
class LocalFolderSource:
    path: Path


@dataclass(frozen=True)
class PublicGitHubSource:
    url: str


CourseSourceInput: TypeAlias = LocalFileSource | LocalFolderSource | PublicGitHubSource


@dataclass(frozen=True)
class CourseSourceImportRequest:
    course_slug: str
    source: CourseSourceInput
    model: str | None = None


@dataclass(frozen=True)
class ImportDetail:
    status: ImportStatus
    label: str
    context_file: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class CourseSource:
    source_id: str
    kind: SourceKind
    label: str
    context_file: str
    summary_file: str
    checksum: str


@dataclass(frozen=True)
class CourseSourceImportResult:
    kind: SourceKind
    sources: tuple[CourseSource, ...]
    imported: tuple[ImportDetail, ...] = ()
    skipped: tuple[ImportDetail, ...] = ()
    failed: tuple[ImportDetail, ...] = ()


def import_course_source(request: CourseSourceImportRequest) -> CourseSourceImportResult:
    """Import one bounded source into an existing course without UI formatting."""
    cli.read_topic(request.course_slug)
    source = request.source
    if isinstance(source, LocalFileSource):
        return _import_file(request.course_slug, source, request.model)
    if isinstance(source, LocalFolderSource):
        return _import_folder(request.course_slug, source, request.model)
    return _import_github(request.course_slug, source, request.model)


def list_course_sources(course_slug: str) -> tuple[CourseSource, ...]:
    metadata = cli.read_topic(course_slug).metadata
    raw_sources = metadata.get(COURSE_SOURCES_METADATA_KEY)
    if not isinstance(raw_sources, list):
        return ()
    sources: list[CourseSource] = []
    for value in raw_sources:
        parsed = _parse_source(value)
        if parsed is not None:
            sources.append(parsed)
    return tuple(sources)


def _import_file(
    course_slug: str, source: LocalFileSource, model: str | None
) -> CourseSourceImportResult:
    kind: SourceKind = "file"
    display_name = source.filename or source.path.name
    if not display_name or Path(display_name).name != display_name:
        return _failure(course_slug, kind, "Selected file", "Unsupported or unsafe file name.")
    if Path(display_name).suffix.lower() in UNSUPPORTED_ARCHIVE_SUFFIXES:
        return _failure(course_slug, kind, display_name, "Archive imports are not supported.")
    if not _safe_display_name(display_name):
        return _failure(course_slug, kind, "Selected file", "Unsupported or unsafe file name.")
    try:
        lexical = source.path.expanduser().absolute()
        if lexical.is_symlink():
            raise cli.OpenLearnError("symlink")
        resolved = lexical.resolve(strict=True)
        snapshot = cli.snapshot_source_file(resolved.parent, resolved)
        if len(snapshot.data) > QUICK_LEARN_MAX_FILE_BYTES:
            return _failure(course_slug, kind, display_name, "File exceeds the import size limit.")
        context = cli.pending_context_from_snapshot(snapshot, output_func=lambda _text: None)
    except (OSError, UnicodeDecodeError, cli.OpenLearnError):
        return _failure(course_slug, kind, display_name, "File could not be read safely.")
    context = PendingContext(display_name, context.text)
    detail = _import_context(
        course_slug,
        kind,
        display_name,
        context,
        model,
        checksum=snapshot.checksum,
    )
    return _result(course_slug, kind, [detail])


def _import_folder(
    course_slug: str, source: LocalFolderSource, model: str | None
) -> CourseSourceImportResult:
    kind: SourceKind = "folder"
    label = source.path.name or "Selected folder"
    try:
        directory = source.path.expanduser().resolve(strict=True)
        if not directory.is_dir():
            raise cli.OpenLearnError("not a directory")
        contexts = cli.quick_directory_contexts(directory, output_func=lambda _text: None)
    except (OSError, RuntimeError, cli.OpenLearnError):
        return _failure(
            course_slug,
            kind,
            label,
            "Folder contained no supported, readable sources.",
        )
    return _import_selection(course_slug, kind, contexts, model)


def _import_github(
    course_slug: str, source: PublicGitHubSource, model: str | None
) -> CourseSourceImportResult:
    kind: SourceKind = "github"
    parts = cli.github_repository_parts(source.url)
    if parts is None:
        return _failure(
            course_slug,
            kind,
            "Public GitHub repository",
            "Enter a public GitHub repository URL.",
        )
    label = f"{parts[0]}/{parts[1]}"
    canonical_url = f"https://github.com/{parts[0]}/{parts[1]}"
    try:
        contexts = cli.quick_source_contexts(canonical_url, kind, output_func=lambda _text: None)
    except (OSError, cli.OpenLearnError):
        return _failure(
            course_slug,
            kind,
            label,
            "Repository could not be imported.",
        )
    return _import_selection(course_slug, kind, contexts, model)


def _import_selection(
    course_slug: str,
    kind: SourceKind,
    contexts: list[PendingContext],
    model: str | None,
) -> CourseSourceImportResult:
    details = _manifest_skips(contexts[0]) if contexts else []
    for context in contexts[1:]:
        label = _context_label(context)
        details.append(
            _import_context(
                course_slug,
                kind,
                label,
                context,
                model,
                checksum=context.source_checksum,
            )
        )
    return _result(course_slug, kind, details)


def _import_context(
    course_slug: str,
    kind: SourceKind,
    label: str,
    context: PendingContext,
    model: str | None,
    *,
    checksum: str | None = None,
) -> ImportDetail:
    checksum = checksum or cli._text_checksum(context.text)
    if checksum in cli.imported_checksums(cli.read_topic(course_slug).metadata):
        return ImportDetail("skipped", label, message="Already imported.")
    if _contains_likely_secret(context.text):
        return ImportDetail("failed", label, message=_LIKELY_SECRET_MESSAGE)
    saved: Path | None = None
    summary: Path | None = None
    try:
        saved = cli.write_context_text(course_slug, context.filename, context.text)
        summary = _write_local_extract(course_slug, saved)
        persisted = _persist_source(
            course_slug,
            CourseSource(
                source_id=f"{kind}:{checksum}",
                kind=kind,
                label=label,
                context_file=saved.name,
                summary_file=summary.name,
                checksum=checksum,
            ),
        )
        if not persisted:
            saved.unlink(missing_ok=True)
            summary.unlink(missing_ok=True)
            return ImportDetail("skipped", label, message="Already imported.")
    except Exception:
        if saved is not None:
            saved.unlink(missing_ok=True)
        if summary is not None:
            summary.unlink(missing_ok=True)
        return ImportDetail("failed", label, message="Source could not be imported.")
    return ImportDetail("imported", label, context_file=saved.name)


def _write_local_extract(course_slug: str, source: Path) -> Path:
    """Write a deterministic local excerpt without contacting a model provider."""
    text = source.read_text(encoding="utf-8")
    clipped = text[:LOCAL_EXTRACT_CHAR_LIMIT].rstrip()
    truncation = "\n\n[Local extract truncated.]" if len(text) > LOCAL_EXTRACT_CHAR_LIMIT else ""
    extract = (
        "Local source extract. This file was created without contacting a model provider.\n\n"
        f"{clipped}{truncation}\n"
    )
    target = cli.context_summary_path(course_slug, source)
    cli.write_text_atomic(target, extract)
    return target


def _persist_source(course_slug: str, source: CourseSource) -> bool:
    path = cli.topic_path(course_slug)
    with cli.file_lock(path):
        metadata, body = cli.parse_topic(path.read_text(encoding="utf-8"))
        if source.checksum in cli.imported_checksums(metadata):
            return False
        metadata = dict(metadata)
        checksums = list(cli.imported_checksums(metadata))
        checksums.append(source.checksum)
        metadata["imported_checksums"] = sorted(checksums)
        raw_sources = metadata.get(COURSE_SOURCES_METADATA_KEY)
        sources = list(raw_sources) if isinstance(raw_sources, list) else []
        sources.append(
            {
                "source_id": source.source_id,
                "kind": source.kind,
                "label": source.label,
                "context_file": source.context_file,
                "summary_file": source.summary_file,
                "checksum": source.checksum,
            }
        )
        metadata[COURSE_SOURCES_METADATA_KEY] = sources
        cli.write_text_atomic(path, cli.format_topic(metadata, body))
    return True


def _parse_source(value: object) -> CourseSource | None:
    if not isinstance(value, dict):
        return None
    fields = {
        key: value.get(key)
        for key in (
            "source_id",
            "kind",
            "label",
            "context_file",
            "summary_file",
            "checksum",
        )
    }
    if not all(isinstance(item, str) and item for item in fields.values()):
        return None
    kind = fields["kind"]
    if kind not in {"file", "folder", "github"}:
        return None
    if any(Path(str(fields[key])).name != fields[key] for key in ("context_file", "summary_file")):
        return None
    return CourseSource(
        source_id=str(fields["source_id"]),
        kind=kind,
        label=str(fields["label"]),
        context_file=str(fields["context_file"]),
        summary_file=str(fields["summary_file"]),
        checksum=str(fields["checksum"]),
    )


def _manifest_skips(manifest: PendingContext) -> list[ImportDetail]:
    marker = "Skipped after candidate filtering:\n"
    if marker not in manifest.text:
        return []
    skipped: list[ImportDetail] = []
    for line in manifest.text.split(marker, 1)[1].splitlines():
        value = line.removeprefix("- ").strip()
        if not value or value == "none":
            continue
        label, separator, reason = value.rpartition(": ")
        skipped.append(
            ImportDetail(
                "skipped",
                label if separator else value,
                message=reason if separator else "Excluded by import safety rules.",
            )
        )
    return skipped


def _context_label(context: PendingContext) -> str:
    first_line = context.text.splitlines()[0] if context.text else ""
    prefix = "Source path: "
    if first_line.startswith(prefix):
        return first_line.removeprefix(prefix).strip() or context.filename
    return context.filename


def _safe_display_name(value: str) -> bool:
    if not value or Path(value).name != value:
        return False
    candidate = Path(value)
    return cli.quick_source_file_allowed(candidate, candidate) and candidate.suffix.lower() in {
        ".txt",
        ".md",
        ".pdf",
        ".docx",
    }


def _contains_likely_secret(text: str) -> bool:
    """Detect high-confidence credential material without retaining the match."""
    if _PRIVATE_KEY_PATTERN.search(text) is not None:
        return True
    if _PROVIDER_SECRET_PATTERN.search(text) is not None:
        return True
    for assignment in _SECRET_ASSIGNMENT_PATTERN.finditer(text):
        if not _is_secret_lookalike(assignment.group(1)):
            return True
    return False


def _is_secret_lookalike(value: str) -> bool:
    normalized = value.casefold()
    return normalized.startswith(_SECRET_LOOKALIKE_PREFIXES) or any(
        marker in normalized for marker in _SECRET_LOOKALIKE_MARKERS
    )


def _failure(
    course_slug: str, kind: SourceKind, label: str, message: str
) -> CourseSourceImportResult:
    return CourseSourceImportResult(
        kind=kind,
        sources=list_course_sources(course_slug),
        failed=(ImportDetail("failed", label, message=message),),
    )


def _result(
    course_slug: str, kind: SourceKind, details: list[ImportDetail]
) -> CourseSourceImportResult:
    return CourseSourceImportResult(
        kind=kind,
        sources=list_course_sources(course_slug),
        imported=tuple(detail for detail in details if detail.status == "imported"),
        skipped=tuple(detail for detail in details if detail.status == "skipped"),
        failed=tuple(detail for detail in details if detail.status == "failed"),
    )
