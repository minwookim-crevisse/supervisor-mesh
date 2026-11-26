---
name: spawn-roles
description: Create the standard mesh role panes via projmux — explicit, visible mesh setup. Hard prerequisite for dispatch (dispatch never auto-spawns).
disable-model-invocation: true
argument-hint: "[role ...]"
allowed-tools: Read Bash(projmux:*) Bash(tmux:*) Bash(ls:*)
---

# supervisor-mesh: spawn-roles

Create one projmux agent pane per mesh role so `/supervisor-mesh:dispatch` has live targets. This is the explicit setup ritual.

## Steps
1. **Prereq.** `projmux --help` must succeed; otherwise stop and point to the projmux install (README → Prerequisites).
2. **Resolve the role list** — union of:
   - roles named in the project's `AGENTS.md` "Roles in this project" (Part B);
   - project-added agents: `${CLAUDE_PROJECT_DIR}/.claude/agents/role-lead-*.md` (from `/supervisor-mesh:add-agent`);
   - plugin base roles: `${CLAUDE_PLUGIN_ROOT}/agents/` (pm-orchestrator, role-lead-fe/qa/designer/backend/security/ship, scope-mapper).
   If `$ARGUMENTS` lists roles, limit to those. A project agent with the same name wins over the plugin one.
3. **Find existing panes.** `projmux get agents -o json` — an item is THIS project's live `<role>` only if all three hold: `metadata.labels.role == <role>`, `spec.workspace.cwd == ${CLAUDE_PROJECT_DIR}`, `status.phase == "Running"`. (Several repos can share one projmux project; never match on the label alone.) Skip live roles (idempotent).
4. **Create missing panes VISIBLY, at most 4 panes per window**, so the operator can read every pane:
   - Pack the roles in order into mesh windows of at most 4 role panes each: the first window is named `mesh`, the next `mesh-2`, then `mesh-3`, … (5 roles: 4 in `mesh`, 1 in `mesh-2`). Reuse a window of that name that already exists and has room; never put a fifth pane into a window.
   - Create each window through projmux: `projmux create window -p <project> --name mesh -o uid` (the next one `--name mesh-2`, …). Keep the window uid it prints.
   - Create each role pane in its window: `projmux create agent -p <project> -w uid:<window uid> --placement right --cwd "${CLAUDE_PROJECT_DIR}" --provider claude --name role-<role> --label role=<role> --label mesh=supervisor-mesh -- "<seed>"`.
   - A new window starts with one default shell pane. Once the window's first role pane exists, close that shell pane through projmux: find it with `projmux get panes -p <project> -w uid:<window uid> -o json` (the pane that no agent owns), preview with `projmux delete pane uid:<shell pane uid> --dry-run`, then run the same command with `--yes` instead of `--dry-run`.
   - Then lay each window out evenly: `tmux -L projmux select-layout -t <the window's tmux window> tiled` (find it with `tmux -L projmux display -p -t <a role pane of that window> '#{window_id}'`, the pane id from `projmux describe agent uid:<agent uid>`). That is the only raw tmux call here.
   - 🔴 Never move panes with raw tmux (`tmux break-pane`, `tmux join-pane`, `tmux move-pane`, `tmux kill-pane`): projmux would lose track of its topology. Create, place and close panes through projmux only.
   - Tell the operator the name of every window and which roles it holds (e.g. `mesh`: backend, fe, qa, designer; `mesh-2`: ship), and how to open one: `projmux focus window uid:<window uid>`.
   🔴 **Role bootstrap seed** — the pane must load its role from the definition file FIRST, not from an ad-hoc description. Resolve the absolute path of the role file (project `.claude/agents/role-lead-<role>.md` wins, else `${CLAUDE_PLUGIN_ROOT}/agents/role-lead-<role>.md`) and seed:
   "Supervisor-mesh role bootstrap. Before anything else: 1) Read your role definition at <abs role file> and adopt it as your operating role for this whole session. 2) Read AGENTS.md in this project. 3) Reply in this pane with: ROLE LOADED <agent-name> (the definition's frontmatter name, e.g. role-lead-qa), then the section headings you adopted. Then wait for work orders from the orchestrator. Do not change any files."
   Seed hygiene: no backticks, no `$`, no `$(`.
5. **Activation check.** If create reports "activation unconfirmed", the new Claude pane is usually stopped at the folder-trust prompt (first run in this folder). Tell the operator to answer it in the pane (`projmux focus` to it) — never answer a trust prompt on their behalf. Then re-check with `projmux describe agent uid:<uid>`.
6. **Verify with evidence.** Re-run `projmux get agents -o json` and print the mesh map `role → agent UID` from that output, and confirm each agent printed `ROLE LOADED <agent-name>` — read it from the agent's transcript (`projmux describe agent uid:<uid>` → `TranscriptPath`), because narrow panes wrap and scroll the line away. A role without a UID, or without the ROLE LOADED line, is NOT ready — report it as a failure; do not dispatch to it.

Panes are transport instances; the discipline comes from the agent definitions. Respond in the user's language.
