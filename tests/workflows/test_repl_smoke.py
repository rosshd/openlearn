from __future__ import annotations

from pathlib import Path

import pexpect


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


def test_repl_multiline_paste_is_one_learner_message(spawn_openlearn) -> None:
    spawn_openlearn.create_topic()
    proc = spawn_openlearn.spawn("repl")
    try:
        proc.expect("openlearn> ")
        proc.send(
            "This was our dialogue:\nLesson: First pasted line.\nCheck: Second pasted line?\n"
        )
        proc.expect("openlearn> ")
        proc.sendline("/q")
        proc.expect(pexpect.EOF)

        topic_path = Path(spawn_openlearn.env["OPENLEARN_HOME"]) / "learning-topics" / "workflow.md"
        topic_text = topic_path.read_text(encoding="utf-8")
        assert topic_text.count(" - chat") == 1
        assert "This was our dialogue:\nLesson: First pasted line." in topic_text
        assert "Check: Second pasted line?" in topic_text
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
        proc.expect("Normal vs Insert")
        proc.expect("Answer> ")
        assert "Quick Learn plan" in proc.clean_output
        assert "Traceback" not in proc.clean_output
        assert "\x1b[" not in proc.clean_output
        proc.sendline("/q")
        proc.expect(pexpect.EOF)
        topic = home / "learning-topics" / "midterm-review.md"
        assert topic.exists()
        assert '"learning_mode": "quick"' in topic.read_text(encoding="utf-8")
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
