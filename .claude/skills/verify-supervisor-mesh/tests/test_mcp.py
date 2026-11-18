"""The MCP journey helpers (features/mcp.md) against stubs and synthetic inputs: state-check, mesh-config, mcp-check,
mcp-calls, mcp-env, json-shape, msg-probe and launch --pm-role. Every test that feeds a planted marker also proves
the marker never reaches the evidence dir. The /mcp screens and the MCP transcript records are SYNTHETIC (no live
capture exists yet); replace them with redacted live captures after the first live run (SKILL.md, Known gaps)."""
import json
import os
import subprocess
import unittest
from pathlib import Path

from harness import MESH_TOOLS, RULE, Env, ready_screen

PREFIX = "mcp__plugin_supervisor-mesh_supervisor-mesh__"


def out(r) -> str:
    return r.stdout + r.stderr


def state_doc(marker: str, phase: str = "verified") -> dict:
    run = {"argv": ["test", "-f", marker], "kind": "test", "status": "pass", "exit_code": 0,
           "started_at": "t", "ended_at": "t"}
    hist = [("new", "pm", None, "draft"), ("ready", "pm", "draft", "ready"), ("dispatch", "pm", "ready", "ready"),
            ("start", "lead_own", "ready", "running"), ("submit", "lead_own", "running", "verifying"),
            ("verify", "lead_own", "verifying", "verified")]
    return {"schema_version": 1, "seq": 9, "next_id": 2, "last_operator_seq": 3, "legacy": {}, "event_uses": {},
            "proposals": {}, "items": {"WO-0001": {
                "id": "WO-0001", "phase": phase, "state": phase, "blocked_reason": None, "version": 7,
                "contract": {"version": 1, "goal": f"goal {marker}", "done_criteria": [marker], "role": "qa",
                             "type": "docs", "depends": [], "scope": {"report": marker}},
                "attempts": [{"id": 1, "lead_uid": "agent-x", "base_commit": "a" * 40, "commit": "b" * 40,
                              "contract_version": 1, "config_hash": "c" * 64,
                              "evidence": {"kind": "verification", "commit": "b" * 40, "contract_version": 1,
                                           "config_hash": "c" * 64, "runs": [run], "recorded_seq": 8}}],
                "pending_actions": [{"action_id": "A1", "kind": "dispatch", "done": True,
                                     "payload": {"message_ref": "mesh-WO-0001-1", "attempts": "delivered",
                                                 "target_uid": "agent-x"}}],
                "history": [{"at": "t", "verb": v, "row": v, "actor": a, "actor_uid": None, "from_phase": f,
                             "to_phase": t, "note": f"note {marker}"} for v, a, f, t in hist]}}}


def write_mesh(e: Env, marker: str, phase: str = "verified", operator: int = 2) -> None:
    mesh = e.proj / ".mesh"
    mesh.mkdir(exist_ok=True)
    (mesh / "state.json").write_text(json.dumps(state_doc(marker, phase)))
    rows = [{"type": "operator", "id": f"E-{n}", "seq": n, "at": "t", "text": f"prompt {marker}",
             "agent_uid": e.run_json()["pm"]} for n in range(1, operator + 1)]
    rows.append({"type": "receipt", "id": "R-1", "tool": "pane_command", "status": "done", "command": marker})
    (mesh / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (mesh / "refusals.jsonl").write_text(json.dumps({"subject": "work_new", "code": "caller_not_pm",
                                                     "detail": marker, "caller_kind": "unknown"}) + "\n")


def mcp_transcript(path: Path, marker: str, tools=MESH_TOOLS, drop_result_for: str = "") -> None:
    recs = []
    for n, tool in enumerate(tools):
        tid = f"toolu_{n:03d}"
        args = {"item_id": "WO-0001", "goal": marker} if tool != "mesh_init" else {"apply": True, "plan_id": "p1"}
        recs.append({"type": "assistant", "timestamp": f"2099-01-01T00:00:{n:02d}.000Z", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": PREFIX + tool, "input": args}]}})
        if tool == drop_result_for:
            continue
        body = {"ok": True, "item_id": "WO-0001", "to": "ready", "item": {
            "contract": {"goal": marker}, "attempts": [{"id": 1, "commit": "b" * 40, "evidence": {
                "runs": [{"kind": "test", "status": "pass", "exit_code": 0}]}}]}, "note": marker}
        recs.append({"type": "user", "timestamp": f"2099-01-01T00:00:{n:02d}.500Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": json.dumps(body)}]}]}})
    # a refused call, and a result whose tool_use_id names no call (never credited to anything)
    recs.append({"type": "assistant", "timestamp": "2099-01-01T00:01:00.000Z", "message": {"content": [
        {"type": "tool_use", "id": "toolu_refused", "name": PREFIX + "work_transition", "input": {"verb": "close"}}]}})
    recs.append({"type": "user", "timestamp": "2099-01-01T00:01:01.000Z", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_refused", "is_error": True, "content": [{"type": "text", "text":
         json.dumps({"ok": False, "error": {"code": "refused", "reason": "caller_not_pm", "message": marker}})}]}]}})
    recs.append({"type": "user", "timestamp": "2099-01-01T00:01:02.000Z", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_orphan", "content": marker}]}})
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def assert_no_marker(t: unittest.TestCase, e: Env, marker: str) -> None:
    hits = subprocess.run(["grep", "-rlF", marker, str(e.run_root / "evidence")], capture_output=True, text=True)
    t.assertEqual(hits.stdout, "", f"the marker reached the evidence: {hits.stdout}")


class StateCheck(unittest.TestCase):
    def test_extract_keeps_fields_and_never_text(self):
        e = Env(self.addCleanup)
        e.make_run()
        marker = e.marker["TOOLRESULT"]
        write_mesh(e, marker)
        r = e.smv("state-check", "--label", "closed", "--item", "WO-0001", "--expect-phase", "verified",
                  "--expect-verbs", "new,ready,dispatch,start,submit,verify", "--expect-evidence-pass",
                  "--expect-operator-from-pm")
        self.assertEqual(r.returncode, 0, out(r))
        ext = json.loads((e.run_root / "evidence/state/closed.json").read_text())
        self.assertEqual(ext["items"]["WO-0001"]["attempts"][0]["evidence"]["runs"], ["test:pass:0"])
        self.assertEqual(ext["events"], {"operator": 2, "receipt": 1})
        assert_no_marker(self, e, marker)

    def test_wrong_phase_order_or_failed_run_is_red(self):
        e = Env(self.addCleanup)
        e.make_run()
        write_mesh(e, "m", phase="running")
        for extra in (["--expect-phase", "verified"], ["--expect-verbs", "verify,start"]):
            r = e.smv("state-check", "--label", "x", "--item", "WO-0001", *extra)
            self.assertEqual(r.returncode, 1, out(r))
        doc = state_doc("m")
        doc["items"]["WO-0001"]["attempts"][0]["evidence"]["runs"][0]["status"] = "fail"
        (e.proj / ".mesh/state.json").write_text(json.dumps(doc))
        r = e.smv("state-check", "--label", "y", "--item", "WO-0001", "--expect-evidence-pass")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertEqual(e.smv("state-check", "--label", "z", "--item", "WO-0002").returncode, 1)

    def test_operator_delta_against_a_baseline(self):
        e = Env(self.addCleanup)
        e.make_run()
        write_mesh(e, "m", operator=2)
        self.assertEqual(e.smv("state-check", "--label", "before").returncode, 0)
        write_mesh(e, "m", operator=3)
        r = e.smv("state-check", "--label", "after", "--baseline", "before", "--expect-operator-delta", "0")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn("+1 operator", out(r))
        r = e.smv("state-check", "--label", "after2", "--baseline", "before", "--expect-operator-delta", "1")
        self.assertEqual(r.returncode, 0, out(r))


class MeshConfig(unittest.TestCase):
    def test_adds_one_verify_command_once(self):
        e = Env(self.addCleanup)
        e.make_run()
        self.assertEqual(e.smv("mesh-config", "--verify-file", "docs/probe.md").returncode, 2, "no config yet")
        (e.proj / ".mesh").mkdir()
        (e.proj / ".mesh/config.toml").write_text('operator_channel = "terminal"\n\n[verify]\n# prepare = []\n')
        for bad in ("../x", "/abs", ".git/x", "~/x"):
            self.assertEqual(e.smv("mesh-config", "--verify-file", bad).returncode, 2, bad)
        r = e.smv("mesh-config", "--verify-file", "docs/probe.md")
        self.assertEqual(r.returncode, 0, out(r))
        import tomllib
        cmds = tomllib.loads((e.proj / ".mesh/config.toml").read_text())["verify"]["commands"]
        self.assertEqual(cmds, [{"argv": ["test", "-f", "docs/probe.md"], "kind": "test", "timeout_s": 60}])
        self.assertEqual(e.smv("mesh-config", "--verify-file", "docs/other.md").returncode, 2, "only once")


MCP_OK = ("Manage MCP servers", "", "  ❯ plugin:supervisor-mesh:supervisor-mesh · ✔ connected", "",
          "  Enter to confirm · Esc to cancel")


class McpCheck(unittest.TestCase):
    def check(self, *lines: str) -> subprocess.CompletedProcess:
        e = Env(self.addCleanup)
        e.make_run(screen="\n".join([RULE, *lines]))
        return e.smv("mcp-check")

    def test_connected_server_passes(self):
        r = self.check(*MCP_OK)
        self.assertEqual(r.returncode, 0, out(r))

    def test_failed_missing_or_closed_dialog_is_red(self):
        for lines in ([x.replace("✔ connected", "✘ failed") for x in MCP_OK],
                      [x.replace("supervisor-mesh:supervisor-mesh", "other:other") for x in MCP_OK]):
            self.assertEqual(self.check(*lines).returncode, 1)
        e = Env(self.addCleanup)
        e.make_run(screen=ready_screen("plugin:supervisor-mesh:supervisor-mesh · ✔ connected"))
        self.assertEqual(e.smv("mcp-check").returncode, 1, "a ready prompt is not the /mcp dialog")


class McpCalls(unittest.TestCase):
    def run_with(self, **kw) -> tuple[Env, str]:
        e = Env(self.addCleanup)
        t = e.base / "pm.jsonl"
        marker = e.marker["TOOLRESULT"]
        mcp_transcript(t, marker, **kw)
        e.make_run(pm_transcript=t)
        return e, marker

    def test_every_tool_answered_with_one_prefix(self):
        e, marker = self.run_with()
        r = e.smv("mcp-calls", "--all-tools", "--prefix", PREFIX, "--expect", "mesh_init", "--expect",
                  "work_transition=error", "--expect", "work_dispatch=any")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertIn("attempt_id=1 attempt_commit=bbbbbbbbbbbb evidence=test:pass:0", out(r))
        self.assertIn("error.reason=caller_not_pm", out(r))
        assert_no_marker(self, e, marker)

    def test_a_tool_without_a_result_or_a_wrong_prefix_is_red(self):
        e, _ = self.run_with(drop_result_for="companions_install")
        r = e.smv("mcp-calls", "--all-tools")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn("missing ['companions_install']", out(r))
        r = e.smv("mcp-calls", "--prefix", "mcp__supervisor-mesh__")
        self.assertEqual(r.returncode, 1, out(r))
        r = e.smv("mcp-calls", "--expect", "work_transition=ok", "--expect", "pane_command=error")
        self.assertEqual(r.returncode, 1, "the only work_transition error is not an ok, and no pane_command error")


def fake_proc(root: Path, procs: dict[int, tuple[int, list[str], dict[str, str], Path]]) -> None:
    for pid, (ppid, argv, env, cwd) in procs.items():
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} ({Path(argv[0]).name}) S {ppid} 1 1")
        (d / "cmdline").write_bytes(b"\0".join(x.encode() for x in argv) + b"\0")
        (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0")
        os.symlink(cwd, d / "cwd")


class McpEnv(unittest.TestCase):
    def run_case(self, case: str) -> subprocess.CompletedProcess:
        """pane pid 100 (projmux supervise) -> 101 claude -> 102 the installed mesh/mcp_server.py. `case`: equal
        (the server's variable equals claude's and the pane uid), absent (the server has none), differs."""
        e = Env(self.addCleanup)
        pm = e.make_run()
        pane_uid = pm["status"]["paneRef"]
        e.pane()["pid"] = 100
        cache = e.base / "cache"
        e.state["plugin_rows"] = [{"id": e.plugin_id, "scope": "project", "projectPath": str(e.proj),
                                   "installPath": str(cache)}]
        e.save()
        e.smv("status")  # records the pane uid in run.json
        secret = e.marker["CONFIG"]
        var = "PMX_INTERNAL_ACTIVATION_PANE_UID"
        claude = pane_uid + ("x" if case == "differs" else "")
        server = {} if case == "absent" else {var: pane_uid}
        fake_proc(e.base / "proc", {
            100: (1, ["projmux", "internal", "supervise"], {}, e.proj),
            101: (100, ["/usr/bin/claude"], {var: claude, "SMV_SECRET": secret}, e.proj),
            102: (101, ["python3", str(cache / "mesh/mcp_server.py")], {"SMV_SECRET": secret, **server}, e.proj)})
        r = e.smv("mcp-env", "--proc-root", str(e.base / "proc"))
        self.assertNotIn(pane_uid, out(r), "the variable's value is never printed")
        assert_no_marker(self, e, secret)
        return r

    def test_identity_present_and_equal(self):
        r = self.run_case("equal")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertIn("script location (info): installed copy", out(r))

    def test_absent_or_different_identity_is_red(self):
        for case in ("absent", "differs"):
            self.assertEqual(self.run_case(case).returncode, 1, case)


class JsonShape(unittest.TestCase):
    def test_key_paths_only(self):
        e = Env(self.addCleanup)
        e.make_run()
        marker = e.marker["CONFIG"]
        e.state["pane_items"] = [{"metadata": {"uid": marker}, "status": {"activation": {
            "agentUID": marker, "runtimeID": "%1", "claude": {"process": {"pid": 7}}}}}]
        e.save()
        r = e.smv("json-shape", "--label", "panes", "--expect", "items[].status.activation.claude.process.pid",
                  "--expect", "items[].metadata.uid", "--", "projmux", "get", "panes", "-A", "-o", "json")
        self.assertEqual(r.returncode, 0, out(r))
        r = e.smv("json-shape", "--label", "panes2", "--expect", "items[].status.activation.agentUid", "--",
                  "projmux", "get", "panes", "-A", "-o", "json")
        self.assertEqual(r.returncode, 1, out(r))
        assert_no_marker(self, e, marker)

    def test_preset_panes(self):
        e = Env(self.addCleanup)
        e.make_run()
        e.state["pane_items"] = [{"metadata": {"uid": "u"}, "status": {"activation": {
            "agentUID": "a", "runtimeID": "%1", "claude": {"process": {"pid": 7}}}}}]
        e.save()
        self.assertEqual(e.smv("json-shape", "--preset", "panes").returncode, 0)
        e.state["pane_items"][0]["status"]["activation"].pop("claude")
        e.save()
        r = e.smv("json-shape", "--preset", "panes")
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn("items[].status.activation.claude.process.pid", out(r))

    def test_only_listed_read_only_commands(self):
        e = Env(self.addCleanup)
        e.make_run()
        for argv in (["projmux", "delete", "agent", "x", "-o", "json"], ["claude", "plugin", "list"],
                     ["cat", "x.json"]):
            r = e.smv("json-shape", "--label", "x", "--", *argv)
            self.assertEqual(r.returncode, 2, " ".join(argv) + out(r))


class MsgProbe(unittest.TestCase):
    def probe(self, dedup: bool) -> subprocess.CompletedProcess:
        e = Env(self.addCleanup)
        t = e.base / "pm.jsonl"
        t.write_text("")
        pm = e.make_run(pm_transcript=t)
        lead = e.agent("qa", labels={"role": "qa"}, creator=pm["metadata"]["uid"])
        e.state["agents"].append(lead)
        e.state["dedup"] = dedup
        e.save()
        self.assertEqual(e.smv("agents").returncode, 0)
        return e.smv("msg-probe", "--to", "pm", "--source", "role:qa", "--wait", "5", "--settle", "1")

    def test_one_delivery_for_one_ref(self):
        r = self.probe(dedup=True)
        self.assertEqual(r.returncode, 0, out(r))

    def test_two_deliveries_is_red(self):
        r = self.probe(dedup=False)
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn("2 user record(s) carry the marker", out(r))


class Launch(unittest.TestCase):
    def test_pm_role_label(self):
        e = Env(self.addCleanup)
        r = e.py("print(json.dumps([m.pm_create_argv('proj-1', Path('/p'), 'smv-1', x) for x in (False, True)]))")
        plain, labelled = json.loads(r.stdout)
        self.assertNotIn("role=pm", plain)
        self.assertEqual(labelled[labelled.index("role=pm") - 1], "--label")
        self.assertIn("smv-run=smv-1", labelled)


if __name__ == "__main__":
    unittest.main()
