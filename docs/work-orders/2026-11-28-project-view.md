# Work orders - project view (0.3.0)

> Paths: `<repo>` = this repo. Absolute paths travel only in dispatch messages (DEC-0008).
> Common header: same as `docs/work-orders/2026-10-21-verification-skills.md`, EXCEPT write access as stated.
> Operating model: lead + in-process subagent worker + lead review (DEC-0016). No worker pane.
> Design: `docs/designs/project-view.md` (D135, Q1-Q5, Q9, U3, E4).

## WO-0044 - move `mesh_status` into `mesh/view.py` + `mesh/hints.py` (role: backend) - depends: WO-0043 PASS + WO-0042 commit (both met; base 5105b84, release 0.2.2)
**Read set:** `docs/designs/project-view.md`; spec row D135; `mesh/tools.py` (`mesh_status`), `mesh/identity.py` (`resolve`), `mesh/projmux.py` (`agents`, `live_members`), `mesh/status.py`, `mesh/init.py` (`NEXT`, `AMBIGUOUS`, `AUTHOR_HINT`), the seven test files that name `mesh_status`.
**Write access:** NEW `mesh/view.py`, NEW `mesh/hints.py`, `mesh/tools.py`, `mesh/identity.py`, `mesh/init.py`, NEW `tests/test_mesh_view.py`, your report `docs/reports/WO-0044-backend.md`. Existing tests stay UNCHANGED.
**Must:**
- Before any edit, capture `mesh_status` output at the base commit for a table of fixture states with a fixed clock and the existing projmux fakes: not initialized; no items; items in every phase; pending actions; stalls; overlaps; a role with 0, 1 and 2 live leads; projmux unavailable; caller PM, lead, unlabelled, outside projmux; `metrics` true and false. The captures are the expected values (literals in the test), never recomputed from the new code.
- One snapshot does all I/O; `project_view(snapshot) -> View` is pure (frozen dataclass); `mesh_status` renders it. `identity.resolve` accepts the already-fetched agents; its other callers keep their behavior.
- projmux `agents` is invoked once per `mesh_status`. When that one fetch fails, the output equals today's for the same failure (identity unknown, `projmux: unavailable`, `present: null`, no stalls).
- `hints.py` holds today's texts verbatim. Both Part B wordings stay as they are: unifying them changes output and belongs to WO-0045. No new keys, no new hints.
- Nothing from D127-D130, D137 or A9 in this order.
- Base is 5105b84: `identity.resolve` already carries the WO-0049 rework 2 reasons (`Resolved.reason`, the caller_not_pm `rule`); keep them byte-identical in every captured refusal.
**DoD (captured):** RED first (the one-fetch test fails at the base), then GREEN; byte-identical output for every captured state; `git diff --stat -- tests` shows only the new file; `make check` exit 0 with the tail; `ruff check .` exit 0; scrub 0 hits; the diff stat. Lead review of every file before reporting.
**Then:** qa runs E4 (the existing verify-supervisor-mesh scenarios) on the result; a separate order.

## Constraints
Read-only on `docs/decisions/`, `docs/tracker.md`, `docs/work-orders/`, `AGENTS.md`, `README.md`, design docs. No commits, no push. No worktree-destroying git commands. No secrets or absolute home paths in repo files. Self-report is not evidence.
