# Tests = the feedback loop

GREEN must be test-proven. Runner: `python3 -m unittest discover -s tests -v` (stdlib only). Full gate: `make check`.

## Conventions
- Write the test alongside the change (not after).
- Bug fix → a regression test named `test_*_regression_NN.py` with an attribution comment (what broke / found by / date).
- New conditional → test both branches. New error path → a test that triggers it.
- Evidence-first: a change is DONE only with captured `make check` output (exit 0).
- FAIL triage: product defect / test defect / environment — classify before acting; never mask with retries.
- Audit gate: `scripts/audit.sh` must exit 0 before ship.

## Suites
- `test_manifests.py` — plugin/marketplace manifests.
- `test_components.py` — skills frontmatter, agent definitions, invariant block inheritance.
- `test_scaffold.py` — init idempotency and merge behaviour (ts / python / none / ambiguous).
- `test_gc.py` — doc and structure drift checks, dogfood copies in sync.
- `test_slack.py` — no token literals, runtime `.env` reads, clean failure without config.
- `test_scrub.py` — the scrub gate passes on the whole repo.
