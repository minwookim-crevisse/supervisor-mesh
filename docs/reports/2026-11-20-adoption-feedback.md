# Adoption feedback — first real project on 0.2.1 (2026-11-20)

Source: the new project's PM transcript (read-only, maintainer PM). Evidence is quoted tool output; no project data copied.

| # | sev | finding | evidence | proposed fix |
|---|---|---|---|---|
| A1 | P0 | init leaves `[verify]` with every command commented out, so every item verifies `not_run` and nothing can close | project `.mesh/config.toml`: `[verify]` then `# [[verify.commands]]`; PM: "no verify command is configured, so the item cannot close" | init seeds ONE entry `bash scripts/verify.sh` plus a template script (docs drift + structure always; stack checks only when their manifest exists) |
| A2 | P1 | the verify config hash is pinned at `start`, so each later edit to the commands invalidates every ready/running item (`INPUTS_CHANGED`) | PM analysis at 10:29; `Config.verify_hash` covers the commands | A1's single stable entrypoint; say so in init and the config comment |
| A3 | P1 | `work_new` accepts an unknown `type` (`chore`, `gate`); the refusal comes only at `ready`, and `type` cannot be patched: 7 items cancelled and renumbered | `work_transition ready` → `unknown type 'chore'`, `patch: unknown field(s) ['type']` | validate `type` in `work_new`; expose the allowed types as an enum in the tool schema |
| A4 | P1 | `spawn-roles` put 5 leads plus a stray bash shell in one window (> 4 panes); splitting it afterwards was done with raw `tmux break-pane/join-pane`, leaving projmux topology out of sync (`reconcile --dry-run`: outside owner window) | PM 10:24 and 10:29 reports | spawn-roles packs ≤ 4 panes per window itself (`mesh`, `mesh-2`, …) and closes the default shell |
| A5 | P1 | order not guided: the PM created and readied 7 items before any lead existed, then found `spawn-roles` is operator-only | `Skill supervisor-mesh:spawn-roles cannot be used … disable-model-invocation`; `mesh_status` leads `present:false` | D130 guided onboarding (`docs/designs/guided-onboarding.md`): roles stage before the first `run` |
| A6 | P2 | `.mesh/state.json` is tracked but rewritten by mesh constantly, so the tree is dirty right after every commit; no stated policy | PM decision 3 at 10:28 | decide and document: track it and let Ship commit it with each batch, or ignore it |
| A7 | P3 | a fresh repo without `git config user.name` makes Ship improvise an author | PM decision 2 at 10:28 | init preflight prints a hint when the repo has no author configured |
| A8 | P3 | plugin installed after the PM pane started needed `/reload-plugins`; tools invisible until then | operator report | D130 stage 1 preflight |

Not plugin issues: the toolchain version pin (Node) and `!sudo` needing a TTY.

## Added 2026-11-21
| # | sev | finding | evidence | proposed fix |
|---|---|---|---|---|
| A9 | P1 | the PM pane was closed by mistake and the operator created a NEW PM agent; the pin (D109) stayed on the offline agent, so every PM tool in the new pane refused. The refusal hint names only re-pin (D114), which keeps authority but not context; resuming the old agent (`projmux agent resume`) would have kept both | `.mesh/refusals.jsonl`: "this pane is not the pinned PM (D109). To move the pin: …"; old agent offline, new agent live with `role=pm` | the refusal and `mesh_status` say first "if the pinned PM agent is only offline, resume it: `projmux agent resume uid:<pinned>`", re-pin second; fold into D128 (session-continuity recovery routine) |
