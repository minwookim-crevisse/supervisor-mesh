"""The verify runner (section 7): argv commands in a disposable git worktree at a validated commit. ONE runner, three
callers: the verification (submitted or merged commit), the bugfix repro (base commit) and the unblock precheck (HEAD).

  with git.worktree(sha):                        a detached worktree outside the project: the shared tree is untouched
      prepare (D91), when configured: the same runner rules; not a pass -> it is the only run returned
      for each command: runner.run(argv, cwd=worktree, timeout=command.timeout_s, env=allowlist)
                        exit 0 but a `fail_if_output` regex matches a line -> FAIL, that line as evidence (D92)
  worktree removed in `finally`; each command's output goes to .mesh/runs/<run_id>/<n>-<kind>.log (0600), never to a
  tool result, stderr or state.json; a log that cannot be written makes that command FAIL.

Status mapping: exit 0 PASS; 126/127 and a spawn error NOT_RUN (a missing tool is never a pass); a timeout TIMEOUT (the
whole process group is killed by the runner); any other exit FAIL.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import git, layers, runner
from .git import WorktreeUnavailable
from .model import Command, CommandRun, RunStatus

__all__ = ["ENV_ALLOW", "WorktreeUnavailable", "child_env", "run_at", "status_of"]

# What a verification command may see of the server's environment; everything else (tokens, PMX_*, MESH_*, SLACK_*,
# GIT_* overrides) is dropped.
ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR")


def child_env(env: Mapping[str, str]) -> dict[str, str]:
    return {k: env[k] for k in ENV_ALLOW if k in env}


def status_of(res: runner.Result) -> RunStatus:
    if res.timed_out:
        return RunStatus.TIMEOUT
    if res.spawn_error or res.code in (126, 127):
        return RunStatus.NOT_RUN
    return RunStatus.PASS if res.code == 0 else RunStatus.FAIL


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def run_at(
    root: str | os.PathLike,
    sha: str,
    commands: Sequence[Command],
    env: Mapping[str, str],
    *,
    prepare: Command | None = None,
    clock: Callable[[], str] = utc_now,
) -> tuple[str, tuple[CommandRun, ...]]:
    """Runs `prepare` (when given) then `commands` in order in a fresh worktree at `sha`; returns (run_id, runs), the
    prepare run first (kind "prepare"). A prepare that does not pass ends the run: no command runs. Raises ValueError
    for a sha that is not 40 hex and WorktreeUnavailable when the worktree cannot be made (nothing ran)."""
    sha = git.valid_sha(sha)
    run_id = f"run-{secrets.token_hex(6)}"
    logs = Path(root) / ".mesh" / "runs" / run_id
    allowed = child_env(env)
    runs: list[CommandRun] = []
    with git.worktree(root, sha, allowed) as wt:
        for n, cmd in enumerate((prepare, *commands) if prepare else commands, 0 if prepare else 1):
            started = clock()
            res = runner.run(list(cmd.argv), float(cmd.timeout_s), cwd=wt, env=allowed, kill_leftovers=True)
            ended = clock()
            status = status_of(res)
            matched = None
            if status is RunStatus.PASS:
                matched = layers.output_failure(cmd.fail_if_output, res.out, res.err, wt)
            if matched is not None:
                status = RunStatus.FAIL
            body = f"status: {status.value}\nexit: {res.code}\n--- stdout\n{res.out}\n--- stderr\n{res.err}"
            if not _write_log(logs, f"{n}-{cmd.kind}.log", body):
                status = RunStatus.FAIL  # evidence that cannot be kept is no evidence: never a pass
            runs.append(CommandRun(tuple(cmd.argv), cmd.kind, status, res.code, started, ended, matched))
            if cmd is prepare and status is not RunStatus.PASS:
                break
    return run_id, tuple(runs)


def _write_log(logs: Path, name: str, body: str) -> bool:
    """One command's output, owner-only (0700 directories, 0600 file: output can hold anything a tool printed)."""
    try:
        logs.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        logs.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(logs / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        return True
    except OSError:
        return False
