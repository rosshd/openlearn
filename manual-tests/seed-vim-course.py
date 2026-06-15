#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import sys
import textwrap
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from openlearn import cli  # noqa: E402


DEFAULT_HOME = Path("/tmp/openlearn-manual-vim")
COURSE_NAME = "Practical Vim Foundations"
COURSE_SLUG = "practical-vim-foundations"
COURSE_GOAL = "Learn Vim well enough for everyday file editing."
FIXTURE = ROOT_DIR / "manual-tests" / "context" / "practical-vim-syllabus.txt"


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed an isolated manual-test course.")
    parser.add_argument(
        "--home",
        default=os.environ.get("OPENLEARN_HOME", str(DEFAULT_HOME)),
        help="Manual-test OPENLEARN_HOME. Defaults to /tmp/openlearn-manual-vim.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the manual-test home before seeding it.",
    )
    state = parser.add_mutually_exclusive_group()
    state.add_argument(
        "--draft",
        action="store_true",
        help="Leave course unstarted so the menu shows Start course. This is the default.",
    )
    state.add_argument(
        "--started",
        action="store_true",
        help="Mark course started so the menu shows Resume.",
    )
    parser.add_argument(
        "--with-session",
        action="store_true",
        help="Add fake prior chat context for Resume testing. Implies --started.",
    )
    parser.add_argument(
        "--with-lock",
        action="store_true",
        help="Create a stale topic lock file for delete testing.",
    )
    args = parser.parse_args()

    home = Path(args.home).expanduser().resolve()
    os.environ["OPENLEARN_HOME"] = str(home)

    if args.reset and home.exists():
        shutil.rmtree(home)

    home.mkdir(parents=True, exist_ok=True)
    seed_course(started=args.started or args.with_session, with_session=args.with_session)

    if args.with_lock:
        cli.topic_lock_path(COURSE_SLUG).write_text("manual stale lock\n", encoding="utf-8")

    print("Seeded manual-test course")
    print(f"OPENLEARN_HOME={home}")
    print(f"Topic: {cli.topic_path(COURSE_SLUG)}")
    print(f"Context: {cli.topic_context_dir(COURSE_SLUG) / FIXTURE.name}")
    if args.with_lock:
        print(f"Stale lock: {cli.topic_lock_path(COURSE_SLUG)}")
    print("")
    print("Next command:")
    print(f"OPENLEARN_HOME={home} bash manual-tests/run-menu-isolated.sh")
    return 0


def seed_course(started: bool, with_session: bool) -> None:
    if not cli.topic_path(COURSE_SLUG).exists():
        cli.cmd_new(argparse.Namespace(topic=COURSE_NAME, goal=COURSE_GOAL))
    else:
        cli.set_active_topic(COURSE_SLUG)

    context_target = cli.topic_context_dir(COURSE_SLUG) / FIXTURE.name
    if not context_target.exists():
        cli.import_context_file(COURSE_SLUG, FIXTURE)

    if started:
        topic = cli.read_topic(COURSE_SLUG)
        metadata = dict(topic.metadata)
        metadata["course_started"] = True
        metadata["current_focus"] = "Vim modes"
        body = ensure_course_plan(topic.body)
        cli.write_topic(topic.path, metadata, body)

    if with_session:
        topic = cli.read_topic(COURSE_SLUG)
        if "I think insert mode is where commands run" not in topic.body:
            cli.append_session(
                topic,
                "chat",
                "I think insert mode is where commands run.",
                (
                    "Not quite. Normal mode is where commands run; insert mode is "
                    "for typing text into the file. Which mode lets you use commands "
                    "like dd or /search?"
                ),
            )


def ensure_course_plan(body: str) -> str:
    if "### Accepted Manual-Test Course Plan" in body:
        return body
    plan = textwrap.dedent(
        """

        ### Accepted Manual-Test Course Plan

        Scope: Practical Vim basics for everyday file editing.
        Excludes: Plugins, advanced macros, and Vimscript.
        Assumptions: Learner can use a terminal but is new to modal editing.
        Units:
        1. Modes: normal, insert, and command mode.
        2. Movement: h, j, k, l and word movement.
        3. Editing: x, i, a, o, dd, yy, and p.
        4. Saving and quitting safely.
        5. Search and small refactors.
        """
    ).rstrip()
    return body.rstrip() + "\n" + plan + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
