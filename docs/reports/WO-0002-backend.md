# WO-0002 — Correctness review: Slack bridge 0.1.2 draft (backend)

- Date: 2026-10-14 · Role: backend lead · Model: lead + one same-role worker + lead review
- Scope: `templates/slack/slack_bridge.py`, `env.example`, `skills/slack-setup/SKILL.md`, `tests/test_slack.py`, DEC-0005, QA report (Slack section), README Slack lines, against findings A–J (`<test-repo-c>/docs/reports/slack-bridge-improvement-request.md`).
- Read-only: no repo file changed except this report. No network calls to Slack.
- Every finding below was **re-run by the lead**. Probes live in the session scratchpad (`lead_probe.py`, `lead_probe2.py`, `lead_probe3.py`). Each probe imports the real `slack_bridge` and either injects a fake `Slack.call` that follows real Slack semantics (history newest-first, `oldest` exclusive, replies return the parent first) or points the bridge at a local HTTP server.
- Baseline: `python3 -m unittest discover -s tests -p 'test_slack.py'` → `Ran 15 tests … OK`, exit 0. None of the failure paths below are exercised by the suite.

## Findings

| id | sev | file:line | what is wrong | evidence (lead re-run) | suggested fix |
|---|---|---|---|---|---|
| R1 | 🔴 | slack_bridge.py:172,193 | `threads` is the live state dict. `threads[ts] = latest` is written per parent **before** the poll finishes. If a later `conversations.replies` fails (5xx, or 429 after retries), the poll aborts, the collected lines are discarded, and that thread's position has already advanced. Next poll the reply is never fetched: **permanent message loss**. | `P1 poll1 raised: conversations.replies: HTTP 503 \| in-memory threads now: {'…621.017754': '…711.017754'}` → `P1 poll2 (retry) ['… reply-1']`. reply-0 is lost. | Work on a copy (`threads = dict(state.get("threads", {}))`) and commit `floor` and `threads` together only after the whole poll succeeds. |
| R2 | 🟠 | slack_bridge.py:193 | The thread position is set from the history snapshot's `latest_reply`, not from the newest reply actually fetched. A reply landing between the history call and the replies call is emitted twice. | `R2 poll1 [… r1, … r2 raced]` → `R2 poll2 [… r2 raced]`. Severity 🟠: the window is milliseconds and delivery is already at-least-once (R15). It is still a duplicated order. | `threads[ts] = max(latest, newest fetched reply ts)`, compared numerically. |
| R3 | 🔴 | slack_bridge.py:75-86, 210-215, 241-245 | `call` converts only `HTTPError` and `URLError`. urllib does not wrap errors raised in `getresponse()` or during the read, and the body may not be JSON. `cmd_watch` and `main` catch only `SlackError`, so the watcher **dies with a traceback** on ordinary faults. | `P3 UNCAUGHT by watch loop: TimeoutError timed out` (server accepts and never answers). `P6 UNCAUGHT: JSONDecodeError` (200 with an HTML body). The worker also reproduced `RemoteDisconnected`. | In `call`, map `OSError`, `http.client.HTTPException` and `ValueError` to `SlackError`; retry 5xx and network errors with backoff. In the watch loop, add a last-resort `except Exception` that logs and continues. |
| R4 | 🟠 | slack_bridge.py:145-148, 206, 213 | Two watchers on one state: no lock, and both write the same `state.tmp`. `replace()` raises `FileNotFoundError`, which is uncaught, so the watcher crashes. Both watchers also emit every message. | `P5 save_state errors with 2 concurrent writers: 2257 {'FileNotFoundError'}`. Severity 🟠: the Monitor spec says an expired monitor "is killed", so a re-arm does not overlap. Two panes running `watch` for the same project does overlap. | `fcntl.flock` on a `watch.lock` in the state directory for the life of `watch`; a second watcher exits with a clear message. Use a unique temp name, fsync, then `os.replace`. |
| R5 | 🔴 | slack_bridge.py:184-185 vs 205 | The backlog cap moves only `floor`. A thread already in state uses its old saved position, so after a long outage **stale thread orders are replayed**. This is the exact behaviour B was meant to prevent. | P2: watcher down about 5 h, cap 1 h, reply 4 h old → `P2 ['[Slack thread=… ] stale 4h-old thread order']`. | Clamp per thread at watch start: `since = max(seen, start_floor)`. Do not use the running `floor`; that would lose replies older than a newer top-level message in the same interval. |
| R6 | 🔴 | slack_bridge.py:104-106 | `token.split("-",1)[0]` returns the **whole value** when it contains no `-` (e.g. a pasted signing secret, which is hex). It is printed to stdout before `auth.test` runs. | `R6 printed whole value: True`. Correction to the worker: against real Slack, `auth.test` then fails and the run exits 1, not 0. The "exit 0" was a mock artifact. The leak happens either way. Overlaps WO-0001. | Print a prefix only when it matches `^xox[a-z]-`; otherwise print "does not look like a Slack bot token" and never echo the value. |
| R7 | 🟠 | slack_bridge.py:173-174 | `fetch_oldest = min(floor, now - ttl)` is effectively `now - 86400`, so **every poll pages through 24 h of history** just to see `latest_reply`. | 1,000 messages in 24 h → `R7 history calls in one steady-state poll: 5` (15/min at a 20 s poll), plus the replies calls. Fits Tier 3 (~50/min) only for quiet channels. **Unverified:** Slack's 2025 limit of 1 req/min with `limit` ≤ 15 for non-Marketplace distributed apps. Internal custom apps are believed exempt; if it applied, every poll would 429. | Fetch new history from `floor`. Keep the active parents in state and re-check their replies directly, either per poll for the parents only or on a slower cadence. Back off after 429s. |
| R8 | 🟠 | slack_bridge.py:138-142, 205 | A missing, corrupt or unreadable state file silently becomes `{}`, so `floor = now` and every message missed during the gap is dropped without warning. There is no fsync before the replace, and the R4 race can produce such a file. | Code reading: `except (OSError, ValueError): return {}`, then `state.get('floor', now)`. | Tell "no file" apart from "corrupt". On corrupt, print a warning on stdout and fall back to `now - backlog`. Fsync on save. |
| R9 | 🟠 | slack_bridge.py:214-215 | Permanent errors (`invalid_auth`, `token_revoked`, `not_in_channel`, `channel_not_found`) go to **stderr only**, every poll, forever. The Monitor tool spec says stderr "does not trigger notifications", so the session never learns the bridge is dead. | Code (:215 `file=sys.stderr`) plus the Monitor tool description: "Only stdout is the event stream." | Exit non-zero on permanent errors (Monitor ends and the re-arm rule surfaces it), or emit one `[slack_bridge error]` line on **stdout** per distinct error. |
| R10 | 🟠 | slack_bridge.py:169, 203, 208 | A blank optional key (`SLACK_POLL_SECONDS=`, `SLACK_THREAD_TTL_SECONDS=`, `SLACK_MAX_BACKLOG_SECONDS=`) raises an uncaught `ValueError`, so the watcher crashes. | `R10 ValueError could not convert string to float: ''`. | `float(env.get(k) or default)`; validate numbers in `load_env`. |
| R11 | 🟠 | slack_bridge.py:176-193 | Output is not in chronological order. A new reply to an older parent prints before a newer top-level message, so the session can act on "GO" after "STOP". | `R11 ['[… thread=…] GO (earlier)', '[Slack user=UOP] STOP (later)']`. Here the order happens to be right, but only because the parent sorts first. Emission follows **parent** ts, not reply ts. | Collect `(ts, line)` pairs and sort them before returning. |
| R12 | 🟠 | slack_bridge.py:179-180 | A thread broadcast ("also send to channel") is rendered as top-level, without `thread=`, so the session answers outside the thread. It is correctly not duplicated. | `R12 ['[Slack user=UOP] bcast']`. | Render with `thread=m["thread_ts"]` when `subtype == "thread_broadcast"`. |
| R13 | 🟡 | SKILL.md:30, DEC-0005 | Replies to parents older than `SLACK_THREAD_TTL_SECONDS` (24 h) are never seen, including replies to the bot's report from yesterday. Only the docstring (:15) says so. | Code :173, :195. | State the horizon in the skill and DEC-0005, or track parents the bot posted for longer. |
| R14 | 🟡 | slack_bridge.py:37, 205 | `me_message` (`/me`) is dropped. The first-run floor uses the local clock: if it runs ahead of Slack's (WSL2 drift), messages sent within that skew after the first arm are lost. | `R14 []` for a `me_message`. The skew part is code reading only. | Add `me_message` to `HUMAN_SUBTYPES`. Start the first-run floor a few seconds back, or seed it from the newest history ts. |
| R15 | 🟡 | slack_bridge.py:47, 50-54, 211-213 | (a) The misnamed-file hint matches `.env.example` and suggests renaming the template. (b) `export KEY=…` lines are not parsed. (c) Delivery is at-least-once (lines are printed before `save_state`), but the test name says "without loss or duplicates". | `R15 hint: found: .env.example — rename to .env)`; `R15 export: SLACK_BOT_TOKEN missing`. | Exclude `*.example`/`*.sample` from the hint; strip a leading `export `; rename the test or document at-least-once. |
| R16 | 🟠 | docs/qa/2026-10-14-live-qa.md:37-55 | "All ten are addressed" is backed by mock tests only. The live DoD proposed in the source request (§3) was not run: no live thread reply received, no live thread send, no live restart-gap delivery. The only live rows are `check` and one top-level `send`. | QA file lines 54-55 list only `check` and `sent ts=1791961225.879019`. | Run the §3 live E2E after the WO-0004 rework, or mark B/D as "mock-verified only". |

## A–J status (lead-verified)

| id | status | basis |
|---|---|---|
| A | Closed **by a skill rule only** | SKILL.md:32 re-arm rule. Nothing enforces it, and R3, R9 and R10 add crash and dead-watcher modes the rule does not catch (R9 never ends the Monitor). |
| B | **Partly closed** | Top-level resume and the cap work (`cmd_watch` :205; tests pass). Thread replies bypass the cap (R5); a corrupt state resets silently (R8); concurrent watchers crash (R4). |
| C | Closed | :129-135 keys state on sha256(resolved `.env` path + channel); `test_state_is_separate_per_project` passes. |
| D | **Partly closed** | Replies collected (:181-193), `send --thread` (:122). Loss (R1), duplicates (R2), broadcast loses its thread (R12), 24 h horizon (R13), API volume (R7). |
| E | Mostly closed | Subtype allowlist (:37) and cursor paging (:91-98). An `ok:false` mid-paging aborts before `floor` moves, so there is no loss. `me_message` dropped (R14); no test for broadcasts. |
| F | Closed | `user=` on every line (:163); `SLACK_ALLOWED_USERS` (:154, :170). |
| G | Partly closed | Skill step 6 rules only; still depends on the model. The hook option from the request was not evaluated. |
| H | Closed (scope uncertain) | `allowed-tools` lists `Monitor` and `Bash(python3 scripts/slack/slack_bridge.py:*)`. Whether these apply outside the skill invocation (later re-arms and sends) is unverified. |
| I | Closed, with a defect | `check` covers auth, token type, membership and history (:103-117); misnamed hint (:47). Defects R6 and R15a. |
| J | Closed | No subprocess; token only in the urllib header (:67). The matching test only greps the source for "subprocess"/"curl", which is weak but the behaviour holds. |

## Claims CONFIRMED (with evidence)

- **Float ts comparison is safe.** No string round-trip mismatches: `P4 roundtrip mismatches: 0 /200000` (ts drawn from 1.7e9–2.1e9 with 6 decimals). The double spacing near 2e9 (~4.8e-7) is below the 1e-6 ts step, so ordering is preserved.
- **`oldest` exclusive semantics are handled.** History uses `oldest` plus a client-side `ts > floor` (:179); replies use `oldest=since` plus `ts > since` (:190). There is no boundary loss or duplicate from inclusive/exclusive handling.
- **Newest-first order and cursor paging.** Results are sorted ascending (:176, :189); paging stops on `has_more` false or an empty cursor (:97). `test_follows_pagination` (50 messages at 20 per page) passes.
- **The parent is never re-emitted as a reply.** :190 skips `r.ts == parent ts`.
- **Thread broadcasts are not duplicated.** They are emitted once from history and skipped in replies (`R12` output shows a single line).
- **A history-level failure does not lose top-level messages.** `floor` is assigned only at the end of `poll` (:194). A 503 or 429 leaves it unchanged, and the next poll refetches. (The thread state is the exception; see R1.)
- **429 honours Retry-After**, with up to 3 attempts, then `SlackError`, and the watcher survives (:81-84).
- **The token never appears in argv or on a command line** (no subprocess; header only, :67).
- **Bot, own-user, join and rename messages are skipped** (:151-154; `test_emits_humans_with_sender_and_skips_noise` passes).
- **Per-project+channel state with 0700 directory permissions** (:129-135).

## Overstated or misleading claims

- **DEC-0005:** "resumes after restarts (capped backlog), follows threads" is false for threads (R5) and omits the 24 h horizon (R13).
- **SKILL.md:32:** "stale orders are not replayed" is false for thread replies (R5).
- **SKILL.md:30:** "including replies to the bot's own posts" holds only within 24 h (R13).
- **SKILL description and README:42:** "self-resuming watcher". It resumes its *position*; staying alive depends on a model re-arm, and it dies on network faults (R3) or goes silently dead (R9).
- **QA report:** "All ten are addressed" overstates it. B, D and G are partial, and the evidence is mock-only (R16).
- **Test name `test_restart_resumes_without_loss_or_duplicates`:** delivery is at-least-once (R15c), and duplicates and loss are possible (R1, R2, R4).

## Uncertain (not proven here)

- Whether Slack's 2025 rate-limit change (1 req/min, `limit` ≤ 15) applies to this app (R7).
- How long skill `allowed-tools` stay in effect (H).
- `send` posts `"thread_ts": null` when no thread is given. The live QA `sent ts=1791961225.879019` suggests Slack accepts it; the lead did not re-verify this live.

## Verdict

**FAIL.** 16 findings: 4 🔴 (R1, R3, R5, R6), 9 🟠, 3 🟡. Recommended for WO-0004: R1, R3, R5 and R6 must be fixed; R2, R4, R8, R9, R10, R11 and R12 should be fixed; each fix needs a regression test that fails on the current draft.
