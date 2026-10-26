# supervisor-mesh verification map

This directory is the maintained source for verifying the user-facing behaviour of the supervisor-mesh plugin. Read this
index before driving the plugin, then use the matching feature file as the recipe. Commands assume the repo root of the
checkout under test as the working directory and `SMV=.claude/skills/verify-supervisor-mesh/bin/smv.py`.

## Baseline preconditions

- Launch and install are done exactly as `../SKILL.md` (Launch) describes, and `SMV_RUN` is exported.
- `"$SMV" plugin-check` and `"$SMV" doctor` both end with `PASS`.
- The scratch project is fresh: before `init` it holds only `.git/` and the `.claude/settings.json` that the
  project-scope marketplace declaration and install wrote (`"$SMV" hash --label pre-init` reports `files=1`).
- The PM pane is the only agent in the run's project (`"$SMV" agents` reports 1) until spawn-roles runs. Later panes
  count only when the creator rule records them (`../SKILL.md`, Hard rules).
- To cover several features in one run, use this order, because each one leaves state the next relies on: init,
  add-agent, spawn-roles, dispatch, gc, slack-setup.
- Never drive a pane, project or process this run did not create; `smv` refuses agent uids missing from `run.json`.

## Driving conventions

- Type one line per `send`. Typed text carries no backticks, no `$`, no `@` (it opens file autocomplete) and no newline.
  The dispatch skill strips backticks and `$` itself, so keep work orders plain words.
- Follow every `send` with a `wait`. Exits 10 to 13 mean stop and ask the operator, then run the `resume:` command
  the STOP line prints (`../SKILL.md`, Stops and resume). Exit 124 means capture and report;
  never resend a slash command blindly.
- Answer the plugin's own conversational questions only with the scripted replies in the feature file. Anything else is
  an unscripted question: stop and ask.
- Create fixtures with `"$SMV" seed`, never through the pane and never inside the checkout.
- Never press keys into a dialog, menu or prompt. Only the operator does that. The one exception is `"$SMV" scope-pick`
  on the `/plugin install` scope menu, which presses Enter only on a verified highlight.
- Take a `hash` before and after every journey and compare with `hash-diff`. When a journey must change files, name each
  expected change with `--allow "ADDED <path>"`, `--allow "MISSING <path>"` or `--allow "CHANGED <path>"` (relative
  scratch-project paths only); every other change, and every allowed change that did not happen, fails.

## Proof and skip reporting

- Capture the user action (the pre-send capture plus `sends.log`, which holds every typed line and key) and the
  resulting state (hashes, transcript extracts, projmux JSON), not only the final screen. A capture keeps only the box,
  footer, menu, dialog or matched lines plus the whole screen's sha256, never the reply area.
- A transcript proof is a `tgrep` / `init-check` / `spawn-check` step file with its exit code. A screen capture alone is
  supporting evidence.
- A no-change proof is a `hash-diff` step reporting `IDENTICAL`. An expected-change proof is a `hash-diff` step
  reporting `ONLY ALLOWED CHANGES`, with each allowance in the step file and `run.json`. An absence proof is a
  `tgrep --absent` step with exit `0`. A projmux proof is the captured `-o json` output.
- Record the feature ID and the entry point (the exact typed line) with every artifact you cite in the report. Name
  every result proven only offline (`"$SMV" selftest`) as such (`../SKILL.md`, Known gaps).
- Report an unreachable path with the attempted command, the exit code and the unmet precondition. Report a stop (10 to
  13) with the capture path and the operator's answer (smv only infers that one came; ask the operator for it).
- Do not report a skipped entry point as verified through a different path. The marketplace is always declared with the
  CLI at project scope; report it as a stand-in for the slash `/plugin marketplace add`, which is not exercised.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behaviour. It then uses exactly
four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behaviour.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with smv` starts with `Preconditions:` and uses labelled bullets that pair each user action with the
   observable result, followed by the exact commands in a fenced block (copy them as they are; no line is wrapped).
4. `Gotchas` lists traps that can waste or invalidate a verification run.

Keep implementation details out of the map. Name only user paths, stable handles (prompt strings, labels, file paths,
summary lines), required state, commands and observable proof.

## Features

- [Init](./init.md) covers the fresh scaffold, the idempotent re-run with unchanged hashes, and the stack question.
- [Add agent](./add-agent.md) covers stamping a project role, the byte-identical INVARIANT block, Part B left blank, and
  both refusals.
- [Spawn roles](./spawn-roles.md) covers creating a role pane, `ROLE LOADED`, the layout, idempotency, and a
  project-added role.
- [Dispatch](./dispatch.md) covers delivery with the standard header, the role's reply, and the refusal for a role with
  no pane.
- [GC](./gc.md) covers structure, doc and code drift reports with nothing deleted.
- [Slack setup](./slack-setup.md) covers the bridge against FakeSlack only: check, send, the armed watcher, the
  allowlist, untrusted text, an unread `.env`, and token hygiene.
