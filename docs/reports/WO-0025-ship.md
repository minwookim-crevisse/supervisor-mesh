# WO-0025 — Release 0.2.0 (ship) · PREP report

> Ship lead. Prep only: no bump, no commit, no push. Inspected at HEAD `0501f50` on 2026-11-18, with backend (`mesh/`) and QA (`.claude/skills/verify-supervisor-mesh/`) editing the tree in parallel. The Release part waits for WO-0030, WO-0023, WO-0024 and the PM gate.
> Every finding below cites a captured command output. Scratch probes ran on a `git archive HEAD` copy under the session scratchpad, never on this checkout.

## 1. Current manifests

| file | version | notes |
|---|---|---|
| `.claude-plugin/plugin.json` | `"version": "0.1.2"` | no `icon` key |
| `.claude-plugin/marketplace.json` | none | the single plugin entry has `name`, `source: "./"` and `description` only |
| `mesh/mcp_server.py:41` | reads `plugin.json` | the MCP `serverInfo.version` comes from `plugin.json`, so the bump needs no code change |
| `README.md:117` | "Pre-release (0.1.x)" | stale after the bump: needs a one-line edit |
| `pyproject.toml` | `supervisor-mesh-dev` `0.1.0` | repo-own dev package, not shipped; leave unchanged |

Live MCP probe against HEAD (stdio `initialize` + `tools/list`, cwd and `CLAUDE_PROJECT_DIR` set to an empty scratch dir; the scratch dir stayed empty afterwards):
```
server {'name': 'supervisor-mesh', 'version': '0.1.2'}
11 ['mesh_init', 'mesh_status', 'mesh_metrics', 'work_list', 'work_show', 'work_new', 'work_dispatch', 'work_transition', 'tracker_render', 'pane_command', 'companions_install']
exit=0
```

## 2. `claude plugin validate . --strict` (Claude Code 2.1.327) on this checkout
Result: `✔ Validation passed`, exit=0. It printed one plugin-directory advisory, which does not change the result:
```
❯ .claude-plugin/plugin.json icon: No icon (ICON_MISSING). ... The validator looked for `icon` in plugin.json and for a file at .claude-plugin/icon.png or .svg, assets/icon.png or .svg, or a similar path.
  Fix: Add a square PNG to the plugin folder at .claude-plugin/icon.png, or set `icon` in plugin.json to the path of a square PNG or JPEG in the plugin: 512 to 2048 px on each side, under 2 MB. SVG and WebP are not accepted. That file may become the listing icon only once: the first time the plugin is saved or submitted in the developer portal. Adding or changing it later does not change the icon.
```

### 2a. What an icon entry needs (scratch probes)
| probe | result |
|---|---|
| file `.claude-plugin/icon.png`, 512×512 PNG, no `icon` key | `✔ These checks found nothing to report` · `✔ Validation passed` · exit=0 |
| `"icon": "./assets/logo.png"` in plugin.json, 256×256 PNG | `✔ These checks found nothing to report` · `✔ Validation passed` · exit=0 |

Conclusions:
- A repo-local file is allowed. Placing a square PNG at `.claude-plugin/icon.png` needs no manifest change. Otherwise, `icon` in plugin.json takes a relative path to a PNG or JPEG.
- The local validator does not enforce the 512–2048 px rule: a 256 px file passed. Follow the advisory anyway (square PNG, 512–2048 px, < 2 MB, no SVG/WebP).
- The listing icon is fixed the first time the plugin is saved or submitted in the developer portal. A placeholder icon would be locked in, so ship a final asset or none.
- **No icon asset exists in the repo** (`find` for `icon*`/`*.png` returns nothing). Making one is design work, not Ship's. Options for the PM: (a) release without an icon (validate already exits 0; the advisory stays), or (b) order a final 512–2048 px square PNG from a designer lead before the release. Recommendation: (a), unless a final asset is ready.

### 2b. Does marketplace.json carry a version?
No. A scratch probe added `"version": "0.2.0"` to the marketplace entry while plugin.json was still 0.1.2:
```
⚠ plugins[0].version: Entry declares version "0.2.0" but .claude-plugin/plugin.json says "0.1.2". At install time, plugin.json wins (calculatePluginVersion precedence) — the entry version is silently ignored.
✘ Validation failed (--strict treats warnings as errors)   exit=1
```
Recommendation: keep marketplace.json without a version. `plugin.json` stays the single source. A second copy only adds a way to fail `--strict`.

## 3. Files the release commit touches (exact)
1. `.claude-plugin/plugin.json`: `"version": "0.1.2"` → `"0.2.0"`. Add `icon` only if option (b) supplies a final asset at a non-default path.
2. `README.md:117`: `Pre-release (0.1.x).` → state 0.2.0. Wording is the PM's call; suggested: `0.2.0 (MCP server MVP).`
3. Only under option (b): the icon file (`.claude-plugin/icon.png`).

Not touched: `.claude-plugin/marketplace.json` (no version), `pyproject.toml` (dev package), `mesh/` (version read at runtime), `docs/tracker.md`, `docs/decisions/`, `docs/work-orders/` (PM-owned).

The release commit sits on top of whatever batch-M commits land first (WO-0030 fixes, the WO-0023/WO-0024 reports, the QA `smv.py` I001 fix). Those land as their own commits with exact path lists, before the release commit.

## 4. Draft release commit message
```
release: 0.2.0 - bundled stdio MCP server (11 tools)

Bumps .claude-plugin/plugin.json to 0.2.0 (single version source; the MCP
server reports it in serverInfo). README version line updated.

0.2.0 ships the mesh harness as a stdlib-only (Python 3.11+) stdio MCP
server bundled via .mcp.json: mesh_init, mesh_status, mesh_metrics,
work_list, work_show, work_new, work_transition, work_dispatch,
tracker_render, pane_command, companions_install. Decisions D77-D114 in
docs/designs/mcp-server-spec.md. Accepted risks: D86, D96 residual, D99, D88.

<standard Co-Authored-By trailer for Claude Opus 5.5, as in 65af1b8 / 0501f50>
```
(The trailer's email address is left out here because `scripts/scrub.sh` flags emails in repo files. The worker order carries the literal line.)

## 5. Draft release note (0.2.0)

**supervisor-mesh 0.2.0: the mesh harness as an MCP server**

What's new
- **Bundled stdio MCP server `supervisor-mesh`** (`.mcp.json` at the plugin root, `python3 ${CLAUDE_PLUGIN_ROOT}/mesh/mcp_server.py`). Python stdlib only, 3.11+, JSON-RPC 2.0 over stdio, logs on stderr only (D77–D79).
- **11 tools.** `mesh_init` (scaffold, dry run and apply as separate calls, legacy tracker import), `mesh_status`, `mesh_metrics` (D90 counters, indicative only), `work_list`, `work_show`, `work_new`, `work_transition` (every state-machine verb, one declarative table), `work_dispatch` (projmux message with the standard header and bounded retry, D93), `tracker_render` (`docs/tracker.md` becomes a generated view of `.mesh/state.json`), `pane_command` (operator-bound slash command into a lead pane, D84/D85), `companions_install` (pstack + mattpocock-skills at project scope after operator approval, D88).
- **Work-item state machine** in `mesh/state.py`, checked against the TLA+ model (formal re-sync through D106). State lives in `.mesh/state.json` with lock + atomic replace + compare-and-set.
- **Evidence-bound verify.** Commands run in a disposable git worktree at the submitted commit, argv only, with timeout and env allowlist, plus `verify.prepare` (D91), `fail_if_output` (D92) and config-hash pinning (D100).
- **Goal intake.** New skill `/supervisor-mesh:run <goal>`: the PM decomposes one goal into work items and dispatches in dependency order (D89). Stall detection in `mesh_status` and an explicit worker completion signal (D102). Scope-overlap warning at dispatch (D94).
- **Identity and operator binding.** The PM is the projmux agent labelled `role=pm`, pinned at init (D82, D109). Callers must descend from their pane's claude process (D108). An operator event must equal the latest real user prompt in the PM transcript (D110). High-impact events need an out-of-band popup code (D113, D114). Writes are contained to the project root (D111). Cross-cutting guards are implemented once (D95).

Requirements change
- python3 ≥ 3.11 (was 3.10). The MCP server needs `tomllib` and `StrEnum`.

Accepted risks (stated, not solved)
- **D86:** `pane_command` has no command allowlist. Privilege-changing slash commands can be typed into a lead when the operator names them; the operator binding (and D113 code for privileged commands) is the control.
- **D96 residual:** the deny rules and provenance checks do not stop a process running as the same OS user from forging mesh state or events.
- **D99:** verify commands have no stdlib sandbox and can write outside the worktree via absolute paths or `$HOME`. The config is PM-owned and hash-pinned.
- **D88:** companion marketplace sources are unpinned (the CLI has no ref option). The installed version and commit of each plugin are recorded in the receipt.

Not in 0.2.0: Slack watcher verbs, `mesh_feedback` / `mesh_fleet_status` (0.3.0, D103), enforced scope serialization, and metrics beyond D90.

## 6. Release-step checklist (for the go)
1. PM go plus a relayed operator OK for the push, after WO-0030, WO-0023, WO-0024 and the PM gate.
2. `git status --short` matches an exact path list (no counts). Any earlier batch-M commits land first.
3. Worker bumps plugin.json and the README line (and the icon under option b); lead re-runs `make check`, `scripts/scrub.sh`, and `claude plugin validate . --strict`, all exit 0.
4. One release commit; `git fetch` + `merge-base --is-ancestor origin/main HEAD` = 0; `git push origin main` (no force, no tags unless ordered). If the push is permission-denied, stop; the operator pushes.
5. Post-push: `git ls-remote origin refs/heads/main` == HEAD. Add a scratch-named marketplace from the GitHub source at project scope in a scratch dir, check that it shows 0.2.0, then remove only that scratch name.

## 7. Open points for the PM
- Icon: option (a), no icon, or (b), order a final asset (section 2a).
- README version wording (section 3, item 2).
- Tag `v0.2.0`? The work order says nothing about tags; I recommend none unless ordered.
