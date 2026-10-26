# Formal model: work-item state machine (TLA+)

`WorkItemState.tla` models the mesh work-item state machine of
`docs/designs/work-item-state-model.md` (sections 1-10 and the open items of section
12; why each row exists: review records D52-D64 in `docs/designs/pm-led-onboarding.md`).
There is no `mesh/` code yet, so the design document is the target: the spec is a
second adapter of the section-4 transition table, beside the future `mesh/state.py`.
The constant `Table` holds the section-4 rows as data, one entry per row and nothing
else. Every action that implements a row carries a `\* row:` comment copied from it.
Two steps the design describes outside the table have actions but no row: the
follow-up draft for a closed item and the split parent's own verification; they are the
STRUCTURAL findings G5 and G8. Names are the design's (states, blocked reasons, verbs,
callers, item record fields, counters).

This file is the single record of what the model covers, which property checks what,
the bounds and the known findings. How to run the check, read its output, tell a model
bug from a design bug and update the model: `.claude/skills/verify-work-item-state/SKILL.md`.

## Files

| file | content |
|---|---|
| `WorkItemState.tla` | the spec: item record, every section-4 row, refusals, properties, row probes, gap probes |
| `Core.cfg` | one bugfix item, every single-item path except replies; O3-9 as the table reads it |
| `CoreO39Refused.cfg` | Core with O3-9 read the failure-registry way (a refused precheck counts) |
| `Replies.cfg` | Slack replies + operator events from the PM terminal; ack/close gates; limit resume |
| `RepliesClockTies.cfg` | Replies with the limit-entry time free to tie with the latest reply/event time or lag one tick behind it (two clocks, D64) |
| `Split.cfg` | parent split into two children, as written: the split keeps the conflict/too_large record, a cancelled child stays linked |
| `SplitConsumed.cfg` | Split read the other way for G2a: the split consumes the failure record |
| `SplitStayLinked.cfg` | O3-8 alternative stay_linked (as written), with room for a re-split |
| `SplitUnlink.cfg` | O3-8 alternative unlink, with room for a re-split |
| `Deps.cfg` | three items with depends: unknown ids, self, cycles, satisfied by verified/closed |
| `Cas.cfg` | one bugfix item, two lead panes run verify as read-check / unlocked commands / compare-and-set write, one config edit |
| `TableWalk.cfg` | operator override drives one item through every state; every unlisted verb x caller must be refusable there |
| `FollowUp.cfg` | follow-up drafts for a closed item, from a thread reply (slack watcher) and a revalidate flag (pm) |
| `FollowUpFlag.cfg` | FollowUp with the pm follow-up only (shows that half of G5) |
| `variants.py` | the variant definitions (edits + one target property + one scenario each), their runner, the TLC trace printer and the last-step signature |

A scenario cfg holds constants and the properties that must hold in it; it is the
authority on which property is checked where (the table below summarises it). The
script's `EXPECT` table adds one extra property per finding, witness or open-item run
and names the last step its counterexample must end with.

## Properties

"Split*" = Split, SplitConsumed, SplitStayLinked, SplitUnlink.

| design | property | kind | checked in | expected |
|---|---|---|---|---|
| inv 1 | `TableOnly`: every change of an item record is a section-4 row, read off the state diff (the step's verb and caller with the item's actual state before and after, not the step's labels); the only changes of other items are the declared side effects (split's child drafts; the follow-up draft once a row lists it) | action | Core, CoreO39Refused, Replies, RepliesClockTies, Deps, Cas, TableWalk | holds; violated in Split (G8), FollowUp and FollowUpFlag (G5): STRUCTURAL |
| inv 1 | `TableOnlyExceptStructural`: `TableOnly` for every step except the G5 and G8 steps | action | Split*, FollowUp, FollowUpFlag | holds |
| inv 1 | `UnlistedRefused`: in every reachable state, every verb x caller x state combination the table does not list has a step in `Next` that refuses it, records it and changes nothing | state (ENABLED) | TableWalk | holds |
| inv 1 | `RefusedUnchanged`: the refusal of an unlisted combination is recorded and leaves the record (and its version) unchanged | action | TableWalk | holds |
| model | `GuardRefusedUnchanged`: a guard refusal of a listed combination is recorded and leaves the record unchanged, except the refused unblock that O3-9 precheck_refused counts (not a section-10 invariant; G3) | action | every scenario but TableWalk | holds |
| inv 2 | `TableExits`: table walk, every non-terminal state has a row out that is neither cancel nor override | constant | Core | holds |
| inv 2 | `NoDeadItem`: in every reachable state each non-terminal item has an enabled step (`Progress`) that changes its state and is neither cancel nor override, or waits (`Waiting`: blocked(limit), an active child, a verify run in flight, no live lead, no free pane, an uncancelled dependency) | state (ENABLED) | Core, CoreO39Refused, Replies, RepliesClockTies, Split | holds; violated in SplitConsumed: READING G2a |
| inv 2 | `NoExit_<state>` (10 probes): the exit is never taken | action | Replies, Split | violated = REACHED |
| inv 3 | `TableReach`: table walk, every state is reachable from draft through rows other than override | constant | Core | holds |
| inv 3 | `Unreached_<state>` (12 probes): the state is never reached | state | Replies, Split | violated = REACHED |
| inv 4 | `LimitOnlyByMesh`: only the mesh counters enter blocked(limit) or record class limit | action | Core, CoreO39Refused, Replies, RepliesClockTies, Split* | holds; violated with the override row (TableWalk): FINDING F1 |
| inv 5 | `BoundedStarts`: starts between counter resets <= 1 + RetryLimit + EnvLimit | state | Core, Replies, RepliesClockTies, Split*, Deps | holds; violated under O3-9 precheck_refused: FINDING |
| inv 6 | `VerifiedBound`: verified binds the submitted commit, contract version, attempt config hash, failing_before | state | every scenario but TableWalk | holds |
| inv 7 | `CloseGates`: close never with unacknowledged replies or a revalidate flag | action | every scenario but Cas, TableWalk | holds |
| inv 8 | `StartNeedsDepends`, `DependsWellFormed`: start needs verified/closed dependencies; no unknown, self or cyclic depends past ready | action, state | Core, CoreO39Refused, Deps | holds |
| inv 9 | `ParentOwnVerification`: a closed split parent closed through its own verification | state | Split* | holds |
| inv 10 | `LimitReplySingleUse`: the reply that unlocks blocked(limit) is one attached reply, and no reply unlocks twice | action | Replies, RepliesClockTies | holds (load-bearing in RepliesClockTies: N2) |
| inv 10 | `LimitReplyFresh`: the reply that unlocks blocked(limit) arrived after the blocked(limit) entry (its tick against the entry's tick, not against the recorded limit_at) | action | Replies; RepliesClockTies (finding run) | holds with one clock; violated under ClockTies: FINDING inv 10 (G7) |
| inv 11 | `StaleNeverWritten`: no verify write lands after another write since its read, and no submit/fail write for an older attempt | action | Cas | holds |
| inv 12 | not expressible: the shared working tree is outside the model | - | - | a run-time check of the implementation, see [Invariant 12](#invariant-12-a-run-time-check) |
| rows | `NoRow_<row>` (27 probes): that row outcome (verb, caller, from, to) never fires | action | the row-* runs | violated = REACHED; a probe that holds is a dead row |
| G1 | `RouteByClass`: a failure record sends the item where section 5 routes its class | action | Replies (finding run) | violated: FINDING |
| G2a | `RouteDefined`: every rework has a class with a way back | state | Split, SplitStayLinked, SplitUnlink; SplitConsumed (finding run) | holds; violated when the split consumes the record: READING |
| G2a | `ClasslessReworkStuck`: a classless rework leaves only by cancel | action | SplitConsumed | holds (confirms G2a under that reading) |
| G2b | `NoOrphanChildren`: no active child while its parent is back in rework | state | Split, SplitStayLinked, SplitUnlink (finding runs) | violated in both O3-8 alternatives: FINDING |
| G3 | `GuardRefusalWritesNothing`: no guard refusal writes the record | action | CoreO39Refused (finding run) | violated: FINDING |
| G4 | `FlagClearedByReverify`: a flag on a verified item belongs to its current verification | state | Core (finding run) | violated: FINDING |
| G6 | `NoWaitOnCancelled`: a ready item never depends on a cancelled item | state | Deps (finding run) | violated: FINDING |
| F2 | `CancelledParentNoActiveChild`: a cancelled split parent leaves no active child | state | Split (finding run) | violated: FINDING |
| O3-8 | `NoResplitAfterCancelledChild`: the parent is never split again after a cancelled child | state | SplitStayLinked, SplitUnlink | violated (re-split happens) in both: REACHED |
| O3-8 | `NoMergedCloseAfterCancelledChild`: the parent never closes through its children after a cancelled child | state | SplitStayLinked, SplitUnlink | holds with stay_linked; violated (re-split completes) with unlink: REACHED |
| O3-8 | `ResplitCanComplete`: a split leaves no already-cancelled child linked | action | SplitUnlink; SplitStayLinked (finding run) | holds with unlink; violated with stay_linked: FINDING |
| N1 | `ReplyNeverDiscardsVerify`: a reply-side write (attach, reply, ack) during a verify run does not discard the run | action | Cas (note run) | violated: NOTE |

Every property is shown failing at least once: by its finding, witness or row run, or by
a broken variant in `variants.py`. Three guard conjuncts are structurally implied in this
model and cannot fail on their own: `FailingBeforeOk` and the contract-version half of
`InputsUnchanged` in the verify guard (contract_version and failing_before change only at
`ready`, which an item in running/verifying cannot take), and the matching conjunct of
`VerifiedBound`. They guard the implementation; `VerifiedBound`'s bugfix conjunct fails
in variant `inv6-ready-records-stale-failing-before`, and the attempt-id half of
`StaleNeverWritten` in `inv11-submit-ignores-attempt-id`.

### Row coverage

Each `row-*` run checks one `NoRow_<row>` probe in a scenario that has the row in scope
and expects it violated with that row as the counterexample's last step: every row
outcome of `Table` fires (new, draft, attach, reply, revise, ready, dispatch, start to
running and to blocked(limit), submit, verify, fail by the lead to each of its three
routes, fail by the pm, reassign, split, the (mesh) row to verifying and to rework,
unblock to ready and to blocked(limit), reject, ack, close, cancel, revalidate,
override). A row whose guard can no longer hold turns its run UNEXPECTED. A lead step
needs the assigned lead pane live (D45), so the pm `fail` row is the only way out of
running or verifying while the lead is dead; variant `inv2-dead-pm-fail` shows
`NoDeadItem` failing without it.

## Known findings

Reproduced on every run; each counterexample is printed by `scripts/formal-check.sh`.

| id | verdict | run | finding |
|---|---|---|---|
| G1 | FINDING | replies-g1 | `reject` always goes to rework (section 4); section 5 routes ambiguity via `reject` to blocked(awaiting_user). |
| G2a | READING | split-consumed-g2a, split-consumed-g2a-inv2 | Holds only if the split consumes the parent's conflict/too_large failure record: then a cancelled child returns the parent to rework with no class, reassign, ready and split are all refused, and only cancel/override leave (invariant 2 fails). As written, failures[] keeps the record (section 3) and O3-8 itself assumes a re-split: the parent's latest record stays conflict/too_large, `ready` and `split` work, and `RouteDefined` and `NoDeadItem` hold (run split). Under the consuming reading, section 5's "mesh (split parent)" as a setter of defect would also make `reassign` work (variant `g2a-reading-mesh-sets-defect`). Both readings are run; the model picks neither. |
| G2b | FINDING | split-g2b, o38-stay-orphans, o38-unlink-orphans | When a cancelled child sends the parent back to rework, the remaining children stay active with no disposition, in both O3-8 alternatives. |
| G3 | FINDING | o39-refused-g3 | Under O3-9 precheck_refused a guard refusal writes the record: the refused unblock increments environment_unblock. Measured against the model-added `GuardRefusalWritesNothing`, not against invariant 1 (which is about unlisted combinations). |
| G4 | FINDING | core-g4 | No verb clears the revalidate flag: after reject, reassign and a new verify the item is verified with the old flag and can never close. |
| G5 | STRUCTURAL | followup-g5, followup-g5-flag | The follow-up draft for a closed item has no section-4 row, both for a thread reply (section 9, D41) and for a revalidate flag (revalidate row guard, D60). Read off the table; TLC only replays it. |
| G6 | FINDING | deps-g6 | A ready item whose dependency is cancelled can never start; only revise or cancel leave. |
| G8 | STRUCTURAL | split-g8 | No section-4 row moves a split parent out of verifying: the (mesh) row's from is blocked(children), and its guard says the parent's own verification runs and "failure -> rework (defect)", but `verify` and `fail` are lead/pm rows for an attempt. The model runs the step (pass -> verified, failure -> rework) without a row. |
| F1 | FINDING | tablewalk-f1 | The operator `override` row (any -> any) can enter blocked(limit), against invariant 4 ("never set by a caller") and section 5 ("mesh only"). The design is silent on whether override is exempt, so the model does not exempt it. The other scenarios leave override out of scope (`WithOverride = FALSE`). |
| F2 | FINDING | split-f2 | Cancelling a split parent leaves its children active; the cancel row gives them no disposition (G2b for a cancelled parent). |
| inv 10 | FINDING | replies-ties-inv10 | (was G7) Freshness compares the reply's Slack ts or hook event time with the recorded limit-entry time, two clocks (D64). When the recorded time lags the entry, a reply received before the blocked(limit) entry unlocks it: the Slack watcher attaches a reply at tick 1, the item enters blocked(limit) at tick 2 with limit_at 0 on the record, PM `ready` accepts the reply. D63 says "received after". Ties alone are safe (strict comparison); single use still holds. |
| O3-8 | FINDING | o38-stay-never-completes | With stay_linked, which is the design as written, a re-split never completes: the cancelled child of the first split stays in children[], so the (mesh) row's "every child closed" never holds and its "a cancelled child -> rework" fires again. With unlink the re-split completes (o38-unlink-close). |
| O3-9 | FINDING | o39-refused-inv5 | O3-9 precheck_refused also breaks invariant 5: passing unblocks are not counted, so starts between counter resets are unbounded. |
| N1 | NOTE | cas-n1 | Any reply-side write (attach, reply, ack) while a verify runs makes the result stale, so the run is discarded (D57); correct by invariant 11, but a busy thread can starve verification. |
| N2 | closed | (variant inv10-single-use-dropped) | With one strictly increasing clock the D63 single-use bit looked implied by freshness and the variant that drops it survived. In RepliesClockTies the variant fails (one reply unlocks blocked(limit) twice) and the real spec holds: the bit is load-bearing. |

Open items stay open: O3-8 (`O38 = "stay_linked" | "unlink"`) and O3-9
(`O39 = "unblock_pass" | "precheck_refused"`) are constants and both alternatives run.

## Coverage

| design | model | checked by |
|---|---|---|
| s1 states, blocked reasons | `item.state`, `item.blocked_reason`, `StateOf` | TypeOK; reach-* probes and TableReach (inv 3) |
| s3 id, state, reason, version | `item`, `version` | TableOnly, RefusedUnchanged, GuardRefusedUnchanged, StaleNeverWritten |
| s3 contract: contract_version, depends, type bugfix, repro hash | `contract_version`, `depends`, `BugfixItems`, `ReproHash` | VerifiedBound, DependsWellFormed |
| s3 attempts[] (current attempt only) | `attempt` | VerifiedBound, StaleNeverWritten |
| s3 counters, s6 | `retry`, `environment_unblock`, `next_start_free` | BoundedStarts (inv 5), LimitOnlyByMesh (inv 4) |
| s3 failures[] (latest only), s5 | `failure`, `RouteOf`, `ReadyRoute`, `SplitFailure` | RouteByClass (G1), RouteDefined (G2a), NoDeadItem (inv 2) |
| s3 failing_before, s7 | `failing_before` | VerifiedBound (inv 6) |
| s3 replies[], s9, s9a | `replies`, `limit_at`, `events`, `clock`; `ClockTies` (two clocks, D64); ghost `limit_entry` | LimitReplySingleUse, LimitReplyFresh (inv 10), CloseGates (inv 7) |
| s3 links | `parent`, `children`, `follows` | ParentOwnVerification (inv 9), NoOrphanChildren (G2b), CancelledParentNoActiveChild (F2), ResplitCanComplete (O3-8), G5 |
| s3 flags | `revalidate` | CloseGates (inv 7), FlagClearedByReverify (G4), G5 (pm follow-up) |
| s4 every row | `New`, `SlackDraft`, `Attach`, `Reply`, `Revise`, `Ready`, `Dispatch`, `Start`, `Submit`, `Verify` (`VerifyRead`/`VerifyWrite` with panes), `Fail`, `Reassign`, `Split`, `MeshChildrenClosed`/`MeshCancelledChild`, `Unblock`, `Reject`, `Ack`, `Close`, `Cancel`, `Revalidate`, `Override`; unlisted combinations `Refuse` | TableOnly, UnlistedRefused, RefusedUnchanged (inv 1); TableExits, NoDeadItem, exit-* (inv 2); TableReach, reach-* (inv 3); row-* (every row fires) |
| s4 steps without a row | `FollowUp` (G5), `MeshParentVerify` (G8) | followup-g5, followup-g5-flag, split-g8 (STRUCTURAL) |
| s4 override row | `Override` (TableWalk only) | tablewalk-f1 (F1) |
| s7 evidence binding | `VerifiedRec`, `InputsUnchanged`, `FailingBeforeOk`, `MergedCommit` | VerifiedBound (inv 6) |
| s8 lock + CAS, older attempts (verify only) | `VerifyRead`, `VerifyWrite`, `Owns`, ghost `pane.dirty` | StaleNeverWritten (inv 11), ReplyNeverDiscardsVerify (N1), scenario Cas |
| s9 reply on a closed item; D60 flag on a closed item | `FollowUp` (no s4 row) | followup-g5, followup-g5-flag (STRUCTURAL) |
| s10 inv 1-11 | the properties above | see `EXPECT` in `scripts/formal-check.sh` |
| s10 inv 12 | not expressible | [run-time check](#invariant-12-a-run-time-check) |
| s12 O3-8, O3-9 | constants `O38`, `O39` | o38-*, o39-* runs |

Not covered: the s2 diagram (the table is authoritative); pending_actions[] and Slack
posts (at-least-once, D48); thread TTL; lock-file and git worktree mechanics; hook
installation; metrics. The section-8 read / unlocked commands / compare-and-set write is
modelled for `verify` only (scenario Cas): a failing command run (`fail`), `not_run`
results and the bugfix repro read and compare-and-set at `ready` are atomic, so a stale
result on those paths cannot appear here. Also outside the bounds: more than two
children per split, more than one cancelled item in the O3-8 scenarios, more than one
attempt record, more than one follow-up per closed item, clock skew beyond one tick or a
recorded time ahead of its tick, and a reply refused because its time ties with the
limit entry it follows (liveness).

## Invariant 12: a run-time check

"No command run changes the shared working tree" is outside the model. The
implementation test takes a fingerprint of the shared working tree before and after
every repro and verify run and requires the two to be equal: `git rev-parse HEAD`, the
index (`git ls-files --stage`), and the sha256 of every tracked file that differs from
HEAD and of every untracked file (`git status --porcelain --untracked-files=all`; add
`--ignored` for ignored files outside mesh's own `.mesh/`). Comparing `git status` and
HEAD alone misses a second edit to a file that is already modified or untracked. The
WO-0017 skill (`verify-supervisor-mesh`) takes the same kind of fingerprint of the lead
tree (HEAD, `git status`, dirty-file sha256).

## Bounds

| scenario | items | retry / env limit | replies / events | other bounds | distinct states |
|---|---|---|---|---|---|
| Core, CoreO39Refused | 1 bugfix | 1 / 1 | 0 / 0 | 1 config edit, lead loss, contract version <= 3, attempts <= 4 | 7,944 / 32,964 |
| Replies | 1 | 0 / 0 | 2 / 1 | contract version <= 3, attempts <= 3, clock <= 4 | 21,334 |
| RepliesClockTies | 1 | 0 / 0 | 2 / 1 | as Replies; each limit entry at tick clock + 1 records limit_at in {clock - 1, clock, clock + 1} | 83,448 |
| Split, SplitConsumed | 3 (parent + 2 children) | 0 / 0 | 0 / 0 | attempts <= 2 | 1,578 / 898 |
| SplitStayLinked, SplitUnlink | 5 (parent + 2 + 2 children) | 0 / 0 | 0 / 0 | attempts <= 2, at most one cancelled item | 9,223 / 2,843 |
| Deps | 3 | 0 / 0 | 0 / 0 | contract version <= 1, attempts <= 1 | 38,897 |
| Cas | 1 bugfix, 2 lead panes | 1 / 1 | 1 / 0 | 1 config edit, lead loss, attempts <= 3, record version <= 12 | 22,170 |
| TableWalk | 1 bugfix + override | 0 / 0 | 1 / 0 | contract version <= 1, attempts <= 1 | 13 |
| FollowUp, FollowUpFlag | 2 (item + follow-up slot) | 0 / 0 | 0 / 0 | attempts <= 2 | 476 (FollowUpFlag: finding run only) |

The design default for the environment limit is 3 and the retry limit is config; the
properties are written in terms of the limits, so larger limits only lengthen the same
paths. A split always creates exactly two children (the design allows two or more);
the O3-8 scenarios allow at most one cancelled item (`MaxCancelled`). Split children use
only ready, start, submit, verify, close and cancel; the Split parent has every verb
that leaves a state it reaches (so `NoDeadItem` is meaningful there); the O3-8
alternative parents use new, ready, start, fail, split, mesh and close. TLC also checks
the first state past a bound (`CONSTRAINT Bounds`). `NoDeadItem` is checked only where
the verb scope holds every way out of the states the scenario reaches; in a cut scope
(Deps, Cas, the O3-8 alternatives, TableWalk, FollowUp) dead items exist by
construction. TableWalk runs without lead loss: which combinations the table lists does
not depend on it, and `UnlistedRefused` costs one ENABLED evaluation per unlisted
combination and state.

## Abstractions and assumptions

- Commits, versions, config hashes and timestamps are small integers; command results
  (repro, verification, precheck, fingerprint re-check) are nondeterministic choices.
- One logical clock ticks once per Slack reply, operator event and limit entry; a reply's
  or event's ts is its tick (a terminal reply carries its hook-recorded event time), so
  a reply is identified by its ts. With `ClockTies = TRUE` (RepliesClockTies only) the
  limit_at that mesh records may tie with the latest reply/event time or lag one tick
  behind it.
- Only the current attempt and the latest failure record are kept. The route out of a
  rework is the class of the parent's latest failure record; the cancelled-child step of
  the (mesh) row records no failure of its own (G2a above).
- `verify` is atomic except in Cas. `StaleNeverWritten` holding there shows that the
  compare-and-set write discards every stale verify result; it says nothing about
  `fail`, `not_run` or the repro at `ready` (not covered above). Record `version` is
  part of the state fingerprint only in Cas. `Owns` compares the attempt id only, which
  suffices while only Cas (one item) has panes.
- A lead step (start, submit, verify, fail by the lead) needs the assigned lead pane
  live; the pm `fail` row covers a dead lead (D45).
- The split parent's own verification runs at the merged commit (section 7) as the
  rowless step of G8.
- `override` (operator) is modelled only in TableWalk (F1 above).
- `CHECK_DEADLOCK FALSE`: terminal states and bounds end behaviours; stuck items are
  named properties (`NoDeadItem`, exit probes, `RouteDefined`, `ClasslessReworkStuck`,
  `NoWaitOnCancelled`, `ResplitCanComplete`). `Waiting` exempts blocked(limit) even where
  no reply can arrive (`MaxReplies = 0`): its way out needs an outside event.

## Verdicts, exit code, files and runtime

Per run the script prints the run name, scenario, states generated and distinct, seconds
and one verdict: PASS (every property holds), REACHED (a witness probe was violated as
required), FINDING (a known design finding reproduced, counterexample printed), READING
(G2a), STRUCTURAL (G5, G8), NOTE (N1), or UNEXPECTED. A violation counts only with the
right counterexample: its last step must equal the run's "last step" column in `EXPECT`;
the same property violated by another path is UNEXPECTED. The script exits 0 when every
run matches `EXPECT` (known findings do not fail the run), 1 on any UNEXPECTED, 2 on a
usage error or when the pinned tools are missing or do not verify, 3 with `--strict`
while known findings remain, and 4 (STALE) when `docs/designs/work-item-state-model.md`
no longer has the sha256 the model is synced to (`DESIGN_SHA256` in the script;
`--allow-stale` runs anyway).

Nothing is written into the repo or the system temp dir. Each invocation works in a new
directory under `${XDG_CACHE_HOME:-~/.cache}/formal-check/`; every TLC run gets a
subdirectory with a copy of the spec, its `run.cfg`, `-metadir`, `-noGenerateSpecTE`
and `java.io.tmpdir`, and `TMPDIR` points inside the work dir too. The work dir is
removed at exit, except with `--keep` (all of it) and except the subdirectories of
UNEXPECTED runs, which stay with `tlc.log`, `trace.json` and `run.cfg`; the script prints
their path. `--variants` uses the same work dir: a variant that behaves as expected is
removed, one that does not stays (path printed), and `--keep` keeps them all. Ctrl-C or
TERM stops every TLC run, waits for it, then removes the work dir.

The pinned tools are checked on every run: the sha256 of `tla2tools.jar` and one sha256
over a manifest of every file of the extracted JRE (paths, link targets, owner-executable
bits, file contents); the published archive sha256 is checked at install.

Measured on the development machine (16 cores shared with other jobs), one TLC job at a
time (`--jobs 1`): 5 min 16 s wall for the 82 runs of the check, 1 min 48 s for
the 34 variants. With the default parallelism (`nproc / 4`, between 2 and 6 jobs)
several runs go at once; the largest runs (RepliesClockTies, CoreO39Refused, Deps,
SplitStayLinked) dominate.
