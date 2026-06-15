#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

WORK_HOME="${OPENLEARN_HOME:-$(mktemp -d /tmp/openlearn-smoke.XXXXXX)}"
FIXTURE="$ROOT_DIR/manual-tests/context/practical-vim-syllabus.txt"

echo "Running non-model smoke tests"
echo "OPENLEARN_HOME=$WORK_HOME"

OPENLEARN_HOME="$WORK_HOME-empty" python -m openlearn menu <<'EOF' >/tmp/openlearn-smoke-empty.out
1
b
q
EOF

if test -d "$WORK_HOME-empty/learning-topics" && compgen -G "$WORK_HOME-empty/learning-topics/*.md" >/dev/null; then
  echo "FAIL: empty back path created a topic"
  exit 1
fi

OPENLEARN_HOME="$WORK_HOME-draft" python -m openlearn menu <<EOF >/tmp/openlearn-smoke-draft.out
1
1
Practical Vim Foundations
2
Learn Vim well enough for everyday file editing.
3
$FIXTURE
b
y
q
EOF

test -f "$WORK_HOME-draft/learning-topics/practical-vim-foundations.md"
test -f "$WORK_HOME-draft/learning-topics/practical-vim-foundations/context/practical-vim-syllabus.txt"

OPENLEARN_HOME="$WORK_HOME-delete" python "$ROOT_DIR/manual-tests/seed-vim-course.py" --reset --draft --with-lock >/tmp/openlearn-smoke-seed.out
OPENLEARN_HOME="$WORK_HOME-delete" python -m openlearn delete practical-vim-foundations --yes >/tmp/openlearn-smoke-delete.out

test ! -e "$WORK_HOME-delete/learning-topics/practical-vim-foundations.md"
test ! -e "$WORK_HOME-delete/learning-topics/.practical-vim-foundations.md.lock"
test ! -d "$WORK_HOME-delete/learning-topics/practical-vim-foundations"

echo "OK: non-model smoke tests passed"
echo "Outputs: /tmp/openlearn-smoke-empty.out /tmp/openlearn-smoke-draft.out /tmp/openlearn-smoke-seed.out /tmp/openlearn-smoke-delete.out"
