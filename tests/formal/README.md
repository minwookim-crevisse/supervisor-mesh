# Formal model: work-item state machine (TLA+)

`WorkItemState.tla` models the mesh work-item state machine of
`docs/designs/work-item-state-model.md` (sections 1-10, decisions D40-D76 and D81; why each
row exists: the review records in `docs/designs/pm-led-onboarding.md`, D65-D76 in "Formal
verification findings", D81 in section 11 of the design). There is no `mesh/` code yet, so the design document is the
target: the spec is a second adapter of the section-4 transition table, beside the
future `mesh/state.py`. The constant `Table` holds the section-4 rows as data, one entry
per row outcome and nothing else; the (mesh) rows share the verb `mesh` and differ by
from / to. Every action that implements a row carries a `\* row:` comment copied from
it. Names are the design's (states, blocked reasons, verbs, callers, item record fields,
counters, the mesh `seq`).

This file is the single record of what the model covers, which property checks what,
the bounds and the known findings. How to run the check, read its output, tell a model
bug from a design bug and update the model: `.claude/skills/verify-work-item-state/SKILL.md`.

## Files

| file | content |
|---|---|
| `WorkItemState.tla` | the spec: item record, every section-4 row, refusals, properties, row probes, decision probes |
| `Core.cfg` | one bugfix item, every single-item path except replies: routes, counters (D68), revalidate flag (D69), lead loss |
| `Replies.cfg` | Slack replies + operator events from the PM terminal; ack/close gates; reject --class (D65); limit resume ordered by the one mesh seq (D74) |
| `Split.cfg` | parent split into two children: failures[] kept (D75), cascade cancel of children (D66), (mesh) parent verify rows (D72) |
| `Resplit.cfg` | a split parent with room for a re-split after a cancelled child (D66, D67) |
| `Deps.cfg` | three items with depends: unknown ids, self, cycles and cancelled dependencies refused at ready (D81), satisfied by verified/closed; a cancel moves ready dependents (D71) |
| `DepsRoutes.cfg` | two items; reassign and unblock refuse a dependency cancelled while the item was out of ready (D81) |
| `Cas.cfg` | one bugfix item, two lead panes run verify as read-check / unlocked commands / compare-and-set write on the attempt-relevant fields (D76), one config edit |
| `TableWalk.cfg` | operator override drives one item through every state but blocked(limit) (D73), which start reaches; every unlisted verb x caller must be refusable there |
| `FollowUp.cfg` | the (mesh) follow-up row (D70): a closed item gets a follow-up draft for a thread reply or a revalidate flag |
| `FollowUpFlag.cfg` | FollowUp with the revalidate-flag trigger only (row coverage of that trigger) |
| `variants.py` | the variant definitions (edits + one target property + one scenario each), their runner, the TLC trace printer and the last-step signature |

A scenario cfg holds constants and the properties that must hold in it; it is the
authority on which property is checked where (the table below summarises it). The
script's `EXPECT` table adds one extra property per finding or witness run and names
the last step its counterexample must end with.

## Properties

| design | property | kind | checked in | expected |
|---|---|---|---|---|
| inv 1 | `TableOnly`: every change of an item record is a section-4 row, read off the state diff (the step's verb and caller with the item's actual state before and after, not the step's labels); the only changes of other items are the declared side effects: split's child drafts, the (mesh) follow-up draft (D70), the cascade cancel of a split parent's active children (cancel row, (mesh) child cancelled row, D66) and ready dependents of a cancelled item to blocked(awaiting_user) ((mesh) dependency cancelled row, D71) | action | every scenario | holds |
| inv 1 | `UnlistedRefused`: in every reachable state, every verb x caller x state combination the table does not list has a step in `Next` that refuses it, records it and changes nothing | state (ENABLED) | TableWalk | holds |
| inv 1 | `RefusedUnchanged`: the refusal of an unlisted combination is recorded and leaves the record (and its version) unchanged | action | TableWalk | holds |
| model, D68 | `GuardRefusedUnchanged`: a guard refusal of a listed combination is recorded and leaves the record unchanged, the refused unblock precheck included (not a section-10 invariant) | action | every scenario but TableWalk | holds |
| inv 2 | `TableExits`: table walk, every non-terminal state has a row out that is neither cancel nor override | constant | Core | holds |
| inv 2 | `NoDeadItem`: in every reachable state each non-terminal item has an enabled step (`Progress`) that changes its state and is neither cancel nor override, or waits (`Waiting`: blocked(limit), an active child, a verify run in flight, no live lead, no free pane, an uncancelled dependency) | state (ENABLED) | Core, Replies, Split | holds |
| inv 2 | `NoExit_<state>` (10 probes): the exit is never taken | action | Replies, Split | violated = REACHED |
| inv 3 | `TableReach`: table walk, every state is reachable from draft through rows other than override | constant | Core | holds |
| inv 3 | `Unreached_<state>` (12 probes): the state is never reached | state | Replies, Split | violated = REACHED |
| inv 4 | `LimitOnlyByMesh`: only the mesh counters enter blocked(limit) or record class limit, override included (D73) | action | Core, Replies, Split, Resplit, TableWalk | holds |
| inv 5 | `BoundedStarts`: starts between counter resets <= 1 + RetryLimit + EnvLimit | state | Core, Replies, Split, Resplit, Deps, DepsRoutes | holds |
| inv 6 | `VerifiedBound`: verified binds the submitted commit, contract version, attempt config hash, failing_before | state | every scenario but TableWalk | holds |
| inv 7 | `CloseGates`: close never with unacknowledged replies or a revalidate flag | action | every scenario but Cas, TableWalk | holds |
| inv 8 | `StartNeedsDepends`, `DependsWellFormed`: start needs verified/closed dependencies; no unknown, self or cyclic depends past ready | action, state | Core, Deps, DepsRoutes | holds |
| inv 8 | `DependsNotCancelledAtReady`: ready, reassign and unblock never return an item to ready with a cancelled dependency (D81) | action | Deps, DepsRoutes | holds |
| inv 9 | `ParentOwnVerification`: a closed split parent closed through its own verification | state | Split, Resplit | holds |
| inv 10 | `LimitReplySingleUse`: the reply that unlocks blocked(limit) is one attached reply, and no reply unlocks twice | action | Replies | holds (implied by freshness under one seq: N2, below) |
| inv 10 | `LimitReplyFresh`: the reply that unlocks blocked(limit) has a mesh seq greater than the entry's seq (kept by the ghost, not the recorded limit_seq, D74) | action | Replies | holds |
| inv 11 | `StaleNeverWritten`: no verify write lands after a write since its read that changed an attempt-relevant field (state, attempt id, contract version, submitted commit; a ghost, so a change and its undo count), and no submit/fail write for an older attempt | action | Cas | holds |
| s8, D76 | `ReplyNeverDiscardsVerify`: a reply-side write (attach, reply, ack) during a verify run does not make the compare-and-set discard the run (was note N1) | action | Cas | holds |
| inv 12 | not expressible: the shared working tree is outside the model | - | - | a run-time check of the implementation, see [Invariant 12](#invariant-12-a-run-time-check) |
| inv 13 | `NoOrphanChildren`: no active child remains linked to a split parent that is in rework or cancelled (D66; was G2b, F2) | state | Split, Resplit | holds |
| inv 14 | `NoWaitOnCancelled`: a ready item never depends on a cancelled item (D71, D81) | state | Deps, DepsRoutes | holds |
| inv 14 | `DependentsBlockedOnCancel`: a write that cancels an item moves its ready dependents to blocked(awaiting_user) in the same write (the cancel row's D71 guard) | action | Deps, DepsRoutes | holds |
| inv 15 | `FlagClearedByReverify`: a revalidate flag on a verified item was set after its current verification (D69; was G4) | state | Core, Replies, Deps, FollowUp, FollowUpFlag | holds |
| rows | `NoRow_<row>` (32 probes): that row outcome (verb, caller, from, to) never fires | action | the row-* runs | violated = REACHED; a probe that holds is a dead row |
| s5, D65 | `RouteByClass`: a failure record sends the item where section 5 routes its class (was G1) | action | Core, Replies, Split | holds |
| s5, D75 | `RouteDefined`: every rework has a class with a way back (was G2a) | state | Split, Resplit | holds |
| D67 | `ResplitCanComplete`: a split leaves no already-cancelled child linked (was O3-8) | action | Resplit | holds |
| D67 | `NoResplitAfterCancelledChild`, `NoMergedCloseAfterCancelledChild`: the parent is never split again / never closes through its children after a cancelled child | state | Resplit | violated (the re-split happens and completes) = REACHED |

Every property is shown failing at least once: by its finding or witness run, or by a
broken variant in `variants.py`. Three guard conjuncts are structurally implied in this
model and cannot fail on their own: `FailingBeforeOk` and the contract-version half of
`InputsUnchanged` in the verify guard (contract_version and failing_before change only at
`ready`, which an item in running/verifying cannot take), and the matching conjunct of
`VerifiedBound`. They guard the implementation; `VerifiedBound`'s bugfix conjunct fails
in variant `inv6-ready-records-stale-failing-before`, and the attempt-id half of
`StaleNeverWritten` in `inv11-submit-ignores-attempt-id`.

**N2 again under one seq.** With one strictly increasing seq (D74) and limit_seq recorded
at every entry, a reply that unlocked blocked(limit) has a seq below every later entry,
so freshness alone implies single use: the D63 consumed bit is defence in depth.
Variant `inv10-single-use-dropped` therefore drops the bit and also lets a re-entry keep
the first limit_seq; then one reply unlocks twice and `LimitReplySingleUse` fails.

### Row coverage

Each `row-*` run checks one `NoRow_<row>` probe in a scenario that has the row in scope
and expects it violated with that row as the counterexample's last step: every row
outcome of `Table` fires (new, draft, attach, reply, revise, ready, dispatch, start to
running and to blocked(limit), submit, verify, fail by the lead to each of its three
routes, fail by the pm, reassign, split, (mesh) children done, (mesh) child cancelled,
(mesh) parent verify pass and fail, (mesh) dependency cancelled (inside the cancelling
write: its probe looks at the dependents a cancel moves), (mesh) follow-up for a
thread reply and for a revalidate flag, unblock to ready and to blocked(limit), reject
to rework and to blocked(awaiting_user), ack, close, cancel, revalidate, override). A row
whose guard can no longer hold turns its run UNEXPECTED. A lead step needs the assigned
lead pane live (D45), so the pm `fail` row is the only way out of running or verifying
while the lead is dead; variant `inv2-dead-pm-fail` shows `NoDeadItem` failing without it.

## Known findings

Reproduced on every run; each counterexample is printed by `scripts/formal-check.sh`.

None. The last one, inv 14 (`deps-inv14`, WO-0020), was decided as D81 (below).

## Resolved findings (WO-0016 decided D65-D76; WO-0020 decided D81)

Each former finding now holds in the real spec; its decision is protected by a broken
variant that restores the old behaviour and must FAIL with the named property.

| was | decision | property (now holds in) | variant (old behaviour, FAIL) |
|---|---|---|---|
| G1 FINDING | D65 `reject --class` | `RouteByClass` (Replies, Core, Split) | `d65-reject-always-rework` |
| G2b FINDING | D66 cascade cancel | `NoOrphanChildren`, inv 13 (Split, Resplit) | `d66-child-cancelled-no-cascade` |
| F2 FINDING | D66 cascade cancel | `NoOrphanChildren`, inv 13 (Split, Resplit) | `d66-parent-cancel-no-cascade` |
| O3-8 FINDING | D67 `former_children[]` | `ResplitCanComplete` (Resplit) | `d67-resplit-keeps-cancelled-child` |
| G3 FINDING | D68 refusal writes nothing | `GuardRefusedUnchanged` (every scenario but TableWalk) | `d68-refused-precheck-writes` |
| O3-9 / inv 5 FINDING | D68 +1 per passing unblock | `BoundedStarts`, inv 5 (Core, ...) | `d68-passing-unblock-not-counted` |
| G4 FINDING | D69 verify clears the flag | `FlagClearedByReverify`, inv 15 (Core, ...) | `d69-verify-keeps-flag` |
| G5 STRUCTURAL | D70 (mesh) follow-up row | `TableOnly` (FollowUp, FollowUpFlag) | `d70-follow-up-without-row` |
| G6 FINDING | D71 dependency cancelled | `DependentsBlockedOnCancel` (Deps, DepsRoutes) | `d71-cancel-leaves-dependents-ready` |
| G8 STRUCTURAL | D72 (mesh) parent verify rows | `TableOnly` (Split) | `d72-parent-verify-without-row` |
| F1 FINDING | D73 override not to blocked(limit) | `LimitOnlyByMesh`, inv 4 (TableWalk, ...) | `d73-override-enters-limit` |
| inv 10 (G7) FINDING | D74 one mesh seq | `LimitReplyFresh`, inv 10 (Replies) | `d74-two-clocks-limit-lags` |
| G2a READING | D75 append-only failures[] | `RouteDefined` (Split, Resplit) | `d75-split-consumes-failure` |
| N1 NOTE | D76 attempt-field CAS | `ReplyNeverDiscardsVerify` (Cas) | `d76-cas-on-record-version` |
| inv 14 FINDING (WO-0020 deps-inv14) | D81 ready refuses a cancelled dependency | `NoWaitOnCancelled`, inv 14 (Deps) | `d81-ready-accepts-cancelled-dependency` |
| (by reading in WO-0020) | D81 reassign refuses it | `DependsNotCancelledAtReady`, inv 8 (DepsRoutes) | `d81-reassign-keeps-cancelled-dependency` |
| (by reading in WO-0020) | D81 unblock refuses it | `NoWaitOnCancelled`, inv 14 (DepsRoutes) | `d81-unblock-keeps-cancelled-dependency` |

The open-item constants of the WO-0016 model (`O38`, `O39`, `SplitFailure`, `ClockTies`)
and their scenarios (CoreO39Refused, RepliesClockTies, SplitConsumed, SplitStayLinked,
SplitUnlink) are gone: the decided alternative is the model.

## Coverage

| design | model | checked by |
|---|---|---|
| s1 states, blocked reasons | `item.state`, `item.blocked_reason`, `StateOf` | TypeOK; reach-* probes and TableReach (inv 3) |
| s3 id, state, reason, version | `item`, `version` | TableOnly, RefusedUnchanged, GuardRefusedUnchanged |
| s3 contract: contract_version, depends, type bugfix, repro hash | `contract_version`, `depends`, `BugfixItems`, `ReproHash` | VerifiedBound, DependsWellFormed |
| s3 attempts[] (current attempt only) | `attempt` | VerifiedBound, StaleNeverWritten |
| s3 counters, s6 | `retry`, `environment_unblock`, `next_start_free` | BoundedStarts (inv 5), LimitOnlyByMesh (inv 4), GuardRefusedUnchanged (D68) |
| s3 failures[] (latest record only), s5 | `failure`, `RouteOf`, `ReadyRoute` | RouteByClass (D65), RouteDefined (D75), NoDeadItem (inv 2) |
| s3 failing_before, s7 | `failing_before` | VerifiedBound (inv 6) |
| s3 replies[], limit_seq, s9, s9a | `replies` (`seq`, source), `limit_seq`, `events`, `seq` (D74); ghost `limit_entry` | LimitReplySingleUse, LimitReplyFresh (inv 10), CloseGates (inv 7) |
| s3 links | `parent`, `children`, `former_children` (D67), `follows` | ParentOwnVerification (inv 9), NoOrphanChildren (inv 13), ResplitCanComplete (D67), TableOnly (follow-up) |
| s3 flags | `revalidate` | CloseGates (inv 7), FlagClearedByReverify (inv 15) |
| s4 every row | `New`, `SlackDraft`, `Attach`, `Reply`, `Revise`, `Ready`, `Dispatch`, `Start`, `Submit`, `Verify` (`VerifyRead`/`VerifyWrite` with panes), `Fail`, `Reassign`, `Split`, `MeshChildrenClosed`, `MeshCancelledChild`, `MeshParentVerify`, `FollowUp`, `Unblock`, `Reject`, `Ack`, `Close`, `Cancel`, `Revalidate`, `Override`; unlisted combinations `Refuse` | TableOnly, UnlistedRefused, RefusedUnchanged (inv 1); TableExits, NoDeadItem, exit-* (inv 2); TableReach, reach-* (inv 3); row-* (every row fires) |
| s4 cascades (cancel, (mesh) child cancelled, D66, D71); D81 guards | `CascadeWrite` ((mesh) dependency cancelled row); `CancelledDepends` in Ready, Reassign, Unblock | NoOrphanChildren (inv 13), DependentsBlockedOnCancel, NoWaitOnCancelled (inv 14), DependsNotCancelledAtReady (inv 8) |
| s4 override row | `Override` (TableWalk only), not to blocked(limit) (D73) | LimitOnlyByMesh in TableWalk (inv 4) |
| s7 evidence binding | `VerifiedRec`, `InputsUnchanged`, `FailingBeforeOk`, `MergedCommit` | VerifiedBound (inv 6), FlagClearedByReverify (inv 15) |
| s8 lock + CAS on attempt-relevant fields, older attempts (verify only) | `VerifyRead`, `VerifyWrite`, `AttemptFields`, `Owns`, ghosts `pane.dirty`, `pane.dirty_by` | StaleNeverWritten (inv 11), ReplyNeverDiscardsVerify (D76), scenario Cas |
| s9 reply on a closed item; D60 flag on a closed item | `FollowUp` ((mesh) follow-up row, D70) | TableOnly in FollowUp, FollowUpFlag; row-mesh-follow-up* |
| s10 inv 1-11, 13-15 | the properties above | see `EXPECT` in `scripts/formal-check.sh` |
| s10 inv 12 | not expressible | [run-time check](#invariant-12-a-run-time-check) |

Not covered: the s2 diagram (the table is authoritative); pending_actions[] and Slack
posts (at-least-once, D48); thread TTL; lock-file and git worktree mechanics; hook
installation; metrics; the cancel reasons (`parent_rework`, `parent_cancelled`) and the
blocked sub-reason `dependency_cancelled` are step labels, not record fields. The
section-8 read / unlocked commands / compare-and-set write is modelled for `verify` only
(scenario Cas): a failing command run (`fail`), `not_run` results, the split parent's own
verification and the bugfix repro read and compare-and-set at `ready` are atomic, so a
stale result on those paths cannot appear here; the record-version guard of every other
write is the atomicity of each step. Also outside the bounds: more than two children per
split, a split child that is itself split or has depends, more than one cancel (with its
cascade) in Resplit, more than one attempt record, more than one follow-up per closed
item.

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
| Core | 1 bugfix | 1 / 1 | 0 / 0 | 1 config edit, lead loss, contract version <= 3, attempts <= 4 | 7,272 |
| Replies | 1 | 0 / 0 | 2 / 1 | contract version <= 3, attempts <= 3, seq <= 4 | 21,373 |
| Split | 3 (parent + 2 children) | 0 / 0 | 0 / 0 | attempts <= 2 | 808 |
| Resplit | 5 (parent + 2 + 2 children) | 0 / 0 | 0 / 0 | attempts <= 2, at most two cancelled items (one cancel + its cascade) | 2,993 |
| Deps | 3 | 0 / 0 | 0 / 0 | contract version <= 1, attempts <= 1 | 38,897 |
| DepsRoutes | 2 | 0 / 1 | 0 / 0 | contract version <= 1, attempts <= 2, seq <= 1 | 12,153 |
| Cas | 1 bugfix, 2 lead panes | 1 / 1 | 1 / 0 | 1 config edit, lead loss, attempts <= 3, record version <= 12 | 22,170 |
| TableWalk | 1 bugfix + override + start | 0 / 0 | 1 / 0 | contract version <= 1, attempts <= 1 | 35 |
| FollowUp, FollowUpFlag | 2 (item + follow-up slot) | 0 / 0 | 0 / 0 | attempts <= 2 | 476 / 286 |

The design default for the environment limit is 3 and the retry limit is config; the
properties are written in terms of the limits, so larger limits only lengthen the same
paths. A split always creates exactly two children (the design allows two or more);
Resplit allows at most two cancelled items (`MaxCancelled`), room for one cancelled child
and the sibling its cascade cancels. Split children use only ready, start, submit,
verify, close and cancel; the Split parent has every verb that leaves a state it reaches
(so `NoDeadItem` is meaningful there); the Resplit parent uses new, ready, start, fail,
split, mesh and close. TLC also checks the first state past a bound (`CONSTRAINT
Bounds`). `NoDeadItem` is checked only where the verb scope holds every way out of the
states the scenario reaches; in a cut scope (Deps, DepsRoutes, Cas, Resplit, TableWalk, FollowUp)
dead items exist by construction. TableWalk runs without lead loss: which combinations
the table lists does not depend on it, and `UnlistedRefused` costs one ENABLED
evaluation per unlisted combination and state; it has `start` in scope only to reach
blocked(limit), which override may no longer enter (D73).

## Abstractions and assumptions

- Commits, versions, config hashes and seqs are small integers; command results (repro,
  verification, precheck, fingerprint re-check) are nondeterministic choices.
- One mesh `seq` (D74) ticks once per Slack reply receipt, operator event and limit
  entry; a reply's seq is its receipt seq, a terminal reply carries its hook-recorded
  event's seq, so a reply is identified by its seq; `limit_seq` is the entry's seq.
- Only the current attempt and the latest failure record are kept. failures[] is
  append-only (D75): split keeps the conflict/too_large record, so it stays the route
  out of the rework a cancelled child returns the parent to (the (mesh) child cancelled
  row records no failure of its own). `ready`, `reassign` and `unblock` start a new
  episode and the model's `failure` field (the record that routes the current rework)
  is then empty; the history is not modelled.
- `reject` keeps its trigger: an attached reply (any of the three classes) or a
  revalidate flag (class defect). The D65 row says only "`--class` required (defect,
  conflict, ambiguity; `defect` for a revalidate flag)".
- A cancel and the (mesh) child cancelled row cancel the active children and move the
  ready dependents of everything they cancel in the same write (`CascadeWrite`), so
  invariants 13 and 14 hold after that write. With D81 (ready, reassign and unblock
  refuse a cancelled dependency) that write is the only way a ready item meets a
  cancelled dependency, so the (mesh) dependency cancelled row has no action of its own.
- `verify` is atomic except in Cas. `StaleNeverWritten` holding there shows that the
  compare-and-set write discards every stale verify result; it says nothing about
  `fail`, `not_run` or the repro at `ready` (not covered above). Record `version` is
  part of the state fingerprint only in Cas. `Owns` compares the attempt id only, which
  suffices while only Cas (one item) has panes.
- A lead step (start, submit, verify, fail by the lead) needs the assigned lead pane
  live; the pm `fail` row covers a dead lead (D45).
- The split parent's own verification runs at the merged commit (section 7) as the
  (mesh) parent verify rows (D72).
- `override` (operator) is modelled only in TableWalk.
- `CHECK_DEADLOCK FALSE`: terminal states and bounds end behaviours; stuck items are
  named properties (`NoDeadItem`, exit probes, `RouteDefined`, `ResplitCanComplete`,
  `NoWaitOnCancelled`). `Waiting` exempts blocked(limit) even where no reply can arrive
  (`MaxReplies = 0`): its way out needs an outside event.

## Verdicts, exit code, files and runtime

Per run the script prints the run name, scenario, states generated and distinct, seconds
and one verdict: PASS (every property holds), REACHED (a witness probe was violated as
required), FINDING (a known design finding reproduced, counterexample printed), READING,
STRUCTURAL and NOTE (kinds kept for later findings; none at present), or UNEXPECTED. A
violation counts only with the right counterexample: its last step must equal the run's
"last step" column in `EXPECT`; the same property violated by another path is
UNEXPECTED. The script exits 0 when every run matches `EXPECT` (known findings do not
fail the run), 1 on any UNEXPECTED, 2 on a usage error or when the pinned tools are
missing or do not verify, 3 with `--strict` while known findings remain, and 4 (STALE)
when `docs/designs/work-item-state-model.md` no longer has the sha256 the model is
synced to (`DESIGN_SHA256` in the script; `--allow-stale` runs anyway).

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

Measured on the development machine (16 cores shared with other jobs): 1 min 30 s wall
for the 67 runs of the check at the default parallelism (4 jobs), 1 min 30 s for the 44
variants (one TLC job at a time). With the default parallelism (`nproc / 4`, between 2
and 6 jobs) several runs go at once; the largest runs (Deps, Cas, Replies) dominate.
