---
name: gc
description: Garbage-collect harness drift — detect dead code, doc drift (AGENTS.md vs tree, deprecated wordings) and structure drift (temp/backup/junk, undisposed one-shot scripts), then guide cleanup. Read-only detection; edits only on confirmation.
disable-model-invocation: true
argument-hint: "[code|doc|structure]"
allowed-tools: Read Grep Edit Bash(python3:*) Bash(vulture:*) Bash(npx:*) Bash(ls:*) Bash(git status:*) Bash(git ls-files:*)
---

# supervisor-mesh: gc

Run the three drift checks (methodology §14) on THIS project and report. `$ARGUMENTS` limits to one drift type. Detection is read-only; propose fixes and apply only what the operator confirms.

Scripts: the project copies laid by `/supervisor-mesh:init` (`scripts/gc/`). If absent, use `${CLAUDE_PLUGIN_ROOT}/templates/gc/` with `--root "${CLAUDE_PROJECT_DIR}"`.

## Steps
1. **CODE drift** — the stack's dead-code detector: `vulture <SRC> --min-confidence 80` (Python) or `npx --yes ts-prune -p tsconfig.json` / `npx --yes knip` (TS). If the tool is missing, say so and how to add it; do not install silently.
2. **DOC drift** — `python3 scripts/gc/check_docs_drift.py` (every backtick path in AGENTS.md exists; glossary-deprecated wordings absent from source).
3. **STRUCTURE drift** — `python3 scripts/gc/check_structure.py` (temp/backup names, env junk such as *.sqlite and *:Zone.Identifier, one-shot scripts). Also list untracked files from `git status --porcelain` that look like scratch output.
   Pass `--exclude <dir>` for directories that hold intentional fixtures or example patterns.
4. **Report** — one list per drift type, each item with the captured tool line and a suggested action. 🔴 Never auto-delete: dead code and "unused" can be false positives (reflection, dynamic import, public API, plugin entry points). Confirm per item or per group before editing. Deleting files the operator confirmed is fine; commit/push is Ship only.
5. If `.github/workflows/housekeeping.yml` is absent, offer `/supervisor-mesh:init` (it lays the weekly + manual workflow).

Respond in the user's language. Idempotent · read-only by default.
