"""WO-0022 step 4 (W3): `mesh/tracker.py`, the legacy import and the marked-region render of docs/tracker.md.

The fixture rows are copied verbatim from this repo's hand-written tracker (the edge cases in the WO-0022 report,
section "Legacy-import edge cases"). The live file is only ever READ, and copied into a temp dir."""

import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from _helpers import ROOT
from test_mesh_state import item_in

sys.path.insert(0, str(ROOT))
from mesh import tracker  # noqa: E402
from mesh.model import Phase, empty_doc  # noqa: E402

FIXTURE = """# Tracker

> Single owner: the Orchestrator (PM) writes this file.

| id | title | role | depends | status | report |
|---|---|---|---|---|---|
| WO-0001 | Security review — Slack bridge 0.1.2 draft | security | — | FAIL (2 🔴) | `docs/reports/WO-0001-security.md` |
| WO-0004 | Rework of accepted findings (`docs/work-orders/2026-10-14-slack-rework.md`) | backend | WO-0001..0003 | PASS (open item → WO-0005) | `docs/reports/WO-0004-backend.md` |
| WO-0007 | Report path hygiene (own report only) | security, backend, qa | — | closed (grep /home/ = 0) | — |
| WO-0005 | Re-review + QA test fidelity + live E2E + `make check` | security, qa | WO-0004, WO-0007 | dispatched | `docs/reports/WO-0005-security.md`, `docs/reports/WO-0005-qa.md` |
| WO-0009 | mesh CLI skeleton | backend | batch 3 closed | open (queued) | — |
| WO-0011 | mesh spawn + mesh status | backend | WO-0009 (∥ WO-0010) | open (queued) | — |
| WO-0017 | App-driving verification skill | qa | — (∥ WO-0016) | pass with correction (live init-check rerun FAIL → verifier fixed) | `docs/reports/WO-0017-qa.md` |
| WO-0023 | Security review of the MCP tool surface | security | WO-0022 | waiting | `docs/reports/WO-0023-security.md` |
| WO-0025 | Release 0.2.0: commit batches W then M | ship | WO-0020, WO-0022..0024, PM gate | waiting | — |
| WO-0030 | Odd one | qa | — | maybe later | — |

Parallel batch 1 = WO-0001 / WO-0002 / WO-0003 (read-only reviews, disjoint report files).
Follow-ups (not dispatched): smv.py package split.
"""  # noqa: E501 (rows copied verbatim from docs/tracker.md)


def rows_by_id(text):
    return {r.id: r for r in tracker.parse_legacy(text).rows}


class LegacyImportTests(unittest.TestCase):
    def test_every_edge_case_keeps_its_raw_text_and_maps_only_when_unambiguous(self):
        rows = rows_by_id(FIXTURE)
        self.assertEqual(
            list(rows),
            [
                "WO-0001",
                "WO-0004",
                "WO-0007",
                "WO-0005",
                "WO-0009",
                "WO-0011",
                "WO-0017",
                "WO-0023",
                "WO-0025",
                "WO-0030",
            ],
        )  # out-of-order rows keep their order
        self.assertEqual([r.order for r in rows.values()], list(range(10)))
        self.assertEqual(rows["WO-0007"].roles, ("security", "backend", "qa"))  # multi-role
        expect = {
            "WO-0001": ("FAIL (2 🔴)", "fail"),
            "WO-0004": ("PASS (open item → WO-0005)", "pass"),
            "WO-0007": ("closed (grep /home/ = 0)", "closed"),
            "WO-0005": ("dispatched", "dispatched"),
            "WO-0009": ("open (queued)", "open"),
            "WO-0017": ("pass with correction (live init-check rerun FAIL → verifier fixed)", "pass"),
            "WO-0023": ("waiting", "open"),
            "WO-0030": ("maybe later", "legacy"),
        }
        for rid, (raw, status) in expect.items():
            self.assertEqual((rows[rid].status_raw, rows[rid].status), (raw, status), rid)
        self.assertNotIn("verified", {r.status for r in rows.values()})

    def test_depends_ranges_prose_and_parallel_annotations(self):
        rows = rows_by_id(FIXTURE)
        self.assertEqual(rows["WO-0004"].depends, ("WO-0001", "WO-0002", "WO-0003"))
        self.assertEqual(rows["WO-0009"].depends, ())
        self.assertEqual(rows["WO-0009"].depends_raw, "batch 3 closed")
        self.assertEqual(rows["WO-0011"].depends, ("WO-0009",))  # "∥ WO-0010" is parallel, not a dependency
        self.assertEqual(rows["WO-0017"].depends, ())
        self.assertEqual(rows["WO-0025"].depends, ("WO-0020", "WO-0022", "WO-0023", "WO-0024"))
        self.assertEqual(rows["WO-0025"].depends_raw, "WO-0020, WO-0022..0024, PM gate")
        self.assertEqual(rows["WO-0001"].depends_raw, "—")

    def test_reports_with_two_paths_and_none(self):
        rows = rows_by_id(FIXTURE)
        self.assertEqual(rows["WO-0005"].reports, ("docs/reports/WO-0005-security.md", "docs/reports/WO-0005-qa.md"))
        self.assertEqual(rows["WO-0007"].reports, ())
        self.assertEqual(
            rows["WO-0004"].title, "Rework of accepted findings (`docs/work-orders/2026-10-14-slack-rework.md`)"
        )

    def test_a_file_without_a_tracker_table_imports_nothing(self):
        self.assertEqual(tracker.parse_legacy("# notes\n\nno table here\n").rows, ())
        self.assertEqual(tracker.parse_legacy("| a | b |\n|---|---|\n| 1 | 2 |\n").rows, ())

    def test_a_duplicate_id_keeps_the_first_row_and_reports_the_line(self):
        text = FIXTURE.replace("| WO-0030 | Odd one", "| WO-0001 | Odd one")
        preview = tracker.parse_legacy(text)
        self.assertEqual(rows_by_id(text)["WO-0001"].status_raw, "FAIL (2 🔴)")
        self.assertEqual([s["reason"] for s in preview.skipped], ["duplicate id WO-0001"])

    def test_foreign_header_names_map_by_name_and_missing_columns_stay_empty(self):
        text = (
            "| \ub2e8\uacc4 | \ub0b4\uc6a9 | \uc0c1\ud0dc |\n|---|---|---|\n"
            "| S1 | \uc124\uacc4 | \uc644\ub8cc |\n"
            "| S2 | \uad6c\ud604 | \uc9c4\ud589 \uc911 |\n"
            "| S3 | \ubc30\ud3ec | \ub300\uae30 |\n"
        )
        rows = rows_by_id(text)
        self.assertEqual(
            [(r.id, r.title, r.status) for r in rows.values()],
            [("S1", "\uc124\uacc4", "closed"), ("S2", "\uad6c\ud604", "dispatched"), ("S3", "\ubc30\ud3ec", "open")],
        )
        self.assertEqual(rows["S1"].roles, ())

    def test_the_live_tracker_imports_every_row_and_none_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "tracker.md"
            shutil.copyfile(ROOT / "docs" / "tracker.md", copy)  # read-only fixture source
            text = copy.read_text(encoding="utf-8")
        ids = [line.split("|")[1].strip() for line in text.splitlines() if line.startswith("| WO-")]
        rows = tracker.parse_legacy(text).rows
        self.assertEqual([r.id for r in rows], ids)
        self.assertTrue(all(r.status != "verified" and r.status_raw for r in rows))


class RenderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "docs").mkdir()
        (self.root / ".mesh").mkdir()
        self.path = self.root / "docs" / "tracker.md"

    def tearDown(self):
        self._tmp.cleanup()

    def doc(self):
        legacy = {r.id: r for r in tracker.parse_legacy(FIXTURE).rows}
        item = item_in(Phase.READY, "WO-0040")
        return replace(empty_doc(), items={item.id: item}, legacy=legacy)

    def test_the_first_render_replaces_only_the_table_and_backs_up_the_original(self):
        self.path.write_text(FIXTURE, encoding="utf-8")
        out = tracker.render_file(self.root, self.doc())
        self.assertEqual((out.changed, out.first_render), (True, True))
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Tracker\n\n> Single owner: the Orchestrator (PM) writes this file.\n\n"))
        self.assertTrue(text.endswith(tracker.END + "\n\n" + FIXTURE.split("\n\n")[-1]))  # trailing prose kept
        self.assertEqual(text.count(tracker.BEGIN), 1)
        self.assertEqual(text.count(tracker.END), 1)
        self.assertEqual((self.root / ".mesh" / out.backup).read_text(encoding="utf-8"), FIXTURE)
        self.assertIn(
            "| WO-0001 | Security review — Slack bridge 0.1.2 draft | security | — | fail (legacy: FAIL (2 🔴))", text
        )
        self.assertIn("| WO-0030 | Odd one | qa | — | legacy: maybe later | — |", text)
        self.assertIn("| WO-0001 | ", text.split(tracker.BEGIN)[1])
        item_line = next(line for line in text.splitlines() if line.startswith("| WO-0040 |"))
        self.assertEqual(item_line, "| WO-0040 | ship it | backend | — | ready | — |")
        self.assertLess(text.index("| WO-0030 |"), text.index("| WO-0040 |"))

    def test_a_marked_file_changes_only_inside_the_markers_and_a_rerender_is_a_noop(self):
        self.path.write_text(FIXTURE, encoding="utf-8")
        tracker.render_file(self.root, self.doc())
        edited = self.path.read_text(encoding="utf-8").replace("Follow-ups", "Hand-edited follow-ups")
        self.path.write_text(edited, encoding="utf-8")
        again = tracker.render_file(self.root, self.doc())
        self.assertEqual((again.changed, again.first_render, again.backup), (False, False, None))
        self.assertEqual(self.path.read_text(encoding="utf-8"), edited)
        self.assertEqual(len(list((self.root / ".mesh").glob("tracker-original*"))), 1)

    def test_a_dry_run_writes_nothing(self):
        self.path.write_text(FIXTURE, encoding="utf-8")
        out = tracker.render_file(self.root, self.doc(), dry_run=True)
        self.assertEqual((out.changed, out.first_render, out.backup), (True, True, None))
        self.assertEqual(self.path.read_text(encoding="utf-8"), FIXTURE)
        self.assertEqual(list((self.root / ".mesh").iterdir()), [])

    def test_a_missing_tracker_is_created_and_unbalanced_markers_are_refused(self):
        out = tracker.render_file(self.root, self.doc())
        self.assertTrue(out.changed)
        self.assertTrue(self.path.read_text(encoding="utf-8").startswith("# Tracker\n\n" + tracker.BEGIN))
        self.path.write_text("x\n" + tracker.BEGIN + "\n| a |\n", encoding="utf-8")
        with self.assertRaises(tracker.InvalidArgs):
            tracker.render_file(self.root, self.doc())

    def test_cells_are_escaped(self):
        item = item_in(Phase.DRAFT)
        item = replace(item, contract=replace(item.contract, goal="a | b\nc"))
        region = tracker.render_region(replace(empty_doc(), items={item.id: item}))
        self.assertIn("| a \\| b c |", region)


if __name__ == "__main__":
    unittest.main()
