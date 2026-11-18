# WO-0028 — D107: narrow the D96 deny rules (backend)

Verdict: **PASS** (lead-verified 2026-11-18, base 0501f50). Nothing committed; commit/push is Ship's.

## Change
- `mesh/init.py` `DENY_RULES` is now exactly the D96 row as narrowed by D107 (spec line 28):
  - `Bash(*mesh* event record*)`;
  - `Write` / `Edit` on `.mesh/state.json`, `.mesh/events.jsonl` and `.mesh/refusals.jsonl`.

  It has no `.mesh/**` wildcard, so `.mesh/config.toml` stays editable (D100 pins it at `ready`).
- Migration: a `mesh_init` re-run removes exactly the two mesh-written legacy entries, `Write(.mesh/**)` and `Edit(.mesh/**)` (`LEGACY_DENY_RULES`), and appends the narrowed rules. Every other user entry is kept in order, a user's own narrower `.mesh/...` rule is left alone, and a second re-run leaves the settings file byte-identical.
- Docs: `README.md` (one deny-rule sentence plus the migration note) and `skills/init/SKILL.md` (the deny-rule clause).
- `git diff --stat`: 4 files, +71 / −11 (`mesh/init.py`, `tests/test_mesh_review_w5.py`, `README.md`, `skills/init/SKILL.md`).

## Evidence (lead re-run)
| check | result | exit |
|---|---|---|
| RED: the final tests against HEAD `mesh/init.py` (worker log `~/.local/state/supervisor-mesh-qa/WO-0028/tdd-log.md`) | 4 of 5 failed | 1 |
| GREEN: `python3 -m unittest discover -s tests -p test_mesh_review_w5.py -k D96HostDenyRulesTests` | `OK` | 0 |
| `make check` | `Ran 533 tests`, `OK`, `scrub: 0 hits`, `audit: PASS` | 0 |
| `claude plugin validate . --strict` | `Validation passed` | 0 |
| `ruff check mesh tests` | `All checks passed!` | 0 |
| `grep '.mesh/**' mesh/init.py` | only the legacy list and its comments | — |

Tests (in `D96HostDenyRulesTests`):
- the fresh-init narrowed set;
- `DENY_RULES == NARROWED_DENY`, additive and idempotent;
- `test_d107_the_old_mesh_wildcards_are_replaced_by_the_narrowed_rules`;
- `test_d107_a_user_written_narrower_mesh_rule_is_left_alone`.

## Notes
- The `.claude/skills/verify-supervisor-mesh/*` and `docs/tracker.md` modifications in `git status` belong to other sessions and were not touched.
