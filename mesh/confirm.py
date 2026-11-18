#!/usr/bin/env python3
"""D113/D121 popup helper: runs INSIDE `tmux display-popup` on the PM pane's client, started by mesh (`layers.confirm`).

    MESH_CONFIRM_DIR=<work dir> confirm.py <timeout seconds>

The work dir (0700, made by mesh) holds two FIFOs mesh created: `code` and `answer`. The helper reads the one-time
code ONCE from `code` (mesh writes it a single time; if another reader took it first the popup says so and answers
nothing), shows it on the popup's screen only, reads what the operator types, and writes the RAW typed text to
`answer` as one line `mesh-confirm/1 <own pid> <typed>`. It never hashes, compares or judges: mesh does. It holds
`answer` open for HOLD_S after writing (mesh accepts an answer only while this pid lives and holds `answer`), then
exits, which closes the popup; mesh judges only after that (D124). When no code arrives (another reader took it) it
writes the distinct line `mesh-confirm/no-code <own pid>`, shows "no code received" and exits 3 (D124: mesh refuses).
Nothing is written to a regular file, logged or put in an argv. Stdlib only; no mesh import.
"""

import os
import select
import signal
import stat
import sys
import time

HOLD_S = 1.5  # after answering: hold `answer` open this long so mesh sees this pid live and holding it (D121)
MAGIC = "mesh-confirm/1"
NO_CODE = "mesh-confirm/no-code"  # D124: the third outcome, distinct from any typed answer
CODE_WAIT_S = 2.0  # how long to wait for the code (mesh writes it on its next poll after this reader opens `code`)
CLOSE_S = 10.0  # the "no code" screen: how long it stays up unless the operator presses Enter
MAX_TYPED = 64


def _fifo(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISFIFO(st.st_mode) and st.st_uid == os.getuid()


def _read_code(path: str, deadline: float) -> str:
    """The code line from the `code` FIFO, or "" when none arrives (taken by another reader, or mesh is gone)."""
    if not _fifo(path):
        return ""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    buf = b""
    try:
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        while b"\n" not in buf and len(buf) < 64:
            left = deadline - time.monotonic()
            if left <= 0 or not poller.poll(left * 1000):
                break
            try:
                chunk = os.read(fd, 64)
            except BlockingIOError:
                continue
            if not chunk:  # the writer closed: all there is
                break
            buf += chunk
    finally:
        os.close(fd)
    code = buf.partition(b"\n")[0].decode("ascii", "replace").strip()
    return code if code.isalnum() else ""


def _send(work: str, line: str, hold_s: float, said: str = "") -> bool:
    """Write one line to the `answer` FIFO (only a FIFO of ours, opened without blocking: mesh holds its reading end),
    show `said`, then keep it open until mesh closes its end (POLLERR on a FIFO with no reader) or `hold_s`."""
    answer = os.path.join(work, "answer")
    if not _fifo(answer):
        return False
    try:
        fd = os.open(answer, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:  # ENXIO: mesh is gone
        return False
    try:
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            return False
        os.write(fd, f"{line}\n".encode("utf-8"))
        if said:
            sys.stdout.write(said)
            sys.stdout.flush()
        if hold_s > 0:
            poller = select.poll()
            poller.register(fd, 0)  # no events asked: POLLERR / POLLHUP only
            poller.poll(hold_s * 1000)
    finally:
        os.close(fd)
    return True


def main(argv: list[str]) -> int:
    work = os.environ.get("MESH_CONFIRM_DIR", "")
    if len(argv) != 2 or not argv[1].isdigit() or not work:
        return 2
    timeout = max(1, int(argv[1]))
    deadline = time.monotonic() + timeout
    code = _read_code(os.path.join(work, "code"), min(deadline, time.monotonic() + CODE_WAIT_S))
    if not code:
        _send(work, f"{NO_CODE} {os.getpid()}", 0)  # D124: tell mesh first; the screen below may wait for Enter
        sys.stdout.write("supervisor-mesh: no code received (D121); refused. Ask the PM to retry. [Enter closes]\n")
        sys.stdout.flush()
        select.select([sys.stdin], [], [], CLOSE_S)
        return 3
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(max(1, int(deadline - time.monotonic())))
    typed = ""
    try:
        sys.stdout.write(
            "supervisor-mesh: the PM asks to run a high-impact action.\n\n"
            f"  code: {code}\n\n"
            "Type the code and press Enter to confirm (Enter alone refuses): "
        )
        sys.stdout.flush()
        del code
        typed = sys.stdin.readline()
    except (TimeoutError, OSError, KeyboardInterrupt):
        typed = ""
    finally:
        signal.alarm(0)
    typed = " ".join(typed.split())[:MAX_TYPED]  # one line, no control characters
    return 0 if _send(work, f"{MAGIC} {os.getpid()} {typed}", HOLD_S, "\nsent\n") else 4


if __name__ == "__main__":
    sys.exit(main(sys.argv))
