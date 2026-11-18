# Work-item state model (mesh stage 1)

> Single source of truth for the `mesh` work-item state machine (design E2 in `pm-led-onboarding.md` points here).
> Consolidates decisions D40-D76 (CEO review 2026-10-19, eng review round 3, Codex rounds, formal-verification findings WO-0016 decided 2026-10-29). Knowledge basis: bundle 05 (workflow, state table, failure classes) and 09 (task types).
> Status: approved; no open items (section 12). Formal model: `tests/formal/` (re-sync required after D65-D76). Implementation: T2 (`mesh/state.py`).

## 1. States

| state | meaning | terminal | entered by |
|---|---|---|---|
| `draft` | request captured, contract incomplete | no | `new`, `draft` (Slack), `revise`, follow-up/split child |
| `ready` | contract complete, waiting for a lead | no | `ready`, `reassign`, `unblock` |
| `running` | an attempt is in progress | no | `start` |
| `verifying` | attempt submitted, verification running | no | `submit`, mesh (split parent, all children closed) |
| `verified` | evidence recorded for the current commit and contract | no | `verify`, mesh (split parent verification passes, D72) |
| `closed` | accepted by the PM; the completion boundary | yes | `close` |
| `rework` | failed; waiting for a PM route | no | `fail`, `reject`, mesh (split parent failure, cancelled child) |

`cancelled` is also entered by mesh cascades: active children of a split parent that returns to rework or is cancelled (D66).
| `blocked(reason)` | stopped; one state with a reason field | no | see reasons |
| `cancelled` | abandoned with a reason | yes | `cancel` |

Blocked reasons:

| reason | entered when | leaves via |
|---|---|---|
| `awaiting_user` | failure class `ambiguity` (via `fail` or `reject --class ambiguity`, D65); a dependency was cancelled (`dependency_cancelled`, D71) | `ready` with revised done criteria or depends (version +1) |
| `environment` | failure class `environment` | `unblock` (precheck passes) |
| `limit` | retry or environment counter exceeded (mesh only; `override` may not enter it, D73) | `ready` with revised contract + fresh single-use operator reply (Slack or PM terminal, D64); counters reset |
| `children` | `split` | mesh, when every child is closed -> `verifying` |

## 2. Diagram

```
                    revise
           ┌──────────────────────┐
           ▼                      │
 new/draft─► draft ──ready──► ready ──start──► running ──submit──► verifying ──verify──► verified ──close──► closed
               ▲               ▲ ▲ ▲              │ fail               │ fail                │ reject      │
               │               │ │ │              ▼                    ▼                     ▼             │ reply → new draft (follows)
               │      reassign │ │ └────────── rework ◄──────────────────────────────────────┘             │ revalidate flag → follow-up draft
               │      /ready   │ │               │ split (too_large)
               │               │ │               ▼
               │               │ │        blocked(children) ──all children closed──► verifying (parent's own commands)
               │               │ └─unblock── blocked(environment) ──env limit──► blocked(limit)
               │               └──ready (revised)── blocked(awaiting_user)            │
               │                  ready (revised + fresh reply, counters reset) ◄─────┘
               └── closed item reply / revalidate flag (follow-up draft, D41/D60)
 any state except closed ──cancel──► cancelled          override (operator only): any → any, logged
```

## 3. Item record

| field | content | decisions |
|---|---|---|
| `id`, `state`, `blocked_reason`, `version` | identity, state, reason, record version for compare-and-set | D45, D57 |
| `contract` | goal, done criteria, role, scope, `type`, type inputs, `depends`, `repro` (bugfix), `contract_version` | D42, D46, D62 |
| `attempts[]` | attempt id, lead, commit, contract version, verification config hash, results | D13, D47 |
| `counters` | `retry`, `environment_unblock` | D44, D53, D52 |
| `failures[]` | class, reason (`too_large`), evidence, attempt id, actor; append-only, `split` does not consume it | D40, D54, D75 |
| `failing_before` | contract version, repro hash, base commit, exit code | D46, D55, D56 |
| `replies[]` | mesh receipt `seq`, source (`slack` ts / `terminal` event id, reference only), acknowledged, consumed (limit resume) | D41, D49, D63, D74 |
| `limit_seq` | mesh `seq` at the blocked(limit) entry | D74 |
| `links` | `parent`, `children[]`, `former_children[]` (cancelled children unlinked at re-split), `follows` | D41, D54, D67 |
| `flags` | `revalidate` (cleared by a passing `verify`, D69) | D27, D60, D69 |
| `pending_actions[]` | notify / dispatch / Slack post, with `action_id`, done flag | D32, D48 |

## 4. Transition table

One declarative table in `mesh/state.py`; the CLI and the tests read the same table. Unlisted verb x caller x state combinations are refused and recorded (caller, verb, reason).

| verb | caller | from | to | guard | counters |
|---|---|---|---|---|---|
| `new` | pm | (none) | draft | none | none |
| `draft` | slack watcher | (none) | draft | allowlisted user, ts dedup | none |
| `attach` | slack watcher | any except closed, cancelled | unchanged | thread map; notifies the PM; never releases a block | none |
| `reply` | pm | any except closed, cancelled | unchanged | cites an operator event id recorded by the PM-pane prompt hook (section 9a); attaches it as a reply; never releases a block (D64) | none |
| `revise` | pm | ready | draft | reason; next `ready` bumps the contract version | none |
| `ready` | pm | draft, rework, blocked(awaiting_user), blocked(limit) | ready | contract + type inputs present; dependencies exist, none cancelled (D81), no self-dependency, no cycle; pins the verification config hash (D100); bugfix repro fails at the base commit (126/127, timeout or spawn error refused as not runnable); from rework/awaiting_user: class route (section 5), with the class-specific revision present (conflict: scope or depends revised; ambiguity: done criteria revised; contract version +1) (D104); from blocked(limit): revised contract (version +1) + fresh unconsumed user reply | leaving blocked(limit) resets both counters |
| `dispatch` | pm | ready | ready | assigned role; message-ref | none |
| `start` | lead (own role) | ready | running | every dependency verified or closed; agent live; new attempt id; records contract version; verification config hash must equal the one pinned at `ready` (D100), else refused as `inputs_changed` | retry +1 for every attempt after the first, except the first after an environment unblock or a limit resume; over the limit -> blocked(limit) + Slack question |
| `submit` | lead (own role) | running | verifying | current attempt id; changed commit that descends from the attempt's base commit (D105) | none |
| `verify` | lead (own role); mesh runs commands | verifying | verified | all commands pass on the submitted commit; type evidence present; bugfix failing-before matches the current contract version and repro hash; config hash and inputs unchanged, else refused as `inputs_changed`; clears a revalidate flag (D69) | none |
| `fail` | lead (own role): defect, conflict, ambiguity, environment; pm: environment only when the lead is absent or dead | running, verifying | per class (section 5) | failure record: class + evidence | none |
| `reassign` | pm | rework | ready | class route (section 5); no dependency cancelled (D81); re-pins the verification config hash (D106) | none (counted at `start`) |
| `split` | pm | rework (conflict, reason `too_large`) | blocked(children) | at least two child drafts linked by `parent`; already-cancelled children move to `former_children[]` (D67); `failures[]` kept (D75) | none |
| (mesh) children done | mesh | blocked(children) | verifying | every linked child closed | none |
| (mesh) child cancelled | mesh | blocked(children) | rework | a linked child is cancelled; remaining active children of the parent -> cancelled (reason `parent_rework`); the cancelled child is unlinked to `former_children[]` at the next `split` (D66, D67) | none |
| (mesh) parent verify pass | mesh | verifying (split parent) | verified | the parent's own verification commands pass on the merged commit; evidence bound as for `verify`; clears a revalidate flag (D72, D69) | none |
| (mesh) parent verify fail | mesh | verifying (split parent) | rework | failure record class `defect` (D72) | none |
| (mesh) dependency cancelled | mesh | ready | blocked(awaiting_user) | an item in `depends` was cancelled; reason `dependency_cancelled` (D71) | none |
| (mesh) follow-up | mesh | closed | unchanged | a Slack thread reply (section 9) or a revalidate flag on a closed item creates one new `draft` with `follows` (D70) | none |
| `unblock` | pm | blocked(environment) | ready | precheck passes; no dependency cancelled (D81); re-pins the verification config hash (D106); a refused precheck is a guard refusal and writes nothing to the item (D68) | environment +1 per passing unblock (D68); over its limit (default 3) -> blocked(limit) + Slack question |
| `reject` | pm | verified | rework, or blocked(awaiting_user) for `--class ambiguity` | `--class` required (defect, conflict, ambiguity; `defect` for a revalidate flag); trigger: an attached reply, a revalidate flag, or the PM's own evidence review with a written reason (D101); failure record (D65) | none |
| `ack` | pm | any non-terminal state with attached replies | unchanged | marks replies acknowledged | none |
| `close` | pm | verified | closed | no unacknowledged replies; no revalidate flag | none |
| `cancel` | pm | any except closed | cancelled | reason; on a split parent its active children -> cancelled (reason `parent_cancelled`, D66); dependents in `ready` -> blocked(awaiting_user) (D71) | none |
| `revalidate` | pm (integration gate) | verified, closed | unchanged (flag) | fingerprint re-check; flag on a closed item -> (mesh) follow-up row (D70) | none |
| `override` | operator only (ask rule) | any | any except blocked(limit) (D73) | actor + reason recorded | none |

## 5. Failure classes and routes

| class | who sets it | goes to | way back | knowledge 05 response |
|---|---|---|---|---|
| `defect` | lead, PM via `reject --class defect`, mesh (split parent verify fail) | rework | `reassign` | assign a fix to the lead/worker |
| `conflict` | lead (reason `too_large` allowed), PM via `reject --class conflict` | rework | `ready` after the PM revises scope/depends, or `split` | PM re-coordinates scope, order, interfaces |
| `ambiguity` | lead, PM via `reject --class ambiguity` | blocked(awaiting_user) | `ready` with revised done criteria (version +1) | confirm with the user, update criteria |
| `environment` | lead, or PM when the lead is absent/dead | blocked(environment) + notify thread and PM | `unblock` (precheck) | recover the environment, re-run only what is needed |
| `limit` | mesh only (counters) | blocked(limit) + Slack question | `ready` with revised contract + fresh reply | stop automatic repetition, report options |

## 6. Counters

| counter | increments | does not increment | limit | on exceed | reset |
|---|---|---|---|---|---|
| `retry` | every `start` after the first | first `start` after an environment unblock or a limit resume | config | blocked(limit) + Slack question | leaving blocked(limit) |
| `environment_unblock` | each passing `unblock` (D68) | refused prechecks, other verbs | config, default 3 | blocked(limit) + Slack question | leaving blocked(limit) |

## 7. Evidence rules

- Verification and repro run in a disposable `git worktree` at the recorded commit (repro: base commit; verify: submitted commit; split parent: merged commit), with an optional config `prepare` command; the worktree is removed afterwards. The shared working tree is never touched.
- Repro runs as argv (no shell) at the worktree root with a config timeout (default 120 s). Pass -> `ready` refused (not reproduced). Exit 126/127, timeout, spawn error -> refused (not runnable). Other non-zero -> `failing_before` recorded.
- `verified` binds: commit, contract version, verification config hash, command exit codes and timestamps, required evidence kinds for the type.
- `verified` is historical; `revalidate` re-checks the current tree at the integration gate and sets a flag; a flag blocks `close`.
- A lead's prose report never reaches `verified`; missing tools are `not_run`, not pass.

## 8. Concurrency

- `.mesh/state.json` is guarded by a lock file + atomic replace. The lock is held only to read-check and to write.
- Commands run unlocked. The write re-checks the attempt-relevant fields (state, current attempt id, contract version, submitted commit) and the attempt id (compare-and-set); a mismatch discards the result as stale. Reply-side writes (`attach`, `reply`, `ack`) do not touch these fields and do not discard a running verification (D76). The record `version` still guards every other write.
- Older attempt ids are rejected. External effects are written as pending actions before execution and replayed on start.

## 9. Slack linkage

- Top-level request -> `draft` (ts dedup). Thread reply -> `attach` to the mapped item (never releases a block).
- Reply on a `verified` item -> attached, `close` waits for `ack`.
- Reply on a `closed` item -> (mesh) follow-up row: new `draft` with `follows`; the thread is remapped to it (a burst of replies yields one item).
- Threads of non-finished items are followed without TTL; finished (closed/cancelled) items keep 24 h.
- Posts are at-least-once with a visible `action_id` (duplicates possible after a crash; upgrade path recorded in D48).
- Ordering (D74): mesh stamps every recorded event (reply receipt, terminal event, blocked(limit) entry) with one monotonic `seq` from `.mesh/state.json`. Freshness compares `seq` only; Slack ts and wall-clock times are kept for reference and never compared.

## 9a. Operator answers from the PM terminal (D64)

- Operator answers arrive through one of two channels: the PM terminal (current default) or Slack. `.mesh/config.toml` `operator_channel = "terminal" | "slack" | "both"`; init writes `terminal`.
- Trigger: a Claude Code `UserPromptSubmit` hook, installed by `mesh init` in the project's `.claude/settings.json`, runs `mesh event record --source terminal`. It records the prompt (text, timestamp, event id) to `.mesh/events.jsonl` only when all of these hold:
  - the caller pane is the pinned PM (projmux role label `pm`, project cwd, the agent uid `mesh_init` pinned, exactly one live `role=pm` agent; D109);
  - the hook runs under that pane's claude process (D96);
  - the hook's `session_id` and realpath(`transcript_path`) equal the live `SessionID` and `TranscriptPath` projmux reports for that agent (`describe agent`); during a D114 re-pin, the calling new PM agent's session. projmux is asked on EVERY event; the session is never pinned from a prompt (D118). No SessionID from projmux, or projmux unavailable, records nothing (fail closed, hook_error `session_not_pm` / `projmux_unavailable`); neither value is logged or stored;
  - the prompt equals the latest real user prompt of that transcript, an entry not used before (D110); this proof runs when a tool first cites the event, not in the hook, because the user entry reaches the transcript after the hook returns (D123).

  Otherwise it exits 0 and records only a hook_error code; the hook never blocks the prompt.
- Residual (D96, D118, D119): a process of the same OS user can still forge an operator event. It can append a fake user line to the REAL transcript, or feed projmux's session record with a forged `projmux internal agent-hook ingest`. `mesh init` adds the advisory deny `Bash(*agent-hook*ingest*)` to PM and lead settings until projmux checks the provenance. Ordinary events stay exposed to this; high-impact events stay behind the D113 out-of-band code.
- The PM agent links an answer with `mesh work reply <item> --event <id>`. Only hook-recorded events are accepted, so the PM cannot fabricate an operator answer; freshness and single use (D63) apply to the event's mesh `seq` exactly as to a Slack reply (D74).
- Slack replies keep the `attach` path (section 9). Both channels feed the same `replies[]` record with `source`.
- Tests: hook in a lead pane records nothing; PM pane records an event; `reply` with an unknown or reused event id is refused; a terminal reply unlocks blocked(limit) once. A fabricated session id and transcript are refused (`session_not_pm`) and the projmux-reported session is accepted; no SessionID from projmux fails closed.

## 10. Invariants (each is a test)

1. Every verb x caller x state combination not in the table is refused and recorded.
2. Every non-terminal state has an exit that is neither `cancel` nor `override`.
3. Every state is reachable from `draft`.
4. `limit` is never set by a caller, `override` included.
5. The retry limit holds whichever verb returns the item to `ready`.
6. `verified` never exists without evidence bound to the current commit, contract version and config hash.
7. `closed` never happens with unacknowledged replies or a revalidate flag.
8. A dependency is satisfied only by `verified` or `closed`; cycles, unknown ids and cancelled dependencies are refused at `ready`, `reassign` and `unblock` (D81).
9. A split parent closes only through its own verification and the normal `close` gates.
10. A user reply unlocks `blocked(limit)` at most once and only if its mesh `seq` is greater than `limit_seq`.
13. No active child remains linked to a split parent that is in rework or cancelled (D66).
14. A ready item never depends on a cancelled item (D71).
15. A passing verification leaves no revalidate flag from before it (D69).
11. A stale result (version or attempt mismatch) is never written.
12. No command run changes the shared working tree.

Invariants 2, 3 and 8 can be checked mechanically by walking the table (graph search over states); a table-walk test covers 1, 2, 3, 4.

## 11. Decision trace

| decision | changed |
|---|---|
| D40 | five failure classes with code-enforced routes |
| D41 | reply on a finished item -> new draft with `follows` |
| D42 | contract `type` and per-type inputs/evidence (presence checks) |
| D43 / D50 | metrics; baseline comparison indicative only |
| D44 | environment unblock limit and notification |
| D45 | single transition table with callers |
| D46 / D55 / D56 | bugfix repro at `ready`; exit classification; bound to contract version + repro hash |
| D47 | contract version and verification config hash per attempt |
| D48 | Slack posts at-least-once with `action_id` |
| D49 | `closed` state; replies before close need `ack` |
| D52 / D63 | exit from blocked(limit) with revised contract and a fresh single-use reply; counters reset |
| D53 | retries counted at `start` |
| D54 | `limit` mesh-only; `too_large` -> `split` with parent/children |
| D57 / D58 | short lock + compare-and-set; commands in a disposable worktree |
| D59 | dependency satisfied by verified or closed |
| D60 | PM `reject`; revalidate flag blocks close; closed flag -> follow-up |
| D61 | split parent re-verifies; `ack` in any non-terminal state |
| D62 | dependency validation; `revise`; strict exit invariant |
| D63 | reply that unlocks blocked(limit) is fresh and single use |
| D64 | operator answers from the PM terminal via `UserPromptSubmit` hook + `mesh work reply`; Slack optional channel |
| D65 (G1) | `reject --class`; ambiguity -> blocked(awaiting_user), others -> rework |
| D66 (G2b, F2) | parent back to rework or cancelled -> mesh cancels its remaining active children |
| D67 (O3-8) | re-split unlinks cancelled children to `former_children[]` |
| D68 (O3-9, G3) | environment counter +1 per passing `unblock`; refused precheck writes nothing |
| D69 (G4) | passing verification clears the revalidate flag |
| D70 (G5) | (mesh) follow-up row for a reply or flag on a closed item |
| D71 (G6) | cancelled dependency -> dependent ready item to blocked(awaiting_user) `dependency_cancelled` |
| D72 (G8) | (mesh) rows for the split parent's own verification: pass -> verified, fail -> rework (defect) |
| D73 (F1) | `override` may not enter blocked(limit) |
| D74 (inv 10, G7) | one mesh `seq` orders replies and limit entry; external clocks are reference only |
| D75 (G2a) | `split` keeps `failures[]` (append-only) |
| D76 (N1) | verification CAS compares attempt-relevant fields only; reply-side writes do not discard it |
| D106 (WO-0027 question) | every PM verb that enters `ready` (`ready`, `reassign`, `unblock`) pins the current verification config hash; a config-only change needs no revise |
| D104 (WO-0022 review F17) | `ready` from rework/awaiting_user requires the class-specific contract revision |
| D105 (WO-0022 review F21) | `submit` requires a commit descending from the attempt's base commit |
| D100 (WO-0022 review P5) | verification config hash pinned at `ready`; `start` refuses a changed config |
| D101 (WO-0022 review P6) | PM may `reject` on its own evidence review (reason + class required) |
| D81 (WO-0020 deps-inv14) | `ready`, `reassign`, `unblock` refuse a cancelled dependency; inv 14 stays strict |

## 12. Open items

None. O3-7 resolved by D64; O3-8 by D67; O3-9 by D68. Formal-verification findings of WO-0016 (G1-G8, F1, F2, inv 10, N1) decided by D65-D76 on 2026-10-29; WO-0020 gap deps-inv14 decided by D81 on 2026-11-18; the TLA+ model must be re-synced to this revision (follow-up work order).
