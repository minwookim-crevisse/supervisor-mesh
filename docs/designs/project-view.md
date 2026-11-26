# Project view and hint catalogue (D135, 0.3.0)

> Status: DECIDED 2026-11-25 (operator answers Q1-Q16, architecture review candidates A + B). Absorbs D130 (`guided-onboarding.md`), D127-D129 (`session-continuity.md`) and adoption finding A9.
> Source review: `/mattpocock-skills:improve-codebase-architecture` on `mesh/` (report kept outside the repo). Vocabulary: module, interface, depth, seam, adapter.

## Problem
No module answers "what state is this project in". `mesh_status` (mesh/tools.py:487-530) assembles the view inline; next-step hints are strings in at least six modules, and the Part B hint exists in two wordings (mesh/init.py:38, mesh/tools.py:350). projmux agents are fetched twice per `mesh_status` (once by `ctx.caller()` identity resolution, once at tools.py:503). Onboarding stages, PM-session continuity and recovery hints would each grow tools.py further.

## Decisions
| Q | decision |
|---|---|
| Q1 | New module `mesh/view.py` (project view) and `mesh/hints.py` (hint catalogue); view uses hints |
| Q2 | One snapshot fetches projmux agents once; `identity.resolve` accepts the already-fetched agents |
| Q3 | Interface: `project_view(snapshot) -> View` (frozen dataclass); `mesh_status` renders it; existing keys keep name and shape; new keys `onboarding`, `pm_session`, `next` |
| Q4 | Two steps: WO-0044 pure move (byte-identical output, existing tests unchanged), WO-0045 new facts |
| Q5 | Catalogue holds next-step hints and refusal texts (codes unchanged), each with `operator_only` and order |
| Q6 | An uninitialized project yields `onboarding.stage == "scaffold"` instead of only `initialized: false` (WO-0045) |
| Q7 | CLI init (`NEXT`, `AMBIGUOUS`, `AUTHOR_HINT`) and `mesh_init` read the same catalogue; the CLI adds its `# ` prefix at output |
| Q9 | All I/O in the snapshot (store, one projmux fetch, file facts, report mtimes, git fact); `project_view` is pure |
| Q10/R1 | The role list comes from `AGENTS.md` "Roles in this project" only, through one parser shared with spawn-roles; no `[roles]` in config (the PM never writes `.mesh/`) |
| Q11 | `next`: ordered list, at most 3, first = do now; each `{id, text, operator_only, command}` |
| Q12 | Catalogue text is English; the PM translates to the operator's language |
| Q13 | WO-0045 implements D130 stages, D127 context reset, D128 hints (lead registration, A9 resume-before-re-pin), the D128 routine text and D129 `docs/handoff.md`; it replaces WO-0041 |
| Q14 | Order: WO-0043 PASS → Ship commit → WO-0044 → WO-0045 (same files) |
| R2 | Snapshot git fact: are `AGENTS.md` and `.mesh/state.json` committed (no repo or no commit = stage not done) |
| R3 | `claude_session` is asked only when a pinned PM exists and is Running; failure gives `pm_session.context == "unknown"` (diagnostic, never a refusal) |

## Tests (TDD: red captured before green, one vertical slice at a time; expected values from the spec tables, never recomputed)
Unit (backend):
- U1 `view.project_view(snapshot)`: table over fixture snapshots → stage, `next[]`, `pm_session`, legacy keys; both sides of every stage boundary.
- U2 `hints`: unique ids; every `operator_only` hint has a `command` starting with `/`; the Part B sentence occurs once in the source tree.
- U3 `mesh_status` through the tool interface with the existing projmux fakes: WO-0044 output byte-identical to a capture taken at the base commit, and projmux `agents` invoked once; WO-0045 new keys, uninitialized → `scaffold`, R3 conditional session call.
- U4 `mesh_init` and CLI init print catalogue text.
- U5 a dispatch refused with the projmux readiness reason adds the resume hint to that lead; a transient refusal does not (unit only: not reproducible live).
E2E (qa, `verify-supervisor-mesh`, throwaway projmux project, live panes, evidence under `~/.local/state/`):
- E1 empty project → init → reference → Part B → spawn-roles → first commit → `/supervisor-mesh:run`; capture `mesh_status` stage and `next[0]` at each stage; spawn-roles shows `operator_only: true` and the PM does not try it.
- E2 `/clear` in the PM pane → `pm_session.context == "reset"`; resume → `same`.
- E3 pinned PM stopped, new PM pane → refusal and `next` show the resume command first.
- E4 regression: the existing verify-supervisor-mesh scenarios pass.
WO-0044 is gated by U3 (byte-identical, one fetch) and E4; WO-0045 by all the rest.

## Out of scope
Candidates C (PM pin and session module), D (typed dispatch attempts) and E (planning-artifact reader) from the review; the project → plugin feedback loop (D103, separate design).
