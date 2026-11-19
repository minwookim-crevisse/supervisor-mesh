# WO-0034: D125 pane_command render wait (backend)

Verdict: **PASS, commit-ready** (lead-verified 2026-11-19, base b94d038). Nothing committed; commit and push are Ship's.

## Defect (live, WO-0024 step f)
On Claude Code 2.1.328 an operator-typed `pane_command` `/cost` aborted with `aborted_text_not_on_prompt` (receipt rc-000003). A typed `/command` reaches the input box only after the slash menu renders: absent at 0.5 s, present at 1.5 s. `_keypress` captured once, immediately after `send-keys -l`.

## Change
- `mesh/pane.py`: after `type_literal`, `_rendered` re-captures every `POLL_S = 0.1` s, up to `render_wait_s`, until `holds(screen, command)`.
  - Enter is pressed only then.
  - On timeout: `_clear` (D116), then `aborted_text_not_on_prompt`.
  - Other text in the box is never entered; it times out.
  - A `TmuxFailed` while polling gives clear plus `aborted_tmux_failed`.
  - `type_literal` runs exactly once (no retry of the keypress).
  - Clock and sleep are injected.
- `mesh/layers.py`: `poll_until(check, timeout_s, step_s, *, sleep, clock)`. The loop lives here because the D95 single-implementation test allows `sleep` only in `layers.py`.
- Config: `[pane_command] render_wait_s` (`mesh/model.py` `Config.pane_render_wait_s = 3`; `mesh/store.py` validates a non-bool number with 0 < v <= 30; `mesh/tools.py` passes it through; `mesh/defaults.py` adds the commented section).
- Tests: `tests/_mesh_fakes.py` gives the fake tmux a `render_after = N` option; `tests/test_mesh_pane.py` adds `RenderWaitTests` and `RenderWaitConfigTests`.
  - The cases: fills after N captures (done, one Enter); never fills (C-u, abort, bounded by the fake clock); other text (no Enter, abort); `TmuxFailed` mid-poll; config read by the tool; default 3; invalid values `0`, `-1`, bools, `"3"`, `31`, `1e9`, `nan` and `inf` refused.
- Three existing tests that expect the text never to arrive now set `render_wait_s = 0.2`, so they do not wait the real 3 s. Their assertions are unchanged. They are one in `test_mesh_pane.py` and two C5 tests in `test_mesh_security_wo0023.py`.

## Evidence (lead re-run)
| check | result | exit |
|---|---|---|
| RED (worker log `~/.local/state/supervisor-mesh-qa/WO-0034/tdd-log.md`) | `Ran 32 tests`, `FAILED (failures=2, errors=5)` | 1 |
| `python3 -m unittest discover -s tests -p 'test_mesh_pane*.py'` | `Ran 32 tests`, `OK` | 0 |
| `make check` (log `.../WO-0034/lead-make-check.log`) | `Ran 674 tests`, `OK`, `scrub: 0 hits`, `audit: PASS` | 0 |
| `ruff check mesh tests` | `All checks passed!` | 0 |
| leftover `mesh-confirm-*` / `mesh-wt-*` | 0 | — |

## Commit path list (WO-0034)
`mesh/defaults.py`, `mesh/layers.py`, `mesh/model.py`, `mesh/pane.py`, `mesh/store.py`, `mesh/tools.py`, `tests/_mesh_fakes.py`, `tests/test_mesh_pane.py`, `tests/test_mesh_security_wo0023.py`, `docs/reports/WO-0034-backend.md`. Diff for these files: +183 / −5.

Not WO-0034 work, but in the working tree: `agents/pm-orchestrator.md` (orchestrator edit, +10) and `docs/designs/mcp-server-spec.md` (the D125 row, +1). The orchestrator decides whether they go in the same commit.

## Notes for the PM
- If a trust or permission dialog appears after typing, the abort now comes after up to `render_wait_s` (default 3 s) instead of at once. Enter is still never pressed, and `_clear` still skips C-u on a dialog (D116).
- Editing `[pane_command]` changes the config hash, so it triggers the D100 `inputs_changed` check for in-flight items, like any other config edit.
- Live confirmation: re-run step f (`/cost`) on Claude Code 2.1.328.
