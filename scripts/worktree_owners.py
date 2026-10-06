"""Local owner index; GitHub issues remain the authoritative task briefs."""

from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import subprocess


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", "--no-optional-locks", *args], cwd=cwd, text=True).strip()


def inventory() -> tuple[Path, dict, list[dict]]:
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    index = common / "openlearn-owners.json"
    owners = json.loads(index.read_text()) if index.exists() else {}
    rows = []
    for record in git("worktree", "list", "--porcelain").split("\n\n"):
        fields = dict(line.split(" ", 1) for line in record.splitlines() if " " in line)
        path = Path(fields["worktree"]).resolve()
        if path == common.parent.resolve():
            continue
        owner_path = str(path)
        owner = owners.get(owner_path, {})
        if not owner:
            for recorded_path, record in owners.items():
                if fields.get("branch") == f"refs/heads/{record.get('branch')}":
                    owner_path, owner = recorded_path, record
                    break
        try:
            dirty = bool(git("status", "--porcelain", cwd=path))
            merged = subprocess.run(
                ["git", "--no-optional-locks", "merge-base", "--is-ancestor", "HEAD", "main"], cwd=path,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ).returncode == 0
            if owner and owner_path != str(path):
                state = "unresolved"
            elif owner.get("state") == "active":
                state = "active"
            elif owner.get("state") == "retired" and (
                fields.get("branch") != f"refs/heads/{owner.get('branch')}"
            ):
                state = "unresolved"
            elif owner.get("state") == "retired" and not dirty:
                state = "retired"
            elif dirty or not merged:
                state = "unresolved"
            else:
                state = "settled"
        except (OSError, subprocess.CalledProcessError):
            state = "unresolved"
        rows.append({"path": str(path), "state": state, "owner": owner})
    return index, owners, rows


def execute() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "count", "register", "retire", "can-finish"])
    parser.add_argument("task", nargs="?")
    parser.add_argument("issue", nargs="?")
    parser.add_argument("--base", default="main")
    parser.add_argument("--start-sha", default="HEAD")
    parser.add_argument("--worktree")
    args = parser.parse_args()
    index, owners, rows = inventory()
    if args.action == "count":
        print(sum(row["state"] in {"active", "unresolved"} for row in rows))
    elif args.action == "status":
        for row in rows:
            print(f"  {row['state']}: {row['path']} ({row['owner'].get('task', 'unregistered')})")
    elif args.action == "can-finish":
        if not args.worktree:
            parser.error("can-finish requires the exact target worktree path")
        target = str(Path(args.worktree).resolve())
        for row in rows:
            owner = row["owner"]
            if owner.get("state") == "active" and (
                row["path"] == target or args.task in {owner.get("branch"), owner.get("base")}
            ):
                parser.error("branch still has an active owner or registered child dependency")
    else:
        path = git("rev-parse", "--show-toplevel")
        if path not in {row["path"] for row in rows}:
            parser.error("register/retire must run from a linked worktree")
        if args.action == "register":
            if not args.task or not args.issue:
                parser.error("register requires task ID and canonical GitHub issue URL")
            if not args.issue.startswith("https://github.com/rosshd/openlearn/issues/"):
                parser.error("issue must be a canonical OpenLearn GitHub issue URL")
            if owners.get(path, {}).get("state") == "active" and owners[path]["task"] != args.task:
                parser.error("worktree already has a different active owner")
            start_sha = git("rev-parse", "--verify", f"{args.start_sha}^{{commit}}")
            if subprocess.run([
                "git", "--no-optional-locks", "merge-base", "--is-ancestor", start_sha, "HEAD",
            ]).returncode:
                parser.error("start SHA must be an ancestor of this candidate")
            owners[path] = {
                "task": args.task, "issue": args.issue, "state": "active",
                "branch": git("branch", "--show-current"), "start_sha": start_sha,
                "base": args.base,
            }
        else:
            if path not in owners:
                parser.error("register the owner before retiring it")
            owners[path]["state"] = "retired"
        index.write_text(json.dumps(owners, indent=2) + "\n")
        print(f"{args.action}: {path}")


if __name__ == "__main__":
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    with (common / "openlearn-owners.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        execute()
