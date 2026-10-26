# Work-item state model (mesh stage 1)

> Single source of truth for the `mesh` work-item state machine (design E2 in `pm-led-onboarding.md` points here).
> Consolidates decisions D40-D63 (CEO review 2026-10-19, eng review round 3, Codex rounds). Knowledge basis: bundle 05 (workflow, state table, failure classes) and 09 (task types).
> Status: approved except the open items at the end. Implementation: T2 (`mesh/state.py`).

## 1. States

| state | meaning | terminal | entered by |
|---|---|---|---|
| `draft` | request captured, contract incomplete | no | `new`, `draft` (Slack), `revise`, follow-up/split child |
| `ready` | contract complete, waiting for a lead | no | `ready`, `reassign`, `unblock` |
| `running` | an attempt is in progress | no | `start` |
| `verifying` | attempt submitted, verification running | no | `submit`, mesh (split parent) |
| `verified` | evidence recorded for the current commit and contract | no | `verify` |
| `closed` | accepted by the PM; the completion boundary | yes | `close` |
| `rework` | failed; waiting for a PM route | no | `fail`, `reject`, mesh (split parent failure, cancelled child) |
| `blocked(reason)` | stopped; one state with a reason field | no | see reasons |
| `cancelled` | abandoned with a reason | yes | `cancel` |

Blocked reasons:

| reason | entered when | leaves via |
|---|---|---|
| `awaiting_user` | failure class `ambiguity` | `ready` with revised done criteria (version +1) |
| `environment` | failure class `environment` | `unblock` (precheck passes) |
| `limit` | retry or environment counter exceeded (mesh only) | `ready` with revised contract + fresh single-use operator reply (Slack or PM terminal, D64); counters reset |
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
| `failures[]` | class, reason (`too_large`), evidence, attempt id, actor | D40, D54 |
| `failing_before` | contract version, repro hash, base commit, exit code | D46, D55, D56 |
| `replies[]` | Slack ts, acknowledged, consumed (limit resume) | D41, D49, D63 |
| `links` | `parent`, `children[]`, `follows` | D41, D54 |
| `flags` | `revalidate` | D27, D60 |
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
| `ready` | pm | draft, rework, blocked(awaiting_user), blocked(limit) | ready | contract + type inputs present; dependencies exist, no self-dependency, no cycle; bugfix repro fails at the base commit (126/127, timeout or spawn error refused as not runnable); from rework/awaiting_user: class route (section 5); from blocked(limit): revised contract (version +1) + fresh unconsumed user reply | leaving blocked(limit) resets both counters |
| `dispatch` | pm | ready | ready | assigned role; message-ref | none |
| `start` | lead (own role) | ready | running | every dependency verified or closed; agent live; new attempt id; records contract version + verification config hash | retry +1 for every attempt after the first, except the first after an environment unblock or a limit resume; over the limit -> blocked(limit) + Slack question |
| `submit` | lead (own role) | running | verifying | current attempt id; changed commit | none |
| `verify` | lead (own role); mesh runs commands | verifying | verified | all commands pass on the submitted commit; type evidence present; bugfix failing-before matches the current contract version and repro hash; config hash and inputs unchanged, else refused as `inputs_changed` | none |
| `fail` | lead (own role): defect, conflict, ambiguity, environment; pm: environment only when the lead is absent or dead | running, verifying | per class (section 5) | failure record: class + evidence | none |
| `reassign` | pm | rework | ready | class route (section 5) | none (counted at `start`) |
| `split` | pm | rework (conflict, reason `too_large`) | blocked(children) | at least two child drafts linked by `parent` | none |
| (mesh) | mesh | blocked(children) | verifying | every child closed; parent's own verification runs; failure -> rework (defect); a cancelled child -> rework | none |
| `unblock` | pm | blocked(environment) | ready | precheck passes | environment +1 (see open item O3-9); over its limit (default 3) -> blocked(limit) + Slack question |
| `reject` | pm | verified | rework | failure record (class from the reply, or `defect` for a revalidate flag) | none |
| `ack` | pm | any non-terminal state with attached replies | unchanged | marks replies acknowledged | none |
| `close` | pm | verified | closed | no unacknowledged replies; no revalidate flag | none |
| `cancel` | pm | any except closed | cancelled | reason | none |
| `revalidate` | pm (integration gate) | verified, closed | unchanged (flag) | fingerprint re-check; flag on a closed item -> PM opens a follow-up draft | none |
| `override` | operator only (ask rule) | any | any | actor + reason recorded | none |

## 5. Failure classes and routes

| class | who sets it | goes to | way back | knowledge 05 response |
|---|---|---|---|---|
| `defect` | lead, PM via `reject`, mesh (split parent) | rework | `reassign` | assign a fix to the lead/worker |
| `conflict` | lead (reason `too_large` allowed), PM via `reject` | rework | `ready` after the PM revises scope/depends, or `split` | PM re-coordinates scope, order, interfaces |
| `ambiguity` | lead, PM via `reject` | blocked(awaiting_user) | `ready` with revised done criteria (version +1) | confirm with the user, update criteria |
| `environment` | lead, or PM when the lead is absent/dead | blocked(environment) + notify thread and PM | `unblock` (precheck) | recover the environment, re-run only what is needed |
| `limit` | mesh only (counters) | blocked(limit) + Slack question | `ready` with revised contract + fresh reply | stop automatic repetition, report options |

## 6. Counters

| counter | increments | does not increment | limit | on exceed | reset |
|---|---|---|---|---|---|
| `retry` | every `start` after the first | first `start` after an environment unblock or a limit resume | config | blocked(limit) + Slack question | leaving blocked(limit) |
| `environment_unblock` | `unblock` (event to confirm, O3-9) | other verbs | config, default 3 | blocked(limit) + Slack question | leaving blocked(limit) |

## 7. Evidence rules

- Verification and repro run in a disposable `git worktree` at the recorded commit (repro: base commit; verify: submitted commit; split parent: merged commit), with an optional config `prepare` command; the worktree is removed afterwards. The shared working tree is never touched.
- Repro runs as argv (no shell) at the worktree root with a config timeout (default 120 s). Pass -> `ready` refused (not reproduced). Exit 126/127, timeout, spawn error -> refused (not runnable). Other non-zero -> `failing_before` recorded.
- `verified` binds: commit, contract version, verification config hash, command exit codes and timestamps, required evidence kinds for the type.
- `verified` is historical; `revalidate` re-checks the current tree at the integration gate and sets a flag; a flag blocks `close`.
- A lead's prose report never reaches `verified`; missing tools are `not_run`, not pass.

## 8. Concurrency

- `.mesh/state.json` is guarded by a lock file + atomic replace. The lock is held only to read-check and to write.
- Commands run unlocked. The write re-checks the record `version` and the attempt id (compare-and-set); a mismatch discards the result as stale.
- Older attempt ids are rejected. External effects are written as pending actions before execution and replayed on start.

## 9. Slack linkage

- Top-level request -> `draft` (ts dedup). Thread reply -> `attach` to the mapped item (never releases a block).
- Reply on a `verified` item -> attached, `close` waits for `ack`.
- Reply on a `closed` item -> new `draft` with `follows`; the thread is remapped to it (a burst of replies yields one item).
- Threads of non-finished items are followed without TTL; finished (closed/cancelled) items keep 24 h.
- Posts are at-least-once with a visible `action_id` (duplicates possible after a crash; upgrade path recorded in D48).

## 9a. Operator answers from the PM terminal (D64)

- Operator answers arrive through one of two channels: the PM terminal (current default) or Slack. `.mesh/config.toml` `operator_channel = "terminal" | "slack" | "both"`; init writes `terminal`.
- Trigger: a Claude Code `UserPromptSubmit` hook, installed by `mesh init` in the project's `.claude/settings.json`, runs `mesh event record --source terminal`. The command records the prompt (text, timestamp, event id) to `.mesh/events.jsonl` only when the caller pane is the PM (projmux role label `pm` + project cwd); in any other pane it exits 0 and records nothing. The hook never blocks the prompt (exit 0 on every error, error written to the event log).
- The PM agent links an answer with `mesh work reply <item> --event <id>`. Only hook-recorded events are accepted, so the PM cannot fabricate an operator answer; freshness and single use (D63) apply to the event timestamp exactly as to a Slack reply.
- Slack replies keep the `attach` path (section 9). Both channels feed the same `replies[]` record with `source`.
- Tests: hook in a lead pane records nothing; PM pane records an event; `reply` with an unknown or reused event id is refused; a terminal reply unlocks blocked(limit) once.

## 10. Invariants (each is a test)

1. Every verb x caller x state combination not in the table is refused and recorded.
2. Every non-terminal state has an exit that is neither `cancel` nor `override`.
3. Every state is reachable from `draft`.
4. `limit` is never set by a caller.
5. The retry limit holds whichever verb returns the item to `ready`.
6. `verified` never exists without evidence bound to the current commit, contract version and config hash.
7. `closed` never happens with unacknowledged replies or a revalidate flag.
8. A dependency is satisfied only by `verified` or `closed`; cycles and unknown ids are refused at `ready`.
9. A split parent closes only through its own verification and the normal `close` gates.
10. A user reply unlocks `blocked(limit)` at most once and only if it postdates the limit event.
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

## 12. Open items (eng review round 3, Codex)

- ~~O3-7~~ resolved by D64 (operator answers from the PM terminal through a prompt hook; section 9a).
- **O3-8** Re-split after a cancelled child: cancelled child stays linked; remaining active children have no disposition.
- **O3-9** Environment counter event: the table counts a passing `unblock`; the failure registry counts a refused precheck. Choose one.
