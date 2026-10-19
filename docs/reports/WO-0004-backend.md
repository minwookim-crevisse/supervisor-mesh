# WO-0004 — Rework of the Slack bridge draft (backend)

- Date: 2026-10-14 · Role: backend lead · Model: lead + one same-role worker + lead review (one rework round)
- Inputs: `docs/reports/WO-0001-security.md`, `WO-0002-backend.md`, `WO-0003-qa.md`; DEC-0005, DEC-0008; `docs/work-orders/2026-10-14-slack-rework.md` (WO-0004).
- Files changed (the write set, nothing else): `templates/slack/slack_bridge.py`, `templates/slack/env.example`, `skills/slack-setup/SKILL.md`, `README.md` (Slack lines 42 and 66), new `tests/test_slack_regression_01.py` (34 tests). Not committed (Ship only).
- Baseline: the pre-edit `slack_bridge.py` was copied to `<scratchpad>/baseline/` before any edit (sha256 `9af028622a18ccc4…`). Baseline runs use a mirror tree `<scratchpad>/leadbase/` holding `tests/` + the baseline bridge.
- **All evidence below was re-run by the lead** (not the worker's self-report), unless marked "worker".

## Gate evidence (lead runs)

| gate | command | result |
|---|---|---|
| baseline-fail | mirror: `cd leadbase/tests && python3 -m unittest test_slack_regression_01` | `FAILED (failures=23, errors=11)`, **0 of 34 ok** |
| fix-pass (new file) | `python3 -m unittest discover -s tests -p test_slack_regression_01.py` | `OK` (34) |
| full suite | `python3 -m unittest discover -s tests` | `Ran 76 tests` · `FAILED (failures=6)` · **exit 1**. See "Open item". |
| the 6 failures | `… -p test_slack.py \| grep ^AssertionError \| sort \| uniq -c` | `6 AssertionError: 1 != 0 : slack_bridge: SLACK_ALLOWED_USERS is empty` (only the DEC-0008 refusal) |
| scrub | `bash scripts/scrub.sh` | `scrub: 0 hits` · exit 0 |
| validate | `claude plugin validate skills --strict` / `claude plugin validate . --strict` | `✔ Validation passed` · exit 0 / exit 0 |
| lint | `which ruff` | not installed. Worker substitute: `py_compile` passes, no unused imports, no lines over 120 chars. |
| write set | `git status --short` | new paths only `templates/slack/slack_bridge.py`, `tests/test_slack_regression_01.py` (+ `docs/reports/`). The other M/D entries predate WO-0004. |

## Finding → change → test → baseline / fix

Lines refer to `templates/slack/slack_bridge.py` unless stated. B = baseline result (lead mirror run), F = fix result.

| finding | change | test (`test_slack_regression_01`) | B | F |
|---|---|---|---|---|
| S-01 (🔴) | :54 `TOKEN_RE`; :184-185 refuse `.env*` (name and resolved name); :190-191 refuse the token value or a token pattern | `test_s01_send_refuses_env_files`, `test_s01_send_refuses_token_text` | FAIL | ok |
| Q-12 | :186-189 a file read error becomes SlackError | `test_q12_send_missing_file_is_a_clean_error` | ERROR | ok |
| DEC-0008 (🔴) | :317-319 `watch` exits 1 when the allowlist is empty; env.example; SKILL steps 1-2 | `test_watch_refuses_without_allowlist` | FAIL | ok |
| S-02 / R6 / Q-05 (🔴) | :164-169 print only the matched prefix, or "unrecognised" | `test_s02_check_never_echoes_a_dashless_secret` | FAIL | ok |
| R1 / Q-01 (🔴) | :271-313 `poll` returns `(lines, new_state)` and never mutates `state`; :354-358 print, then commit, then save | `test_r1_mid_poll_failure_keeps_the_reply` | FAIL | ok |
| R3 / Q-04 / S-08 (🔴) | :126-150 OSError / HTTPException / ValueError / bad Retry-After become SlackError; GET retries 5xx and network faults; :359-367 last-resort handler in the loop | `test_r3_non_json_body`, `_dropped_connection`, `_timeout`, `_call_raises_slack_error`, `_5xx_is_retried`, `_non_int_retry_after`, `_malformed_message_does_not_kill_watch` | ERROR ×6, FAIL ×1 | ok |
| R5 / Q-03 (🔴) | :338 `start_floor = now - backlog`; :298 `since = max(saved, start_floor)` | `test_r5_stale_thread_reply_not_replayed_after_restart` (90 min dropped, 30 min kept) | FAIL | ok |
| S-04 | :112-115 base must be `https://slack.com/api` or `http://127.0.0.1\|localhost:<port>`; :104-106 opener refuses redirects | `test_s04_api_base_is_restricted`, `test_s04_redirect_is_not_followed_with_the_token` | FAIL ×2 | ok |
| S-05 | :257-268 Cc/Zl/Zp → space across text, file names, user and thread; `[Slack` → `(Slack` | `test_s05_render_sanitises_text_and_file_names` | FAIL | ok |
| S-07 | :199-211 leaf dir lstat + owner check, OSError → SlackError; :232-244 mkstemp (0600) + fsync + replace | `test_s07_state_file_is_private_and_atomic`, `_foreign_state_dir_is_refused`, `_unwritable_state_root_is_a_clean_error` | FAIL ×2, ERROR | ok |
| R2 / Q-09 | :299, :307 position = max(latest_reply, newest processed, saved) | `test_r2_raced_reply_not_duplicated` | FAIL | ok |
| R4 | :321-332 `flock(LOCK_EX\|LOCK_NB)` on `watch.lock`; a second watcher exits 1 | `test_r4_second_watcher_exits_clearly` | FAIL | ok |
| R7 / Q-08 | :284-286 history from `floor`; the TTL window is re-scanned at most every `SLACK_THREAD_SCAN_SECONDS` (default 60); `scan` kept in state | `test_r7_history_from_floor_and_periodic_thread_scan` | FAIL | ok |
| R8 | :214-229 missing → `{}`, corrupt → None; :340-343 one stdout warning, fall back to the backlog cap | `test_r8_corrupt_state_warns_and_replays_backlog` | ERROR | ok |
| R9 | :57 `PERMANENT`; :360-363 one stdout line, exit 2; :364-367 transient errors once per distinct error | `test_r9_permanent_error_exits_2_on_stdout`, `test_r9_transient_error_reported_once` | FAIL ×2 | ok |
| R10 | :70 `num()` (blank = default); :96-100 non-numeric → SlackError | `test_r10_blank_optional_keys_use_defaults`, `test_r10_non_numeric_key_is_a_clean_error` | ERROR, FAIL | ok |
| R11 | :313 lines sorted by ts | `test_r11_lines_in_ts_order` | FAIL | ok |
| R12 / Q-11 | :292 a broadcast is rendered with `thread=<thread_ts>` | `test_r12_broadcast_rendered_with_thread` | FAIL | ok |
| R14 | :50 `me_message`; :345 first-run floor = now − 5 s | `test_r14_me_message_and_first_run_floor` | FAIL | ok |
| R15 | :77 the hint skips `*.example`/`*.sample`; :86-87 `export ` prefix | `test_r15_hint_ignores_example_files`, `test_r15_export_prefix` | FAIL ×2 | ok |
| **RW-1** (found in lead review) | :133, :139 a POST is retried only on 429; a lost response on a POST fails at once (no double-post) | `test_rw1_post_is_not_retried_after_a_lost_response` | FAIL on the first fix: `0 != 2 : drop: exactly one POST` (worker run) | ok |

Skill / README (claims checked against the code by the lead):
- **S-03:** `allowed-tools` drops bare `Write`, `cp:*` and `grep:*`; the other grants are exact commands. `Monitor` is kept, with a body rule restricting it to exactly `python3 scripts/slack/slack_bridge.py watch` (SKILL:13-14). Bare `Read` is kept (it was not in the order), plus a rule never to read `.env` (SKILL:27).
- **S-06 / DEC-0008:** a trust rule (SKILL:16-18); the allowlist is required (SKILL:25, :28); README:42 reworded.
- **R13 + R7 latency:** the 24 h horizon and the scan latency are stated (SKILL:44). "Resumable, not self-restarting", at-least-once delivery, one watcher per project+channel (SKILL:45).
- **S-09 (skill part):** the skill regex matches the order's pattern (SKILL:52).

## Lead verification beyond the worker's tests

Batch-1 probes were re-run against the fix (`<scratchpad>/lead_verify4.py`, real `poll`/`call`):
- R1: `P1 raised: conversations.replies: HTTP 503 | state unchanged: True`, then `P1 retry: [… reply-0, … reply-1]`.
- R5: `P2 stale replay: []`.
- R3: `P3 hang ->SlackError: conversations.history: TimeoutError`; `P6 html ->SlackError: … unreadable response (JSONDecodeError)`.
- R4 / S-07: `P5 errors: 0 mode: 0o600 leftover tmp: []` (2 threads × 1500 saves).
- R7: `R7 calls: non-scan poll 1 | scan poll 5` (1,000 messages in 24 h). Before the fix it was 5 on every poll.
- RW-1: before rework `P7 … server received POSTs: 2`; after rework `P7 … server received POSTs: 1`.
- Test quality spot-check: R1, R5, S-01 and DEC-0008 assert both the positive and the negative side (e.g. R5 drops the stale reply AND keeps the fresh one; S-01 asserts zero posts and that the secret is absent from stderr).

## Design decisions / deviations (lead-accepted)

1. **R7 design (lead decision):** each poll reads history from `floor`, and the full TTL window is re-scanned at most every `SLACK_THREAD_SCAN_SECONDS` (default 60). Trade-off: a reply to an older thread, including a reply to the bot's own post, can arrive up to about 60 s + one poll later. This is documented in the skill. Set the key to 0 for the old per-poll behaviour.
2. **R5 `start_floor = now − backlog`, not the restored floor:** the scan cadence means a thread reply can be older than the newest top-level ts. Clamping to the restored floor would lose it, and `now − backlog` still enforces the cap. Accepted.
3. **Untracked parents:** `since = max(start_floor, min(floor, last_scan − 5 s))`. Such a parent had no replies at the last scan, so nothing at or before that point is a duplicate. Accepted.
4. **R10 validation in `load_env`:** `check`/`send` also reject a non-numeric optional key. Accepted (fails early).

## Rejected findings
None. Items outside the WO-0004 write set are left to their owners:
- S-09 (scrub/test regex): QA / orchestrator.
- S-10 / Q-06 / Q-07 (test_slack fidelity): QA, WO-0005.
- Q-02 / S-11 (gate paths), Q-10 / Q-14, the DEC-0005 wording: orchestrator.
- R16 (live E2E): WO-0005.

## Open item — DoD 1 not met as written
`python3 -m unittest discover -s tests` exits 1. The cause is exactly 6 `test_slack.WatchTests` tests whose `write_env` sets no `SLACK_ALLOWED_USERS`, so `watch` correctly refuses under DEC-0008. `tests/test_slack.py` is QA-owned and outside this write set. The orchestrator was asked to choose (a) accept until QA fixes it in WO-0005, or (b) grant a one-line edit; no answer had arrived when this report was written.
For QA: add `SLACK_ALLOWED_USERS=UOP` **and** `SLACK_THREAD_SCAN_SECONDS=0` to `BridgeTestCase.write_env`. `test_thread_replies_including_replies_to_our_posts` also needs the second key because of design decision 1. Worker scratch run with both keys: `Ran 7 tests … OK`.

## Residual (not proven here)
- Live Slack behaviour of the reworked bridge (check/send/watch, thread reply, restart gap) is not run. That is WO-0005 live E2E.
- `send` still posts `"thread_ts": null` when no thread is given. The earlier live send suggests Slack accepts it; not re-verified.
- Whether Slack's 2025 rate-limit change applies to this app remains unverified. R7 reduces the volume either way.

## Verdict
**PASS on all WO-0004 items (22 order items + 1 rework fixed, 0 rejected), with one open item:** the full-suite DoD is red only because of the DEC-0008 / test-ownership conflict above. All 34 regression tests fail on the baseline and pass on the fix. Scrub 0, validate 0.
