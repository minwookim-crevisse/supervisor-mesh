#!/usr/bin/env python3
"""D113/D121 popup helper: runs INSIDE `tmux display-popup` on the PM pane's client, started by mesh (`layers.confirm`).

    MESH_CONFIRM_DIR=<work dir> confirm.py <timeout seconds>

The work dir (0700, made by mesh) holds two FIFOs mesh created: `code` and `answer`. The helper reads the one-time
code ONCE from `code` (mesh writes it a single time; if another reader took it first the popup says so and answers
nothing), shows it on the popup's screen only, reads what the operator types, and writes the RAW typed text to
`answer` as one line `mesh-confirm/1 <own pid> <typed>`. It never hashes, compares or judges: mesh does. It holds
`answer` open until mesh closes its end (mesh accepts an answer only while this pid lives). Nothing is written to a
regular file, logged or put in an argv. Stdlib only; no mesh import.
"""

import os
import select
import signal
import stat
import sys
import time

HOLD_S = 10.0  # after answering: how long to hold `answer` open for mesh to read it (mesh closing its end ends it)
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


def _hold(fd: int) -> None:
    """Keep the answer end open until mesh closes its reading end (POLLERR on a FIFO with no reader) or HOLD_S."""
    poller = select.poll()
    poller.register(fd, 0)  # no events asked: POLLERR / POLLHUP only
    poller.poll(HOLD_S * 1000)


def main(argv: list[str]) -> int:
    work = os.environ.get("MESH_CONFIRM_DIR", "")
    if len(argv) != 2 or not argv[1].isdigit() or not work:
        return 2
    timeout = max(1, int(argv[1]))
    deadline = time.monotonic() + timeout
    code = _read_code(os.path.join(work, "code"), min(deadline, time.monotonic() + 10))
    if not code:
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
    answer = os.path.join(work, "answer")
    if not _fifo(answer):
        return 4
    fd = os.open(answer, os.O_WRONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            return 4
        os.write(fd, f"mesh-confirm/1 {os.getpid()} {typed}\n".encode("utf-8"))
        sys.stdout.write("\nsent\n")
        sys.stdout.flush()
        _hold(fd)
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
