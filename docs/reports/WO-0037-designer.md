# WO-0037 — README diagrams as SVG (designer lead report)

Date: 2026-11-19. Role: designer lead (Tier 1). Order: `docs/work-orders/2026-11-19-readme-diagrams.md`.
Status: **DONE**: three final SVGs in `docs/assets/`, untracked and not committed. The Ship role commits after the operator says OK.

## Operating model
- The order was first given to a separate projmux pane worker. The operator then decided (DEC-0016, relayed by the PM) that a worker is the lead's in-process subagent.
- The pane worker was told to stop before it had produced anything. A dry run showed 1 agent and 1 pane, and it was then deleted by uid (exit 0).
- The same brief then went to an in-process subagent worker. That worker hand-wrote every SVG. The lead checked each step itself: it re-ran every check and viewed every PNG render.
- The subagent's work is finished; no worker pane remains (label query `worker-of=designer` returns none).

## Deliverables (final, operator-approved)
| file | source candidate | change from candidate | bytes | viewBox |
|---|---|---|---|---|
| `docs/assets/mesh-overview.svg` | Mesh D (built from A per operator) | none (`cmp` exit 0) | 9755 | 0 0 1060 716 |
| `docs/assets/work-item-lifecycle.svg` | Lifecycle B | none (`cmp` exit 0) | 5092 | 0 0 980 460 |
| `docs/assets/operator-approval.svg` | Approval A | footer sentence + its now-unused `.s` style removed, height 700 -> 680 | 6436 | 0 0 1060 680 |

Not done here: the README still embeds the Mermaid blocks; the PM swaps them for these images.

## Candidates and what each optimizes (phase 1)
Scratch, outside the repo: `~/.local/state/supervisor-mesh-design/WO-0037/` holds `candidates/`, `renders/` (2x PNG through gstack browse, headless Chromium), `contact-sheet.html`/`.png` and the worker's raw evidence `worker-report.md`.

- **Mesh A:** a column for each lead and its worker under a full-width PM bar. Straight vertical arrows.
- **Mesh B:** swim lanes by tier, left to right. Shows the tiers most clearly; short horizontal arrows.
- **Mesh C:** radial layout around the PM. Stresses the hub, but it is the tallest and its arrows are diagonal.
- **Mesh D** (added after the operator's feedback, **chosen**):
  - You and the PM exchange arrows both ways.
  - Each role is one container box, "<role> agent". It holds the lead and a dashed-border worker tagged "subagent", with the arrows "brief" and "output" and a "lead verifies" note.
  - The PM connects only to the containers: "work order (work_dispatch)" (for ship, "release order (work_dispatch)") in, "verified result + evidence" out.
  - No "each box is a pane" footer.
- **Lifecycle A:** one row like the Mermaid diagram, with rework below. Labels are coloured by who takes the step, and failure arrows are red.
- **Lifecycle B** (**chosen**): two rows that fold into a loop, so the rework cycle reads as a cycle. Narrower than A.
- **Approval A** (**chosen**): a classic sequence diagram with 5 lifelines and numbered steps 1-7. The transcript check is a note with a shield icon.
- **Approval B:** a numbered step list with from -> to chips, in two groups.

## Operator decisions applied
1. The "Each box is a Claude pane managed by projmux" footer is dropped everywhere, because workers are subagents (DEC-0016).
2. The lifecycle "who takes the step" key is kept. The other footer sentences are dropped.
3. The PM -> agent orders are labelled `work_dispatch`.

## Evidence (lead re-run, captured, from the repo root)
```
docs/assets/mesh-overview.svg parse_exit=0 bytes=9755 viewBox="0 0 1060 716"
docs/assets/operator-approval.svg parse_exit=0 bytes=6436 viewBox="0 0 1060 680"
docs/assets/work-item-lifecycle.svg parse_exit=0 bytes=5092 viewBox="0 0 980 460"
cmp_mesh=0
cmp_lifecycle=0
diff approval-A.svg operator-approval.svg:
  1c1   height 700 -> 680 (svg width/height/viewBox)
  6d5   < .s{font-size:13px;fill:#57606a}
  21c20 card rect height 698 -> 678
  90,91d88 < <!-- footer --> / < <text class="s" ...>A prompt the PM makes up ...</text>
grep -lE '<script|<image|foreignObject|href="http|/home/' docs/assets/*.svg -> forbidden_exit=1 (no hits)
font sizes used: 13 15
classes used in operator-approval: l m n o t v ; styles defined: t l m n o v (no orphan)
scrub: 0 hits
scrub_exit=0
git status --short docs/assets -> ?? docs/assets/
```
XML parse command: `python3 -c "import xml.dom.minidom,sys; xml.dom.minidom.parse(sys.argv[1])" <file>`.

Visual review by the lead:
- All 7 phase-1 renders, plus Mesh D and both final approval renders, were viewed as PNG.
- No overlap or clipping.
- Labels match the README terms (`role=pm`, `work_dispatch`, `pane_command`, `UserPromptSubmit`, state names).

Style compliance:
- Hand-authored SVG with a system font stack.
- No scripts, images, foreignObject or external refs.
- A white card with a #d0d7de border, so it reads on GitHub light and dark. The contact sheet's last row shows samples on #0d1117.

## Notes for the PM
- **README line 43** still says "Each box is a Claude pane managed by projmux". This contradicts DEC-0016 and the new mesh diagram. It is PM-owned text.
- **Approval A margin:** the bottom margin is 24 px below the lifelines, matching the top margin.
- **Mesh D container heights:** all four containers are the same height. The ship container uses the spare room for "the only role that commits and pushes".
- **Scratch evidence** is kept outside the repo. Nothing in the repo besides `docs/assets/` and this report was written by this order.
