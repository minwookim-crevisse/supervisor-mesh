"""D122 (WO-0023 delta 1: F12, N2): (1) any confirm helper failure, non-zero exit, timeout or missing answer is refused,
never approved, and no path but the answer FIFO mesh reads can approve (F12: a planted `answer.tmp` made the old
helper fail and a forged answer file then approved); (2) the pane_command name is case-folded before the privileged
check, and the fixed D113 list gains /allowed-tools, /settings, /plugins and /permission; (3) dead `mesh-confirm-*`
dirs are reaped at recovery by the verify worktree reaper's rule (mesh pattern in the system temp dir, owner pid
dead, exact path) plus: mode 0700, owned by this uid, mesh's marker. A live one is never reaped."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import ROOT
from test_mesh_d121 import DIR_VAR, D121Case, Gate, Operator
from test_mesh_security_wo0023 import Case

sys.path.insert(0, str(ROOT))
from mesh import layers, pane  # noqa: E402

PY = sys.executable


# ================================================================ (1) F12: failure is refusal
class F12Tests(D121Case):
    def test_a_pre_created_answer_tmp_file_or_fifo_is_refused_before_any_popup(self):
        real = tempfile.mkdtemp
        for make in (lambda d: (d / "answer.tmp").write_text("x"), lambda d: os.mkfifo(d / "answer.tmp", 0o600)):

            def mkdtemp(*a, make=make, **k):
                d = real(dir=str(self.tmp))
                make(Path(d))
                self.made.append(d)
                return d

            with mock.patch.object(layers.tempfile, "mkdtemp", mkdtemp):
                op = Operator()
                self.assertEqual(self.run_confirm(op, timeout_s=2), "tampered")
                self.assertEqual(op.calls, 0)

    def test_the_f12_shape_a_planted_file_and_a_forged_answer_beside_a_refusing_operator(self):
        def on_code(op):  # mid-flight: a planted answer.tmp and a forged `answer.ok` carrying the right code
            Path(op.fifo("answer.tmp")).write_text("")
            Path(op.fifo("answer.ok")).write_text(f"mesh-confirm/1 {op.proc.pid} {op.code}\n")

        op = Operator(answer=lambda c: "", on_code=on_code)  # the operator refuses
        self.assertEqual(self.run_confirm(op), "tampered")

    def test_a_crashing_helper_is_refused(self):
        def popup(argv, env, timeout_s):  # the helper exits non-zero at once (no work dir in its env)
            r = subprocess.run(argv, env={k: v for k, v in os.environ.items() if k != DIR_VAR}, capture_output=True,
                               input="", text=True, timeout=20)  # fmt: skip
            self.codes.append(r.returncode)
            return True

        self.codes = []
        self.assertEqual(self.run_confirm(popup, timeout_s=2), "no_answer")  # D124: the popup returned, no answer
        self.assertEqual(len(self.codes), 1)
        self.assertNotEqual(self.codes[0], 0)

    def test_a_helper_killed_before_it_answers_is_a_missing_answer_and_refused(self):
        def on_code(op):
            op.proc.kill()
            op.proc.wait()

        self.assertEqual(self.run_confirm(Operator(on_code=on_code), timeout_s=2), "no_answer")  # D124

    def test_a_helper_that_times_out_sends_nothing_that_approves(self):
        op = Operator(on_code=lambda op: op.proc.wait(10), answer=lambda c: c)  # the operator never types in time
        self.assertIn(self.run_confirm(op, timeout_s=1), ("none", "timeout"))  # whichever deadline fires first

    def test_a_thief_that_answers_with_the_stolen_code_is_refused(self):
        thief = (
            "import os,sys,time\n"
            "code=open(sys.argv[1]).read().strip()\n"
            "pid=sys.argv[3] if sys.argv[3]!='self' else str(os.getpid())\n"
            "fd=os.open(sys.argv[2],os.O_WRONLY|os.O_NONBLOCK)\n"
            "os.write(fd,('mesh-confirm/1 '+pid+' '+code+chr(10)).encode())\n"
            "print(code,flush=True)\n"
            "time.sleep(30)\n"
        )
        for claim in ("self", "1"):  # its own pid, or another live pid (init) it claims is the popup
            with self.subTest(claim=claim):

                def before(op, claim=claim):
                    p = self.spawn(PY, "-c", thief, op.fifo("code"), op.fifo("answer"), claim)
                    op.stolen = p.stdout.readline().strip()

                op = Operator(before=before)
                self.assertIn(self.run_confirm(op, timeout_s=3), ("popup_gone", "second_writer"))
                self.assertEqual(len(op.stolen), layers.CONFIRM_LEN)
                self.assertIn("no code", op.screen)

    def test_a_forged_line_ahead_of_the_real_answer_is_refused(self):
        gate = Gate()

        def after_send(op):
            gate.release()

        def on_code(op):
            gate.park()
            fd = os.open(op.fifo("answer"), os.O_WRONLY | os.O_NONBLOCK)
            os.write(fd, f"mesh-confirm/1 {op.proc.pid} {op.code}\n".encode())
            os.close(fd)

        op = Operator(answer=lambda c: "", on_code=on_code, after_send=after_send)
        self.assertEqual(self.run_confirm(op, sleep=gate), "second_writer")


# ================================================================ (2) the privileged names
class PrivilegedNameTests(unittest.TestCase):
    NEW = ("allowed-tools", "settings", "plugins", "permission")
    OLD = ("permissions", "config", "login", "logout", "mcp", "plugin", "hooks")

    def test_the_fixed_list(self):
        self.assertEqual(layers.PRIVILEGED_COMMANDS, frozenset(self.OLD + self.NEW))

    def test_every_name_and_its_case_variants_are_privileged(self):
        for name in self.OLD + self.NEW:
            for variant in (name, name.upper(), name.title(), name[:1].upper() + name[1:]):
                self.assertTrue(pane.privileged(f"/{variant} arg"), variant)
                self.assertTrue(pane.privileged(f"/{variant}"), variant)

    def test_ordinary_and_near_miss_names_are_not(self):
        for cmd in ("/status", "/compact", "/help", "/configure", "/setting", "/logins", "/mcp-tools", "/x:config"):
            self.assertFalse(pane.privileged(cmd), cmd)


class PrivilegedCaseFoldWireTests(Case):
    """In process, through pane_command: `/CONFIG` and `/Config` need the popup code like `/config`."""

    def setUp(self):
        super().setUp()
        from mesh import tools

        self.tools = tools
        self.fakes.agent("pm-uid", "pm", self.root, pane="pane-pm", tmux_id="%1")
        self.fakes.agent("be-uid", "backend", self.root, pane="pane-be", tmux_id="%2")
        self.pin(session="s-pm")
        self.store.config_path.write_text("[confirm]\ntimeout_s = 2\n")

    def send(self, command):
        from mesh.model import Caller

        ev = self.store.record_operator_event(f"type {command} into backend", "t", "pm-uid")
        ctx = self.tools.Context.for_project(
            self.root, self.env("pane-pm"), clock=lambda: "t", caller=lambda: Caller.pm("pm-uid")
        )
        return self.tools.call(ctx, "pane_command", {"role": "backend", "command": command, "event_id": ev.id})

    def test_case_variants_need_the_code(self):
        for command in ("/CONFIG", "/Config", "/Allowed-Tools", "/SETTINGS", "/Plugins", "/permission"):
            self.fakes.set(popup="none")
            out = self.send(command)
            self.assertEqual(out.data["error"]["reason"], "confirm_failed", (command, out.data))
            self.fakes.set(popup="right")
            self.assertTrue(self.send(command).ok, command)
        self.assertEqual(len(self.fakes.calls("tmux", "-L", "projmux", "display-popup")), 12)
        self.assertEqual(self.fakes.load()["tmux"]["%2"]["submitted"],
                         ["/CONFIG", "/Config", "/Allowed-Tools", "/SETTINGS", "/Plugins", "/permission"])  # fmt: skip


# ================================================================ (3) the reaper
class ConfirmDirReaperTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        patcher = mock.patch.object(tempfile, "tempdir", str(self.base))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.kids = []

    def tearDown(self):
        for p in self.kids:
            p.kill()
            p.wait()
        self._tmp.cleanup()

    def dead_pid(self):
        p = subprocess.Popen([PY, "-c", "pass"])
        p.wait()
        return p.pid

    def live_pid(self):
        p = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])
        self.kids.append(p)
        return p.pid

    def leaked(self, pid, *, name=None, mode=0o700, marker=True, fifos=True):
        d = Path(tempfile.mkdtemp(prefix=layers.CONFIRM_PREFIX)) if name is None else self.base / name
        d.mkdir(exist_ok=True)
        if marker:
            layers.mark_owned(d, pid)
        if fifos:
            os.mkfifo(d / "code", 0o600)
            os.mkfifo(d / "answer", 0o600)
        os.chmod(d, mode)
        return d

    def test_a_dead_one_with_every_check_passing_is_removed(self):
        dead = self.leaked(self.dead_pid())
        self.assertEqual(layers.reap_confirm_dirs(), 1)
        self.assertFalse(dead.exists())

    def test_live_and_unproven_ones_are_kept(self):
        kept = {
            "our own live run": self.leaked(os.getpid()),
            "another session in flight": self.leaked(self.live_pid()),
            "wrong mode": self.leaked(self.dead_pid(), mode=0o755),
            "no marker": self.leaked(self.dead_pid(), marker=False),
            "another prefix": self.leaked(self.dead_pid(), name="mesh-other-1"),
        }
        untagged = self.leaked(self.dead_pid(), marker=False)
        (untagged / layers.OWNER_FILE).write_text(str(self.dead_pid()))  # a bare pid: not mesh's marker
        kept["untagged owner file"] = untagged
        link_target = self.leaked(self.dead_pid(), name="target")
        link = self.base / f"{layers.CONFIRM_PREFIX}link"
        link.symlink_to(link_target)
        kept["symlink"] = link
        self.assertEqual(layers.reap_confirm_dirs(), 0)
        for why, d in kept.items():
            self.assertTrue(os.path.lexists(d), why)
        self.assertTrue(link_target.exists())

    def test_another_uid_is_kept(self):
        d = self.leaked(self.dead_pid())
        with mock.patch.object(layers.os, "getuid", return_value=os.getuid() + 1):
            self.assertEqual(layers.reap_confirm_dirs(), 0)
        self.assertTrue(d.exists())

    def test_confirm_marks_its_dir_and_recovery_reaps_a_dead_one(self):
        from mesh import tools
        from mesh.model import Caller
        from mesh.store import Store

        seen = []

        def popup(argv, env, timeout_s):
            work = Path(env[DIR_VAR])
            seen.append((stat_mode(work), sorted(os.listdir(work)), layers.marked_owner(work)))
            self.assertEqual(layers.reap_confirm_dirs(), 0)  # our live run is left alone
            return False

        self.assertEqual(layers.confirm(popup, 1), "unavailable")
        self.assertEqual(seen, [(0o700, ["answer", "code", layers.OWNER_FILE], os.getpid())])
        dead = self.leaked(self.dead_pid())
        root = self.base / "proj"
        root.mkdir()
        Store(root / ".mesh").create()
        ctx = tools.Context.for_project(root, {"PATH": "/usr/bin:/bin", "HOME": str(self.base)}, clock=lambda: "t",
                                        caller=lambda: Caller.lead("backend", "be-uid"))  # fmt: skip
        tools.recover(ctx)
        self.assertFalse(dead.exists())


def stat_mode(path):
    import stat

    return stat.S_IMODE(os.lstat(path).st_mode)


if __name__ == "__main__":
    unittest.main()
