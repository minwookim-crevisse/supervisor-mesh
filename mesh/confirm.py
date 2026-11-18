#!/usr/bin/env python3
"""D113 popup helper: runs INSIDE `tmux display-popup` on the PM pane's client, started by mesh (`layers.confirm`).

    confirm.py <work dir> <timeout seconds>

Reads the one-time code from <work dir>/code (0600, written by mesh) and removes the file at once, shows the code on
the popup's screen only, reads what the operator types, and writes the sha256 of it (upper-cased; empty when nothing
was typed) to <work dir>/answer. The code is never written anywhere else, never logged and never in an argv. Stdlib
only; no mesh import.
"""

import hashlib
import os
import signal
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 2
    work, timeout = argv[1], int(argv[2])
    path = os.path.join(work, "code")
    try:
        with open(path, encoding="ascii") as fh:
            code = fh.read().strip()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(max(1, timeout))
    typed = ""
    try:
        sys.stdout.write(
            "supervisor-mesh: the PM asks to run a high-impact action.\n\n"
            f"  code: {code}\n\n"
            "Type the code and press Enter to confirm (Enter alone refuses): "
        )
        sys.stdout.flush()
        del code
        typed = sys.stdin.readline().strip().upper()
    except (TimeoutError, OSError, KeyboardInterrupt):
        typed = ""
    finally:
        signal.alarm(0)
    digest = hashlib.sha256(typed.encode("utf-8")).hexdigest() if typed else ""
    tmp = os.path.join(work, "answer.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(digest)
    os.replace(tmp, os.path.join(work, "answer"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
