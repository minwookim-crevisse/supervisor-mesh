---
name: verify-work-item-state
description: Model-check the mesh work-item state machine (docs/designs/work-item-state-model.md, later mesh/state.py) with TLA+/TLC by running scripts/formal-check.sh. Use when a change touches work-item states or blocked reasons, the section-4 transition table (verbs, callers, guards), failure classes and routes, the retry/environment counters, replies/ack/operator events, split parents and children, dependencies, the revalidate flag, evidence binding or the compare-and-set write; and before trusting such a change.
---

# Verify the work-item state model

The model is `tests/formal/WorkItemState.tla`, a second adapter of the section-4
transition table beside the future `mesh/state.py`. Every action carries a `\* row:`
comment copied from the table row it implements, and the constant `Table` holds the
rows as data. There is no `mesh/` code yet, so the design document is the target.
What the model covers, which property checks what, the bounds and the known findings:
`tests/formal/README.md` (the single record; this skill holds the procedure and the
sync record).

## 1. Does my change touch the verified design?

The design file decides by its hash, not by git: `scripts/formal-check.sh` compares the
sha256 of `docs/designs/work-item-state-model.md` with `DESIGN_SHA256` (the sync point,
section 5) and stops with `STALE` and exit code 4 when they differ. That also catches an
edit to the uncommitted design file, which `git log` cannot show. A STALE run means:
read the changed sections 1-10 and 12, update the model (section 4 below), then move
the sync point.

Not hashed, so check by reading:
- A decision in `docs/designs/pm-led-onboarding.md` (D40-D64) that changes E2.
- Once it exists: the transition table, guards, counters, reply handling or the state
  write in `mesh/state.py` (compare its table row by row with `Table`).
- Once these files are committed, `git log --oneline <sync commit>..HEAD --
  docs/designs/work-item-state-model.md docs/designs/pm-led-onboarding.md mesh/` lists
  the commits to look at.

## 2. Run it

```
scripts/formal-check.sh                    # or: make formal; on a loaded machine add --jobs 1
scripts/formal-check.sh --only '^(core|replies)$'   # a subset while you iterate
scripts/formal-check.sh --variants         # every broken/fixed variant, one TLC job at a time
FORMAL_DIR=<scratch copy of tests/formal> scripts/formal-check.sh --only '<runs>'
```

`FORMAL_DIR` checks a scratch copy of `tests/formal` instead of the real one: use it to
try a deliberate break or a candidate model change without touching the repo (the design
hash check still reads the repo's design file). `--keep` keeps the whole work dir;
`--allow-stale` runs against a changed design while you update the model; `--help`
lists every option and exit code.

The script verifies the pinned tools (Temurin JRE 21.0.12.1+1, tla2tools 1.8.0: sha256
of the jar, of the JRE archive at install time, and of a manifest of every extracted JRE
file at every run, plus `java -version`). If they are missing it installs them under
`~/.local/share/tlaplus` (no sudo); if that fails it prints the exact commands. On a
hash mismatch it names what to remove (the jar file, or the whole JRE directory) and
reinstalls it on the next run.

Nothing is written into the repo or the system temp dir: each invocation works in a new
directory under `${XDG_CACHE_HOME:-~/.cache}/formal-check/`, removed at exit. The dirs
of UNEXPECTED runs (and NOT AS EXPECTED variants) are kept there with `tlc.log`,
`trace.json` and `run.cfg`, and their path is printed below the run; delete the printed
work dir when you are done with it. Ctrl-C or TERM stops every TLC run before cleanup.

Read the table it prints, one line per run (name, scenario, states generated and
distinct, seconds, verdict):

| verdict | meaning |
|---|---|
| `PASS (...)` | every property listed in that scenario cfg holds within the bounds |
| `REACHED inv 3 / inv 2 / row / O3-8 ...` | the probe was violated as required: the state is reachable / the exit is taken / the table row fires / the alternative behaviour happens |
| `FINDING <id> (... known)` | a known design finding reproduced; its counterexample is printed below the line |
| `READING G2a (...)` | a known finding that holds only under one reading of an ambiguous row; the other reading's run passes |
| `STRUCTURAL G5 / G8 (... known)` | a known finding read off the table itself (no row for the step); TLC only replays it |
| `NOTE N1 (... known)` | a known consequence that breaks no section-10 invariant, printed the same way |
| `UNEXPECTED: got X, expected Y` | another result: the trace is printed and the logs are kept |
| `UNEXPECTED: ... by another counterexample: last step 'A', expected 'B'` | the expected property is violated, but by a different path than the known one |

A violation only counts with the right counterexample: each finding, witness and row run
in `EXPECT` names the last step its trace must end with (`caller verb item k: from ->
to`, read off the trace's states; a refusal adds its reason).

Exit code: `0` all runs as expected (known findings do not fail the run), `1` any
UNEXPECTED, `2` usage error or tools missing / hash mismatch, `3` with `--strict` when
known findings remain, `4` STALE design. The expected results live in the `EXPECT`
table at the top of the script.

`--variants` prints, per variant in `tests/formal/variants.py`: name, kind, expected
TLC outcome (FAIL for a broken guard; PASS for a removed witness, a hypothetical fix or
the other reading of a row; fixes and readings keep every property of their scenario),
actual outcome, and the counterexample. It exits 1 if any variant does not behave as
expected.

## 3. UNEXPECTED: model bug or design bug?

Read the printed steps (`N. caller verb item k: from -> to (why)`), then:
1. For each step, open the action it names in the spec and its `\* row:` comment, and
   the same row in section 4 of the design (and section 5 for routes, 6 for counters).
2. A step the design row does not allow, or a step the design allows but the model
   refuses: **model bug**. Fix the action (or `Table`) to match the row. Never change
   the property to make it pass.
3. Every step matches the design text and a section-10 invariant (or a gap probe) is
   broken: **design bug**. Keep the property, add the run to `EXPECT` as a `finding`
   with a new id and its last step, and report the counterexample to the orchestrator
   in your report file. Do not edit the design documents.
4. The trace ends at a bound (`MaxAttempts`, `MaxContractVersion`, `MaxClock`,
   `MaxReplies`, see the scenario cfg): rerun that scenario in a scratch copy
   (`FORMAL_DIR`) with the bound raised before you call it either.
5. A `NoDeadItem` violation in a scenario whose verb scope (`ItemVerbs`, `ChildVerbs`)
   leaves out the verb the table lists as the way out is a **scope bug** in the cfg,
   not a design bug: add the verb or do not check `NoDeadItem` there.
6. `got error(rc=...)` is not a verdict: a parse or evaluation error in the spec is a
   model bug; rc 2 is tooling.

A probe that is no longer violated (`got pass, expected violate:Unreached_X`, `NoExit_X`
or `NoRow_X`) means a state became unreachable, an exit can no longer be taken or a row
can no longer fire: that is invariant 2 or 3 failing (or a dead row), so treat it like a
broken invariant.

**Another counterexample** (the expected property, another last step): the property
now fails along a path that is not the known finding. TLC searches breadth-first and
reports the shortest violation, so a new, shorter path hides the known one. Triage the
new path as above; the known finding is not shown to be gone.

**Masked known findings**: one break often makes several runs UNEXPECTED. A change that
lets an earlier step break a property that many scenarios check (for example a `close`
that ignores the revalidate flag breaks `CloseGates` in core, replies and o39-refused)
also turns finding runs of those scenarios UNEXPECTED (core-g4 then reports `CloseGates`
instead of `FlagClearedByReverify`), because TLC stops at the first violated property.
Those findings are masked, not fixed: resolve the first cause, rerun, and they come back.

**The intended change breaks an invariant**: you changed the model to follow an
intended design change and a section-10 invariant now fails along steps that all match
the new design text. That is a design finding of the new design, not a model bug: keep
the property, report the counterexample to the orchestrator, and add the `finding` row
(with its last step) only once the orchestrator accepts it as known. Do not adapt the
property or the bounds to make the change pass.

## 4. Update the model for an intended design change

1. Edit the action of each changed row; keep its `\* row:` comment equal to the new
   table text, and update `Table` if a verb, caller, source or target changed. A new
   row action also goes into `Next` and, unless it is cancel or override, `Progress`.
   A new row outcome gets a `NoRow_<row>` probe and a `row-*` run in `EXPECT`.
2. Changed or new invariant in section 10: change or add the property next to its
   number; do not weaken an existing one to pass.
3. Run `scripts/formal-check.sh --allow-stale` until it matches. Update `EXPECT` only
   for results you can explain, and give every violate row the last step of its
   counterexample (copy it from the UNEXPECTED line once you have read the trace).
4. Prove each new or changed property can fail: add a variant to
   `tests/formal/variants.py` (exact-text edits that break the guard the property
   depends on, the scenario, the property) and run
   `scripts/formal-check.sh --variants --only '<name>'`. A variant that survives means
   the property is too weak or the model too ideal: N2 survived while one strictly
   increasing clock stamped every time, and fails in RepliesClockTies, where the
   limit-entry time can tie or lag. Strengthen the property or widen the model; only if
   neither is possible, record it in the README as structurally implied.
5. Update the README (properties, findings, coverage, bounds) and the sync record below,
   and move `DESIGN_SHA256` in the script; then run without `--allow-stale`.

When the orchestrator decides O3-8 or O3-9, keep the chosen alternative's runs, delete
the other alternative's scenario cfg and `EXPECT` rows, and fold the result into the
main scenario. When the design says whether `override` is exempt from invariants 4-10,
update or drop the `tablewalk-f1` run accordingly. When it settles whether the split
consumes the failure record (G2a), keep the matching `SplitFailure` value and drop the
other scenario.

## 5. Sync record

- Repo HEAD at sync: `b656008`.
- Design file `docs/designs/work-item-state-model.md` sha256
  `f7235b6acb7cd57287efd48d785cbe4770143dca43a7af1711e56dfad8827c1b`
  (the file was uncommitted at sync time); the same value is `DESIGN_SHA256` in
  `scripts/formal-check.sh`.
- Covered: design sections 1 and 3-10 (invariant 12 as a run-time check only) and the
  open items of section 12; not covered: section 2 (the diagram). Per property, bound and
  finding: `tests/formal/README.md`.
- Move this record and `DESIGN_SHA256` forward only after you have re-read the changed
  design sections, confirmed (or made) the model describe the new version, and seen
  `scripts/formal-check.sh` exit 0. Then record the new commit and the new sha256 here.
