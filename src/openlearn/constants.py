from __future__ import annotations

from pathlib import Path


DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
# Hosted provider defaults that always require an API key; local or custom
# endpoints (for example Ollama) may be keyless.
HOSTED_BASE_URLS = frozenset(
    {
        "https://api.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "https://api.anthropic.com/v1",
    }
)
DEFAULT_MAX_TOKENS = 1600
STATE_FILE = "state.json"
CONFIG_FILE = "config.json"
PROMPT_TOPIC_LINE_LIMIT = 120
FIRST_LESSON_WORD_LIMIT = 220
CONTEXT_SUMMARY_CHAR_LIMIT = 60000
CONTEXT_SUMMARY_LINE_LIMIT = 120
QUICK_LEARN_MAX_FILES = 32
QUICK_LEARN_MAX_FILE_BYTES = 200000
QUICK_LEARN_MAX_TOTAL_CHARS = 240000
QUICK_LEARN_BUNDLE_CHAR_LIMIT = 60000
MANUAL_TEST_HOME = Path("/tmp/openlearn-manual-vim")
MANUAL_TEST_COURSE_NAME = "Practical Vim Foundations"
MANUAL_TEST_COURSE_SLUG = "practical-vim-foundations"
MANUAL_TEST_COURSE_GOAL = "Learn Vim well enough for everyday file editing."
MANUAL_TEST_CONTEXT_FILENAME = "practical-vim-syllabus.txt"
DEFAULT_COURSE_OPTIONS = {
    "quiz_after_chapter": True,
    "show_progress": True,
    "review_weak_spots": True,
    "hands_on_drills": True,
    "suggest_videos": False,
}
GAMING_OVERLAP_TRIGRAM_JACCARD = 0.6
GAMING_MIN_ANSWER_TOKENS = 6
ROLLING_PASS_RATE_WINDOW = 10
CUMULATIVE_QUIZ_MIN_PRACTICED_CONCEPTS = {
    "efficient": 3,
    "proficient": 4,
    "deep": 4,
}
CUMULATIVE_QUIZ_MIN_ANSWERS = {
    "efficient": 6,
    "proficient": 5,
    "deep": 4,
}
CUMULATIVE_QUIZ_DUE_REVIEW_THRESHOLD = {
    "efficient": 3,
    "proficient": 2,
    "deep": 1,
}
CUMULATIVE_QUIZ_SIZE = {
    "efficient": 3,
    "proficient": 5,
    "deep": 7,
}
CUMULATIVE_QUIZ_RECENT_UNITS = {
    "efficient": 1,
    "proficient": 2,
    "deep": 3,
}
PROFILES = {
    "efficient": {
        "mastery_score": 0.7,
        "mastery_rate": 0.7,
        "unit_mastery_fraction": 0.7,
        "transfer_required": False,
        "recognition_counts": True,
        "impasse_probe_frequency": "low",
    },
    "proficient": {
        "mastery_score": 0.8,
        "mastery_rate": 0.75,
        "unit_mastery_fraction": 0.8,
        "transfer_required": True,
        "recognition_counts": False,
        "impasse_probe_frequency": "medium",
    },
    "deep": {
        "mastery_score": 0.9,
        "mastery_rate": 0.85,
        "unit_mastery_fraction": 0.9,
        "transfer_required": True,
        "recognition_counts": False,
        "impasse_probe_frequency": "high",
    },
}
PLACEMENT_CONTEXT_FILENAME = "placement-quiz.txt"
COURSE_OPTION_LABELS = {
    "quiz_after_chapter": "Cumulative quizzes (spaced)",
    "show_progress": "Show progress reminders",
    "review_weak_spots": "Review weak spots before new chapters",
    "hands_on_drills": "Prefer hands-on drills",
    "suggest_videos": "Suggest YouTube videos after hard answers",
}
MANUAL_TEST_CONTEXT = """Course: Practical Vim Foundations

Learner profile:
- Comfortable with basic terminal commands.
- New to modal editing.
- Wants practical fluency rather than memorizing every command.

Learning goal:
Become comfortable using Vim for everyday editing, including navigation, insert/normal mode, saving/quitting, basic edits, search, and small refactors.

Course priorities:
- Teach modal editing early and repeatedly.
- Keep lessons short and hands-on.
- Prefer realistic editing drills over abstract command lists.
- Do not assume prior Vim knowledge.

Suggested lesson sequence:
1. Normal mode, insert mode, and command mode.
2. Movement with h, j, k, l plus word movement.
3. Editing with x, i, a, o, dd, yy, p.
4. Saving, quitting, and recovering from common mistakes.
5. Search with / and n.
6. Basic changes with cw, ciw, and visual selection.

Known learner weak spots:
- May confuse insert mode and normal mode.
- May forget how to quit safely.
- Benefits from tiny drills with immediate feedback.

Assessment idea:
By the end, learner should edit a short paragraph, move around without arrow keys, delete/move lines, search for text, and save/quit confidently.
"""
