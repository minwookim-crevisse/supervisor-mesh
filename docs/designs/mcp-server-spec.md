# MCP server MVP: the mesh harness as MCP tools (0.2.0)

> Status: approved scope (operator decisions 2026-11-18, asked in Korean in chat, recorded in English).
> Sources: `docs/designs/work-item-state-model.md` (state machine, D40-D76: the single source for states, table, invariants), `docs/designs/pm-led-onboarding.md` (mesh package layout, identity, tracker grammar), `skills/*/SKILL.md` (current behaviour).

## Decisions

| id | question | answer |
|---|---|---|
| D77 | MVP tool scope | coordination set (init, status, tracker, dispatch) + the work-item state machine |
| D78 | implementation | Python stdlib only (3.11+), MCP JSON-RPC 2.0 over stdio implemented directly; no pip packages |
| D79 | distribution | bundled in the plugin (`.mcp.json` at the plugin root, `${CLAUDE_PLUGIN_ROOT}`); version 0.2.0 |
| D80 | order | WO-0020 (formal re-sync) completes and lands before the MCP release commit |

## Shape

- One package `mesh/` at the plugin root. Every behaviour lives in plain functions; two thin front doors call the same functions:
  - `mesh/cli.py` (CLI, also used by hooks: `mesh event record`);
  - `mesh/mcp_server.py` (stdio MCP server: `initialize`, `tools/list`, `tools/call`; newline-delimited JSON-RPC; logs to stderr only).
- `.mcp.json`: server `supervisor-mesh`, command `python3`, args `${CLAUDE_PLUGIN_ROOT}/mesh/mcp_server.py`. The project root is the server's working directory (`CLAUDE_PROJECT_DIR` when set).
- State: `<project>/.mesh/state.json` is the source of truth (lock file + atomic replace + compare-and-set, section 8 of the state model). `docs/tracker.md` becomes a generated view; an existing tracker is imported once as legacy rows (normalized, never deleted).
- Caller identity: the projmux pane of the calling session (role label `pm` or the lead's role), isolated in `mesh/identity.py`; unknown pane = refused for every verb except read-only tools. `operator` is never inferred: `override` requires a hook-recorded operator event (section 9a).
- The transition table is one declarative structure in `mesh/state.py`; the MCP tool, the CLI and the tests read it. Unlisted verb x caller x state = refused and recorded.

## MVP tools

| tool | caller | does |
|---|---|---|
| `mesh_init` | pm | scaffold the 4 pillars (wraps `skills/init/scaffold.py`); `dry_run` and `apply` are separate calls; creates `.mesh/` and imports a legacy tracker |
| `mesh_status` | any | items by state, live leads (projmux), pending actions; read-only |
| `work_list` / `work_show` | any | read items / one item record; read-only |
| `work_new` | pm | `new`: draft with goal, done criteria, role, type, depends |
| `work_transition` | per table | every other verb of section 4 (`ready`, `start`, `submit`, `verify`, `fail`, `reassign`, `split`, `unblock`, `reject`, `ack`, `close`, `cancel`, `revise`, `reply`, `override`); args validated per verb |
| `work_dispatch` | pm | `dispatch`: sends the work order to the role lead with `projmux agent message send` (standard header: read set, mission, operating model, 4 principles, evidence-first DoD, constraints, report target); records the message-ref |
| `tracker_render` | pm | regenerate `docs/tracker.md` from state.json |

`verify` runs the item's verification commands (`.mesh/config.toml`, `tomllib`) in a disposable `git worktree` at the submitted commit, argv only, with a timeout; exit codes and timestamps are bound to commit + contract version + config hash. A prose report never reaches `verified`.

## Out of the MVP (recorded, not built)
Slack watcher verbs (`draft`, `attach` from Slack; the slack-setup skill keeps working as is), `revalidate` at an integration gate, metrics (D43/D50), LangGraph (stage 2).

## Acceptance (evidence-first)
1. Unit tests: table walk (invariants 1-4), one test per invariant 5-15 where expressible, CAS stale write refused, lock contention, legacy tracker import, identity refusal, MCP handshake + `tools/list` + one `tools/call` per tool over a real stdio pipe.
2. The TLA+ model (WO-0020) and `mesh/state.py` agree: a conformance test replays the model's table rows against `state.py`.
3. `make check` exit 0, scrub 0, `claude plugin validate . --strict` exit 0.
4. Live: in a throwaway projmux project, install the plugin at project scope, `/mcp` lists `supervisor-mesh` connected, the PM pane runs `mesh_init` (dry run, then apply), `work_new` -> `ready` -> `work_dispatch` to a spawned lead, the lead `start`/`submit`, `verify` passes, PM `close`; file hashes and state.json captured as evidence.
