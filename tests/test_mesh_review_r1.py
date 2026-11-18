"""WO-0022 step 5, fix round R1: one test class per review finding (F1, F2c, F2d, F4-F21 of the lead's dispositions).
projmux, tmux and claude are fakes on PATH (tests/_mesh_fakes.py); git is real, in temp repos."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from _helpers import ROOT
from _mesh_fakes import Fakes, parse_caller, pin_pm, prompt_payload, work_order
from test_mesh_state import contract, doc_of, item_in

sys.path.insert(0, str(ROOT))
from mesh import actions, tools  # noqa: E402
from mesh.engine import Engine  # noqa: E402
from mesh.model import Caller, EventUse, Fact, Facts, Phase  # noqa: E402
from mesh.store import Store  # noqa: E402

CLI = ROOT / "mesh" / "cli.py"
SERVER = ROOT / "mesh" / "mcp_server.py"
SEAM = {"MESH_TEST_MODE": "1", "MESH_TEST_CALLER": "pm:forged"}


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

    def tearDown(self):
        self._tmp.cleanup()

    def env(self, **extra):
        base = {k: v for k, v in os.environ.items() if not k.startswith(("MESH_", "CLAUDE_", "PMX_"))}
        base.update(HOME=str(self.home), CLAUDE_PROJECT_DIR=str(self.root))
        return self.fakes.env(base, **extra)

    def cli(self, *argv, stdin="", **extra):
        return subprocess.run(
            [sys.executable, str(CLI), *argv],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=self.tmp,
            env=self.env(**extra),
        )


class F1TestSeamGoneTests(Case):
    """F1: MESH_TEST_MODE / MESH_TEST_CALLER are read by no shipped code path: the env never makes a caller."""

    def test_the_resolver_ignores_the_seam_variables(self):
        self.assertEqual(tools.caller_from_env(SEAM)(), Caller.unknown())
        self.fakes.agent("be-uid", "backend", self.root, pane="pane-be")
        env = self.fakes.env(PMX_INTERNAL_ACTIVATION_PANE_UID="pane-be", **SEAM)
        self.assertEqual(tools.caller_from_env(env, self.root)(), Caller.lead("backend", "be-uid"))

    def test_the_cli_and_the_mcp_server_ignore_the_seam(self):
        self.store.create()
        out = self.cli("mesh_status", **SEAM)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["identity"]["kind"], "unknown")
        proc = subprocess.run(
            [sys.executable, str(SERVER)],
            input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "mesh_status"}})
            + "\n",
            capture_output=True,
            text=True,
            timeout=60,
            cwd=self.root,
            env=self.env(**SEAM),
        )
        reply = json.loads(proc.stdout.splitlines()[0])
        self.assertEqual(reply["result"]["structuredContent"]["identity"]["kind"], "unknown")

    def test_the_hook_ignores_the_seam(self):
        self.store.create()
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "companions_install"}
        out = self.cli("event", "record", "--source", "terminal", stdin=json.dumps(payload), **SEAM)
        self.assertEqual((out.returncode, out.stdout), (0, ""))
        self.assertEqual(self.store.operator_events(), [])

    def test_no_shipped_file_names_the_seam(self):
        for path in (ROOT / "mesh").rglob("*.py"):
            self.assertNotIn("MESH_TEST", path.read_text(encoding="utf-8"), path.name)


AT = "2026-11-18T00:00:00Z"
ATTEMPTING = EventUse("ev-000001", "pane_command", "be-uid", 1, "attempting")


class InProcess(Case):
    def ctx(self, caller="pm:pm-uid", production=False, **env):
        base = self.fakes.env({"PATH": "/usr/bin:/bin", "HOME": str(self.home)}, **env)
        who = None if production else (lambda c=parse_caller(caller): c)
        return tools.Context.for_project(self.root, base, clock=lambda: AT, caller=who)

    def seed(self, *items, **kw):
        self.store.create()
        with self.store.locked() as txn:
            txn.commit(doc_of(*items, **kw))
        work_order(self.root)  # D117 (4): the work order of WO-0001 exists, as a PM writes it before dispatch


class F20CallerOncePerRequestTests(InProcess):
    def test_one_tool_call_resolves_the_caller_once(self):
        self.seed(item_in(Phase.READY, contract=contract(version=1)))
        self.fakes.agent("pm-uid", "pm", self.root, pane="pane-pm")
        self.fakes.agent("be-uid", "backend", self.root)
        ctx = self.ctx(production=True, PMX_INTERNAL_ACTIVATION_PANE_UID="pane-pm")
        out = tools.call(ctx, "work_dispatch", {"item_id": "WO-0001"})
        self.assertTrue(out.ok, out.data)
        self.assertEqual(len(self.fakes.calls("projmux", "get", "panes")), 1)


class F6RecoveryTests(InProcess):
    def split_parent_due(self):
        done = [item_in(Phase.CLOSED, item_id=f"WO-000{n}", contract=contract(version=1)) for n in (2, 3)]
        done = [replace(d, links=replace(d.links, parent="WO-0001")) for d in done]
        parent = item_in(
            Phase.BLOCKED_CHILDREN,
            contract=contract(version=1),
            links=replace(item_in(Phase.READY).links, children=("WO-0002", "WO-0003")),
        )
        self.seed(parent, *done)

    def with_merged_commit(self, ctx):
        def provider(needs, doc, req, cfg):
            return Facts(merged_commit="e" * 40) if Fact.MERGED_COMMIT in needs else Facts()

        return replace(ctx, engine=Engine(ctx.store, facts=provider))

    def test_recovery_drains_the_due_mesh_rows(self):
        self.split_parent_due()
        tools.recover(self.with_merged_commit(self.ctx()))
        self.assertEqual(self.store.read().doc.items["WO-0001"].phase, Phase.VERIFYING)

    def test_a_lead_server_takes_no_pm_recovery_action(self):
        self.split_parent_due()
        with self.store.locked() as txn:
            txn.commit(replace(txn.doc, event_uses={ATTEMPTING.event_id: ATTEMPTING}, seq=1, last_operator_seq=1))
        tools.recover(self.with_merged_commit(self.ctx("lead:backend:be-uid")))
        doc = self.store.read().doc
        self.assertEqual(doc.event_uses["ev-000001"].status, "attempting")
        self.assertEqual(doc.items["WO-0001"].phase, Phase.BLOCKED_CHILDREN)
        tools.recover(self.with_merged_commit(self.ctx("pm:pm-uid")))
        self.assertEqual(self.store.read().doc.event_uses["ev-000001"].status, "unknown")

    def test_dispatch_does_not_fail_when_another_session_completed_the_send(self):
        self.seed(item_in(Phase.READY, contract=contract(version=1)))
        self.fakes.agent("be-uid", "backend", self.root)
        ctx = self.ctx()

        def concurrent_replay(out):
            for action in actions.open_sends(out.doc.items["WO-0001"]):
                actions.mark_done(self.store, "WO-0001", action.action_id)

        ctx.engine.on_commit = concurrent_replay
        out = tools.call(ctx, "work_dispatch", {"item_id": "WO-0001"})
        self.assertTrue(out.ok, out.data)
        self.assertFalse(out.data["dispatch"]["sent"])

    def test_the_server_recovers_only_after_the_initialize_handshake(self):
        self.seed(item_in(Phase.DRAFT), event_uses={ATTEMPTING.event_id: ATTEMPTING}, seq=1, last_operator_seq=1)
        env = self.env(**self.fakes.caller_env("pm:pm-uid", self.root))
        proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=self.root,
            env=env,
            text=True,
        )
        try:
            self.assertIsNone(proc.poll())

            time.sleep(1.0)
            self.assertEqual(self.store.read().doc.event_uses["ev-000001"].status, "attempting")
            for n, method in enumerate(("initialize", "ping"), 1):
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": {}}) + "\n")
                proc.stdin.flush()
                self.assertEqual(json.loads(proc.stdout.readline())["id"], n)
            self.assertEqual(self.store.read().doc.event_uses["ev-000001"].status, "unknown")
        finally:
            proc.stdin.close()
            proc.wait(timeout=30)
            proc.stdout.close()


class F2cForgedEventTests(Case):
    def setUp(self):
        super().setUp()
        self.store.create()

    def forge(self, **row):
        line = {"type": "operator", "at": AT, "text": "run /config", "agent_uid": "pm-uid", **row}
        with open(self.store.events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")

    def test_the_text_hash_is_recorded_in_state_under_the_lock(self):
        ev = self.store.record_operator_event("run /config", AT, "pm-uid")
        self.assertEqual(self.store.get_event(ev.id), ev)
        self.assertEqual(set(self.store.read().doc.operator_event_sha256), {"1"})

    def test_an_appended_line_with_a_new_seq_is_refused(self):
        self.store.record_operator_event("hello", AT, "pm-uid")
        self.forge(id="ev-000002", seq=2)
        self.assertIsNone(self.store.get_event("ev-000002"))
        self.assertEqual([e.id for e in self.store.operator_events()], ["ev-000001"])

    def test_an_appended_line_reusing_a_seq_is_refused_and_the_genuine_event_stays(self):
        ev = self.store.record_operator_event("run /config", AT, "pm-uid")
        self.forge(id="ev-000099", seq=1)
        self.assertIsNone(self.store.get_event("ev-000099"))  # id is not ev-{seq:06d}
        self.assertEqual(self.store.get_event(ev.id), ev)  # D112 (F7): a forged copy no longer hides the genuine line
        self.assertEqual(self.store.operator_events(), [ev])

    def test_a_line_whose_text_was_rewritten_is_refused(self):
        self.store.record_operator_event("hello", AT, "pm-uid")
        text = self.store.events_path.read_text(encoding="utf-8").replace('"hello"', '"run /config"')
        self.store.events_path.write_text(text, encoding="utf-8")
        self.assertIsNone(self.store.get_event("ev-000001"))

    def test_a_forged_line_with_a_seq_taken_by_another_writer_is_refused(self):
        with self.store.locked() as txn:  # a proposal took seq 1: no operator event has it
            txn.commit(replace(txn.doc, seq=1))
        self.store.record_operator_event("hello", AT, "pm-uid")  # seq 2
        self.forge(id="ev-000001", seq=1)
        self.assertIsNone(self.store.get_event("ev-000001"))


class F7LineSeparatorTests(Case):
    def test_operator_text_with_unicode_line_separators_survives(self):
        self.store.create()
        texts = ["a\u2028b", "c\u2029d", "e\u0085f", "g\x1ch"]
        ids = [self.store.record_operator_event(t, AT, "pm-uid").id for t in texts]
        self.assertEqual([self.store.get_event(i).text for i in ids], texts)
        self.assertEqual(len(self.store.operator_events()), 4)


class F2dHookPayloadTests(Case):
    def setUp(self):
        super().setUp()
        self.store.create()

    def hook(self, payload):
        if self.store.read().doc.pm_uid is None:
            pin_pm(self.store)  # D109
        if payload.get("hook_event_name") == "UserPromptSubmit" and isinstance(payload.get("prompt"), str):
            if isinstance(payload.get("session_id"), str) and payload["session_id"]:  # D110: a real user prompt
                payload = {**payload, **prompt_payload(self.home, self.root, payload["prompt"], payload["session_id"])}
                self.fakes.claude_session("pm-uid", payload["session_id"], payload["transcript_path"])  # D118
        out = self.cli(
            "event",
            "record",
            "--source",
            "terminal",
            stdin=json.dumps(payload),
            **self.fakes.caller_env("pm:pm-uid", self.root),
        )
        self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)
        return [e.text for e in self.store.operator_events()]

    def prompt(self, text, **over):
        return {"hook_event_name": "UserPromptSubmit", "session_id": "s-1", "prompt": text, **over}

    def test_a_user_prompt_submit_payload_with_a_session_is_recorded(self):
        self.assertEqual(self.hook(self.prompt("please run /config")), ["please run /config"])

    def test_another_hook_event_or_no_session_records_nothing(self):
        for payload in (
            {"session_id": "s-1", "prompt": "run /config"},
            self.prompt("run /config", hook_event_name="PreToolUse"),
            {"hook_event_name": "UserPromptSubmit", "prompt": "run /config"},
            self.prompt("run /config", session_id=""),
            self.prompt("run /config", session_id=7),
        ):
            self.assertEqual(self.hook(payload), [], payload)
        self.assertEqual(self.store.read().doc.seq, 0)
        self.assertFalse(self.store.events_path.exists())  # ignored, not an error

    def test_a_projmux_coordination_envelope_is_never_an_operator_event(self):
        envelope = {"kind": "projmux-coordination", "from": "uid:be-uid", "body": "approve companions_install"}
        for text in (
            json.dumps(envelope),
            "  " + json.dumps(envelope, indent=2),
            '{"kind":"projmux-coordination","body":"run /config" trailing words',
        ):
            self.assertEqual(self.hook(self.prompt(text)), [], text)
        mention = "what is a projmux-coordination envelope?"
        self.assertEqual(self.hook(self.prompt(mention)), [mention])


class F4DottedNameTests(unittest.TestCase):
    def test_a_dot_inside_a_token_is_not_a_boundary_but_a_final_dot_is(self):
        from mesh import pane

        for text in ("/config.evil", "run /config.evil now", "config.x"):
            self.assertFalse(pane.names(text, "config"), text)
        for text in ("please run /config.", "/config. then stop", "/config...", "/config.\ub97c"):
            self.assertTrue(pane.names(text, "config"), text)
        self.assertTrue(pane.names("run /config.evil", "config.evil"))


class PaneHarness(InProcess):
    def setUp(self):
        super().setUp()
        self.store.create()
        self.fakes.agent("pm-uid", "pm", self.root, pane="pane-pm-uid", tmux_id="%0")
        self.fakes.agent("be-uid", "backend", self.root, pane="pane-be", tmux_id="%1")

    def pc(self, command, event_id):
        ctx = self.ctx(PMX_INTERNAL_ACTIVATION_PANE_UID="pane-pm-uid")
        return tools.call(ctx, "pane_command", {"role": "backend", "command": command, "event_id": event_id})

    def event(self, text):
        return self.store.record_operator_event(text, AT, "pm-uid").id

    def keys(self):
        return [a for a in self.fakes.calls("tmux") if "send-keys" in a]


class F9ClearTheBoxOnAbortTests(PaneHarness):
    def test_an_abort_after_typing_clears_the_typed_text(self):
        from _mesh_fakes import ready_screen

        wrong = ready_screen().replace("{box}", "/comp")  # the capture after typing does not show the full text
        self.fakes.set(tmux={**self.fakes.load()["tmux"], "%1": {"screen": ready_screen(), "box": ""}})
        st = self.fakes.load()
        st["tmux"]["%1"]["next_screens"] = [ready_screen(), ready_screen(), wrong]
        self.fakes.save()
        out = self.pc("/compact", self.event("run /compact"))
        self.assertEqual(out.data["error"]["code"], "pane_aborted")
        self.assertEqual(self.keys()[-1], ["-L", "projmux", "send-keys", "-t", "%1", "C-u"])
        self.assertNotIn("Enter", [a[-1] for a in self.keys()])
        self.assertEqual(self.fakes.load()["tmux"]["%1"]["box"], "")

    def test_an_abort_before_typing_sends_no_keys(self):
        from _mesh_fakes import BUSY_SCREEN, ready_screen

        st = self.fakes.load()
        st["tmux"]["%1"]["next_screens"] = [ready_screen(), BUSY_SCREEN]
        self.fakes.save()
        out = self.pc("/compact", self.event("run /compact"))
        self.assertEqual(out.data["error"]["code"], "pane_aborted")
        self.assertEqual(self.keys(), [])


class F15InteractionFailsClosedTests(PaneHarness):
    def test_no_interaction_kind_or_an_unknown_one_is_not_idle(self):
        for kind in (None, "unknown", "something_new"):
            st = self.fakes.load()
            st["interaction"]["be-uid"] = kind
            if kind is None:
                st["interaction"].pop("be-uid")
            self.fakes.save()
            out = self.pc("/compact", self.event("run /compact"))
            self.assertEqual(out.data["error"].get("reason"), "pane_not_idle", (kind, out.data))
        self.assertEqual(self.keys(), [])

    def test_the_adapter_raises_when_projmux_reports_nothing(self):
        from mesh.projmux import Projmux, ProjmuxUnavailable

        with self.assertRaises(ProjmuxUnavailable):
            Projmux(self.fakes.env()).interaction("nobody")


class F5NoPathOnStderrTests(InProcess):
    def test_an_unexpected_error_logs_its_type_and_a_fixed_id_only(self):
        import contextlib
        import io
        from unittest import mock

        self.store.create()
        ctx = self.ctx()
        boom = PermissionError(13, "Permission denied", str(self.root / ".mesh" / "state.json"))
        err = io.StringIO()
        with mock.patch.object(ctx.store, "read", side_effect=boom), contextlib.redirect_stderr(err):
            out = tools.call(ctx, "work_list", {})
        self.assertEqual(out.data["error"]["code"], "internal")
        self.assertNotIn(str(self.tmp), err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertRegex(err.getvalue(), r"^supervisor-mesh: internal error PermissionError \[E-TOOL\]\n$")

    def test_the_mcp_servers_fallback_logs_no_path_either(self):
        import contextlib
        import io
        from unittest import mock

        from mesh import mcp_server

        err = io.StringIO()
        boom = OSError(5, "I/O error", str(self.root / "secret"))
        with mock.patch.object(mcp_server, "_tools_list", side_effect=boom), contextlib.redirect_stderr(err):
            reply = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, self.ctx())
        self.assertEqual(reply["error"]["code"], mcp_server.INTERNAL_ERROR)
        self.assertNotIn(str(self.tmp), err.getvalue())
        self.assertRegex(err.getvalue(), r"^supervisor-mesh: internal error OSError \[E-RPC\]\n$")

    def test_a_post_commit_hook_failure_logs_no_message(self):
        import contextlib
        import io

        self.store.create()
        ctx = self.ctx()

        def hook(out):
            raise OSError(5, "I/O error", str(self.root / "secret"))

        ctx.engine.on_commit = hook
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = tools.call(
                ctx, "work_new", {"goal": "g", "done_criteria": ["c"], "role": "backend", "type": "feature"}
            )
        self.assertTrue(out.ok, out.data)
        self.assertNotIn(str(self.tmp), err.getvalue())
        self.assertIn("OSError", err.getvalue())

    def test_a_pane_refusal_before_mesh_init_is_a_refusal_not_an_internal_error(self):
        out = tools.call(self.ctx("unknown"), "pane_command", {"role": "backend", "command": "/compact"})
        self.assertEqual((out.data["error"]["code"], out.data["error"]["reason"]), ("refused", "caller_not_pm"))
        self.assertFalse((self.root / ".mesh").exists())


PY = sys.executable


class F8RunnerLifecycleTests(unittest.TestCase):
    def test_the_read_after_a_kill_is_bounded_when_a_new_session_grandchild_holds_the_pipe(self):
        from mesh import runner

        script = (
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import os, time; os.setsid(); time.sleep(25)'])\n"
            "time.sleep(25)\n"
        )
        t0 = time.monotonic()
        res = runner.run([PY, "-c", script], timeout=1.0)
        self.assertTrue(res.timed_out)
        self.assertLess(time.monotonic() - t0, 10.0)

    def test_a_background_child_does_not_turn_a_pass_into_a_timeout(self):
        from mesh import runner

        t0 = time.monotonic()
        res = runner.run(["sh", "-c", "echo hi; sleep 25 & exit 0"], timeout=8.0)
        self.assertEqual((res.code, res.timed_out, res.out), (0, False, "hi\n"))
        self.assertLess(time.monotonic() - t0, 6.0)

    def test_the_verify_runner_kills_what_a_passing_command_left_running(self):
        from mesh import runner

        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d) / "pid"
            res = runner.run(["sh", "-c", f"sleep 25 & echo $! > {pidfile}; exit 0"], timeout=8.0, kill_leftovers=True)
            self.assertEqual(res.code, 0)
            pid = int(pidfile.read_text())
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and _alive(pid):
                time.sleep(0.05)
            self.assertFalse(_alive(pid))


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _git(cwd, *args):
    from test_mesh_verify import sh_git

    return sh_git(cwd, *args)


class F8WorktreeReapTests(Case):
    def setUp(self):
        super().setUp()
        from test_mesh_verify import git_env

        self.genv = git_env(self.home)
        _git(self.root, "init", "-q", "-b", "main")
        (self.root / "f.txt").write_text("x\n")
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-q", "-m", "base")
        self.sha = _git(self.root, "rev-parse", "HEAD")
        self.made = []

    def tearDown(self):
        import shutil

        for d in self.made:
            shutil.rmtree(d, ignore_errors=True)
        super().tearDown()

    def leaked(self, owner: str | None, prefix="mesh-wt-"):
        parent = Path(tempfile.mkdtemp(prefix=prefix))
        self.made.append(parent)
        if owner is not None:  # D122: mesh's marker with the owner pid
            from mesh import layers

            layers.mark_owned(parent, int(owner))
        _git(self.root, "worktree", "add", "--detach", "--quiet", str(parent / "wt"), self.sha)
        return parent / "wt"

    def listed(self):
        out = _git(self.root, "worktree", "list", "--porcelain")
        return {Path(x[9:]).resolve() for x in out.split("\n") if x.startswith("worktree ")}

    def dead_pid(self):
        proc = subprocess.Popen([PY, "-c", "pass"])
        proc.wait()
        return str(proc.pid)

    def test_only_a_dead_owners_mesh_worktree_of_this_repo_is_removed_by_exact_path(self):
        from mesh import git

        stale = self.leaked(self.dead_pid())
        live = self.leaked(str(os.getpid()))
        foreign = self.leaked(self.dead_pid(), prefix="other-")
        gone = self.leaked(None, prefix="someone-")
        import shutil

        shutil.rmtree(gone)  # a missing directory: `worktree prune` would drop its metadata; we never prune
        reaped = git.reap_worktrees(self.root, self.genv)
        self.assertEqual(reaped, 1)
        listed = self.listed()
        self.assertNotIn(stale.resolve(), listed)
        self.assertFalse(stale.parent.exists())
        for kept in (live, foreign, gone):
            self.assertIn(kept.resolve(), listed, kept.parent.name)

    def test_recovery_reaps_in_any_callers_server(self):
        stale = self.leaked(self.dead_pid())
        self.store.create()
        env = self.fakes.env({"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home)})
        who = parse_caller("lead:backend:be-uid")
        tools.recover(tools.Context.for_project(self.root, env, clock=lambda: AT, caller=lambda: who))
        self.assertNotIn(stale.resolve(), self.listed())

    def test_a_worktree_the_runner_makes_names_its_owner(self):
        from mesh import git, layers

        with git.worktree(self.root, self.sha, self.genv) as wt:
            self.assertEqual(layers.marked_owner(wt.parent), os.getpid())
            self.assertTrue(wt.parent.name.startswith("mesh-wt-"))
            self.assertEqual(git.reap_worktrees(self.root, self.genv), 0)  # our own, live run is left alone


class F8SigtermTests(Case):
    def test_sigterm_unwinds_the_server_so_finally_blocks_run(self):
        import signal

        proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=self.root,
            env=self.env(),
            text=True,
        )
        try:
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n")
            proc.stdin.flush()
            self.assertEqual(json.loads(proc.stdout.readline())["id"], 1)
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=30), 128 + signal.SIGTERM)  # SystemExit, not death by signal
        finally:
            proc.stdin.close()
            proc.stdout.close()


class F10OneVerificationPerDueItemTests(InProcess):
    def run_drain(self, statuses):
        from test_mesh_state import passing

        from mesh.model import RunStatus

        F6RecoveryTests.split_parent_due(self)
        merged, calls = "e" * 40, []

        def provider(needs, doc, req, cfg):
            if Fact.MERGED_COMMIT in needs:
                return Facts(merged_commit=merged)
            calls.append(req.row_id)
            status = statuses[min(len(calls), len(statuses)) - 1]
            return passing(merged, cfg=cfg, status=RunStatus(status))

        Engine(self.store, facts=provider).drain(AT)
        return calls, self.store.read().doc.items["WO-0001"]

    def test_a_failing_parent_verification_runs_once(self):
        calls, parent = self.run_drain(["fail"])
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(parent.phase, Phase.REWORK)

    def test_a_flaky_result_is_judged_once_and_never_sticks(self):
        calls, parent = self.run_drain(["fail", "pass"])
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(parent.phase, Phase.REWORK)  # the one result decides; it never stays verifying


class F11VerifyConfigTests(unittest.TestCase):
    def config(self, body):
        from mesh.store import parse_config

        return parse_config(body)

    def test_a_kind_must_be_a_plain_name_and_argv_must_be_non_empty(self):
        from mesh.model import InvalidArgs

        for table in ("verify", "precheck"):
            for cmd in (
                'argv = ["make"]\nkind = "../../x"',
                'argv = ["make"]\nkind = "Tests"',
                'argv = ["make"]\nkind = ""',
                'argv = []\nkind = "tests"',
                'argv = [""]\nkind = "tests"',
            ):
                with self.assertRaises(InvalidArgs, msg=(table, cmd)):
                    self.config(f"[[{table}.commands]]\n{cmd}\n")
        ok = self.config('[[verify.commands]]\nargv = ["make", "check"]\nkind = "unit_tests-2"\n')
        self.assertEqual(ok.verify_commands[0].kind, "unit_tests-2")


class F11VerifyLogTests(Case):
    def setUp(self):
        super().setUp()
        from test_mesh_verify import git_env

        self.genv = git_env(self.home)
        _git(self.root, "init", "-q", "-b", "main")
        (self.root / "f.txt").write_text("x\n")
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-q", "-m", "base")
        self.sha = _git(self.root, "rev-parse", "HEAD")
        (self.root / ".mesh").mkdir()

    def test_logs_are_owner_only(self):
        from mesh import verify
        from mesh.model import Command, RunStatus

        run_id, runs = verify.run_at(self.root, self.sha, [Command(("true",), "tests")], self.genv)
        self.assertEqual(runs[0].status, RunStatus.PASS)
        logs = self.root / ".mesh" / "runs" / run_id
        self.assertEqual(logs.stat().st_mode & 0o777, 0o700)
        self.assertEqual((logs / "1-tests.log").stat().st_mode & 0o777, 0o600)

    def test_a_log_that_cannot_be_written_is_a_failed_run(self):
        from mesh import verify
        from mesh.model import Command, RunStatus

        (self.root / ".mesh" / "runs").write_text("not a directory")
        _, runs = verify.run_at(self.root, self.sha, [Command(("true",), "tests")], self.genv)
        self.assertEqual(runs[0].status, RunStatus.FAIL)


class F14TrackerMarkerTests(Case):
    def test_a_marker_inside_a_goal_never_breaks_a_later_render(self):
        from mesh import tracker

        goal = f"fix {tracker.END} and {tracker.BEGIN} handling"
        doc = doc_of(item_in(Phase.DRAFT, contract=contract(goal=goal)))
        first = tracker.render_file(self.root, doc)
        self.assertTrue(first.changed)
        text = (self.root / tracker.TRACKER).read_text(encoding="utf-8")
        self.assertEqual((text.count(tracker.BEGIN), text.count(tracker.END)), (1, 1))
        second = tracker.render_file(self.root, replace(doc, next_id=9))  # a re-render over the marked file works
        self.assertFalse(second.first_render)
        self.assertIn("fix", (self.root / tracker.TRACKER).read_text(encoding="utf-8"))


class F16ReasonsTests(unittest.TestCase):
    def test_revise_requires_a_reason(self):
        from test_mesh_state import PM, ready_sim

        from mesh.model import InvalidArgs, RefusalCode, ReviseArgs, Verb, from_json

        for args in ({}, {"reason": ""}, {"reason": "   "}):
            with self.assertRaises(InvalidArgs, msg=args):
                from_json(ReviseArgs, args)
        sim = ready_sim()
        self.assertEqual(sim.do(Verb.REVISE, PM, ReviseArgs(" "), "WO-0001").refusal.code, RefusalCode.REASON_REQUIRED)
        sim.ok(Verb.REVISE, PM, ReviseArgs("scope changed"), "WO-0001")
        self.assertEqual(sim.item().history[-1].note, "scope changed")

    def test_the_mesh_rows_record_dependency_cancelled_and_parent_rework(self):
        from test_mesh_state import PM, Sim, split_sim

        from mesh.model import CancelArgs, Verb

        sim = split_sim()
        sim.ok(Verb.CANCEL, PM, CancelArgs("child is wrong"), "WO-0002")
        sibling = sim.item("WO-0003").history[-1]
        self.assertEqual((sibling.row, sibling.note), ("mesh_rework", "parent_rework"))
        dep = item_in(Phase.READY, item_id="WO-0002", contract=contract(version=1, depends=("WO-0001",)))
        sim = Sim(None, doc_of(item_in(Phase.RUNNING), dep))
        sim.ok(Verb.CANCEL, PM, CancelArgs("dropped"), "WO-0001")
        moved = sim.item("WO-0002").history[-1]
        self.assertEqual((moved.row, moved.note), ("mesh_dependency", "dependency_cancelled"))


class F17ResumeRevisionTests(unittest.TestCase):
    def test_conflict_back_to_ready_needs_a_scope_or_depends_revision(self):
        from test_mesh_state import PM, fail, running_sim

        from mesh.model import ContractPatch, ReadyArgs, RefusalCode, Scope, Verb

        sim = running_sim()
        fail(sim, "conflict")
        for patch in (None, ContractPatch(done_criteria=("other",)), ContractPatch(goal="new goal")):
            out = sim.do(Verb.READY, PM, ReadyArgs(patch), "WO-0001")
            self.assertEqual(out.refusal.code, RefusalCode.CONTRACT_NOT_REVISED, patch)
        sim.ok(Verb.READY, PM, ReadyArgs(ContractPatch(scope=Scope(write_set=("src/a.py",)))), "WO-0001")
        self.assertEqual(sim.item().phase, Phase.READY)

    def test_ambiguity_back_to_ready_needs_revised_done_criteria(self):
        from test_mesh_state import PM, fail, running_sim

        from mesh.model import ContractPatch, ReadyArgs, RefusalCode, Scope, Verb

        sim = running_sim()
        fail(sim, "ambiguity")
        for patch in (ContractPatch(role="frontend"), ContractPatch(scope=Scope(read_set=("x",)))):
            out = sim.do(Verb.READY, PM, ReadyArgs(patch), "WO-0001")
            self.assertEqual(out.refusal.code, RefusalCode.CONTRACT_NOT_REVISED, patch)
        sim.ok(Verb.READY, PM, ReadyArgs(ContractPatch(done_criteria=("clearer",))), "WO-0001")
        self.assertEqual(sim.item().contract.version, 2)

    def test_a_cancelled_dependency_back_to_ready_needs_revised_depends(self):
        from test_mesh_state import PM, Sim

        from mesh.model import CancelArgs, ContractPatch, ReadyArgs, RefusalCode, Verb

        dep = item_in(Phase.READY, item_id="WO-0002", contract=contract(version=1, depends=("WO-0001",)))
        sim = Sim(None, doc_of(item_in(Phase.RUNNING), dep))
        sim.ok(Verb.CANCEL, PM, CancelArgs("dropped"), "WO-0001")
        out = sim.do(Verb.READY, PM, ReadyArgs(ContractPatch(done_criteria=("x",))), "WO-0002")
        self.assertEqual(out.refusal.code, RefusalCode.CONTRACT_NOT_REVISED)
        sim.ok(Verb.READY, PM, ReadyArgs(ContractPatch(depends=())), "WO-0002")


class F21AncestryTests(unittest.TestCase):
    def test_a_submitted_commit_must_descend_from_the_base(self):
        from test_mesh_state import BACKEND, COMMIT, running_sim

        from mesh.model import RefusalCode, SubmitArgs, Verb

        for facts, code in (
            (Facts(commit_exists=True, commit_descends=False), RefusalCode.COMMIT_NOT_DESCENDANT),
            (Facts(commit_exists=True), RefusalCode.FACT_UNAVAILABLE),
        ):
            sim = running_sim()
            out = sim.do(Verb.SUBMIT, BACKEND, SubmitArgs(1, COMMIT), "WO-0001", facts)
            self.assertEqual(out.refusal.code, code)
        sim = running_sim()
        sim.ok(Verb.SUBMIT, BACKEND, SubmitArgs(1, COMMIT), "WO-0001", Facts(commit_exists=True, commit_descends=True))

    def test_the_git_fact_uses_merge_base_is_ancestor(self):
        from test_mesh_verify import git_env

        from mesh import git

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            env = git_env(root)
            _git(root, "init", "-q", "-b", "main")
            (root / "f").write_text("1")
            _git(root, "add", ".")
            _git(root, "commit", "-q", "-m", "base")
            base = _git(root, "rev-parse", "HEAD")
            (root / "f").write_text("2")
            _git(root, "commit", "-qam", "child")
            child = _git(root, "rev-parse", "HEAD")
            _git(root, "checkout", "-q", "--orphan", "other")
            _git(root, "commit", "-qm", "unrelated")
            unrelated = _git(root, "rev-parse", "HEAD")
            self.assertTrue(git.descends(root, child, base, env))
            self.assertFalse(git.descends(root, unrelated, base, env))
            self.assertIsNone(git.descends(root / "nope", child, base, env))


class F18HookRobustnessTests(Case):
    def hook(self, root):
        from mesh import init

        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CLAUDE_PROJECT_DIR": str(root)}
        return subprocess.run(["sh", "-c", init.HOOK_COMMAND], capture_output=True, text=True, timeout=30, env=env)

    def test_the_hook_is_silent_and_exits_0_without_the_launcher(self):
        out = self.hook(self.root)  # a fresh clone: .mesh/bin/mesh is gitignored, so it is absent
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))

    def test_the_hook_runs_the_launcher_when_it_is_there(self):
        from mesh import init

        self.assertIn(init.HOOK_MARK, init.HOOK_COMMAND)  # re-runs of mesh_init still recognise the entry
        launcher = self.root / ".mesh" / "bin" / "mesh"
        launcher.parent.mkdir(parents=True)
        launcher.write_text(f'#!/bin/sh\necho "$@" > {self.tmp / "ran"}\n')
        launcher.chmod(0o700)
        out = self.hook(self.root)
        self.assertEqual(out.returncode, 0)
        self.assertEqual((self.tmp / "ran").read_text(), "event record --source terminal\n")

    def test_mesh_status_reports_launcher_health_and_the_last_event(self):
        from mesh import init

        self.store.create()
        ctx = InProcess.ctx(self)
        status = tools.call(ctx, "mesh_status", {}).data
        self.assertEqual((status["launcher"], status["last_event"]), ("missing", None))
        init.write_launcher(self.root, tools.PLUGIN_ROOT, init.Log(dry=False))
        self.assertEqual(tools.call(ctx, "mesh_status", {}).data["launcher"], "ok")
        launcher = self.root / ".mesh" / "bin" / "mesh"
        launcher.chmod(0o600)
        self.assertEqual(tools.call(ctx, "mesh_status", {}).data["launcher"], "not_executable")
        launcher.write_text('#!/bin/sh\nexec python3 /moved/away/cli.py "$@"\n')
        launcher.chmod(0o700)
        self.assertEqual(tools.call(ctx, "mesh_status", {}).data["launcher"], "stale")
        self.store.record_operator_event("hello", AT, "pm-uid")
        status = tools.call(ctx, "mesh_status", {}).data
        self.assertEqual(status["last_event"], {"id": "ev-000001", "seq": 1})
        self.assertNotIn(str(self.tmp), json.dumps(status))
        self.assertNotIn("hello", json.dumps(status))


class F19SizeBoundTests(InProcess):
    def new(self, **over):
        args = {"goal": "g", "done_criteria": ["c"], "role": "backend", "type": "feature", **over}
        return tools.call(self.ctx(), "work_new", args)

    def test_work_new_bounds_the_goal_and_the_done_criteria(self):
        from mesh.model import MAX_CRITERIA, MAX_TEXT

        self.store.create()
        for over in (
            {"goal": "x" * (MAX_TEXT + 1)},
            {"done_criteria": ["y" * (MAX_TEXT + 1)]},
            {"done_criteria": ["c"] * (MAX_CRITERIA + 1)},
        ):
            out = self.new(**over)
            self.assertEqual(out.data["error"]["code"], "invalid_args", list(over))
        self.assertEqual(self.store.read().doc.items, {})
        self.assertTrue(self.new(goal="x" * MAX_TEXT).ok)
        schema = tools.REGISTRY["work_new"].input_schema()
        self.assertEqual(schema["properties"]["goal"]["maxLength"], MAX_TEXT)
        self.assertEqual(schema["properties"]["done_criteria"]["maxItems"], MAX_CRITERIA)

    def test_a_message_over_the_projmux_limit_is_refused_before_anything_is_recorded(self):
        from mesh import dispatch
        from mesh.model import Scope

        long_order = "docs/" + ("w" * 200 + "/") * 10 + "o.md"  # a real file (D117 (4)): every name under 255 bytes
        work_order(self.root, rel=long_order)
        big = Scope(work_order=long_order, report="docs/" + "r" * 2000 + ".md")
        self.seed(item_in(Phase.READY, contract=contract(version=1, scope=big)))
        self.fakes.agent("be-uid", "backend", self.root)
        text = dispatch.compose(self.store.read().doc.items["WO-0001"], self.root, "mesh-WO-0001-d1")
        self.assertGreater(len(text.encode("utf-8")), dispatch.MAX_MESSAGE)
        out = tools.call(self.ctx(), "work_dispatch", {"item_id": "WO-0001"})
        self.assertEqual(out.data["error"]["code"], "invalid_args")
        self.assertEqual(self.store.read().doc.items["WO-0001"].pending_actions, ())
        self.assertEqual(self.fakes.load().get("messages", []), [])


class F12WorkOrderReachesTheLeadTests(unittest.TestCase):
    def test_the_order_names_the_work_order_file(self):
        from mesh import dispatch
        from mesh.model import Scope

        item = item_in(Phase.READY, contract=contract(version=1))
        text = dispatch.compose(item, Path("/work/proj"), "mesh-WO-0001-d1")
        self.assertIn("Work order: /work/proj/docs/work-orders/WO-0001.md (read it first", text)
        named = item_in(Phase.READY, contract=contract(version=1, scope=Scope(work_order="plans/wo-1.md")))
        self.assertIn(
            "Work order: /work/proj/plans/wo-1.md (read it first", dispatch.compose(named, Path("/work/proj"), "r")
        )

    def test_the_run_skill_puts_the_read_set_report_and_work_order_into_the_item(self):
        text = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
        step = text[text.index("## 4.") : text.index("## 5.")]
        for word in ("patch", "scope", "read_set", "report", "work_order"):
            self.assertIn(word, step)


class F13RunSkillToolsTests(unittest.TestCase):
    def test_write_and_edit_are_scoped_to_the_work_orders(self):
        text = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
        line = next(x for x in text.splitlines() if x.startswith("allowed-tools:"))
        tokens = line.split(":", 1)[1].split()
        self.assertIn("Write(docs/work-orders/**)", tokens)
        self.assertIn("Edit(docs/work-orders/**)", tokens)
        self.assertNotIn("Write", tokens)
        self.assertNotIn("Edit", tokens)


if __name__ == "__main__":
    unittest.main()
