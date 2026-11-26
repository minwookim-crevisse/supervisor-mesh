#!/usr/bin/env bash
# supervisor-mesh verify entrypoint (seeded by init; yours to edit).
# .mesh/config.toml runs this ONE script as its only verify command. The verify hash is pinned when an item starts,
# so a change to the commands in the config invalidates every ready or running item; add and change checks HERE.
# Runs from the project root, in order, and stops at the first failing check.
set -euo pipefail
case $0 in */*) cd "${0%/*}/.." ;; *) cd .. ;; esac  # the project root (this file lives in scripts/)

step() { echo "== $*"; }

step "docs drift"
python3 scripts/gc/check_docs_drift.py
step "structure"
python3 scripts/gc/check_structure.py

if [ -f package.json ]; then
  step "npm ci"
  npm ci
  # the npm scripts package.json defines, one per line (python3 is already required above; no jq)
  scripts=$(python3 -c 'import json; print("\n".join(json.load(open("package.json")).get("scripts", {})))')
  for name in typecheck lint test; do
    if [[ $'\n'"$scripts"$'\n' == *$'\n'"$name"$'\n'* ]]; then
      step "npm run $name"
      npm run "$name"
    fi
  done
fi

if [ -f pyproject.toml ]; then
  if command -v ruff >/dev/null 2>&1; then
    step "ruff"
    ruff check .
  else
    echo "-- ruff not installed: skipped"
  fi
  if command -v pytest >/dev/null 2>&1; then
    step "pytest"
    pytest -q
  else
    echo "-- pytest not installed: skipped"
  fi
fi

echo "verify: all checks passed"
