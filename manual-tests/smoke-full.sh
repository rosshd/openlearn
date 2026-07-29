#!/usr/bin/env bash
set -euo pipefail

# Strict end-to-end smoke test for the public openLearn application interface.
# The journey uses only openlearn commands for product actions. Shell operations
# create isolated input fixtures and verify learner-visible output.
#
# Usage: manual-tests/smoke-full.sh [--mock]

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENLEARN_BIN="${OPENLEARN:-$ROOT_DIR/.venv/bin/openlearn}"
MOCK=0
if [ "${1:-}" = "--mock" ]; then
  MOCK=1
elif [ -n "${1:-}" ]; then
  echo "usage: $0 [--mock]" >&2
  exit 2
fi

if [ ! -x "$OPENLEARN_BIN" ]; then
  echo "ERROR: openlearn executable not found: $OPENLEARN_BIN" >&2
  exit 2
fi

CREATED_WORK_HOME=0
if [ -n "${OPENLEARN_HOME:-}" ]; then
  WORK_HOME="$OPENLEARN_HOME"
else
  WORK_HOME="$(mktemp -d /tmp/openlearn-e2e.XXXXXX)"
  CREATED_WORK_HOME=1
fi
if [ -n "$(find "$WORK_HOME" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
  echo "ERROR: OPENLEARN_HOME must be an empty isolated directory: $WORK_HOME" >&2
  exit 2
fi

cleanup() {
  if [ "$CREATED_WORK_HOME" = "1" ] && [ "${OPENLEARN_E2E_KEEP:-0}" != "1" ]; then
    rm -rf -- "$WORK_HOME"
  fi
}
trap cleanup EXIT

export OPENLEARN_HOME="$WORK_HOME"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
if [ "$MOCK" = "1" ]; then
  unset OPENAI_API_KEY OPENLEARN_MODEL OPENLEARN_EXTRACTOR_MODEL OPENLEARN_BASE_URL
  export OPENLEARN_MOCK=1
  echo "Running in MOCK mode (no provider calls)"
elif [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "ERROR: OPENAI_API_KEY must be set unless --mock is used" >&2
  exit 2
fi

ARTIFACTS="$WORK_HOME/e2e-output"
FIXTURES="$WORK_HOME/e2e-input"
mkdir -p "$ARTIFACTS" "$FIXTURES/scan"
cp "$ROOT_DIR/manual-tests/context/practical-vim-syllabus.txt" "$FIXTURES/vim.txt"
printf '%s\n' '# Registers' 'Use named registers to preserve text.' >"$FIXTURES/scan/registers.md"
printf '%s\n' '# Motions' 'Combine operators with motions.' >"$FIXTURES/scan/motions.txt"

run_openlearn() {
  "$OPENLEARN_BIN" "$@"
}

capture() {
  local name="$1"
  shift
  run_openlearn "$@" >"$ARTIFACTS/$name.out" 2>&1
}

assert_output() {
  local name="$1"
  local expected="$2"
  if ! grep -Fq -- "$expected" "$ARTIFACTS/$name.out"; then
    echo "FAIL: '$expected' not found in $ARTIFACTS/$name.out" >&2
    sed -n '1,160p' "$ARTIFACTS/$name.out" >&2
    exit 1
  fi
}

echo "Smoke run: OPENLEARN_HOME=$WORK_HOME"

echo "Checking the complete parser surface..."
capture version --version
assert_output version "openlearn "
for command in \
  init menu templates doctor test repl shell tui config new interview attempt \
  delete list recent status stats summary repair active edit import quick \
  quick-learn paste chat review due videos resume next chapter
do
  capture "help-$command" "$command" --help
done
for command in show set-key set-model set-extractor-model clear-extractor-model set-base-url set-editor clear-key
do
  capture "help-config-$command" config "$command" --help
done
for command in setup profile edit clear placement
do
  capture "help-interview-$command" interview "$command" --help
done
for command in list inspect resume abandon retry reflect verify-transfer
do
  capture "help-attempt-$command" attempt "$command" --help
done

echo "Exercising configuration and diagnostics..."
capture config-model config set-model mock-model
capture config-extractor config set-extractor-model mock-extractor
capture config-extractor-clear config clear-extractor-model
capture config-base-url config set-base-url http://localhost:11434/v1
capture config-key config set-key e2e-secret
capture config-show config show
assert_output config-show "Model: mock-model"
assert_output config-show "Extractor model: mock-model (tutor fallback)"
assert_output config-show "Base URL: http://localhost:11434/v1"
assert_output config-show "API key: saved locally ("
if grep -Fq "e2e-secret" "$ARTIFACTS/config-show.out"; then
  echo "FAIL: config show exposed the saved API key" >&2
  exit 1
fi
OPENLEARN_MODEL=env-model \
  OPENLEARN_EXTRACTOR_MODEL=env-extractor \
  OPENLEARN_BASE_URL=http://localhost:22434/v1 \
  OPENAI_API_KEY=env-secret \
  run_openlearn config show >"$ARTIFACTS/config-env.out" 2>&1
assert_output config-env "Model: env-model"
assert_output config-env "Extractor model: env-extractor (environment override)"
assert_output config-env "Base URL: http://localhost:22434/v1"
assert_output config-env "API key: set by OPENAI_API_KEY ("
if grep -Fq "env-secret" "$ARTIFACTS/config-env.out"; then
  echo "FAIL: config show exposed the environment API key" >&2
  exit 1
fi
capture config-key-clear config clear-key
capture config-show-cleared config show
assert_output config-show-cleared "API key: not set"
capture init-configured init
assert_output init-configured "Already configured."
# A missing OCI runtime is a valid diagnostic result on development machines.
if capture doctor doctor; then
  doctor_status=0
else
  doctor_status=$?
fi
if [ "$doctor_status" != "0" ] && [ "$doctor_status" != "1" ]; then
  echo "FAIL: doctor returned unexpected status $doctor_status" >&2
  exit 1
fi
assert_output doctor "Code runner:"
capture templates templates
assert_output templates "Available course templates:"

echo "Creating topics and exercising local topic commands..."
capture seed test --resume --with-lock --home "$WORK_HOME" --no-menu
assert_output seed "Seeded openLearn manual test course"
capture new new "Audit Course" --goal "Audit the public CLI" --template vim --mastery-profile efficient
assert_output new "Template 'Vim' loaded"
capture list list
assert_output list "audit-course"
capture recent recent
assert_output recent "audit-course"
capture active active audit-course
assert_output active "Active topic: audit-course"
capture status status audit-course
assert_output status "Topic: Audit Course"
capture summary summary audit-course
assert_output summary "Course: Audit Course"
capture stats stats audit-course --text
assert_output stats "openlearn progress - Audit Course"
capture repair repair audit-course
assert_output repair "Metadata repaired: audit-course"

echo "Exercising file, folder, Quick Learn, paste, and edit interfaces..."
capture import-file import audit-course "$FIXTURES/vim.txt"
assert_output import-file "Saved source:"
capture import-scan import audit-course --scan "$FIXTURES/scan"
assert_output import-scan "2 imported, 0 skipped (already imported), 0 failed"
printf '%s\n' \
  '#!/bin/sh' \
  "printf '%s\n' \"\$1\" >'$FIXTURES/paste-editor-arg'" \
  'printf "%s\n" "Pasted through the configured editor." >"$1"' \
  >"$FIXTURES/editor"
chmod +x "$FIXTURES/editor"
capture config-paste-editor config set-editor "$FIXTURES/editor"
capture config-paste-editor-show config show
assert_output config-paste-editor-show "Editor: $FIXTURES/editor"
capture paste paste audit-course --name pasted-notes.txt
assert_output paste "Saved source: pasted-notes.txt"
grep -Fq "Pasted through the configured editor." \
  "$WORK_HOME/learning-topics/audit-course/context/pasted-notes.txt"
test -s "$FIXTURES/paste-editor-arg"
printf '%s\n' \
  '#!/bin/sh' \
  "printf '%s\n' \"\$1\" >'$FIXTURES/edit-editor-arg'" \
  >"$FIXTURES/edit-editor"
chmod +x "$FIXTURES/edit-editor"
capture config-edit-editor config set-editor "$FIXTURES/edit-editor"
capture edit edit audit-course
grep -Fq "$WORK_HOME/learning-topics/audit-course.md" "$FIXTURES/edit-editor-arg"
printf '/q\n' | run_openlearn quick "$FIXTURES/vim.txt" --name "Quick Audit" >"$ARTIFACTS/quick.out" 2>&1
assert_output quick "Quick Learn plan"
capture chapter chapter 1 quick-audit
assert_output chapter "Unit 1/"

echo "Exercising model-backed commands in isolated mode..."
capture chat chat quick-audit "Explain Normal mode"
assert_output chat "Mock reply"
capture next next quick-audit
assert_output next "Mock reply"
capture review review quick-audit
assert_output review "Mock reply"
capture videos videos quick-audit --query registers --n 2
assert_output videos "mock-openlearn"
capture due due
assert_output due "No review concepts due today."
for command in chat resume next review
do
  case "$command" in
    chat)
      capture "dry-$command" "$command" quick-audit "Preview this request" --dry-run
      ;;
    *)
      capture "dry-$command" "$command" quick-audit --dry-run
      ;;
  esac
  assert_output "dry-$command" "dry run: request not sent"
done
assert_output dry-chat "Preview this request"
assert_output dry-resume "Pick up naturally where this learner left off."
assert_output dry-next "Continue the current slide"
assert_output dry-review "Create a short active-recall review session"

echo "Exercising the REPL and its public command router..."
printf '%s\n' \
  '/help --all' \
  '/status' \
  '/summary' \
  '/plan' \
  '/chapter 1' \
  '/videos --n 1 registers' \
  '/ask What is Normal mode?' \
  '/review' \
  '/attempt list' \
  '/recent' \
  '/repair' \
  '/q' \
  | run_openlearn repl quick-audit >"$ARTIFACTS/repl.out" 2>&1
assert_output repl "Commands: /resume"
assert_output repl "Course plan"
assert_output repl "Mock reply"
assert_output repl "No coding attempts for quick-audit."
printf '/q\n' | run_openlearn shell quick-audit >"$ARTIFACTS/shell.out" 2>&1
assert_output shell "Learning session"
if printf '/q\n' | run_openlearn tui quick-audit >"$ARTIFACTS/tui.out" 2>&1; then
  tui_status=0
else
  tui_status=$?
fi
if [ "$tui_status" = "0" ]; then
  assert_output tui "openLearn TUI"
elif [ "$tui_status" = "2" ]; then
  assert_output tui "prompt-toolkit is required for the TUI"
else
  echo "FAIL: tui returned unexpected status $tui_status" >&2
  exit 1
fi

echo "Exercising interview-prep and attempt-management interfaces..."
capture interview-setup interview setup audit-course --role-family backend --target-level mid --weekly-minutes 180
assert_output interview-setup "Created local interview-prep profile"
capture interview-profile interview profile audit-course
assert_output interview-profile "role family: backend"
capture interview-edit interview edit audit-course weekly_minutes 240
assert_output interview-edit "Updated weekly_minutes"
capture interview-defer interview placement audit-course defer
assert_output interview-defer "Placement: deferred"
capture interview-status interview placement audit-course status
assert_output interview-status "Placement: deferred"
capture attempts attempt list audit-course
assert_output attempts "No coding attempts for audit-course."
capture interview-clear interview clear audit-course --yes
assert_output interview-clear "Interview-prep profile cleared"

echo "Exercising menu and deletion interfaces..."
printf 'q\n' | run_openlearn >"$ARTIFACTS/bare-menu.out" 2>&1
assert_output bare-menu "Local-first AI tutoring"
printf 'q\n' | run_openlearn menu >"$ARTIFACTS/menu.out" 2>&1
assert_output menu "Local-first AI tutoring"
capture delete-quick delete quick-audit --yes
assert_output delete-quick "Deleted topic: quick-audit"
capture delete-audit delete audit-course --yes
assert_output delete-audit "Deleted topic: audit-course"
capture delete-manual delete practical-vim-foundations --yes
assert_output delete-manual "Deleted topic: practical-vim-foundations"
if [ ! -e "$WORK_HOME/learning-topics/.practical-vim-foundations.md.lock" ] || \
  [ -d "$WORK_HOME/learning-topics/practical-vim-foundations" ]; then
  echo "FAIL: deletion did not preserve the lock identity and remove topic data" >&2
  exit 1
fi
capture final-list list
assert_output final-list "No topics yet."

echo "OK: public application interface journey passed"
if [ "$CREATED_WORK_HOME" = "1" ] && [ "${OPENLEARN_E2E_KEEP:-0}" != "1" ]; then
  echo "Evidence verified in $ARTIFACTS (removed after exit)"
  echo "Set OPENLEARN_E2E_KEEP=1 to preserve it."
else
  echo "Evidence: $ARTIFACTS"
fi
