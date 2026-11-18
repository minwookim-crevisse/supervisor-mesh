"""Acceptance 1 (core part): the stdio MCP server over a REAL pipe, and the tool registry behind it.

Caller: the server resolves its caller from the calling projmux pane (D82), UNKNOWN when that fails (read tools work,
every verb is refused and recorded). No environment variable overrides it (F1): a test makes the server's caller "pm",
"unknown" or "lead:<role>[:<agent uid>]" the production way, with a labelled agent in the FAKE projmux on PATH and its
pane uid in PMX_INTERNAL_ACTIVATION_PANE_UID (`Fakes.caller_env`). tmux and claude on PATH are fakes too.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from _helpers import ROOT
from _mesh_fakes import Fakes, pin_pm
from test_mesh_state import contract, doc_of, item_in

sys.path.insert(0, str(ROOT))
from mesh import mcp_server, state, tools  # noqa: E402
from mesh.model import Caller, NewArgs, OverrideArgs, Phase, json_schema  # noqa: E402
from mesh.store import Store  # noqa: E402

SERVER = ROOT / "mesh" / "mcp_server.py"
SPEC_TOOLS = [
    "mesh_init",
    "mesh_status",
    "mesh_metrics",
    "work_list",
    "work_show",
    "work_new",
    "work_dispatch",
    "work_transition",
    "tracker_render",
    "pane_command",
    "companions_install",
]
FILLED_BY_W3 = ["mesh_init", "tracker_render", "companions_install"]


class Server:
    """A real `python3 mesh/mcp_server.py` child talking newline-delimited JSON-RPC over its stdio."""

    def __init__(
        self,
        project: Path,
        caller: str | None = "pm:pm-uid",  # the pinned PM (D109)
        cwd: Path | None = None,
        fakes: Fakes | None = None,
        extra_env: dict | None = None,
    ):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MESH_", "CLAUDE_", "PMX_"))}
        if fakes is not None:  # projmux / tmux / claude on PATH are fakes: a test never reaches the real tools
            env = fakes.env(env, HOME=str(fakes.dir / "home"))
        env["CLAUDE_PROJECT_DIR"] = str(project)
        if fakes is not None:
            env.update(fakes.caller_env(caller, project))
        env.update(extra_env or {})
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd or project,
            env=env,
            text=True,
            encoding="utf-8",
        )
        self.next_id, self.raw_lines = 1, []

    def send(self, obj) -> None:
        self.proc.stdin.write((obj if isinstance(obj, str) else json.dumps(obj)) + "\n")
        self.proc.stdin.flush()

    def read(self) -> dict:
        line = self.proc.stdout.readline()
        self.raw_lines.append(line)
        return json.loads(line)

    def rpc(self, method: str, params=None, **extra):
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method, **extra}
        if params is not None:
            msg["params"] = params
        self.next_id += 1
        self.send(msg)
        reply = self.read()
        assert reply["id"] == msg["id"], reply
        return reply

    def call(self, name: str, arguments: dict | None = None) -> dict:
        reply = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return reply["result"]

    def close(self) -> str:
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        rest = self.proc.stdout.read()
        self.raw_lines.extend(rest.splitlines(keepends=True))
        err = self.proc.stderr.read()
        self.proc.stdout.close()
        self.proc.stderr.close()
        return err


class McpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.store = Store(self.project / ".mesh")
        pin_pm(self.store)  # D109: as mesh_init apply leaves it
        self.servers = []
        self.fakes = Fakes(Path(self.tmp.name) / ".fakes")

    def tearDown(self):
        for s in self.servers:
            if s.proc.poll() is None:
                s.proc.kill()
                s.proc.wait()
            for stream in (s.proc.stdin, s.proc.stdout, s.proc.stderr):
                stream.close()
        self.tmp.cleanup()

    def start(self, **kw) -> Server:
        kw.setdefault("fakes", self.fakes)
        s = Server(self.project, **kw)
        self.servers.append(s)
        return s

    def seed(self, doc):
        with self.store.locked() as txn:
            txn.commit(doc)


class HandshakeTests(McpCase):
    def test_initialize_then_tools_list_exposes_all_eleven_tools_with_derived_schemas(self):
        s = self.start()
        reply = s.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
        result = reply["result"]
        self.assertEqual(
            (reply["jsonrpc"], result["protocolVersion"], result["serverInfo"]["name"]),
            ("2.0", "2025-06-18", "supervisor-mesh"),
        )
        self.assertIn("tools", result["capabilities"])
        s.send({"jsonrpc": "2.0", "method": "notifications/initialized"})  # a notification: no reply
        listed = s.rpc("tools/list")["result"]["tools"]
        self.assertEqual(sorted(t["name"] for t in listed), sorted(SPEC_TOOLS))
        by_name = {t["name"]: t for t in listed}
        self.assertEqual(by_name["work_new"]["inputSchema"], json_schema(NewArgs))
        verbs = by_name["work_transition"]["inputSchema"]["properties"]["verb"]["enum"]
        self.assertEqual(verbs, list(state.surface_verbs("work_transition")))
        self.assertEqual(len(verbs), 15)
        for t in listed:
            self.assertEqual(t["inputSchema"]["type"], "object", t["name"])
        s.close()

    def test_an_older_client_protocol_is_echoed_and_an_unknown_one_gets_the_newest(self):
        s = self.start()
        self.assertEqual(
            s.rpc("initialize", {"protocolVersion": "2024-11-05"})["result"]["protocolVersion"], "2024-11-05"
        )
        self.assertEqual(
            s.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"],
            mcp_server.PROTOCOL_VERSIONS[0],
        )
        s.close()

    def test_ping_unknown_methods_and_bad_params_get_the_jsonrpc_errors(self):
        s = self.start()
        self.assertEqual(s.rpc("ping")["result"], {})
        self.assertEqual(s.rpc("no/such/method")["error"]["code"], -32601)
        for params in (
            None,
            [],
            {"arguments": {}},
            {"name": 7},
            {"name": "work_list", "arguments": []},
            {"name": "no_such_tool"},
        ):
            self.assertEqual(s.rpc("tools/call", params)["error"]["code"], -32602, params)
        s.send("this is not json")
        self.assertEqual(s.read()["error"]["code"], -32700)
        s.send("[1, 2]")
        self.assertEqual(s.read()["error"]["code"], -32600)
        s.send({"jsonrpc": "2.0", "method": "no/such/notification"})  # no reply to a notification
        self.assertEqual(s.rpc("ping")["result"], {})  # and the stream is still in step
        s.close()


class ToolCallTests(McpCase):
    def test_new_then_ready_then_list_and_show_over_the_pipe(self):
        s = self.start()
        made = s.call(
            "work_new", {"goal": "ship it", "done_criteria": ["tests pass"], "role": "backend", "type": "feature"}
        )
        self.assertFalse(made["isError"], made)
        data = made["structuredContent"]
        self.assertEqual(
            (data["ok"], data["item"]["id"], data["item"]["phase"], data["row"]), (True, "WO-0001", "draft", "new")
        )
        self.assertEqual(json.loads(made["content"][0]["text"]), data)  # text and structured content agree

        moved = s.call("work_transition", {"item_id": "WO-0001", "verb": "ready"})["structuredContent"]
        self.assertEqual((moved["from"], moved["to"], moved["item"]["contract"]["version"]), ("draft", "ready", 1))

        listed = s.call("work_list", {"phase": "ready"})["structuredContent"]["items"]
        self.assertEqual([(i["id"], i["phase"], i["role"]) for i in listed], [("WO-0001", "ready", "backend")])
        self.assertEqual(s.call("work_list", {"phase": "closed"})["structuredContent"]["items"], [])

        shown = s.call("work_show", {"item_id": "WO-0001"})["structuredContent"]["item"]
        self.assertEqual(
            (shown["phase"], shown["state"], shown["blocked_reason"], shown["version"]), ("ready", "ready", None, 2)
        )
        self.assertEqual(shown["contract"]["goal"], "ship it")
        s.close()

    def test_a_refused_verb_is_an_error_result_that_is_recorded_and_changes_nothing(self):
        self.seed(doc_of(item_in(Phase.DRAFT)))
        before = (self.project / ".mesh" / "state.json").read_bytes()
        s = self.start()
        result = s.call("work_transition", {"item_id": "WO-0001", "verb": "close"})
        self.assertTrue(result["isError"])
        err = result["structuredContent"]["error"]
        self.assertEqual((err["code"], err["reason"]), ("refused", "not_in_table"))
        self.assertEqual((self.project / ".mesh" / "state.json").read_bytes(), before)
        self.assertEqual([r["code"] for r in self.store.refusals()], ["not_in_table"])
        s.close()

    def test_bad_tool_arguments_are_an_error_result_not_a_protocol_error(self):
        s = self.start()
        cases = [
            ("work_new", {"goal": "x"}),
            ("work_new", {"goal": "x", "done_criteria": [], "role": "Bad Role!", "type": "t"}),
            ("work_transition", {"item_id": "WO-0001", "verb": "dispatch"}),
            ("work_transition", {"item_id": "not-an-id", "verb": "ready"}),
            ("work_transition", {"item_id": "WO-0001", "verb": "submit", "args": {"attempt_id": "one"}}),
            ("work_show", {"item_id": "WO-0042"}),
        ]
        for name, arguments in cases:
            result = s.call(name, arguments)
            self.assertTrue(result["isError"], (name, arguments))
            self.assertIn(
                result["structuredContent"]["error"]["code"], ("invalid_args", "unknown_item"), (name, arguments)
            )
        s.close()

    def test_the_w3_tools_are_implemented_over_the_pipe(self):
        s = self.start()
        init = s.call("mesh_init", {"stack": "none"})
        self.assertFalse(init["isError"], init)
        self.assertFalse(init["structuredContent"]["applied"])
        render = s.call("tracker_render", {"dry_run": True})
        self.assertFalse(render["isError"], render)
        install = s.call("companions_install", {"event_id": "ev-000001"})  # no such event: refused, not unimplemented
        self.assertEqual(install["structuredContent"]["error"]["reason"], "event_unknown")
        for name in FILLED_BY_W3:
            self.assertNotIn("not implemented", json.dumps(s.call(name, {})), name)
        s.close()

    def test_an_uninitialised_project_says_to_run_mesh_init(self):
        with tempfile.TemporaryDirectory() as bare:
            s = Server(Path(bare), fakes=Fakes(Path(bare) / ".fakes"))
            try:
                result = s.call("work_list")
                self.assertEqual(result["structuredContent"]["error"]["code"], "not_initialized")
            finally:
                s.close()

    def test_the_server_runs_from_any_working_directory_via_the_project_dir_variable(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            s = self.start(cwd=Path(elsewhere))
            self.assertFalse(s.call("work_list")["isError"])
            s.close()


class IdentityTests(McpCase):
    def test_an_unknown_caller_reads_but_every_verb_is_refused_and_recorded(self):
        self.seed(doc_of(item_in(Phase.DRAFT)))
        for kw in (
            dict(caller=None),  # not inside a managed pane
            dict(caller=None, extra_env={"MESH_TEST_MODE": "1", "MESH_TEST_CALLER": "pm"}),  # no env override (F1)
            dict(caller=None, extra_env={"PMX_INTERNAL_ACTIVATION_PANE_UID": "pane-nobody"}),  # fails closed
            dict(caller="garbage"),
            dict(caller="unknown"),
        ):
            s = self.start(**kw)
            self.assertFalse(s.call("work_list")["isError"], kw)
            self.assertFalse(s.call("work_show", {"item_id": "WO-0001"})["isError"], kw)
            result = s.call("work_new", {"goal": "g", "done_criteria": ["c"], "role": "backend", "type": "feature"})
            self.assertEqual(result["structuredContent"]["error"]["reason"], "unknown_caller", kw)
            s.close()
        self.assertEqual({r["code"] for r in self.store.refusals()}, {"unknown_caller"})
        self.assertEqual(len(self.store.refusals()), 5)
        self.assertEqual(len(self.store.read().doc.items), 1)

    def test_a_lead_of_another_role_cannot_start_and_a_lead_cannot_create_items(self):
        self.seed(doc_of(item_in(Phase.READY, contract=contract(version=1))))
        s = self.start(caller="lead:frontend:fe-uid")
        result = s.call("work_transition", {"item_id": "WO-0001", "verb": "start"})
        self.assertEqual(result["structuredContent"]["error"]["reason"], "not_in_table")
        made = s.call("work_new", {"goal": "g", "done_criteria": ["c"], "role": "frontend", "type": "feature"})
        self.assertEqual(made["structuredContent"]["error"]["reason"], "not_in_table")
        s.close()
        self.assertEqual([r["actor"] for r in self.store.refusals()], ["lead (other role)", "lead (other role)"])

    def test_the_own_role_lead_reaches_the_guards_and_the_projmux_facts(self):
        self.seed(doc_of(item_in(Phase.READY, contract=contract(version=1))))
        s = self.start(caller="lead:backend:be-uid")
        error = s.call("work_transition", {"item_id": "WO-0001", "verb": "start"})["structuredContent"]["error"]
        self.assertEqual(error["reason"], "fact_unavailable")  # live (it is the caller); HEAD needs git: none here
        s.close()

    def test_in_production_the_caller_is_resolved_from_the_calling_pane(self):
        self.fakes.agent("pm-uid", "pm", self.project, pane="pane-pm")
        self.fakes.agent("be-uid", "backend", self.project, pane="pane-be")
        new = {"goal": "g", "done_criteria": ["c"], "role": "backend", "type": "feature"}
        pm = self.start(caller=None, extra_env={"PMX_INTERNAL_ACTIVATION_PANE_UID": "pane-pm"})
        self.assertFalse(pm.call("work_new", new)["isError"])
        pm.close()
        lead = self.start(caller=None, extra_env={"PMX_INTERNAL_ACTIVATION_PANE_UID": "pane-be"})
        self.assertEqual(lead.call("work_new", new)["structuredContent"]["error"]["reason"], "not_in_table")
        lead.close()
        nobody = self.start(caller=None)
        self.assertEqual(nobody.call("work_new", new)["structuredContent"]["error"]["reason"], "unknown_caller")
        nobody.close()
        self.assertTrue(self.fakes.calls("projmux", "get", "panes"))


class CallerFromEnvTests(unittest.TestCase):
    def test_no_environment_variable_makes_a_caller(self):
        for env in ({}, {"MESH_TEST_CALLER": "pm", "MESH_TEST_MODE": "1"}, {"PMX_INTERNAL_ACTIVATION_PANE_UID": "p"}):
            self.assertEqual(tools.caller_from_env(env)(), Caller.unknown(), env)  # no project root: unknown


class StdoutDisciplineTests(McpCase):
    def test_stdout_carries_json_rpc_only_even_when_something_prints_or_writes_to_fd_1(self):
        code = textwrap.dedent(f"""
            import os, sys
            sys.path.insert(0, {str(ROOT)!r})
            from mesh import mcp_server
            out = mcp_server.claim_stdout()
            print("a stray print")
            os.write(1, b"a child that inherited fd 1\\n")
            sys.stdout.write("another stray\\n")
            out.write('{{"jsonrpc":"2.0","id":1,"result":{{}}}}\\n')
            out.flush()
        """)
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=20)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, '{"jsonrpc":"2.0","id":1,"result":{}}\n')
        for stray in ("a stray print", "a child that inherited fd 1", "another stray"):
            self.assertIn(stray, done.stderr)

    def test_every_line_the_server_wrote_parses_as_json_rpc(self):
        s = self.start()
        s.rpc("initialize", {"protocolVersion": "2025-06-18"})
        s.call("work_new", {"goal": "g", "done_criteria": ["c"], "role": "backend", "type": "feature"})
        s.call("work_list")
        s.send("garbage")
        s.read()
        stderr = s.close()
        self.assertTrue(s.raw_lines)
        for line in s.raw_lines:
            msg = json.loads(line)
            self.assertEqual(msg["jsonrpc"], "2.0", line)
            self.assertTrue("result" in msg or "error" in msg, line)
        self.assertNotIn("Traceback", stderr)

    def test_run_as_a_script_the_plugin_root_replaces_the_scripts_own_directory_on_the_import_path(self):
        code = textwrap.dedent(f"""
            import runpy, sys
            sys.stdin = open("/dev/null")                     # EOF at once: main() returns
            sys.path[0] = {str(ROOT / "mesh")!r}              # what `python3 mesh/mcp_server.py` starts with
            try:
                runpy.run_path({str(SERVER)!r}, run_name="__main__")
            except SystemExit:
                pass
            sys.stderr.write("PATH0=" + sys.path[0] + "\\n")
        """)
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
        done = subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=20, env=env, cwd=self.project
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"PATH0={ROOT}", done.stderr.splitlines())


class RecoveryOnStartTests(McpCase):
    def test_on_start_an_attempting_keypress_becomes_unknown_and_an_unsent_dispatch_is_replayed(self):
        from dataclasses import replace

        from mesh import dispatch
        from mesh.model import ActionKind, EventUse, PendingAction

        item = item_in(Phase.READY, contract=contract(version=1))
        sha = dispatch.text_sha256(dispatch.compose(item, self.project, "mesh-WO-0001-d1"))
        send = PendingAction(
            "WO-0001-d1",
            ActionKind.DISPATCH_SEND,
            {
                "role": "backend",
                "target_uid": "be-uid",
                "source_uid": "pm-uid",
                "text_sha256": sha,
                "message_ref": "mesh-WO-0001-d1",
            },
        )
        use = EventUse("ev-000001", "pane_command", "be-uid", 1, "attempting")
        self.seed(replace(doc_of(replace(item, pending_actions=(send,))), event_uses={"ev-000001": use}))
        self.fakes.agent("be-uid", "backend", self.project)
        s = self.start()
        s.rpc("initialize", {"protocolVersion": "2025-06-18"})
        s.close()
        doc = self.store.read().doc
        self.assertEqual(doc.event_uses["ev-000001"].status, "unknown")
        self.assertEqual(doc.items["WO-0001"].pending_actions[0].done, True)
        (msg,) = self.fakes.load()["messages"]
        self.assertEqual(msg["args"][msg["args"].index("--message-ref") + 1], "mesh-WO-0001-d1")


class WiredToolsOverStdioTests(McpCase):
    """W1's three tools over the real pipe, against the fakes."""

    def test_status_dispatch_and_pane_command_answer_over_stdio(self):
        self.seed(doc_of(item_in(Phase.READY, contract=contract(version=1))))
        self.fakes.agent("pm-uid", "pm", self.project, pane="pane-pm")
        self.fakes.agent("be-uid", "backend", self.project, pane="pane-be", tmux_id="%1")
        s = self.start(caller="pm:pm-uid", extra_env={"PMX_INTERNAL_ACTIVATION_PANE_UID": "pane-pm"})
        status = s.call("mesh_status")["structuredContent"]
        self.assertEqual(status["leads"], [{"role": "backend", "present": True}])
        sent = s.call("work_dispatch", {"item_id": "WO-0001"})["structuredContent"]
        self.assertEqual(sent["dispatch"]["sent"], True, sent)
        ev = self.store.record_operator_event("run /compact", "2026-11-18T00:00:00Z").id
        typed = s.call("pane_command", {"role": "backend", "command": "/compact", "event_id": ev})
        self.assertFalse(typed["isError"], typed)
        self.assertEqual(typed["structuredContent"]["receipt"]["outcome"], "done")
        stderr = s.close()
        self.assertNotIn("Traceback", stderr)


class RegistryTests(unittest.TestCase):
    def test_the_registry_is_the_single_list_of_eleven_tools_and_work_transition_is_table_derived(self):
        self.assertEqual(sorted(tools.REGISTRY), sorted(SPEC_TOOLS))
        schema = tools.REGISTRY["work_transition"].input_schema()
        self.assertEqual(schema["properties"]["verb"]["enum"], list(state.surface_verbs("work_transition")))
        self.assertEqual(set(schema["required"]), {"item_id", "verb"})
        for verb in state.surface_verbs("work_transition"):
            self.assertIn(verb, schema["properties"]["args"]["description"])
        self.assertEqual(
            {n for n, t in tools.REGISTRY.items() if t.read_only},
            {"work_list", "work_show", "mesh_status", "mesh_metrics"},
        )

    def test_override_targets_in_the_schema_exclude_blocked_limit(self):
        enum = json_schema(OverrideArgs)["properties"]["to"]["enum"]
        self.assertNotIn("blocked(limit)", enum)
        self.assertEqual(len(enum), len(Phase) - 1)


if __name__ == "__main__":
    unittest.main()
