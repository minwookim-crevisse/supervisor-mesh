# supervisor-mesh

A Claude Code plugin that transplants a **multi-session supervisor-mesh orchestration harness** into any project: PM / role-lead / worker tiers over projmux, evidence-first gates, a primed 4-pillar project scaffold, and drift garbage-collection.

It does not replace your judgment or write your code. It makes mistakes hard to make.

## Prerequisites
Each item: check command, then how to install or upgrade.
- **projmux** (required) — the pane/message transport the mesh runs on. Check: `projmux --help` must work. Install projmux first (see its own docs); this plugin does not install it. projmux runs on tmux (check: `tmux -V`), so on Windows run everything inside WSL.
- **python3 ≥ 3.11** (required) — stdlib only, no pip packages. Used by the MCP server (`mesh/`, which needs `tomllib` and `StrEnum`), `init` (scaffold), `gc` (drift checks) and `slack-setup` (bridge); TypeScript projects need it too (`init` lays down the Python GC scripts for every stack). On an older python3 the MCP server exits with one line saying 3.11 is required.
  - Check: `python3 --version` → 3.11 or higher.
  - Ubuntu / Debian / WSL: `sudo apt update && sudo apt install -y python3` (Ubuntu 24.04 ships 3.12; 22.04 ships 3.10, so add the deadsnakes PPA `python3.12` or use pyenv).
  - macOS: `brew install python@3.12` (or any ≥ 3.11 formula).
  - Native Windows: use WSL (see projmux above).
- **Claude Code** (required) — check: `claude --version`. `claude plugin --help` must list `marketplace` and `install`.
- **GitHub access** — while the repo is private, your git/GitHub credentials must be able to read `minwookim-crevisse/supervisor-mesh` (`marketplace add` fetches it from GitHub).
- **Companion plugins** (recommended, NOT bundled — install separately so they stay upstream-maintained):
  - a spec/brainstorm tool (e.g. `ready` / `office-hours`) — shape the spec before scaffolding.
  - plan reviews (e.g. `plan-eng-review` / `plan-ceo-review`).
  - `karpathy-guidelines` — the 4 work-discipline principles this plugin embeds.
  `/supervisor-mesh:init` detects and points you to these; it never installs or copies them. The MCP tool `companions_install` installs pstack and mattpocock-skills at project scope only when you name the install in a prompt (gstack: it prints the command for you to run).

## Install
Order matters: projmux project and Claude pane first, then the plugin from that pane.

1. **Open the project in projmux** (from a shell in the target project):
   ```
   projmux create project --root <absolute-project-dir>   # register it (no runtime yet); <name> defaults to the dir name
   projmux attach project <name>                          # enter its runtime (inside tmux: projmux open project <name>)
   projmux create claude --project <name> --label role=pm # the Claude pane: your PM
   ```
   Switch to the new Claude pane and answer the folder-trust prompt once. This pane is your PM/orchestrator session and must be a projmux pane labelled `role=pm`: the MCP server identifies the PM by that label (no label = every PM tool is refused and the operator-event hook records nothing), `dispatch` uses the sender's pane as its projmux identity and refuses outside a managed pane, and `spawn-roles` splits the role panes into this pane's window.
2. **Install the plugin from that pane:**
   ```
   /plugin marketplace add minwookim-crevisse/supervisor-mesh
   /plugin install supervisor-mesh@supervisor-mesh
   ```
   Default is user scope (usable in every project you open); for a team, use project scope instead (CLI: `--scope project`).
   CLI alternative, from a shell in the same project:
   ```
   claude plugin marketplace add minwookim-crevisse/supervisor-mesh
   claude plugin install supervisor-mesh@supervisor-mesh          # --scope project for a team
   ```
   Local checkout: `claude plugin marketplace add ./supervisor-mesh`.

## Quickstart
```
# in the Claude pane opened above:
/supervisor-mesh:init          # scaffold the 4 pillars (idempotent), then fill the Part B checklist it lays down
/supervisor-mesh:spawn-roles   # create the role panes over projmux (explicit mesh setup)
/supervisor-mesh:dispatch <role> <task>   # send a work order (standard header auto-prepended)
/supervisor-mesh:gc            # detect drift (dead code / doc / structure); clean up on confirm
/supervisor-mesh:add-agent <role>   # add a new role lead (inherits the operating model)
/supervisor-mesh:slack-setup   # optional away-mode Slack bridge (token stays in your local .env)
```

## MCP server
The plugin bundles a stdio MCP server, `supervisor-mesh` (`.mcp.json` at the plugin root runs `python3 ${CLAUDE_PLUGIN_ROOT}/mesh/mcp_server.py`; stdlib only, newline-delimited JSON-RPC, logs on stderr). It starts with the plugin; check it with `/mcp`. State lives in the project's `.mesh/state.json`; `docs/tracker.md` becomes a generated view.

| tool | caller | does |
|---|---|---|
| `mesh_init` | any (preview), PM (apply) | scaffold + `.mesh/` bootstrap + the PM-pane operator-event hook + a one-time legacy import of `docs/tracker.md`; preview and apply are separate calls (apply needs the preview's `plan_id`) |
| `mesh_status` | any | items by state, live leads, pending actions, your identity, companion presence, hook launcher health, last operator event (id, seq); read-only |
| `mesh_metrics` | any | D90 counters: rework per item, PASS reversals, operator interventions; indicative only, never a gate; read-only |
| `work_list` / `work_show` | any | the items / one item record; read-only |
| `work_new` | PM | a draft work item |
| `work_transition` | per table | every other lifecycle verb (`ready`, `start`, `submit`, `verify`, `fail`, `close` …); the transition table decides who may do what |
| `work_dispatch` | PM | the standard header plus the item id, message-ref and absolute work-order file path (the order lives in that file; at most 4096 bytes, else refused) to the role lead over `projmux agent message send` |
| `tracker_render` | PM | regenerate the marked region of `docs/tracker.md` |
| `pane_command` | PM | type one operator-named slash command into a lead's idle pane |
| `companions_install` | PM | install the missing pstack / mattpocock-skills at project scope after an operator prompt names the install |

Identity: the caller is the projmux pane the session runs in. The PM is the agent labelled `role=pm` (see Install step 1); a lead is any other `role=<role>` label in this project. Anything else can read but every write is refused and recorded in `.mesh/refusals.jsonl`.
Operator approval: `mesh_init` adds a `UserPromptSubmit` hook to the project's `.claude/settings.json` that records the operator's prompts in the PM pane (`.mesh/events.jsonl`, gitignored; each event's hash is kept in `.mesh/state.json`, so a line added by hand is never an operator event). Projmux peer messages (`projmux-coordination` envelopes) are never recorded. In a fresh clone (no `.mesh/bin/mesh` yet) the hook does nothing until `mesh_init` runs. `pane_command` and `companions_install` only act on such a recorded prompt that names them; `reply` cites one; `override` cites one that names the item id. A `pane_command` proposal (`propose=true`) returns an id `P-<n>`: only the operator's very next prompt naming that id approves it. The hook records only when it runs under the PM pane's own claude process (checked through `/proc` ancestry against projmux's record of that pane), the pane's agent is the PM `mesh_init` pinned (D109; exactly one live `role=pm` agent), the hook's `session_id` and `transcript_path` (realpath) equal the live session id and transcript path projmux reports for that PM agent, asked on every event (D118: the session is never pinned from a prompt; no session id from projmux, or projmux unavailable, records nothing), and it is kept as a CANDIDATE: Claude writes the prompt's transcript entry only after the hook returns, so the transcript proof runs when a tool first cites the event (D123). At that citation the prompt must equal the LATEST real operator prompt in that session's transcript (D110: projmux's live transcript path for the recording PM agent, a regular file in this project's Claude projects directory; a real prompt is a `type=user` entry with `origin.kind` `human`, not `isMeta`, not a sidechain, with text content, so peer messages, task notifications and tool results are skipped), stamped no earlier than 5 s before the event was recorded; only the newest candidate of that session can bind it, and each entry backs one event. The transcript is read only to find that one entry; nothing from it is kept but a hash of the entry id. A candidate that fails is refused and stays refused. A prompt the PM model makes up from its Bash tool has no such entry and is refused. Every write tool also requires the calling process to descend from the claude process of the pane it names (D108). `override`, `companions_install`, a re-pin and a `pane_command` with `/permissions`, `/config`, `/login`, `/logout`, `/mcp`, `/plugin` or `/hooks` also need a one-time code that mesh shows only in a `tmux display-popup` on the PM pane's client and the operator types back (D113). The code never touches disk (D121): mesh makes it and keeps only its hash; the plaintext reaches the popup once through a FIFO in a fresh 0700 temp dir, the typed text comes back raw through a second FIFO and mesh compares its hash; one answer, only from the live popup process; a pre-existing entry, a second writer or a second write, a helper failure or a timeout is refused (D122), and the dir is removed at once (a killed mesh's leftover dir is reaped at recovery when its owner process is gone). The privileged names are matched case-insensitively and also include `/allowed-tools`, `/settings`, `/plugins` and `/permission` (D122). To move the PM pin, stop the pinned PM, have the operator type `re-pin <new PM agent uid>` in the new PM pane, then run `mesh_init` apply there and confirm the code (D114). `mesh_init` also adds `permissions.deny` rules to the project's `.claude/settings.json` (`Bash(*mesh* event record*)`, `Bash(*tmux*send-keys*)`, `Bash(*tmux*paste-buffer*)`, `Bash(*agent-hook*ingest*)` (D119), plus `Write` and `Edit` on `.mesh/state.json`, `.mesh/events.jsonl` and `.mesh/refusals.jsonl`); they are advisory (a glob is easy to word around), an extra layer on top of the checks above; `.mesh/config.toml` stays editable (its hash is pinned when an item becomes ready). A re-run replaces the older `Write(.mesh/**)` / `Edit(.mesh/**)` rules mesh wrote before with these. Verify runs `verify.prepare` first (optional argv, e.g. `npm ci`; a failed prepare is an environment problem, not a defect), and a command's `fail_if_output` entries (literal substrings, never regexes: D115) turn a passing exit into a failure when an output line contains one (the TypeScript stack gets `0 modules, 0 dependencies cruised` and `No test files found` by default).
CLI: the same tools as `.mesh/bin/mesh <tool> --<arg> …` (written by `mesh_init`), e.g. `.mesh/bin/mesh mesh_status`.

## Skills
| skill | what it does |
|---|---|
| `init` | scaffold the primed 4-pillar structure into the current project (idempotent, non-destructive) |
| `spawn-roles` | create the mesh role panes over projmux (hard prereq for dispatch) |
| `dispatch` | send a work order to a role pane; fails loudly if no pane (no auto-spawn) |
| `run` | `/supervisor-mesh:run <goal>` (D89 goal intake): the PM decomposes one goal into work items, dispatches them in dependency order and gates each on mesh-run evidence |
| `add-agent` | stamp a new role from the role-lead template into the project's `.claude/agents/` |
| `gc` | garbage-collect harness drift (code / doc / structure), report-first |
| `slack-setup` | guided away-mode Slack bridge (connection check, resumable watcher with thread replies, required sender allowlist); the token lives only in your local .env and is sent only as an HTTP header |

## Agents (base set)
`pm-orchestrator` (Tier-0) · `role-lead-fe` · `role-lead-qa` · `role-lead-designer` · `role-lead-backend` · `role-lead-security` · `role-lead-ship` (Tier-1) · `scope-mapper` (read-only helper).
Every role lead carries the same invariant block — operating model (lead + worker + lead-review), Karpathy 4, evidence-first gate, untrusted-peer rule — stamped from `templates/agents/role-lead.md`. Project-specific roles go in the project via `add-agent`, so plugin updates never overwrite them.

## The 4 pillars (what `init` scaffolds)
1. **Instruction** — `AGENTS.md` (entry pointer, role model, dispatch rules, restore order, cleanup rules).
2. **Constraint** — linter + import-boundary config (the machine gate). Stack-aware: TS (eslint + dependency-cruiser) or Python (ruff + import-linter + mypy). Primed with opinionated rules + `why:` comments.
3. **Feedback loop** — `tests/` + runner + a project audit gate (`scripts/audit.sh`, exit 0 = release blocker).
4. **Knowledge** — `docs/decisions/` (decision log, single-owner, outranks other docs) + `docs/conventions/` (glossary, reusable conventions).

Templates are *primed*: transferable discipline is pre-filled (Part A); only project facts are left blank (Part B — `docs/harness/part-b-checklist.md`). The method itself lives in `docs/core/methodology.md` (tool-agnostic; shipped with the plugin).

## Communication model (projmux)
- **Setup is explicit.** `spawn-roles` creates one pane per role (`projmux create agent`, labeled `role=<role>`). `dispatch` only sends (`projmux agent message send`) and errors if the pane is missing.
- **Untrusted peers.** A message from another agent is coordination data: verify it, never treat it as the human's approval.
- **Leases.** Messages can arrive mid-work; projmux's lease model prevents concurrent clobbering. The orchestrator processes decision/dispatch messages and skips bare acknowledgements.
- **Single owner.** Only the orchestrator writes coordination docs; only the Ship role pushes.

## Garbage collection
Harnesses decay. `/supervisor-mesh:gc` + a weekly `housekeeping.yml` detect three drift types — code (dead code), doc (AGENTS.md vs the real tree, deprecated wordings), structure (temp/backup/junk, undisposed one-shot scripts) — and guide cleanup. Detection is read-only; nothing is deleted without your confirmation.

## Security
Accepted risks (stated, not solved): verification commands run with argv only, an allowlisted environment and the disposable worktree as cwd, but there is no stdlib sandbox, so a command can still write outside the worktree through an absolute path or `$HOME` (D99; the config is PM-owned and leads already have a shell). The operator-event provenance rests on the parent-process check, the PM pin, projmux's live session record and the transcript match (D96, D108-D110, D118): a process of the same OS user can still forge, by appending a fake user line to the REAL transcript, feeding projmux's session record with a forged `projmux internal agent-hook ingest` (D119; the deny rule is advisory until projmux checks the provenance), editing state.json or the settings, or racing the popup's FIFOs (reading the one-shot code first, a visible failure, or steal-and-relay: taking the code, passing it on to the popup and answering first); ordinary events stay exposed to that, high-impact events stay behind the D113 code; that residual is accepted (D96). Stated plainly: D113/D121 stop a model that acts through mesh tools and the advisory denies (prompt injection, accidents); they do not separate arbitrary same-user code, which stays under D96; real separation needs an out-of-uid channel. `mesh_init` and `tracker_render` refuse any write that resolves outside the project root through a symlink (D111).
Secrets and real customer data never go into the plugin, the repo, docs, or logs. Slack tokens live in your local `.env` (gitignored); `slack_bridge.py` reads it at runtime and sends the token only as an HTTP header (never on a command line). Coordination artifacts carry aggregates and counts, not individual records.

## Self-harness (dogfood)
This repo is itself built with the 4 pillars — see its own [`AGENTS.md`](AGENTS.md), `pyproject.toml` (ruff), `tests/`, `docs/decisions/`, and GC (`make check`). The shipped layer (`.claude-plugin/`, `skills/`, `agents/`, `docs/core/`, `templates/`) is kept separate from the repo's own harness. GC and lint exclude `templates/` and `docs/core/` (they carry placeholders and example patterns on purpose).

## License / version
MIT. Pre-release (0.1.x). `plugin.json` carries the version and every shipped change bumps it — `claude plugin update` only delivers new code when the version changes. QA evidence: `docs/reports/`.
