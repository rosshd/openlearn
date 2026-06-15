from __future__ import annotations


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
    output_func(f"== {label} ==")


def status_bar(topic_name: str, progress: str, focus: str) -> str:
    return f"[ openLearn ] topic: {topic_name} | progress: {progress} | focus: {focus}"


def format_user_prompt(prompt: str) -> str:
    return f"You > {prompt}"


def format_action(label: str) -> str:
    return f"Action: {label}"
