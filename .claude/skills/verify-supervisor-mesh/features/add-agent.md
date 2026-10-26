# Add agent

`/supervisor-mesh:add-agent <role>` stamps a new role lead into the project's `.claude/agents/` from the plugin's
role-lead template, keeps the INVARIANT block byte-for-byte, asks the operator for the Part B role scope instead of
inventing it, and refuses an existing role or an invalid name.

## Sub-features

- `add-new` creates `.claude/agents/role-lead-<role>.md` with frontmatter `name: role-lead-<role>` and the INVARIANT
  block identical to `templates/agents/role-lead.md`.
- `add-part-b` asks the operator for the role scope; with no answers the Role scope section keeps the template's
  `<...>` blanks.
- `add-existing` refuses a role whose file exists and leaves the file byte-identical.
- `add-invalid` refuses a name that is not kebab-case and creates no file.

## How to get to it (user POV)

- Type `/supervisor-mesh:add-agent data` in the project's Claude pane.

## Driving it with smv

Preconditions:

- Init is done (`features/init.md`), and `.claude/agents/role-lead-data.md` does not exist.
- `"$SMV" doctor` ends with `PASS`.

- **Snapshot.**
  ```
  "$SMV" hash --label pre-add
  ```
- **Stamp.** Type the command. `wait` exits `0`.
  ```
  "$SMV" send --text "/supervisor-mesh:add-agent data"
  "$SMV" wait --timeout 900
  ```
- **Part B reply.** If the turn ended with role-scope questions, give the scripted no-answer reply. `wait` exits `0`.
  ```
  "$SMV" send --text "I have no answers yet. Leave every Role scope value blank exactly as the template has it."
  "$SMV" wait --timeout 600
  ```
- **File proof.** `invariant` ends with `invariant: PASS`: the INVARIANT block is byte-identical, the name is
  `role-lead-data`, and the Role scope section is the template's. A file that lost its BEGIN or END marker is
  `invariant: FAIL`, not a crash.
  ```
  "$SMV" cmd --label ls-agents -- ls -l .claude/agents
  "$SMV" invariant --file .claude/agents/role-lead-data.md --role data --blank-part-b
  ```
- **Change set.** `hash-diff` prints `ONLY ALLOWED CHANGES`, exit `0`. Any other line, for example
  `CHANGED AGENTS.md`, makes it `DIFFERENT` (exit `1`) and is a finding: the skill only tells the orchestrator to add
  the role there. An `UNMET` line means the file was not created.
  ```
  "$SMV" hash --label post-add
  "$SMV" hash-diff pre-add post-add --allow "ADDED .claude/agents/role-lead-data.md"
  ```
- **Existing role.** Type the same command again. `hash-diff` prints `IDENTICAL`; the `tgrep` step keeps the refusal
  wording (at least one hit).
  ```
  "$SMV" send --text "/supervisor-mesh:add-agent data"
  "$SMV" wait --timeout 600
  "$SMV" hash --label post-add-again
  "$SMV" hash-diff post-add post-add-again
  "$SMV" tgrep --since-last-send --assistant-only --pattern '(?i)(exists|overwrit)'
  ```
- **Invalid name.** Type a name that is not kebab-case. `hash-diff` prints `IDENTICAL`; the `tgrep` step keeps the
  refusal (at least one hit).
  ```
  "$SMV" send --text "/supervisor-mesh:add-agent Data Role!"
  "$SMV" wait --timeout 600
  "$SMV" hash --label post-add-invalid
  "$SMV" hash-diff post-add-again post-add-invalid
  "$SMV" tgrep --since-last-send --assistant-only --pattern '(?i)kebab'
  ```
- **Screen.**
  ```
  "$SMV" capture --label add-agent
  ```

## Gotchas

- Writing into `.claude/agents/` can raise a permission prompt even though `Write` is in the skill's
  `allowed-tools`. `wait` exits 11: stop and ask the operator; do not answer it.
- If the Part B questions come as a selection menu instead of text, `wait` exits 13. That menu is unscripted: stop
  and ask.
- Compare the INVARIANT block with the checkout's template only after `plugin-check` proved the installed copy
  equals the checkout; otherwise you compare against the wrong template.
- `--allow` takes only relative scratch-project paths. A home, absolute, `..` or checkout path is refused with exit 2
  and nothing is excused; never widen an allowance to make a journey pass.
- The new role is what `features/spawn-roles.md` uses for `spawn-project-role`. Keep the file until that journey
  ran; cleanup removes it with the scratch project.
