# Work orders — verification skills (formal + app-driving)

> Paths: `<repo>` = this repo; `<pstack>` = the installed pstack plugin folder (`claude plugin list` shows it; skills under `<pstack>/skills/`); `<scratchpad>` = your session scratchpad. Absolute paths travel only in dispatch messages (DEC-0008).
> Common header (operating model, discipline, constraints, report format): same as `docs/work-orders/2026-10-14-slack-review.md`, EXCEPT write access as stated per order.
> Operator decisions (2026-10-21): formal target = the work-item state model, tool = TLA+ (B); both skills dispatched to the QA lead; method = scope check with `improve-codebase-architecture`, parallel multi-model candidates with `/arena`, proof with TDD, adversarial review with `/interrogate` by a different model.

## Method (both orders, in this order)

1. **Scope** — run `mattpocock-skills:improve-codebase-architecture` (Skill tool) on the read set to confirm what is in scope, the seams, and what is out of scope. Write the scope list into your report before building.
2. **Build with /arena** — follow `<pstack>/skills/arena/SKILL.md` (Read it; the Skill tool refuses pstack skills; Claude Code adapter: `<pstack>/docs/harness/claude-code.md`). Candidates come from `~/.claude/rules/pstack-models.md` (`arena runners`, `arena cross-judge pool`). Keep candidates in `<scratchpad>` or worktrees; only the grafted winner lands in `<repo>`.
3. **Prove with TDD** — `mattpocock-skills:tdd`: each property/journey is first shown FAILING on a deliberately broken variant (scratch copy only), then PASSING on the real target. Capture both outputs.
4. **Adversarial review with /interrogate** — follow `<pstack>/skills/interrogate/SKILL.md`. The configured panel is Claude-only, so add one Codex review as the different model (read-only `codex exec`, same prompt). Fix accepted findings; list rejected ones with reasons.

## WO-0016 — Formal verification of the work-item state model, TLA+ (role: qa) · depends: none
**Read set:** `docs/designs/work-item-state-model.md` (target: sections 1-10, the 12 invariants), `docs/designs/pm-led-onboarding.md` (E2 pointer + round-3 review records D52-D64), `<pstack>/skills/create-formal-verification-skill/SKILL.md` (follow it).
**Write access:** NEW `tests/formal/` (TLA+ spec `.tla`, TLC config `.cfg`, a README), NEW `scripts/formal-check.sh` (one command, pinned tool version, exit code = result), NEW `.claude/skills/verify-work-item-state/SKILL.md`, a `formal` target in `Makefile` (NOT inside `make check`), your report `docs/reports/WO-0016-qa.md`. Nothing else.
**Tooling:** this machine has no Java. Install a user-local JRE and `tla2tools.jar` under your home (no sudo, no system packages), pin versions and record sha256 in the script. If that is impossible, stop and report the exact operator command instead.
**Must:**
- Model states, blocked reasons, verbs x callers, counters, failing-before binding, replies (fresh/consumed), split parent/children, dependencies, as one TLA+ spec whose names come from the state model. Each action points to its table row.
- Check every invariant that is expressible (section 10); say which ones are not and why. Bounds (items, attempts, replies) stated in the README and the report.
- Open items O3-8 (re-split after a cancelled child) and O3-9 (environment counter event) are UNDECIDED: model both alternatives as constants, report the result of each; do not pick one.
- Findings stay findings: if the design breaks a property, report the counterexample as numbered steps in the model's words. Do NOT change the design docs.
- The generated skill records covered sections + the design commit it was synced at, and how to tell a model bug from a design bug.
**DoD (captured):** `scripts/formal-check.sh` output with states explored and every property PASS (or reported findings); one broken variant per property showing FAIL + counterexample; the skill followed once by a fresh worker on a scratch break and catching it; `make check` exit 0; `scripts/scrub.sh` 0 hits; interrogate + Codex review outputs with dispositions.

## WO-0017 — App-driving verification skill for the plugin (role: qa) · depends: none (parallel with WO-0016, disjoint files)
**Read set:** `README.md` (install flow), `skills/*/SKILL.md`, `docs/reports/2026-10-14-live-qa.md` (past live QA), `<pstack>/skills/create-verification-skill/SKILL.md` + its `references/feature-map-example/` (follow it).
**Write access:** NEW `.claude/skills/verify-supervisor-mesh/` (SKILL.md, `features/README.md`, one file per feature, executable helpers), your report `docs/reports/WO-0017-qa.md`. Nothing else.
**Surface:** the real user path = a Claude pane created by projmux in a projmux-registered scratch project, then `/plugin marketplace add <repo>`, `/plugin install supervisor-mesh@...` at PROJECT scope, `/supervisor-mesh:init`, re-run (idempotent).
**Must:**
- Launch / Doctor / Drive / Evidence / Cleanup / Helpers sections with real commands from this repo and projmux; no placeholders.
- Isolation: scratch project under `<scratchpad>`, project-scope install only; never change user-scope plugins or settings; cleanup deletes only the agents (by UID) and registrations it created; never kill by name.
- Trust prompt: never answer it for the operator; the skill stops and asks the PM/operator.
- Feature map: init (fresh + re-run), spawn-roles, dispatch, gc, add-agent; slack-setup with the mock server only (no live token).
- Evidence (file hashes, transcripts, captured output) survives cleanup in a location the skill names.
**DoD (captured):** the skill followed once end to end by a fresh worker: launch, doctor, drive `init` (fresh + re-run with unchanged hashes), evidence, cleanup, evidence still present; `make check` exit 0; scrub 0 hits; interrogate + Codex review outputs with dispositions.

## Constraints (both)
Read-only on `docs/decisions/`, `docs/tracker.md`, `docs/work-orders/`, `AGENTS.md`, design docs (report gaps instead). No commits, no push (Ship only, after operator OK). No worktree-destroying git commands (`reset --hard`, `checkout .`, `restore .`, `clean -fd`). No secrets, `.env` contents or tokens anywhere. Repo files English only, no absolute home paths (scrub). Self-report is not evidence: every DoD item is a captured command output with exit code.
