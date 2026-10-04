"""Pure helpers for interpreting answer evidence from a tutor turn."""

from __future__ import annotations

import re

from openlearn.constants import (
    GAMING_MIN_ANSWER_TOKENS,
    GAMING_OVERLAP_TRIGRAM_JACCARD,
)


def answer_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def token_trigrams(tokens: list[str]) -> set[tuple[str, str, str]]:
    if len(tokens) < 3:
        return set()
    return set(zip(tokens, tokens[1:], tokens[2:]))


def trigram_jaccard(left: str, right: str) -> float:
    left_trigrams = token_trigrams(answer_tokens(left))
    right_trigrams = token_trigrams(answer_tokens(right))
    if not left_trigrams or not right_trigrams:
        return 0.0
    return len(left_trigrams & right_trigrams) / len(left_trigrams | right_trigrams)


def normalized_answer_kind(value: object) -> str:
    return (
        value if isinstance(value, str) and value in {"recognition", "production"} else "production"
    )


def answer_eval_is_transfer(value: object) -> bool:
    return value is True


def judge_gameable(value: object) -> bool:
    return value is True


def detect_gaming_suspected(
    learner_prompt: str, shown_text: str, answer_kind: str, gameable: bool
) -> tuple[bool, float, int]:
    tokens = answer_tokens(learner_prompt)
    overlap = trigram_jaccard(learner_prompt, shown_text)
    overlap_suspected = (
        answer_kind == "production"
        and len(tokens) >= GAMING_MIN_ANSWER_TOKENS
        and overlap >= GAMING_OVERLAP_TRIGRAM_JACCARD
    )
    return overlap_suspected or gameable, overlap, len(tokens)
