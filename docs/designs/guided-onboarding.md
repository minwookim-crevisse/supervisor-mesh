# Guided onboarding — init walks the operator to the first run (0.3.0 draft)

> Status: DRAFT (operator request 2026-11-20). Proposed decision D130; enters `docs/designs/mcp-server-spec.md` after review.
> Source: the first real adoption (a new expense-management project, 2026-11-20). The operator had to ask the plugin maintainer's PM what to do after every step, and was led into per-role dispatch instead of `/supervisor-mesh:run`. Operator: "init should do the whole initial setup and keep asking and explaining with hints."

## Relation to the approved design
`docs/designs/pm-led-onboarding.md` (APPROVED 2026-10-14, WO-0009..0015) made the PM pane drive onboarding and added preflight. D130 extends it past scaffolding: it carries the operator from references to the first `/supervisor-mesh:run`, one hinted question at a time.

## What went wrong (observed)
- `init` ends with one line: `# next: fill docs/harness/part-b-checklist.md`. Step 7 of `skills/init/SKILL.md` still says "spawn-roles → dispatch"; it never names `/supervisor-mesh:run`, the intended entry.
- No place for background documents. The operator asked for one; it was made by hand (`docs/reference/`).
- Order was unclear: plugin installed after the PM pane started (needs `/reload-plugins`); stack chosen before or after init; `stack none` then a second init for lint.
- Part B was filled through a long free-form exchange instead of one question at a time.
- Nothing said "you are done with setup; now give one goal to run".

## D130 — init is a staged, resumable onboarding guide
Stages, each derived from files on disk (no new state), so a fresh PM session resumes at the right stage:

| # | stage | done when | hint shown to the operator |
|---|---|---|---|
| 1 | scaffold | `.mesh/state.json` exists | preview → "type `apply`" |
| 2 | reference | `docs/reference/` has a file besides README | "put background docs in docs/reference/, then say `done`" (skippable) |
| 3 | understand | PM summary written to `docs/reference/SUMMARY.md` | PM reads references, shows domain summary + open questions |
| 4 | stack | stack lint files present, or operator chose `none` | ONE question (ts / python / none), with the reason from the references; re-applies init for lint |
| 5 | part B | no `<...>` blank left in `docs/harness/part-b-checklist.md` | ONE question per blank, recommended answer first, value written after each answer |
| 6 | roles | a live lead for every role in Part B | "run `/supervisor-mesh:spawn-roles`; press trust in each new pane" |
| 7 | first commit | the harness files committed | "Ship commits; push only when you type it in the ship pane" |
| 8 | first goal | at least one work item exists | "give ONE goal: `/supervisor-mesh:run <goal>`" with an example drawn from the references |

Rules:
- Every stage ends with a hint block: what just happened, the exact next thing to type, and why. One decision per question (the PM's question tool, recommended option first).
- `mesh_status` gains `onboarding: {stage, next_hint}` computed from the table; `/supervisor-mesh:init` with no arguments on an initialized project resumes at the first unfinished stage.
- init scaffolds `docs/reference/README.md` (stage 2) and the PM's restore order reads it.
- Step 7 of `skills/init/SKILL.md` and `NEXT` in `mesh/init.py` point to the stage guide and name `/supervisor-mesh:run`, not `dispatch`.
- Preflight in stage 1: if the plugin's MCP tools are absent in this session, say "run `/reload-plugins`" before anything else.
- The guide never decides for the operator: domain rules and Part B values come from the operator or the references, never invented (existing rule).

## Out of scope
- Writing domain code or work orders during onboarding (that is stage 8's `run`).
- Auto-spawning leads (operator-visible `spawn-roles` stays).

## Tests (draft)
- Stage derivation: each fixture tree yields the expected stage and hint; a partial Part B gives stage 5 with the first blank named.
- init on an initialized project resumes, writes nothing until the stage's own apply.
- `NEXT`, init step 7 and the PM restore order mention `/supervisor-mesh:run` and `docs/reference/`.
