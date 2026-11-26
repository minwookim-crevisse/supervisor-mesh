# WO-0042: adoption fixes D131-D134 (backend)

Verdict: **PASS, commit-ready, with one PM decision open (D134 `events.jsonl`, below)** (lead-verified 2026-11-20, base 42779ac). Worker: one in-process subagent (DEC-0016). Nothing committed; commit and push are Ship's.

## Change
- **D131, verify entrypoint.**
  - New `templates/verify/verify.sh` (0755) is copied to `scripts/verify.sh` by the scaffold and `mesh_init`, and is never overwritten. It runs with `set -euo pipefail` and stops at the first failure:
    1. `check_docs_drift.py`;
    2. `check_structure.py`;
    3. if `package.json` exists: `npm ci`, then whichever of the npm scripts `typecheck`, `lint` and `test` exist (read with `python3 -c json`, no jq);
    4. if `pyproject.toml` exists: `ruff check .` and `pytest -q`, each only when installed.
  - `mesh/defaults.py`: `[verify]` has exactly one `[[verify.commands]]`, argv `["bash", "scripts/verify.sh"]`, kind `check`, timeout 1800 s. The ts `fail_if_output` patterns stay. The comment says the verify hash is pinned at start, so change checks in the script. An existing `.mesh/config.toml` is never rewritten.
- **D132, `work_new` type.**
  - `mesh/state.py`: the `new` row gets the guard `type_known`, which refuses with `contract_incomplete` and the message `unknown type 'X'; allowed: <sorted Config.types>`. The ready-time guard uses the same message.
  - `mesh/tools.py` and `mesh/mcp_server.py`: the `work_new` schema's `type` is an enum built from the project config when the tools are listed (defaults when there is no config or it does not parse). The call-time guard is what enforces it; a type added mid-session reaches the enum after a server restart.
  - `_config` was renamed `config_or_defaults`, with all call sites updated.
- **D133, spawn-roles layout** (`skills/spawn-roles/SKILL.md` step 4):
  - at most 4 role panes per window, in windows `mesh`, `mesh-2`, …, each made with `projmux create window`;
  - the default shell pane is closed with `projmux delete pane uid:<pane> --dry-run`, then the same command with `--yes`;
  - raw `tmux break-pane`, `join-pane`, `move-pane` and `kill-pane` are forbidden; the one raw tmux call left is `select-layout tiled` (layout only);
  - the operator is told each window's name and its roles.
  - The lead checked the flags with read-only `--help`: `create window --name`, `delete pane --dry-run/--yes`, `get panes -w`.
- **D134, state files and author.**
  - `agents/role-lead-ship.md`: one line added after the PM's uncommitted DEC-0017 edit, which is untouched (lead diff of the snapshot taken before dispatch: only `+1` line differs).
  - `mesh/git.py` `user_name`: a read-only `git config --get user.name` through runner.
  - The scaffold and `mesh_init` print or return `# hint: git user.name is not set ...` when it is empty. The hint stays outside `plan_id`.
- Docs: `skills/init/SKILL.md` gains the verify entrypoint sentence and the hint line.
- `git diff --stat -- mesh skills templates agents/role-lead-ship.md tests`: 11 files, +123 / −43 (the ship-md count includes the PM's own lines). New files: `templates/verify/verify.sh` (44 lines) and `tests/test_mesh_adoption_wo0042.py` (280 lines).

## Tests (in `tests/test_mesh_adoption_wo0042.py` unless noted)
- **D131:**
  - `D131ConfigTests.test_a_fresh_config_has_exactly_one_verify_command_bash_scripts_verify_sh`;
  - `D131InitTests`: `test_init_creates_an_executable_verify_script`, `test_an_existing_verify_script_is_never_overwritten`, `test_mesh_init_writes_the_one_command_config_and_never_rewrites_an_existing_one`;
  - `D131ScriptRunTests` (real bash, with fakes on PATH): `test_a_docs_only_tree_passes`, `test_seeded_docs_drift_fails_it`, `test_seeded_structure_drift_fails_it`, `test_package_json_runs_npm_ci_and_only_the_scripts_that_exist`, `test_a_failing_npm_script_fails_it`, `test_pyproject_runs_ruff_and_pytest_when_installed_and_skips_them_when_not`.
- **D132:**
  - `D132StateTests.test_new_refuses_an_unknown_type_with_contract_incomplete_and_lists_the_allowed_types`;
  - `D132ToolTests`: `test_work_new_refuses_an_unknown_type_and_writes_no_item`, `test_a_custom_type_declared_in_the_config_is_accepted`, `test_the_work_new_schema_lists_the_allowed_types_as_an_enum`.
  - Adapted: `test_mesh_state` (the ready-time refusal still covered: the config drops the type after `new`) and `test_mesh_mcp` `HandshakeTests` (expects the enum).
- **D134:**
  - `D134AuthorHintTests.test_the_hint_is_printed_and_returned_when_user_name_is_unset_and_absent_when_set` (empty `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_NOSYSTEM=1`);
  - `D134ToolHintTests.test_the_hint_reaches_the_mesh_init_tool_result_through_the_servers_env`;
  - `D134ShipLineTests.test_the_ship_lead_commits_the_state_files_with_each_batch`.

## Evidence
| check | result | exit |
|---|---|---|
| RED (worker log `~/.local/state/supervisor-mesh-qa/WO-0042/tdd-log.md`): `python3 -m unittest -v test_mesh_adoption_wo0042` | `Ran 17 tests`, `FAILED (failures=11, errors=4)` | 1 |
| GREEN, lead re-run of the same module | `OK` | 0 |
| `make check` (lead log `.../WO-0042/lead-make-check.log`) | `Ran 691 tests`, `OK`, `no doc drift`, `no structure drift`, `scrub: 0 hits`, `audit: PASS` | 0 |
| `scripts/scrub.sh` | `scrub: 0 hits` | 0 |
| `ruff check mesh tests` | `All checks passed!` | 0 |
| `claude plugin validate . --strict` | `Validation passed` | 0 |
| Lead end-to-end on a throwaway git repo (empty global git config): `scaffold.py --target . --stack none` | `CREATE scripts/verify.sh`, mode `755`, hint line printed | 0 |
| … `bash scripts/verify.sh` on that docs-only tree | `verify: all checks passed` | 0 |
| … after `git config user.name tester`, re-run scaffold | `hint:` lines: 0 | 0 |
| … after appending a reference to `docs/no-such-file.md` to `AGENTS.md`, `bash scripts/verify.sh` | fails | 1 |

## Lead review (every file)
- `verify.sh`: it changes to the project root through `$0`, so `bash scripts/verify.sh` works from the verify worktree. The exec bit does not matter there because bash is called explicitly. It uses no network beyond `npm ci`, and only when `package.json` exists.
- The `type_known` guard only refuses more on the existing `new` row and adds no transition; the TABLE-to-TLA conformance test passes. I did not run the formal check (`make formal`); it is the PM's gate, and the PM can run it if it wants.
- `tools/list` now reads the config, and `config_or_defaults` catches `MeshError`, so a project without `.mesh` still lists the tools. `Context.store` is always set.
- `spawn-roles` drops the old "split into the orchestrator's own window" path; all roles now go to `mesh*` windows, as D133 asks.
- My own first end-to-end attempt called `scaffold.py` without `--target`; that was my usage error (exit 2), not a defect. I re-ran it correctly above.

## Open: PM decision (D134 vs. `mesh_init`)
D134 says `.mesh/events.jsonl` stays tracked, but `mesh/init.py` `MESH_GITIGNORE` ignores it (with `refusals.jsonl`, `runs/` and `bin/`). So in a fresh project only `state.json` is tracked. The worker left the gitignore unchanged and qualified the ship line:

> `.mesh/state.json` and `.mesh/events.jsonl` stay tracked: include them in each batch commit as they are at commit time (never edit them; `events.jsonl` only where the project tracks it, since `mesh_init` gitignores it by default).

Lead recommendation: keep `events.jsonl` ignored and amend D134 to cover `state.json` only. Since D123 the event log holds operator prompt text, and committing it would publish operator prompts. If the PM wants it tracked instead, the change is one line in `MESH_GITIGNORE`, a migration for existing projects, and the ship line without the qualifier.

## Commit path list (WO-0042)
`mesh/defaults.py`, `mesh/git.py`, `mesh/init.py`, `mesh/mcp_server.py`, `mesh/state.py`, `mesh/tools.py`, `skills/init/SKILL.md`, `skills/spawn-roles/SKILL.md`, `templates/verify/verify.sh`, `agents/role-lead-ship.md` (it also carries the PM's DEC-0017 lines), `tests/test_mesh_adoption_wo0042.py`, `tests/test_mesh_mcp.py`, `tests/test_mesh_state.py`, `docs/reports/WO-0042-backend.md`.

Other modified or untracked files in the tree belong to other sessions: `agents/pm-orchestrator.md`, `docs/decisions/`, `docs/designs/*`, `docs/tracker.md`, `docs/reports/2026-11-20-adoption-feedback.md` and `docs/work-orders/2026-11-20-adoption-fixes.md`.

## Rework 1: D134 narrowed (PM decision)
The PM accepted the recommendation: only `.mesh/state.json` is tracked and committed by Ship. `events.jsonl` and `refusals.jsonl` stay git-ignored as `mesh_init` writes them. The spec D134 row is narrowed. The same in-process worker did the rework.

- `agents/role-lead-ship.md`: the D134 line is now `` - `.mesh/state.json` stays tracked: include it in each batch commit as it is at commit time (never edit it); the other `.mesh/` logs stay git-ignored as `mesh_init` writes them. ``
  - The lead diff against the pre-dispatch snapshot shows only this `+1` line; the PM's DEC-0017 lines are untouched.
  - `grep -c events.jsonl agents/role-lead-ship.md` gives 0.
- Test: `D134ShipLineTests.test_the_ship_lead_commits_state_json_with_each_batch_and_never_the_ignored_logs`. Exactly one line names `.mesh/state.json`, it says "each batch commit" and "git-ignored", and there is no `events.jsonl` in the file.
- Init and skill text: nothing added says `events.jsonl` is tracked. The `skills/init/SKILL.md:16` mention is the unrelated deny rule. `MESH_GITIGNORE` is unchanged.

| check | result | exit |
|---|---|---|
| RED (worker log, "Rework 1"): `D134ShipLineTests` | `'git-ignored' not found`, `FAILED (failures=1)` | 1 |
| lead: `python3 -m unittest test_mesh_adoption_wo0042` | `OK` | 0 |
| lead: `make check` (log `.../WO-0042/lead-make-check-rework1.log`) | `Ran 691 tests`, `OK`, `scrub: 0 hits`, `audit: PASS` | 0 |
| lead: `ruff check mesh tests` | `All checks passed!` | 0 |

The commit path list is unchanged.
