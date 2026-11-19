# WO-0039: D126 dispatch header follows DEC-0016 (backend)

Verdict: **PASS, commit-ready** (lead-verified 2026-11-19, base 64ad970). Nothing committed; commit and push are Ship's.

## Defect
The `work_dispatch` header (`mesh/dispatch.py` HEADER, mirrored in `skills/dispatch/SKILL.md`) told every lead to "spawn each worker with the label worker-of=..." That made every worker a separate pane, contradicting DEC-0016.

## Change
- `mesh/dispatch.py` and `skills/dispatch/SKILL.md` (pinned equal by `skill_header()`):
  - Operating model: `lead + in-process subagent worker + lead-review`.
  - Workers: the order's exact text. Each worker is the lead's in-process subagent. Only an order that explicitly asks for a separate worker agent uses `worker-of=<your role>`, the `<WO>-done` message and `projmux agent wait ... --until idle --timeout <duration>`.
- `skills/run/SKILL.md` section 6: the same rule, citing DEC-0016. The D102 path is the exception; the `stalls` text is unchanged.
- `tests/test_mesh_review_w5.py` `D102CompletionSignalTextTests`:
  - the header and skill contain the new phrases and the pinned D102 strings;
  - the old `spawn each worker with the label` is gone;
  - the header has no backtick and no `$`;
  - the run skill contains `in-process subagent` and not `spawn it with the label`.
- `git diff --stat -- mesh skills tests`: 4 files, +27 / −10.

## Evidence (lead re-run)
| check | result | exit |
|---|---|---|
| RED (worker log `~/.local/state/supervisor-mesh-qa/WO-0039/tdd-log.md`) | `Ran 2 tests`, `FAILED (failures=2)` ('in-process subagent' not found) | 1 |
| HEADER lines equal the order's text (lead script) | Workers line exact: True; Operating-model line: True | — |
| `python3 -m unittest discover -s tests -p 'test_mesh_review_w5.py'` | `OK` | 0 |
| 5 KB-order size: `test_mesh_security_wo0023` `DispatchMessageSizeTests` (5 KB order file, 3000-char goal, 40 read paths) | `OK`; header 1000 bytes, composed message 1526 bytes with a 16-character root, limit 4096 | 0 |
| `make check` (log `.../WO-0039/lead-make-check.log`) | `Ran 674 tests`, `OK`, `scrub: 0 hits`, `audit: PASS` | 0 |
| `ruff check mesh tests` | `All checks passed!` | 0 |
| `grep -rn "spawn each worker\|spawn it with the label"` over mesh, skills, agents, templates, docs/core, README.md and AGENTS.md | no hits | 1 |

## Commit path list (WO-0039)
`mesh/dispatch.py`, `skills/dispatch/SKILL.md`, `skills/run/SKILL.md`, `tests/test_mesh_review_w5.py`, `docs/reports/WO-0039-backend.md`.

The other modified and untracked files in the working tree are other sessions' work and are not part of this list: `AGENTS.md`, `README.md`, `agents/*`, `docs/*`, `templates/*`, `docs/reports/WO-0038-qa.md` and `docs/work-orders/2026-11-19-readme-diagrams.md`.

## Notes
- The apostrophe in "provider's" is safe on both send paths: mesh passes the message as an argv list with no shell, and the skill's manual fallback double-quotes the order.
- The size margin is large (1526 of 4096 bytes). A real project root only adds its extra path length per read path.
