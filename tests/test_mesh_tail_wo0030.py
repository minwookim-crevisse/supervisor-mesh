"""WO-0030 tail: D117 (4) work_dispatch needs a work-order file under the project root, ASCII-only item ids, no dead
code after output_failure's return, the security test module importable from the repo root as well as by discovery,
D118 (the PM session is projmux's live one on every operator event) and D119 (the agent-hook ingest deny rule)."""

import ast
import json
import subprocess
import sys
import unittest

from _helpers import ROOT
from _mesh_fakes import Fakes, parse_caller
from test_mesh_security_wo0023 import Case, user
from test_mesh_state import contract, item_in
from test_mesh_wire import WireCase

sys.path.insert(0, str(ROOT))
from mesh import init, tools, tracker  # noqa: E402
from mesh.model import Phase, Scope  # noqa: E402


class WorkOrderFileTests(WireCase):
    """D117 (4): the item's work-order file must exist as a regular file under the project root (realpath)."""

    def setUp(self):
        super().setUp()
        self.seed(item_in(Phase.READY, contract=contract(version=1, scope=Scope(report="docs/reports/r.md"))))
        self.pm_agent()
        self.fakes.agent("be-uid", "backend", self.root)
        self.order = self.root / "docs" / "work-orders" / "WO-0001.md"
        self.order.parent.mkdir(parents=True)

    def dispatch(self):
        return tools.call(self.ctx(), "work_dispatch", {"item_id": "WO-0001"})

    def nothing_happened(self):
        item = self.store.read().doc.items["WO-0001"]
        self.assertEqual((item.phase, item.pending_actions), (Phase.READY, ()))
        self.assertEqual(self.fakes.load().get("messages", []), [])

    def test_a_missing_work_order_is_refused_and_nothing_is_recorded_or_sent(self):
        result = self.dispatch()
        self.assertFalse(result.ok, result.data)
        self.assertEqual(result.data["error"]["reason"], "work_order_missing")
        self.nothing_happened()

    def test_a_directory_in_place_of_the_work_order_is_refused(self):
        self.order.mkdir()
        self.assertEqual(self.dispatch().data["error"]["reason"], "work_order_missing")
        self.nothing_happened()

    def test_a_present_work_order_is_sent(self):
        self.order.write_text("# WO-0001\n", encoding="utf-8")
        result = self.dispatch()
        self.assertTrue(result.ok, result.data)
        self.assertTrue(result.data["dispatch"]["sent"])
        self.assertEqual(len(self.fakes.load().get("messages", [])), 1)

    def test_a_work_order_symlinked_outside_the_root_is_refused(self):
        outside = self.tmp / "outside.md"
        outside.write_text("# planted\n", encoding="utf-8")
        self.order.symlink_to(outside)
        result = self.dispatch()
        self.assertFalse(result.ok, result.data)
        self.assertEqual(result.data["error"]["reason"], "outside_root")
        self.nothing_happened()


class AsciiItemIdTests(WireCase):
    BAD = ("WO-٠١٢٣", "WO-０００１", "WO-000١")  # Arabic-Indic, fullwidth, mixed

    def setUp(self):
        super().setUp()
        self.seed(item_in(Phase.READY, contract=contract(version=1)))
        self.pm_agent()

    def test_non_ascii_digits_are_refused_at_every_id_input(self):
        for bad in self.BAD:
            for name, args in (
                ("work_show", {"item_id": bad}),
                ("work_dispatch", {"item_id": bad}),
                ("work_transition", {"item_id": bad, "verb": "cancel", "args": {}}),
                ("pane_command", {"role": "backend", "command": "/help", "item_id": bad}),
                ("work_new", {"goal": "g", "done_criteria": ["d"], "role": "backend", "type": "feature",
                              "depends": [bad]}),
            ):  # fmt: skip
                result = self.call(name, args)
                self.assertFalse(result.ok, (name, bad))
                self.assertEqual(result.data["error"]["code"], "invalid_args", (name, bad, result.data))
            self.assertEqual(tracker.parse_depends(bad), (), bad)

    def test_ascii_ids_are_accepted(self):
        self.assertTrue(self.call("work_show", {"item_id": "WO-0001"}).ok)
        self.assertEqual(tracker.parse_depends("WO-0001, WO-0002..0003"), ("WO-0001", "WO-0002", "WO-0003"))

    def test_every_id_match_uses_the_one_pattern(self):
        for path in sorted((ROOT / "mesh").glob("*.py")):
            self.assertNotIn(r"WO-\d", path.read_text(encoding="utf-8"), path.name)


class OutputFailureTests(unittest.TestCase):
    def test_output_failure_has_no_code_after_its_final_return(self):
        tree = ast.parse((ROOT / "mesh" / "layers.py").read_text(encoding="utf-8"))
        (fn,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "output_failure"]
        self.assertIsInstance(fn.body[-1], ast.Return)
        returns = [i for i, s in enumerate(fn.body) if isinstance(s, ast.Return)]
        self.assertEqual(returns, [len(fn.body) - 1], "a statement follows an unconditional return")
        self.assertNotIn("compile", ast.dump(fn))


class SecurityModuleImportTests(unittest.TestCase):
    def test_the_security_module_loads_from_the_repo_root(self):
        cmd = [sys.executable, "-m", "unittest", "tests.test_mesh_security_wo0023.D112CliDestTests"]
        run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])


class D118LiveSessionTests(Case):
    """Item 5, security N4 (D117-2 residual) ported: the PM session is never pinned from a prompt; every operator event
    must carry the session id and transcript path projmux reports for the pinned PM agent right now."""

    def setUp(self):
        super().setUp()
        self.pm_and_lead()
        self.pin()  # pm_uid pinned (as after mesh_init apply), no session recorded anywhere in state.json

    def test_n4_a_fabricated_session_and_transcript_is_refused_and_the_real_session_still_records(self):
        self.live_session("pm-uid", "s-real")
        fab = self.transcript("fab-0001", user("companions_install", "fab-u-1"))
        self.hook("pane-pm", "companions_install", session="fab-0001", transcript=fab)
        self.assertEqual((self.texts(), self.hook_errors()), ([], ["session_not_pm"]))
        real = self.transcript("s-real", user("companions_install", "u-1"))
        self.hook("pane-pm", "companions_install", session="s-real", transcript=real)
        self.assertEqual(self.texts(), ["companions_install"])
        fab2 = self.transcript("fab-0002", user("run /config now", "fab-u-2"))
        self.hook("pane-pm", "run /config now", session="fab-0002", transcript=fab2)
        self.assertEqual((self.texts(), self.hook_errors()), (["companions_install"], ["session_not_pm"] * 2))
        self.assertIsNone(self.doc().pm_session)

    def test_no_session_id_from_projmux_fails_closed(self):
        t = self.transcript("s-pm", user("companions_install", "u-1"))
        self.hook("pane-pm", "companions_install", session="s-pm", transcript=t)  # projmux reports no session
        self.fakes.claude_session("pm-uid", None, t)
        self.hook("pane-pm", "companions_install", session="s-pm", transcript=t)
        self.assertEqual((self.texts(), self.hook_errors()), ([], ["session_not_pm"] * 2))

    def test_projmux_unavailable_fails_closed(self):
        self.live_session("pm-uid", "s-pm")
        t = self.transcript("s-pm", user("companions_install", "u-1"))
        self.fakes.set(exit={"projmux describe agent uid:pm-uid": 1})
        self.hook("pane-pm", "companions_install", session="s-pm", transcript=t)
        self.assertEqual((self.texts(), self.hook_errors()), ([], ["projmux_unavailable"]))

    def test_the_right_session_id_with_another_transcript_path_is_refused(self):
        self.fakes.claude_session("pm-uid", "s-pm", self.tmp / "elsewhere" / "s-pm.jsonl")
        t = self.transcript("s-pm", user("companions_install", "u-1"))
        self.hook("pane-pm", "companions_install", session="s-pm", transcript=t)
        self.assertEqual((self.texts(), self.hook_errors()), ([], ["session_not_pm"]))

    def test_neither_live_value_is_logged_or_stored(self):
        session = "s-live-7f3a9c"
        self.live_session("pm-uid", session)
        t = self.transcript(session, user("companions_install", "u-1"))
        self.hook("pane-pm", "companions_install", session=session, transcript=t)
        self.hook("pane-pm", "companions_install", session="fab-1", transcript=t)  # refused: only the code is kept
        self.assertEqual((self.texts(), self.hook_errors()), (["companions_install"], ["session_not_pm"]))
        mesh_files = "".join(p.read_text() for p in self.store.dir.glob("*") if p.is_file())
        for value in (session, str(t), "fab-1"):
            self.assertNotIn(value, mesh_files)


class D119DenyRuleTests(unittest.TestCase):
    RULE = "Bash(*agent-hook*ingest*)"

    def test_a_fresh_init_and_a_re_run_carry_the_ingest_deny_exactly_once(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            root.mkdir()
            (Path(tmp) / "home").mkdir()
            env = Fakes(Path(tmp) / "fakes").env({"PATH": "/usr/bin:/bin", "HOME": str(Path(tmp) / "home")})
            who = parse_caller("pm:pm-uid")
            ctx = tools.Context.for_project(root, env, clock=lambda: "t", caller=lambda: who)
            settings = root / ".claude" / "settings.json"
            for _ in range(2):  # fresh, then the additive idempotent re-run
                plan = tools.call(ctx, "mesh_init", {"stack": "none"}).data["plan_id"]
                self.assertTrue(tools.call(ctx, "mesh_init", {"stack": "none", "apply": True, "plan_id": plan}).ok)
                deny = json.loads(settings.read_text(encoding="utf-8"))["permissions"]["deny"]
                self.assertEqual(deny.count(self.RULE), 1, deny)
                for kept in ("Bash(*mesh* event record*)", "Bash(*tmux*send-keys*)", "Bash(*tmux*paste-buffer*)"):
                    self.assertEqual(deny.count(kept), 1, kept)
        self.assertIn(self.RULE, init.DENY_RULES)


if __name__ == "__main__":
    unittest.main()
