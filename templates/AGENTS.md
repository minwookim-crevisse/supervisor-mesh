# <PROJECT NAME> — harness entry

> Convention docs for all agents. The decision log (docs/decisions/) outranks every other doc on conflict.

## Restore order (new/compacted session)
1. docs/decisions/  (latest decisions first — these win)
2. docs/conventions/glossary.md  (terms)
3. This file + the role model below

## Role model (supervisor mesh)
Tier-0 Orchestrator (one) → Tier-1 role leads → Tier-2 workers. A lead supervises a same-role worker and verifies output; it does not implement. Only <SHIP ROLE> pushes. Full method: (plugin) supervisor-mesh core methodology.

## Roles in this project  <!-- Part B: fill -->
- <role>: <what it owns · canonical docs · data boundary>

## Dispatch rules
Every work order carries: read-set (abs paths) · mission · operating model · the 4 principles · evidence-first DoD · constraints (no edits to docs/decisions or secrets; push = <SHIP ROLE> only) · deliverables · report target. No tracker row = no dispatch.

## Coordination docs (single owner)
The tracker and coordination docs are written by the Orchestrator alone. Every other session is read-only on them and reports via its own report file.  # why: parallel edits to shared coordination docs cause rollbacks and lost work.

## Production data anchor  <!-- Part B: fill -->
- <the one production store id/region. All applies/queries target this only. Never commit secrets.>

## Money / number rules (if applicable)  <!-- Part B: keep or delete -->
- See docs/conventions/.

## Cleanup rules (GC)
- Delete temp files the moment the task is done; temp goes to the session scratchpad, never the repo.
- Do not create `temp_` / `_new` / `_old` / `_backup` / `_fix` named files.
- Remove unused imports/variables immediately (surgical changes leave no orphans of your own).
- Strip debug code before the PR.
- Dispose one-shot delete/backfill scripts right after they run (identify targets by condition at run time, never by hardcoded ids — a stale script re-runs against new real data).
- Never run worktree-destroying git commands (reset --hard / checkout . / clean -fd) on a shared tree; revert specific files only.
