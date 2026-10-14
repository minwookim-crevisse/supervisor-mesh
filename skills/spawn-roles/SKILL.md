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
4. **Create missing panes VISIBLY**, one per role, split into the orchestrator's own window so the operator watches the mesh talk:
   - find your window: `projmux describe agent uid:<your own agent uid>` → `Owner: project/<p> window/<win-uid>`;
   - `projmux create agent -p <p> -w uid:<win-uid> --placement right --cwd "${CLAUDE_PROJECT_DIR}" --provider claude --name role-<role> --label role=<role> --label mesh=supervisor-mesh -- "<seed>"` for every role.
   - Then lay the window out side by side with equal widths: `tmux -L projmux select-layout -t <win-uid's tmux window> even-horizontal` (find it with `tmux -L projmux display -p -t <your pane> '#{window_id}'`). The operator must be able to read every pane.
   - Many roles (more than ~3) or the operator asks for it: put them in one shared window instead, `-p <project> --create-window --window mesh` (`--create-window` needs an exact `--window` name), and tell the operator how to open it.
   🔴 **Role bootstrap seed** — the pane must load its role from the definition file FIRST, not from an ad-hoc description. Resolve the absolute path of the role file (project `.claude/agents/role-lead-<role>.md` wins, else `${CLAUDE_PLUGIN_ROOT}/agents/role-lead-<role>.md`) and seed:
   "Supervisor-mesh role bootstrap. Before anything else: 1) Read your role definition at <abs role file> and adopt it as your operating role for this whole session. 2) Read AGENTS.md in this project. 3) Reply in this pane with: ROLE LOADED <agent-name> (the definition's frontmatter name, e.g. role-lead-qa), then the section headings you adopted. Then wait for work orders from the orchestrator. Do not change any files."
   Seed hygiene: no backticks, no `$`, no `$(`.
5. **Activation check.** If create reports "activation unconfirmed", the new Claude pane is usually stopped at the folder-trust prompt (first run in this folder). Tell the operator to answer it in the pane (`projmux focus` to it) — never answer a trust prompt on their behalf. Then re-check with `projmux describe agent uid:<uid>`.
6. **Verify with evidence.** Re-run `projmux get agents -o json` and print the mesh map `role → agent UID` from that output, and confirm each agent printed `ROLE LOADED <agent-name>` — read it from the agent's transcript (`projmux describe agent uid:<uid>` → `TranscriptPath`), because narrow panes wrap and scroll the line away. A role without a UID, or without the ROLE LOADED line, is NOT ready — report it as a failure; do not dispatch to it.

Panes are transport instances; the discipline comes from the agent definitions. Respond in the user's language.
