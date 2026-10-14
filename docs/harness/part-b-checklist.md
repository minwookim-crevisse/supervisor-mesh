# Part B — project facts to fill (never shipped in the plugin)

`/supervisor-mesh:init` laid down the method (Part A). These blanks are YOUR project's facts. Fill them; never let an agent invent them.

- [x] **Project name** → `AGENTS.md` title
- [x] **Role list** (which leads exist, what each owns, canonical docs) → `AGENTS.md` "Roles in this project"; add new roles with `/supervisor-mesh:add-agent <role>`
- [x] **Ship role** (the one pusher) → `AGENTS.md`
- [x] **Production data anchor** (the one live store id/region; secrets stay in local `.env`) → `AGENTS.md`
- [x] **Data boundary** (read layer vs write layer globs / modules) → constraint config (`.dependency-cruiser.cjs` or `.importlinter`)
- [x] **Domain terms + deprecated wordings** → `docs/conventions/glossary.md`
- [x] **Test runner command** → `tests/README.md`
- [x] **Audit invariants** → `scripts/audit.sh` (exit 0 = release gate)
- [x] **GC roots / exclusions** → `scripts/gc/check_structure.py` args in `.github/workflows/housekeeping.yml`
- [x] **First decision** → `docs/decisions/0001-decision-log.md` (delete the example block)

> Filled for this repo: see `AGENTS.md` (roles, gates, no data store), `docs/conventions/glossary.md`, `tests/README.md`, `scripts/audit.sh`, `docs/decisions/0001-decision-log.md`.
