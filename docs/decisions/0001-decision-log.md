# Decision Log

> Single owner: the Orchestrator writes this. Others propose via their report file.
> This log OUTRANKS every other doc on conflict. Newest decision wins.

## DEC-0007 — Every shipped change bumps the plugin version
- Date: 2026-10-14
- Status: accepted
- Context: live QA showed `claude plugin update` keeps the cached copy while `plugin.json` `version` is unchanged, so installed users never receive fixes.
- Decision: any change under the shipped layer bumps `version` in `.claude-plugin/plugin.json` (patch for fixes).
- Consequences: Ship checks the bump before pushing; the marketplace entry still omits `version`.

## DEC-0006 — init is a deterministic script, not free-form writes
- Date: 2026-10-14
- Status: accepted
- Context: idempotency ("never overwrite; merge or skip") is load-bearing and hard to guarantee when a model hand-writes files.
- Decision: `skills/init/scaffold.py` (stdlib only) performs every write; the skill previews (`--dry-run`), applies, and shows the captured CREATE/MERGE/SKIP output as evidence.
- Consequences: idempotency is unit-tested (`tests/test_scaffold.py`); merges are limited to AGENTS.md (marker block), `.gitignore` lines, package.json script keys, and pyproject tables.

## DEC-0005 — Slack credentials are runtime-only
- Date: 2026-10-14
- Status: accepted
- Context: the bridge needs a bot token and channel id.
- Decision: watcher/sender read `SLACK_BOT_TOKEN` / `SLACK_CHANNEL_ID` from the project's local `.env` at runtime; the plugin ships only `env.example` with empty values.
- Consequences: `tests/test_slack.py` and `scripts/scrub.sh` fail on any token literal.

## DEC-0004 — Recursion guard for the dogfood harness
- Date: 2026-10-14
- Status: accepted
- Context: `templates/` and `docs/core/` intentionally contain `<...>` placeholders and forbidden-name example strings.
- Decision: repo GC and lint exclude `templates/` and `docs/core/`.
- Consequences: those directories are covered instead by component tests (manifest/skill/agent checks) and the scrub gate.

## DEC-0003 — Third-party skills are companions, never bundled
- Date: 2026-10-14
- Status: accepted
- Context: bundling spec/review/discipline skills from other plugins risks forks, drift and license issues.
- Decision: README documents them as companion plugins; `init` only detects and points to them.
- Consequences: this plugin implements only harness-specific wiring and the spec → scaffold hand-off.

## DEC-0002 — Project-added roles live in the project
- Date: 2026-10-14
- Status: accepted
- Context: roles added inside the installed plugin would be lost on plugin update.
- Decision: `add-agent` stamps `templates/agents/role-lead.md` into `<project>/.claude/agents/`; spawn-roles/dispatch discover plugin and project agents.
- Consequences: every role lead inherits the invariant block byte-for-byte (`tests/test_components.py`).

## DEC-0001 — One repo = marketplace + plugin
- Date: 2026-10-14
- Status: accepted
- Context: simplest distribution for a single plugin.
- Decision: `.claude-plugin/marketplace.json` lists one plugin with `source: "./"`; the marketplace entry omits `version` pre-release.
- Consequences: `claude plugin marketplace add <owner>/supervisor-mesh` then `claude plugin install supervisor-mesh@supervisor-mesh`.
