---
name: add-agent
description: Add a new role lead to the mesh by stamping the role-lead template into the project's .claude/agents/, inheriting the operating model, Karpathy discipline and evidence gate automatically.
disable-model-invocation: true
argument-hint: "<role>"
allowed-tools: Read Write Edit Bash(ls:*) Bash(mkdir:*) Bash(test:*)
---

# supervisor-mesh: add-agent

Create a new `<role>` lead for THIS project. `$ARGUMENTS` = role name (kebab-case, e.g. `data`, `mobile`, `docs`).

## Steps
1. **Validate the name**: kebab-case `[a-z0-9-]+`. Target: `${CLAUDE_PROJECT_DIR}/.claude/agents/role-lead-<role>.md`.
   🔴 PROJECT level, NOT the installed plugin — survives plugin updates and stays with the project.
2. **Idempotent**: if the target exists, stop and report it — never overwrite.
3. **Stamp** `${CLAUDE_PLUGIN_ROOT}/templates/agents/role-lead.md` into the target, replacing only:
   - `role-lead-<role>` → `role-lead-<role name>` (frontmatter name)
   - `<Role>` / `<ROLE>` → the display title
   🔴 Copy everything between `<!-- INVARIANT:BEGIN` and `<!-- INVARIANT:END -->` byte-for-byte (operating model / Karpathy 4 / evidence gate / peer-message rule). Do not strip or reword it.
4. **Part B scope**: ask the operator what this role owns, its canonical docs (absolute paths), its data boundary and the verification axes it owns. Fill the "Role scope" section with their answers only; leave `<...>` where they have no answer.
5. **Evidence**: show `ls -l` of the new file and confirm the invariant block is present.
6. Report: the role is now discoverable by `/supervisor-mesh:spawn-roles` and `/supervisor-mesh:dispatch`; add it to `AGENTS.md` "Roles in this project" (orchestrator-owned).

Respond in the user's language.
