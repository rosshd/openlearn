PYTHON ?= .venv/bin/python
OPENLEARN ?= .venv/bin/openlearn
REVIEW_DIR ?= .artifacts/review
TYPE ?= feat

.PHONY: test unit pytest lint typecheck smoke e2e diff validate check review repo-status worktree finish

# --- Individual lanes ---------------------------------------------------------

test: unit

unit:
	$(PYTHON) -m unittest

pytest:
	$(PYTHON) -m pytest -q

lint:
	ruff check src tests

# Non-blocking: surfaces type issues in the dynamic core; not part of `check` yet.
typecheck:
	pyright src

smoke:
	@home=$$(mktemp -d); \
	OPENLEARN_MOCK=1 OPENLEARN_HOME="$$home" $(OPENLEARN) test --reset --no-menu >/dev/null && \
	OPENLEARN_MOCK=1 OPENLEARN_HOME="$$home" $(OPENLEARN) chat practical-vim-foundations "explain normal mode" >/dev/null && \
	echo "smoke: seed + mock chat ok"

e2e:
	OPENLEARN_MOCK=1 OPENLEARN_HOME="$$(mktemp -d)" ./manual-tests/smoke-full.sh --mock

diff:
	git diff --stat
	git diff

# --- Repository workflow ------------------------------------------------------

repo-status:
	@./scripts/repo-workflow status

worktree:
	@test -n "$(NAME)" || { echo "usage: make worktree NAME=<task> [TYPE=feat]" >&2; exit 2; }
	@./scripts/repo-workflow start "$(TYPE)" "$(NAME)"

finish:
	@test -n "$(NAME)" || { echo "usage: make finish NAME=<task>" >&2; exit 2; }
	@./scripts/repo-workflow finish "$(NAME)"

# Back-compat alias for the old umbrella target.
validate: check

# --- The one obvious command --------------------------------------------------
# Fast, fully green gate: lint + tests + mock smoke. Run this before pushing.
check: lint unit pytest smoke
	@echo "check: all green"

# --- Review-before-PR: run the gate and collect evidence ----------------------
# Writes logs + diff to $(REVIEW_DIR)/<timestamp>/ for the agent (or you) to
# summarize risk against. Fails loudly if the gate is red.
review:
	@stamp=$$(date +%Y%m%d-%H%M%S); out="$(REVIEW_DIR)/$$stamp"; mkdir -p "$$out"; \
	echo "Evidence: $$out"; \
	git diff --stat | tee "$$out/diff.stat"; \
	git diff > "$$out/diff.patch"; \
	if $(MAKE) check > "$$out/check.log" 2>&1; then \
		echo "GATE: PASS (see $$out/check.log)"; \
	else \
		echo "GATE: FAIL — tail of $$out/check.log:"; tail -20 "$$out/check.log"; \
		exit 1; \
	fi
