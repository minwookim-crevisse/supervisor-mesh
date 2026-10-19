# WO-0008 — README: projmux-first install order + actionable prerequisites (backend)

- Date: 2026-10-14 · Role: backend lead · Model: lead + one same-role worker + lead review
- Write set used: `README.md` (`## Prerequisites`, `## Install`, `## Quickstart` only) + this report. Not committed (Ship only).
- Every claim below that is marked "lead" was re-run by the lead; the others come from the worker's captured output and are labelled.

## Gate evidence

| gate | result |
|---|---|
| diff scope | `git diff HEAD -- README.md` hunks: `@@ -5,8 +5,15 @@` and `@@ -14,15 +21,31 @@` (Prerequisites / Install / Quickstart; headings now at :7, :23, :46). The other two hunks (`@@ -39` Skills row, `@@ -63` Security) are the earlier WO-0004 edits, not WO-0008. (lead) |
| scrub | `bash scripts/scrub.sh` → `scrub: 0 hits`, exit 0 (lead) |
| make check | `make check` → **exit 0**: `✔ Validation passed` ×4 · `Ran 86 tests … OK` · `no doc drift` · `no structure drift` · `scrub: 0 hits` · `audit: PASS` (lead; run after QA's concurrent `tests/test_slack.py` change landed. A run moments earlier, during that edit, showed 1 transient failure, which was gone on re-run) |
| lint | ruff not installed (worker: `command -v ruff` → nothing) |

## Operator requests → README (verified)

| request | README now | verification |
|---|---|---|
| projmux project + Claude pane FIRST | Install step 1: `projmux create project --root <absolute-project-dir>` → `projmux attach project <name>` (inside tmux: `projmux open project <name>`) → `projmux create claude --project <name>`; answer the folder-trust prompt once | lead: `projmux create project --help` → `projmux create project --root <absolute-path> [--name <name>] …`, "no runtime is materialized", "Without `--name` a new Project is named after its root directory"; `projmux attach --help` → `projmux attach project <ref>` "Enter a Project runtime from outside tmux"; `projmux open --help` → "outside tmux it refuses and points at attach"; `projmux create claude --help` → `projmux create claude [--project <ref> \| -p <ref>] …` |
| why the PM pane must be a projmux pane | "dispatch uses the sender's pane as its projmux identity and refuses outside a managed pane; spawn-roles splits role panes into this pane's window" | worker: `skills/dispatch/SKILL.md:27,30`; `skills/spawn-roles/SKILL.md:21`; folder-trust prompt: `skills/spawn-roles/SKILL.md:29` |
| install from that pane | `/plugin marketplace add minwookim-crevisse/supervisor-mesh` → `/plugin install supervisor-mesh@supervisor-mesh` | worker: help strings in the installed CLI bundle (v2.1.287): `/plugin marketplace add <path/url>`, `/plugin install <plugin>@<market>`. Not executed (it would install). |
| user scope default; project scope for teams | "Default is user scope …; for a team, use project scope instead (CLI: `--scope project`)" | lead: `claude plugin install --help` → `-s, --scope <scope> Installation scope: user, project, or local`; `marketplace add --help` → `--scope … user (default)` |
| CLI form kept as an alternative | `claude plugin marketplace add …` / `claude plugin install … # --scope project for a team`, "from a shell in the same project" | lead: `Usage: claude plugin install\|i [options] <plugin>`; `Usage: claude plugin marketplace add [options] <source>` |
| then init + the existing quickstart, unchanged | the Quickstart commands are identical; only the comment changed to "in the Claude pane opened above:" | lead: diff hunk 2 |
| python3 ≥ 3.10, actionable | Check `python3 --version`; apt `sudo apt update && sudo apt install -y python3` (22.04+ ships ≥ 3.10; older releases → deadsnakes PPA `python3.12` or pyenv); macOS `brew install python@3.12`; native Windows → WSL | lead: 3.10 syntax at `skills/init/scaffold.py:53` (`-> str \| None`), `:62`; no `from __future__` anywhere in skills/templates/scripts, so 3.9 fails at def time. Worker: `apt-cache policy python3` → `3.12.3-0ubuntu2.1` (24.04); `brew info python@3.12` → `stable 3.12.15 (bottled)` |
| which skills need it; TypeScript too | init / gc / slack-setup; TS projects too, because `init` lays down the Python GC scripts for every stack | lead: `scaffold.py` COMMON map holds `scripts/gc/check_docs_drift.py`, `check_structure.py`, `housekeeping.yml` |
| every prerequisite has a check + install | projmux (`projmux --help`; "install projmux first, see its own docs"; tmux `tmux -V`; Windows → WSL); python3 (above); Claude Code (`claude --version`, `claude plugin --help`); GitHub access while private | worker: `projmux --help` / `update --help` show no installer subcommand, so no install command is invented; `tmux 3.4` |

## Final sections (verbatim, as in README.md)

````
## Prerequisites
Each item: check command, then how to install or upgrade.
- **projmux** (required) — the pane/message transport the mesh runs on. Check: `projmux --help` must work. Install projmux first (see its own docs); this plugin does not install it. projmux runs on tmux (check: `tmux -V`), so on Windows run everything inside WSL.
- **python3 ≥ 3.10** (required) — stdlib only, no pip packages. Used by `init` (scaffold), `gc` (drift checks) and `slack-setup` (bridge); TypeScript projects need it too (`init` lays down the Python GC scripts for every stack). Scripts use 3.10 syntax (`X | None`).
  - Check: `python3 --version` → 3.10 or higher.
  - Ubuntu / Debian / WSL: `sudo apt update && sudo apt install -y python3` (Ubuntu 22.04+ ships ≥ 3.10). Older releases: the deadsnakes PPA (`python3.12`) or pyenv.
  - macOS: `brew install python@3.12` (or any ≥ 3.10 formula).
  - Native Windows: use WSL (see projmux above).
- **Claude Code** (required) — check: `claude --version`. `claude plugin --help` must list `marketplace` and `install`.
- **GitHub access** — while the repo is private, your git/GitHub credentials must be able to read `minwookim-crevisse/supervisor-mesh` (`marketplace add` fetches it from GitHub).
- **Companion plugins** (recommended, NOT bundled — install separately so they stay upstream-maintained):
  - a spec/brainstorm tool (e.g. `ready` / `office-hours`) — shape the spec before scaffolding.
  - plan reviews (e.g. `plan-eng-review` / `plan-ceo-review`).
  - `karpathy-guidelines` — the 4 work-discipline principles this plugin embeds.
  `/supervisor-mesh:init` detects and points you to these; it never installs or copies them.

## Install
Order matters: projmux project and Claude pane first, then the plugin from that pane.

1. **Open the project in projmux** (from a shell in the target project):
   ```
   projmux create project --root <absolute-project-dir>   # register it (no runtime yet); <name> defaults to the dir name
   projmux attach project <name>                          # enter its runtime (inside tmux: projmux open project <name>)
   projmux create claude --project <name>                 # the Claude pane
   ```
   Switch to the new Claude pane and answer the folder-trust prompt once. This pane is your PM/orchestrator session and must be a projmux pane: `dispatch` uses the sender's pane as its projmux identity and refuses outside a managed pane, and `spawn-roles` splits the role panes into this pane's window.
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
````

## Not verified (phrased conservatively)
- "Ubuntu 22.04+ ships ≥ 3.10": a known distro fact; only 24.04 could be checked locally.
- deadsnakes `python3.12` / pyenv: pointers only, with no `add-apt-repository` command (untested here).
- The `/plugin` slash form: confirmed from the CLI's embedded help text, not executed. The scope picker in the `/plugin` UI is unverified, so the README names only the verified CLI flag.
- How to focus the new pane: `create claude` is detached; the README says "switch to the new Claude pane" without an exact focus command (pane-uid acceptance unverified).
- Private-repo fetch through `marketplace add`: auth behaviour not tested.
- projmux has no documented installer in `--help`, so the README invents no install command.

## Verdict
**PASS.** Order and prerequisites match the operator requests; `make check` exit 0; scrub 0; only the three README sections changed.
