PYTHON ?= .venv/bin/python
OPENLEARN ?= .venv/bin/openlearn
REVIEW_DIR ?= .artifacts/review
RELEASE_CANDIDATE ?= .artifacts/release-candidate
TYPE ?= feat

.PHONY: test unit pytest lint typecheck smoke e2e oci-live codex-dogfood tutor-behavior-eval outcome-eval package-assets release-build release-verify release-smoke-wheel release-smoke-sdist diff validate check review repo-status worktree finish

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

package-assets:
	OPENLEARN_PACKAGE_SMOKE=1 $(PYTHON) -m unittest tests.test_package_assets -v

# Build wheel and sdist once into an immutable candidate directory, inspect
# their contents, and record hashes beside (not inside) dist/.
release-build:
	$(PYTHON) scripts/release_artifacts.py build \
		--repository . \
		--candidate "$(RELEASE_CANDIDATE)" \
		--expected-version "$$($(PYTHON) -c 'import openlearn; print(openlearn.__version__)')"

release-verify:
	$(PYTHON) scripts/release_artifacts.py verify \
		--candidate "$(RELEASE_CANDIDATE)" \
		--expected-version "$$($(PYTHON) -c 'import openlearn; print(openlearn.__version__)')" \
		$(if $(RELEASE_TAG),--tag "$(RELEASE_TAG)",)

release-smoke-wheel: release-verify
	$(PYTHON) scripts/release_artifacts.py smoke \
		--candidate "$(RELEASE_CANDIDATE)" \
		--kind wheel \
		--expected-version "$$($(PYTHON) -c 'import openlearn; print(openlearn.__version__)')"

release-smoke-sdist: release-verify
	$(PYTHON) scripts/release_artifacts.py smoke \
		--candidate "$(RELEASE_CANDIDATE)" \
		--kind sdist \
		--expected-version "$$($(PYTHON) -c 'import openlearn; print(openlearn.__version__)')"

e2e:
	OPENLEARN="$(OPENLEARN)" ./manual-tests/smoke-full.sh --mock

oci-live:
	OPENLEARN_RUN_OCI_TESTS=1 PYTHONPATH=src $(PYTHON) -m unittest tests.test_code_runner_live -v

# Opt-in and live. This target is deliberately absent from `check`.
codex-dogfood:
	@test -n "$(RUN_ROOT)" || { echo "usage: make codex-dogfood RUN_ROOT=<new-private-path>" >&2; exit 2; }
	PYTHON=$(PYTHON) ./scripts/run-codex-dogfood "$(RUN_ROOT)" --openlearn "$(OPENLEARN)"

# Opt-in, live, and intentionally absent from `check`.
tutor-behavior-eval:
	@test -n "$(RUN_ROOT)" || { echo "usage: make tutor-behavior-eval RUN_ROOT=<new-private-path> JUDGE_MODEL=<model-distinct-from-tutor> [SUITE=multi-turn] [SCENARIO=<name>]" >&2; exit 2; }
	PYTHON=$(PYTHON) ./scripts/run-tutor-behavior-eval "$(RUN_ROOT)" $(if $(JUDGE_MODEL),--judge-model "$(JUDGE_MODEL)",) $(if $(SUITE),--suite "$(SUITE)",) $(if $(SCENARIO),--scenario "$(SCENARIO)",)

# Opt-in, live, diagnostic until calibrated, and intentionally absent from `check`.
outcome-eval:
	@test -n "$(RUN_ROOT)" || { echo "usage: make outcome-eval RUN_ROOT=<new-private-path> JUDGE_MODEL=<model-distinct-from-tutor> [SCENARIO=<name>]" >&2; exit 2; }
	PYTHON=$(PYTHON) ./scripts/run-outcome-eval "$(RUN_ROOT)" $(if $(JUDGE_MODEL),--judge-model "$(JUDGE_MODEL)",) $(if $(SCENARIO),--scenario "$(SCENARIO)",)

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
# Fully green gate: lint + tests + focused and interface-wide mock smoke.
check: lint unit pytest smoke e2e
	@echo "check: all green"

# --- Optional evidence collection --------------------------------------------
# Reruns the gate and writes logs + diff to $(REVIEW_DIR)/<timestamp>/.
# This does not replace an independent review.
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
