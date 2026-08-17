from __future__ import annotations

import builtins
import json
import os
import stat
import threading
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from openlearn import cli
from openlearn.web import launcher

LIVE_LEASE_ID = "a" * 32
LIVE_ACCESS_TOKEN = "test-capability-token-value-1234567890"
LIVE_URL_NAMESPACE = "/_openlearn/test-route-namespace-value-1234567890"


class FakeListener:
    def __init__(self, port: int) -> None:
        self.port = port
        self.closed = False

    def close(self) -> None:
        self.closed = True


def fake_listener_factory(auto_port: int = launcher.DEFAULT_PORT):
    @contextmanager
    def reserve(requested_port: int | None) -> Iterator[tuple[FakeListener, int]]:
        selected_port = auto_port if requested_port is None else requested_port
        listener = FakeListener(selected_port)
        try:
            yield listener, selected_port
        finally:
            listener.close()

    return reserve


def write_live_record(home: Path, *, pid: int = 4242, url: str = "http://127.0.0.1:9191/") -> Path:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".web-server.lock").write_bytes(b"\0" + LIVE_LEASE_ID.encode("ascii"))
    lease = home / ".web-server.json"
    lease.write_text(
        json.dumps(
            {
                "pid": pid,
                "url": url,
                "lease_id": LIVE_LEASE_ID,
                "access_token": LIVE_ACCESS_TOKEN,
                "url_namespace": LIVE_URL_NAMESPACE,
            }
        ),
        encoding="utf-8",
    )
    return lease


def test_server_lease_is_exclusive_private_and_removed(tmp_path: Path) -> None:
    with launcher.server_lease(tmp_path, url="http://127.0.0.1:9123/") as record:
        lease = tmp_path / ".web-server.json"
        assert json.loads(lease.read_text(encoding="utf-8")) == {
            "pid": os.getpid(),
            "url": "http://127.0.0.1:9123/",
            "lease_id": record.lease_id,
            "access_token": record.access_token,
            "url_namespace": record.url_namespace,
        }
        assert record.pid == os.getpid()
        assert record.url == "http://127.0.0.1:9123/"
        if os.name != "nt":
            assert stat.S_IMODE(lease.stat().st_mode) == 0o600
        with pytest.raises(launcher.ExistingWebServer, match="already running"):
            with launcher.server_lease(tmp_path):
                pass
    assert not lease.exists()


def test_server_record_and_launcher_errors_never_render_access_token() -> None:
    record = launcher.ServerRecord(
        4242,
        "http://127.0.0.1:9191/",
        LIVE_LEASE_ID,
        LIVE_ACCESS_TOKEN,
        LIVE_URL_NAMESPACE,
    )
    error = launcher.ExistingWebServer(record)

    assert LIVE_ACCESS_TOKEN not in repr(record)
    assert LIVE_URL_NAMESPACE not in repr(record)
    assert LIVE_ACCESS_TOKEN not in str(error)
    assert LIVE_URL_NAMESPACE not in str(error)
    assert LIVE_ACCESS_TOKEN not in repr(error)
    assert LIVE_URL_NAMESPACE not in repr(error)


@pytest.mark.parametrize(
    "contents",
    [
        '{"pid": 99999999, "url": "http://127.0.0.1:8765/"}\n',
        "not json\n",
        '{"pid": "nope", "url": "http://127.0.0.1:8765/"}\n',
    ],
)
def test_server_lease_replaces_stale_or_malformed_record(tmp_path: Path, contents: str) -> None:
    lease = tmp_path / ".web-server.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    lease.write_text(contents, encoding="utf-8")

    with launcher.server_lease(tmp_path):
        saved = json.loads(lease.read_text(encoding="utf-8"))
        assert saved["pid"] == os.getpid()
        assert saved["url"] == "http://127.0.0.1:8765/"
        assert len(saved["lease_id"]) == 32
        assert len(saved["access_token"]) >= 32
        assert saved["url_namespace"].startswith("/_openlearn/")


def test_server_lease_does_not_replace_invalid_record_with_live_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lease = tmp_path / ".web-server.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    lease.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "url": "https://attacker.example/",
                "lease_id": LIVE_LEASE_ID,
                "access_token": LIVE_ACCESS_TOKEN,
                "url_namespace": LIVE_URL_NAMESPACE,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".web-server.lock").write_bytes(b"\0" + LIVE_LEASE_ID.encode("ascii"))
    monkeypatch.setattr(launcher, "_acquire_owner_lock", lambda _handle: False)

    with pytest.raises(launcher.WebLaunchError, match="live owner.*invalid"):
        with launcher.server_lease(tmp_path):
            pass

    assert json.loads(lease.read_text(encoding="utf-8"))["url"] == ("https://attacker.example/")


def test_server_lease_lock_preserves_malformed_record_while_owner_is_live(
    tmp_path: Path,
) -> None:
    lease = tmp_path / ".web-server.json"

    with launcher.server_lease(tmp_path):
        lease.write_text("corrupted while running\n", encoding="utf-8")
        with pytest.raises(launcher.WebLaunchError, match="live owner.*invalid"):
            with launcher.server_lease(tmp_path):
                pass

    assert lease.read_text(encoding="utf-8") == "corrupted while running\n"


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8765/",
        "http://attacker.example:8765/",
        "http://127.0.0.1:8765/path",
        "http://127.0.0.1:8765/?next=evil",
        "http://user@127.0.0.1:8765/",
        "not a URL",
    ],
)
def test_server_lease_rejects_non_loopback_or_malformed_url(tmp_path: Path, url: str) -> None:
    with pytest.raises(launcher.WebLaunchError, match="valid loopback"):
        with launcher.server_lease(tmp_path, url=url):
            pass

    assert not (tmp_path / ".web-server.json").exists()


def test_run_reuses_healthy_recorded_server_without_starting_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_live_record(tmp_path)
    monkeypatch.setattr(launcher, "_acquire_owner_lock", lambda _handle: False)
    probed: list[launcher.ServerRecord] = []
    opened: list[str] = []

    launcher.run(
        home=tmp_path,
        probe=lambda record: probed.append(record) or True,
        opener=lambda url: opened.append(url),
        server_runner=lambda *args, **kwargs: pytest.fail("started a second server"),
        listener_factory=fake_listener_factory(),
    )

    assert probed == [
        launcher.ServerRecord(
            4242,
            "http://127.0.0.1:9191/",
            LIVE_LEASE_ID,
            LIVE_ACCESS_TOKEN,
            LIVE_URL_NAMESPACE,
        )
    ]
    assert opened == [f"http://127.0.0.1:9191/?access_token={LIVE_ACCESS_TOKEN}"]


def test_run_waits_for_claimed_server_to_become_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_live_record(tmp_path)
    monkeypatch.setattr(launcher, "_acquire_owner_lock", lambda _handle: False)
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    probes = 0
    opened: list[str] = []

    def probe(record: launcher.ServerRecord) -> bool:
        nonlocal probes
        assert record.access_token == LIVE_ACCESS_TOKEN
        probes += 1
        return probes >= 2

    launcher.run(
        home=tmp_path,
        probe=probe,
        opener=lambda url: opened.append(url),
        server_runner=lambda *args, **kwargs: pytest.fail("started a second server"),
        listener_factory=fake_listener_factory(),
    )

    assert probes == 2
    assert opened == [f"http://127.0.0.1:9191/?access_token={LIVE_ACCESS_TOKEN}"]


def test_run_respects_no_browser_when_reusing_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_live_record(tmp_path)
    monkeypatch.setattr(launcher, "_acquire_owner_lock", lambda _handle: False)

    notices: list[str] = []
    launcher.run(
        home=tmp_path,
        open_browser=False,
        probe=lambda record: record.access_token == LIVE_ACCESS_TOKEN,
        opener=lambda url: pytest.fail("opened a browser"),
        notifier=notices.append,
        server_runner=lambda *args, **kwargs: pytest.fail("started a second server"),
        listener_factory=fake_listener_factory(),
    )

    assert notices == [f"Open openlearn: http://127.0.0.1:9191/?access_token={LIVE_ACCESS_TOKEN}"]


def test_reserve_loopback_port_avoids_an_occupied_preferred_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listeners: list[object] = []

    class Listener:
        def __init__(self, *_args: object) -> None:
            self.closed = False
            listeners.append(self)

        def bind(self, address: tuple[str, int]) -> None:
            if address[1] == launcher.DEFAULT_PORT:
                raise OSError(launcher.errno.EADDRINUSE, "address already in use")

        def listen(self) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(launcher.socket, "socket", Listener)

    with launcher._reserve_loopback_port(None) as (listener, selected):
        assert selected == launcher.DEFAULT_PORT + 1
        assert listener is listeners[1]
        assert listeners[0].closed is True
        assert listeners[1].closed is False

    assert listeners[1].closed is True


def test_run_prints_selected_bootstrap_url_when_new_server_has_no_browser(
    tmp_path: Path,
) -> None:
    notices: list[str] = []

    captured: dict[str, object] = {}

    def run_server(_app: object, **kwargs: object) -> None:
        captured.update(kwargs)

    launcher.run(
        home=tmp_path,
        open_browser=False,
        notifier=notices.append,
        opener=lambda url: pytest.fail("opened a browser"),
        server_runner=run_server,
        listener_factory=fake_listener_factory(9124),
    )

    assert len(notices) == 1
    assert notices[0].startswith("Open openlearn: http://127.0.0.1:9124/?access_token=")
    assert len(notices[0].partition("access_token=")[2]) >= 32
    assert captured["port"] == 9124


def test_run_rejects_an_unavailable_explicit_port_before_starting(
    tmp_path: Path,
) -> None:
    @contextmanager
    def occupied(_requested_port: int | None) -> Iterator[tuple[FakeListener, int]]:
        raise launcher.WebLaunchError(
            "port 9123 is already in use; omit --port to choose one automatically"
        )
        yield FakeListener(9123), 9123

    with pytest.raises(launcher.WebLaunchError, match="port 9123 is already in use"):
        launcher.run(
            port=9123,
            home=tmp_path,
            open_browser=False,
            server_runner=lambda *_args, **_kwargs: pytest.fail("started a server"),
            listener_factory=occupied,
        )

    assert not (tmp_path / ".web-server.json").exists()


def test_run_refuses_to_start_when_live_server_fails_health_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_live_record(tmp_path)
    monkeypatch.setattr(launcher, "_acquire_owner_lock", lambda _handle: False)
    monkeypatch.setattr(
        launcher,
        "_wait_for_healthy_owner",
        lambda _home, _record, _probe: None,
    )

    with pytest.raises(launcher.WebLaunchError, match="health check failed"):
        launcher.run(
            home=tmp_path,
            probe=lambda url: False,
            opener=lambda url: pytest.fail("opened a browser"),
            server_runner=lambda *args, **kwargs: pytest.fail("started a second server"),
            listener_factory=fake_listener_factory(),
        )


def test_losing_launcher_waits_for_current_owner_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_id = "b" * 32
    lock_path = tmp_path / ".web-server.lock"
    lease = tmp_path / ".web-server.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(b"\0" + LIVE_LEASE_ID.encode("ascii"))
    lease.write_text(
        json.dumps(
            {
                "pid": 1111,
                "url": "http://127.0.0.1:8111/",
                "lease_id": stale_id,
                "access_token": "stale-capability-token-value-1234567890",
                "url_namespace": "/_openlearn/stale-route-namespace-value-1234567890",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "_acquire_owner_lock", lambda _handle: False)
    replaced = False

    def publish_current(_seconds: float) -> None:
        nonlocal replaced
        if replaced:
            return
        replaced = True
        lease.write_text(
            json.dumps(
                {
                    "pid": 2222,
                    "url": "http://127.0.0.1:8222/",
                    "lease_id": LIVE_LEASE_ID,
                    "access_token": LIVE_ACCESS_TOKEN,
                    "url_namespace": LIVE_URL_NAMESPACE,
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(launcher.time, "sleep", publish_current)

    with pytest.raises(launcher.ExistingWebServer) as captured:
        with launcher.server_lease(tmp_path):
            pass

    assert captured.value.record.url == "http://127.0.0.1:8222/"


def test_losing_launcher_accepts_legacy_owner_token_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_live_record(tmp_path)
    (tmp_path / ".web-server.lock").write_bytes(LIVE_LEASE_ID.encode("ascii"))
    monkeypatch.setattr(launcher, "_acquire_owner_lock", lambda _handle: False)

    with pytest.raises(launcher.ExistingWebServer) as captured:
        with launcher.server_lease(tmp_path):
            pass

    assert captured.value.record.lease_id == LIVE_LEASE_ID


def test_run_starts_server_with_claimed_home(tmp_path: Path) -> None:
    opened: list[str] = []
    listening = threading.Event()
    browser_opened = threading.Event()

    def run_server(app: object, **kwargs: object) -> None:
        listener = kwargs.pop("listener")
        assert isinstance(listener, FakeListener)
        assert listener.closed is False
        assert kwargs == {
            "host": "127.0.0.1",
            "port": 9234,
            "log_level": "info",
            "access_log": False,
        }
        assert (tmp_path / ".web-server.json").exists()
        saved = json.loads((tmp_path / ".web-server.json").read_text(encoding="utf-8"))
        assert app.state.security.access_token == saved["access_token"]
        assert opened == []
        listening.set()
        assert browser_opened.wait(timeout=1)

    def open_browser(url: str) -> None:
        opened.append(url)
        browser_opened.set()

    launcher.run(
        port=9234,
        home=tmp_path,
        probe=lambda _record: listening.is_set(),
        opener=open_browser,
        server_runner=run_server,
        listener_factory=fake_listener_factory(),
    )

    saved_token = opened[0].partition("access_token=")[2]
    assert len(saved_token) >= 32
    assert opened == [f"http://127.0.0.1:9234/?access_token={saved_token}"]
    assert not (tmp_path / ".web-server.json").exists()


def test_run_never_opens_browser_when_server_fails_before_ready(
    tmp_path: Path,
) -> None:
    opened: list[str] = []

    def fail_bind(*_args: object, **_kwargs: object) -> None:
        raise OSError("address already in use")

    with pytest.raises(OSError, match="address already in use"):
        launcher.run(
            home=tmp_path,
            probe=lambda _record: False,
            opener=lambda url: opened.append(url),
            server_runner=fail_bind,
            listener_factory=fake_listener_factory(),
        )

    assert opened == []


def test_health_probe_sends_private_capability_header(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[tuple[str, str, dict[str, str]]] = []

    class Response:
        status = 200

        def read(self) -> bytes:
            return b"ok"

    class Connection:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            assert (host, port, timeout) == ("127.0.0.1", 9191, 0.75)

        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            requests.append((method, path, headers))

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(launcher.http.client, "HTTPConnection", Connection)
    record = launcher.ServerRecord(
        4242,
        "http://127.0.0.1:9191/",
        LIVE_LEASE_ID,
        LIVE_ACCESS_TOKEN,
        LIVE_URL_NAMESPACE,
    )

    assert launcher._probe_server(record) is True
    assert requests == [
        (
            "GET",
            "/health",
            {
                "Host": "127.0.0.1:9191",
                "X-Openlearn-Capability": LIVE_ACCESS_TOKEN,
            },
        )
    ]


def test_cmd_web_maps_arguments_to_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}
    monkeypatch.setattr(
        launcher,
        "run",
        lambda **values: called.update(values),
    )

    assert cli.cmd_web(Namespace(port=9123, no_browser=True)) == 0
    assert called == {"port": 9123, "open_browser": False}


def test_cmd_web_explains_missing_web_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def import_without_fastapi(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "web.launcher" and level == 1:
            raise ModuleNotFoundError("No module named 'fastapi'", name="fastapi")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_fastapi)

    with pytest.raises(
        cli.OpenLearnError,
        match=r"Maker Bench dependencies are missing.*python -m pip install -e \.",
    ):
        cli.cmd_web(Namespace(port=9123, no_browser=True))


def test_parser_exposes_web_command() -> None:
    args = cli.build_parser().parse_args(["web", "--port", "9012", "--no-browser"])

    assert args.func is cli.cmd_web
    assert args.port == 9012
    assert args.no_browser is True
