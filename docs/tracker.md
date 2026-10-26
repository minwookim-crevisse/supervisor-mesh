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
| WO-0016 | Formal verification of the work-item state model (TLA+), `docs/work-orders/2026-10-21-verification-skills.md` | qa | — | dispatched | `docs/reports/WO-0016-qa.md` |
| WO-0017 | App-driving verification skill `verify-supervisor-mesh`, same work order | qa | — (∥ WO-0016) | dispatched | `docs/reports/WO-0017-qa.md` |
| WO-0018 | Local checkpoint commit (batch V work + state model + docs), NO push | ship | — | dispatched | — |

Parallel batch 1 = WO-0001 / WO-0002 / WO-0003 (read-only reviews, disjoint report files).

Batch 2 = WO-0004 (backend, code) ∥ WO-0007 (each lead, own report). Disjoint files.
Batch 3 = WO-0005-S (security, read-only) ∥ WO-0005-Q (qa: tests/test_slack.py, scripts/scrub.sh) ∥ WO-0008 (backend: README). Disjoint files.
Batch V = WO-0016 ∥ WO-0017 (qa lead, disjoint files; integration gate = PM runs `make check` + `make formal`).
Design for WO-0009..0015: `docs/designs/pm-led-onboarding.md` (APPROVED 2026-10-14).
