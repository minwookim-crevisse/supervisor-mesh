"""The ONLY module in mesh/ that starts a process (an import-contract test enforces it).

`run(argv, timeout, cwd=None, env=None) -> Result`: argv list only (never a shell string), always a timeout, the child
in its own process group so a timeout kills everything it started. A spawn error (missing or non-executable program)
and a timeout are distinct RESULTS, never exceptions that leak to a caller. Output is decoded as UTF-8 with
replacement: a tool's bytes never crash the server.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class Result:
    code: int | None  # None: the program never ran (spawn error) or was killed (timeout)
    out: str = ""
    err: str = ""
    spawn_error: bool = False
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0


KILL_GRACE_S = 5.0  # after SIGKILL: how long to wait for the direct child to be reaped
PIPE_GRACE_S = 2.0  # after the direct child is gone: how long to wait for the pipes to reach EOF


class _Drain:
    """Reads one pipe to EOF on a daemon thread, so a process that inherited the pipe (a background or new-session
    grandchild) can never block the caller: `take` waits a bounded time and returns what was read so far."""

    def __init__(self, pipe):
        self._pipe, self._chunks = pipe, []
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            while chunk := self._pipe.read1(65536):
                self._chunks.append(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._pipe.close()
            except OSError:
                pass

    def take(self, wait_s: float) -> str:
        self._thread.join(wait_s)
        return _text(b"".join(self._chunks))


def run(
    argv: Sequence[str],
    timeout: float,
    cwd: str | os.PathLike | None = None,
    env: Mapping[str, str] | None = None,
    *,
    kill_leftovers: bool = False,
) -> Result:
    """The outcome is the DIRECT child's: its exit code, or a timeout when IT runs past `timeout` (then its whole
    process group is killed). Pipes are read on threads and given a bounded grace once the child is gone, so a
    grandchild holding stdout neither turns a pass into a timeout nor blocks the caller. `kill_leftovers` (the verify
    runner) also kills what a finished command left running in its process group."""
    if isinstance(argv, (str, bytes)) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a non-empty list of strings")
    try:
        proc = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # its own process group: a timeout kills the whole tree
        )
    except OSError:
        return Result(None, spawn_error=True)
    out, err = _Drain(proc.stdout), _Drain(proc.stderr)
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        try:
            proc.wait(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            pass
    if kill_leftovers and not timed_out:
        _kill_group(proc)
    stdout, stderr = out.take(PIPE_GRACE_S), err.take(PIPE_GRACE_S)
    if timed_out:
        return Result(None, stdout, stderr, timed_out=True)
    return Result(proc.returncode, stdout, stderr)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _text(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")
