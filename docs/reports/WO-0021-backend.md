# WO-0021 — init step 6 privacy fix and preview/apply rule (backend)

Verdict: **PASS** (lead-verified 2026-10-29; worker output re-checked by re-running every gate in the lead session).

## Changes (3 files, all in the write set)
- `skills/init/companions.py` (new, stdlib only): checks `ready`, `office-hours`, `plan-eng-review`, `karpathy-guidelines` by name. It counts a match on a plugin key (before `@`) or on `<installPath>/skills/<name>/SKILL.md`. Output is exactly one `companion <name>: installed|missing` line per companion and nothing else. A missing, malformed or wrongly shaped registry reports all four missing and exits 0, with no traceback.
- `skills/init/SKILL.md`: step 6 runs the helper and says plainly not to read, cat or print the plugin registry or any other user config. Steps 2-3 say preview and apply are separate Bash calls and must never be chained (`--dry-run && ...` is named as the forbidden form). Frontmatter is unchanged.
- `tests/test_companions.py` (new, 7 tests): uses a fixture registry with unrelated plugins, marketplace, paths, version and skill. The tests assert that only the four companion lines appear and that none of those strings, the tempdir path, `Traceback` or `installed_plugins` leaks. They also cover the dict entry shape, a missing registry, malformed JSON and wrong top-level shapes, plus two SKILL.md text tests.

## Evidence (re-run by the lead)
| check | result | exit |
|---|---|---|
| RED: `tests/test_companions.py` run against a `git archive HEAD` copy | `Ran 7 tests` / `FAILED (failures=7)` | non-zero (unittest FAILED) |
| GREEN: `python3 -m unittest tests.test_companions -v` | `Ran 7 tests` / `OK` | 0 |
| `make check` | 4× `Validation passed`; `Ran 93 tests` `OK`; `no doc drift`; `no structure drift`; `scrub: 0 hits`; `audit: PASS` | 0 |
| `scripts/scrub.sh` | `scrub: 0 hits` | 0 |
| `claude plugin validate . --strict` | `Validation passed` | 0 |
| `ruff check .` | not installed here (worker: exit 127) | n/a |

## Review notes
- The registry is read only inside the helper process, and only presence lines reach the session. Neither the worker nor the lead ran the helper against the real user registry.
- No files outside the write set changed. `tests/formal` was untouched. Nothing was committed.

## Gaps / requests to the PM
- ruff is not installed in this environment, so lint is unproven. Ship or QA should run `ruff check .` where it is available.
- `git status` showed pre-existing modifications under `docs/designs/` and `docs/tracker.md` that were not made by this WO.
- Tracker update request: WO-0021 → PASS, report at this file.
