"""Launch the loopback-only openlearn web application."""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import uvicorn

from openlearn.config import project_home

from .app import create_app
from .security import BrowserSecurity, NAMESPACE_PREFIX, is_valid_url_namespace


class WebLaunchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServerRecord:
    pid: int
    url: str
    lease_id: str
    access_token: str = field(repr=False)
    url_namespace: str = field(repr=False)


OWNER_RECORD_TIMEOUT_SECONDS = 1.0
OWNER_HEALTH_TIMEOUT_SECONDS = 3.0
BROWSER_READY_TIMEOUT_SECONDS = 10.0


class ExistingWebServer(WebLaunchError):
    def __init__(self, record: ServerRecord) -> None:
        super().__init__("openlearn web is already running for this data directory")
        self.record = record


def _validated_loopback_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    hostname = parsed.hostname
    if (
        parsed.scheme != "http"
        or hostname is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None
    if hostname.lower() == "localhost":
        normalized_host = "localhost"
    else:
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return None
        if not address.is_loopback:
            return None
        normalized_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"http://{normalized_host}:{port}/"


def _read_record(path: Path) -> tuple[int, ServerRecord | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, None
    if not isinstance(value, dict):
        return 0, None
    raw_pid = value.get("pid")
    pid = raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else 0
    url = _validated_loopback_url(value.get("url"))
    lease_id = value.get("lease_id")
    access_token = value.get("access_token")
    url_namespace = value.get("url_namespace")
    if (
        pid <= 0
        or url is None
        or not isinstance(lease_id, str)
        or len(lease_id) != 32
        or any(character not in "0123456789abcdef" for character in lease_id)
        or not isinstance(access_token, str)
        or not 32 <= len(access_token) <= 128
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in access_token
        )
        or not is_valid_url_namespace(url_namespace)
    ):
        return pid, None
    return pid, ServerRecord(
        pid=pid,
        url=url,
        lease_id=lease_id,
        access_token=access_token,
        url_namespace=url_namespace,
    )


def _write_candidate(root: Path, record: ServerRecord) -> Path:
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".web-server.", dir=root, delete=False
        ) as handle:
            path = Path(handle.name)
            os.chmod(path, 0o600)
            json.dump(
                {
                    "pid": record.pid,
                    "url": record.url,
                    "lease_id": record.lease_id,
                    "access_token": record.access_token,
                    "url_namespace": record.url_namespace,
                },
                handle,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise
    assert path is not None
    return path


def _acquire_owner_lock(handle: BinaryIO) -> bool:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
    else:
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


def _release_owner_lock(handle: BinaryIO) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_owner_token(handle: BinaryIO, lease_id: str) -> None:
    handle.seek(0)
    handle.truncate()
    handle.write(lease_id.encode("ascii"))
    handle.flush()
    os.fsync(handle.fileno())


def _read_owner_token(handle: BinaryIO) -> str | None:
    handle.seek(0)
    value = handle.read(64).decode("ascii", errors="ignore").strip()
    if len(value) != 32 or any(character not in "0123456789abcdef" for character in value):
        return None
    return value


def _wait_for_owner_record(
    lock_handle: BinaryIO,
    path: Path,
    *,
    timeout: float = OWNER_RECORD_TIMEOUT_SECONDS,
) -> ServerRecord | None:
    deadline = time.monotonic() + timeout
    while True:
        owner_token = _read_owner_token(lock_handle)
        _owner_pid, current = _read_record(path)
        if current is not None and owner_token == current.lease_id:
            return current
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.025)


@contextmanager
def server_lease(
    home: Path | None = None,
    *,
    url: str = "http://127.0.0.1:8765/",
) -> Iterator[ServerRecord]:
    """Atomically claim one web-server process for an openlearn home."""
    normalized_url = _validated_loopback_url(url)
    if normalized_url is None:
        raise WebLaunchError("web server URL must be a valid loopback HTTP URL")
    root = project_home() if home is None else home.resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".web-server.json"
    lock_path = root / ".web-server.lock"
    record = ServerRecord(
        pid=os.getpid(),
        url=normalized_url,
        lease_id=uuid4().hex,
        access_token=secrets.token_urlsafe(32),
        url_namespace=f"{NAMESPACE_PREFIX}{secrets.token_urlsafe(24)}",
    )
    lock_handle = lock_path.open("a+b")
    os.chmod(lock_path, 0o600)
    if lock_path.stat().st_size == 0:
        lock_handle.write(b"\0")
        lock_handle.flush()
    locked = _acquire_owner_lock(lock_handle)
    try:
        if not locked:
            current = _wait_for_owner_record(lock_handle, path)
            if current is None:
                raise WebLaunchError("openlearn web has a live owner but an invalid server record")
            raise ExistingWebServer(current)

        _write_owner_token(lock_handle, record.lease_id)
        candidate = _write_candidate(root, record)
        try:
            os.replace(candidate, path)
        except OSError as error:
            raise WebLaunchError("could not claim the openlearn web server lease") from error
        finally:
            candidate.unlink(missing_ok=True)

        try:
            yield record
        finally:
            owner_pid, current = _read_record(path)
            if owner_pid == record.pid and current == record:
                path.unlink(missing_ok=True)
    finally:
        if locked:
            _release_owner_lock(lock_handle)
        lock_handle.close()


def _probe_server(record: ServerRecord, *, timeout: float = 0.75) -> bool:
    normalized = _validated_loopback_url(record.url)
    if normalized is None:
        return False
    parsed = urlsplit(normalized)
    hostname = parsed.hostname
    port = parsed.port
    assert hostname is not None and port is not None
    connection = http.client.HTTPConnection(hostname, port, timeout=timeout)
    try:
        connection.request(
            "GET",
            "/health",
            headers={
                "Host": parsed.netloc,
                "X-Openlearn-Capability": record.access_token,
            },
        )
        response = connection.getresponse()
        response.read()
        return response.status == 200
    except OSError:
        return False
    finally:
        connection.close()


def _wait_for_healthy_owner(
    home: Path,
    record: ServerRecord,
    probe: Callable[[ServerRecord], bool],
    *,
    timeout: float = OWNER_HEALTH_TIMEOUT_SECONDS,
) -> ServerRecord | None:
    deadline = time.monotonic() + timeout
    current = record
    lock_path = home / ".web-server.lock"
    record_path = home / ".web-server.json"
    while True:
        if probe(current):
            return current
        try:
            with lock_path.open("rb") as handle:
                owner_token = _read_owner_token(handle)
        except OSError:
            owner_token = None
        _owner_pid, refreshed = _read_record(record_path)
        if refreshed is not None and refreshed.lease_id == owner_token:
            current = refreshed
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def _bootstrap_url(record: ServerRecord) -> str:
    return f"{record.url}?{urlencode({'access_token': record.access_token})}"


def _open_browser_when_ready(
    record: ServerRecord,
    probe: Callable[[ServerRecord], bool],
    opener: Callable[[str], object],
    cancelled: threading.Event,
    *,
    timeout: float = BROWSER_READY_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout
    while not cancelled.is_set():
        if probe(record):
            if not cancelled.is_set():
                opener(_bootstrap_url(record))
            return
        if time.monotonic() >= deadline:
            return
        cancelled.wait(0.05)


def run(
    *,
    port: int = 8765,
    open_browser: bool = True,
    home: Path | None = None,
    probe: Callable[[ServerRecord], bool] = _probe_server,
    opener: Callable[[str], object] = webbrowser.open,
    notifier: Callable[[str], object] = print,
    server_runner: Callable[..., object] = uvicorn.run,
) -> None:
    if not 1 <= port <= 65535:
        raise WebLaunchError("port must be between 1 and 65535")
    address = f"http://127.0.0.1:{port}/"
    try:
        with server_lease(home, url=address) as record:
            app = create_app(
                security=BrowserSecurity.local(
                    access_token=record.access_token,
                    url_namespace=record.url_namespace,
                )
            )
            browser_cancelled = threading.Event()
            browser_thread: threading.Thread | None = None
            if open_browser:
                browser_thread = threading.Thread(
                    target=_open_browser_when_ready,
                    args=(record, probe, opener, browser_cancelled),
                    name="openlearn-browser-readiness",
                    daemon=True,
                )
                browser_thread.start()
            else:
                notifier(f"Open openlearn: {_bootstrap_url(record)}")
            try:
                server_runner(
                    app,
                    host="127.0.0.1",
                    port=port,
                    log_level="info",
                    access_log=False,
                )
            finally:
                browser_cancelled.set()
                if browser_thread is not None:
                    browser_thread.join(timeout=0.25)
    except ExistingWebServer as error:
        root = project_home() if home is None else home.resolve()
        existing = _wait_for_healthy_owner(root, error.record, probe)
        if existing is None:
            raise WebLaunchError(
                "openlearn web has a live owner, but its loopback health check failed"
            ) from error
        if open_browser:
            opener(_bootstrap_url(existing))
        else:
            notifier(f"Open openlearn: {_bootstrap_url(existing)}")
