from __future__ import annotations

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
