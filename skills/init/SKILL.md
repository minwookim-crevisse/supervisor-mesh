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

## Preferred: the supervisor-mesh MCP server
When the `supervisor-mesh` MCP server is connected (`/mcp`), run init through `mesh_init` (`mcp__plugin_supervisor-mesh_supervisor-mesh__mesh_init`). It runs the same scaffold as the steps below and also bootstraps `.mesh/` (state, config, the `.mesh/bin/mesh` launcher, the PM-pane `UserPromptSubmit` operator-event hook and the `permissions.deny` rules for the hook command and for `Write`/`Edit` on `.mesh/state.json`, `.mesh/events.jsonl` and `.mesh/refusals.jsonl` (`.mesh/config.toml` stays editable; an older `.mesh/**` pair written by mesh is replaced) merged into `.claude/settings.json`; for stack `ts` the config carries the `fail_if_output` defaults) and imports an existing `docs/tracker.md` once as legacy rows (raw text kept, never verified, the file untouched).
- **Preview and apply are separate calls.** First call `mesh_init` with `stack` and no `apply`: it writes nothing and returns the rows (CREATE / MERGE / SKIP), the legacy import mapping, the companion presence and install plan, and a `plan_id`. Show that to the operator verbatim. Only then call `mesh_init` again with `apply: true` and that `plan_id`; a project that changed in between is refused (`plan_changed`): preview again.
- **The PM pane must be labelled.** Apply (and every PM tool) is refused unless this pane is the projmux agent labelled `role=pm`. If the result says so, tell the operator to start the PM pane with `projmux create claude --project <name> --label role=pm`, and stop. Apply pins this pane's agent as the project's PM in `.mesh/state.json` (D109): from then on only that agent acts as the PM, and only while it is the one live `role=pm` agent. A `REFUSE` row (a path that resolves outside the project through a symlink, D111) makes apply refuse: tell the operator to replace the symlink, then preview again. To move the pin to a new PM pane: the old PM is stopped, the operator types `re-pin <new agent uid>` in the new PM pane, then apply runs there and the operator types the one-time code shown in the PM pane's tmux popup (D113, D114).
- **Companions.** The result shows presence only (booleans). For missing pstack / mattpocock-skills it shows the D88 install plan (project scope only); installing needs the operator to name `companions_install` (or one plugin name) in a prompt, then a `companions_install` call (`mcp__plugin_supervisor-mesh_supervisor-mesh__companions_install`) with that prompt's event id. gstack is printed as a command for the operator to run.
- Show the captured tool result, not a paraphrase. Then continue with step 5 (Part B) and step 7.
CLI fallback with the same behaviour: `.mesh/bin/mesh mesh_init --stack <stack>` once `.mesh/` exists; before that, the scaffold steps below.

## Steps (fallback: no MCP server)
1. **Detect stack.** `package.json` → TypeScript (eslint + dependency-cruiser); `pyproject.toml` → Python (ruff + import-linter + mypy). If `$ARGUMENTS` names a stack, use it. If neither or both exist, ask the operator ONE question (ts / python / none) — do not guess.
2. **Preview** (no writes):
   `python3 "${CLAUDE_PLUGIN_ROOT}/skills/init/scaffold.py" --target "${CLAUDE_PROJECT_DIR}" --stack <stack> --dry-run`
   Preview and apply are separate steps: run them as two separate Bash calls and never chain them in one command (no `--dry-run && ...`). Show the preview output to the operator before running apply.
3. **Apply** the same command without `--dry-run`, as its own Bash call after the preview output has been shown. It lays down:
   - ① **Instruction** — `AGENTS.md` (entry pointer, restore order, role model, dispatch rules, single-owner coordination, cleanup rules). Existing `AGENTS.md` → harness block appended once (marker-guarded).
   - ② **Constraint** — TS: `.dependency-cruiser.cjs` + `.eslintrc.cjs` + package.json scripts (typecheck / lint / lint:boundary / check:unused, missing keys only). Python: `[tool.ruff]` + `[tool.mypy]` appended to pyproject.toml when absent + `.importlinter`. Rules: absolute imports only · read/write layer boundary · type-check required · no unused/orphans · no arbitrary shared-pin bumps · secrets server-only. Rationale table: `docs/conventions/constraints.md`.
   - ③ **Feedback loop** — `tests/README.md` (regression naming `*.regression-NN`, coverage goal, test-alongside-function, evidence-first, FAIL triage) + `scripts/audit.sh` (release-blocking audit gate; exits 2 until configured).
   - ④ **Knowledge** — `docs/decisions/0001-decision-log.md` (single-owner, outranks other docs) + `docs/conventions/glossary.md` (terms skeleton + money/number convention example).
   - **GC** — `scripts/gc/check_docs_drift.py`, `scripts/gc/check_structure.py`, `.github/workflows/housekeeping.yml` (weekly + manual).
   - `.gitignore` — secret/junk lines (.env, .auth/, *.sqlite …) appended if missing.
4. **Show the captured script output** (CREATE / MERGE / SKIP per file + summary line) to the operator verbatim. That output is the evidence; do not paraphrase it into "done".
5. **Part B checklist.** Point the operator at `docs/harness/part-b-checklist.md`: project name, role list, ship role, production data anchor, data boundary globs, domain terms, test runner, audit invariants. NEVER invent these values — ask, or leave the `<...>` blank.
6. **Companions (detect, never install).** Run `python3 "${CLAUDE_PLUGIN_ROOT}/skills/init/companions.py"` and report its lines (`companion <name>: installed|missing`) for the recommended companions: a spec/brainstorm tool (`ready`, `office-hours`), plan reviews (`plan-eng-review`), `karpathy-guidelines`. Do not read, cat or print the plugin registry or any other user config yourself — the helper checks presence by name only and prints nothing else. For missing ones, say where they come from; do not copy or install them. This helper and these steps never install anything; installing the D88 companions is only the separate, operator-approved `companions_install` call above.
7. **Next step.** Shape the spec with the companion spec tool (if any) → fill Part B → `/supervisor-mesh:spawn-roles` → `/supervisor-mesh:dispatch`.

## Guardrails
- Never overwrite; never delete. Secrets are never written anywhere.
- Do not hand-write the pillar files when the script can — the script is the idempotency guarantee. Use Edit only to fill Part B values the operator gives you.
- Respond in the user's language.
