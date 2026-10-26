# Init

`/supervisor-mesh:init` lays the primed 4-pillar harness into the current project: it previews, applies, and shows
the scaffold output verbatim. A re-run is idempotent: it creates and merges nothing and leaves every file
byte-identical.

## Sub-features

- `init-fresh` creates every pillar file in an empty repo; the apply run's summary reads `created=N merged=0
  skipped=0` with N equal to the files added on disk.
- `init-rerun` re-run on the same project: every apply run's summary reads `created=0 merged=0`, and every file's
  sha256 is unchanged.
- `init-stack-arg` a stack argument (`python`) is used as given: `.importlinter` and `pyproject.toml` are created and
  no stack question is asked.
- `init-stack-ask` with no manifest and no argument the skill asks exactly one question (ts / python / none) and
  writes nothing until answered.

## How to get to it (user POV)

- Type `/supervisor-mesh:init python` in the project's Claude pane.
- Type `/supervisor-mesh:init` with no argument in a project with no `package.json` and no `pyproject.toml`, then
  answer the stack question.

## Driving it with smv

Preconditions:

- `"$SMV" plugin-check` and `"$SMV" doctor` end with `PASS`.
- The scratch project holds only `.git/` and `.claude/settings.json`; there is no `package.json` or `pyproject.toml`.
- No other `send` happens between an init command and its `init-check`, except the scripted replies below.

- **Snapshot.** Record the starting tree. The hash step prints `files=1`.
  ```
  "$SMV" hash --label pre-init
  "$SMV" cmd --label git-status-pre-init -- git status --porcelain=v1 -uall
  ```
- **Fresh run.** Type the command with the stack argument. `wait` exits `0` with `DONE status=response_complete`.
  ```
  "$SMV" send --text "/supervisor-mesh:init python"
  "$SMV" wait --timeout 900
  ```
- **Fresh proof.** Hash the result and check it. `init-check` ends with `init-check fresh: PASS`. Its first info line
  counts the `scaffold.py` runs since the send (apply, dry-run, other) and the agent's echoed summary lines, which are
  never proof; another names the `scaffold.py` the agent ran (checkout or installed copy). The proof is the apply run:
  the Bash call without `--dry-run` on the scratch project and the result with its tool_use id. Its summary is
  `created=14 merged=0 skipped=0` with the current templates, its `CREATE` list equals the 14 files added on disk
  (`AGENTS.md`, `.gitignore`, `pyproject.toml`, `.importlinter`, `scripts/gc/check_structure.py` and the rest), and no
  pre-existing file changed.
  ```
  "$SMV" hash --label post-init
  "$SMV" init-check fresh --before pre-init --after post-init
  "$SMV" cmd --label git-status-post-init -- git status --porcelain=v1 -uall
  ```
- **Fresh screen.** The capture shows the scaffold output or its summary in the pane.
  ```
  "$SMV" capture --label init-fresh
  ```
- **Re-run.** Type the same command again. `wait` exits `0`.
  ```
  "$SMV" send --text "/supervisor-mesh:init python"
  "$SMV" wait --timeout 900
  ```
- **Re-run proof.** `init-check` ends with `init-check rerun: PASS`: every apply run's summary is `created=0 merged=0`
  (expect `skipped=14`) and the hash comparison is identical. `hash-diff` prints `IDENTICAL`, and the last step
  prints exactly the lines of the `git-status-post-init` step.
  ```
  "$SMV" hash --label post-rerun
  "$SMV" init-check rerun --before post-init --after post-rerun
  "$SMV" hash-diff post-init post-rerun
  "$SMV" cmd --label git-status-post-rerun -- git status --porcelain=v1 -uall
  ```
- **Re-run screen.**
  ```
  "$SMV" capture --label init-rerun
  ```
- **Stack question (own run).** In a new run, before any init, type the bare command. The `tgrep` step needs at
  least one hit and `hash-diff` prints `IDENTICAL`. Then answer, and continue with **Fresh proof** using `pre-ask` as
  `--before`.
  ```
  "$SMV" hash --label pre-ask
  "$SMV" send --text "/supervisor-mesh:init"
  "$SMV" wait --timeout 600
  "$SMV" tgrep --since-last-send --assistant-only --pattern '(?is)\b(ts|typescript)\b.{0,80}\bpython\b.{0,80}\bnone\b'
  "$SMV" hash --label post-ask
  "$SMV" hash-diff pre-ask post-ask
  "$SMV" send --text "python"
  "$SMV" wait --timeout 900
  ```

## Gotchas

- 14 is the count for the current templates with `python` on an empty repo. When templates change, `init-check` still
  compares the output with the disk, not with 14. Preview the new count with the command below (a dry-run against a
  path that does not exist writes nothing).
  ```
  python3 -B skills/init/scaffold.py --target "$SMV_RUN/preview-empty" --stack python --dry-run
  ```
- The pane folds long tool output (`+N lines`), so the screen may not show the summary line. The proof is the
  transcript, which `init-check` reads.
- `init-check` reads the proof rows of every transcript record after the last `send` (the same rows the extract
  keeps). If you had to send a scripted reply, the script output that followed it is still in range.
- An agent that only previews (`--dry-run`) or only reports a summary in its reply fails `init-check`: there is no
  apply run to prove. If the turn stopped after the preview, that is the finding; do not run the script yourself.
- If the turn asks before running the script (for example whether to run again on an initialized project), reply
  with `"$SMV" send --text "Yes, run the scaffold script as the skill says."` and wait again. Never edit pillar files
  by hand.
- If the turn ends with Part B questions (project name, roles, ship role), do not answer them: the skill must leave
  the blanks, and inventing values would break the re-run hashes.
- A permission prompt for `python3` means the skill's `allowed-tools` did not apply. That is a finding: stop and ask,
  do not answer.
- With the local-directory marketplace the PM may run `python3 <checkout>/skills/init/scaffold.py` instead of the
  installed copy (run #4 did). `init-check` names the path in its info line; report it, because the journey then
  proves the checkout's script, not the installed one (`../SKILL.md`, Known gaps).
- `.claude/settings.json` is written by the project-scope marketplace declaration (`mkt-project`) and install before
  init. It is part of the `pre-init` hashes and must stay unchanged.
