from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[96m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RED = "\033[91m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"

PROMPT = "> "


def color_enabled() -> bool:
    return "NO_COLOR" not in os.environ and bool(getattr(sys.stdout, "isatty", lambda: False)())


def styled(*codes: str, text: str) -> str:
    if not color_enabled() or not codes:
        return text
    return "".join(codes) + text + RESET


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def print_list(label: str, value: object) -> None:
    if not isinstance(value, list) or not value:
        print(f"{label}: none")
        return
    print(f"{label}:")
    for item in value:
        print(f"- {item}")


def count_list(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def print_section(label: str, output_func=print) -> None:
    title = label.strip()
    output_func(styled(BOLD, CYAN, text=f"  {title}"))
    output_func(styled(DIM, text=f"  {'─' * max(3, len(title))}"))


def truncate(value: str, width: int) -> str:
    if width <= 1:
        return value[: max(0, width)]
    if len(value) <= width:
        return value
    return value[: max(0, width - 1)].rstrip() + "…"


def status_bar(topic_name: str, progress: str, focus: str) -> str:
    width = shutil.get_terminal_size((88, 24)).columns
    fixed = f"  openlearn  ·  {topic_name}  ·  {progress}  ·  "
    focus_width = max(12, width - len(strip_ansi(fixed)))
    parts = [
        "",
        styled(BOLD, CYAN, text="  openlearn"),
        styled(DIM, text="  ·  "),
        styled(BOLD, WHITE, text=topic_name),
        styled(DIM, text="  ·  "),
        styled(DIM, WHITE, text=progress),
        styled(DIM, text="  ·  "),
        truncate(focus, focus_width),
    ]
    return "".join(parts)


def format_user_prompt(prompt: str) -> str:
    return f"{PROMPT}{prompt}"


def format_action(label: str) -> str:
    return styled(DIM, text=label)


def format_error(message: str) -> str:
    return f"{styled(RED, text='✗')} {message}"


def format_resume_line(line: str) -> str:
    m = re.match(r"^([^:]+):(.*)", line)
    if m:
        return styled(DIM, text=m.group(1) + ":") + m.group(2)
    return styled(DIM, text=line)


def format_menu_item(key: str, label: str) -> str:
    return f"  {styled(DIM, text=key)}  {label}"


def menu_separator() -> str:
    return styled(DIM, text="  ─")


def wrap_prose(text: str, width: int | None = None) -> str:
    limit = width or min(shutil.get_terminal_size((94, 24)).columns - 6, 88)
    limit = max(30, limit)
    lines = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            lines.append(line)
            continue
        if (
            not stripped
            or in_fence
            or line.startswith(" ")
            or re.search(r"\$[A-Za-z_]|\$\(", line)
            or re.match(r"^[A-D][\).:-]\s+", stripped)
            or re.match(r"^(Lesson|Example|Check|Feedback|Quiz|Next):", stripped)
        ):
            lines.append(line)
            continue
        lines.append(
            textwrap.fill(
                stripped,
                width=limit,
                subsequent_indent="  ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return "\n".join(lines)


def format_tutor_output(text: str) -> str:
    formatted = []
    for line in wrap_prose(text).splitlines():
        formatted.append(format_tutor_line(line))
    return "\n".join(formatted)


def format_tutor_line(line: str) -> str:
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    option = re.match(r"^([A-D])([\).:-]\s+)(.*)$", stripped)
    if option:
        return indent + styled(BOLD, text=option.group(1)) + option.group(2) + option.group(3)

    label = re.match(r"^(Lesson|Example|Check|Feedback|Quiz|Next):(.*)$", stripped)
    if not label:
        return line

    name = label.group(1)
    rest = label.group(2)
    codes = {
        "Lesson": (BOLD, CYAN),
        "Example": (BOLD, BLUE),
        "Check": (BOLD, YELLOW),
        "Quiz": (BOLD, MAGENTA),
        "Next": (BOLD, WHITE),
    }.get(name, (BOLD, WHITE))
    if name == "Feedback":
        lowered = rest.lower()
        codes = (BOLD, GREEN) if "correct" in lowered else (BOLD, RED)
    return indent + styled(*codes, text=f"{name}:") + rest
