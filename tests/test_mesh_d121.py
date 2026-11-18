"""D121 (revised): the D113 code never touches disk. mesh makes the code and keeps only its hash; the plaintext goes to
the popup helper once through a FIFO (`code`) in a fresh 0700 dir; the operator's RAW typed text comes back through a
second FIFO (`answer`) and mesh hashes and compares it. One answer, only from the live helper pid holding the FIFO; a
pre-existing entry, a second writer or a second write is refused; the dir is removed at once.

The popup here is a Python callable that runs the REAL mesh/confirm.py helper (the argv and env mesh hands it) and
plays the operator through its stdin/stdout. Every helper is a child of this test; nothing real is touched."""

import contextlib
import hashlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from _helpers import ROOT

sys.path.insert(0, str(ROOT))
from mesh import layers  # noqa: E402

DIR_VAR = "MESH_CONFIRM_DIR"
HOLD = (  # a process that opens the answer FIFO for writing, writes argv[2] (maybe empty), says so, and holds it open
    "import os,sys,time\n"
    "fd=os.open(sys.argv[1],os.O_WRONLY|os.O_NONBLOCK)\n"
    "msg=sys.argv[2].replace('{pid}',str(os.getpid()))\n"
    "msg and os.write(fd,(msg+chr(10)).encode())\n"
    "print('held',flush=True)\n"
    "time.sleep(30)\n"
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Gate:
    """An injected `sleep` for mesh's poll loop: once `park()` is called, mesh's next poll waits until `release()`, so a
    test can line up writes before mesh reads anything."""

    def __init__(self):
        self.hold, self.parked, self.go = threading.Event(), threading.Event(), threading.Event()

    def __call__(self, s):
        if self.hold.is_set() and not self.go.is_set():
            self.parked.set()
            self.go.wait(20)
        time.sleep(s)

    def park(self):
        self.hold.set()
        assert self.parked.wait(10), "mesh never polled"

    def release(self):
        self.go.set()


class Operator:
    """The popup: runs the helper argv with the popup env, reads the code off its screen, types `answer(code)`."""

    def __init__(self, answer=lambda code: code, on_code=None, after_send=None, before=None, run_helper=True):
        self.answer, self.on_code, self.after_send, self.before = answer, on_code, after_send, before
        self.run_helper = run_helper
        self.code = self.proc = self.argv = self.env = None
        self.screen, self.err, self.calls = "", "", 0

    def fifo(self, name):
        return os.path.join(self.env[DIR_VAR], name)

    def __call__(self, argv, env, timeout_s):
        try:
            return self._run(argv, env, timeout_s)
        finally:
            for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr) if self.proc else ():
                try:
                    pipe.close()
                except OSError:
                    pass

    def _run(self, argv, env, timeout_s):
        self.calls += 1
        self.argv, self.env = list(argv), dict(env)
        if self.before:
            self.before(self)
        if not self.run_helper:
            return True
        self.proc = subprocess.Popen(
            argv, env={**os.environ, **env}, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )  # fmt: skip
        while self.code is None:
            line = self.proc.stdout.readline()
            if not line:
                break
            self.screen += line
            if "code: " in line:
                self.code = line.split("code: ", 1)[1].strip()
        if self.code is None:  # no code on the screen: the helper refused (a stolen code); nothing to type
            out, self.err = self.proc.communicate(timeout=30)
            self.screen += out
            return True
        if self.on_code:
            self.on_code(self)
        try:
            self.proc.stdin.write(self.answer(self.code) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):  # the helper is gone (killed by the test): nothing typed
            return True
        while "sent" not in self.screen:
            line = self.proc.stdout.readline()
            if not line:
                break
            self.screen += line
        if self.after_send:
            self.after_send(self)
        try:
            out, self.err = self.proc.communicate(timeout=30)
            self.screen += out
        except ValueError:  # killed and reaped by the test
            pass
        return True


class D121Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.made = []
        real = tempfile.mkdtemp

        def mkdtemp(*a, **k):
            d = real(*a, **{**k, "dir": str(self.tmp)})
            self.made.append(d)
            return d

        patcher = mock.patch.object(layers.tempfile, "mkdtemp", mkdtemp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.kids = []

    def tearDown(self):
        for p in self.kids:
            if p.poll() is None:
                p.kill()
            p.wait()
            p.stdout.close()
        self._tmp.cleanup()

    def spawn(self, *argv):
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True)
        self.kids.append(p)
        return p

    def run_confirm(self, popup, timeout_s=8, sleep=time.sleep):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            why = layers.confirm(popup, timeout_s, sleep=sleep)
        self.mesh_out = out.getvalue() + err.getvalue()
        for d in self.made:
            self.assertFalse(os.path.lexists(d), "the work dir must be removed at once")
        return why


class ProtocolTests(D121Case):
    def test_a_right_raw_code_is_accepted_and_the_code_never_touches_disk(self):
        seen, stop = [], threading.Event()

        def scan_forever():  # lstat only (never opens a FIFO); reads every regular file it finds
            while not stop.is_set():
                for d in self.made:
                    for root, _dirs, files in os.walk(d):
                        for f in files:
                            p = os.path.join(root, f)
                            try:
                                if stat.S_ISREG(os.lstat(p).st_mode):
                                    seen.append(Path(p).read_bytes())
                            except OSError:
                                pass
                time.sleep(0.002)

        cmdlines = []

        def on_code(op):  # while the popup shows the code: what is on disk, in any argv
            work = op.env[DIR_VAR]
            self.assertEqual(sorted(os.listdir(work)), ["answer", "code", layers.OWNER_FILE])
            for name in ("answer", "code"):
                self.assertTrue(stat.S_ISFIFO(os.lstat(os.path.join(work, name)).st_mode), name)
            self.assertEqual(stat.S_IMODE(os.lstat(work).st_mode), 0o700)
            for pid in os.listdir("/proc"):
                if pid.isdigit():
                    try:
                        cmdlines.append(Path(f"/proc/{pid}/cmdline").read_bytes())
                    except OSError:
                        pass

        scanner = threading.Thread(target=scan_forever, daemon=True)
        scanner.start()
        op = Operator(on_code=on_code)
        try:
            why = self.run_confirm(op)
        finally:
            stop.set()
            scanner.join(5)
        self.assertIsNone(why, op.screen + op.err)
        code = op.code.encode()
        self.assertEqual(len(op.code), layers.CONFIRM_LEN)
        for blob in seen + cmdlines:
            self.assertNotIn(code, blob)
        self.assertEqual(op.argv, [sys.executable, str(layers.CONFIRM_HELPER), "8"])  # no dir, no code
        self.assertNotIn(op.code, op.err)
        self.assertNotIn(op.code, self.mesh_out)
        self.assertTrue(cmdlines)

    def test_a_forged_answer_without_the_code_is_refused(self):
        for typed in ("WRONGCODE", "anything at all", "{hash}", "{hash_upper}", "{hash} {hash}", "OK", "TRUE"):
            op = Operator(answer=lambda c, t=typed: t.format(hash=sha(c), hash_upper=sha(c).upper()))
            self.assertEqual(self.run_confirm(op), "wrong", typed)

    def test_an_empty_answer_is_none(self):
        self.assertEqual(self.run_confirm(Operator(answer=lambda c: "")), "none")

    def test_the_code_is_case_insensitive_as_typed(self):
        self.assertIsNone(self.run_confirm(Operator(answer=lambda c: f"  {c.lower()}  ")))

    def test_a_forger_that_writes_first_with_its_own_pid_is_refused(self):
        for msg in ("mesh-confirm/1 {pid} ABCDEFGH", "mesh-confirm/1 {pid} " + sha("ABCDEFGH")):
            with self.subTest(msg=msg):

                def before(op, msg=msg):  # no helper at all: only a forger on the answer FIFO
                    p = self.spawn(sys.executable, "-c", HOLD, op.fifo("answer"), msg)
                    self.assertEqual(p.stdout.readline().strip(), "held")

                op = Operator(before=before, run_helper=False)
                self.assertEqual(self.run_confirm(op, timeout_s=3), "popup_gone")

    def test_a_forger_beside_the_real_helper_is_a_second_writer(self):
        gate = Gate()

        def on_code(op):
            gate.park()
            p = self.spawn(sys.executable, "-c", HOLD, op.fifo("answer"), "")  # holds the FIFO, writes nothing
            self.assertEqual(p.stdout.readline().strip(), "held")

        op = Operator(on_code=on_code, after_send=lambda op: gate.release())
        self.assertEqual(self.run_confirm(op, sleep=gate), "second_writer")


class OneAnswerTests(D121Case):
    def test_a_second_write_is_refused(self):
        gate = Gate()

        def after_send(op):
            fd = os.open(op.fifo("answer"), os.O_WRONLY | os.O_NONBLOCK)
            os.write(fd, f"mesh-confirm/1 {op.proc.pid} {op.code}\n".encode())
            os.close(fd)
            gate.release()

        op = Operator(on_code=lambda op: gate.park(), after_send=after_send)
        self.assertEqual(self.run_confirm(op, sleep=gate), "second_writer")

    def test_an_answer_after_the_popup_pid_died_is_refused(self):
        gate = Gate()

        def after_send(op):  # the right code is in the FIFO, then the helper dies before mesh reads it
            op.proc.kill()
            op.proc.wait()
            gate.release()

        op = Operator(on_code=lambda op: gate.park(), after_send=after_send)
        self.assertEqual(self.run_confirm(op, sleep=gate), "popup_gone")

    def test_a_stolen_code_leaves_the_popup_without_a_code(self):
        def before(op):  # a reader takes the one-shot code before the helper opens the FIFO
            thief = self.spawn(sys.executable, "-c", "import sys;print(open(sys.argv[1]).read(),flush=True)",
                               op.fifo("code"))  # fmt: skip
            op.stolen = thief.stdout.readline().strip()

        op = Operator(before=before)
        self.assertEqual(self.run_confirm(op, timeout_s=3), "second_writer")  # D124: two processes held `code`
        self.assertEqual(len(op.stolen), layers.CONFIRM_LEN)
        self.assertIsNone(op.code)
        self.assertIn("no code", op.screen)


class PreExistingTests(D121Case):
    def precreate(self, make):
        real = tempfile.mkdtemp

        def mkdtemp(*a, **k):
            d = real(dir=str(self.tmp))
            os.chmod(d, 0o700)
            make(Path(d))
            self.made.append(d)
            return d

        return mock.patch.object(layers.tempfile, "mkdtemp", mkdtemp)

    def test_a_pre_created_fifo_or_answer_file_is_refused_and_no_popup_runs(self):
        cases = {
            "code fifo": lambda d: os.mkfifo(d / "code", 0o600),
            "answer fifo": lambda d: os.mkfifo(d / "answer", 0o600),
            "answer file": lambda d: (d / "answer").write_text("mesh-confirm/1 1 X\n"),
            "code file": lambda d: (d / "code").write_text(""),
            "stray file": lambda d: (d / "x").write_text(""),
            "answer symlink": lambda d: (d / "answer").symlink_to(d / "elsewhere"),
            "open dir": lambda d: os.chmod(d, 0o755),
        }
        for name, make in cases.items():
            with self.subTest(name), self.precreate(make):
                op = Operator()
                self.assertEqual(self.run_confirm(op, timeout_s=2), "tampered", name)
                self.assertEqual(op.calls, 0, name)

    def test_an_entry_planted_while_waiting_is_refused(self):
        op = Operator(before=lambda op: Path(op.fifo("answer.tmp")).write_text("x"), run_helper=False)
        self.assertEqual(self.run_confirm(op, timeout_s=3), "tampered")

    def test_a_replaced_answer_fifo_is_refused(self):
        def before(op):
            os.unlink(op.fifo("answer"))
            os.mkfifo(op.fifo("answer"), 0o600)

        self.assertEqual(self.run_confirm(Operator(before=before, run_helper=False), timeout_s=3), "tampered")


class RefusedAsBeforeTests(D121Case):
    def test_no_popup_is_unavailable_and_a_silent_popup_times_out(self):
        self.assertEqual(self.run_confirm(lambda argv, env, t: False, timeout_s=2), "unavailable")
        t0 = time.monotonic()
        self.assertEqual(self.run_confirm(lambda argv, env, t: True, timeout_s=1), "no_answer")  # D124: it returned
        self.assertLess(time.monotonic() - t0, 5)

    def test_the_helper_alone_without_mesh_exits_and_writes_nothing(self):
        work = Path(tempfile.mkdtemp(dir=str(self.tmp)))
        r = subprocess.run([sys.executable, str(layers.CONFIRM_HELPER), "1"], env={**os.environ, DIR_VAR: str(work)},
                           input="X\n", capture_output=True, text=True, timeout=20)  # fmt: skip
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(os.listdir(work), [])

    def test_the_tmux_popup_passes_the_dir_by_env_and_no_secret_in_argv(self):
        from mesh.tmux import Tmux

        calls = []
        fake = mock.Mock(side_effect=lambda argv, **k: calls.append(argv) or mock.Mock(ok=True, out="c0\n"))
        with mock.patch("mesh.tmux.runner.run", fake):
            Tmux({"PATH": "/usr/bin"}).popup("%1", ["py", "helper", "5"], {DIR_VAR: "/t/mesh-confirm-x"}, 5)
        popup = calls[-1]
        self.assertIn("display-popup", popup)
        self.assertIn(f"{DIR_VAR}=/t/mesh-confirm-x", popup[popup.index("-e") + 1])
        self.assertEqual(popup[-1], "py helper 5")


if __name__ == "__main__":
    unittest.main()
