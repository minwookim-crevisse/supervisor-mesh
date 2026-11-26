"""git adapter: read-only questions about the shared tree and a disposable detached worktree. Real git in tests.

Every commit argument is a validated 40-hex sha (`valid_sha`), checked BEFORE git runs, so a value can never be read as
an option or a revision expression. Every call goes through `runner.run` (argv only, always a timeout). A git that
cannot answer (not a repo, spawn error, timeout) yields None: the fact is unavailable and its guard fails closed.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Iterator, Mapping

from . import layers, runner
from .model import SHA_PATTERN

GIT_TIMEOUT_S = 30.0


def valid_sha(sha: object) -> str:
    if not isinstance(sha, str) or not re.fullmatch(SHA_PATTERN, sha):
        raise ValueError("a commit must be a 40-hex sha")
    return sha


def _git(root: str | os.PathLike, env: Mapping[str, str], *args: str) -> runner.Result:
    return runner.run(["git", *args], GIT_TIMEOUT_S, cwd=root, env=env)


def head(root: str | os.PathLike, env: Mapping[str, str]) -> str | None:
    """`git rev-parse --verify HEAD` of the shared tree (read-only)."""
    res = _git(root, env, "rev-parse", "--verify", "--quiet", "HEAD")
    out = res.out.strip()
    return out if res.ok and re.fullmatch(SHA_PATTERN, out) else None


def user_name(root: str | os.PathLike, env: Mapping[str, str]) -> str | None:
    """`git config user.name` as git resolves it for `root` (read-only; D134): the name, "" when unset (exit 1), None
    when git could not answer (spawn error, timeout, a broken config)."""
    res = _git(root, env, "config", "--get", "user.name")
    if res.spawn_error or res.timed_out or res.code not in (0, 1):
        return None
    return res.out.strip() if res.ok else ""


def commit_exists(root: str | os.PathLike, sha: str, env: Mapping[str, str]) -> bool | None:
    """`git cat-file -t <sha>` is `commit`. None when git itself could not answer (not a repo, spawn error)."""
    sha = valid_sha(sha)
    if not _git(root, env, "rev-parse", "--git-dir").ok:
        return None
    res = _git(root, env, "cat-file", "-t", sha)
    if res.spawn_error or res.timed_out:
        return None
    return res.ok and res.out.strip() == "commit"


def descends(root: str | os.PathLike, commit: str, base: str, env: Mapping[str, str]) -> bool | None:
    """`git merge-base --is-ancestor <base> <commit>`: exit 0 yes, 1 no; anything else (not a repo, an unknown
    commit, spawn error, timeout) None."""
    commit, base = valid_sha(commit), valid_sha(base)
    res = _git(root, env, "merge-base", "--is-ancestor", base, commit)
    return {0: True, 1: False}.get(res.code) if not (res.spawn_error or res.timed_out) else None


WT_PREFIX = "mesh-wt-"  # a mesh-owned worktree is <system temp>/mesh-wt-*/wt with mesh's `owner` marker beside it


class WorktreeUnavailable(Exception):  # noqa: N818  (the spec-style name, like model.Busy)
    """`git worktree add` failed (unknown commit, not a repo, git missing): nothing ran, nothing is left behind."""


@contextlib.contextmanager
def worktree(root: str | os.PathLike, sha: str, env: Mapping[str, str]) -> Iterator[Path]:
    """`git worktree add --detach <tmp>/wt <sha>` OUTSIDE the project (a system temp dir), so the shared working tree
    is never touched (inv 12). In `finally`: `git worktree remove --force <exact path>` of THIS worktree only, then the
    temp parent dir is removed. Never `worktree prune`: it would drop the metadata of any other session's worktree whose
    directory is temporarily missing. A failed remove is logged as one stderr line (exit code only, no paths)."""
    sha = valid_sha(sha)
    parent = Path(tempfile.mkdtemp(prefix=WT_PREFIX))
    path = parent / "wt"
    added = False
    try:
        layers.mark_owned(parent)  # `reap_worktrees` leaves a live run alone
        res = _git(root, env, "worktree", "add", "--detach", "--quiet", str(path), sha)
        if not res.ok:
            raise WorktreeUnavailable("git worktree add failed")
        added = True
        yield path
    finally:
        if added:
            res = _git(root, env, "worktree", "remove", "--force", str(path))
            if not res.ok:
                why = "timeout" if res.timed_out else "spawn error" if res.spawn_error else f"exit {res.code}"
                layers.log(f"git worktree remove failed ({why})")
        shutil.rmtree(parent, ignore_errors=True)


def reap_worktrees(root: str | os.PathLike, env: Mapping[str, str]) -> int:
    """Recovery (section 8): remove the worktrees a killed verify run left behind. Only a worktree that (1) THIS repo
    lists (`git worktree list --porcelain`), (2) sits at `<system temp>/mesh-wt-*/wt` and (3) whose parent passes the
    one reaper rule (`layers.dead_mesh_dir`, D122: 0700, this uid, mesh's marker, owner process gone), each by its
    exact path (`git worktree remove --force <path>`). Never `worktree prune`: it would drop the metadata of another
    session's worktree whose directory is only temporarily missing. Returns how many it removed."""
    res = _git(root, env, "worktree", "list", "--porcelain")
    if not res.ok:
        return 0
    removed = 0
    for line in res.out.split("\n"):
        if not line.startswith("worktree "):
            continue
        path = Path(line[len("worktree ") :])
        parent = path.parent
        if path.name != "wt" or not layers.dead_mesh_dir(parent, WT_PREFIX):  # D122: the one reaper rule
            continue
        if _git(root, env, "worktree", "remove", "--force", str(path)).ok:
            shutil.rmtree(parent, ignore_errors=True)
            removed += 1
    return removed
