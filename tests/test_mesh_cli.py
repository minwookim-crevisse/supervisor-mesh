"""WO-0022 step 4 (W3): `mesh/cli.py`, the second front door. Its subcommands are derived from `tools.REGISTRY`, plus
`mesh event record` for the section-9a UserPromptSubmit hook. Run as a real child process (`python3 mesh/cli.py`, and
through the `.mesh/bin/mesh` launcher mesh_init writes); projmux on PATH is a fake."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _helpers import ROOT
from _mesh_fakes import Fakes, pin_pm, prompt_payload

sys.path.insert(0, str(ROOT))
from mesh import tools  # noqa: E402
from mesh.store import Store  # noqa: E402

CLI = ROOT / "mesh" / "cli.py"


class CliCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "proj"
        self.root.mkdir()
        (self.tmp / "home").mkdir()
        self.fakes = Fakes(self.tmp / "fakes")
        self.store = Store(self.root / ".mesh")

    def tearDown(self):
        self._tmp.cleanup()

    def env(self, caller="pm:pm-uid", **extra):
        base = {k: v for k, v in os.environ.items() if not k.startswith(("MESH_", "CLAUDE_", "PMX_"))}
        base.update(HOME=str(self.tmp / "home"), CLAUDE_PROJECT_DIR=str(self.root))
        base.update(self.fakes.caller_env(caller, self.root))  # the production way: a labelled pane (F1)
        return self.fakes.env(base, **extra)

    def run_cli(self, *argv, stdin="", caller="pm:pm-uid", program=None, **extra):
        cmd = [str(program)] if program else [sys.executable, str(CLI)]
        return subprocess.run(
            [*cmd, *argv],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=self.tmp,
            env=self.env(caller, **extra),
        )


class SubcommandTests(CliCase):
    def test_the_subcommands_are_the_registry_plus_event(self):
        out = self.run_cli("--help")
        self.assertEqual(out.returncode, 0, out.stderr)
        for name in [*tools.REGISTRY, "event"]:
            self.assertIn(name, out.stdout)

    def test_a_tool_call_prints_its_json_result_and_the_exit_code_says_ok_or_error(self):
        pin_pm(self.store)  # D109
        new = self.run_cli(
            "work_new",
            "--goal",
            "ship it",
            "--done-criteria",
            '["tests pass"]',
            "--role",
            "backend",
            "--type",
            "feature",
        )
        self.assertEqual(new.returncode, 0, new.stdout + new.stderr)
        self.assertEqual(json.loads(new.stdout)["item_id"], "WO-0001")
        listed = self.run_cli("work_list", "--role", "backend")
        self.assertEqual([i["id"] for i in json.loads(listed.stdout)["items"]], ["WO-0001"])
        shown = self.run_cli("work_show", "--json", '{"item_id": "WO-0042"}')
        self.assertEqual(shown.returncode, 1)
        self.assertEqual(json.loads(shown.stdout)["error"]["code"], "unknown_item")
        refused = self.run_cli(
            "work_new", "--goal", "x", "--done-criteria", "[]", "--role", "qa", "--type", "feature", caller="unknown"
        )
        self.assertEqual((refused.returncode, json.loads(refused.stdout)["error"]["code"]), (1, "refused"))

    def test_bad_json_for_a_structured_option_is_a_usage_error(self):
        self.store.create()
        out = self.run_cli("work_new", "--goal", "x", "--done-criteria", "not-json", "--role", "qa", "--type", "f")
        self.assertEqual(out.returncode, 2)
        self.assertEqual(out.stdout, "")


class EventRecordTests(CliCase):
    def hook(self, prompt="please run /config", caller="pm:pm-uid", program=None, **extra):
        if self.store.initialized() and self.store.read().doc.pm_uid is None:
            pin_pm(self.store)  # D109: mesh_init apply pins the PM
        payload = prompt_payload(self.tmp / "home", self.root, prompt)  # D110: a real user prompt
        self.fakes.claude_session("pm-uid", payload["session_id"], payload["transcript_path"])  # D118: projmux's record
        payload = json.dumps(payload)
        return self.run_cli(
            "event", "record", "--source", "terminal", stdin=payload, caller=caller, program=program, **extra
        )

    def test_the_pm_pane_records_one_operator_event_and_prints_nothing(self):
        self.store.create()
        out = self.hook()
        self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)
        events = self.store.operator_events()
        self.assertEqual([(e.id, e.text, e.agent_uid) for e in events], [("ev-000001", "please run /config", "pm-uid")])
        self.assertEqual(self.store.read().doc.last_operator_seq, 1)

    def test_a_lead_or_unlabelled_pane_records_nothing_and_exits_0(self):
        self.store.create()
        for caller in ("lead:backend:be-uid", "unknown"):
            out = self.hook(caller=caller)
            self.assertEqual((out.returncode, out.stdout), (0, ""), caller)
        self.assertEqual(self.store.operator_events(), [])
        self.assertEqual(self.store.read().doc.seq, 0)

    def test_production_identity_binds_the_hook_to_the_pm_session(self):
        self.store.create()
        self.fakes.agent("pm-uid", "pm", self.root, pane="pane-pm")
        self.fakes.agent("be-uid", "backend", self.root, pane="pane-be")
        self.assertEqual(self.hook(caller=None, PMX_INTERNAL_ACTIVATION_PANE_UID="pane-be").returncode, 0)
        self.assertEqual(self.store.operator_events(), [])
        self.assertEqual(self.hook(caller=None, PMX_INTERNAL_ACTIVATION_PANE_UID="pane-pm").returncode, 0)
        self.assertEqual([e.agent_uid for e in self.store.operator_events()], ["pm-uid"])

    def test_every_error_exits_0_and_never_blocks_the_prompt(self):
        out = self.hook()  # no .mesh/: nothing to record into, nothing created
        self.assertEqual((out.returncode, out.stdout), (0, ""))
        self.assertFalse((self.root / ".mesh").exists())
        self.store.create()
        for stdin in ("not json", "[]", json.dumps({"prompt": 7})):
            out = self.run_cli("event", "record", "--source", "terminal", stdin=stdin)
            self.assertEqual((out.returncode, out.stdout), (0, ""), stdin)
        self.assertEqual(self.store.operator_events(), [])
        errors = [json.loads(x) for x in self.store.events_path.read_text().splitlines()]
        self.assertEqual({e["type"] for e in errors}, {"hook_error"})
        self.assertNotIn(str(self.tmp), self.store.events_path.read_text())

    def test_the_launcher_mesh_init_writes_runs_the_hook(self):
        env = self.env()
        ctx = tools.Context.for_project(self.root, env)
        plan = tools.call(ctx, "mesh_init", {"stack": "none"}).data["plan_id"]
        self.assertTrue(tools.call(ctx, "mesh_init", {"stack": "none", "apply": True, "plan_id": plan}).ok)
        settings = json.loads((self.root / ".claude" / "settings.json").read_text())
        command = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        payload = prompt_payload(self.tmp / "home", self.root, "install companions_install please")
        self.fakes.claude_session("pm-uid", payload["session_id"], payload["transcript_path"])  # D118
        payload = json.dumps(payload)
        out = subprocess.run(
            ["sh", "-c", command], input=payload, capture_output=True, text=True, timeout=30, cwd=self.root, env=env
        )
        self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)
        self.assertEqual([e.text for e in self.store.operator_events()], ["install companions_install please"])


if __name__ == "__main__":
    unittest.main()
