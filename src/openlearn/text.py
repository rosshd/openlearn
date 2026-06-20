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
    text = re.sub(r"(?im)^\s*answer\s*key\s*:\s*[A-D]\s*$", "", text)
    text = re.sub(r"(?im)^\s*correct\s+answer\s*:\s*[A-D]\s*[\).:-]?.*$", "", text)
    text = re.sub(r"(?im)^\s*\(?answer\s*:\s*[A-D]\)?.*$", "", text)
    blocked = re.compile(
        r"\b(system reminder|operational mode|read-only mode)\b", re.IGNORECASE
    )
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
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    return text.strip()


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
