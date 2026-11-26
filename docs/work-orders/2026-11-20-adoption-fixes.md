# Work order — adoption fixes from the first real project (0.3.0 batch 1)

> Paths: `<repo>` = this repo. Absolute paths travel only in dispatch messages (DEC-0008).
> Common header (operating model, discipline, constraints, report format): same as `docs/work-orders/2026-10-21-verification-skills.md`, EXCEPT write access as stated.
> Operating model: lead + in-process subagent worker + lead review (DEC-0016). No worker pane.
> Source: `docs/reports/2026-11-20-adoption-feedback.md` (A1-A4, A6, A7). Decisions D131-D134 are in `docs/designs/mcp-server-spec.md`.

## WO-0042 — init seeds a verify entrypoint, `work_new` checks the type, spawn-roles packs windows (role: backend) · depends: none
**Read set:** `docs/reports/2026-11-20-adoption-feedback.md`; `docs/designs/mcp-server-spec.md` rows D131-D134; `mesh/init.py`, `mesh/defaults.py`, `mesh/model.py` (`Config`, `DEFAULT_TYPES`, `verify_hash`), `mesh/state.py` (the `unknown type` guard), `mesh/tools.py` (`work_new`, the tool schemas), `skills/init/SKILL.md`, `skills/spawn-roles/SKILL.md`, `agents/role-lead-ship.md`, `templates/`, `tests/`.
**Write access:** `mesh/`, `skills/init/`, `skills/spawn-roles/`, `templates/`, `agents/role-lead-ship.md` (one line for D134), `tests/`, your report `docs/reports/WO-0042-backend.md`. NOT `docs/decisions/`, `docs/tracker.md`, `docs/work-orders/`, `docs/designs/`, `README.md`.
**Must:**
- D131 (A1, A2): `mesh_init` (and the scaffold) creates `scripts/verify.sh` from a new template (never overwrites an existing one) and a config whose `[verify]` has exactly one command, `bash scripts/verify.sh`. The template runs, in order and failing on the first error: `python3 scripts/gc/check_docs_drift.py`, `python3 scripts/gc/check_structure.py`; then, only when the manifest exists, the stack checks (`package.json` → `npm ci` and the `typecheck` / `lint` / `test` scripts that exist; `pyproject.toml` → `ruff check .` and `pytest -q` when installed). The config comment says why: the verify hash is pinned at start, so change checks in the script, not the config. An existing `.mesh/config.toml` is never rewritten.
- D132 (A3): `work_new` refuses an unknown `type` with the same `contract_incomplete` code and a message listing the allowed types (`Config.types`); the MCP schema of `work_new` lists them as an enum. A custom type in `[types]` is accepted.
- D133 (A4): `skills/spawn-roles/SKILL.md`: at most 4 panes per window; more roles go into `mesh`, `mesh-2`, … created with projmux (never raw `tmux break-pane/join-pane`); the default shell pane a new window starts with is closed through projmux; tell the operator each window's name.
- D134 (A6, A7): `.mesh/state.json` and `.mesh/events.jsonl` stay tracked; `agents/role-lead-ship.md` gets one line: include them in each batch commit as they are at commit time. init prints a hint line when `git config user.name` is empty in the target (no write).
**DoD (captured):** new and changed tests with their names and a red-before/green-after run for D131 (fresh tree → config has one command, script present and executable, script exit 0 on a docs-only tree, a seeded drift fails it) and D132 (unknown type refused at `work_new`, custom type accepted); `make check` exit 0 with the tail; `scripts/scrub.sh` 0 hits; the diff stat. Your lead review of every file before reporting.

## WO-0043 — live check of WO-0042 on a throwaway project (role: qa) · depends: WO-0042 (lead PASS, uncommitted in the working tree)
**Read set:** this file (WO-0042 section), `docs/reports/WO-0042-backend.md`, `docs/designs/mcp-server-spec.md` rows D131-D134, `.claude/skills/verify-supervisor-mesh/SKILL.md`.
**Write access:** your report `docs/reports/WO-0043-qa.md`; evidence under `~/.local/state/supervisor-mesh-qa/WO-0043/`; a throwaway projmux project (scratch name) that you delete at the end. Nothing else in this repo.
**Must:** use the `verify-supervisor-mesh` skill. The working tree is uncommitted, so export the tree as it is now (a scratch commit on a temporary ref or a copy into the scratch marketplace; never commit to `main`, never reset or clean the shared tree). In the throwaway project, at PROJECT scope:
- D131: `mesh_init` preview + apply → `.mesh/config.toml` has exactly one verify command `bash scripts/verify.sh`; `scripts/verify.sh` exists and is executable. Create one docs-type item, take it through ready → start → submit with a commit → verify → close, and capture that `verify` ran the script and passed (not `not_run`).
- D132: `work_new` with type `chore` is refused at creation with the allowed-type list; `tools/list` shows the enum.
- D133: `/supervisor-mesh:spawn-roles` with 5 roles → windows of at most 4 panes, no stray shell pane, `projmux reconcile resources --dry-run` shows no out-of-window entries. Trust prompts: ask the operator through the PM; never press them yourself.
- D134: init prints the author hint when the throwaway repo has no `user.name`; `.mesh/.gitignore` still ignores `events.jsonl`.
**DoD (captured):** pane captures and tool results per check, exit codes, the throwaway project deleted (`projmux get projects` output), scrub 0 on your report. Verdict PASS / FAIL per D-number. Self-report is not evidence.

## Then
- Later batches: WO-0041 (D130 guided onboarding), session continuity D127-D129.

## Constraints
Read-only on `docs/decisions/`, `docs/tracker.md`, `docs/work-orders/`, `AGENTS.md`, `README.md`, design docs. No commits, no push (Ship only, after operator OK). No worktree-destroying git commands. No secrets or absolute home paths in repo files (scrub). Self-report is not evidence: every DoD item is captured output.
