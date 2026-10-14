# GC — harness drift control (methodology §14)

| drift | check | prevention rule (AGENTS.md "Cleanup rules") |
|---|---|---|
| CODE — dead code | Python: `vulture <SRC> --min-confidence 80` · TS: `ts-prune` / `knip` | remove unused imports immediately |
| DOC — docs ≠ tree | `scripts/gc/check_docs_drift.py` (AGENTS.md paths exist; deprecated glossary wordings absent) | update AGENTS.md in the same change that moves a path |
| STRUCTURE — junk | `scripts/gc/check_structure.py` (temp/backup names, env junk, one-shot scripts) | temp → scratchpad; dispose one-shots after running |

Detection is read-only. Dead-code findings can be false positives (reflection, dynamic import, public API) — confirm each before deleting.

Python vulture config (append to pyproject.toml, Part B paths):
```toml
[tool.vulture]
min_confidence = 80
paths = ["<SRC>"]
ignore_names = ["test_*", "setUp", "tearDown"]
```
