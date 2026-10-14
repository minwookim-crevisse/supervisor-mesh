---
name: spawn-roles
description: Create the standard mesh role panes via projmux — explicit, visible mesh setup. Hard prerequisite for dispatch (dispatch never auto-spawns).
disable-model-invocation: true
argument-hint: "[role ...]"
allowed-tools: Read Bash(projmux:*) Bash(ls:*)
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
3. **Find existing panes.** `projmux get agents -o json` — an agent labeled `role=<role>` already counts as live. Skip it (idempotent).
4. **Create missing panes**, one per role:
   `projmux create agent --name <role> --label role=<role> --label mesh=supervisor-mesh -- "<seed>"`
   Seed text: "You are the <role> in this project's supervisor mesh. Adopt the agent definition <agent-name> (supervisor-mesh plugin or .claude/agents/). Read AGENTS.md first, then wait for work orders." Seed hygiene: no backticks, no `$`, no `$(`.
5. **Verify with evidence.** Re-run `projmux get agents -o json` and print the mesh map `role → agent UID` from that output. A role without a UID in the captured output is NOT spawned — report it as a failure.

Panes are transport instances; the discipline comes from the agent definitions. Respond in the user's language.
