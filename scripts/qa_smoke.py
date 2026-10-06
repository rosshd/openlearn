"""Launch, exercise, and stop a fresh mock-only browser fixture."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

from workflow_evidence import candidate, git


ROOT = Path(__file__).resolve().parents[1]


def isolated_environment(run: Path) -> dict[str, str]:
    # An allowlist prevents ambient provider credentials/configuration from entering QA.
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "TMPDIR") if name in os.environ}
    env.update({
        "HOME": str(run / "os-home"), "OPENLEARN_HOME": str(run / "fixture"),
        "OPENLEARN_MOCK": "1", "PYTHONPATH": str(ROOT / "src"),
    })
    return env


def main() -> int:
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    parent = ROOT / ".artifacts/qa"
    parent.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="mock-", dir=parent))
    run.chmod(0o700)
    env = isolated_environment(run)
    Path(env["HOME"]).mkdir()
    evidence = {"schema": 1, "head": git("rev-parse", "HEAD"), "candidate": candidate(),
                "fixture": str(run / "fixture"), "mode": "mock", "result": "fail"}
    process = None
    address = None
    try:
        with (run / "seed.log").open("wb") as log:
            subprocess.run([sys.executable, "-c", (
                "from openlearn import application; "
                "application.create_course(application.CourseCreationRequest("
                "name='Workflow QA', goal='Practice basic algebra'))"
            )], cwd=run, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        with (run / "server.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, "-c", (
                "from openlearn.web.launcher import run; "
                "run(open_browser=False, notifier=lambda message: None)"
            )], cwd=run, env=env, stdout=log, stderr=subprocess.STDOUT)
            evidence["pid"] = process.pid
            deadline = time.monotonic() + 20
            record = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Mock QA server exited during startup; inspect server.log")
                try:
                    record = json.loads((run / "fixture/.web-server.json").read_text())
                    request = Request(record["url"] + "health", headers={
                        "X-Openlearn-Capability": record["access_token"],
                    })
                    with urlopen(request, timeout=0.25) as response:
                        if response.status == 200:
                            address = record["url"]
                            break
                except (OSError, ValueError, KeyError):
                    time.sleep(0.05)
            if address is None or record is None:
                raise RuntimeError("Mock QA server did not become healthy")
            evidence["url"] = address  # No capability token or namespace in handback evidence.
            from playwright.sync_api import sync_playwright, expect
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(env=env)
                page = browser.new_page()
                page.goto(address + "?access_token=" + record["access_token"])
                page.goto(address.rstrip("/") + record["url_namespace"] + "/dashboard")
                expect(page.get_by_role("heading", name="Your courses")).to_be_visible()
                expect(page.locator('[data-course-slug="workflow-qa"]')).to_be_visible()
                page.locator('[data-course-slug="workflow-qa"]').click()
                page.reload()
                expect(page.locator('[data-course-slug="workflow-qa"]')).to_be_visible()
                browser.close()
            evidence["journey"] = ["fresh mock course", "authenticated browser", "course selection", "reload"]
            evidence["result"] = "pass"
    except (Exception, KeyboardInterrupt) as error:
        # Exception text can contain the private bootstrap URL, so retain only its type.
        evidence["error_type"] = type(error).__name__
        print(f"QA failed: {type(error).__name__}; inspect private logs under {run}")
    finally:
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        evidence["process_stopped"] = process is None or process.poll() is not None
        if address:
            from urllib.parse import urlsplit
            port = urlsplit(address).port
            with socket.socket() as connection:
                connection.settimeout(0.5)
                evidence["port_closed"] = connection.connect_ex(("127.0.0.1", port)) != 0
        evidence["lease_removed"] = not (run / "fixture/.web-server.json").exists()
        evidence["candidate_unchanged"] = evidence["candidate"] == candidate()
        if not all(evidence[key] for key in ("process_stopped", "lease_removed", "candidate_unchanged")):
            evidence["result"] = "fail"
        if address and not evidence.get("port_closed"):
            evidence["result"] = "fail"
        (run / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"QA: {evidence['result'].upper()} - {run / 'evidence.json'}")
    return 0 if evidence["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
