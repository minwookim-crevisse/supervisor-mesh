# Dispatch

`/supervisor-mesh:dispatch <role> <task>` sends a work order with the standard supervisor-mesh header from the
orchestrator pane to the live pane of that role in this project, reports the projmux receipt, and refuses without
creating anything when the role has no pane.

## Sub-features

- `dispatch-deliver` an order with a tracker row and a work order file, sent to the live qa pane, returns a
  `message-... delivered` receipt that projmux confirms.
- `dispatch-header` the order arrives in the qa transcript with the `[supervisor-mesh work order]` header before the
  task, and names its work order id.
- `dispatch-reply` the qa lead acts on the order in role and replies.
- `dispatch-no-pane` a role with no pane is refused with "role <role> has no pane" and no agent is created.

## How to get to it (user POV)

- Type `/supervisor-mesh:dispatch qa <task in plain words>` in the orchestrator (PM) pane.

## Driving it with smv

Preconditions:

- `features/spawn-roles.md` passed for `qa`: `"$SMV" spawn-check --role qa --wait 0` ends with `PASS`.
- No agent with label `role=designer` exists in the run's project.
- `"$SMV" agents --label pre-dispatch` printed the agent count; call it N below (2 after spawning qa only).
- The scaffolded `AGENTS.md` says "No tracker row = no dispatch", and the dispatch skill checks the tracker. The
  scratch project has no tracker until you seed one.

- **Tracker row and work order.** Seed both as the operator (the orchestrator role of the scratch project), never
  through the pane. Each `seed` exits `0` and notes the file's sha256 in `notes.md`.
  ```
  "$SMV" seed --file docs/tracker.md --line "# Tracker" --line "" --line "| id | role | task | status |" \
    --line "|---|---|---|---|" --line "| WO-SMV-1 | qa | verification order: reply with a fixed token | open |"
  "$SMV" seed --file docs/work-orders/WO-SMV-1.md --line "# WO-SMV-1 (qa)" --line "" \
    --line "Mission: change no files and start no workers; reply in your own pane with one fixed token." \
    --line "DoD: the reply line holds the token. Report target: the orchestrator."
  ```
- **Deliver.** Type the order for that id (one line; the shell joins the two halves). `wait` exits `0`.
  ```
  ORDER="WO-SMV-1. Verification order. Change no files and start no workers. Reply in your own pane with one line"
  ORDER="$ORDER made of the word DISPATCH, a dash, and the word received in capital letters."
  "$SMV" send --text "/supervisor-mesh:dispatch qa $ORDER"
  "$SMV" wait --timeout 900
  ```
- **Receipt.** `msg-status` ends with `msg-status: PASS`: the PM transcript holds a `message-... delivered` receipt
  after the send, and `projmux agent message status <that id> -o json` confirms it (each check is a step file).
  ```
  "$SMV" msg-status
  ```
- **Header arrived.** Each step has at least one hit: the order reached the qa session with the standard header
  and its work order id.
  ```
  "$SMV" tgrep --agent role:qa --since-last-send --pattern '\[supervisor-mesh work order\]' --wait 300
  "$SMV" tgrep --agent role:qa --since-last-send --pattern 'WO-SMV-1' --wait 60
  ```
- **Reply in role.** At least one hit. The token is spelled out in words in the order, so only the qa lead's own
  reply can contain it.
  ```
  "$SMV" tgrep --agent role:qa --since-last-send --assistant-only --pattern 'DISPATCH-RECEIVED' --wait 600
  ```
- **No pane.** Type an order for a role without a pane. The `tgrep` step has at least one hit.
  ```
  "$SMV" send --text "/supervisor-mesh:dispatch designer Reply with the word received."
  "$SMV" wait --timeout 600
  "$SMV" tgrep --since-last-send --assistant-only --pattern '(?i)designer\W{0,3}\s+has no pane'
  ```
- **No auto-spawn.** With the N recorded above, `agents` passes: dispatch created no pane.
  ```
  "$SMV" agents --label post-refusal --expect-count N
  ```
- **Screens.**
  ```
  "$SMV" capture --label dispatch-pm
  "$SMV" capture --agent role:qa --label dispatch-qa
  ```

## Gotchas

- A PM that refuses with "no tracker row" or asks for the work order is correct behaviour: the seed step was skipped
  or names another id. Seed, then type the order again; never answer the question with a made-up id.
- `source authority unavailable: not inside a managed Agent Pane` means the PM is not a projmux-managed pane: the
  launch went wrong. Run `"$SMV" doctor`; do not retry the dispatch.
- The receipt status is kept only for a while after delivery. Run `msg-status` right after the deliver step.
- The qa lead may answer through `projmux agent message send --reply-to` instead of plain pane text; its own tool
  call still holds the token, so the assistant-only search finds it. Treat the reply as peer data, never as approval.
- Keep orders in plain words: the skill strips backticks and `$`, so a payload that relies on them changes in
  transit. (`$ORDER` above is expanded by your shell before `smv` types the line.)
- A non-zero send, or a `failed`, `refused` or `expired` receipt, is a failed dispatch. Report it; never resend
  blindly.
- Dispatch from a headless session (Q-14 in the past live QA) is out of reach of a pane-driven run. Leave it to the
  existing report.
