---
name: init
description: Scaffold the primed 4-pillar harness (instruction / constraint / feedback-loop / knowledge) into the current project. Idempotent and non-destructive.
disable-model-invocation: true
argument-hint: "[ts|python|none]"
allowed-tools: Read Edit Bash(python3:*) Bash(ls:*) Bash(test:*) Bash(cat:*)
---

# supervisor-mesh: init

Scaffold the harness 4 pillars into the CURRENT project (`${CLAUDE_PROJECT_DIR}`). IDEMPOTENT: an existing file is never overwritten — known files are merged additively, everything else is skipped and reported.

Templates are **primed**: transferable discipline is pre-filled as opinionated defaults with `why:` comments; only project facts are left as `<...>` blanks (Part B).

## Steps
1. **Detect stack.** `package.json` → TypeScript (eslint + dependency-cruiser); `pyproject.toml` → Python (ruff + import-linter + mypy). If `$ARGUMENTS` names a stack, use it. If neither or both exist, ask the operator ONE question (ts / python / none) — do not guess.
2. **Preview** (no writes):
   `python3 "${CLAUDE_PLUGIN_ROOT}/skills/init/scaffold.py" --target "${CLAUDE_PROJECT_DIR}" --stack <stack> --dry-run`
3. **Apply** the same command without `--dry-run`. It lays down:
   - ① **Instruction** — `AGENTS.md` (entry pointer, restore order, role model, dispatch rules, single-owner coordination, cleanup rules). Existing `AGENTS.md` → harness block appended once (marker-guarded).
   - ② **Constraint** — TS: `.dependency-cruiser.cjs` + `.eslintrc.cjs` + package.json scripts (typecheck / lint / lint:boundary / check:unused, missing keys only). Python: `[tool.ruff]` + `[tool.mypy]` appended to pyproject.toml when absent + `.importlinter`. Rules: absolute imports only · read/write layer boundary · type-check required · no unused/orphans · no arbitrary shared-pin bumps · secrets server-only. Rationale table: `docs/conventions/constraints.md`.
   - ③ **Feedback loop** — `tests/README.md` (regression naming `*.regression-NN`, coverage goal, test-alongside-function, evidence-first, FAIL triage) + `scripts/audit.sh` (release-blocking audit gate; exits 2 until configured).
   - ④ **Knowledge** — `docs/decisions/0001-decision-log.md` (single-owner, outranks other docs) + `docs/conventions/glossary.md` (terms skeleton + money/number convention example).
   - **GC** — `scripts/gc/check_docs_drift.py`, `scripts/gc/check_structure.py`, `.github/workflows/housekeeping.yml` (weekly + manual).
   - `.gitignore` — secret/junk lines (.env, .auth/, *.sqlite …) appended if missing.
4. **Show the captured script output** (CREATE / MERGE / SKIP per file + summary line) to the operator verbatim. That output is the evidence; do not paraphrase it into "done".
5. **Part B checklist.** Point the operator at `docs/harness/part-b-checklist.md`: project name, role list, ship role, production data anchor, data boundary globs, domain terms, test runner, audit invariants. NEVER invent these values — ask, or leave the `<...>` blank.
6. **Companions (detect, never install).** Read `~/.claude/plugins/installed_plugins.json` if present and report which recommended companions are installed: a spec/brainstorm tool (e.g. `ready`, `office-hours`), plan reviews (e.g. `plan-eng-review`), `karpathy-guidelines`. For missing ones, say where they come from; do not copy or install them.
7. **Next step.** Shape the spec with the companion spec tool (if any) → fill Part B → `/supervisor-mesh:spawn-roles` → `/supervisor-mesh:dispatch`.

## Guardrails
- Never overwrite; never delete. Secrets are never written anywhere.
- Do not hand-write the pillar files when the script can — the script is the idempotency guarantee. Use Edit only to fill Part B values the operator gives you.
- Respond in the user's language.
