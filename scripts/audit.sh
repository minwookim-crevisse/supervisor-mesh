#!/usr/bin/env bash
# Repo audit gate (release blocker). Exit 0 = every gate holds; anything else blocks ship.
# why: completion is stamped by this script, not self-declared.
set -euo pipefail
cd "$(dirname "$0")/.."
step() { printf '\n== %s\n' "$*"; }

if command -v claude >/dev/null 2>&1; then
  step "plugin validate --strict"
  claude plugin validate . --strict
  claude plugin validate .claude-plugin/plugin.json --strict
  claude plugin validate skills --strict
  claude plugin validate agents --strict
else
  echo "audit: claude CLI not found — validate skipped (CI runs it where available)" >&2
fi

step "unit tests"
python3 -m unittest discover -s tests

step "GC: doc drift"
python3 scripts/gc/check_docs_drift.py --exclude templates --exclude docs/core

step "GC: structure drift"
python3 scripts/gc/check_structure.py --exclude templates --exclude docs/core

if command -v ruff >/dev/null 2>&1; then
  step "lint (ruff)"
  ruff check .
fi

step "scrub"
scripts/scrub.sh

printf '\naudit: PASS\n'
