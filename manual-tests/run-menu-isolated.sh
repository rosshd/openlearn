#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export OPENLEARN_HOME="${OPENLEARN_HOME:-/tmp/openlearn-manual-vim}"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

echo "openLearn manual menu"
echo "OPENLEARN_HOME=$OPENLEARN_HOME"
echo "Fixture context: $ROOT_DIR/manual-tests/context/practical-vim-syllabus.txt"
echo ""

python -m openlearn menu
