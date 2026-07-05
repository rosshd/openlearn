from __future__ import annotations

import json
import re
from collections import deque


def parse_metadata_update(raw_update: str) -> dict[str, object]:
    raw_update = raw_update.strip()
    if not raw_update:
        return {}
    if raw_update.startswith("```"):
        raw_update = re.sub(r"^```(?:json)?\s*", "", raw_update)
        raw_update = re.sub(r"\s*```$", "", raw_update)
    if not raw_update.startswith("{"):
        match = re.search(r"\{.*\}", raw_update, flags=re.DOTALL)
        if not match:
            return {}
        raw_update = match.group(0)
    data = json.loads(raw_update)
    return data if isinstance(data, dict) else {}


def last_question(text: str) -> str:
    matches = re.findall(r"[^.!?\n]*(?:\?+)", one_line(text))
    return matches[-1].strip() if matches else ""


def snippet(text: str, limit: int) -> str:
    value = one_line(text)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def concept_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def first_lines(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    return "\n".join(text.split("\n", limit)[:limit])


def last_lines(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    return "\n".join(deque(text.splitlines(), maxlen=limit))


def sanitize_model_output(text: str) -> str:
    text = re.sub(r"(?is)<system-reminder>.*?</system-reminder>", "", text)
    text = re.sub(r"(?is)<!--\s*answer\s*:\s*[A-D]\s*-->", "", text)
    text = re.sub(r"(?is)<!--\s*covered\s*:\s*.*?-->", "", text)
    text = re.sub(r"(?im)^\s*answer\s*key\s*:\s*[A-D]\s*$", "", text)
    text = re.sub(r"(?im)^\s*correct\s+answer\s*:\s*[A-D]\s*[\).:-]?.*$", "", text)
    text = re.sub(r"(?im)^\s*\(?answer\s*:\s*[A-D]\)?.*$", "", text)
    blocked = re.compile(r"\b(system reminder|operational mode|read-only mode)\b", re.IGNORECASE)
    instruction_action = re.compile(
        r"^\s*Action:\s+(Ask|Reiterate|Introduce|Explain|Provide|Fill in|Create|Generate|Respond|Evaluate)\b",
        re.IGNORECASE,
    )
    cleaned_lines = []
    seen_action_lines = set()
    for line in text.splitlines():
        if blocked.search(line) or instruction_action.search(line):
            continue
        if line.startswith("Action:"):
            key = line.strip().casefold()
            if key in seen_action_lines:
                continue
            seen_action_lines.add(key)
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    text = re.sub(r"(?m)^(\s*)\*\s+", r"\1- ", text)
    return normalize_multiple_choice_layout(text).strip()


def normalize_multiple_choice_layout(text: str) -> str:
    """Put inline A-D choices on separate lines for terminal rendering."""
    normalized: list[str] = []
    option_pattern = re.compile(r"(?<!\w)([A-D])[\).:-]\s+")
    for line in text.splitlines():
        matches = list(option_pattern.finditer(line))
        should_split = len(matches) >= 2 or (
            len(matches) == 1
            and bool(line[: matches[0].start()].strip())
            and (
                "?" in line[: matches[0].start()]
                or line[: matches[0].start()].strip().lower().startswith("check:")
            )
        )
        if not should_split:
            normalized.append(line)
            continue

        prefix = line[: matches[0].start()].rstrip()
        if prefix:
            normalized.append(prefix)
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            option = line[match.end() : end].strip()
            normalized.append(f"{match.group(1).upper()}) {option}")
    return "\n".join(normalized)


def sanitize_stream_preview(text: str) -> str:
    """Sanitize an incomplete streamed response without exposing hidden metadata."""
    text = re.sub(r"(?is)<!--.*$", "", text)
    text = re.sub(r"(?is)<system-reminder\b.*$", "", text)
    return sanitize_model_output(text)


def extract_answer_key(text: str) -> str:
    match = re.search(r"(?is)<!--\s*answer\s*:\s*([A-D])\s*-->", text)
    if match:
        return match.group(1).upper()
    match = re.search(r"(?im)^\s*answer\s*key\s*:\s*([A-D])\s*$", text)
    if match:
        return match.group(1).upper()
    match = re.search(r"(?im)^\s*correct\s+answer\s*:\s*([A-D])\s*[\).:-]?", text)
    if match:
        return match.group(1).upper()
    match = re.search(r"(?im)^\s*\(?answer\s*:\s*([A-D])\)?", text)
    return match.group(1).upper() if match else ""


def extract_covered_concepts(text: str) -> list[str]:
    match = re.search(r"(?is)<!--\s*covered\s*:\s*(.*?)\s*-->", text)
    if not match:
        return []
    values = re.split(r"\s*;\s*|\s*,\s*", match.group(1))
    covered: list[str] = []
    seen: set[str] = set()
    for value in values:
        label = value.strip()
        key = label.casefold()
        if not label or key in seen:
            continue
        seen.add(key)
        covered.append(label)
    return covered
