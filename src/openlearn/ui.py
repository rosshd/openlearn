from __future__ import annotations

import io
import re
from contextlib import nullcontext
from typing import ContextManager

from rich.console import Console
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console()

PROMPT = "> "


def _flush_console() -> None:
    try:
        console.file.flush()
    except Exception:
        pass


def _plain_console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(
        file=buffer,
        force_terminal=False,
        color_system=None,
        legacy_windows=False,
        width=console.width,
    ), buffer


def render_plain(renderable: object) -> str:
    temp_console, buffer = _plain_console()
    temp_console.print(renderable)
    text = buffer.getvalue().rstrip("\n")
    return "\n".join(line.rstrip() for line in text.splitlines())


def emit(renderable: object, output_func=print) -> None:
    if output_func is print:
        console.print(renderable)
        _flush_console()
        return
    text = render_plain(renderable)
    if not text:
        output_func("")
        return
    for line in text.splitlines():
        output_func(line)


def print_section(label: str, output_func=print) -> None:
    emit(Rule(Text(label.strip(), style="bold cyan"), style="dim"), output_func)


def status_bar(topic_name: str, progress: str, focus: str, reviews_due: int = 0) -> Text:
    text = Text()
    text.append("openlearn", style="bold cyan")
    text.append("  ·  ", style="dim")
    text.append(topic_name, style="bold")
    text.append("  ·  ", style="dim")
    text.append(progress, style="dim")
    if reviews_due > 0:
        text.append("  ·  ", style="dim")
        text.append(f"Reviews: {reviews_due}", style="yellow")
    text.append("  ·  ", style="dim")
    text.append(focus)
    return text


def menu_table(rows: list[tuple[str, str]]) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="dim")
    table.add_column()
    for key, label in rows:
        table.add_row(key, label)
    return table


def print_menu(rows: list[tuple[str, str]], output_func=print) -> None:
    emit(menu_table(rows), output_func)


def review_due_table(rows: list[tuple[str, str, str, str]]) -> Table:
    table = Table(title="Review due today", header_style="bold magenta")
    table.add_column("Topic")
    table.add_column("Concept")
    table.add_column("Due")
    table.add_column("Difficulty")
    for topic, concept, due, difficulty in rows:
        table.add_row(topic, concept, due, difficulty)
    return table


def print_error(message: str, output_func=print) -> None:
    if output_func is print:
        console.print(f"[red]✗[/] {message}")
        _flush_console()
    else:
        output_func(f"✗ {message}")


def print_info(message: str, output_func=print) -> None:
    if output_func is print:
        console.print(f"[dim]{message}[/]")
        _flush_console()
    else:
        output_func(message)


def format_action(label: str) -> str:
    return render_plain(Text(label, style="dim"))


def format_resume_line(line: str) -> str:
    match = re.match(r"^([^:]+):(.*)", line)
    if not match:
        return render_plain(Text(line, style="dim"))
    text = Text()
    text.append(match.group(1) + ":", style="dim")
    text.append(match.group(2))
    return render_plain(text)


def tutor_markdown(text: str) -> Markdown:
    return Markdown(text)


def emit_tutor_markdown(text: str, output_func=print) -> None:
    emit(tutor_markdown(text), output_func)


def thinking_progress(output_func=print) -> ContextManager[Progress | None]:
    if output_func is not print:
        return nullcontext()
    return Progress(
        SpinnerColumn(),
        TextColumn("[dim]waiting for tutor...[/]"),
        console=console,
        transient=True,
    )


def count_list(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def print_list(label: str, value: object, output_func=print) -> None:
    if not isinstance(value, list) or not value:
        emit(f"{label}: none", output_func)
        return
    emit(f"{label}:", output_func)
    for item in value:
        emit(f"- {item}", output_func)
