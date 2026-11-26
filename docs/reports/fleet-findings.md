# Fleet findings ledger (D136)

> Single owner: the supervisor-mesh PM. One row per plugin finding, keyed by project alias + PF id. Each project's own `docs/reports/plugin-feedback.md` is the source of truth; this ledger tracks triage and the answer. Plugin-level facts only (DEC-0018).
> Axes: severity P0-P3 · kind bug/gap/docs/ux · status new / accepted (D, WO) / duplicate / wont-fix / needs-info / shipped (version).
> Aliases: `pilot` = the 0.1.2 test-bed project; `adopt1` = the first real adoption project (2026-11-20).

| project | PF | sev | kind | finding | status | answer |
|---|---|---|---|---|---|---|
| pilot | PF-001 | P1 | bug | audit invariant passed while checking 0 modules (false PASS) | shipped 0.2.0 (part) | D92 `fail_if_output`; the "zero checked = FAIL" audit template rule is a 0.3.0 follow-up, and the `tsPreCompilationDeps` default and version check have no decision yet |
| pilot | PF-002 | P1 | bug | projmux message sends failed (5) and one size-limit refusal | shipped 0.2.0 | D93 dispatch retry, 4096-byte refusal |
| pilot | PF-003 | P2 | gap | parallel items collided on a shared environment | shipped 0.2.0 (part) | D94 overlap warning only; enforced serialization is a 0.3.0 follow-up |
| pilot | PF-004 | P1 | bug | a finished worker never reached its lead | shipped 0.2.1 | D102, then DEC-0016 / D126 (worker = in-process subagent) |
| pilot | PF-005 | P2 | gap | init added eslint/ts-prune scripts without installing or naming the tools | accepted | 0.3.0 follow-up "init installs the lint devDependencies" |
| pilot | PF-006 | P2 | bug | companion check reported gstack missing | shipped 0.2.0 | gstack detection fix (WO-0022) |
| adopt1 | PF-001 | P0 | bug | init left `[verify]` empty, so nothing could close (A1) | accepted | D131, WO-0042 (lead PASS, live check WO-0043 pending) |
| adopt1 | PF-002 | P1 | bug | verify hash pinned at start, editing commands invalidated ready items (A2) | accepted | D131, WO-0042 |
| adopt1 | PF-003 | P1 | bug | `work_new` accepted an unknown type; 7 items cancelled (A3) | accepted | D132, WO-0042 |
| adopt1 | PF-004 | P1 | ux | spawn-roles put more than 4 panes in one window; raw tmux moves desynced projmux (A4) | accepted | D133, WO-0042 |
| adopt1 | PF-005 | P1 | ux | no guidance on order; PM readied items before any lead existed (A5) | accepted | D135 / WO-0045 (onboarding stages) |
| adopt1 | PF-006 | P2 | gap | `.mesh/state.json` tracked but always dirty, no policy (A6) | accepted | D134, WO-0042 |
| adopt1 | PF-007 | P3 | ux | no git author in a fresh repo (A7) | accepted | D134, WO-0042 |
| adopt1 | PF-008 | P3 | ux | plugin installed after the PM pane started needed `/reload-plugins` (A8) | accepted | D135 / WO-0045 |
| adopt1 | PF-009 | P1 | ux | a closed PM pane recreated as a new agent lost pin and context; hint names re-pin only (A9) | accepted | D135 / WO-0045 (resume first) |
| adopt1 | PF-010 | P1 | gap | `mesh_status` lists leads by role without their agent uid; the PM searched old transcripts to find the ship uid (2026-11-25) | accepted | D137, WO-0045 |
| adopt1 | PF-011 | P2 | ux | dispatch takes three calls (`work_new`, `ready`, `work_dispatch`) and ids are sequential, so a draft was created just to keep numbers aligned (2026-11-25) | accepted | D138, WO-0047; today: any file name through `scope.work_order` |
| adopt1 | PF-012 | P2 | gap | a Ship commit order needs per-file `git hash-object` fingerprints computed and compared by hand; replies need the uid and `--reply-to` assembled by hand; leads asked Ship for commits directly (2026-11-25) | accepted | D139 and D140 text, WO-0047; D140 guard, WO-0048 |
| adopt1 | PF-013 | P1 | gap | no built-in enforcement of PM routing and commit requests; the project wrote its own guard (2026-11-25) | accepted | D140, WO-0048 (threat model adds same-account forgery and the SendMessage tool) |
| adopt1 | PF-014 | P2 | bug | the operator-event hook records subagent hand-backs and task notifications as operator events; cite-time proof still requires origin human (2026-11-26) | accepted | WO-0049 F5 (PM read `mesh/cli.py` `operator_prompt`) |
| adopt1 | PF-015 | P1 | ux | `pane_command` never succeeded: a typed slash prompt and mid-turn prompts are `event_unproven`, a P-id after another prompt is `event_not_bound` (2026-11-26) | accepted | WO-0049 F1-F3 (PM checked transcript shapes and `mesh/layers.py` `_prompt_text`) |
| adopt1 | PF-016 | P1 | bug | `pane_command` reads Claude Code's dimmed prompt suggestion as a dirty input box (`pane_not_idle`) (2026-11-26) | accepted | WO-0049 F4 (`mesh/tmux.py` capture without -e) |

Migrated 2026-11-25 from the pilot's own file (PF-001..006) and from `docs/reports/2026-11-20-adoption-feedback.md` (A1-A9 → adopt1 PF-001..009). adopt1 PF-010..012 come from that project's PM reply to its operator on 2026-11-25 (read on operator request).
