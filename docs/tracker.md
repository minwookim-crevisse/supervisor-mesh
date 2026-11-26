# Tracker

> Single owner: the Orchestrator (PM) writes this file. Every other session is read-only on it and reports via `docs/reports/`.
> No row = no dispatch.

| id | title | role | depends | status | report |
|---|---|---|---|---|---|
| WO-0001 | Security review — Slack bridge 0.1.2 draft | security | — | FAIL (2 🔴) | `docs/reports/WO-0001-security.md` |
| WO-0002 | Correctness review — Slack bridge 0.1.2 draft vs findings A–J | backend | — | FAIL (4 🔴) | `docs/reports/WO-0002-backend.md` |
| WO-0003 | Independent verification — tests, mock fidelity, live channel, claim accuracy | qa | — | FAIL (2 🔴) | `docs/reports/WO-0003-qa.md` |
| WO-0004 | Rework of accepted findings (`docs/work-orders/2026-10-14-slack-rework.md`) | backend | WO-0001..0003 | PASS (open item → WO-0005) | `docs/reports/WO-0004-backend.md` |
| WO-0007 | Report path hygiene (own report only) | security, backend, qa | — | closed (grep /home/ = 0) | — |
| WO-0005 | Re-review + QA test fidelity (Q-06, Q-07, regex unify S-09) + live E2E + `make check` | security, qa | WO-0004, WO-0007 | dispatched | `docs/reports/WO-0005-security.md`, `docs/reports/WO-0005-qa.md` |
| WO-0006 | Commit + push 0.1.2 | ship | WO-0005, WO-0008 | open (pending) | — |
| WO-0008 | README: projmux-first install order + actionable prerequisites incl. python3 ≥ 3.10 install/upgrade (operator requests) | backend | WO-0004 | dispatched | `docs/reports/WO-0008-backend.md` |
| WO-0009 | mesh CLI skeleton: sh shim, cli (all subcommands registered), identity, tracker grammar, manifest + role parser, preflight — design §1-3b; also fix R3-3, R3-8, R3-9, R3-17 | backend | batch 3 closed | open (queued) | — |
| WO-0010 | mesh init: scaffold under mesh (scaffold.py kept as wrapper), standard layout §E1b (templates/AGENTS.md layer map, part-b checklist, manifest), `.mesh/`, WO-0001, upgrade rules — §7; fix R3-10 | backend | WO-0009 | open (queued) | — |
| WO-0011 | mesh spawn + mesh status — §4, §6; fix R3-4 (status side), R3-6, R3-12, R3-14 | backend | WO-0009 (∥ WO-0010) | open (queued) | — |
| WO-0012 | /supervisor-mesh:pm skill, pm-orchestrator onboarding, mesh dispatch row-id rule, thin wrappers — §5, §8; fix R3-4, R3-5, R3-11; decide R3-13 | backend | WO-0010, WO-0011 | open (queued) | — |
| WO-0013 | Security review of PM bootstrap (trust, preflight bypass, unprompted writes incl. R3-5) | security | WO-0012 | open (queued) | — |
| WO-0014 | Live fresh-project E2E (all design Success Criteria incl. hand-started-shell FAIL) + integration gate; fix R3-16 | qa | WO-0012 | open (queued) | — |
| WO-0015 | Release 0.2.0: version, README Quickstart, commit + push after operator OK | ship | WO-0013, WO-0014 | open (queued) | — |
| WO-0016 | Formal verification of the work-item state model (TLA+), `docs/work-orders/2026-10-21-verification-skills.md` | qa | — | pass (PM gate 2026-10-26: make formal 82 runs 0 UNEXPECTED; 12 FINDING/2 READING/3 STRUCTURAL/1 NOTE await PM decision) | `docs/reports/WO-0016-qa.md` |
| WO-0017 | App-driving verification skill `verify-supervisor-mesh`, same work order | qa | — (∥ WO-0016) | pass with correction (live init-check rerun FAIL → verifier fixed, re-checked on saved live data; selftest + make check 0) | `docs/reports/WO-0017-qa.md` |
| WO-0018 | Local checkpoint commit (batch V work + state model + docs), NO push | ship | — | pass (335540f, local only) | — |
| WO-0019 | Commit batch V final (skill fixes, WO-0016/0017 reports, tracker) and PUSH main to origin (operator OK 2026-10-26) | ship | WO-0016, WO-0017 | pass (692c02a; commit by ship, push run by operator after worker push was permission-denied; origin/main = 692c02a) | — |
| WO-0020 | Re-sync TLA+ model to D65-D76, `docs/work-orders/2026-10-29-state-model-resync.md` | qa | — | pass (PM gate 2026-11-18: make formal 67 runs 0 FINDING 0 UNEXPECTED; variants 44/44; make check 0; D81 added) | `docs/reports/WO-0020-qa.md` |
| WO-0021 | init step 6 privacy fix + preview/apply rule, same work order | backend | — (∥ WO-0020) | pass (PM-checked 2026-10-29: 7 companion tests OK; helper prints presence only; ruff not installed, lint unproven) | `docs/reports/WO-0021-backend.md` |
| WO-0022 | mesh package + stdio MCP server (MVP 0.2.0), `docs/work-orders/2026-11-18-mcp-mvp.md` | backend | — (∥ WO-0020) | pass (PM gate 2026-11-18: make check 0, 531 tests; validate 0; stdio probe 11 tools; G-4 -> WO-0028) | `docs/reports/WO-0022-backend.md` |
| WO-0023 | Security review of the MCP tool surface, same work order | security | WO-0022 | pass (final verdict at b27c2c4: F1-F13 fixed, 116 tests OK; accepted risks D86 D88 D96 D99 D121) | `docs/reports/WO-0023-security.md` |
| WO-0024 | Live MCP verification in a new project, same work order | qa | WO-0022 (∥ WO-0023) | pass (runs 1+2 live on a1bb251 / 382cf02; step (f) PASS on e9ed309 after D125 fix; 9a formal re-pin: 68 runs 0 FINDING) | `docs/reports/WO-0024-qa.md` |
| WO-0025 | Release 0.2.0: commit batches W then M, push after operator OK | ship | WO-0020, WO-0022..0024, PM gate | pass (0.2.0 pushed 2026-11-19: 65af1b8..64ad970, fast-forward, operator-approved) | — |
| WO-0026 | Commit batch W (WO-0020, WO-0021, D65-D81 design, batch M spec + work orders) and push main (operator OK 2026-10-30) | ship | WO-0020, WO-0021 | pass (65af1b8 pushed by ship; origin/main = 65af1b8, PM-verified) | — |
| WO-0027 | Re-sync TLA+ model to D100 (config hash at ready) + D101 (PM self-review reject), `docs/work-orders/2026-10-29-state-model-resync.md` method | qa | — (∥ WO-0022) | pass (PM gate 2026-11-18: make formal 68 runs 0 FINDING 0 UNEXPECTED; variants 49/49; D100-D106; unit gate excludes 3 W5-touched modules, full make check re-run after W5) | `docs/reports/WO-0027-qa.md` |
| WO-0028 | D107: narrow the D96 deny rule to .mesh/state.json, events.jsonl, refusals.jsonl (config.toml stays editable; D100 pins it) | backend | WO-0029 | pass (PM-checked DENY_RULES 2026-11-18; 533 tests) | `docs/reports/WO-0022-backend.md` (addendum) |
| WO-0030 | Security fixes D108-D112 (WO-0023 F1-F7), `docs/designs/mcp-server-spec.md` | backend | WO-0028 | pass (PM gate 2026-11-18: make check 0, 591 tests; deviations accepted as D117) | `docs/reports/WO-0030-backend.md` | Tail (6 items) pass at PM gate (606 tests); D120 recorded; D121 (code never on disk) in progress.
| WO-0031 | Local checkpoint commit: WO-0028 + WO-0030 + QA WO-0024 skill build + specs/reports, NO push | ship | WO-0030 | pass (a1bb251 local, not pushed; PM-verified) | — |
| WO-0032 | Local checkpoint commit: WO-0030 tail 1+2 (D118-D123), QA skill run-1 fixes, specs, reports, NO push | ship | WO-0030 tail 2 | pass (382cf02 local, not pushed; PM-verified) | — |
| WO-0033 | Local checkpoint commit: WO-0030 tail 3 (D124), QA formal 9a re-pin, WO-0024 report, specs, NO push | ship | WO-0030 tail 3 | pass (b27c2c4 local, not pushed; PM-verified) | — |
| WO-0034 | D125 pane_command render wait (live step f finding) + PM channel-choice rule in agents/pm-orchestrator.md | backend | WO-0025 | pass (PM gate) | — |
| WO-0035 | Local commit of WO-0034 on top of the 0.2.0 release commit, NO push | ship | WO-0034 | pass (e9ed309 local; PM-verified) | — |
| WO-0036 | Local commit of the final WO-0024 report + tracker, NO push | ship | WO-0024 | pass (64ad970) | — |
| WO-0037 | README diagrams as hand-authored SVG, candidates rendered for operator choice (docs/work-orders/2026-11-19-readme-diagrams.md) | designer | — | pass (operator final: Mesh D, Lifecycle B, Approval A; README swapped to SVG) | — |
| WO-0038 | DEC-0016 worker = lead's in-process subagent: role-lead invariant block, methodology, AGENTS.md, templates/AGENTS.md, pm-orchestrator (PM doc edits; QA to verify) | qa | — | pass (final; R1 R3 test gaps -> 0.3.0 follow-ups; R2 R4 fixed by PM) | — |
| WO-0039 | D126 dispatch header: workers are in-process subagents (WO-0038 F1, root cause of pane workers) | backend | WO-0038 | pass (PM gate: make check 0, 674 tests) | — |
| WO-0040 | Release 0.2.1: README rewrite + SVG diagrams, DEC-0016 worker-as-subagent (docs, agents, dispatch header D126); commit + push after operator OK | ship | WO-0037, WO-0038, WO-0039 | pass (0.2.1 pushed 2026-11-19 by the operator: 64ad970..42779ac) | — |
| WO-0041 | guided onboarding: init as a staged, resumable guide with hints to the first `/supervisor-mesh:run` (D130, design `docs/designs/guided-onboarding.md`) | backend+qa | 0.3.0 | superseded by WO-0045 (D135) |
| WO-0042 | adoption fixes A1-A4, A6-A7 from `docs/reports/2026-11-20-adoption-feedback.md` (verify entrypoint seeded by init, `work_new` type check, spawn-roles ≤4 panes/window, state.json policy) | backend | 0.3.0 | pass 2026-11-26 (lead PASS, PM make check rc=0, live WO-0043 PASS); awaiting Ship commit |
| WO-0043 | live check of WO-0042 (D131-D134) on a throwaway project, verify-supervisor-mesh | qa | 0.3.0 | pass 2026-11-26: D131-D134 PASS live (`docs/reports/WO-0043-qa.md`); cleanup excusal (skill-creator auto-update, F-f4) by PM, kept apart from verdicts |
| WO-0044 | project view step 1: pure move of mesh_status into `mesh/view.py` + `mesh/hints.py`, one projmux fetch (D135) | backend+qa | 0.3.0 | designed 2026-11-25, depends WO-0043 + commit |
| WO-0045 | project view step 2: onboarding stages, PM session, next hints, handoff, lead uids (D135 absorbs D130, D127-D129, A9; plus D137) | backend+qa | 0.3.0 | designed 2026-11-25, depends WO-0044 |
| WO-0046 | finding intake build: init template, `mesh_feedback` (PM-only, contained, leak refusal, issue draft), repo-own `scripts/fleet.py` over `~/.local/state/supervisor-mesh/fleet.toml` (D136, DEC-0018) | backend+qa | 0.3.0 | designed 2026-11-25; protocol runs by hand first (ledger `docs/reports/fleet-findings.md`) |
| WO-0047 | PM coordination: `work_issue`, `commit_request` / `commit_check`, lead-request text (D138-D140, `docs/designs/pm-coordination.md`) | backend+qa | 0.3.0 | designed 2026-11-25, depends WO-0045; order not written yet |
| WO-0048 | lead-request guard: shipped PreToolUse hook G1-G3 (D140, `docs/designs/pm-coordination.md`); threat model and probe corpus before the build | security+backend+qa | 0.3.0 | designed 2026-11-25 (operator: include the hook), depends WO-0047; order not written yet |
| WO-0049 | pane_command approvals work: slash-prompt form, queued human prompt, P-id until expiry, dim suggestion = empty, hook records human candidates only (adopt1 PF-014..016; draft F1-F5; S1/S2 open) | backend+qa | 0.3.0 (hotfix candidate) | proposed 2026-11-26, under arena review; order not written yet |
| WO-0029 | Local checkpoint commit of batch M so far (WO-0022, WO-0027, specs, DEC-0015), NO push | ship | WO-0022, WO-0027 | pass (0501f50 local, not pushed; PM-verified) | — |

Parallel batch 1 = WO-0001 / WO-0002 / WO-0003 (read-only reviews, disjoint report files).

Batch 2 = WO-0004 (backend, code) ∥ WO-0007 (each lead, own report). Disjoint files.
Batch 3 = WO-0005-S (security, read-only) ∥ WO-0005-Q (qa: tests/test_slack.py, scripts/scrub.sh) ∥ WO-0008 (backend: README). Disjoint files.
Batch V = WO-0016 ∥ WO-0017 (qa lead, disjoint files; integration gate = PM runs `make check` + `make formal`). Gate 2026-10-26: make check 0, make formal 0, smv selftest 0, scrub 0. Durable QA evidence: `~/.local/state/supervisor-mesh-qa/` (scratchpad is wiped on reboot).
Design findings G1-G8, F1, F2, O3-8, O3-9, inv 10, N1 decided 2026-10-29 (D65-D76). Batch W = WO-0020 ∥ WO-0021 (integration gate = PM runs `make check` + `make formal`).
Batch M (MCP MVP 0.2.0, spec `docs/designs/mcp-server-spec.md`, D77-D80): WO-0020 ∥ WO-0022 → WO-0023 ∥ WO-0024 → PM gate (make check + make formal + live evidence) → WO-0025.
Pilot (operator 2026-11-18): project `mesh-pilot` = Tiny Ledger (TS split-expense library + CLI). First 1-2 items run on 0.1.2 as the baseline (manual tracker); the rest on 0.2.0 (MCP). Compare rework count, PASS reversals and operator interventions; result feeds WO-0024.
New-project adoption (operator 2026-11-18): wait for 0.2.0 (MCP) and adopt from it; no new project on 0.1.2. Pilot phase 2 also starts on 0.2.0.
0.3.0 follow-ups from WO-0038: R1 run-skill test must assert the explicit-order condition; R3 test for the >4096-byte InvalidArgs refusal.
Follow-ups (not dispatched): smv.py package split, FakeSlack helper, shared role lookup, formal runner/probes module, 5 skill doc notes (qa); live runs of the other five features and `marketplace remove` (qa).
Design for WO-0009..0015: `docs/designs/pm-led-onboarding.md` (APPROVED 2026-10-14).
