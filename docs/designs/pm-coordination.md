# PM coordination: lead uids, one-call issue, commit fingerprints (D137-D140, 0.3.0)

> Status: in 0.3.0 scope by operator instruction 2026-11-25. D140 enforcement answered by the operator the same day (ship the guard). The details of D137-D140 were written by the PM and not reviewed one by one.
> Source: plugin findings adopt1 PF-010, PF-011, PF-012 (`docs/reports/fleet-findings.md`). Code receipts are from the working tree on 2026-11-25.

## D137 (PF-010) - lead uids in the project view
- Problem: `mesh_status` returns `leads: [{role, present}]` (mesh/tools.py:510) although `live_members` already holds each `Agent.uid` (mesh/projmux.py:47). To answer Ship, a PM spent five calls and searched its own transcript.
- Decision: each `leads` row gains `uids` (every live agent of that role, `[]` when none, `null` when projmux is unavailable). The view also gains `pm: {uid, running}` from the pin. Existing keys keep name and shape (Q3 of D135). More than one uid for a role is shown as is: mesh does not pick one.
- No new projmux call: the D135 snapshot already fetches agents once. Uids are already visible to every pane through `projmux get agents`, so this widens nothing.
- Placement: WO-0045. Not WO-0044, whose output must stay byte-identical.

## D138 (PF-011) - `work_issue`: new, ready, dispatch and render in one call
- Problem: one order is `work_new` -> `work_transition ready` -> `work_dispatch` -> `tracker_render`. The generated tracker went stale when the last was forgotten, and a placeholder draft was created only to make the next id match a file already named after it.
- The placeholder was avoidable: `Scope.work_order` (mesh/model.py:239) accepts any path, and `dispatch.work_order_path` uses it before the id default. Nothing says so. Decision (a): the dispatch and run skills tell the PM to name an order file by topic and pass `scope.work_order`. Ids stay sequential; no caller-chosen id.
- Decision (b): a PM-only tool `work_issue(contract, dry_run)` that runs the four steps through the EXISTING table rows, in order, each recorded exactly as today. It adds no row, guard or phase, so the state model and its pinned hash do not change.
- It stops at the first refusal and returns `{item_id, reached: "draft"|"ready"|"dispatched", refusal, rendered}`. It never cancels or rolls back; the PM continues with the ordinary verbs on that id.
- It checks the order file (`layers.contained_file`) BEFORE `work_new`, so a missing file costs no id.
- When a depends is not `verified` or `closed` it stops at `ready` (the run skill's rule). `start` already refuses open depends (`depends_satisfied`), so this only avoids an order that cannot start.
- A repeated call must not create a second item: the caller passes `request_key`; a key already used returns the item it created.

## D139 (PF-012) - commit request and commit check
- Problem: the PM computes `git hash-object` per file, pastes the values into a message, and compares them again after the commit.
- `commit_request(item_id, message, paths)` (PM only, not read-only, so D108/D109 apply): records `{request_id, item_id, message, head, files: {path: {blob, mode} | "deleted"}}`. It refuses a path outside the root, a symlink, a secret-like path (`.env*`, `.auth/`, `*.sqlite`) and an empty list. A path may be declared `any` only for `.mesh/state.json`.
- `commit_check(request_id, commit)` (PM and the Ship lead): compares the commit with its parent through `git diff-tree`, and returns `match`, or the exact `extra`, `missing` and `different` paths. Evidence comes from git, not from either side's message.
- Storage: `.mesh/commit-requests.jsonl`, git-ignored, with the same `Write`/`Edit` deny rules as the other mesh logs. Not `state.json`: leads keep running the older server until they restart, and a field they do not know could make them refuse the whole state file.
- This DETECTS a wrong commit; it does not prevent one. A guard on `submit` would change the transition table and needs a formal re-sync, so it is not in 0.3.0.
- Replies need no tool: the projmux envelope already carries `replyAction` with the exact argv. The role definitions say "use the envelope's `replyAction` argv as given".

## D140 (PF-012, second part) - requests between leads go through the PM
- Problem: leads messaged Ship directly for commits because orders told them to; nothing said otherwise. `agents/pm-orchestrator.md` reserves cross-role DISPATCH for the PM, but no role-lead text covers a REQUEST.
- Decision (text, now): the role-lead invariant block (7 identical files) gains: "A request to another role (a commit request to Ship, a question that asks for work) goes to the PM, not to that role. Answering a message you received is allowed." Orders and the dispatch header stop telling leads to contact Ship.
- Enforcement (operator 2026-11-25: ship the guard in 0.3.0, together with the text). A `PreToolUse` guard on `Bash`, command `.mesh/bin/mesh guard`, merged into `.claude/settings.json` by `mesh_init` next to the existing hook.
  - G1 a lead sends projmux messages only to the pinned PM or to its own `worker-of=<role>` agents; a `--reply-to` answer is allowed.
  - G2 `git push` is allowed for the Ship role only (the PM and every other lead are denied). It does not replace DEC-0017: Ship still needs the operator's approval in its own pane. `git commit` is NOT restricted by default: the dispatch header tells every lead to submit "your commit". A project may opt in to Ship-only commits.
  - G3 `projmux agent message send --source` must name the calling pane's own agent. Live case 2026-11-25: an unlabelled second pane resumed on the PM's session sent three messages under the PM's uid; projmux documents `--source` as an anchor, not authentication.
  - Cost: a cheap string test first; only a command that could match triggers a projmux lookup.
  - Lookup failure on a matching command: `ask` (neither silently open nor closed). An unmatched command is never delayed or blocked.
  - A guardrail, not a sandbox: an interpreter or a script file is not read, and a session that edits `.claude/settings.json` can remove it. The docs say so; no security claim rests on it.
  - Gate before building (security lead): a threat model and measured answers to: does the hook fire inside in-process subagents; what does `ask` do in auto mode and in headless runs; hook latency with and without a lookup; what happens when a project already has its own PreToolUse guard (both run; the stricter answer must win).
  - Reference: one project's own guard. Its log records that 13 of 19 extra probes of the first, regex-based version behaved wrongly (`X=1 git push`, `bash -c`, `timeout`, `git --no-pager push` passed), so commands are parsed to argv and the probe corpus comes first.

## Tests (TDD, red captured first)
Unit: 
- T1 view: 0, 1 and 2 live agents for a role, and projmux unavailable -> `uids` `[]`, one, two, `null`; `pm.running` for a Running, Offline and missing pinned agent.
- T2 `work_issue`: the recorded rows equal those of the separate calls; one test per stopping point (unknown type, missing order file with no id consumed, open depends, no live lead); the same `request_key` twice -> one item; non-PM caller refused; `dry_run` writes nothing.
- T3 `commit_request`: outside path, symlink, secret-like path, empty list, non-PM refused; a deleted file and a mode change recorded.
- T4 `commit_check`: match; one extra, missing and changed file; a mode-only change; a deletion; a merge commit and an unknown sha refused; a file edited after the request is reported as different.
- T5 text: the invariant line is identical in the 7 files; no shipped text tells a lead to message Ship for a commit.
- T6 guard: a probe corpus written BEFORE the parser (assignment prefixes, wrappers, `bash -c`, global git options, heredocs, quoted text that only mentions a command, aliases); expected verdicts are literals. Each rule allows and denies for lead, Ship, PM, unlabelled and no-projmux callers; lookup failure gives `ask`; an unmatched command makes zero projmux calls; an existing project hook entry is kept.
E2E (qa, live): E8 a lead's message to another lead is denied and one to the PM is delivered; a worker subagent is covered or the gap is recorded; E5 the PM reads Ship's uid from one `mesh_status` call; E6 `work_issue` -> the lead receives the order and the tracker shows it; E7 a request, a Ship commit with one extra file -> `commit_check` names that file.

## Placement
D137 in WO-0045. D138, D139 and the D140 text in WO-0047, after WO-0045 (same files: mesh/tools.py, the tool list, the skills). The D140 guard in WO-0048 (security threat model first, then backend, then qa), after WO-0047. The tool count tests move with it.
