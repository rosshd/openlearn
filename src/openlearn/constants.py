from __future__ import annotations

from pathlib import Path


DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MAX_TOKENS = 1600
STATE_FILE = "state.json"
CONFIG_FILE = "config.json"
PROMPT_TOPIC_LINE_LIMIT = 120
FIRST_LESSON_WORD_LIMIT = 220
CONTEXT_SUMMARY_CHAR_LIMIT = 60000
CONTEXT_SUMMARY_LINE_LIMIT = 120
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
PLACEMENT_CONTEXT_FILENAME = "placement-quiz.txt"
COURSE_OPTION_LABELS = {
    "quiz_after_chapter": "Quiz after finished chapter",
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
