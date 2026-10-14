# Part B — project facts to fill (never shipped in the plugin)

`/supervisor-mesh:init` laid down the method (Part A). These blanks are YOUR project's facts. Fill them; never let an agent invent them.

- [ ] **Project name** → `AGENTS.md` title
- [ ] **Role list** (which leads exist, what each owns, canonical docs) → `AGENTS.md` "Roles in this project"; add new roles with `/supervisor-mesh:add-agent <role>`
- [ ] **Ship role** (the one pusher) → `AGENTS.md`
- [ ] **Production data anchor** (the one live store id/region; secrets stay in local `.env`) → `AGENTS.md`
- [ ] **Data boundary** (read layer vs write layer globs / modules) → constraint config (`.dependency-cruiser.cjs` or `.importlinter`)
- [ ] **Domain terms + deprecated wordings** → `docs/conventions/glossary.md`
- [ ] **Test runner command** → `tests/README.md`
- [ ] **Audit invariants** → `scripts/audit.sh` (exit 0 = release gate)
- [ ] **GC roots / exclusions** → `scripts/gc/check_structure.py` args in `.github/workflows/housekeeping.yml`
- [ ] **First decision** → `docs/decisions/0001-decision-log.md` (delete the example block)
