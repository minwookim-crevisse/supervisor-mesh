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
