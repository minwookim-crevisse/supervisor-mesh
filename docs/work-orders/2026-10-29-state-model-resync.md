# Work orders — formal model re-sync (D65-D76) and init follow-ups

> Paths: `<repo>` = this repo. Absolute paths travel only in dispatch messages (DEC-0008).
> Common header (operating model, discipline, constraints, report format): same as `docs/work-orders/2026-10-21-verification-skills.md`, EXCEPT write access as stated per order.
> Operator decisions (2026-10-29): all twelve WO-0016 findings decided as D65-D76 (`docs/designs/pm-led-onboarding.md`, "Formal verification findings"); applied to `docs/designs/work-item-state-model.md`.
> Durable evidence goes under `~/.local/state/supervisor-mesh-qa/<WO>/` (the session scratchpad does not survive a reboot).

## WO-0020 — Re-sync the TLA+ model to D65-D76 (role: qa) · depends: none
**Read set:** `docs/designs/work-item-state-model.md` (current revision; sections 3, 4, 5, 6, 8, 9, 10, 12), `docs/designs/pm-led-onboarding.md` ("Formal verification findings" table), `docs/reports/WO-0016-qa.md`, `tests/formal/README.md`, `.claude/skills/verify-work-item-state/SKILL.md`.
**Write access:** `tests/formal/` (spec, cfgs, `variants.py`, README), `scripts/formal-check.sh`, `.claude/skills/verify-work-item-state/SKILL.md` (sync record), your report `docs/reports/WO-0020-qa.md`. Nothing else.
**Must:**
- Model the new rows and rules by name: `reject --class` (D65), child cascade cancel (D66), `former_children[]` unlink (D67, the O3-8 constant goes away), environment +1 per passing unblock and refusal writes nothing (D68, the O3-9 constant goes away), flag cleared by a passing verify (D69), (mesh) follow-up row (D70), dependency_cancelled (D71), (mesh) parent verify rows (D72), override excluded from blocked(limit) (D73), one mesh `seq` (D74), append-only `failures[]` (D75), attempt-field CAS (D76).
- Add invariants 13-15 of section 10.
- Every former FINDING / STRUCTURAL / READING / NOTE of WO-0016 must now hold (PASS), except where the decided design itself says otherwise; any remaining violation is reported as a finding with numbered steps, not fixed in the design.
- Keep one broken variant per decision (the old behaviour) that FAILs, so each decision is protected.
- Move the skill's sync record to the commit that carries this design revision.
**DoD (captured):** `make formal` total line with 0 UNEXPECTED and 0 FINDING (or each remaining finding listed); variants all as expected; `make check` exit 0; `scripts/scrub.sh` 0 hits; one fresh worker follows the skill on a scratch break of a D65-D76 rule and catches it.

## WO-0021 — init step 6 privacy fix and preview/apply rule (role: backend) · depends: none (parallel with WO-0020, disjoint files)
**Read set:** `skills/init/SKILL.md`, `docs/reports/WO-0017-qa.md` (follow-ups 2 and 3), `templates/` that init copies, `tests/` for init.
**Write access:** `skills/init/SKILL.md`, init helper scripts it calls (if any), the matching tests under `tests/`, your report `docs/reports/WO-0021-backend.md`. Nothing else.
**Must:**
- Step 6 must not read the whole user plugin registry into the session. Check only the recommended companion plugin names (by name, presence only), and never print other installed plugins, paths or settings.
- State in the skill whether preview (dry run) and apply may be chained in one command. Decision: they must be separate steps; the skill says so plainly (the verifier tolerates the chained form on re-runs only).
- Unit test for the registry check: a fixture registry with unrelated plugins; the output names only the companions' presence.
**DoD (captured):** test red before / green after; `make check` exit 0; `scripts/scrub.sh` 0 hits; `claude plugin validate . --strict` exit 0.

## Constraints (both)
Read-only on `docs/decisions/`, `docs/tracker.md`, `docs/work-orders/`, `AGENTS.md`, design docs (report gaps instead). No commits, no push (Ship only, after operator OK). No worktree-destroying git commands (`reset --hard`, `checkout .`, `restore .`, `clean -fd`). No secrets, `.env` contents or tokens anywhere. Repo files English only, no absolute home paths (scrub). Self-report is not evidence: every DoD item is a captured command output with exit code.
