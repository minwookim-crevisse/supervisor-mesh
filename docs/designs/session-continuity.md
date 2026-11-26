# Session continuity — PM restarts and context loss (0.3.0 draft)

> Status: eng-reviewed 2026-11-20 (plan-eng-review, operator answers D1-D4 below). D127-D129 as amended here go into `docs/designs/mcp-server-spec.md` when the 0.3.0 work is ordered.
> Context: operator questions 2026-11-20 ("the PM session dies and its session id changes: can supervisor-mesh record and prevent this?" and "if the session id is not pinned, is the accumulated context lost?").

## Problem

Two different things are at stake when a Claude session restarts:

1. **Authority.** Who may use PM verbs and whose prompts count as operator events. Today this is bound to the projmux agent uid (D109 pin), and the Claude session is re-read from projmux on every event (D118). A restart in the same agent keeps authority. A new agent needs a re-pin (D114).
2. **Context.** What the PM remembers. It lives in the Claude transcript of one session id. `projmux agent resume` / `claude --resume` reopen the same session, so context survives. `/clear` or a fresh session starts a new session id, and the context is gone, whatever mesh pins.

Mesh cannot keep a context that Claude dropped. It can make the loss visible and the recovery mechanical. Facts that survive any restart are already on disk: `.mesh/state.json`, `events.jsonl`, `refusals.jsonl`, the decision log, the tracker, work orders and reports.

Gaps today:
- G1. A PM session change is never recorded. Nobody can tell later when the PM lost its context, or that an event recorded before the change can no longer be proven (it fails closed as `event_unproven`).
- G2. A fresh PM has no defined first steps. Recovery depends on the model happening to read `AGENTS.md` "Restore order". Background watchers (for example the Slack bridge) that died with the session are not restarted.
- G3. Before a risky step, nothing writes down what the PM is waiting for, so a context reset at that moment loses the open thread (for example "push approved, waiting for Ship").
- G4. A lead whose projmux registration broke (seen 2026-11-18 after a Claude Code update) is reported only as `lead_absent` or `lead_quiet`, with no fix hint.

## Proposed decisions

### D127 — detect PM session changes, read-only (G1; review D1 = A)
- No new state field and no new write. Every hook-recorded candidate already stores `sha256(session_id)` (`mesh/cli.py:208`), and that is the only session data mesh keeps (D110/D118 privacy rule).
- `mesh_status` computes, read-only: (a) the session hash of the latest operator event, (b) the hash of the session projmux reports for the pinned PM right now (`Projmux.claude_session`), and (c) the candidates of older session hashes that were never cited.
- It returns `pm_session: {context: "same" | "reset", since_event: <seq of the first event under the current hash, or null>, unused_events: n}`. `reset` appears as soon as projmux reports a new session, before the operator's first prompt in it. `mesh_metrics` counts session-hash changes across events as `pm_context_resets`.
- Not a gate: nothing new is refused. D118 already refuses old candidates at citation.

### D128 — a fixed recovery routine (G2, G4; review D2 = A, D3 = A)
- `agents/pm-orchestrator.md` gains "On a fresh session": (1) read the decision log, newest first; (2) call `mesh_status`; (3) read `docs/handoff.md` (D129); (4) for each pending action or stall, act or ask; (5) if the project has the Slack bridge set up (`scripts/slack/slack_bridge.py` and a `.env` with its keys), restart exactly `python3 scripts/slack/slack_bridge.py watch` (the command slack-setup already pre-approves). No generic watcher list: `.mesh/config.toml` is editable by other sessions (D107), so a configurable command would be a path to make a fresh PM run planted commands.
- Lead registration hint: `work_dispatch` keeps the refusal reason it already classifies (D93) on the dispatch's pending action. When the latest refusal for a lead is the projmux readiness refusal ("registration lease probe did not answer"), `mesh_status` adds to that lead's stall: `hint: "in that pane: /exit, then projmux agent resume uid:<agent> -w uid:<window>"`. projmux `describe agent` has no live registration signal (`status.activation` is a stale first observation), so it is not used.
- The routine is text for the model plus data from `mesh_status`; mesh starts no process.

### D129 — a PM handoff note (G3; review D4 = A)
- `docs/handoff.md`, PM-owned, at most about 15 lines: "waiting for", "next step", "operator decisions pending". The PM rewrites it before a risky step (release, push request, operator decision, long wait) and after it resolves.
- `mesh_status` shows the handoff file's age; when `pm_session.context == "reset"` and the handoff is older than the last session change, it says so.
- Plain markdown, not machine state: it carries intent, which `state.json` does not.

## Out of scope
- Restoring a lost transcript or summarizing it into a new session.
- Pinning the Claude session id (it would lock the operator out after `/clear` and protect nothing more than D118 does).
- Auto-restarting lead or watcher processes from mesh.

## Tests
- D127: candidates under two session hashes give `context: "reset"`, the right `since_event` and `unused_events`; a projmux-reported session that differs from the latest event's hash gives `reset` with no new event; the same hash gives `same`; `.mesh/` holds no raw session id; `mesh_metrics.pm_context_resets` counts hash changes.
- D128: a dispatch refused with the readiness reason yields the hint with the agent and window uids; a transient refusal of another kind yields none; the routine text in `agents/pm-orchestrator.md` names the exact Slack command and no configurable watcher.
- D129: the handoff-age warning appears only when `context == "reset"` and `docs/handoff.md` is older than the first event under the current session (or missing).

## Review answers
- Q1 (hook as writer): moot. D127 needs no write; the data is already recorded per candidate.
- Q2 (`[watchers]` in config.toml): dropped (D3). Note: `Config.verify_hash` (`mesh/model.py:847`) covers only commands, evidence and prepare, so new config keys would not have moved the D100 pin anyway.
- Q3 (handoff location): `docs/handoff.md` (D4).

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|---|---|---|---|---|---|
| Eng Review | operator: /plan-eng-review | lock the 0.3.0 session-continuity plan before ordering | 1 | CLEAR | 4 findings, 4 resolved (D1-D4), 1 factual note (Q2) |

- F1 [P2] (confidence 9/10) `mesh/cli.py:208` stores the session hash per candidate: D127's new field and record were redundant. Resolved D1 = A (read-only).
- F2 [P2] (confidence 8/10) projmux `status.activation` is not live (state `acknowledged`, observed 2026-10-28): D128's probe had no signal. Resolved D2 = A (dispatch refusal reason).
- F3 [P2] (confidence 7/10) a `[watchers]` list in the editable `.mesh/config.toml` would let another session plant commands a fresh PM runs. Resolved D3 = A (known Slack command only).
- F4 [P3] (confidence 6/10) handoff location was open. Resolved D4 = A.
- Outside voice: not run (small plan, operator-steered review).
- Not run: `/mattpocock-skills:improve-codebase-architecture` is operator-only (`disable-model-invocation`); proposed as the next step for the code placement in `mesh/`.

VERDICT: CLEARED for a 0.3.0 work order.

NO UNRESOLVED DECISIONS
