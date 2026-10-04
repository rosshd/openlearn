from __future__ import annotations

import os
import subprocess
import sys
from unittest import mock

import pytest

from openlearn import tutor_service


@pytest.mark.parametrize("alive", [False, True])
def test_windows_liveness_never_sends_console_events(alive: bool) -> None:
    with (
        mock.patch.object(sys, "platform", "win32"),
        mock.patch.object(tutor_service, "_windows_process_is_alive", return_value=alive) as probe,
        mock.patch.object(os, "kill") as kill,
    ):
        assert tutor_service._process_is_alive(4242) is alive
    probe.assert_called_once_with(4242)
    kill.assert_not_called()


@pytest.mark.parametrize("wait_result, alive", [(0, False), (258, True), (0xFFFFFFFF, True)])
def test_windows_liveness_queries_and_closes_handle(wait_result: int, alive: bool) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = mock.Mock()
    kernel.OpenProcess.return_value = 0x100000001
    kernel.WaitForSingleObject.return_value = wait_result
    with mock.patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
        assert tutor_service._windows_process_is_alive(4242) is alive
    kernel.OpenProcess.assert_called_once_with(0x100000, False, 4242)
    kernel.WaitForSingleObject.assert_called_once_with(0x100000001, 0)
    kernel.CloseHandle.assert_called_once_with(0x100000001)
    assert kernel.OpenProcess.restype is wintypes.HANDLE
    assert kernel.WaitForSingleObject.argtypes == [wintypes.HANDLE, wintypes.DWORD]


@pytest.mark.parametrize("error, alive", [(87, False), (5, True), (6, True)])
def test_windows_liveness_handles_unavailable_process(error: int, alive: bool) -> None:
    import ctypes

    kernel = mock.Mock()
    kernel.OpenProcess.return_value = None
    with (
        mock.patch.object(ctypes, "WinDLL", return_value=kernel, create=True),
        mock.patch.object(ctypes, "get_last_error", return_value=error, create=True),
    ):
        assert tutor_service._windows_process_is_alive(4242) is alive
    kernel.WaitForSingleObject.assert_not_called()
    kernel.CloseHandle.assert_not_called()


@pytest.mark.parametrize("error, alive", [(None, True), (ProcessLookupError(), False)])
def test_posix_liveness_uses_signal_zero(error: OSError | None, alive: bool) -> None:
    with (
        mock.patch.object(sys, "platform", "linux"),
        mock.patch.object(os, "kill", side_effect=error) as kill,
    ):
        assert tutor_service._process_is_alive(4242) is alive
    kill.assert_called_once_with(4242, 0)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows process handle check")
def test_native_windows_probe_preserves_live_child_and_detects_exit() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                               stdin=subprocess.PIPE)
    try:
        assert tutor_service._process_is_alive(process.pid)
        assert process.poll() is None
        process.communicate(input=b"", timeout=10)
        assert process.returncode == 0
        assert not tutor_service._process_is_alive(process.pid)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
