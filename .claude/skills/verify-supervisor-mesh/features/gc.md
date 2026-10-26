# GC

`/supervisor-mesh:gc [code|doc|structure]` runs the harness drift checks on the project, reports each finding with
the captured tool line and a suggested action, and deletes nothing unless the operator confirms an item.

## Sub-features

- `gc-structure` seeded temp and backup files (`temp_*`, `*_old.*`) are reported as structure drift.
- `gc-doc` AGENTS.md paths and glossary-deprecated wordings are checked and the result reported.
- `gc-code` the dead-code detector is run, or its absence is reported without installing it.
- `gc-read-only` detection and a declined cleanup leave every file byte-identical.

## How to get to it (user POV)

- Type `/supervisor-mesh:gc structure`, `/supervisor-mesh:gc doc` or `/supervisor-mesh:gc code` in the project's
  Claude pane.
- Type `/supervisor-mesh:gc` with no argument for all three checks.

## Driving it with smv

Preconditions:

- Init is done: `scripts/gc/check_structure.py` and `scripts/gc/check_docs_drift.py` exist in the scratch project.
- slack-setup has not run yet in this run (its `.env` and `.slack-state` would join the findings).

- **Seed.** Create the fixtures inside the scratch project (never in the checkout).
  ```
  "$SMV" seed --file temp_smv_seed.txt --text "smv gc fixture"
  "$SMV" seed --file notes_old.md --text "smv gc fixture"
  "$SMV" hash --label pre-gc
  ```
- **Structure.** Type the check. `wait` exits `0`.
  ```
  "$SMV" send --text "/supervisor-mesh:gc structure"
  "$SMV" wait --timeout 900
  ```
- **Structure proof.** Each `tgrep` step has at least one hit.
  ```
  "$SMV" tgrep --since-last-send --pattern 'STRUCTURE DRIFT'
  "$SMV" tgrep --since-last-send --pattern 'temp_smv_seed\.txt'
  "$SMV" tgrep --since-last-send --pattern 'notes_old\.md'
  ```
- **Nothing deleted.** `hash-diff` prints `IDENTICAL`.
  ```
  "$SMV" hash --label post-gc-structure
  "$SMV" hash-diff pre-gc post-gc-structure
  ```
- **Decline.** If the turn asks what to delete, decline. `hash-diff` prints `IDENTICAL`.
  ```
  "$SMV" send --text "Delete nothing. Keep every file as it is."
  "$SMV" wait --timeout 600
  "$SMV" hash --label post-gc-decline
  "$SMV" hash-diff pre-gc post-gc-decline
  ```
- **Doc.** The `tgrep` step has at least one hit; record which one. `hash-diff` prints `IDENTICAL`.
  ```
  "$SMV" send --text "/supervisor-mesh:gc doc"
  "$SMV" wait --timeout 900
  "$SMV" tgrep --since-last-send --pattern 'no doc drift|DOC DRIFT'
  "$SMV" hash --label post-gc-doc
  "$SMV" hash-diff pre-gc post-gc-doc
  ```
- **Code.** The `tgrep` step has at least one hit: the run or the absence is reported. `hash-diff` prints
  `IDENTICAL`, so nothing was installed into the project. The `which` step exits `1` when vulture is not installed;
  record which.
  ```
  "$SMV" cmd --label which-vulture -- which vulture
  "$SMV" send --text "/supervisor-mesh:gc code"
  "$SMV" wait --timeout 900
  "$SMV" tgrep --since-last-send --assistant-only --pattern '(?i)vulture'
  "$SMV" hash --label post-gc-code
  "$SMV" hash-diff pre-gc post-gc-code
  ```
- **Screen.**
  ```
  "$SMV" capture --label gc
  ```

## Gotchas

- Seed with the exact names above. The skill text itself mentions `temp/backup`, so a loose pattern such as `temp_`
  proves nothing.
- Never confirm a deletion in this journey. If the pane asks for permission to run `rm`, `wait` exits 11: stop and
  ask the operator.
- The AGENTS.md laid by init names paths that may not exist yet, so `DOC DRIFT` can be a correct finding. Either
  result is evidence; the gate is that nothing changed.
- With the project-scope install, `.claude/settings.json` is an untracked file. If the pane lists it from
  `git status --porcelain` as scratch output, that is a finding about the skill's wording, not a failure of the drift
  scripts.
- Python-only stack: the code check uses `vulture`. A TypeScript stack would use `npx`, which may try the network;
  this map does not cover it.
