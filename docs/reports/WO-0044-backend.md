# WO-0044: move `mesh_status` into `mesh/view.py` + `mesh/hints.py` (D135, 0.3.0 step 1) (backend)

Verdict: **PASS, commit-ready** (lead-verified 2026-11-28, base 5105b84). Worker: one in-process subagent (DEC-0016). Nothing committed; commit and push are Ship's.

## Change (a pure move: no new keys, no new hints, no new behaviour)
- **NEW `mesh/view.py`**:
  - `Snapshot` (frozen): caller, doc or None, live lead roles or None (projmux unavailable), companions, launcher, operator events, D102 stalls, metrics flag.
  - `View` (frozen) and `Lead`.
  - Pure `project_view(snapshot) -> View`: no store, no projmux, no filesystem, no clock.
  - `render(View) -> dict`: today's keys and order.
  - `attempt_log` moved here from tools.py.
- **NEW `mesh/hints.py`**: today's texts verbatim.
  - CLI init: `AMBIGUOUS`, `NEXT`, `AUTHOR_HINT`.
  - `PM_LABEL_HINT`.
  - mesh_init `next`: `STACK_AMBIGUOUS`, `PART_B`, `COMPANIONS_MISSING`, `show_plan(plan_id)`.
  - Both Part B wordings are kept (`NEXT` for the CLI, `PART_B` for mesh_init); unifying them is WO-0045.
- **`mesh/tools.py`**:
  - `mesh_status` is now `view.render(view.project_view(_status_snapshot(ctx, metrics)))`.
  - `_status_snapshot` does all the I/O, with ONE `ctx.pmx.agents()` call. A failure is kept as the `ProjmuxUnavailable` itself. That one answer feeds the caller, `live_members(agents=)` and `status.stalls`.
  - New `Context.pane_caller`: set in `__post_init__` when the caller is an `identity.PaneCaller`, and kept across `once_per_request`'s `replace`. The prefetched answer is used only when `pane_caller.pmx is ctx.pmx`; an injected caller uses `ctx.caller()` as before.
  - mesh_init `next` and `_pm_only` read from `hints`. `_last_event` and `attempt_log` are removed.
- **`mesh/identity.py`**:
  - New signature: `resolve(env, root, pmx, agents=None)`, and `PaneCaller.resolved(agents=None)`.
  - With `None` it fetches itself, as today. With a `ProjmuxUnavailable` it raises it where `live_members` ran. So `no_pane`, `ambiguous_pane` and `not_managed` still come before `projmux_unavailable`.
  - The other callers are unchanged: `PaneCaller.__call__`, `layers.proven_caller` and `cli.py`.
- **`mesh/init.py`**:
  - The four constants are removed; `AMBIGUOUS`, `AUTHOR_HINT` and `NEXT` are imported from `hints`, so `init.NEXT` still resolves.
  - `init.PM_LABEL_HINT` is gone. Its only user was tools.py, which now uses `hints.PM_LABEL_HINT`.
- `git diff --stat -- mesh`: 3 files, +64 / −78. Untracked: `mesh/hints.py`, `mesh/view.py`, `tests/test_mesh_view.py`.

## Tests (`tests/test_mesh_view.py`, 23 tests; existing tests unchanged)
- **`ByteIdenticalStatusTests`**: 21 states captured at 5105b84 with a fixed clock and the existing projmux fakes, kept as literals.
  - States: not_initialized, no_items, every_phase (all 12), pending_actions, stalls (all five kinds), overlaps, leads_0_1_2, projmux_unavailable, projmux_panes_unavailable, caller_pm, caller_lead, caller_unlabelled, caller_invalid_role, caller_outside_projmux, caller_no_pane, caller_not_managed, caller_ambiguous, caller_other_repo, metrics_true, metrics_false, metrics_true_not_initialized.
  - Each test compares the `mesh_status` JSON text, including key order.
  - The caller and projmux states also compare the `identity.resolve` result (kind, role, rule, reason, pane_uid, live_pms) and a caller_not_pm refusal (message and `rule`). This keeps the WO-0049 rework 2 reasons identical.
- **`OneFetchTests`**:
  - projmux `agents` is asked once per `mesh_status` call;
  - resolving from the fetched agents answers as its own fetch would.
- The capture script and output are `~/.local/state/supervisor-mesh-qa/WO-0044/capture.py` and `capture.json` (0 `/home/` hits).

## Evidence
| check | result | exit |
|---|---|---|
| lead: the new test file run against a detached worktree of 5105b84 (worktree removed afterwards) | `Ran 23 tests`, `FAILED (failures=6, errors=12)`. All 21 byte-identical tests pass at the base. The 6 failures are the one-fetch subtests (`2 != 1`); the 12 errors are the fetched-agents test, which uses the new `agents` argument | 1 |
| RED, worker at the base: `OneFetchTests` | `FAILED (failures=6)`, each `2 != 1` | 1 |
| GREEN, worker: `test_mesh_view` | `Ran 23 tests`, `OK` | 0 |
| worker: the seven existing mesh_status modules, unchanged | `Ran 207 tests`, `OK` | 0 |
| worker `make check` | `Ran 778 tests`, `OK`, `audit: PASS` | 0 |
| lead `make check` (load avg 6; log `.../WO-0044/lead-make-check.log`) | `Ran 778 tests in 519.277s`, `OK`, `no doc drift`, `no structure drift`, `scrub: 0 hits`, `audit: PASS` | 0 |
| lead: `uvx ruff check .` | `All checks passed!` | 0 |
| lead: `scripts/scrub.sh` | `scrub: 0 hits` | 0 |
| lead: `git diff --stat -- tests` | empty; only `?? tests/test_mesh_view.py` is new | 0 |

## Lead review (every file)
- `project_view` and `render` were checked line by line against the removed inline code: the same item sort, the phase grouping order, the lead set (`roles | live`), the pending-action shape, the `last_event` id and seq only when initialized, overlaps, and metrics only when asked (None without a doc). `projmux: "unavailable"` is set exactly when `live` is None, which covers no adapter as well as a failed fetch.
- Stalls are empty without a doc, a root or projmux, as before.
- The caller is unchanged for a read-only tool: `once_per_request` called `resolve()`, which is `PaneCaller.resolved().caller`, and the new path is `resolved(agents).caller` with the same answer.
- `live_members(agents=...)` with a given tuple makes no projmux call, so moving it out of the `try` cannot raise.
- `hints.py` was compared word for word with the removed strings.

## Deviations
1. Stalls and live roles are worked out in the snapshot, because they read report mtimes and realpaths, and `status.py` and `projmux.py` are outside the write set. `project_view` is pure over what they return.
2. Operator events are read once per call instead of up to twice. The output is the same.
3. `init.PM_LABEL_HINT` was removed (the text is now `hints.PM_LABEL_HINT`); nothing else referenced it.

## Commit path list (WO-0044)
`mesh/view.py`, `mesh/hints.py`, `mesh/tools.py`, `mesh/identity.py`, `mesh/init.py`, `tests/test_mesh_view.py`, `docs/reports/WO-0044-backend.md`.

The PM's uncommitted files are not part of this list and were not touched: `docs/tracker.md`, `docs/handoff.md` and `docs/work-orders/2026-11-28-project-view.md`.
