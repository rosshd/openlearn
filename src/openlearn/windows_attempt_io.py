"""Secure Windows file I/O for durable attempt workspaces."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import PureWindowsPath
from typing import Protocol

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
DELETE = 0x00010000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
CREATE_NEW = 1
OPEN_EXISTING = 3
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_DISPOSITION_INFO = 4
ERROR_FILE_EXISTS = 80
ERROR_ALREADY_EXISTS = 183
WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class WindowsSecureIOError(ValueError):
    """A Windows workspace operation could not be completed safely."""


class _FileTime(ctypes.Structure):
    _fields_ = [
        ("low", wintypes.DWORD),
        ("high", wintypes.DWORD),
    ]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("creation_time", _FileTime),
        ("last_access_time", _FileTime),
        ("last_write_time", _FileTime),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


class _FileDispositionInfo(ctypes.Structure):
    _fields_ = [("delete_file", wintypes.BOOL)]


class _WindowsFileAPI(Protocol):
    def open_directory(self, path: str) -> int: ...

    def open_file(self, path: str) -> int: ...

    def create_file(self, path: str) -> int: ...

    def close(self, handle: int) -> None: ...

    def final_path(self, handle: int) -> PureWindowsPath: ...

    def information(self, handle: int) -> _ByHandleFileInformation: ...

    def read(self, handle: int, size: int) -> bytes: ...

    def write(self, handle: int, data: bytes) -> None: ...

    def flush(self, handle: int) -> None: ...

    def delete_on_close(self, handle: int) -> None: ...


class _CtypesWindowsFileAPI:
    def __init__(self) -> None:
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._create_file = kernel32.CreateFileW
            self._close_handle = kernel32.CloseHandle
            self._get_final_path = kernel32.GetFinalPathNameByHandleW
            self._get_information = kernel32.GetFileInformationByHandle
            self._get_size = kernel32.GetFileSizeEx
            self._read_file = kernel32.ReadFile
            self._write_file = kernel32.WriteFile
            self._flush_file = kernel32.FlushFileBuffers
            self._set_information = kernel32.SetFileInformationByHandle
        except (AttributeError, OSError) as exc:
            raise WindowsSecureIOError(
                "secure Windows attempt workspace I/O is unavailable: "
                "required kernel32 file APIs could not be loaded"
            ) from exc

        self._create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._create_file.restype = wintypes.HANDLE
        self._close_handle.argtypes = [wintypes.HANDLE]
        self._close_handle.restype = wintypes.BOOL
        self._get_final_path.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._get_final_path.restype = wintypes.DWORD
        self._get_information.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        ]
        self._get_information.restype = wintypes.BOOL
        self._get_size.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
        self._get_size.restype = wintypes.BOOL
        self._read_file.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self._read_file.restype = wintypes.BOOL
        self._write_file.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self._write_file.restype = wintypes.BOOL
        self._flush_file.argtypes = [wintypes.HANDLE]
        self._flush_file.restype = wintypes.BOOL
        self._set_information.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self._set_information.restype = wintypes.BOOL
        self._invalid_handle = wintypes.HANDLE(-1).value

    def _open(
        self,
        path: str,
        access: int,
        sharing: int,
        disposition: int,
        flags: int,
    ) -> int:
        handle = self._create_file(
            path,
            access,
            sharing,
            None,
            disposition,
            flags,
            None,
        )
        if handle == self._invalid_handle:
            error = ctypes.get_last_error()
            if disposition == CREATE_NEW and error in {ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS}:
                raise FileExistsError(error, "workspace already exists", path)
            raise OSError(error, "CreateFileW failed", path)
        return int(handle)

    def open_directory(self, path: str) -> int:
        return self._open(
            path,
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        )

    def open_file(self, path: str) -> int:
        return self._open(
            path,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT,
        )

    def create_file(self, path: str) -> int:
        return self._open(
            path,
            GENERIC_WRITE | DELETE,
            0,
            CREATE_NEW,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
        )

    def close(self, handle: int) -> None:
        self._close_handle(wintypes.HANDLE(handle))

    def final_path(self, handle: int) -> PureWindowsPath:
        buffer = ctypes.create_unicode_buffer(32768)
        length = self._get_final_path(
            wintypes.HANDLE(handle),
            buffer,
            len(buffer),
            0,
        )
        if length == 0 or length >= len(buffer):
            raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
        return _normalize_final_path(buffer.value)

    def information(self, handle: int) -> _ByHandleFileInformation:
        information = _ByHandleFileInformation()
        if not self._get_information(wintypes.HANDLE(handle), ctypes.byref(information)):
            raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed")
        return information

    def read(self, handle: int, size: int) -> bytes:
        file_size = ctypes.c_longlong()
        if not self._get_size(wintypes.HANDLE(handle), ctypes.byref(file_size)):
            raise OSError(ctypes.get_last_error(), "GetFileSizeEx failed")
        if file_size.value > size:
            raise WindowsSecureIOError("attempt workspace is too large to snapshot")
        remaining = file_size.value
        chunks: list[bytes] = []
        while remaining:
            chunk_size = min(remaining, 64 * 1024)
            buffer = ctypes.create_string_buffer(chunk_size)
            read = wintypes.DWORD()
            if not self._read_file(
                wintypes.HANDLE(handle),
                buffer,
                chunk_size,
                ctypes.byref(read),
                None,
            ):
                raise OSError(ctypes.get_last_error(), "ReadFile failed")
            if read.value == 0:
                break
            chunks.append(buffer.raw[: read.value])
            remaining -= read.value
        return b"".join(chunks)

    def write(self, handle: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            chunk = bytes(view[: min(len(view), 64 * 1024)])
            written = wintypes.DWORD()
            if not self._write_file(
                wintypes.HANDLE(handle),
                chunk,
                len(chunk),
                ctypes.byref(written),
                None,
            ):
                raise OSError(ctypes.get_last_error(), "WriteFile failed")
            if written.value == 0:
                raise OSError("WriteFile made no progress")
            view = view[written.value :]

    def flush(self, handle: int) -> None:
        if not self._flush_file(wintypes.HANDLE(handle)):
            raise OSError(ctypes.get_last_error(), "FlushFileBuffers failed")

    def delete_on_close(self, handle: int) -> None:
        disposition = _FileDispositionInfo(wintypes.BOOL(True))
        if not self._set_information(
            wintypes.HANDLE(handle),
            FILE_DISPOSITION_INFO,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise OSError(ctypes.get_last_error(), "could not roll back unsafe retry file")


def _load_api() -> _WindowsFileAPI:
    return _CtypesWindowsFileAPI()


def _normalize_final_path(value: str) -> PureWindowsPath:
    lowered = value.casefold()
    if lowered.startswith("\\\\?\\unc\\"):
        value = "\\\\" + value[8:]
    elif lowered.startswith("\\\\?\\"):
        value = value[4:]
    return PureWindowsPath(value)


def _safe_component(value: str, field: str) -> str:
    if not value or value in {".", ".."} or PureWindowsPath(value).name != value:
        raise WindowsSecureIOError(f"{field} must be one safe path component")
    stem = value.casefold().split(".", 1)[0]
    if (
        "/" in value
        or "\\" in value
        or any(character in '<>:"|?*' or ord(character) < 32 for character in value)
        or value[-1] in {" ", "."}
        or stem in WINDOWS_RESERVED_NAMES
    ):
        raise WindowsSecureIOError(f"{field} must be one safe path component")
    return value


def _identity(information: _ByHandleFileInformation) -> tuple[int, int, int]:
    return (
        int(information.volume_serial_number),
        int(information.file_index_high),
        int(information.file_index_low),
    )


def _fingerprint(information: _ByHandleFileInformation) -> tuple[int, ...]:
    return (
        *_identity(information),
        int(information.file_size_high),
        int(information.file_size_low),
        int(information.last_write_time.high),
        int(information.last_write_time.low),
    )


def _same_path(left: PureWindowsPath, right: PureWindowsPath) -> bool:
    return str(left).casefold() == str(right).casefold()


def _require_directory(api: _WindowsFileAPI, handle: int, field: str) -> None:
    attributes = int(api.information(handle).attributes)
    if not attributes & FILE_ATTRIBUTE_DIRECTORY or attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise WindowsSecureIOError(f"{field} is not a safe directory")


def _require_regular_file(api: _WindowsFileAPI, handle: int) -> None:
    attributes = int(api.information(handle).attributes)
    if attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT):
        raise WindowsSecureIOError("attempt workspace is not a safe regular file")


def _open_owned_directory(
    api: _WindowsFileAPI,
    topics_root: PureWindowsPath,
    topic: str,
) -> tuple[list[int], PureWindowsPath, tuple[int, int, int]]:
    handles: list[int] = []
    expected_paths = (
        topics_root,
        topics_root / "drills",
        topics_root / "drills" / topic,
    )
    parent_final: PureWindowsPath | None = None
    try:
        for index, expected in enumerate(expected_paths):
            handle = api.open_directory(str(expected))
            handles.append(handle)
            _require_directory(api, handle, "attempt workspace parent")
            final = api.final_path(handle)
            if index and not _same_path(final, parent_final / expected.name):  # type: ignore[operator]
                raise WindowsSecureIOError("attempt workspace parent escaped its owned directory")
            parent_final = final
        return handles, parent_final, _identity(api.information(handles[-1]))
    except Exception:
        for handle in reversed(handles):
            api.close(handle)
        raise


def _recheck_topic_identity(
    api: _WindowsFileAPI,
    topic_path: PureWindowsPath,
    expected_identity: tuple[int, int, int],
    expected_final: PureWindowsPath,
) -> None:
    handle = api.open_directory(str(topic_path))
    try:
        _require_directory(api, handle, "attempt workspace parent")
        if (
            _identity(api.information(handle)) != expected_identity
            or not _same_path(api.final_path(handle), expected_final)
        ):
            raise WindowsSecureIOError("attempt workspace parent changed during file access")
    finally:
        api.close(handle)


def read_workspace_file(
    topics_root: os.PathLike[str] | str,
    topic: str,
    filename: str,
    *,
    max_bytes: int,
    _api: _WindowsFileAPI | None = None,
) -> bytes:
    """Read one owned workspace through a stable, no-follow Windows handle."""
    if max_bytes < 0:
        raise WindowsSecureIOError("workspace size limit is invalid")
    topic = _safe_component(topic, "topic")
    filename = _safe_component(filename, "workspace filename")
    root = PureWindowsPath(os.fspath(topics_root))
    api = _api or _load_api()
    handles: list[int] = []
    try:
        handles, topic_final, topic_identity = _open_owned_directory(api, root, topic)
        topic_path = root / "drills" / topic
        workspace_path = topic_path / filename
        file_handle = api.open_file(str(workspace_path))
        try:
            _require_regular_file(api, file_handle)
            if not _same_path(api.final_path(file_handle), topic_final / filename):
                raise WindowsSecureIOError("attempt workspace escaped its owned directory")
            before = _fingerprint(api.information(file_handle))
            _recheck_topic_identity(api, topic_path, topic_identity, topic_final)
            data = api.read(file_handle, max_bytes)
            if _fingerprint(api.information(file_handle)) != before:
                raise WindowsSecureIOError("attempt workspace changed during snapshot")
            _recheck_topic_identity(api, topic_path, topic_identity, topic_final)
            return data
        finally:
            api.close(file_handle)
    except WindowsSecureIOError:
        raise
    except OSError as exc:
        raise WindowsSecureIOError(
            f"attempt workspace could not be read safely: {exc}"
        ) from exc
    finally:
        for handle in reversed(handles):
            api.close(handle)


def create_workspace_file_exclusive(
    topics_root: os.PathLike[str] | str,
    topic: str,
    filename: str,
    data: bytes,
    *,
    _api: _WindowsFileAPI | None = None,
) -> None:
    """Create one retry workspace exclusively and durably under its owned topic."""
    topic = _safe_component(topic, "topic")
    filename = _safe_component(filename, "workspace filename")
    root = PureWindowsPath(os.fspath(topics_root))
    api = _api or _load_api()
    handles: list[int] = []
    file_handle: int | None = None
    completed = False
    operation_error: BaseException | None = None
    try:
        handles, topic_final, topic_identity = _open_owned_directory(api, root, topic)
        topic_path = root / "drills" / topic
        workspace_path = topic_path / filename
        file_handle = api.create_file(str(workspace_path))
        _require_regular_file(api, file_handle)
        if not _same_path(api.final_path(file_handle), topic_final / filename):
            raise WindowsSecureIOError("retry workspace escaped its owned directory")
        _recheck_topic_identity(api, topic_path, topic_identity, topic_final)
        api.write(file_handle, data)
        api.flush(file_handle)
        _recheck_topic_identity(api, topic_path, topic_identity, topic_final)
        completed = True
    except (OSError, WindowsSecureIOError) as exc:
        operation_error = exc
        if isinstance(exc, WindowsSecureIOError):
            raise
        raise WindowsSecureIOError(f"retry workspace could not be created safely: {exc}") from exc
    finally:
        if file_handle is not None:
            if not completed:
                try:
                    api.delete_on_close(file_handle)
                except OSError as cleanup_error:
                    detail = (
                        f" after {operation_error}" if operation_error is not None else ""
                    )
                    raise WindowsSecureIOError(
                        "unsafe retry workspace could not be rolled back"
                        f"{detail}: {cleanup_error}"
                    ) from cleanup_error
            api.close(file_handle)
        for handle in reversed(handles):
            api.close(handle)
