from __future__ import annotations

import unittest
from collections import defaultdict
from pathlib import PureWindowsPath
from unittest import mock

from openlearn import windows_attempt_io


def _information(
    *,
    identity: tuple[int, int, int],
    attributes: int,
    size: int = 0,
    write_time: int = 1,
) -> windows_attempt_io._ByHandleFileInformation:
    value = windows_attempt_io._ByHandleFileInformation()
    value.volume_serial_number, value.file_index_high, value.file_index_low = identity
    value.attributes = attributes
    value.file_size_high = size >> 32
    value.file_size_low = size & 0xFFFFFFFF
    value.last_write_time.high = write_time >> 32
    value.last_write_time.low = write_time & 0xFFFFFFFF
    return value


class FakeWindowsAPI:
    def __init__(self) -> None:
        self.next_handle = 10
        self.paths: dict[int, PureWindowsPath] = {}
        self.identities: dict[int, tuple[int, int, int]] = {}
        self.attributes: dict[int, int] = {}
        self.contents: dict[int, bytes] = {}
        self.open_counts: defaultdict[str, int] = defaultdict(int)
        self.closed: list[int] = []
        self.deleted: list[int] = []
        self.flushed: list[int] = []
        self.topic_identities = [(1, 0, 3)]

    def _new(
        self,
        path: str,
        *,
        attributes: int,
        identity: tuple[int, int, int],
        content: bytes = b"",
    ) -> int:
        self.next_handle += 1
        handle = self.next_handle
        self.paths[handle] = PureWindowsPath(path)
        self.identities[handle] = identity
        self.attributes[handle] = attributes
        self.contents[handle] = content
        return handle

    def open_directory(self, path: str) -> int:
        normalized = str(PureWindowsPath(path))
        self.open_counts[normalized] += 1
        if normalized.casefold().endswith("\\drills\\python"):
            index = min(self.open_counts[normalized] - 1, len(self.topic_identities) - 1)
            identity = self.topic_identities[index]
        elif normalized.casefold().endswith("\\drills"):
            identity = (1, 0, 2)
        else:
            identity = (1, 0, 1)
        return self._new(
            normalized,
            attributes=windows_attempt_io.FILE_ATTRIBUTE_DIRECTORY,
            identity=identity,
        )

    def open_file(self, path: str) -> int:
        return self._new(path, attributes=0, identity=(1, 0, 4), content=b"solution")

    def create_file(self, path: str) -> int:
        return self._new(path, attributes=0, identity=(1, 0, 5))

    def close(self, handle: int) -> None:
        self.closed.append(handle)

    def final_path(self, handle: int) -> PureWindowsPath:
        return self.paths[handle]

    def information(self, handle: int) -> windows_attempt_io._ByHandleFileInformation:
        return _information(
            identity=self.identities[handle],
            attributes=self.attributes[handle],
            size=len(self.contents[handle]),
        )

    def read(self, handle: int, size: int) -> bytes:
        content = self.contents[handle]
        if len(content) > size:
            raise windows_attempt_io.WindowsSecureIOError(
                "attempt workspace is too large to snapshot"
            )
        return content

    def write(self, handle: int, data: bytes) -> None:
        self.contents[handle] = data

    def flush(self, handle: int) -> None:
        self.flushed.append(handle)

    def delete_on_close(self, handle: int) -> None:
        self.deleted.append(handle)


class WindowsAttemptIOTests(unittest.TestCase):
    root = PureWindowsPath(r"C:\openlearn\learning-topics")

    def test_reads_normal_workspace_through_stable_handles(self) -> None:
        api = FakeWindowsAPI()

        data = windows_attempt_io.read_workspace_file(
            self.root,
            "python",
            "solution.py",
            max_bytes=1024,
            _api=api,
        )

        self.assertEqual(data, b"solution")
        self.assertEqual(api.deleted, [])
        self.assertGreaterEqual(len(api.closed), 6)

    def test_read_rejects_parent_swap_after_file_open(self) -> None:
        api = FakeWindowsAPI()
        api.topic_identities = [(1, 0, 3), (1, 0, 99)]

        with self.assertRaisesRegex(
            windows_attempt_io.WindowsSecureIOError,
            "parent changed",
        ):
            windows_attempt_io.read_workspace_file(
                self.root,
                "python",
                "solution.py",
                max_bytes=1024,
                _api=api,
            )

    def test_read_rejects_reparse_point_file(self) -> None:
        api = FakeWindowsAPI()
        original_open = api.open_file

        def open_reparse(path: str) -> int:
            handle = original_open(path)
            api.attributes[handle] = windows_attempt_io.FILE_ATTRIBUTE_REPARSE_POINT
            return handle

        api.open_file = open_reparse  # type: ignore[method-assign]

        with self.assertRaisesRegex(
            windows_attempt_io.WindowsSecureIOError,
            "safe regular file",
        ):
            windows_attempt_io.read_workspace_file(
                self.root,
                "python",
                "solution.py",
                max_bytes=1024,
                _api=api,
            )

    def test_creates_and_flushes_normal_retry_exclusively(self) -> None:
        api = FakeWindowsAPI()

        windows_attempt_io.create_workspace_file_exclusive(
            self.root,
            "python",
            "solution-retry.py",
            b"new solution",
            _api=api,
        )

        created = api.flushed[0]
        self.assertEqual(api.contents[created], b"new solution")
        self.assertEqual(api.flushed, [created])
        self.assertEqual(api.deleted, [])

    def test_create_rolls_back_open_file_when_parent_is_swapped(self) -> None:
        api = FakeWindowsAPI()
        api.topic_identities = [(1, 0, 3), (1, 0, 99)]

        with self.assertRaisesRegex(
            windows_attempt_io.WindowsSecureIOError,
            "parent changed",
        ):
            windows_attempt_io.create_workspace_file_exclusive(
                self.root,
                "python",
                "solution-retry.py",
                b"new solution",
                _api=api,
            )

        self.assertEqual(len(api.deleted), 1)
        self.assertIn(api.deleted[0], api.closed)

    def test_unsafe_filename_is_rejected_before_loading_windows_api(self) -> None:
        with mock.patch.object(windows_attempt_io, "_load_api") as load:
            for filename in (r"..\escape.py", "solution.py:answers", "CON.txt", "trailing."):
                with self.subTest(filename=filename):
                    with self.assertRaisesRegex(
                        windows_attempt_io.WindowsSecureIOError,
                        "one safe path component",
                    ):
                        windows_attempt_io.read_workspace_file(
                            self.root,
                            "python",
                            filename,
                            max_bytes=1024,
                        )

        load.assert_not_called()

    def test_missing_windows_apis_fail_closed_with_actionable_error(self) -> None:
        with mock.patch.object(
            windows_attempt_io.ctypes,
            "WinDLL",
            side_effect=OSError("missing"),
            create=True,
        ):
            with self.assertRaisesRegex(
                windows_attempt_io.WindowsSecureIOError,
                "required kernel32 file APIs",
            ):
                windows_attempt_io._load_api()


if __name__ == "__main__":
    unittest.main()
