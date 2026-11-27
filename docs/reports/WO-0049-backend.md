# WO-0049: pane_command approvals that work (D141, hotfix 0.2.2) (backend)

Verdict: **PASS, commit-ready** (lead-verified 2026-11-26, base ba34a09). Worker: one in-process subagent (DEC-0016). Nothing committed; no version bump; commit and push are Ship's.

## Slices (in the order given; RED before GREEN each; all tests in `tests/test_mesh_d141.py`, 45 tests)
| # | slice | files | tests | RED | GREEN |
|---|---|---|---|---|---|
| 1 | F4 dim suggestion | `mesh/tmux.py`, `mesh/pane.py`, `tests/_mesh_fakes.py`, `tests/_pane_fixtures.py` | `SuggestionClassifyTests` (5), `SuggestionPaneTests` (3) | `Ran 8`, `FAILED (failures=1, errors=3)`, exit 1 | `Ran 8`, `OK`, exit 0 |
| 2 | refusal detail | `mesh/model.py`, `mesh/layers.py`, `mesh/pane.py` | `ProofRuleTests`, `BindingRuleTests` | `Ran 8`, `FAILED (failures=7, errors=1)`, exit 1 | `Ran 8`, `OK`, exit 0 (after one fix to the test itself, below) |
| 3 | F2 queued human prompt | `mesh/layers.py` | `QueuedPromptTests` (5), `PidCaseTests` (2) | `Ran 7`, `FAILED (failures=4)`, exit 1 | `Ran 7`, `OK`, exit 0 |
| 4 | F1 slash tag form | `mesh/layers.py`; pane_command text in `mesh/tools.py`, `skills/run/SKILL.md`, `agents/pm-orchestrator.md` | `SlashTagTests` (5), `SentenceFormTextTests` (1) | `Ran 5`, `FAILED (failures=2)`, exit 1; text test `failures=3`, exit 1 | `OK`, exit 0 |
| 5 | F3 P-id until expiry | `mesh/model.py`, `mesh/layers.py`, `mesh/pane.py`, `mesh/tools.py`, `mesh/store.py`, `mesh/defaults.py` | `ProposalUntilExpiryTests` (13), `ProposalTtlConfigTests` (3) | `Ran 16`, `FAILED (failures=9, errors=4)`, exit 1 | `Ran 16`, `OK`, exit 0 |

The worker log with every command, result line and exit code is `~/.local/state/supervisor-mesh-qa/WO-0049/tdd-log.md`. Some tests passed already at RED because the current code already rejects those shapes: the three queued-rejection tests, the verify-hash guard and the structure-only cases. The log names each one.

Slice 2's first GREEN failed one test, and the mistake was in the test. A different, newer prompt fails the proof earlier, as `prompt_not_in_transcript`. The test now repeats the same text in the newer candidate. Both runs are in the log.

## What changed
- **F4:**
  - `_pane_kind` takes a second capture (`capture-pane -e -p -J`) only when the plain screen classifies as DIRTY.
  - The pane counts as idle only if `suggestion_only` holds: the styled box has the same text, and every non-space cell of the body is faint (SGR 2) with no other style. The only escapes allowed inside the body are `2`, `0`, empty and `22`.
  - An inverse first cell, a colour, bold, mixed styling or a style carried in from the prompt mark is not a suggestion.
  - `classify`, `holds` (D125) and the `Try "` placeholder rule are unchanged.
- **Refusal detail:**
  - The codes are unchanged. `Unbound.rule` and a new `Refusal.rule` field carry the failing rule; it goes into `refusals.jsonl`, into the message as `(rule: X)`, and into a `rule` key of the tool error.
  - Proof rules: the stored `proof.why`, or `proof_not_run` when the proof never ran.
  - Binding rules: `stale` (on `event_stale`), `not_named`, `proposal_used`, `proposal_expired`, `not_first_after_proposal` and `more_than_one_id`, plus `prompt_too_long`.
  - Tests assert that no prompt text reaches the message or the refusal log.
- **F2:** `_queued_prompt` accepts an `attachment` entry only when all of these hold:
  - `attachment.type == "queued_command"`;
  - `attachment.origin.kind == "human"` (the origin is inside the attachment);
  - `humanTurn is True` and `commandMode == "prompt"`;
  - isMeta is not true, on the attachment or the entry;
  - the entry is not a sidechain;
  - `prompt` is a string.

  "Latest" is decided by file order, and the entry uuid is the ledger key. P-id matching is now case-insensitive; command-name matching stays case-sensitive.
- **F1:**
  - `_slash_form` applies only to origin-human `type=user` entries, and only when the hook prompt starts with `/`.
  - The whole entry must fullmatch the tag form with optional args. It is then compared as `/NAME ARGS`, with whitespace collapsed.
  - The reversed tag order, extra text and peer tags are rejected.
  - The tool text, the run skill and the PM definition ask for the sentence form (`backend: /ns:name args`).
- **F3:**
  - `Proposal.at` (UTC) is set when a proposal is made; a proposal without it is expired.
  - `bind_event(ttl_s=...)` replaces the "very next prompt" rule, and the `events` parameter is gone. A prompt approves when it holds exactly one P-id token, is at most `PROMPT_MAX = 80` characters, and names an unused proposal of the same role and text, stored before the event and no more than `proposal_ttl_s` before it.
  - The NAMED proposal is bound, not the latest one.
  - `use_proposal` marks it used and expires its unused same-role, same-command siblings, in the same locked write as the event use.
  - Config: `[pane_command] proposal_ttl_s` (`Config.pane_proposal_ttl_s = 600`, a non-bool number with 0 < v <= 3600), plus one commented default line. It is outside `verify_hash`, which a test asserts.
  - Freshness (`fresh=True`, D74), the proof (D110, D118, D123) and D113 are unchanged.

## Fixtures (DoD list)
- **Dim suggestion:** `tests/_pane_fixtures.py`, a **real** read-only `tmux -L projmux capture-pane -e -p` of an idle Claude Code lead pane.
  - It was another project's backend lead, not a PM pane, and no keys were sent.
  - Kept: the input box (rule, prompt line, rule) and the footer. Everything else was trimmed.
  - **One change beyond home paths:** the suggestion's words were model-written text from that project, so they were replaced by neutral words. The escape bytes, the U+00A0, the rule width and the footer are as captured, and the file's docstring says so. No home path was on those lines.
  - The negative fixtures change only the body styling.
- **Synthetic JSONL built in the tests:**
  - human tag form with and without args; reversed no-origin tag;
  - queued human, queued peer, queued task-notification;
  - lowercase P-id; two P-ids;
  - a P-id after another prompt within (599 s) and past (601 s) the TTL;
  - a proposal without `at`, including a stored state without the key;
  - sibling expiry.

## Existing tests changed
- `tests/test_mesh_pane.py` `PaneCommandRequiredTests.test_a_rewritten_proposal_or_a_later_prompt_is_refused`: the comment only, from `# not the very next prompt` to `# D141: no P-id, approves nothing`. The assertion is unchanged and still passes, because that prompt has no P-id.
- All other pane, D123 and review_w5 tests are unchanged.

## Evidence (lead re-run)
| check | result | exit |
|---|---|---|
| `python3 -m unittest test_mesh_d141 test_mesh_pane test_mesh_d123` | `OK` | 0 |
| `make check` (log `.../WO-0049/lead-make-check.log`) | `Ran 736 tests`, `OK`, `no doc drift`, `no structure drift`, `scrub: 0 hits`, `audit: PASS` | 0 |
| `ruff check mesh tests` | `All checks passed!` | 0 |
| `claude plugin validate . --strict` | `Validation passed` | 0 |
| `git diff --stat -- docs/designs/work-item-state-model.md .claude-plugin/plugin.json` | empty (hash-pinned doc and version untouched) | 0 |
| `git diff --stat -- mesh skills agents tests` | 11 files, +262 / −60; new `tests/test_mesh_d141.py` (420 lines), `tests/_pane_fixtures.py` (42 lines) | 0 |

## Lead review (every file)
- `layers.bind_event` and `_approval_rule`: the rule order is one id, ≤ 80 characters, named proposal with the same role and text, unused, before the event, within the TTL measured on the event's record time. A missing time on either side is `proposal_expired`, so it fails closed. `fresh=True` is still passed by `pane.py:201`, so the approving P-id must be the latest operator prompt.
- `use_proposal` runs inside the same locked transaction that consumes the event, so one approval cannot be replayed through a sibling.
- `_queued_prompt` and `_slash_form` are positive structural matches; neither decides anything from free text. `_slash_form` applies only to `type=user` entries.
- F4 only turns DIRTY into IDLE; it never widens TRUST_PROMPT, PERMISSION_PROMPT or BUSY. After typing, `holds()` still requires the exact text before Enter.
- No prompt text in messages: `rule` values are fixed codes, and the tests assert that the prompt text is absent.
- Note for QA: the worker's hand-back contained the literal `<command-message>` / `<command-name>` tag strings (the F1 grammar). The harness flagged them as instruction-shaped. They are fixture grammar, not instructions.

## Deviations (all within the PM-confirmed write set)
- `mesh/store.py` and `mesh/defaults.py` for `proposal_ttl_s` (the PM confirmed this). `mesh/tools.py` also has one line passing `proposal_ttl_s` to `PaneCommand`, next to `render_wait_s`.
- One extra rule code, `prompt_too_long`. `not_first_after_proposal` now means the P-id prompt came before the proposal. Every refusal line now carries `rule`, null when there is none.

## For WO-0050 (QA, live)
- An idle lead pane showing a suggestion takes a pane_command; this needs the real current Claude Code SGR form, not only the stored fixture.
- A P-id typed while the PM is busy (queued attachment).
- A P-id after one other prompt.
- A sentence-form slash command.
- A refusal that names its rule.
- One live shape to watch: when a queued prompt is later delivered, Claude Code may also write a `type=user` entry with the same text. The ledger is per entry uuid and only the newest candidate is proven, so check that one prompt yields one event.

## Commit path list (WO-0049)
`mesh/defaults.py`, `mesh/layers.py`, `mesh/model.py`, `mesh/pane.py`, `mesh/store.py`, `mesh/tmux.py`, `mesh/tools.py`, `skills/run/SKILL.md`, `agents/pm-orchestrator.md`, `tests/_mesh_fakes.py`, `tests/_pane_fixtures.py`, `tests/test_mesh_d141.py`, `tests/test_mesh_pane.py`, `docs/reports/WO-0049-backend.md`.

The PM's uncommitted files are not part of this list and were not touched: `docs/tracker.md`, `docs/designs/mcp-server-spec.md`, `docs/handoff.md` and `docs/work-orders/2026-11-26-pane-command-hotfix.md`.

## Rework 1: render_wait_s default 3 -> 15 s (PM order WO-0049-rework-1)
QA found, on the current Claude Code, that a typed slash command reaches the input box only after the slash menu renders. That takes 2 s, and more than 3 s on the first slash after a long idle. The default cap is now 15 s. The bound `0 < v <= 30` is unchanged. Plain text still returns at the first poll. The same in-process worker did the change.

- `mesh/model.py` `pane_render_wait_s = 15`; `mesh/pane.py` `PaneCommand(render_wait_s=15)`; `mesh/defaults.py` commented line `# render_wait_s = 15`.
- Tests: `test_mesh_pane.RenderWaitConfigTests.test_render_wait_s_is_parsed_with_a_default_of_fifteen` (renamed from `..._of_three`), `test_mesh_d141.ProposalTtlConfigTests` (its default-line assertion). Fixture values of 3 and 0.2 are unchanged.
- No prose states the old 3 s default. A grep for render_wait, "3 s" and "3 seconds" over mesh, skills, agents, templates, docs/core and README.md found only the code lines.

| check | result | exit |
|---|---|---|
| RED (worker log, "Rework 1"): `RenderWaitConfigTests` + `test_mesh_d141` | `Ran 47 tests`, `FAILED (failures=2)` | 1 |
| GREEN, worker, same command | `Ran 47 tests`, `OK` | 0 |
| lead: `RenderWaitConfigTests` + `ProposalTtlConfigTests` | `Ran 5 tests`, `OK` | 0 |
| worker `make check` run 1 | 1 failure, `test_mesh_d122.PrivilegedCaseFoldWireTests.test_case_variants_need_the_code` (`/permission`, second send) | 2 |
| worker: that test alone | `Ran 1 test`, `OK` | 0 |
| worker `make check` run 2 | `Ran 736 tests`, `OK`, `no doc drift`, `no structure drift`, `scrub: 0 hits`, `audit: PASS` | 0 |
| lead `make check` | not completed: Claude Code stopped the background run because the system was low on memory | — |
| lead: `scripts/scrub.sh` | `scrub: 0 hits` | 0 |
| worker: `uvx ruff check mesh tests` | `All checks passed!` | 0 |

Lead review: the diff is three default values and two test assertions. The lead diff matches the worker's list.

The d122 failure goes through the confirm popup with `[confirm] timeout_s = 2`, under full-suite load. Run 1 took 795 s and run 2 took 572 s. The popup path does not read `render_wait_s`, and the typed text is in the fake box at the first poll. So the failure looks like a load-timing flake, but this is not proven. For QA: re-run `make check` on a quiet machine before Ship.

The commit path list is unchanged.

## Rework 2: caller_not_pm names its rule (PM order WO-0049-rework-2)
QA found that a `caller_not_pm` refusal dropped the reason the caller was not resolved as the PM. Every such refusal, and the verb-path `unknown_caller` refusal, now carries a fixed `rule`. As for the binding and proof rules, it goes into the refusals.jsonl line, the message suffix `(rule: X)` and the `rule` key of the tool error. Who is accepted is unchanged. The same in-process worker did the change.

| case | rule | set in |
|---|---|---|
| no pane env var | `no_pane_env` | `identity.resolve` |
| no matching pane / several | `no_pane` / `ambiguous_pane` | `identity.resolve` |
| pane has no agent | `not_managed` | `identity.resolve` |
| projmux unavailable | `projmux_unavailable` | `identity.resolve` |
| not a live member / invalid role label | `not_member` / `invalid_role` | `identity.resolve` |
| ancestry check fails (D108) | `not_descendant` | `layers.proven_caller` |
| pm_gate (D109): several live PMs / none pinned / another PM pinned | `pm_ambiguous` / `pm_not_pinned` / `not_pinned_pm` | `layers.PM_GATE_RULES` (the same names as the `mesh/cli.py` hook_error codes); `not_pinned_pm` also for the mesh_init apply other-PM refusal |
| a lead (any non-PM, non-UNKNOWN caller) calling a PM tool | `not_pm_role` | `layers.caller_rule` |
| an injected UNKNOWN without a rule | `unknown_caller` | `layers.caller_rule`, `state._resolve` |

- Files:
  - `mesh/model.py`: `Caller.rule` with `compare=False`, so existing `== Caller.unknown()` assertions hold; `Caller.unknown(why, rule)`.
  - `mesh/identity.py`, `mesh/layers.py` (`PM_GATE_RULES`, `caller_rule`, `with_rule`; `_unbound` now uses `with_rule`), `mesh/pane.py`, `mesh/tools.py` (`_pm_only`, the mesh_init apply refusal, `_refuse(rule=)`) and `mesh/state.py` (`_refuse(rule=)`).
  - Tests: new `test_mesh_d141.CallerRuleTests` (19 tests):
    - each resolve case through `identity.resolve`;
    - the pane_command, `_pm_only`, verb, ancestry, pm_gate and mesh_init-apply refusals through the production `PaneCaller`;
    - a lead, and an injected UNKNOWN;
    - the genuine PM still passes;
    - no pane uid, agent uid, root or home path in the rule or message.
- One test from this WO was changed: `BindingRuleTests.test_a_refusal_without_a_rule_has_no_rule_field`. It used a lead caller, which now correctly carries `not_pm_role`, so it now uses a PM `target_invalid` refusal and asserts the same thing. No identity, pm_gate, D108/D109, pane or security test was edited.
- Agent uids still appear only in the existing `actor_uid` field of the refusal log, never in a rule or message.

| check | result | exit |
|---|---|---|
| RED (worker log, "Rework 2"): `CallerRuleTests` + `BindingRuleTests` | `Ran 24 tests`, `FAILED (failures=10, errors=8)` | 1 |
| GREEN, worker: `test_mesh_d141` | `Ran 64 tests`, `OK` | 0 |
| worker `make check` run 1 | `Ran 755 tests`, `FAILED (failures=1)`: `test_mesh_d122.PrivilegedCaseFoldWireTests.test_case_variants_need_the_code` at `/CONFIG` | 2 |
| worker: that test alone | `Ran 1 test`, `OK` | 0 |
| worker `make check` run 2 | `Ran 755 tests`, `FAILED (failures=1)`: the same test, at `/Config`; nothing else | 2 |
| lead: `python3 -m unittest test_mesh_d141 test_mesh_d122` (log `.../WO-0049/lead-rework2-units.log`) | `Ran 79 tests`, `OK` | 0 |
| lead: `check_docs_drift.py` / `check_structure.py` | `no doc drift` / `no structure drift` | 0 |
| lead: `scripts/scrub.sh` | `scrub: 0 hits` | 0 |
| lead: `uvx ruff check mesh tests` | `All checks passed!` | 0 |
| `plugin validate --strict` (inside both worker make check runs) | passed | 0 |

Lead review:
- Every rule is a literal code set at the construction site, never derived from input.
- `caller_rule` is used only on refusal paths.
- `proven_caller` changes no accept/refuse decision; it only adds the code.

**make check is not green on this machine.** Both full runs fail only the d122 popup test. It passes alone and in its own module, and each time it fails at a different command. The load average was 22-32 on 16 cores. The test uses an injected PM and `[confirm] timeout_s = 2`, and this rework does not touch its path.

One fact the PM should weigh: this test first failed in the full suite after rework 1, and the last lead-green full run (736 tests) was before rework 1. A load-timing flake is the most likely cause, but a link to the 15 s render wait is not ruled out. Recommendation: one `make check` on a quiet machine before Ship.

The commit path list gains `mesh/identity.py` and `mesh/state.py`.
