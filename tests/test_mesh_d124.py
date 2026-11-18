"""D124 (WO-0023 final gate, F13): mesh judges a confirmation only after the popup it started has returned; it refuses
when more than one process ever held either FIFO, and when the popup's helper reports `no code` (a distinct line on
the answer FIFO). A stolen code is always a refusal the operator can see, never an approval behind a refusal screen.

The popups here are Python callables that run the REAL mesh/confirm.py helper with the argv and env mesh hands them
(as the repo's D121 tests do); a "thief" is a second instance of that same helper. Every process is a child of this
test; nothing real (tmux, projmux, claude) is touched."""

import os
import re
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from _helpers import ROOT
from test_mesh_d121 import DIR_VAR, D121Case, Operator

sys.path.insert(0, str(ROOT))
from mesh import layers  # noqa: E402

T = 3  # seconds: the confirm timeout in these tests


def read_code(proc, limit_s=10):
    """The code the helper shows on its screen ('' when it shows none), and the screen so far."""
    deadline, seen = time.monotonic() + limit_s, ""
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        seen += line
        m = re.search(r"code: (\S+)", line)
        if m:
            return m.group(1), seen
        if "no code received" in line:
            return "", seen
    return "", seen


def steal_in_process(path, limit_s=5):
    """Read the code FIFO from THIS process (mesh's own pid is never a holder it counts): the popup's helper is then
    the only other process, and it gets no code."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    buf, deadline = b"", time.monotonic() + limit_s
    try:
        while b"\n" not in buf and time.monotonic() < deadline:
            try:
                chunk = os.read(fd, 64)
            except BlockingIOError:
                chunk = b""
            buf += chunk
            if not chunk:
                time.sleep(0.02)
    finally:
        os.close(fd)
    return buf.decode().strip()


class D124Case(D121Case):
    def helper(self, argv, env):
        p = subprocess.Popen(argv, env={**os.environ, **env}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)  # fmt: skip
        self.kids.append(p)
        return p

    def tearDown(self):
        for p in self.kids:
            for f in (p.stdin, p.stderr):
                if f is not None:
                    f.close()
        super().tearDown()


class ThiefTests(D124Case):
    def test_n1_a_thief_running_its_own_helper_instance(self):
        """Ported from security's WO-0023 final test: a same-user process runs mesh's own helper (argv[1] is the helper
        path, so the old `_is_helper` held), reads the code off ITS screen and types it back; the operator's popup then
        gets no code and says so. mesh must refuse."""
        seen = {}

        def popup(argv, env, timeout_s):
            thief = self.helper(argv, env)
            code, _ = read_code(thief)
            seen["thief_has_code"] = bool(code)
            real = self.helper(argv, env)  # the operator's popup, started a moment later
            thief.stdin.write(code + "\n")
            thief.stdin.flush()
            seen["real_code"], seen["real_screen"] = read_code(real)
            real.stdin.write("\n")
            real.stdin.flush()
            thief.wait(30)
            real.wait(30)
            return True

        why = self.run_confirm(popup, timeout_s=T)
        self.assertTrue(seen["thief_has_code"])
        self.assertEqual(seen["real_code"], "")
        self.assertIn("no code received", seen["real_screen"])
        self.assertIsNotNone(why, "approved while the operator's popup said 'no code received; refused'")

    def test_two_helper_instances_ever_seen_are_refused_even_if_one_left_before_the_answer(self):
        """A second helper instance opens `code` while the real popup shows the code, is seen for a few polls and is
        gone before the operator answers correctly: it was seen once, so the confirmation is refused."""
        second = {}

        def on_code(op):
            p = second["p"] = self.helper(op.argv, op.env)  # waits on `code` (mesh writes it only once)
            time.sleep(0.5)  # ~10 mesh polls with the second helper holding `code`
            p.kill()
            p.wait()

        why = self.run_confirm(Operator(on_code=on_code), timeout_s=T)
        self.assertEqual(why, "second_writer")
        self.assertIsNotNone(second["p"].returncode)

    def test_a_second_helper_that_steals_the_code_is_refused_before_the_code_is_sent(self):
        """Two helpers reading `code` when mesh is about to send it: mesh withholds the code and refuses."""
        screens = []

        def popup(argv, env, timeout_s):
            a, b = self.helper(argv, env), self.helper(argv, env)
            for p in (a, b):
                screens.append(read_code(p)[0])
                p.stdin.write("\n")
                p.stdin.flush()
                p.wait(30)
            return True

        gate = threading.Event()

        def sleep(s):  # hold mesh's first poll until both helpers hold `code`
            if not gate.is_set():
                deadline = time.monotonic() + 10
                while len(self._code_readers()) < 2 and time.monotonic() < deadline:
                    time.sleep(0.02)
                gate.set()
            time.sleep(s)

        self.assertEqual(self.run_confirm(popup, timeout_s=T, sleep=sleep), "second_writer")
        self.assertEqual(screens, ["", ""], "the code reached a helper although two held `code`")

    def _code_readers(self):
        work = self.made[-1]
        try:
            want = os.stat(os.path.join(work, "code"))
        except OSError:
            return set()
        readers = set()
        for pid in filter(str.isdigit, os.listdir("/proc")):
            try:
                fds = os.listdir(f"/proc/{pid}/fd")
            except OSError:
                continue
            for fd in fds:
                try:
                    st = os.stat(f"/proc/{pid}/fd/{fd}")
                except OSError:
                    continue
                if (st.st_dev, st.st_ino) == (want.st_dev, want.st_ino) and int(pid) != os.getpid():
                    readers.add(int(pid))
        return readers


class NoCodeTests(D124Case):
    def test_the_popups_helper_reporting_no_code_is_refused_as_no_code(self):
        seen = {}

        def popup(argv, env, timeout_s):
            seen["stolen"] = steal_in_process(os.path.join(env[DIR_VAR], "code"))
            real = self.helper(argv, env)
            seen["code"], seen["screen"] = read_code(real)
            real.stdin.write("\n")
            real.stdin.flush()
            real.wait(30)
            seen["exit"] = real.returncode
            return True

        why = self.run_confirm(popup, timeout_s=T)
        self.assertEqual(len(seen["stolen"]), layers.CONFIRM_LEN)
        self.assertEqual(seen["code"], "")
        self.assertIn("no code received", seen["screen"])
        self.assertEqual(seen["exit"], 3)
        self.assertEqual(why, "no_code")

    def test_the_helper_writes_a_distinct_no_code_line_and_exits_3(self):
        work = tempfile.mkdtemp(dir=str(self.tmp))
        code_p, answer_p = os.path.join(work, "code"), os.path.join(work, "answer")
        os.mkfifo(code_p, 0o600)
        os.mkfifo(answer_p, 0o600)
        afd = os.open(answer_p, os.O_RDONLY | os.O_NONBLOCK)
        try:
            p = subprocess.Popen([sys.executable, str(layers.CONFIRM_HELPER), "1"], env={**os.environ, DIR_VAR: work},
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)  # fmt: skip
            out, _ = p.communicate("\n", timeout=30)  # no code ever arrives: the helper gives up after 1 s
            select.select([afd], [], [], 5)
            line = os.read(afd, 256)
        finally:
            os.close(afd)
        self.assertEqual(p.returncode, 3)
        self.assertIn("no code received", out)
        self.assertEqual(line, f"{layers.CONFIRM_NO_CODE} {p.pid}\n".encode())

    def test_a_forged_no_code_line_is_a_refusal_too(self):
        def on_code(op):
            fd = os.open(op.fifo("answer"), os.O_WRONLY | os.O_NONBLOCK)
            os.write(fd, f"{layers.CONFIRM_NO_CODE} {op.proc.pid}\n".encode())
            os.close(fd)

        self.assertIsNotNone(self.run_confirm(Operator(on_code=on_code), timeout_s=T))


class AfterThePopupTests(D124Case):
    def test_an_answer_before_the_popup_returned_is_not_judged_early(self):
        """The right code arrives at once, but the popup stays up for a while after its helper exited: mesh is still
        listening (its work dir and its reading end of `answer` are there) until the popup returns, and only then
        approves."""
        op, marks = Operator(), {}

        def popup(argv, env, timeout_s):
            op(argv, env, timeout_s)
            time.sleep(0.5)  # ~10 mesh polls after the helper has exited with its answer sent
            answer = os.path.join(env[DIR_VAR], "answer")
            marks["dir_still_there"] = os.path.isdir(env[DIR_VAR])
            try:  # a writer can open a FIFO without blocking only while a reader (mesh) holds it
                os.close(os.open(answer, os.O_WRONLY | os.O_NONBLOCK))
                marks["mesh_still_listening"] = True
            except OSError:
                marks["mesh_still_listening"] = False
            marks["returned"] = time.monotonic()
            return True

        why = self.run_confirm(popup, timeout_s=T)
        self.assertIsNone(why, op.screen + op.err)
        self.assertTrue(marks["dir_still_there"], "mesh judged and removed the dir before the popup returned")
        self.assertTrue(marks["mesh_still_listening"], "mesh judged before the popup returned")

    def test_a_line_after_the_answer_but_before_the_popup_returned_is_refused(self):
        """The real helper answers right; before the popup returns, the popup's helper (here: its pid, claimed by the
        test process) reports no code. Buffered to the end, the second line refuses."""

        def popup(argv, env, timeout_s):
            op = Operator()
            op(argv, env, timeout_s)
            fd = os.open(os.path.join(env[DIR_VAR], "answer"), os.O_WRONLY | os.O_NONBLOCK)
            os.write(fd, f"{layers.CONFIRM_NO_CODE} {op.proc.pid}\n".encode())
            os.close(fd)
            time.sleep(0.3)
            return True

        self.assertIsNotNone(self.run_confirm(popup, timeout_s=T))

    def test_a_popup_that_returns_failed_after_a_right_answer_is_refused(self):
        def popup(argv, env, timeout_s):
            Operator()(argv, env, timeout_s)
            return False  # e.g. tmux reported the popup's command failed

        self.assertEqual(self.run_confirm(popup, timeout_s=T), "unavailable")

    def test_a_popup_that_returns_without_any_answer_is_refused_at_once(self):
        t0 = time.monotonic()
        self.assertEqual(self.run_confirm(lambda argv, env, t: True, timeout_s=T), "no_answer")
        self.assertLess(time.monotonic() - t0, T)


class LegitimateTests(D124Case):
    def test_the_legitimate_single_helper_is_approved(self):
        op = Operator()
        self.assertIsNone(self.run_confirm(op, timeout_s=T), op.screen + op.err)
        self.assertEqual(op.proc.returncode, 0)
        self.assertIn("sent", op.screen)


if __name__ == "__main__":
    unittest.main()
