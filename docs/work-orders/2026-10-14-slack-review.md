> Paths: `<repo>` = this repo; `<test-repo-c>` = the Slack test repo. Absolute paths travel only in the dispatch message, never in committed docs (DEC-0008).

# Work orders — Slack bridge 0.1.2 review (batch 1, parallel, read-only)

## Common header (applies to WO-0001, WO-0002, WO-0003)
- **Working dir:** `<repo>` (the plugin repo root; absolute path given in the dispatch message) (plugin repo). Read ``AGENTS.md`` first.
- **Background:** the Slack bridge was rewritten (two shell scripts → `templates/slack/slack_bridge.py`) to close gaps A–J found by a live test pane.
  The rewrite was written by the orchestrator session itself and verified only by its author — it is an UNVERIFIED DRAFT. Treat every claim
  (code comments, tests, DEC-0005, QA report, skill text) as unproven until you check it. Find errors AND misleading decisions/claims.
- **Operating model:** lead + worker + lead-review. Spawn ONE same-role worker (your Agent tool, a subagent) to do the review work;
  then YOU verify each worker finding against the code before it goes in your report. Self-reports are not accepted — cite file:line and captured command output.
- **Discipline:** think before concluding, simplicity, surgical, goal-driven.
- **Constraints:** 🔴 READ-ONLY on the repo except your own report file. Do not edit code, tests, skills, `docs/decisions/`, `docs/tracker.md`, `AGENTS.md`.
  No commit / push (Ship only). Never print, echo or log a Slack token or the contents of any `.env`. No worktree-destroying git commands.
- **Report format** (`docs/reports/WO-000N-<role>.md`): table of findings — id · severity (🔴 must-fix / 🟠 should-fix / 🟡 nit) · file:line · what is wrong ·
  evidence (command + output) · suggested fix. Then a list of claims you CONFIRMED with evidence. End with PASS (no 🔴) or FAIL.
- **Report target:** reply to the orchestrator over projmux with one line: `WO-000N done: <n> findings (<n> red) — report <path>`.

Draft files under review:
- `templates/slack/slack_bridge.py`, `templates/slack/env.example`
- `skills/slack-setup/SKILL.md`
- `tests/test_slack.py`
- `docs/decisions/0001-decision-log.md` (DEC-0005), `docs/qa/2026-10-14-live-qa.md` (Slack section), `README.md` (Slack lines)
- Source findings A–J: `<test-repo-c>/docs/reports/slack-bridge-improvement-request.md` (path given in the dispatch message)

## WO-0001 — Security review (role: security)
Mission: decide whether the draft is safe to ship. At minimum check:
1. Token exposure: argv / `ps`, stdout/stderr, exception messages and tracebacks, URLs, state files, logs. Does any error path print the Authorization header or token?
2. `SLACK_API_BASE` override: can a value in `.env` send the token to an arbitrary host or over plain http? Is that acceptable or must it be restricted?
3. State directory/file: location, permissions, contents, symlink / shared-machine risks.
4. Inbound trust: Slack text re-invokes a session — prompt-injection exposure; are the skill's trust rules and `SLACK_ALLOWED_USERS` default adequate and honestly described?
5. Skill `allowed-tools` breadth (`Monitor`, `Bash(python3 scripts/slack/slack_bridge.py:*)` …) — least privilege?
6. `.env` handling: parsing, misnamed-file hint (does it leak file contents?), gitignore guidance.
7. Any secret-like literal anywhere (`scripts/scrub.sh` must stay 0).

## WO-0002 — Correctness review (role: backend)
Mission: does the code actually close A–J, correctly, against real Slack API semantics? At minimum check:
1. Each of A–J: closed, partially closed, or not — with evidence.
2. `conversations.history` semantics: `oldest` inclusive/exclusive, ordering, pagination cursor handling, `limit`.
3. Timestamp handling: Slack `ts` strings compared as floats — precision/ordering risk? Floor update rules; duplicate or lost messages at boundaries.
4. Thread logic: parent detection, `latest_reply` tracking, replies older than the fetch window, `thread_broadcast` de-duplication, TTL pruning, API call volume per poll vs Slack rate limits.
5. Restart / backlog semantics: is `SLACK_MAX_BACKLOG_SECONDS` behaviour what the docs say? Two watchers on the same state?
6. Error handling: HTTP 429/5xx, network loss, `ok:false` mid-pagination — is state advanced past unprocessed messages (message loss)?
7. Misleading decisions/claims: DEC-0005, skill text, README, QA-report rows — anything overstated or wrong.

## WO-0003 — Independent verification (role: qa)
Mission: re-verify from scratch; trust nothing the author reported. At minimum:
1. Run `python3 -m unittest discover -s tests -v` and `make check` (ruff may be absent; say so). Capture output + exit codes.
2. Test quality: does each Slack test actually prove the claim mapped to it in the QA report? Weak/tautological assertions, mock-only behaviour, missing negative cases.
3. Mock fidelity: where does `FakeSlack` differ from real Slack (oldest semantics, replies payload, broadcast, error shapes)? Which tests would still pass if the code were wrong?
4. Live check against the real channel, READ-ONLY plus one send:
   `python3 templates/slack/slack_bridge.py --env <test-repo-c>/.env check` and one `send` of a short QA line. Never print the token or the `.env` contents.
5. Claim audit: every Slack row in `docs/qa/2026-10-14-live-qa.md` — accurate, overstated, or unproven?
6. FAIL triage for anything red: product defect / test defect / environment.
