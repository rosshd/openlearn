"""Content- and environment-bound receipts for the local repository gate."""

from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / ".artifacts/gate/receipt.json"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode().strip()


def candidate() -> str:
    digest = sha256()
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT
    ).split(b"\0")
    for raw in sorted(set(paths) - {b""}):
        path = ROOT / os.fsdecode(raw)
        digest.update(raw + b"\0")
        if path.is_symlink():
            digest.update(b"link:" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            digest.update(str(path.stat().st_mode & 0o777).encode() + b"\0")
            digest.update(path.read_bytes())
        else:
            digest.update(b"missing")
    return digest.hexdigest()


def environment() -> str:
    value = {
        "python": str(Path(sys.executable).resolve()),
        "version": sys.version,
        "platform": platform.platform(),
        "packages": sorted(
            (dist.metadata["Name"], dist.version) for dist in importlib.metadata.distributions()
        ),
        "path": os.environ.get("PATH", ""),
        "pythonpath": os.environ.get("PYTHONPATH", ""),
        "pythonhome": os.environ.get("PYTHONHOME", ""),
        "browser_tests": os.environ.get("OPENLEARN_BROWSER_TEST", ""),
        "package_smoke": os.environ.get("OPENLEARN_PACKAGE_SMOKE", ""),
        "oci_tests": os.environ.get("OPENLEARN_RUN_OCI_TESTS", ""),
        "mock": os.environ.get("OPENLEARN_MOCK", ""),
        "learner_home": os.environ.get("OPENLEARN_HOME", ""),
        "pytest_addopts": os.environ.get("PYTEST_ADDOPTS", ""),
        "makeflags": os.environ.get("MAKEFLAGS", ""),
        "browser_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
        "ci": os.environ.get("CI", ""),
    }
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def binding(command: list[str]) -> dict:
    return {"schema": 1, "candidate": candidate(), "environment": environment(), "command": command}


def reusable(command: list[str]) -> bool:
    try:
        receipt = json.loads(RECEIPT.read_text())
        log = Path(receipt["log"])
        return (
            receipt["binding"] == binding(command)
            and receipt["result"] == "pass"
            and log.is_file()
            and sha256(log.read_bytes()).hexdigest() == receipt["log_sha256"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def check(command: list[str]) -> int:
    before = binding(command)
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    log = RECEIPT.parent / f"check-{time.time_ns()}.log"
    with log.open("wb") as output:
        result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
    stable = before == binding(command)
    receipt = {
        "binding": before,
        "head": git("rev-parse", "HEAD"),
        "result": "pass" if result.returncode == 0 and stable else "fail",
        "log": str(log),
        "log_sha256": sha256(log.read_bytes()).hexdigest(),
        "completed_at": time.time(),
    }
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"GATE: {receipt['result'].upper()} - {log}")
    if receipt["result"] != "pass":
        print("\n".join(log.read_text(errors="replace").splitlines()[-30:]))
        if not stable:
            print("Candidate or environment changed during the gate; receipt rejected.")
        return result.returncode or 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "review", "reusable"])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--out", default=".artifacts/review")
    parser.add_argument("--openlearn", default=str(Path(sys.executable).parent / "openlearn"))
    args = parser.parse_args()
    command = ["make", "gate", f"PYTHON={sys.executable}", f"OPENLEARN={args.openlearn}"]
    if args.action == "reusable":
        return 0 if reusable(command) else 1
    if args.action == "check":
        return check(command)
    if not reusable(command):
        print("No valid receipt for this candidate and environment; running the gate.")
        result = check(command)
        if result:
            return result
    else:
        print(f"Reusing exact candidate/environment gate receipt: {RECEIPT}")
    out = ROOT / args.out / str(time.time_ns())
    out.mkdir(parents=True)
    base = git("merge-base", args.base, "HEAD")
    for name, options in [("diff.patch", []), ("diff.stat", ["--stat"])]:
        (out / name).write_text(git("diff", *options, base) + "\n")
    (out / "receipt.json").write_bytes(RECEIPT.read_bytes())
    print(f"Evidence: {out}; independent review still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
