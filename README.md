# supervisor-mesh

A Claude Code plugin that transplants a **multi-session supervisor-mesh orchestration harness** into any project: PM / role-lead / worker tiers over projmux, evidence-first gates, a primed 4-pillar project scaffold, and drift garbage-collection.

It does not replace your judgment or write your code. It makes mistakes hard to make.

## Prerequisites
- **projmux** (required) — the pane/message transport the mesh runs on. Install it first; this plugin assumes it (`projmux --help` must work).
- **python3** (3.10+) — `init` and the GC checks are small stdlib-only scripts.
- **Companion plugins** (recommended, NOT bundled — install separately so they stay upstream-maintained):
  - a spec/brainstorm tool (e.g. `ready` / `office-hours`) — shape the spec before scaffolding.
  - plan reviews (e.g. `plan-eng-review` / `plan-ceo-review`).
  - `karpathy-guidelines` — the 4 work-discipline principles this plugin embeds.
  `/supervisor-mesh:init` detects and points you to these; it never installs or copies them.

## Install
```
claude plugin marketplace add minwookim-crevisse/supervisor-mesh
claude plugin install supervisor-mesh@supervisor-mesh
```
Installs at user scope (`~/.claude/plugins/`) and is usable in every project you open. Local checkout: `claude plugin marketplace add ./supervisor-mesh`.

## Quickstart
```
# in the target project:
/supervisor-mesh:init          # scaffold the 4 pillars (idempotent), then fill the Part B checklist it lays down
/supervisor-mesh:spawn-roles   # create the role panes over projmux (explicit mesh setup)
/supervisor-mesh:dispatch <role> <task>   # send a work order (standard header auto-prepended)
/supervisor-mesh:gc            # detect drift (dead code / doc / structure); clean up on confirm
/supervisor-mesh:add-agent <role>   # add a new role lead (inherits the operating model)
/supervisor-mesh:slack-setup   # optional away-mode Slack bridge (token stays in your local .env)
```

## Skills
| skill | what it does |
|---|---|
| `init` | scaffold the primed 4-pillar structure into the current project (idempotent, non-destructive) |
| `spawn-roles` | create the mesh role panes over projmux (hard prereq for dispatch) |
| `dispatch` | send a work order to a role pane; fails loudly if no pane (no auto-spawn) |
| `add-agent` | stamp a new role from the role-lead template into the project's `.claude/agents/` |
| `gc` | garbage-collect harness drift (code / doc / structure), report-first |
| `slack-setup` | guided away-mode Slack bridge; token never leaves your local .env |

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
Secrets and real customer data never go into the plugin, the repo, docs, or logs. Slack tokens live in your local `.env` (gitignored); the watcher/sender scripts read it at runtime. Coordination artifacts carry aggregates and counts, not individual records.

## Self-harness (dogfood)
This repo is itself built with the 4 pillars — see its own [`AGENTS.md`](AGENTS.md), `pyproject.toml` (ruff), `tests/`, `docs/decisions/`, and GC (`make check`). The shipped layer (`.claude-plugin/`, `skills/`, `agents/`, `docs/core/`, `templates/`) is kept separate from the repo's own harness. GC and lint exclude `templates/` and `docs/core/` (they carry placeholders and example patterns on purpose).

## License / version
MIT. Pre-release (0.1.x). `plugin.json` carries the version and every shipped change bumps it — `claude plugin update` only delivers new code when the version changes. QA evidence: `docs/qa/`.
