# WO-0030 — security fixes D108-D116, F8, dispatch size limit (backend)

Verdict: **PASS** (lead-verified 2026-11-18, base 0501f50 + WO-0028). Nothing committed; commit/push is Ship's.

## Scope built
Spec D108-D116 (`docs/designs/mcp-server-spec.md` lines 37-45), including the PM refinements for D108, D109 and D110, plus security F8 (JSON nesting) and the pilot's 4096-byte projmux limit.
- **D108:** the caller must descend from the named pane's claude; no claude pid means fail closed.
- **D109:** the PM uid is pinned; more than one live `role=pm` refuses PM verbs; re-pin is a pending event confirmed at `mesh_init` apply.
- **D110:** an event must match the latest real user prompt of the PM session's transcript; each entry backs one event; only `sha256(session:uuid)` is stored; the transcript must realpath into this project's Claude projects dir.
- **D111:** realpath containment for every init and tracker write, and for refusal records.
- **D112:** the CLI dest is `_tool`; override evidence needs an event use and counts as an intervention; a new event takes a fresh seq, and on read the first hash-matching line wins.
- **D113:** a one-time code shown in `tmux display-popup` on the PM's client. It goes via a 0600 file that is deleted at once; only its hash is held; it is in no argv.
- **D114:** re-pin from a new pane only when the pinned PM is not Running, exactly one live `role=pm` exists, and a D113 code is confirmed.
- **D115:** `fail_if_output` entries are literal substrings.
- **D116:** tmux targets must be `%<digits>`; clear-line only when the prompt line is visible.
- **F8:** a single JSON parser with a depth bound of 64, plus a 4 MiB line bound.
- **Dispatch:** the message is the pinned header plus the item id, message-ref, work-order path and report path, never the body; at most 4096 UTF-8 bytes, refused before recording if larger.

## Evidence (lead re-run)
| check | result | exit |
|---|---|---|
| `make check` | `Ran 591 tests`, `OK`, `scrub: 0 hits`, `audit: PASS` | 0 |
| `claude plugin validate . --strict` | `Validation passed` | 0 |
| `ruff check mesh tests` | `All checks passed!` | 0 |
| `tests/test_mesh_security_wo0023.py` | `OK` | 0 |
| key tests | `test_no_claude_pid_..._fails_..._closed`, `test_privacy_nothing_from_the_transcript_but_the_matching_prompt_is_kept_or_printed`, `test_each_confirmation_has_a_fresh_code_and_it_is_in_no_argv`, `test_a_5kb_work_order_travels_as_its_path_and_the_message_fits_4096_bytes` all `ok` | 0 |
| leftovers | `/tmp/mesh-wt-*` 0 entries; `git worktree list` = main | — |

Worker evidence is in `~/.local/state/supervisor-mesh-qa/WO-0030/`:
- `tdd-log.md`: the first RED run had 48/50 failing (the 2 passes are positive controls); a second RED for D115/D116; GREEN 57/57;
- `gates-final/`;
- `sec-rerun/`: security's probes P1a, P1c, P2a, P2b, P2e, P3, P4, P5 and P9 re-run against the repo, all now refused or clean.

Security guard tests: 4 of the 48 adopted (the F9/F10 reds plus C5), with source comments; the other 44 were green on 0501f50 and are covered by existing tests.

## Deviations accepted by the lead
1. **Re-pin flow:** the re-pin is stored as a pending `pm_repin`; the new PM's `mesh_init` apply moves the pin, with the D114 checks and the D113 popup. This keeps the popup outside the hook's 10 s timeout.
2. **PM session pinning:** the session is pinned by the first fully checked prompt. Residual: before that first prompt the session id is unchecked.
3. **Duplicate seq:** a new event never reuses a seq; it is not refused, so one planted line cannot block every later event.

## Open items
- **Live checks for WO-0024:**
  - whether Claude Code writes the user entry to the transcript before `UserPromptSubmit` runs (mesh re-reads up to 3 times, about 0.7 s);
  - whether `tmux display-popup -c <client> -t <pane> -E` behaves as assumed.
- **For the PM:** `work_dispatch` does not check that the work-order file exists before sending its path. Should a guard be added?
- **Noted:** `ITEM_ID_PATTERN` accepts Unicode digits; not covered by D112.
- **Accepted residual (D96):** a same-user process editing both `state.json` and `events.jsonl` can still forge an event (probe P2a-residual); documented in the README.

## Tail (after WO-0031 a1bb251) — D117 item 4, ASCII ids, dead code, import, D118 (F11), D119
Verdict: **PASS** (lead re-run). Tests: `tests/test_mesh_tail_wo0030.py` (15 tests). Worker log: `~/.local/state/supervisor-mesh-qa/WO-0030/tail-tdd-log.md`.
1. **`work_dispatch` refuses a missing work-order file** (`work_order_missing`) or one symlinked outside the root (`outside_root`), before recording or sending anything. RED: both were sent. GREEN: OK.
2. **`ITEM_ID_PATTERN = WO-[0-9]{4,}`**, and `tracker.py` now uses it. Arabic-Indic, fullwidth and mixed digits are refused at every id input. Lead check: `grep 'WO-\d' mesh/` gives 0 hits.
3. **The dead `re.compile` loop after `return None`** in `layers.output_failure` is removed (AST test).
4. **`tests/test_mesh_security_wo0023.py` imports** from the root and under discovery. Lead check: `python3 -m unittest tests.test_mesh_security_wo0023` → `OK`.
5. **D118 / F11 (security N4 port):**
   - The session is never pinned from a prompt.
   - Every operator event checks the hook's `session_id` and realpath(`transcript_path`) against projmux `describe agent` `.status.sessionRef.claude.{sessionId,transcriptPath}`; a mismatch is refused with `session_not_pm`.
   - No sessionId → `session_not_pm`; projmux down → `projmux_unavailable`. Both fail closed, and neither value is logged or stored.
   - RED was captured against an `a1bb251` copy (5 FAIL).
6. **D119:** the `Bash(*agent-hook*ingest*)` deny rule is present exactly once after init and after a re-run (`mesh/init.py:369`).

Lead re-run:
- `make check` exit 0 (`Ran 606 tests`, `OK`, `scrub: 0 hits`, `audit: PASS`);
- validate passed;
- `ruff check mesh tests` → `All checks passed!`;
- tail tests 15 `ok`.

### Decisions for the PM
- **Re-pin session check:** a re-pin prompt is checked against the CALLING (new PM) agent's projmux session. D118's wording names only the pinned agent, but the pinned PM is not Running during a D114 re-pin. Please confirm.
- **Two error codes:** `projmux_unavailable` is kept as a separate hook_error code from `session_not_pm`; both fail closed. Say if D118 wants a single code.
- **Legacy fields:** `Doc.pm_session` and `RePin.session` remain as legacy schema fields (never written or read) so older state files still load.

### Proposed section-9a text (state model; PM applies)
- "Trigger: a Claude Code `UserPromptSubmit` hook, installed by `mesh init` in the project's `.claude/settings.json`, runs `mesh event record --source terminal`. It records the prompt (text, timestamp, event id) to `.mesh/events.jsonl` only when all of these hold:
  - the caller pane is the pinned PM (projmux role label `pm`, project cwd, the agent uid `mesh_init` pinned, exactly one live `role=pm` agent; D109);
  - the hook runs under that pane's claude process (D96);
  - the hook's `session_id` and realpath(`transcript_path`) equal the live `SessionID` and `TranscriptPath` projmux reports for that agent (`describe agent`). projmux is asked on EVERY event; the session is never pinned from a prompt (D118). No SessionID from projmux, or projmux unavailable, records nothing (fail closed, hook_error `session_not_pm` / `projmux_unavailable`); neither value is logged or stored;
  - the prompt equals the latest real user prompt of that transcript, an entry not used before (D110).

  Otherwise it exits 0 and records only a hook_error code; the hook never blocks the prompt."
- "Residual (D96, D118, D119): a process of the same OS user can still forge an operator event. It can append a fake user line to the REAL transcript, or feed projmux's session record with a forged `projmux internal agent-hook ingest`. `mesh init` adds the advisory deny `Bash(*agent-hook*ingest*)` to PM and lead settings until projmux checks the provenance. Ordinary events stay exposed to this; high-impact events stay behind the D113 out-of-band code."
- Tests line, add: "a fabricated session id and transcript are refused (`session_not_pm`) and the projmux-reported session is accepted; no SessionID from projmux fails closed."

## Tail 2 — D121 (revised), D122, D123 — commit-ready
Verdict: **PASS** (lead re-run 2026-11-18). Worker log: `~/.local/state/supervisor-mesh-qa/WO-0030/tail2-tdd-log.md`. RED exit 1 for each of the three new modules, then GREEN.
- **D121:**
  - mesh generates the code and holds only its sha256.
  - The plaintext goes once through FIFO `code`, in a fresh 0700 `mesh-confirm-*` dir with an `owner` marker.
  - The RAW typed text comes back through FIFO `answer` as `mesh-confirm/1 <pid> <typed>`, and mesh hashes and compares it itself (hmac).
  - Refused: exactly-one-line violations, other pids holding the FIFO, a dead or non-helper claimed pid, a changed dir set or changed inodes (`tampered`). The dir is removed in `finally`.
  - The dir path reaches the helper via `display-popup -e` env; the code is in no argv. The earlier hash-pair design was never built.
- **D122:**
  - Any helper failure, crash, kill, timeout, missing answer, planted entry (the `answer.tmp` file or FIFO) or forged line is refused.
  - Command names are case-folded; `/allowed-tools`, `/settings`, `/plugins` and `/permission` are added.
  - There is one reaper rule, `layers.dead_mesh_dir`: tmp prefix, a real dir, this uid, 0700, the mesh `owner` marker, a dead owner pid. It is used for both confirm dirs and verify worktrees.
- **D123:**
  - The hook records a CANDIDATE after the D96, D109 and D118 checks, with no transcript read.
  - The proof runs at the first citation (`layers.resolve_event`):
    - live session hash equal to the one at record time;
    - the latest REAL prompt, by the positive rule (type=user, not isMeta, not a sidechain, `origin.kind == "human"`, text only), equals the event text;
    - the entry's ts is at least record time minus 5 s;
    - rule 4: only the newest candidate binds;
    - rule 5: one event per entry uuid.
  - A failed candidate is marked for good; projmux being unavailable is not marked.
  - New refusal code `event_unproven`. The retry loop in the hook is removed.

Lead re-run:
- `test_mesh_d121`, `test_mesh_d122` and `test_mesh_d123` → `OK` each;
- `make check` exit 0 (`Ran 656 tests`, `OK`, `scrub: 0 hits`, `audit: PASS`);
- validate passed;
- ruff `All checks passed!`;
- no `mesh-wt-*` left.

Cleanup: a stale `/tmp/mesh-confirm-879i9j51` (19:20, old pre-D121 format: a 0600 plaintext `code` file, no marker) was held by no process. The new reaper keeps marker-less dirs, so the lead removed that exact path without reading the file; 0 `mesh-confirm-*` dirs remain.

### For the PM
- **Decision:** old-format verify worktrees without the marker are now kept by the reaper, not reaped (they fail closed on the stricter rule).
- **Formal model:** not re-run. D123 adds a lookup refusal (`event_unproven`) but no transition.
- **Live checks for WO-0024 re-run:**
  - `tmux display-popup -e` passes the env to the helper;
  - the helper is woken (POLLERR) when mesh closes its reader;
  - the D123 candidate is proven at cite time on Claude Code 2.1.327.

## Tail 3 — D124 (security F13) — commit-ready
Verdict: **PASS** (lead re-run 2026-11-18, base 382cf02). Written only under `mesh/` and `tests/` while QA run 2 was live. Worker log: `~/.local/state/supervisor-mesh-qa/WO-0030/tail3-tdd-log.md`.
- **F13 reproduced (RED):** security's `test_n1_a_thief_running_its_own_helper_instance`, ported to `tests/test_mesh_d124.py`, returned "approved while the operator's popup said 'no code received; refused'". `test_mesh_d124` failed: Ran 11, failures=8, errors=1, exit 1.
- **Fix:**
  - (a) Approval is judged only after mesh's own `display-popup -E` returns. Answers are buffered; refusals can still come early. New reasons: `no_answer` (the popup returned with no answer) and `unavailable` (the popup failed after an answer).
  - (b) Any second process ever seen holding `code` or `answer` (a /proc scan every tick, and again right before the code is written) is refused as `second_writer`.
  - (c) The helper reports `mesh-confirm/no-code <pid>` and exits 3; this is refused as `no_code`.
  - Approval is bound to the single helper that held `code` when mesh wrote it.
- **Lead re-run:**
  - `test_mesh_d124`, `test_mesh_d121` and `test_mesh_d122` → `OK`;
  - `make check` exit 0 (`Ran 667 tests`, `OK`, `scrub: 0 hits`, `audit: PASS`);
  - validate passed; ruff `All checks passed!`;
  - diff `mesh/` + `tests/`: 4 files, +146 / −58, plus the new `tests/test_mesh_d124.py`;
  - no `mesh-confirm-*` or `mesh-wt-*` left.
- **Changed reasons in old tests** (still refused, only the reason differs; 4 assertions): stolen code `timeout` → `second_writer`; silent popup, crashing helper and killed helper `timeout` → `no_answer`.
- **Residual (D96, same user):**
  - if the popup's own helper dies before opening `code` while the popup still returns ok, a lone thief helper could be bound;
  - a thief reading `answer` could swallow the no-code line;
  - without /proc, mesh falls back to one line, not no-code, a live pid, and the popup returned.
- **For the PM:**
  - `no_code` and `no_answer` are in the `confirm` docstring but not yet in the spec;
  - each approval now takes about 1.5 s longer (the helper holds `answer` for 1.5 s before exiting);
  - live check of the real tmux popup timing on the next QA run.
