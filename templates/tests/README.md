# Tests = the feedback loop

GREEN must be test-proven; live evidence for render/write/money/auth.

## Conventions
- Write the test alongside the function (not after).
- Bug fix → a regression test named `*.regression-NN.<ext>` with an attribution comment:
    `// Regression: <ISSUE-ID> — what broke / found by <who> on <date> / report: <path>`
- New conditional (if/switch) → test both branches. New error path → a test that triggers it.
- Coverage goal: high (100% is the target; tests are what make fast change safe).
- Never commit code that makes existing tests fail.
- Evidence-first: a task is DONE only with captured runner output / exit code; for live requirements attach a real capture, not a mock pass.
- **Audit gate (release blocker):** the project invariant/audit script (`scripts/audit.sh`) must **exit 0** before ship — the consistency/integrity oracle unit tests don't cover (e.g. a cross-table reconciliation that re-counts the whole chain). Completion is STAMPED by this script, not self-declared; FAIL blocks ship even with approval.  # why: full-chain truth beyond green units.
- Money oracle (if money is in scope): verify reverse/floor with a FULL integer oracle, not float spot-checks.  # why: float division reintroduces ±1 base-unit error at round multiples.
- FAIL triage: classify every red as product defect / test defect (flaky, wrong expectation) / environment before acting. Never mask with retries.
- Env gotchas live in docs/conventions (e.g. test concurrency / shared global state in e2e) — <Part B>.

## Runner
<Part B: vitest / pytest / ... + the exact command, e.g. `npm test` or `pytest -q`>
