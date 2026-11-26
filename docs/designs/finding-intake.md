# Finding intake — how projects report plugin findings (D136, 0.3.0)

> Status: DECIDED 2026-11-25 (operator answers Q1-Q12 in a grill-with-docs session; inputs: /pstack:how and /pstack:why on D103). Supersedes the open parts of D103.
> Terms (glossary): **plugin finding** (PF), **finding intake**.

## Why this design exists
D103 (2026-11-18) started finding intake as a local markdown file because the MCP tools were not built yet and adding them would have delayed 0.2.0 (operator choice, PM session record 11-18). It fixed only the entry format (date, situation, evidence, proposal) and `PF-NNN` ids. Cadence, triage, reply and a data boundary were never designed, D103 never reached the decision log, and the 2026-11-20 adoption findings were taken from a project transcript under separate ids (A1-A9).

## Decisions
| # | decision |
|---|---|
| Q1 | Terms: plugin finding (PF, `PF-NNN` per project), finding intake. Distinct from the feedback-loop pillar |
| Q2 | The source of truth is the project's own `docs/reports/plugin-feedback.md`. `mesh_feedback` validates and appends to that file only. The plugin side only reads (pull); nothing writes another project's repo |
| Q3 | Only the project's PM files a finding; leads report plugin problems in their own report and the PM decides plugin issue vs project issue |
| Q4 | File the moment it happens (a refusal, a workaround, the operator stuck). The plugin PM sweeps at each release planning, at a phase-closing commit or a PM session start, and at least weekly. P0 is announced at once with a projmux message (local) or an issue (remote) |
| Q5 | Three axes: severity P0 (cannot close work / security), P1 (needs a workaround), P2 (confusion or cost), P3 (idea); kind bug / gap / docs / ux; status new → accepted (D-id, WO) / duplicate (of) / wont-fix (reason) / needs-info → shipped (version) |
| Q6 | The plugin repo keeps one ledger, `docs/reports/fleet-findings.md`, keyed by project alias + PF id. Status changes are told to the project PM (projmux message locally, issue comment remotely); each project PM updates its own file |
| Q7 | A finding carries plugin-level facts only: refusal codes, tool output excerpts, plugin version, project-relative paths. Never business data, secrets, emails or operator prompt text. `mesh_feedback` refuses secret/email patterns. The plugin PM reads only finding files, tracker and `mesh_status`; transcripts only when the operator asks, as targeted excerpts |
| Q8 | Migrate the pilot's PF-001..006 and the 2026-11-20 findings A1-A9 into the ledger; ask each local project without a file to create one |
| Q2'/Q6' | Two layers. Local (same machine): file + projmux message. Remote (another machine or person): the same entry as a GitHub Issue on the plugin repo, label `plugin-finding`, title `PF-<alias>-NNN`; `mesh_feedback` renders the draft, the operator approves the publish (the repo is public; same principle as DEC-0017) |
| Q9 | Public issues name the project by alias only: `[feedback] alias` in `.mesh/config.toml`, else a short hash of the repo path; drafts with paths, user names or business terms are refused |
| Q10 | Local projects are registered explicitly in `~/.local/state/supervisor-mesh/fleet.toml` by the operator (init offers it at the end). No label inference (projmux labels sit on agents, not projects, and the `mesh` value is not a project id) |
| Q11 | `mesh_fleet_status` is NOT a shipped MCP tool: read-only tools bypass the PM gate and would let any project's PM read the fleet. Collection is a repo-own script `scripts/fleet.py`; the only shipped tool is `mesh_feedback` (PM-only, write contained to its own root) |
| Q12 | The fleet script reads registered roots only, realpaths each path and refuses symlinks leaving the root, takes no lock, and reads only the finding file and item counts/states of `.mesh/state.json`; never `events.jsonl`, transcripts or business documents |

## Order of work
1. This design, the decision-log entry (DEC-0018) and the ledger with the migrated findings (no code).
2. Local projects create their finding file now (manual, same format plus status).
3. 0.3.0 build: init template, `mesh_feedback`, `scripts/fleet.py`, issue draft; tests below. Protocol first, tool second (/why constraint: do not freeze an undefined process into a tool).

## Tests (for the build order)
- `mesh_feedback`: appends a well-formed `PF-NNN` with the next number; refuses a non-PM caller, a write outside the root, a secret or email pattern; renders an issue draft with the alias and no path.
- `scripts/fleet.py`: reads only registered roots; a symlinked finding file pointing outside is refused; output carries counts and PF ids, never event text; no lock file created.
- init: lays down the template once, never overwrites an existing file.
- E2E: a throwaway project files a PF locally; the plugin PM's sweep lists it; a status change message reaches the project PM.
