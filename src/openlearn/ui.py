from __future__ import annotations

import io
import re
from contextlib import nullcontext
from typing import ContextManager

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

OPENLEARN_THEME = Theme(
    {
        "markdown.strong": "bold cyan",
    }
)

console = Console(theme=OPENLEARN_THEME)

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


def stats_dashboard(
    label: str,
    *,
    streak: int,
    longest_streak: int,
    weekly_minutes: float,
    forecast: dict[str, int],
    mastery_rows: list[dict[str, object]],
) -> Panel:
    def days(value: int) -> str:
        return f"{value} {'day' if value == 1 else 'days'}"

    overview = Table.grid(padding=(0, 2))
    overview.add_column(style="dim")
    overview.add_column(justify="right")
    overview.add_row("Current streak", days(streak))
    overview.add_row("Longest streak", days(longest_streak))
    overview.add_row("Minutes this week", f"{weekly_minutes:g}")
    overview.add_row("Reviews due now", str(forecast.get("due_today", 0)))
    overview.add_row("Reviews next 7 days", str(forecast.get("due_this_week", 0)))
    overview.add_row("Reviews later", str(forecast.get("due_later", 0)))

    mastery = Table(title="Mastery by unit", header_style="bold cyan")
    mastery.add_column("Unit", justify="right")
    mastery.add_column("Title")
    mastery.add_column("Concepts", justify="right")
    mastery.add_column("Mastery", justify="right")
    if mastery_rows:
        for row in mastery_rows:
            mastery.add_row(
                str(row.get("unit", "")),
                str(row.get("title", "")),
                f"{row.get('known', 0)}/{row.get('total', 0)}",
                f"{row.get('percent', 0)}%",
            )
    else:
        mastery.add_row("", "No structured course units", "0/0", "0%")

    return Panel(
        Group(overview, Text(""), mastery),
        title=Text(f"Study stats - {label}", style="bold cyan"),
        title_align="left",
        border_style="cyan",
        padding=(1, 2),
    )


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


def emit_resume_line(line: str, output_func=print) -> None:
    """Emit a resume-panel line with a bold label prefix.

    Unlike format_resume_line (which flattens to plain text), this routes a
    styled Text through emit() so the label renders bold on a real terminal
    while still degrading to plain text for piped/test output.
    """
    match = re.match(r"^([^:]+):(.*)", line)
    if not match:
        emit(Text(line, style="bold cyan"), output_func)
        return
    text = Text()
    text.append(match.group(1) + ":", style="bold cyan")
    text.append(match.group(2))
    emit(text, output_func)


def tutor_markdown(text: str) -> Markdown:
    styled = re.sub(
        r"(?m)^(Lesson|Feedback|Example|Check|Hint|Next|Action):",
        r"**\1:**",
        text,
    )
    # Rich's Markdown collapses single \n to a space, so multiple-choice option
    # lines need Markdown hard line breaks (two trailing spaces) to stay separate.
    styled = re.sub(r"(?m)(\?)\n(?=[A-D]\) )", r"\1  \n", styled)
    styled = re.sub(r"(?m)^([A-D]\) .+)$", r"\1  ", styled)
    return Markdown(styled)


def emit_tutor_markdown(text: str, output_func=print) -> None:
    emit(tutor_markdown(text), output_func)


def tutor_response_panel(text: str) -> Panel:
    return Panel(
        tutor_markdown(text),
        title=Text("Tutor", style="bold cyan"),
        title_align="left",
        border_style="cyan",
        padding=(1, 2),
    )


def emit_tutor_response(text: str, output_func=print) -> None:
    if output_func is print:
        emit(tutor_response_panel(text), output_func)
        return
    output_func("Tutor")
    emit_tutor_markdown(text, output_func)
    output_func("End tutor response")


class TutorResponseStream:
    """Incrementally redraw one tutor panel as model tokens arrive."""

    def __init__(self) -> None:
        self._live = Live(
            tutor_response_panel(" "),
            console=console,
            refresh_per_second=12,
            vertical_overflow="visible",
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        console.print()
        self._live.start(refresh=True)
        self._started = True

    def update(self, text: str) -> None:
        if text:
            self._live.update(tutor_response_panel(text), refresh=False)

    def finish(self, text: str) -> None:
        self.update(text)
        if self._started:
            self._live.stop()
            self._started = False
        console.print()
        _flush_console()

    def abort(self) -> None:
        if self._started:
            self._live.stop()
            self._started = False
        _flush_console()


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
