from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest

if sys.platform == "win32":
    pytest.skip("pexpect.spawn requires a POSIX pty", allow_module_level=True)

import pexpect

from openlearn import cli, lesson_policy


def test_menu_quit(spawn_openlearn) -> None:
    proc = spawn_openlearn.spawn("menu")
    try:
        proc.expect("> ")
        proc.sendline("q")
        proc.expect(pexpect.EOF)
        proc.child.close()
        assert proc.child.exitstatus == 0
    finally:
        proc.close()


def test_repl_blank_enter_no_double_prompt(spawn_openlearn) -> None:
    spawn_openlearn.create_topic()
    proc = spawn_openlearn.spawn("repl")
    try:
        proc.expect("openlearn> ")
        proc.sendline("")
        proc.expect("openlearn> ")
        proc.sendline("")
        proc.expect("openlearn> ")
        assert "openlearn> openlearn>" not in proc.clean_output
        proc.sendline("/q")
        proc.expect(pexpect.EOF)
    finally:
        proc.close()


def test_repl_ctrl_c_exits(spawn_openlearn) -> None:
    spawn_openlearn.create_topic()
    proc = spawn_openlearn.spawn("repl")
    try:
        proc.expect("openlearn> ")
        proc.sendcontrol("c")
        proc.expect(pexpect.EOF)
        proc.child.close()
        assert proc.child.exitstatus == 130
    finally:
        proc.close()


def test_repl_arrow_key_no_escape_literal(spawn_openlearn) -> None:
    spawn_openlearn.create_topic()
    proc = spawn_openlearn.spawn("repl")
    try:
        proc.expect("openlearn> ")
        proc.send("\x1b[D")
        proc.sendline("/q")
        proc.expect(pexpect.EOF)
        assert "^[[D" not in proc.clean_output
        assert "\x1b[D" not in proc.log.getvalue()
    finally:
        proc.close()


def test_repl_unknown_command_no_crash(spawn_openlearn) -> None:
    spawn_openlearn.create_topic()
    proc = spawn_openlearn.spawn("repl")
    try:
        proc.expect("openlearn> ")
        proc.sendline("/badcommand")
        proc.expect("unknown REPL command")
        proc.expect("openlearn> ")
        proc.sendline("/q")
        proc.expect(pexpect.EOF)
    finally:
        proc.close()


@pytest.mark.parametrize("terminator", ["\n", "\r", "\r\n"])
def test_repl_multiline_paste_is_one_learner_message(spawn_openlearn, terminator) -> None:
    spawn_openlearn.create_topic()
    proc = spawn_openlearn.spawn("repl")
    try:
        proc.expect("openlearn> ")
        message = "This was our dialogue:\nLesson: First pasted café line.\n\nCheck: Second line?"
        proc.send(message.replace("\n", terminator) + terminator)
        proc.expect("openlearn> ")
        proc.sendline("/q")
        proc.expect(pexpect.EOF)

        topic_path = Path(spawn_openlearn.env["OPENLEARN_HOME"]) / "learning-topics" / "workflow.md"
        topic_text = topic_path.read_text(encoding="utf-8")
        assert topic_text.count(" - chat") == 1
        _metadata, body = cli.parse_topic(topic_text)
        _context, session_log = cli.split_session_log(body)
        chat_entries = [entry for entry in cli.session_entries(session_log) if entry["kind"] == "chat"]
        assert len(chat_entries) == 1
        stored = chat_entries[0]["prompt"]
        if terminator == "\r\n":
            # Readline/PTY translation differs across platforms: CRLF may
            # reach the reader as doubled boundaries or a visible pair.
            # This blank has four LF boundaries on Mac and three on Linux.
            # Exact byte preservation is checked by the raw-reader tests.
            assert [line for line in stored.split("\n") if line] == [
                line for line in message.split("\n") if line
            ]
            assert "café line.\n\n" in stored
        else:
            assert stored == message
        assert topic_text.count("Check: Second line?") == 1
    finally:
        proc.close()


def test_quick_learn_file_reaches_repl(spawn_openlearn) -> None:
    home = Path(spawn_openlearn.env["OPENLEARN_HOME"])
    source = home / "midterm-review.md"
    source.write_text(
        "# Midterm review\n\n- Explain sorting complexity.\n- Trace binary search.\n",
        encoding="utf-8",
    )
    proc = spawn_openlearn.spawn("quick", str(source), timeout=10)
    try:
        proc.expect("First lesson")
        proc.expect("For example,")
        proc.expect("openlearn> ")
        assert "Quick Learn plan" in proc.clean_output
        assert "Traceback" not in proc.clean_output
        assert "\x1b[" not in proc.clean_output
        proc.sendline("/q")
        proc.expect(pexpect.EOF)
        topic = home / "learning-topics" / "midterm-review.md"
        assert topic.exists()
        metadata, body = cli.parse_topic(topic.read_text(encoding="utf-8"))
        assert metadata["learning_mode"] == "quick"
        _context, log = cli.split_session_log(body)
        lesson = cli.session_entries(log)[-1]["response"]
        assert lesson_policy.first_lesson_response_is_valid(lesson)
        assert "<!--" not in lesson
        state = json.loads(topic.with_suffix(".state.json").read_text(encoding="utf-8"))
        assert state["slide_coverage"]["1:1"]
        assert "pending_question" not in state
        assert metadata["known"] == []
    finally:
        proc.close()


def test_videos_url_plaintext(spawn_openlearn) -> None:
    spawn_openlearn.create_topic()
    proc = spawn_openlearn.spawn("repl")
    try:
        proc.expect("openlearn> ")
        proc.sendline("/videos sorting")
        proc.expect("openlearn> ")
        assert "https://www.youtube.com/watch?v=mock-openlearn" in proc.clean_output
        proc.sendline("/q")
        proc.expect(pexpect.EOF)
    finally:
        proc.close()
