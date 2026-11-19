# Work order — README diagrams as SVG (batch D)

> Paths: `<repo>` = this repo. Absolute paths travel only in dispatch messages (DEC-0008).
> Common header (operating model, discipline, constraints, report format): same as `docs/work-orders/2026-10-21-verification-skills.md`, EXCEPT write access as stated.
> Operator decision (2026-11-19): README diagrams are hand-authored SVG (not Mermaid, not AI-generated raster, not draw.io), reviewed as rendered candidates before one is chosen.

## WO-0037 — Three README diagrams as SVG (role: designer) · depends: none
**Read set:** `README.md` (sections "Why it works this way", "How the pieces fit", "MCP server" > "How your approval is recorded"; the three Mermaid blocks are the content source), `docs/designs/work-item-state-model.md` (sections 1 and 4, for the lifecycle), `agents/pm-orchestrator.md` ("Channel choice").
**Write access:** NEW `docs/assets/` (the SVG files), your report `docs/reports/WO-0037-designer.md`, and scratch renders outside the repo under `~/.local/state/supervisor-mesh-design/WO-0037/`. NOT `README.md` (the PM swaps the Mermaid blocks for the chosen images).
**Must:**
- Three diagrams, same content as the Mermaid blocks: (1) the mesh: operator, PM (Tier 0, `role=pm`), four role leads, their workers, solid arrows for orders, dotted arrows for results with evidence; (2) the work-item lifecycle: draft, ready, running, verifying, verified, closed, rework, with the actor on each arrow; (3) the operator-approval sequence: operator prompt, prompt hook, candidate event, `pane_command` citation, transcript check, keystrokes into the lead pane, notice to the lead.
- Style in the spirit of a clean architecture diagram: rounded boxes, simple line icons drawn in SVG (person, terminal pane, shield, gear, git branch), grouped regions with a light tint and a label, short arrow labels. No brand logos, no external fonts or images, no scripts; system font stack; readable on GitHub light AND dark themes (transparent background with sufficient contrast, or a light card with a border).
- Text in English, matching README terms exactly (`role=pm`, `work_dispatch`, `pane_command`, state names).
- Width about 900-1100 px viewBox; text at least 13 px at 1x; every SVG under 60 KB; valid XML (`python3 -c "import xml.dom.minidom,sys; xml.dom.minidom.parse(sys.argv[1])" <file>` exit 0).
- Candidates: for diagram (1), produce 3 style variants (for example: grouped regions, swim lanes by tier, radial around the PM); for (2) and (3), 2 variants each. Render every candidate to PNG at 2x with a headless browser (gstack browse, `$B`) into the scratch dir, and put a contact sheet (one HTML page or PNG showing all candidates side by side with labels A, B, C) there for the operator to choose from.
**DoD (captured):** XML parse exit 0 and file size for each SVG; the PNG renders and the contact sheet path; `scripts/scrub.sh` 0 hits; a short note per candidate (what it optimizes). The lead reviews every render itself before reporting. Final files after the operator's choice: `docs/assets/mesh-overview.svg`, `docs/assets/work-item-lifecycle.svg`, `docs/assets/operator-approval.svg`.

## Constraints
Read-only on `docs/decisions/`, `docs/tracker.md`, `docs/work-orders/`, `AGENTS.md`, `README.md`, design docs. No commits, no push (Ship only, after operator OK). No worktree-destroying git commands. No secrets or absolute home paths in repo files (scrub). Self-report is not evidence: every DoD item is captured output.
