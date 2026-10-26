# Spawn roles

`/supervisor-mesh:spawn-roles [role ...]` creates one visible projmux Claude pane per mesh role, split into the
orchestrator's own window, bootstraps each pane from its role definition file, and skips roles that already have a
live pane for this project directory.

## Sub-features

- `spawn-new` creates a pane named `role-<role>` with labels `role=<role>` and `mesh=supervisor-mesh`,
  `spec.workspace.cwd` equal to the project, phase `Running`, and `ROLE LOADED role-lead-<role>` in its transcript.
- `spawn-layout` places the pane in the orchestrator's window, side by side with it.
- `spawn-idempotent` a re-run while the role is live creates no second pane.
- `spawn-project-role` a role stamped by add-agent (`.claude/agents/role-lead-data.md`) is discovered and loads from
  the project file.

## How to get to it (user POV)

- Type `/supervisor-mesh:spawn-roles qa` (one role) in the orchestrator pane.
- Type `/supervisor-mesh:spawn-roles` with several role names, or with none for every role. Do not use the
  no-argument form for verification: it opens a pane for each of the eight base roles.

## Driving it with smv

Preconditions:

- Init is done (`AGENTS.md` exists in the scratch project) and `"$SMV" doctor` ends with `PASS`.
- `"$SMV" agents --label pre-spawn --expect-count 1` passes (only the PM pane).
- For `spawn-project-role`, `features/add-agent.md` has run.

- **Spawn.** Type the command for one role. `wait` exits `0`; stderr shows
  `recorded agent agent-... (creator uid:<PM uid>)` as soon as the new pane exists.
  ```
  "$SMV" send --text "/supervisor-mesh:spawn-roles qa"
  "$SMV" wait --timeout 900
  ```
- **Pane proof.** `spawn-check` ends with `spawn-check qa: PASS`: exactly one live `role=qa` agent with this
  project's cwd, label `mesh=supervisor-mesh`, name `role-qa`, and `ROLE LOADED role-lead-qa` in that agent's
  transcript. Then `agents` passes and its step file holds the projmux view (uid, labels, window).
  ```
  "$SMV" spawn-check --role qa --wait 300
  "$SMV" agents --label post-spawn --expect-count 2
  ```
- **Layout.** The `spawn-check` info line shows the role pane's window uid equal to the PM's. The capture shows the
  PM pane narrower, with the role pane beside it.
  ```
  "$SMV" capture --label spawn-layout
  ```
- **Re-run.** Type the same command again. Both checks pass: no second qa pane.
  ```
  "$SMV" send --text "/supervisor-mesh:spawn-roles qa"
  "$SMV" wait --timeout 900
  "$SMV" agents --label post-respawn --expect-count 2
  "$SMV" spawn-check --role qa --wait 0
  ```
- **Project role.** After add-agent, spawn the stamped role. `spawn-check` passes with `ROLE LOADED role-lead-data`,
  and `agents` passes.
  ```
  "$SMV" send --text "/supervisor-mesh:spawn-roles data"
  "$SMV" wait --timeout 900
  "$SMV" spawn-check --role data --wait 300
  "$SMV" agents --label post-spawn-data --expect-count 3
  ```
- **Role screen.**
  ```
  "$SMV" capture --agent role:qa --label role-qa-pane
  ```

## Gotchas

- `activation unconfirmed` in the PM's output usually means the new pane sits at the folder-trust prompt. Run
  `"$SMV" status --agent role:qa`; if it reports `screen=trust`, stop and ask the operator to answer it in that pane,
  then resume with `smv wait --ready --settle 3` plus `--agent role:qa`.
- Read `ROLE LOADED` from the transcript, not the screen: a narrow pane wraps and scrolls the line away.
- The PM runs `projmux` and `tmux` commands from the skill's `allowed-tools`. A permission prompt for them is a
  finding: stop and ask.
- Every role pane is a full Claude session that costs tokens. Spawn one or two roles per run.
- A role lead may start its own worker panes when it gets work. Those land in this run's project; `wait` records them
  through the creator rule (creator = the recorded role pane, cwd = the scratch project), so cleanup removes them by
  uid.
- A pane whose `projmux.io/creator-agent` annotation does not name a recorded agent, or whose cwd is elsewhere, is
  never recorded: `--agent role:<role>` refuses it, `spawn-check` cannot pass on it, and cleanup stops with exit 4.
  That is a provenance finding to report; never adopt the pane by hand.
- `spawn-check` matches the role label, the project cwd and phase `Running` together, as the skill itself must; a
  pane of another project with the same label does not count.
