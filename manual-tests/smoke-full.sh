#!/usr/bin/env bash
set -euo pipefail

# Smoke test for openLearn CLI.
# - Seeds manual test course
# - Runs a short scripted interactive resume session
# - Executes a set of non-interactive commands to exercise core flows
# Usage: manual-tests/smoke-full.sh [--mock]

MOCK=0
if [ "${1:-}" = "--mock" ]; then
  MOCK=1
fi

OPENLEARN_HOME=${OPENLEARN_HOME:-/tmp/openlearn-smoke-$$}
export OPENLEARN_HOME
export PYTHONPATH=src

if [ "$MOCK" = "1" ]; then
  export OPENLEARN_MOCK=1
  echo "Running in MOCK mode (no network)"
else
  if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "ERROR: OPENAI_API_KEY must be set to run this smoke script unless --mock is used"
    exit 2
  fi
fi

echo "Smoke run: OPENLEARN_HOME=${OPENLEARN_HOME}"
mkdir -p "$OPENLEARN_HOME"

echo "Seeding manual test course (no menu)..."
python -m openlearn test --reset --resume --home "$OPENLEARN_HOME" --no-menu

echo "Running scripted interactive resume (answers + /next /summary /review)..."
# Scripted interactive: Resume, answer, ask next/summary/review, then quit
printf '1\nIt inserts text; press Esc to return to Normal mode.\n/next\n/summary\n/review\nq\n' | python -m openlearn

echo "Exercising context import/paste/summarize, change scope, and progress..."
# Create a topic for testing
python -m openlearn new "Smoke Topic" --goal "Smoke test"
# Paste a context file to the topic
printf 'context.txt\nThis is mock context for smoke testing.\n.\n' | env OPENLEARN_HOME="$OPENLEARN_HOME" python -m openlearn >/dev/null 2>&1 || true
# Use menu context paste via non-interactive write_context_text: create a file directly
python - <<'PY'
from pathlib import Path
import os
os.environ.setdefault('OPENLEARN_HOME', '${OPENLEARN_HOME}')
from openlearn.cli import write_context_text, topic_context_dir
slug='smoke-topic'
path=write_context_text(slug,'smoke-context.txt','This is smoke test context.\n')
print('Wrote context:',path)
PY
# Summarize the context (uses mock if enabled)
python -m openlearn test --home "$OPENLEARN_HOME" --no-menu >/dev/null 2>&1 || true
python - <<'PY'
import os
os.environ.setdefault('OPENLEARN_HOME','${OPENLEARN_HOME}')
from openlearn.cli import summarize_context_file, topic_context_dir
slug='smoke-topic'
ctx=topic_context_dir(slug)/'smoke-context.txt'
print('Summarize result path:', summarize_context_file(slug, ctx))
PY
# Change scope to force course plan generation and saving
python - <<'PY'
import os
os.environ.setdefault('OPENLEARN_HOME','${OPENLEARN_HOME}')
from openlearn.cli import change_course_scope
print('Changing scope...')
change_course_scope('Focus more on drills', input_func=lambda p: 'y', output_func=print)
PY
# Set progress
python -m openlearn status smoke-topic || true
python -m openlearn summary smoke-topic || true
python -m openlearn next smoke-topic || true
python -m openlearn review smoke-topic || true
python -m openlearn chat smoke-topic "Give a one-sentence summary of what smoke testing verifies." || true

echo "Smoke run completed. Logs are left in ${OPENLEARN_HOME}"
