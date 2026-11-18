# WO-0022 — mesh package + stdio MCP server (backend)

Status: **verified, ready** (lead-verified 2026-11-18). Every DoD item has captured evidence below. Commit/push is Ship's (WO-0025). Open gap G-4 is for the PM.

## Step 1 — scope (`/mattpocock-skills:improve-codebase-architecture`)
Provenance: the command was run in the backend lead pane twice on 2026-11-18. The orchestrator says it typed the keypresses on the operator's explicit instruction; the lead did not verify that. The second invocation carried the arguments "WO-0022 scope: docs/designs/mcp-server-spec.md against skills/, templates/, skills/init/*.py". One read-only explorer walked the area. The lead spot-checked these citations:
- `skills/init/scaffold.py:44-50` (Log.add prints), `:153-178` (main);
- `skills/dispatch/SKILL.md:13` vs `skills/spawn-roles/SKILL.md:20` (pane-match copies differ);
- `pyproject.toml:6` (`>=3.10`).

The HTML candidates report was written outside the repo (OS temp dir).

### Seams (what moves into `mesh/`)
| # | module | interface (shape only, settled in step 2) | strength | seam / adapters |
|---|---|---|---|---|
| 1 | `mesh/state.py` | apply a verb as a caller to an item; the declarative table (state model §4) is data that the CLI, MCP and tests all read; guards, counters, recorded refusals and the lock + CAS write (§8) stay internal | Strong | in-process; store rooted at an injected project root, with `now`/`seq` passed in |
| 2 | `mesh/projmux.py` (+ `mesh/identity.py`) | live lead for a role in this project, send a message, the caller's pane. It absorbs the pane-match rule (three copies today: spawn-roles, dispatch, the verifier's smv.py), the argv and the error mapping (`dispatch:28,30`) | Strong | real seam: a subprocess adapter + a JSON replay fake |
| 3 | `skills/init/scaffold.py` | `init(target, stack, dry_run) -> result`; `main()` stays the print adapter with byte-identical text (tests and the live verifier match `CREATE/MERGE/SKIP` lines) | Strong | in-process; `mesh_init` calls `init()`; scaffold.py stays as the wrapper (DEC-0009) |
| 4 | `mesh/tracker.py` | render `docs/tracker.md` from state.json; one-time legacy import (raw text kept as `legacy`, never `verified`, never deleted — O5/D31) | Strong | in-process |
| 5 | `mesh/dispatch.py` | compose the standard header from the item record and send it through #2; record the message-ref | Worth exploring | uses #2 |
| 6 | `mesh/verify.py` | run the config's argv commands in a disposable `git worktree` at the submitted commit with a timeout; return evidence bound to commit + contract version + config hash | Worth exploring | real git in temp repos for tests (one adapter, so no fake seam) |
| 7 | role resolver | only the role → live-lead part that dispatch needs; full spawn-roles unification is out of the MVP | Speculative | — |
| — | `mesh/cli.py`, `mesh/mcp_server.py` | thin front doors over the same functions; MCP stdout = JSON-RPC only, logs on stderr | — | — |

### Legacy-import edge cases (from the live `docs/tracker.md`)
- Multi-role cells: `security, backend, qa`.
- Annotated statuses: `FAIL (2 🔴)`, `PASS (open item → WO-0005)`, `pass with correction (…)`.
- An off-vocabulary status: `waiting`.
- Depends written as ranges (`WO-0001..0003`), prose (`batch 3 closed`) or with `∥` annotations.
- Report cells with two paths.
- Rows out of order.
- Prose after the table.

### Stays outside `mesh/`
- `templates/` (content).
- The Slack bridge and the slack-setup skill (out of the MVP).
- `skills/init/companions.py` (privacy-scoped).
- The gc scripts.
- add-agent stamping, `agents/*.md`, the methodology docs and the Part B interview.

### Kept contracts (must not break)
- scaffold's printed lines (`tests/test_scaffold*.py` and the live verifier).
- The strings `Do NOT auto-spawn` and `projmux agent message send` in `skills/dispatch/SKILL.md` (`tests/test_components.py:28-31`).
- The CLI fallback in the dispatch skill.

### Gaps for the PM (not in the backend write set)
- G-1: the PM pane is started by hand and has no `role=pm` label, but the spec's identity rule expects role label `pm`. Needs a rule (e.g. the pm skill labels its pane, or identity maps `pm-orchestrator`).
- G-2: `pyproject.toml` declares `requires-python >=3.10` / ruff `py310`, but the spec needs 3.11 (`tomllib`). That file is not in the write set.
- G-3: the dispatch header exists in four drifting copies (`skills/dispatch`, `agents/pm-orchestrator.md`, `AGENTS.md`, `templates/AGENTS.md`). The MVP unifies only the dispatch path.

## Step 2 — shape (`/architect` with checkpoint) — CHECKPOINT, awaiting PM sign-off
Durable record: `~/.local/state/supervisor-mesh-qa/WO-0022/architect/` (`grounding.md`, `candidate-1/`, `candidate-2/`, `synthesis.md`).
- Arena: 2 runners (opus xhigh, sonnet xhigh), no dropouts. The cross-judge (sonnet xhigh, read-only) scored candidate-1 23 and candidate-2 24 and recommended candidate-2 as the base. The lead agrees.
- Lead re-ran both table claims against `tests/formal/WorkItemState.tla`:
  - cand-1 `rowto_check.py`: `TLA keys 79 PY keys 79`, 0 mismatches, exit 0;
  - cand-2 `TestTableShape`: `Ran 5 tests ... OK`, exit 0 (27 rows, 208 triples).
- Base candidate-2:
  - a 1:1 declarative table;
  - a pure `decide()` with the CAS as a stamp comparison;
  - one engine path with no subprocess under the lock;
  - append-only refusal and event logs;
  - a marked-region tracker render;
  - D88 as data with `--scope project`;
  - a single tool registry.
- Grafts from candidate-1:
  1. the pane_command keypress protocol: re-classify after consuming the event, type literally, verify the text on the prompt line, then press Enter;
  2. seq-only event freshness (D74), Korean-tolerant whole-token name match, the hook bound to the PM session via `.mesh/bin/mesh`;
  3. a dispatch pending action that stores a hash and recomposes the text;
  4. structural stdout isolation in the MCP server.
- Dropped:
  - the `role-<role>` name fallback (D82);
  - the wall-clock TTL;
  - absolute paths in state and hooks;
  - the stdlib-shadowing module name.
- PM questions that block the build:
  - Q1: `skills/init/scaffold.py` in the write set;
  - Q2: `tests/_mesh_*.py` helpers;
  - Q3: does mesh_init write the §9a hook into the target project's `.claude/settings.json`.
- Accepted risk (D86): privilege-changing slash commands can be typed when the operator names them.

## Step 0 (before build) — pane identity env probe (PM ordering request)
- The pane's `claude` process has `PMX_INTERNAL_ACTIVATION_PANE_UID`; its parent is `projmux internal supervise`.
- A throwaway stdio MCP server, run via `claude -p --mcp-config <scratch> --strict-mcp-config` (Claude Code 2.1.327, exit 0), saw `present=True equal_to_parent=True`.
- Evidence: `~/.local/state/supervisor-mesh-qa/WO-0022/envprobe/`.
- Not yet proven for a server declared in a plugin's `.mcp.json` (WO-0024 live).

## Step 3 — build (`/arena`, core: model/state/store/engine/tools/mcp_server)
- The harness `isolation: worktree` failed: the WorktreeCreate hook returned no path. The lead created two detached worktrees (`git worktree add --detach`) under `~/.local/state/supervisor-mesh-qa/WO-0022/build/`. They are removed after the graft.
- Runners: X = opus xhigh (wt-1), Y = sonnet xhigh (wt-2). Lead re-runs:
  - X: `Ran 102 tests ... OK`, exit 0; ruff 5 fixable errors.
  - Y: `Ran 155 tests ... OK`, exit 0; ruff `All checks passed!`.
- Cross-judge (sonnet xhigh, read-only, ~60 in-memory probes): X 23, Y 25, base **Y**.
  - Bias note: the judge shares a model with Y. The lead checked the deciding defects in code:
    - X `state.py:659-671`: override applies any target with no close or depends gates, so an override into closed with unacked replies breaks inv 7; Y refuses it.
    - Y `state.py:1353-1357`: freshness comes from an unlocked fact (race); graft X's under-lock seq check.
    - Y `engine.py:85`: `drain` has no production caller; graft X's auto-drain.
- Lead decision: invalid tool arguments return an `isError` result with `invalid_args` (newer MCP guidance: the model can self-correct). Unknown tool or missing name stays -32602. This deviates from the build brief; recorded.

## Step 3 — graft into the main tree (base Y + X fixes)
- Lead re-run:
  - `python3 -m unittest discover -s tests -p 'test_mesh_*.py'` → `Ran 165 tests ... OK`, exit 0.
  - `make check` → exit 0 (`Ran 258 tests`, `OK`, `scrub: 0 hits`, `audit: PASS`).
  - `claude plugin validate . --strict` → `Validation passed`, exit 0.
  - ruff on the mesh files → clean.
  - The `pyproject.toml` diff is exactly `requires-python = ">=3.11"` and `target-version = "py311"`.
- Grafts, each RED then GREEN (log: `~/.local/state/supervisor-mesh-qa/WO-0022/build/graft-tdd-log.md`):
  - (a) Operator-event freshness via `Doc.last_operator_seq` under the lock (`mesh/state.py:1356`). RED: the race test returned Applied.
  - (b) Auto-drain after a non-noop commit, and X's `FactsProvider(needs, doc, req, cfg)` contract with the engine owning `OPERATOR_EVENT`.
  - (c) `Store.create` under the flock; 0600 modes. RED: modes were 0o666 / 0o644.
  - (d) The AST import-contract test plus a no-clock check, and the stale-during-gather test. RED by mutation on a scratch copy.
- Rework sent back (lead): a drain error after the caller's committed write must not reach the caller.
- Environment note: the harness `isolation: worktree` failed (the WorktreeCreate hook returned no path). Manual worktrees wt-1 and wt-2 were removed with `git worktree remove --force <exact path>` (exit 0 each). `--force` was needed because the worktrees held untracked candidate files; those were snapshotted first to `build/snapshot-wt-1/` and `build/snapshot-wt-2/`. `git worktree list` shows only main. No prune, reset or clean was run.
- Write-set extension (PM): `tests/test_scaffold_regression_01.py`, for the I001 import re-sort only (caused by the py311 bump). The pre-existing `smv.py:42` I001 is routed to WO-0024.
- Rework verified by the lead:
  - `mesh/engine.py:80-84` wraps the drain; it prints the error type only to stderr and returns the caller's own outcome.
  - Mesh tests: `OK`, exit 0.
  - The `tests/test_scaffold_regression_01.py` diff touches import lines only (`import tomllib` moved into the stdlib block); `discover -p test_scaffold_regression_01.py` gives OK.
  - `ruff check .` → `Found 1 error`, the pre-existing `smv.py:42` only (routed to WO-0024).

## Step 4 — wiring (sequential workers W1 → W2 → W3)
Brief: `~/.local/state/supervisor-mesh-qa/WO-0022/wire/brief.md`.
- W1: identity, projmux/tmux, mesh_status, work_dispatch, pane_command.
- W2: verify runner and git facts.
- W3: tracker, mesh_init + scaffold seam + hook, companions_install, cli, .mcp.json, docs.
- W1 (identity, projmux/tmux, mesh_status, work_dispatch, pane_command): lead re-run.
  - Mesh tests: `OK`, exit 0 (233).
  - The 8 PM-required `PaneCommandRequiredTests` all `ok`.
  - `subprocess` is imported only in `mesh/runner.py`; `shell=True` 0 hits.
  - `mesh/companions.py` returns presence booleans only.
  - ruff `All checks passed!`; scrub 0 hits.
  - TDD log: `~/.local/state/supervisor-mesh-qa/WO-0022/wire/W1-tdd-log.md`.
  - For WO-0024 to confirm live: the projmux `get agents` / `get panes -A` JSON shapes (assumed from the design doc), and `--message-ref` dedup.
- W2 (verify runner, git facts): lead re-run.
  - Mesh tests: `OK`, exit 0 (273).
  - Rework R1 (lead): `git worktree prune` was removed from `mesh/git.py`, because prune on the shared repo drops other sessions' worktrees. Cleanup is now `worktree remove --force <exact path>`.
  - New test `test_cleanup_removes_only_its_own_worktree_and_never_prunes_others`: RED on the old code (the other entry was pruned), GREEN after.
  - Required tests are in `tests/test_mesh_verify.py`: inv 12 shared tree untouched; timeout kills the process group; inputs_changed (twice); prose never verified (4).
  - Log: `~/.local/state/supervisor-mesh-qa/WO-0022/wire/W2-tdd-log.md`.
  - Lead-accepted choices: the precheck rule (falls back to the verify commands being runnable; spec silent); worktrees in system tmp, logs under `.mesh/runs/`.
- W3 (tracker, mesh_init + scaffold seam + hook, companions_install, cli, .mcp.json, docs, plus the gstack-detection add-on): lead re-run.
  - `make check` exit 0 (`Ran 419 tests`, `OK`, `scrub: 0 hits`, `audit: PASS`); `claude plugin validate . --strict` passed, exit 0; `ruff check .` → only the pre-existing `smv.py:42`.
  - Scaffold byte-identity: `diff -r scaffold-before scaffold-after` → no output, exit 0 (ts/python/none: dry-run, apply, re-run, merge cases, ambiguous stack; target path normalised).
  - Stdio transcript `~/.local/state/supervisor-mesh-qa/WO-0022/wire/stdio-transcript.jsonl` (25 lines, 0 `/home/` hits, real production identity with fakes on PATH): initialize, tools/list, and one call each to the 10 tools, every one `isError False`.
  - The add-on helper prints only the 4 presence lines (`--home /nonexistent-dir-x` gives 4× missing).
  - `.mcp.json` matches the spec (server `supervisor-mesh`, `python3 ${CLAUDE_PLUGIN_ROOT}/mesh/mcp_server.py`).
  - For WO-0024 to confirm live: the `claude plugin marketplace list --json` shape; the MCP tool name prefix used in the skills; and that `mesh_init` needs a `plan_id` from its dry run (apply refuses `plan_changed`).
  - W3's claim that pyproject still says 3.10 is wrong: it reads `>=3.11`. The `docs/tracker.md` mtime change is the PM's own edit (PM-owned file, already modified before this WO).
- W4 (D89 run skill + pm-orchestrator goal intake; D90 `mesh_metrics` + `mesh_status metrics:true`): lead re-run.
  - `make check` exit 0 (`Ran 430 tests`, `OK`, `scrub: 0 hits`, `audit: PASS`); validate passed, exit 0; `ruff check .` → only the pre-existing `smv.py:42`.
  - `tests/test_components.py` diff is the one-word `"run"` addition to SKILLS (PM-approved).
  - README: two added rows (the `mesh_metrics` tool, the `run` skill).
  - Tests:
    - `tests/test_mesh_metrics.py` (exact counts from a fixture history; receipts and hook errors not counted);
    - `tests/test_mesh_run.py` (every MCP tool named in the run, dispatch and init skills exists in `tools.REGISTRY`; mutation `work_bogus` killed).
  - CLI form: `mesh mesh_status --metrics` (derived from REGISTRY names).

## Step 5 — review (interrogate + Codex)
Full dispositions: `~/.local/state/supervisor-mesh-qa/WO-0022/review/dispositions.md`.
- Reviewers: A = opus xhigh (13 findings), B = sonnet xhigh (13), Codex `gpt-6-astra` read-only (9). The first Codex try failed because the configured model `gpt-5.5` is unsupported on this account; the retry exited 0.
- Lead repro of the top finding: with no PMX env, `MESH_TEST_MODE=1 MESH_TEST_CALLER=pm mesh/cli.py mesh_status` returned `"kind":"pm"`.
- Act on (fix round R1): F1 (remove the test identity seam; all 3 reviewers), F2c-d, F4-F21. Details in dispositions.md.
- PM decisions requested:
  - P1: operator-event provenance (host deny rules + parent-process check; correct the §9a claim);
  - P2: D85 proposal approval must cite the proposal id;
  - P3: override bound to the item id;
  - P4: verify escaping via absolute paths as an accepted risk;
  - P5: pin the config hash at `ready`;
  - P6: the `reject` trigger (formal model question).
- Dismissed: none.
- PM decisions (spec D96-D101; state model §4 rows ready/start/reject; lead checked spec lines 28-33 and state-model lines 205-206):
  - P1 = D96: deny rules written into the target project, a parent-process check, and id + seq + text-hash binding; the residual same-user risk is stated in §9a.
  - P2 = D97: the proposal id `P-<n>` as a whole token.
  - P3 = D98: the override event names the item id.
  - P4 = D99: accepted risk.
  - P5 = D100: the config hash is pinned at `ready` and checked at `start` and `verify`.
  - P6 = D101: the PM may reject on its own review, with reason + class.

  These are built in W5 as the single operator-binding layer (D95). D100 and D101 change guards, not the (verb, caller, from, to) projection, so the TLA table conformance test stays valid while QA re-syncs the formal model (WO-0027).
- D102 (stall detection plus a worker completion step) was added to W5 by the PM. Lead checked the spec row.
- R1 (review fixes F1, F2c, F2d, F4-F21): lead re-run.
  - The F1 repro now returns `"kind":"unknown"` (it was `"pm"`).
  - `grep MESH_TEST mesh/` → 0 hits; no prune call in `mesh/`.
  - `make check` exit 0 (`Ran 485 tests`, `OK`, `scrub: 0 hits`, `audit: PASS`); validate passed; ruff `All checks passed!`; `git worktree list` shows only main.
  - Log: `~/.local/state/supervisor-mesh-qa/WO-0022/review/R1-tdd-log.md`; tests: `tests/test_mesh_review_r1.py`.
  - Flagged to the PM:
    - F17 (class-specific revision before `ready`) and F21 (commit ancestry at `submit`) make guards stricter than the formal model; the projection test still passes, and this belongs with WO-0027;
    - F15: projmux interaction kind `unknown` now refuses `pane_command`, which WO-0024 must check live;
    - an existing install keeps its old hook command (the marker matches), so W5 refreshes it.
  - A stray `/tmp/mesh-wt-ps66i45_` predates R1 and has no owner file; it was left untouched.
- Orphan `/tmp/mesh-wt-ps66i45_` (created 16:46): its `wt/.git` pointed into a probe repo under the lead's session scratchpad, created by a review probe (Reviewer B's SIGTERM-during-verify probe) and since deleted. The lead removed that exact path; `ls /tmp/mesh-wt-*` now finds 0 entries. Cause and cover:
  - SIGTERM during `run_at` skipped the `finally` cleanup. R1 F8 now handles SIGTERM (`F8SigtermTests`) and reaps owner-dead `mesh-wt-*` worktrees of this repo at recovery (`F8WorktreeReapTests`).
  - A worktree whose owning repo no longer exists is not reachable by the repo-scoped reap. That is by design: mesh never touches another repo.
- W5 (D95 single layers, D96-D98, D100, D101, D106, D99 docs, D91-D94, D102): lead re-run.
  - `make check` exit 0 (`Ran 531 tests`, `OK`, `scrub: 0 hits`, `audit: PASS`); validate passed; `ruff check .` → only the pre-existing `smv.py:42` (WO-0024).
  - `D95OneImplementationPerConcernTests` OK (AST: one implementation per concern; all 11 tools wrapped by `mesh/layers.py`).
  - Stdio transcript regenerated (11 tools, all `isError False`, 0 `/home/` hits).
  - No `mesh-wt-*` left in tmp; worktree list = main.
  - Log: `~/.local/state/supervisor-mesh-qa/WO-0022/review/W5-tdd-log.md`; tests: `tests/test_mesh_review_w5.py`.
  - Recorded deviations: an item without a pin (pre-D100) has the hash recorded at `start`; the D96 parent check accepts the PM claude pid within 8 ancestor levels; worker-to-lead ownership uses the label `worker-of=<role>`; `dispatch.retry` counts resends (default 3, so up to 4 sends).

## Final DoD (WO-0022)
| DoD item | evidence | result |
|---|---|---|
| Spec acceptance 1 (unit: table walk, invariants, CAS, lock, legacy import, identity refusal, MCP handshake + tools/list + one tools/call per tool over a real pipe) | `tests/test_mesh_*.py`, RED→GREEN logs under `~/.local/state/supervisor-mesh-qa/WO-0022/{build,wire,review}/` | 435 mesh tests OK |
| Spec acceptance 3 | `make check` exit 0 (531 tests), scrub 0 hits, `claude plugin validate . --strict` exit 0 | PASS |
| Stdio transcript, all tools | `wire/stdio-transcript.jsonl` | 11/11 `isError False` |
| interrogate + Codex dispositions | `review/dispositions.md`; all "Act on" items fixed (R1); P1-P6 decided by the PM (D96-D101) and built (W5) | done |
| Spec acceptance 2 (TLA conformance) | the TABLE projection equals the TLA `Table` (`TestTableShape`) | passes. Guards stricter than the model (D100, D101, D104-D106) are with QA in WO-0027 |
| Spec acceptance 4 (live) | not backend's | WO-0024 |

## Accepted risks (operator decisions)
- D86: privilege-changing slash commands can be typed when the operator names them.
- D96 residual: a process of the same OS user can still forge an operator event.
- D99: verify commands can write outside the worktree.
- D88: companion sources are unpinned.

## Open gaps for the PM
- G-4: the D96 deny rule `Write(.mesh/**)` / `Edit(.mesh/**)` also blocks editing `.mesh/config.toml` (the verify config, D91/D92/D100) through Claude's tools in the target project, so the PM can only change it by hand or through Bash. Options: narrow the rules to `.mesh/state.json`, `.mesh/events.jsonl` and `.mesh/refusals.jsonl`; or move the config out of `.mesh/`.
- Live checks for WO-0024:
  - the plugin-declared MCP server receives the pane env;
  - the projmux JSON shapes and the idle interaction kind;
  - `--message-ref` dedup, and whether peer messages fire `UserPromptSubmit`;
  - the `marketplace list --json` shape;
  - the MCP tool name prefix.
