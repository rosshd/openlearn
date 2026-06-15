"""Minimal prompt-toolkit TUI wrapper for openlearn.

This module implements a lightweight REPL using prompt_toolkit that delegates
to the existing run_repl and handle_repl_command functions. It avoids importing
prompt_toolkit at module import time; the TUI entrypoint imports it lazily so
the rest of the CLI remains usable without the extra dependency.
"""
from __future__ import annotations

from typing import List

from .cli import run_repl, resolve_topic_slug, handle_repl_command


def run_tui(topic: str | None = None, model: str | None = None) -> int:
    """Launch a minimal TUI built on prompt-toolkit.

    If prompt_toolkit is not installed, instruct the user how to install it.
    """
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.patch_stdout import patch_stdout
        from prompt_toolkit.history import FileHistory
    except Exception:
        print(
            "prompt-toolkit is required for the TUI. Install with: python -m pip install prompt-toolkit"
        )
        return 2

    # Helper: history file under project_home
    from pathlib import Path
    from .cli import project_home, recent_topic_summaries

    history_path = project_home() / "tui_history.txt"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    # Simple completer that suggests /commands and recent topics
    class OpenLearnCompleter(Completer):
        def __init__(self):
            self._commands = [
                "/resume",
                "/next",
                "/review",
                "/status",
                "/summary",
                "/options",
                "/plan",
                "/progress",
                "/scope",
                "/repair",
                "/active",
                "/recent",
                "/new",
                "/delete",
                "/ask",
                "/quit",
            ]

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if text.startswith("/"):
                for c in self._commands:
                    if c.startswith(text):
                        yield Completion(c, start_position=-len(text))
            else:
                # Suggest recent topic slugs and titles
                try:
                    topics = recent_topic_summaries()
                except Exception:
                    topics = []
                for t in topics:
                    label = f"{t.slug} - {t.metadata.get('topic', '')}"
                    if t.slug.startswith(text) or label.startswith(text):
                        yield Completion(t.slug, start_position=-len(text), display=label)

    session = PromptSession(
        message="You > ",
        completer=OpenLearnCompleter(),
        complete_while_typing=True,
        history=FileHistory(str(history_path)),
    )

    # Use run_repl for core loop via a thin wrapper to integrate prompt-toolkit input
    # We'll emulate run_repl behavior but use session.prompt for input.

    topic_slug = None
    if topic:
        try:
            topic_slug = resolve_topic_slug(topic)
        except Exception:
            topic_slug = None
    return _prompt_toolkit_loop(session, topic_slug, model)


def _prompt_toolkit_loop(session: "PromptSession", topic_slug: str | None, model: str | None) -> int:
    """Loop that reads input from prompt-toolkit and delegates to existing handlers."""
    # Reuse run_repl's logic for commands by calling handle_repl_command and ask_topic
    from .cli import run_repl, ask_topic

    # Show intro similar to run_repl
    print("== openLearn TUI ==")
    print("Type a question to ask the active topic. Commands: /help, /resume, /next, /review, /summary, /options, /plan, /progress, /scope, /q")

    if topic_slug:
        try:
            from .cli import set_active_topic

            set_active_topic(topic_slug)
        except Exception:
            pass

    while True:
        try:
            # Import patch_stdout here so this module doesn't require prompt-toolkit
            # at import time unless the TUI is launched.
            from prompt_toolkit.patch_stdout import patch_stdout

            with patch_stdout():
                text = session.prompt()
        except (KeyboardInterrupt, EOFError):
            print("")
            return 0
        prompt = text.strip()
        if not prompt:
            continue
        if prompt.lower() in {"/q", "/quit", "/exit", "quit", "exit", "q"}:
            return 0
        try:
            if prompt.startswith("/"):
                # strip leading slash and delegate to handler
                handle_repl_command(prompt[1:], model=model, input_func=input, output_func=print)
            else:
                ask_topic(None, prompt, model)
        except Exception as exc:
            print(f"error: {exc}")


if __name__ == "__main__":
    run_tui()
