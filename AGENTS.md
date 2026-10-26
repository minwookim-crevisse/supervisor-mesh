# supervisor-mesh (plugin repo) — harness entry
<!-- supervisor-mesh:harness -->

> Repo-own instruction doc for developing THIS plugin. Not to be confused with `templates/AGENTS.md` (the shipped template).
> The decision log (`docs/decisions/0001-decision-log.md`) outranks every other doc on conflict.

## Restore order (new/compacted session)
1. `docs/decisions/0001-decision-log.md`  (latest decisions first — these win)
2. `docs/conventions/glossary.md`  (terms)
3. This file + the layer map below

## Two layers (do not mix)
| layer | paths | rule |
|---|---|---|
| **Shipped** (installed into other projects) | `.claude-plugin/` · `skills/` · `agents/` · `docs/core/` · `templates/` | project-agnostic: no project names, ids, people, secrets. Placeholders are `<...>`. |
| **Repo-own** (develops this plugin) | `AGENTS.md` · `.claude/CLAUDE.md` · `pyproject.toml` · `Makefile` · `tests/` · `scripts/` · `docs/decisions/` · `docs/conventions/` · `docs/harness/` · `.github/` | may describe this repo; still no secrets. |

### Repo-own docs layout (the standard minimum layout `mesh init` builds — design E1b, DEC-0014)
| path | purpose | writer |
|---|---|---|
| `docs/decisions/` | decision log (outranks every other doc) | orchestrator |
| `docs/conventions/` | glossary, constraint rationale | orchestrator |
| `docs/harness/` | Part B checklist | orchestrator |
| `docs/tracker.md` | work tracker (becomes a generated view of the `mesh` state file, .mesh/state.json, once `mesh` ships) | orchestrator now, `mesh` later |
| `docs/work-orders/` | dispatch orders | orchestrator |
| `docs/designs/` | design docs + review records (`*.review.*/` is gitignored) | orchestrator |
| `docs/reports/` | lead reports and QA evidence, `WO-<id>-<role>.md`; each lead writes only its own file | leads |
| `docs/core/` | shipped methodology (Part A) | orchestrator |

Reports written before 2026-10-19 may still cite the old docs/qa/ path; the live-QA record now lives at `docs/reports/2026-10-14-live-qa.md`.

`scripts/gc/*.py` are produced by `/supervisor-mesh:init` from `templates/gc/` (dogfood) and must stay byte-identical — edit the template, then re-copy.

## Role model (supervisor mesh)
Tier-0 Orchestrator (one) → Tier-1 role leads → Tier-2 workers. A lead supervises a same-role worker and verifies output; it does not implement. Only the Ship role pushes, and only after the maintainer confirms. Full method: `docs/core/methodology.md`.

## Roles in this project
- orchestrator: plans changes to the plugin, owns `docs/decisions/` and this file.
- qa: owns `tests/`, `scripts/audit.sh`, `scripts/scrub.sh`; verifies with `make check`.
- ship: owns commits/pushes/releases (version bump in `.claude-plugin/plugin.json`).

## Dispatch rules
Every work order carries: read-set (abs paths) · mission · operating model · the 4 principles · evidence-first DoD · constraints (no edits to docs/decisions or secrets; push = Ship only) · deliverables · report target. No tracker row = no dispatch.

## Coordination docs (single owner)
The decision log, `docs/tracker.md`, `docs/work-orders/` and this file are written by the Orchestrator alone. Every other session is read-only on them and reports via its own report file.

## Gates (all must exit 0 before ship)
- `make check` → `scripts/audit.sh`: `claude plugin validate . --strict` (marketplace, plugin manifest, skills, agents) · unit tests · GC (doc + structure) · scrub.
- Lint: `ruff check .` when ruff is installed (config in `pyproject.toml`).
- 🔴 GC and lint EXCLUDE `templates/` and `docs/core/` — they carry `<...>` placeholders and intentional `temp_` / `_old` / `_backup` example strings.

## Production data anchor
- None. This repo has no data store. Never commit `.env`, `.auth/`, `*.sqlite`, or tokens (blocked by `.gitignore`, checked by `scripts/scrub.sh`).

## Cleanup rules (GC)
- Delete temp files the moment the task is done; temp goes to the session scratchpad, never the repo.
- Do not create `temp_` / `_new` / `_old` / `_backup` / `_fix` named files.
- Remove unused imports/variables immediately (surgical changes leave no orphans of your own).
- Strip debug code before the PR.
- Dispose one-shot scripts right after they run (identify targets by condition at run time, never by hardcoded ids).
- When a document is superseded or copied for a review, delete its drift copies and outdated md in the same task; keep evidence (reports, verdict JSON, work orders).
- Never run worktree-destroying git commands (reset --hard / checkout . / clean -fd) on a shared tree; revert specific files only.
