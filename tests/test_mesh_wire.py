"""WO-0022 step 4 (W1) through the tool layer: mesh_status, work_dispatch and pane_command, in process, with projmux and
tmux as FAKE executables on PATH (tests/_mesh_fakes.py) and HOME pointed at a temp dir."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from _helpers import ROOT
from _mesh_fakes import Fakes, parse_caller
from test_mesh_state import contract, doc_of, item_in

sys.path.insert(0, str(ROOT))
from mesh import companions, dispatch, tools  # noqa: E402
from mesh.model import Phase, Scope  # noqa: E402
from mesh.store import Store, load_doc  # noqa: E402


class WireCase(unittest.TestCase):
    caller = "pm:pm-uid"

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

    def ctx(self, caller=None, **env):
        base = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "PMX_INTERNAL_ACTIVATION_PANE_UID": "pane-pm-uid"}
        who = parse_caller(caller or self.caller)  # injected in process (F1: no env override)
        return tools.Context.for_project(
            self.root, self.fakes.env(base, **env), clock=lambda: "2026-11-18T00:00:00Z", caller=lambda: who
        )

    def call(self, name, args=None, caller=None):
        return tools.call(self.ctx(caller), name, args or {})

    def seed(self, *items, **kw):
        with self.store.locked() as txn:
            txn.commit(doc_of(*items, **kw))

    def pm_agent(self):
        return self.fakes.agent("pm-uid", "pm", self.root, pane="pane-pm-uid", tmux_id="%0")


class MeshStatusTests(WireCase):
    def registry(self, plugins):
        path = self.home / ".claude" / "plugins" / "installed_plugins.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 2, "plugins": plugins}), encoding="utf-8")

    def test_items_by_state_live_leads_pending_actions_and_identity(self):
        from mesh.model import ActionKind, PendingAction

        pending = PendingAction("WO-0002-d1", ActionKind.DISPATCH_SEND, {"role": "frontend", "target_uid": "fe"})
        self.seed(
            item_in(Phase.DRAFT),
            item_in(
                Phase.READY,
                item_id="WO-0002",
                contract=contract(version=1, role="frontend"),
                pending_actions=(pending,),
            ),
            item_in(Phase.READY, item_id="WO-0003", contract=contract(version=1)),
        )
        self.pm_agent()
        self.fakes.agent("be-uid", "backend", self.root)
        self.fakes.agent("qa-uid", "qa", self.tmp)  # another repo: not this project's lead
        result = self.call("mesh_status")
        self.assertTrue(result.ok, result.data)
        d = result.data
        self.assertEqual(d["identity"], {"kind": "pm", "role": None})
        self.assertEqual(d["items"], {"draft": ["WO-0001"], "ready": ["WO-0002", "WO-0003"]})
        self.assertEqual(d["leads"], [{"role": "backend", "present": True}, {"role": "frontend", "present": False}])
        self.assertEqual(
            d["pending_actions"],
            [{"item_id": "WO-0002", "action_id": "WO-0002-d1", "kind": "dispatch_send", "attempts": []}],
        )
        text = json.dumps(d)
        for secret in ("be-uid", "fe", str(self.root), str(self.home)):
            self.assertNotIn(f'"{secret}"', text)

    def test_companions_are_presence_only_and_scope_aware(self):
        other = self.tmp / "other"
        self.registry(
            {
                "pstack@cursor-plugins": [{"scope": "user", "installPath": str(self.home / "p")}],
                "mattpocock-skills@claude-plugins-official": [{"scope": "project", "projectPath": str(other)}],
                "secret-plugin@private-market": [{"scope": "user", "installPath": str(self.home / "s")}],
            }
        )
        d = self.call("mesh_status").data
        self.assertEqual(d["companions"], {"gstack": False, "mattpocock-skills": False, "pstack": True})
        self.registry(
            {"mattpocock-skills@claude-plugins-official": [{"scope": "project", "projectPath": str(self.root)}]}
        )
        (self.home / ".claude" / "skills" / "gstack").mkdir(parents=True)
        (self.home / ".claude" / "skills" / "gstack" / "SKILL.md").write_text("x")
        d = self.call("mesh_status").data
        self.assertEqual(d["companions"], {"gstack": True, "mattpocock-skills": True, "pstack": False})
        text = json.dumps(d)
        for leak in ("secret-plugin", "private-market", "cursor-plugins", "installed_plugins", str(self.home)):
            self.assertNotIn(leak, text)

    def skill(self, base, name):
        (base / name).mkdir(parents=True)
        (base / name / "SKILL.md").write_text("x")
        return base / name

    def test_gstack_counts_from_one_of_its_skill_dirs_under_home(self):
        self.skill(self.home / ".claude" / "skills" / "gstack", "office-hours")  # no gstack/SKILL.md at the top
        d = self.call("mesh_status").data
        self.assertEqual(d["companions"], {"gstack": True, "mattpocock-skills": False, "pstack": False})
        self.assertNotIn(str(self.home), json.dumps(d))
        self.assertNotIn("office-hours", json.dumps(d))

    def test_a_symlinked_skill_dir_counts_by_name_only(self):
        target = self.skill(self.tmp / "elsewhere", "pe")
        (self.home / ".claude" / "skills").mkdir(parents=True)
        (self.home / ".claude" / "skills" / "plan-eng-review").symlink_to(target, target_is_directory=True)
        self.assertTrue(companions.skill_present(self.home, "plan-eng-review"))
        self.assertFalse(companions.skill_present(self.home, "ready"))
        self.skill(self.home / ".claude" / "skills" / "gstack", "office-hours")
        self.assertTrue(companions.skill_present(self.home, "office-hours"))
        self.assertFalse(companions.skill_present(None, "office-hours"))

    def test_status_is_read_only_and_works_for_an_unknown_caller_and_without_projmux(self):
        self.seed(item_in(Phase.DRAFT))
        before = (self.root / ".mesh" / "state.json").read_bytes()
        self.fakes.state["exit"] = {"projmux get agents -o": 1}
        self.fakes.save()
        result = self.call("mesh_status", caller="unknown")
        self.assertTrue(result.ok, result.data)
        self.assertEqual(result.data["identity"], {"kind": "unknown", "role": None})
        self.assertEqual(result.data["leads"], [{"role": "backend", "present": None}])
        self.assertEqual(result.data["projmux"], "unavailable")
        self.assertEqual((self.root / ".mesh" / "state.json").read_bytes(), before)
        self.assertEqual(self.store.refusals(), [])

    def test_an_uninitialised_project_still_reports_identity_and_companions(self):
        (self.root / ".mesh" / "state.json").unlink()
        d = self.call("mesh_status").data
        self.assertEqual((d["initialized"], d["items"], d["identity"]["kind"]), (False, {}, "pm"))
        self.assertIn("companions", d)


def skill_header() -> str:
    """The fenced header block of skills/dispatch/SKILL.md step 3, dedented: the ONE text both paths send."""
    text = (ROOT / "skills" / "dispatch" / "SKILL.md").read_text(encoding="utf-8")
    block = text.split("prepended verbatim", 1)[1].split("```", 2)[1]
    return "\n".join(line.strip() for line in block.strip().splitlines())


class DispatchHeaderTests(unittest.TestCase):
    def test_the_header_is_the_dispatch_skill_text(self):
        self.assertTrue(skill_header().startswith("[supervisor-mesh work order]\n"))
        self.assertEqual(dispatch.HEADER, skill_header())

    def test_compose_is_one_pure_function_of_the_item_record(self):
        item = item_in(
            Phase.READY,
            contract=contract(
                version=2,
                goal="add the export",
                done_criteria=("tests pass", "lint clean"),
                depends=("WO-0007",),
                scope=Scope(read_set=("docs/a.md", "/abs/b.md"), write_set=("src/x.py",), report="docs/reports/r.md"),
            ),
        )
        text = dispatch.compose(item, Path("/work/proj"))
        self.assertEqual(text, dispatch.compose(item, Path("/work/proj")))
        self.assertTrue(text.startswith(dispatch.HEADER + "\n\n"))
        for part in (
            "Work item: WO-0001 (role backend, type feature, contract v2)",
            "Mission: add the export",
            "- tests pass",
            "- lint clean",
            "- /work/proj/docs/a.md",
            "- /abs/b.md",
            "- src/x.py",
            "Depends on: WO-0007",
            "Report: /work/proj/docs/reports/r.md",
            "work_transition",
        ):
            self.assertIn(part, text)
        self.assertNotEqual(dispatch.text_sha256(text), dispatch.text_sha256(text + " "))

    def test_the_composer_does_no_io(self):
        import ast

        tree = ast.parse((ROOT / "mesh" / "dispatch.py").read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertEqual(names & {"os", "subprocess", "io", "shutil", "socket", "time", "sys"}, set())


class WorkDispatchTests(WireCase):
    def setUp(self):
        super().setUp()
        self.seed(
            item_in(
                Phase.READY,
                contract=contract(
                    version=1, scope=Scope(read_set=("docs/spec.md",), report="docs/reports/WO-0001-backend.md")
                ),
            )
        )
        self.pm_agent()

    def dispatch(self, **env):
        return tools.call(self.ctx(**env), "work_dispatch", {"item_id": "WO-0001"})

    def actions(self):
        return self.store.read().doc.items["WO-0001"].pending_actions

    def sends(self):
        return self.fakes.load().get("messages", [])

    def test_dispatch_records_the_pending_send_then_sends_the_composed_order_after_the_commit(self):
        self.fakes.agent("be-uid", "backend", self.root)
        result = self.dispatch(MESH_FAKE_PEEK=str(self.root / ".mesh" / "state.json"))
        self.assertTrue(result.ok, result.data)
        text = dispatch.compose(self.store.read().doc.items["WO-0001"], self.root)
        (action,) = self.actions()
        self.assertEqual(
            dict(action.payload),
            {
                "role": "backend",
                "target_uid": "be-uid",
                "source_uid": "pm-uid",
                "text_sha256": dispatch.text_sha256(text),
                "message_ref": "mesh-WO-0001-d1",
                "attempts": "delivered",  # D93: every attempt recorded
            },
        )
        self.assertTrue(action.done)
        (sent,) = self.sends()
        self.assertEqual(
            sent["args"],
            [
                "agent",
                "message",
                "send",
                "uid:be-uid",
                "--source",
                "uid:pm-uid",
                "--message-ref",
                "mesh-WO-0001-d1",
                "--",
                text,
            ],
        )
        at_send = load_doc(json.loads(sent["peek"])).items["WO-0001"].pending_actions
        self.assertEqual([(a.action_id, a.done) for a in at_send], [("WO-0001-d1", False)])  # committed first
        self.assertEqual(
            result.data["dispatch"],
            {
                "action_id": "WO-0001-d1",
                "message_ref": "mesh-WO-0001-d1",
                "sent": True,
                "replayed": False,
                "attempts": ["delivered"],
            },
        )
        raw = (self.root / ".mesh" / "state.json").read_text()
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn("Mission:", raw)

    def test_a_failed_send_stays_pending_and_the_replay_uses_the_same_message_ref(self):
        self.fakes.agent("be-uid", "backend", self.root)
        self.fakes.set(send_code=1)
        first = self.dispatch()
        self.assertTrue(first.ok, first.data)
        self.assertEqual(first.data["dispatch"]["sent"], False)
        self.assertEqual([a.done for a in self.actions()], [False])
        self.fakes.set(send_code=0)
        again = self.dispatch()
        self.assertTrue(again.ok, again.data)
        self.assertEqual(
            again.data["dispatch"],
            {
                "action_id": "WO-0001-d1",
                "message_ref": "mesh-WO-0001-d1",
                "sent": True,
                "replayed": True,
                "attempts": ["delivered"],  # this call's attempt; the first call's "refused" stays on the action
            },
        )
        self.assertEqual([(a.action_id, a.done) for a in self.actions()], [("WO-0001-d1", True)])
        refs = [m["args"][m["args"].index("--message-ref") + 1] for m in self.sends()]
        self.assertEqual(refs, ["mesh-WO-0001-d1", "mesh-WO-0001-d1"])

    def test_a_replay_whose_order_no_longer_matches_the_recorded_hash_is_not_sent(self):
        self.fakes.agent("be-uid", "backend", self.root)
        self.fakes.set(send_code=1)
        self.dispatch()
        with self.store.locked() as txn:  # the contract changed since: the recorded hash no longer matches
            item = txn.doc.items["WO-0001"]
            from dataclasses import replace

            txn.commit(
                replace(
                    txn.doc, items={"WO-0001": replace(item, contract=replace(item.contract, goal="something else"))}
                )
            )
        self.fakes.set(send_code=0)
        out = tools.replay_dispatches(self.ctx())
        self.assertEqual(out, [{"action_id": "WO-0001-d1", "sent": False, "superseded": True}])
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(self.actions()[0].done, True)
        self.assertEqual(self.actions()[0].payload["superseded"], "order changed")

    def test_no_live_lead_is_refused_and_nothing_is_sent(self):
        self.fakes.agent("be-away", "backend", self.tmp)
        result = self.dispatch()
        self.assertEqual(result.data["error"]["reason"], "role_has_no_lead")
        self.assertEqual((self.actions(), self.sends()), ((), []))

    def test_only_the_pm_dispatches(self):
        self.fakes.agent("be-uid", "backend", self.root)
        result = tools.call(self.ctx("lead:backend:be-uid"), "work_dispatch", {"item_id": "WO-0001"})
        self.assertEqual(result.data["error"]["reason"], "not_in_table")
        self.assertEqual(self.sends(), [])


if __name__ == "__main__":
    unittest.main()
