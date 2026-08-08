#!/usr/bin/env python3
"""Build, inspect, verify, and smoke immutable Openlearn release candidates."""

from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPCookieProcessor
import venv
from zipfile import ZipFile


HASH_FILENAME = "SHA256SUMS"
REQUIRED_PACKAGE_FILES = frozenset(
    {
        "openlearn/drills.json",
        "openlearn/interview_problem_catalogs/openlearn-interview-v1.json",
        "openlearn/interview_skill_graphs/coding-interview-v1.json",
        "openlearn/templates/technical-interview-prep.json",
        "openlearn/web/static/favicon.svg",
        "openlearn/web/static/openlearn.css",
        "openlearn/web/static/openlearn.js",
        "openlearn/web/templates/base.html",
        "openlearn/web/templates/data.html",
        "openlearn/web/templates/focus.html",
        "openlearn/web/templates/setup.html",
    }
)
FORBIDDEN_PARTS = frozenset(
    {
        ".artifacts",
        ".env",
        ".git",
        ".github",
        ".gnhf",
        ".worktrees",
        "learning-topics",
        "manual-tests",
        "node_modules",
    }
)
FORBIDDEN_NAMES = frozenset({"config.json", "state.json"})
FORBIDDEN_SUFFIXES = (".events.jsonl", ".state.json")
SECRET_PATTERN = re.compile(
    rb"(?:"
    rb"sk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{20,}"
    rb"|gh[pousr]_[A-Za-z0-9]{20,}"
    rb"|github_pat_[A-Za-z0-9_]{20,}"
    rb"|xox[baprs]-[A-Za-z0-9-]{20,}"
    rb"|AKIA[0-9A-Z]{16}"
    rb")"
)
VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){2}(?:[A-Za-z0-9.+-]*)$")


class ReleaseArtifactError(RuntimeError):
    """The release candidate is incomplete, mutable, or unsafe to promote."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifacts(candidate: Path) -> tuple[Path, Path]:
    distribution = candidate / "dist"
    wheels = sorted(distribution.glob("openlearn-*.whl"))
    sdists = sorted(distribution.glob("openlearn-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseArtifactError("candidate must contain exactly one wheel and one sdist")
    extras = sorted(
        path.name
        for path in distribution.iterdir()
        if path.is_file() and path not in {wheels[0], sdists[0]}
    )
    if extras:
        raise ReleaseArtifactError(f"candidate contains unexpected distributions: {extras}")
    return wheels[0], sdists[0]


def _normalized_member(name: str, *, sdist: bool) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ReleaseArtifactError(f"unsafe archive member: {name}")
    parts = path.parts[1:] if sdist else path.parts
    if sdist and (not path.parts or not path.parts[0].startswith("openlearn-")):
        raise ReleaseArtifactError(f"sdist member is outside its package root: {name}")
    return PurePosixPath(*parts).as_posix()


def _forbidden_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else ""
    return (
        bool(FORBIDDEN_PARTS.intersection(parts))
        or name in FORBIDDEN_NAMES
        or name.endswith(FORBIDDEN_SUFFIXES)
        or name.startswith(".env.")
        or "__pycache__" in parts
        or name.endswith((".pyc", ".pyo"))
    )


def _wheel_members(path: Path) -> list[tuple[str, bytes]]:
    with ZipFile(path) as archive:
        return [
            (_normalized_member(info.filename, sdist=False), archive.read(info))
            for info in archive.infolist()
            if not info.is_dir()
        ]


def _sdist_members(path: Path) -> list[tuple[str, bytes]]:
    members: list[tuple[str, bytes]] = []
    with tarfile.open(path, "r:gz") as archive:
        for info in archive.getmembers():
            normalized = _normalized_member(info.name, sdist=True)
            if info.issym() or info.islnk():
                raise ReleaseArtifactError(f"archive link is not allowed: {normalized}")
            if not info.isfile():
                continue
            handle = archive.extractfile(info)
            if handle is None:
                raise ReleaseArtifactError(f"archive member is unreadable: {normalized}")
            members.append((normalized, handle.read()))
    return members


def _metadata_version(
    members: list[tuple[str, bytes]], metadata_name: str, *, exact: bool = False
) -> str:
    matches = [
        content
        for name, content in members
        if name == metadata_name or (not exact and name.endswith(metadata_name))
    ]
    if len(matches) != 1:
        raise ReleaseArtifactError(f"archive must contain exactly one {metadata_name}")
    for line in matches[0].decode("utf-8").splitlines():
        if line.startswith("Version: "):
            version = line.removeprefix("Version: ").strip()
            if VERSION_PATTERN.fullmatch(version):
                return version
    raise ReleaseArtifactError(f"archive {metadata_name} has no valid version")


def inspect_artifact(path: Path, *, sdist: bool) -> str:
    members = _sdist_members(path) if sdist else _wheel_members(path)
    names = {name for name, _content in members}
    package_names = {
        name.removeprefix("src/") for name in names if name.startswith(("openlearn/", "src/openlearn/"))
    }
    missing = sorted(REQUIRED_PACKAGE_FILES - package_names)
    if missing:
        raise ReleaseArtifactError(f"archive is missing required package assets: {missing}")
    forbidden = sorted(name for name in names if _forbidden_path(name))
    if forbidden:
        raise ReleaseArtifactError(f"archive contains private or development paths: {forbidden}")
    secret_paths = sorted(name for name, content in members if SECRET_PATTERN.search(content))
    if secret_paths:
        raise ReleaseArtifactError(
            f"archive contains provider-shaped secret material: {secret_paths}"
        )
    return _metadata_version(
        members,
        "PKG-INFO" if sdist else ".dist-info/METADATA",
        exact=sdist,
    )


def _hash_lines(candidate: Path) -> list[str]:
    wheel, sdist = _artifacts(candidate)
    return [f"{_sha256(path)}  dist/{path.name}" for path in (wheel, sdist)]


def write_hashes(candidate: Path) -> None:
    (candidate / HASH_FILENAME).write_text("\n".join(_hash_lines(candidate)) + "\n", encoding="ascii")


def verify_hashes(candidate: Path) -> None:
    hash_path = candidate / HASH_FILENAME
    try:
        recorded = hash_path.read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise ReleaseArtifactError("candidate is missing SHA256SUMS") from exc
    expected = _hash_lines(candidate)
    if recorded != expected:
        raise ReleaseArtifactError("candidate hashes do not match the exact distribution files")


def verify_candidate(candidate: Path, *, expected_version: str | None, tag: str | None) -> str:
    candidate = candidate.resolve(strict=True)
    verify_hashes(candidate)
    wheel, sdist = _artifacts(candidate)
    versions = {
        inspect_artifact(wheel, sdist=False),
        inspect_artifact(sdist, sdist=True),
    }
    if len(versions) != 1:
        raise ReleaseArtifactError("wheel and sdist versions do not agree")
    version = versions.pop()
    if expected_version is not None and version != expected_version:
        raise ReleaseArtifactError(
            f"artifact version {version} does not match module version {expected_version}"
        )
    if tag is not None:
        if not tag.startswith("v") or tag[1:] != version:
            raise ReleaseArtifactError(f"release tag {tag} does not match artifact version {version}")
    return version


def build_candidate(repository: Path, candidate: Path, *, expected_version: str) -> None:
    if candidate.exists():
        raise ReleaseArtifactError("candidate path already exists; release artifacts are build-once")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openlearn-release-build-", dir=candidate.parent) as raw:
        staged = Path(raw) / "candidate"
        distribution = staged / "dist"
        distribution.mkdir(parents=True)
        subprocess.run(
            [sys.executable, "-m", "build", "--outdir", str(distribution)],
            cwd=repository,
            check=True,
        )
        wheel, sdist = _artifacts(staged)
        versions = {
            inspect_artifact(wheel, sdist=False),
            inspect_artifact(sdist, sdist=True),
        }
        if versions != {expected_version}:
            raise ReleaseArtifactError(
                f"built artifact versions {sorted(versions)} do not match {expected_version}"
            )
        write_hashes(staged)
        verify_candidate(staged, expected_version=expected_version, tag=None)
        staged.replace(candidate)


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_openlearn(environment: Path) -> Path:
    return environment / ("Scripts/openlearn.exe" if os.name == "nt" else "bin/openlearn")


def _free_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _smoke_web(command: Path, home: Path) -> None:
    port = _free_loopback_port()
    environment = {
        **os.environ,
        "OPENLEARN_HOME": str(home),
        "OPENLEARN_MOCK": "1",
        "PYTHONNOUSERSITE": "1",
    }
    process = subprocess.Popen(
        [str(command), "web", "--no-browser", "--port", str(port)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        last_error: Exception | None = None
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        base_url: str | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ReleaseArtifactError(
                    f"Maker Bench exited before startup with status {process.returncode}"
                )
            try:
                record = json.loads((home / ".web-server.json").read_text(encoding="utf-8"))
                namespace = record["url_namespace"]
                access_token = record["access_token"]
                if not isinstance(namespace, str) or not isinstance(access_token, str):
                    raise ValueError("invalid server record")
                root_url = f"http://127.0.0.1:{port}"
                base_url = f"{root_url}{namespace}"
                bootstrap = f"{root_url}/?{urlencode({'access_token': access_token})}"
                with opener.open(bootstrap, timeout=1):
                    pass
                with opener.open(f"{base_url}/setup", timeout=1) as response:
                    body = response.read().decode("utf-8")
                    if response.status == 200 and "Connect your tutor" in body:
                        break
            except Exception as exc:  # The server is expected to refuse connections while starting.
                last_error = exc
                time.sleep(0.2)
        else:
            raise ReleaseArtifactError(f"Maker Bench did not start on loopback: {last_error}")
        assert base_url is not None
        for path, marker in (
            ("/static/openlearn.css", b"--orange"),
            ("/static/openlearn.js", b"requestJson"),
            ("/static/favicon.svg", b"<svg"),
        ):
            with opener.open(f"{base_url}{path}", timeout=2) as response:
                if response.status != 200 or marker not in response.read():
                    raise ReleaseArtifactError(f"Maker Bench asset failed installed smoke: {path}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def smoke_candidate(candidate: Path, *, kind: str, expected_version: str) -> None:
    version = verify_candidate(candidate, expected_version=expected_version, tag=None)
    wheel, sdist = _artifacts(candidate)
    artifact = wheel if kind == "wheel" else sdist
    with tempfile.TemporaryDirectory(prefix=f"openlearn-{kind}-smoke-") as raw:
        root = Path(raw)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = _venv_python(environment)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(artifact)],
            check=True,
            env={**os.environ, "PYTHONNOUSERSITE": "1"},
        )
        command = _venv_openlearn(environment)
        output = subprocess.run(
            [str(command), "--version"],
            check=True,
            capture_output=True,
            text=True,
            cwd=root,
            env={**os.environ, "PYTHONNOUSERSITE": "1"},
        ).stdout.strip()
        if output != f"openlearn {version}":
            raise ReleaseArtifactError(f"installed CLI version does not match: {output}")
        imported = json.loads(
            subprocess.run(
                [
                    str(python),
                    "-c",
                    (
                        "import json, openlearn; "
                        "print(json.dumps({'version': openlearn.__version__, "
                        "'file': openlearn.__file__}))"
                    ),
                ],
                check=True,
                capture_output=True,
                text=True,
                cwd=root,
                env={**os.environ, "PYTHONNOUSERSITE": "1"},
            ).stdout
        )
        if imported.get("version") != version:
            raise ReleaseArtifactError(
                f"installed module version does not match: {imported.get('version')}"
            )
        module_path = Path(str(imported.get("file", ""))).resolve(strict=True)
        if not module_path.is_relative_to(environment.resolve(strict=True)):
            raise ReleaseArtifactError("installed smoke imported Openlearn outside its clean environment")
        templates = subprocess.run(
            [str(command), "templates"],
            check=True,
            capture_output=True,
            text=True,
            cwd=root,
            env={
                **os.environ,
                "OPENLEARN_HOME": str(root / "home"),
                "PYTHONNOUSERSITE": "1",
            },
        ).stdout
        if "Technical Interview Prep" not in templates:
            raise ReleaseArtifactError("installed CLI cannot load the baseline course catalog")
        _smoke_web(command, root / "home")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--repository", type=Path, default=Path.cwd())
    build.add_argument("--candidate", type=Path, required=True)
    build.add_argument("--expected-version", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--candidate", type=Path, required=True)
    verify.add_argument("--expected-version")
    verify.add_argument("--tag")
    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--candidate", type=Path, required=True)
    smoke.add_argument("--kind", choices=("wheel", "sdist"), required=True)
    smoke.add_argument("--expected-version", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.action == "build":
            build_candidate(
                args.repository.resolve(strict=True),
                args.candidate.absolute(),
                expected_version=args.expected_version,
            )
            print(f"release candidate built: {args.candidate}")
        elif args.action == "verify":
            version = verify_candidate(
                args.candidate,
                expected_version=args.expected_version,
                tag=args.tag,
            )
            print(f"release candidate verified: openlearn {version}")
        else:
            smoke_candidate(
                args.candidate,
                kind=args.kind,
                expected_version=args.expected_version,
            )
            print(f"installed {args.kind} smoke passed")
    except (OSError, ReleaseArtifactError, subprocess.CalledProcessError) as exc:
        print(f"release artifact error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
