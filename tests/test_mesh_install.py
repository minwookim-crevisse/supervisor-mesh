"""WO-0022 step 4 (W3): `companions_install` (D88) and `tracker_render` through the tool layer. `claude` is a FAKE on
PATH (tests/_mesh_fakes.py): it logs every argv and registers an installed plugin in the temp HOME's registry."""

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from _helpers import ROOT
from _mesh_fakes import Fakes, parse_caller
from test_mesh_state import item_in
from test_mesh_tracker import FIXTURE

sys.path.insert(0, str(ROOT))
from mesh import tools  # noqa: E402
from mesh.model import Phase  # noqa: E402
from mesh.store import Store  # noqa: E402

PSTACK = [
    ["plugin", "marketplace", "add", "cursor/plugins", "--scope", "project"],
    ["plugin", "install", "pstack@cursor-plugins", "--scope", "project"],
]
MATT = [
    ["plugin", "marketplace", "add", "anthropics/claude-plugins-official", "--scope", "project"],
    ["plugin", "install", "mattpocock-skills@claude-plugins-official", "--scope", "project"],
]
LIST = ["plugin", "marketplace", "list", "--json"]


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.fakes = Fakes(self.tmp / "fakes")
        self.store = Store(self.root / ".mesh")
        self.store.create()

    def tearDown(self):
        self._tmp.cleanup()

    def call(self, name, args=None, caller="pm:pm-uid"):
        env = self.fakes.env({"PATH": "/usr/bin:/bin", "HOME": str(self.home)})
        who = parse_caller(caller)
        ctx = tools.Context.for_project(self.root, env, clock=lambda: "2026-11-18T00:00:00Z", caller=lambda: who)
        return tools.call(ctx, name, args or {})

    def event(self, text):
        return self.store.record_operator_event(text, "2026-11-18T00:00:00Z", "pm-uid").id

    def claude_calls(self):
        return self.fakes.calls("claude")


class CompanionsInstallTests(Case):
    def test_the_whole_plan_runs_at_project_scope_and_the_receipt_records_codes_and_versions(self):
        self.fakes.set(claude_versions={"pstack": "0.4.1", "mattpocock-skills": "2.0.0"})
        ev = self.event("ok, run companions_install")
        out = self.call("companions_install", {"event_id": ev})
        self.assertTrue(out.ok, out.data)
        self.assertEqual(self.claude_calls(), [*PSTACK, LIST, *MATT])
        for argv in self.claude_calls():
            if argv != LIST:
                self.assertEqual(argv[-2:], ["--scope", "project"])
        receipt = out.data["receipt"]
        self.assertEqual([s["exit_code"] for s in receipt["steps"]], [0, 0, 0, 0])
        self.assertEqual(receipt["versions"], {"pstack": "0.4.1", "mattpocock-skills": "2.0.0"})
        self.assertEqual(receipt["manual"][0]["plugin"], "gstack")
        logged = [json.loads(x) for x in self.store.events_path.read_text().splitlines()]
        self.assertEqual([r["type"] for r in logged], ["operator", "receipt"])
        self.assertEqual(logged[1]["versions"], receipt["versions"])
        self.assertEqual(self.store.read().doc.event_uses[ev].status, "done")
        text = json.dumps(out.data)
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn("installed_plugins", text)
        self.assertEqual(self.fakes.calls("git"), [])

    def test_a_declared_official_marketplace_is_not_added_again(self):
        self.fakes.set(claude_marketplaces=[{"name": "claude-plugins-official"}, {"name": "secret-market"}])
        out = self.call("companions_install", {"event_id": self.event("companions_install")})
        self.assertEqual(self.claude_calls(), [*PSTACK, LIST, MATT[1]])
        self.assertNotIn("secret-market", json.dumps(out.data))
        self.assertEqual(
            out.data["receipt"]["steps"][2],
            {
                "plugin": "mattpocock-skills",
                "command": " ".join(["claude", *MATT[0]]),
                "exit_code": None,
                "skipped": "marketplace already declared",
            },
        )

    def test_one_plugin_name_installs_only_that_plugin(self):
        out = self.call("companions_install", {"event_id": self.event("install pstack"), "only": "pstack"})
        self.assertTrue(out.ok, out.data)
        self.assertEqual(self.claude_calls(), PSTACK)

    def test_a_plugin_name_does_not_authorise_the_whole_plan(self):
        out = self.call("companions_install", {"event_id": self.event("install pstack")})
        self.assertEqual(out.data["error"]["reason"], "event_not_bound")
        self.assertEqual(self.claude_calls(), [])

    def test_an_event_that_does_not_name_the_install_is_refused_and_nothing_runs(self):
        for text in ("yes go ahead", "companions_install_all", "the pstack-like thing"):
            out = self.call("companions_install", {"event_id": self.event(text), "only": "pstack"})
            self.assertEqual(out.data["error"]["reason"], "event_not_bound", text)
        self.assertEqual(self.claude_calls(), [])

    def test_unknown_reused_and_stale_events_are_refused(self):
        self.assertEqual(
            self.call("companions_install", {"event_id": "ev-000099"}).data["error"]["reason"], "event_unknown"
        )
        old = self.event("companions_install")
        self.event("something newer")
        self.assertEqual(self.call("companions_install", {"event_id": old}).data["error"]["reason"], "event_stale")
        ev = self.event("companions_install")
        self.assertTrue(self.call("companions_install", {"event_id": ev}).ok)
        self.assertEqual(self.call("companions_install", {"event_id": ev}).data["error"]["reason"], "event_reused")
        reasons = [r["code"] for r in self.store.refusals()]
        self.assertEqual(reasons, ["event_unknown", "event_stale", "event_reused"])

    def test_only_the_pm_installs(self):
        ev = self.event("companions_install")
        for caller in ("lead:backend:be-uid", "unknown"):
            self.assertEqual(
                self.call("companions_install", {"event_id": ev}, caller).data["error"]["reason"], "caller_not_pm"
            )
        self.assertEqual(self.claude_calls(), [])

    def test_a_failed_step_stops_that_plugin_and_reports_every_code(self):
        self.fakes.set(exit={"claude plugin install pstack@cursor-plugins": 1})
        out = self.call("companions_install", {"event_id": self.event("companions_install")})
        self.assertTrue(out.ok, out.data)
        codes = [(s["plugin"], s["exit_code"]) for s in out.data["receipt"]["steps"]]
        self.assertEqual(codes, [("pstack", 0), ("pstack", 1), ("mattpocock-skills", 0), ("mattpocock-skills", 0)])
        self.assertEqual(out.data["receipt"]["versions"], {"pstack": None, "mattpocock-skills": "1.0.0"})
        self.assertFalse(out.data["receipt"]["all_ok"])

    def test_nothing_missing_runs_nothing_and_keeps_the_event_unused(self):
        reg = self.home / ".claude" / "plugins" / "installed_plugins.json"
        reg.parent.mkdir(parents=True)
        reg.write_text(
            json.dumps({"plugins": {"pstack@x": [{"scope": "user"}], "mattpocock-skills@y": [{"scope": "user"}]}})
        )
        ev = self.event("companions_install")
        out = self.call("companions_install", {"event_id": ev})
        self.assertTrue(out.ok, out.data)
        self.assertEqual((out.data["receipt"], self.claude_calls()), (None, []))
        self.assertNotIn(ev, self.store.read().doc.event_uses)


class TrackerRenderToolTests(Case):
    def test_render_writes_the_marked_region_and_a_dry_run_writes_nothing(self):
        (self.root / "docs").mkdir()
        (self.root / "docs" / "tracker.md").write_text(FIXTURE)
        with self.store.locked() as txn:
            item = item_in(Phase.READY, "WO-0040")
            txn.commit(replace(txn.doc, items={item.id: item}, next_id=41))
        dry = self.call("tracker_render", {"dry_run": True})
        self.assertTrue(dry.ok, dry.data)
        self.assertEqual((dry.data["changed"], dry.data["written"]), (True, False))
        self.assertEqual((self.root / "docs" / "tracker.md").read_text(), FIXTURE)
        out = self.call("tracker_render")
        self.assertEqual(
            (out.data["changed"], out.data["written"], out.data["backup"]), (True, True, ".mesh/tracker-original.md")
        )
        self.assertIn("| WO-0040 | ship it |", (self.root / "docs" / "tracker.md").read_text())
        self.assertNotIn(str(self.tmp), json.dumps(out.data))
        self.assertFalse(self.call("tracker_render").data["changed"])

    def test_only_the_pm_renders(self):
        out = self.call("tracker_render", caller="lead:qa:qa-uid")
        self.assertEqual(out.data["error"]["reason"], "caller_not_pm")
        self.assertFalse((self.root / "docs" / "tracker.md").exists())


if __name__ == "__main__":
    unittest.main()
