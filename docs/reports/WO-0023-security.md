# WO-0023 — security review of the MCP tool surface (security)

Status: **final gate on 382cf02: REWORK (one 🟠, F13) or PASS once the PM accepts F13 as a named residual** (see "Verdict"). Findings go to the PM; fixes are backend work.

- Target: commit `0501f50`. The probes ran on a frozen `git archive` copy outside the repo. Its `mesh/*.py` and `.mcp.json` hashes equal the `0501f50` blobs (`git show | sha256sum`, empty diff), and `sha256sum -c` still passed after the runs.
- Durable evidence (outside the repo): `~/.local/state/supervisor-mesh-security/WO-0023/`
  - `brief.md`, `brief-2.md`: the worker orders.
  - `evidence/`: the worker's captured outputs.
  - `harness/`, `guardtests/`: probe scripts and unittest guard tests.
  - `lead-rerun/`: the lead's own re-runs.
  - `findings.md`: the worker's full findings.
- Method: the lead dispatched two same-role workers.
  - Worker 1 ran P1-P5 and P7-P9 as probe scripts against mesh's own test fakes.
  - Worker 1's session stopped on P6, P10 and P11 (a safety-filter stop while writing those probe scripts). It did not retry.
  - On PM instruction, P6, P10 and P11 were re-scoped as defensive unittest guard tests of mesh's own refusal paths and given to worker 2. None of those tests was stopped.
  - Every probe used fake projmux / tmux / claude and throwaway projects with a temp HOME. No real message, keypress or install happened.
  - The lead re-ran every harness and the guard suite, and reproduced F1 live (read-only) and F8 by hand.

## Probe table
| probe | result | lead re-run evidence |
|---|---|---|
| P1 caller identity | FINDING F1, F2; P1b PASS | `lead-rerun/p1.log`, `p1c.log`, `F1-live-readonly.txt` |
| P2 operator-event forgery | FINDING F3; residual = D96 accepted risk | `lead-rerun/p2.log` |
| P3 state.json tampering | NOTE F6 (D96 same-user residual) | `lead-rerun/p3.log` |
| P4 argv injection | PASS (projmux, git); FINDING F5 (CLI) | `lead-rerun/p4.log` |
| P5 path traversal and symlinks | FINDING F4 | `lead-rerun/p5.log` |
| P6 verify escape (V1-V7) | PASS V1, V3-V5, V6a; FINDING F9 (V6b); ACCEPTED-RISK V7 (D99); FACT V2 | `lead-rerun/G-run.txt` |
| P7 leaks | PASS | `lead-rerun/p7.log` |
| P8 lock/CAS race | PASS | `lead-rerun/p8.log` |
| P9 JSON-RPC robustness | FINDING F8; everything else PASS | `lead-rerun/p9.log`, `F8.out`, `F8.err` |
| P10 pane_command (C1-C6) | PASS C1, C2, C4-C6; FINDING F10 (C3); ACCEPTED-RISK `/config` when named (D86); FACT C5 | `lead-rerun/G-run.txt` |
| P11 companions_install (I1-I7) | PASS I1-I4, I6, I7; I5 PASS (mesh) plus a FACT (the CLI's own project-scope registry write) | `lead-rerun/G-run.txt` |

Lead re-run totals:
- Harness scripts p1, p1c, p2, p3, p4, p5, p7, p8 and p9 each exited 0, with the same outputs as the worker's evidence.
- Guard suite: `Ran 48 tests`, `FAILED (failures=5)`, exit 1. The failures are exactly F10 (4 subtests) and F9 (1 test).
- `ls /tmp | grep -c mesh-wt-` gave 0.

## Findings (ranked) and PM decisions
| # | sev | where (0501f50) | defect | repro (output) | decision |
|---|---|---|---|---|---|
| F1 | 🔴 | `mesh/identity.py:34-54` | identity rests on the env var `PMX_INTERNAL_ACTIVATION_PANE_UID` alone, so any lead becomes PM for every tool | live, read-only, real projmux: `resolve(env + PM pane uid)` gives `Caller(kind=PM, agent_uid=<pm>)`, while the lead's own env gives `LEAD security`. Fake projmux: spoofed `work_new` exit 0 to draft, then `ready` exit 0 | D108 ancestry check, fail-closed without a claude pid |
| F2 | 🔴 | `identity.resolve` (any live `role=pm`) | `projmux label agent [<ref>] role=pm` is open to any agent; a relabelled lead passes the D96 ancestry check from its own pane and records operator events | `p1c.log`: identity pm with two `role=pm` agents; `ev-000001` authored by the lead | D109 PM uid pin and refuse more than one live pm; D114 re-pin |
| F3 | 🟠 | `mesh/cli.py:149-171`, `init.py:336` | the PM model's own Bash forges operator events; the deny glob `Bash(*mesh* event record*)` is bypassed (`event  record`, `ev''ent record`, `python3 -c` into `cli.main` or `Store`) | `p2.log`: forged event recorded | D110 session + latest-transcript-prompt match with per-entry dedup; glob documented as advisory |
| F3b | 🟠 | design (D110) | a model can type into its own pane (`tmux send-keys -t $TMUX_PANE ...`), which makes a real user prompt that D110 accepts | design review, to be probed in the delta run | D113 one-time code in a tmux popup for high-impact events, plus advisory send-keys and paste-buffer deny rules |
| F4 | 🟡 | `mesh/init.py:348-391` | `mesh_init` follows a symlinked `.claude/settings.json`, `.claude/`, `.mesh/` or `docs/` and writes outside the project; the dry run shows a plain MERGE; against `~/.claude/settings.json` that would change user scope | `p5.log`: outside file sha changed; files created in the outside dir | D111 realpath containment |
| F5 | 🟡 | `mesh/cli.py:73` | `add_subparsers(dest="command")` collides with `pane_command --command`: KeyError traceback with absolute paths, and `--command work_list` runs work_list | `P4b-cli-command-dest-collision.txt` | D112 |
| F8 | 🟡 | `mesh/mcp_server.py:134-137` | deeply nested JSON raises `RecursionError` (not caught), the server exits, and the traceback with paths goes to stderr | lead: initialize, then `'['*10000`, then ping → exit 1, ping unanswered, 3 `/home/` lines on stderr | WO-0030 (PM) |
| F9 | 🟡 | `mesh/layers.py:252-262` | `fail_if_output` regexes from config run unbounded in the server thread (ReDoS) | `(a+)+$`: n=22 0.86 s, n=28 >30 s (killed) | to decide (PM): bound the match (child with timeout, line cap) or use literal substrings |
| F10 | 🟡 | `mesh/pane.py:200-205` | the tmux target (projmux `runtimeID`) is not checked to be `%<digits>`; `sess:1`, `-t`, `%1;x`, `=s:0.0` are typed into plus Enter | `G-run.txt` C3: 4 subtests fail; the fake tmux got `send-keys -t sess:1 -l -- /compact`, then Enter | to decide (PM): `fullmatch(r"%\d+")`, else `target_invalid` |
| F6 | ℹ️ | `mesh/model.py:575-582` | a hand-made attempt with evidence kind `override` passes binding and closes, and `operator_interventions` stays 0 | `p3.log` | D112 |
| F7 | ℹ️ | store, `model.py` | a duplicate event seq shadows a genuine event (DoS); `ITEM_ID_PATTERN` accepts Unicode digits (harmless) | `p2.log`, `p4.log` | D112 (dup seq) |

## Facts and residual risks recorded (no spec rule broken)
- D86 accepted: `/config` is typed when the operator names it (C1).
- D99 accepted: verify writes outside the worktree (V7).
- D96 residual: same-user writers of `state.json` and the transcript.
- V2: a `setsid` child of a verify command survives `run_at` (it is outside the killed process group). This is consistent with D99; the PM may extend the D99 text.
- C5: an abort after typing sends `C-u` into the pane even while a trust or permission dialog shows (`pane.py:220-222`). It is not Enter, but it is a keypress into a dialog. Suggested fix: send `C-u` only when the prompt line is visible.
- C5 (static): between the re-check capture and `send-keys -l` a dialog can open, and the literal text then goes into it. This is inherent to screen-scraping, so it is recorded as a residual.
- P11/I5: a project-scope install changes `~/.claude/plugins/installed_plugins.json` (the Claude CLI writes a `scope=project` entry). That is the CLI's own registry, not mesh. WO-0024 should not count it as a "user scope change".
- User state during the run: `~/.claude/settings.json` and `installed_plugins.json` were unchanged. `known_marketplaces.json` changed twice. Both changes match Claude Code's marketplace auto-refresh (the `lastUpdated` fields of two marketplaces), and the fake logs show 0 claude calls. A whole-file hash of that file is not a stable oracle (forwarded to QA by the PM).

## Passed checks (summary)
- P1b: an absent, unknown or ambiguous pane, an empty agentUID, a PM that is not Running, the cwd prefix trick, a subdirectory cwd, a bad-case label and projmux failing all resolve UNKNOWN. Reads work and writes are refused `unknown_caller`.
- P4: free text reaches projmux as one argv element after `--`. `SHA_PATTERN` refuses hostile refs before git runs. `shell=True` has 0 hits, and `subprocess` is imported only in `runner.py`.
- P7: stdout carries JSON-RPC only. The canary, `/home/` and `Traceback` are absent from stdout and stderr. The companions status is three booleans only.
- P8: 150 race rounds, each with exactly one winner and version +1. kill -9 ×50 left no torn `state.json`, no stale tmp file, and no blocking lock.
- P9: every malformed-input case except deep nesting returns a clean JSON-RPC error, and the server keeps answering; a 50 MB line was answered.
- P10: event binding (whole token), the proposal path (`P-<n>`), the target project, cwd and own-pane checks, control and bidi characters, idle-state checks and receipt type separation all hold. Accepted text is typed with `send-keys -l --`, then a separate Enter.
- P11: no install without a fresh naming PM event. A single-plugin event installs only that plugin. Extra args are `invalid_args`. Every argv carries `--scope project`. Config skills never reach argv. The receipt records versions.

## Delta 1 — a1bb251 (WO-0028 + WO-0030 + D117)
- Frozen copies `src-a1bb251/` and `plug-a1bb251/` (git archive; `sha256sum -c harness/src-a1bb251.sha` OK). Tests are in `deltatests/` (brief 3).
- Lead re-run:
  - `test_delta_*.py`: `Ran 48 tests`, `FAILED (failures=3)`, exit 1 (`lead-rerun/D-run-2.txt`).
  - `test_g_*.py` (the brief-2 guards on the new code): `Ran 48 tests`, `OK`, exit 0 (`lead-rerun/D-run-2-g.txt`).
  - Total 96 tests, 3 failures: F11 and F12 (×2). `ls /tmp | grep -c mesh-wt-` gave 0.

| check | result |
|---|---|
| R1-R10 (F1-F10 regressions) | PASS: F1-F10 fixed. R3 same-user append to the real transcript = ACCEPTED-RISK (D96/D110) |
| brief-2 guards V/C/I on a1bb251 | PASS, including the old F9 (literal match, 0.002 s) and F10. No C-u into a dialog |
| N1a-d D113 code (argv/env/files/logs/result, popup on the PM client only, no client refused, wrong/none/timeout, single use) | PASS. FACT: 8 characters from a 32-letter alphabet (~40 bits), one attempt per popup |
| N1e code file readable, answer forgeable | FINDING F12 |
| N2 privileged scope | PASS for the 7 names. FACT: an exact, case-sensitive match, so `/CONFIG`, `/Config`, `/plugins`, `/settings`, `/allowed-tools` and others are typed without a code (Claude Code's own alias handling not asserted) |
| N3 re-pin preconditions (D114) | PASS |
| N4 fabricated session pin (D117-2) | FINDING F11 |
| N5 deny rules | PASS: 9 rules (event record, tmux send-keys, tmux paste-buffer, Write/Edit on state.json, events.jsonl, refusals.jsonl); config.toml stays editable (D107). FACT: D119 `Bash(*agent-hook*ingest*)` absent (the tail commit adds it) |
| N6 work-order path (D117-4) | FACT: missing, absolute-outside, `../` and symlinked work orders are all sent (the tail commit adds the check) |
| stale `/tmp/mesh-confirm-879i9j51` (19:20, plaintext `code` 0600, not from these runs) | FACT: a killed confirm leaves the plaintext code in tmp and nothing reaps it |

| # | sev | where (a1bb251) | defect | repro (output) | decision |
|---|---|---|---|---|---|
| F11 | 🔴 | `mesh/cli.py:168-169`, `:203-204`; `layers.py:476-510` | before the PM session is pinned, a fabricated `<session>.jsonl` under `~/.claude/projects/<slug>/` plus a matching hook stdin is recorded, pins the fake session and locks out the real one (`session_not_pm`); further fabricated prompts are accepted | `D-N4.txt`: `fabricated_accepted: true`, `final_pm_session: fab-0001`, `errors_after_real: [session_not_pm]` | D118: every event must match the pinned PM agent's live projmux SessionID + TranscriptPath, fail closed. D119: forged `agent-hook ingest` = D96 residual + advisory deny |
| F12 | 🟠 | `mesh/layers.py:525-549`, `confirm.py:48-52` | the plaintext code sits in `<tmp>/mesh-confirm-*/code`, and the work dir is in the popup argv; a same-user reader forges `answer`; a pre-created `answer.tmp` makes the helper fail (`FileExistsError`), and the forged answer then approves even when the operator refuses | `D-run-2.txt` N1e: `unexpectedly None: the operator refused, yet the forged answer approved` | D121 (revised): mesh keeps the hash; the code reaches the popup over a one-shot FIFO; RAW typed text comes back over a second FIFO; mesh compares; threat model stated (stops a model acting through mesh tools and the advisory denies, not arbitrary same-user code). Steal-and-relay is a named ACCEPTED-RISK |

## Final gate — 382cf02 (D118-D123 + D122 reaper)
- Frozen full archive `plug-382cf02/`, 61 hashes in `harness/plug-382cf02.sha`, `sha256sum -c` OK. Tests are in `finaltests/` (brief 4).
- Lead re-run: `Ran 116 tests`, `FAILED (failures=1)`, exit 1 (`lead-rerun/F-run.txt`). The one failure is F13. Afterwards there was no `mesh-wt-*` or `mesh-confirm-*` dir in /tmp.

| check | result |
|---|---|
| regression: R1-R10, brief-2 V/C/I, N3, N5 | PASS |
| D119 deny `Bash(*agent-hook*ingest*)` | PASS (present with the other advisory rules) |
| N6 work-order path: missing, absolute-outside, `../`, symlink out of root | PASS (refused; a real one is sent) |
| N4 / D118 fabricated session, no SessionID (fail closed), D120 re-pin session | PASS. FACT: a symlinked transcript path that resolves (realpath) to the pinned file is accepted |
| N1 / D121 + D122(1): forged answer, pre-existing FIFO or file, second writer, after popup exit, stolen code, helper failure, no plaintext on disk/argv/env, dir removed | PASS except F13 |
| steal-and-relay | ACCEPTED-RISK (named in D121): approved, and the operator saw the real code |
| N2 / D122(2): case fold + the 4 new names; `/compact` without a code | PASS. FACT: fullwidth and NFKC look-alikes are `command_invalid` |
| D123: never-appearing prompt, double citation, older-prompt replay, cite after a newer prompt, ts bound, one usable event per entry | PASS. FACT: a later Bash-made candidate with the same text as the operator's prompt binds instead of the hook's (newest binds). The authorized text is the operator's own, so the effect is the same approval |
| structural origin rule: human string or text list accepted; peer, task-notification, auto-continuation, no-origin, tool_result, sidechain, isMeta, unknown kind refused or skipped; wrapper-like text from a human counts as the operator's; no displacement | PASS |
| D122(3) reaper: dead-owner marked dir reaped; a live owner, no marker, another mode, a symlink and a plain file are kept | PASS. FACT: confirm dirs from before the marker are never reaped (the one stale dir has since gone) |

| # | sev | where (382cf02) | defect | repro (output) | suggested fix |
|---|---|---|---|---|---|
| F13 | 🟠 | `mesh/layers.py:734-746` (`_is_helper`), `:775-792` (`_judge`); `mesh/confirm.py:73-78` | `_is_helper` accepts ANY live pid whose argv[1] is the helper path. A same-user process that runs its own copy of the helper opens the code FIFO first, takes the code and answers. mesh APPROVES while the operator's real popup shows "no code received (D121); refused". D121 counts a stolen code as a *visible failure*, but the failure the operator sees here is a refusal while the action goes through | `F-run.txt`: `test_n1_a_thief_running_its_own_helper_instance` FAIL (approved) | judge only after the `display-popup -E` mesh started has returned; refuse when more than one helper pid ever held either FIFO; refuse when any helper reports "no code" (a third outcome on FIFO 2, or its exit status). This stays same-user (D96/D121), but it turns the misleading case into a refusal |

## Verdict (382cf02)
**REWORK, one item: F13 🟠.** Every other gate item passes: F1-F12 are fixed, and the D118-D123 controls hold. F13 is inside the stated same-user threat model (D96/D121), so the PM and operator may instead accept it as a named residual next to steal-and-relay. With that decision recorded, or with the fix and a re-run of `test_n1_a_thief_running_its_own_helper_instance`, this order is PASS.

## Delta run (PASS gate) — planned against the WO-0028/WO-0030 commit
1. Re-run the harness and guard suite. The F1-F10 tests must flip to refusals.
2. D108: fail closed without a claude pid.
3. D109/D114: pin, more than one pm, re-pin preconditions (pinned PM still Running, 0 or 2+ live pm, no code).
4. D110: an older real prompt replayed, and per-entry dedup.
5. D113: the code never appears in argv, env, files, tmux buffers, options or environment, logs or MCP output. The popup is not readable via `capture-pane`, `display -p` or a second client. The comparison happens in mesh, not through an ok-flag file. Single use, bounded attempts, expiry. The popup opens only on the PM client.
6. D107: deny rules narrowed and `config.toml` editable. D113 advisory deny rules present.
