# MCP server

The plugin ships a stdio MCP server (`supervisor-mesh`, `.mcp.json` at the plugin root, `mesh/mcp_server.py`). Once
the plugin is installed at project scope, `/mcp` lists it connected, and the PM pane (projmux label `role=pm`)
drives a work item through the state machine with its eleven tools: `mesh_init` (a dry run, then apply with its
`plan_id`), `work_new`, `work_transition` `ready`, `work_dispatch` to a spawned lead, the lead's `start`, `submit`
and `verify`, and the PM's `close`. Every state change lands in `.mesh/state.json`. This is spec acceptance 4
(`docs/designs/mcp-server-spec.md`), plus the backend's live checks (a) to (g) from `docs/reports/WO-0022-backend.md`.

## Sub-features

- `mcp-install` the commit under test, exported as the scratch marketplace `smv-<run-id>` (only the manifest's
  `name` differs from the commit), installed at project scope as `supervisor-mesh@smv-<run-id>`; the user-level
  `supervisor-mesh` marketplace (GitHub source) keeps its source and is never added, removed or declared.
- `mcp-connected` `/mcp` shows the `supervisor-mesh` server connected.
- `mcp-init` `mesh_init` without `apply` writes nothing and returns a `plan_id`; with `apply` and that `plan_id` it
  scaffolds the pillars and creates `.mesh/` (state, config, launcher, the operator-event hook and the deny rules).
- `mcp-walk` one item: `new` -> `ready` -> `dispatch` (PM) -> `start` -> `submit` -> `verify` (lead) -> `close`
  (PM); `verify` passes on a real command run at the submitted commit.
- `mcp-env` (a) the plugin-declared server under a pane received `PMX_INTERNAL_ACTIVATION_PANE_UID`, equal to its
  claude process's and to the pane uid projmux reports (presence and equality only).
- `mcp-shapes` (b) `projmux get agents` / `get panes -A` JSON carries the key paths `mesh/projmux.py` reads;
  (d) `claude plugin marketplace list --json` rows carry `name` and `source` (key paths only).
- `mcp-dedup` (c) one `--message-ref` sent twice is delivered once; a peer message does not create an operator event
  (it must not fire the PM pane's `UserPromptSubmit` hook as an operator prompt).
- `mcp-prefix` (e) every call carries one tool-name prefix, `mcp__plugin_supervisor-mesh_supervisor-mesh__`, the one
  `skills/*/SKILL.md` allowed-tools name.
- `mcp-idle-kind` (f) the projmux interaction kind of an idle lead pane is recorded; with kind `unknown`,
  `pane_command` refuses (`pane_not_idle`) and types nothing (F15).
- `mcp-reach` (g) all eleven tools the export registers answer at least once (an `invalid_args` or refusal result
  counts: it came from the server).

## How to get to it (user POV)

- Install the plugin at project scope (`/plugin install supervisor-mesh@<marketplace>`, project-scope option), then
  type `/mcp` in the PM pane.
- Ask the PM in plain words to call a tool ("Call mesh_init without apply ..."); the PM pane must carry the label
  `role=pm` (README install, D82), or every pm tool is refused.
- The lead receives the work order as a projmux message from `work_dispatch` and calls its own verbs from its pane.

## Driving it with smv

Preconditions:

- The commit under test exists in this checkout (the live run uses the WO-0029 commit, after WO-0030 landed) and
  `export COMMIT=<its sha>`. Re-read `mesh/identity.py`, `mesh/pane.py` and `mesh/projmux.py` at that commit against
  checks (a) and (f) below before the run: WO-0030 (D108-D112) changes identity and operator-event proof.
- `TMPDIR="$SCRATCH_ROOT" "$SMV" selftest` exits `0`. `SCRATCH_ROOT` is durable, for example
  `~/.local/state/supervisor-mesh-qa/WO-0024/live` (create it first).
- No other smv launch is running (projmux registry lock, `../SKILL.md` Gotchas).
- Typed lines carry no backticks, no `$` that the shell would not expand, and never the tool names
  `companions_install` or `pane_command` (a prompt naming them is an operator event that could authorize them, D85).
  The scripted lines say "the companions install tool" and "the pane command tool" instead.

- **Launch with the PM label.** `launch` exits `0`, or `10` at the folder-trust prompt: stop, the operator answers
  it in the pane, then resume. `doctor --pre` ends with `doctor: PASS`, including the line
  `user marketplace 'supervisor-mesh' source unchanged (doctor vs baseline)`.
  ```
  "$SMV" launch --scratch-root "$SCRATCH_ROOT" --pm-role
  export SMV_RUN="<the SMV_RUN= path launch printed>"
  "$SMV" doctor --pre
  ```
- **Scratch marketplace from the commit.** `mkt-project` exports the commit with `git archive`, renames only the
  manifest's `name` to `smv-<run-id>`, proves the export equals the commit (every other file's blob id; the manifest
  differs at key path `name` only; `evidence/export/tree.txt` and `manifest-keypaths.txt`), declares the export at
  project scope and ends `mkt-project: PASS` with `PLUGIN_ID=supervisor-mesh@smv-<run-id>`. It never names
  `supervisor-mesh` as a marketplace; the guard line before vs after the add must PASS.
  ```
  "$SMV" mkt-project --commit "$COMMIT"
  export PLUGIN_ID="<the PLUGIN_ID= value mkt-project printed>"
  "$SMV" send --text "/reload-plugins"
  "$SMV" wait --ready --settle 5
  ```
- **Project-scope install.** `wait` exits `12` (the scope menu); `scope-pick` ends `scope-pick: PASS`;
  `plugin-check` ends `plugin-check: PASS` (installed copy == commit export, `mesh/` and `.mcp.json` included);
  `doctor` ends `doctor: PASS`.
  ```
  "$SMV" send --text "/plugin install $PLUGIN_ID"
  "$SMV" wait --timeout 180
  "$SMV" scope-pick
  "$SMV" plugin-check
  "$SMV" send --text "/reload-plugins"
  "$SMV" wait --ready --settle 5
  "$SMV" doctor
  "$SMV" hash --label pre-mcp
  ```
- **`/mcp` lists the server.** `/mcp` opens a dialog, so `wait` stops with `13`: that is expected. `mcp-check` reads
  the screen without pressing a key and ends `mcp-check: PASS` (a `supervisor-mesh` line shows connected, none shows a
  failure). Then stop: the operator closes the dialog (Esc). Resume with `wait --ready`.
  ```
  "$SMV" send --text "/mcp"
  "$SMV" wait --timeout 60
  "$SMV" mcp-check
  "$SMV" wait --ready --settle 3
  ```
- **(a) pane identity reaches the server.** `mcp-env` finds the `mesh/mcp_server.py` process under the PM pane and
  ends `mcp-env: PASS`: `PMX_INTERNAL_ACTIVATION_PANE_UID` present, equal to its claude process's and to the pane uid
  projmux reports, cwd the scratch project. Values are compared as keyed digests in memory and never written. Its
  info line says where the script runs from (`installed copy` closes the Known gap on the installed copy executing).
  ```
  "$SMV" mcp-env
  ```
- **(b) and (d) JSON shapes.** Each preset runs one read-only command and requires the key paths `mesh/projmux.py`
  reads (`SHAPE_PRESETS` in `bin/smv.py`: agents `metadata.uid|name|labels.role`, `spec.workspace.cwd`,
  `status.phase|paneRef`; panes `metadata.uid`, `status.activation.agentUID|runtimeID|claude.process.pid`;
  marketplace rows `name`, `source`). Each ends `PASS`; the step file holds key paths and value types only. The
  `agents-kind` preset is info: a FAIL there means projmux reports no interaction kind (see (f)).
  ```
  "$SMV" json-shape --preset agents
  "$SMV" json-shape --preset agents-kind
  "$SMV" json-shape --preset panes
  "$SMV" json-shape --preset marketplaces
  ```
- **`mesh_init` dry run.** The empty scratch project has no `package.json` or `pyproject.toml`, so the line names
  `stack none` (without it `mesh_init` answers `exit_code 3`, stack ambiguous, and no `plan_id`: F8). The first call
  of each MCP tool may raise a permission prompt (stop `11`, the operator answers; resume). `mcp-calls --mesh-init
  dry-run` ends `PASS` only on a call with `applied=False`, a non-empty `plan_id` and `exit_code 0` (F9); its prefix
  check is (e). `hash-diff` prints `IDENTICAL`: a dry run writes nothing.
  ```
  "$SMV" send --text "Call mesh_init once without apply and with stack none; show me its plan_id and summary."
  "$SMV" wait --timeout 600
  "$SMV" mcp-calls --since-last-send --mesh-init dry-run --prefix mcp__plugin_supervisor-mesh_supervisor-mesh__
  "$SMV" hash --label post-dryrun
  "$SMV" hash-diff pre-mcp post-dryrun
  ```
- **`mesh_init` apply.** `mcp-calls --mesh-init apply` ends `PASS` only on a call with `applied=True`,
  `exit_code 0` and the dry run's `plan_id` both as its argument and in its result. `hash-diff` ends `ONLY ALLOWED
  CHANGES` against `mcp-init.allow`: 16 files, the state lock `.mesh/state.lock` (F11) and the CHANGED
  `.claude/settings.json` (at a1bb251; after a template change, re-derive the list from the diff and report it).
  `state-check` ends `PASS` and writes `evidence/state/init.json`.
  ```
  "$SMV" send --text "Call mesh_init with apply true, stack none and the dry run plan_id. Change nothing else."
  "$SMV" wait --timeout 600
  "$SMV" mcp-calls --since-last-send --mesh-init apply --expect-field mesh_init:summary.create=16
  "$SMV" hash --label post-init
  "$SMV" hash-diff pre-mcp post-init --allow-file .claude/skills/verify-supervisor-mesh/features/mcp-init.allow
  "$SMV" state-check --label init
  "$SMV" cmd --label git-status-post-init -- git status --porcelain=v1 -uall
  ```
- **Hook check.** One plain prompt must add exactly one operator event recorded from the PM pane. `+0` means the
  hook `mesh_init` merged into `.claude/settings.json` is not active in this session: stop and ask (the operator may
  need to review it in `/hooks` or restart the pane; smv never does either).
  ```
  "$SMV" send --text "Reply with the single word ready."
  "$SMV" wait --timeout 300
  "$SMV" state-check --label hook --baseline init --expect-operator-delta 1 --expect-operator-from-pm
  ```
- **Verify config and baseline commit.** `mesh-config` is the operator's hand edit (the D96 deny rules keep the PM's
  file tools off `.mesh/`): one verify command `test -f docs/smv-probe.md`. The PM commits the scaffold; the item's
  `start` needs a known HEAD. The commit uses the repo-local identity `launch` set (`smv` / `smv-qa`, F12; never a
  global one). A `git` permission prompt is stop `11`.
  ```
  "$SMV" mesh-config --verify-file docs/smv-probe.md
  "$SMV" send --text "Commit every file in this project with git, message smv baseline. Change no file."
  "$SMV" wait --timeout 600
  "$SMV" cmd --label head-baseline -- git log --oneline -1
  ```
- **Spawn the lead.** As `spawn-roles.md`: `spawn-check` ends `spawn-check qa: PASS`.
  ```
  "$SMV" send --text "/supervisor-mesh:spawn-roles qa"
  "$SMV" wait --timeout 900
  "$SMV" spawn-check --role qa --wait 300
  ```
- **`work_new` and `ready`.** `mcp-calls` shows `work_new` with `to=draft` and its `item_id`; export that id.
  Then `work_transition` with `to=ready`, and `state-check` ends `PASS`.
  ```
  "$SMV" send --text "Call work_new: role qa, type docs, goal commit a new docs/smv-probe.md, done when committed."
  "$SMV" wait --timeout 600
  "$SMV" mcp-calls --since-last-send --expect work_new
  export ITEM="<the item_id=WO-.... mcp-calls printed>"
  "$SMV" send --text "Call work_transition with verb ready for item $ITEM."
  "$SMV" wait --timeout 600
  "$SMV" mcp-calls --since-last-send --expect work_transition
  "$SMV" state-check --label ready --item "$ITEM" --expect-phase ready --expect-verbs new,ready
  ```
- **`work_dispatch`.** The result shows `dispatch.sent=True` and a `dispatch.message_ref`.
  ```
  "$SMV" send --text "Call work_dispatch for item $ITEM."
  "$SMV" wait --timeout 600
  "$SMV" mcp-calls --since-last-send --expect work_dispatch
  ```
- **The lead works the item.** The lead's turn starts from the projmux message (no send into its pane). It may spawn a
  worker (recorded by the creator rule) and raise permission prompts there (stop `11`, the operator answers). Then
  the lead's `mcp-calls` shows `work_transition` calls, and `state-check` ends `PASS` with the item `verified`, the
  history in order and every evidence run passed at the submitted commit.
  `wait --agent role:qa` without `--ready` exits `2` here, because smv never sent into the lead's pane (F13); wait for
  a ready pane that stays idle for 60 s, then check the state, and repeat both until the item is `verified` or the
  lead asks something (a stop).
  ```
  "$SMV" wait --agent role:qa --ready --settle 60 --timeout 2400
  "$SMV" agents --label post-lead
  "$SMV" mcp-calls --agent role:qa --expect work_transition
  "$SMV" state-check --label verified --item "$ITEM" --expect-phase verified --expect-evidence-pass
  "$SMV" state-check --label verified-walk --item "$ITEM" --expect-verbs new,ready,dispatch,start,submit,verify
  ```
- **(c) message-ref dedup and the hook.** `msg-probe` sends one probe twice with one `--message-ref` from the lead's
  agent to the PM and ends `PASS` on exactly one delivery. `state-check` ends `PASS` with `+0` operator events: a
  peer message is not an operator prompt. `+1` or more is a security finding (peer text recorded as an operator
  event); report it with the step files, do not retry.
  ```
  "$SMV" wait --ready --settle 5
  "$SMV" state-check --label pre-probe
  "$SMV" msg-probe --to pm --source role:qa
  "$SMV" wait --timeout 300
  "$SMV" state-check --label post-probe --baseline pre-probe --expect-operator-delta 0
  ```
- **`close`.** `state-check` ends `PASS` with the item `closed`.
  ```
  "$SMV" send --text "Call work_transition with verb close for item $ITEM."
  "$SMV" wait --timeout 600
  "$SMV" mcp-calls --since-last-send --expect work_transition
  "$SMV" state-check --label closed --item "$ITEM" --expect-phase closed --expect-evidence-pass
  "$SMV" state-check --label closed-walk --item "$ITEM" --expect-verbs new,ready,dispatch,start,submit,verify,close
  ```
- **(f) idle interaction kind.** With the lead idle, `status` prints projmux's interaction kind. Record it. If it is
  `unknown` (or anything outside `idle`, `response_complete`), `pane_command` must refuse: this needs an operator
  event naming a harmless command, so it is the operator's step, never smv's. Stop and ask the operator to type
  into the PM pane themselves, for example: Use the pane command tool to type /cost into the qa lead pane. Then
  `mcp-calls` must show the call refused, and `state-check` lists the refusal `pane_command:pane_not_idle`; the
  lead's screen (`capture`) shows nothing typed. If the kind is `idle`, skip the probe and report the kind.
  ```
  "$SMV" status --agent role:qa
  "$SMV" mcp-calls --since-last-send --expect pane_command=error
  "$SMV" state-check --label f15
  "$SMV" capture --agent role:qa --label f15-lead
  ```
- **(g) all eleven tools.** `mcp-calls --all-tools` ends `PASS`: every tool the export's `mesh/tools.py` registers
  has a call with a result, all with the one prefix (e).
  ```
  "$SMV" send --text "Call mesh_status, mesh_metrics, work_list and work_show for $ITEM; report ok or the error code."
  "$SMV" wait --timeout 900
  "$SMV" send --text "Call tracker_render with dry_run true; report ok or the error code."
  "$SMV" wait --timeout 600
  "$SMV" send --text "Call the pane command tool with no arguments; report only the error code."
  "$SMV" wait --timeout 600
  "$SMV" send --text "Call the companions install tool with no arguments; report only the error code."
  "$SMV" wait --timeout 600
  "$SMV" mcp-calls --all-tools --prefix mcp__plugin_supervisor-mesh_supervisor-mesh__
  ```
- **Final state and cleanup.** `hash` and `state-check` keep the final file hashes and the hashed `state.json` with
  its extracted fields. `cleanup --dry-run` names the uninstall of `$PLUGIN_ID`, the removal of `smv-<run-id>` at
  project scope and the recorded agents only. `cleanup` ends `cleanup: PASS`, including
  `scratch marketplace 'smv-<run-id>' gone from the registry`, the guard source line, user `settings.json` unchanged,
  marketplace names equal to the baseline and `installed_plugins.json` changed only under `@smv-<run-id>`.
  ```
  "$SMV" hash --label final
  "$SMV" state-check --label final
  "$SMV" cleanup --dry-run
  "$SMV" cleanup
  ls "$SMV_RUN/evidence"
  ```
  From `$SMV_RUN/evidence`, `sha256sum -c MANIFEST.sha256` reports every file `OK`.

## Gotchas

- Never `claude plugin marketplace add` anything named `supervisor-mesh`, and never use the checkout itself as the
  source: the registry is keyed by name, and a project-scope add of that name rewrites the user's GitHub entry for
  every project ("now points at ..."). `mkt-project` always exports and renames; exit `21` means the scratch name is
  taken: stop and ask.
- The export is the commit, not the working tree: uncommitted backend changes are not under test. Pass the commit
  with `--commit`; one commit per run (a second commit is exit `2`, launch a new run).
- Stops to expect, each answered by the operator only: the folder-trust prompt at launch (`10`, maybe again for a
  spawned pane); the scope menu (`12`, `scope-pick`); the `/mcp` dialog (`13`, the operator closes it); a permission
  prompt on the first call of each MCP tool, on `git commit`, and on the lead's or worker's file edits and commit
  (`11`, unless the pane runs in auto mode: then say so, permission coverage is not exercised); any `/hooks` or
  restart the hook check asks for; the (f) probe, which only the operator types.
- The MCP server starts with the session. After `/reload-plugins`, if `/mcp` shows no `supervisor-mesh` line, the
  operator restarts the pane; smv never does.
- A PM pane without `role=pm` resolves as `unknown`: `mesh_status` shows `identity.kind=unknown` and every pm verb is
  refused. Always launch with `--pm-role`.
- The `mesh_init` apply changes `.claude/settings.json` in the scratch project (the hook and deny rules): that is
  expected here, unlike the init journey's `pre-init` rule.
- The verify command runs in a disposable worktree at the submitted commit: `docs/smv-probe.md` must be in that
  commit. A lead that edits without committing fails `submit` (commit unchanged), which is a finding about the work
  order, not the harness.
- `msg-probe` makes the PM take a turn (it replies `ack`); wait for it before the next `send`.
- `mcp-calls` keeps the whitelisted fields of each result only (ids, phases, codes, plan id, attempt and evidence
  runs); a goal, a message or a path in a result never reaches the evidence. The `/mcp` screen parser and the MCP
  transcript fixtures are synthetic until the first live run: save the live captures (redacted) into
  `../tests/fixtures/` and re-run `selftest`.
