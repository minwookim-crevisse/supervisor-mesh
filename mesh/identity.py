"""Who is calling (D82), in production. ONE function, so a projmux change breaks one place loudly.

  PMX_INTERNAL_ACTIVATION_PANE_UID (inherited by a Claude session projmux started)
    -> `projmux get panes -A -o json`: the ONE pane with that uid -> status.activation.agentUID
    -> `projmux get agents -o json`: that agent, through the live-member rule (role label, realpath(cwd) == the
       project root, phase Running)
    -> label role=pm: PM; any other valid role label: LEAD; anything else: UNKNOWN.
No fallback guessing: a missing variable, no or several matching panes, an empty agentUID, an agent that is not a live
member, an invalid role, or projmux failing all resolve to UNKNOWN. `reason` names the case, never an env value.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .model import ROLE_PATTERN, Caller
from .projmux import Projmux, ProjmuxUnavailable

PANE_VAR = "PMX_INTERNAL_ACTIVATION_PANE_UID"


@dataclass(frozen=True, slots=True)
class Resolved:
    caller: Caller
    reason: str
    pane_uid: str | None = None  # the caller's own pane (pane_command never types into it)
    claude_pid: int | None = None  # that pane's claude process, as projmux reports it (the D96 parent check)
    live_pms: int = 0  # live role=pm members of this project (D109: exactly one may act as the PM)


def resolve(env: Mapping[str, str], root: str | os.PathLike, pmx: Projmux) -> Resolved:
    pane_uid = env.get(PANE_VAR) or ""
    if not pane_uid:
        return Resolved(Caller.unknown(), "not inside a managed projmux pane")
    try:
        matches = [p for p in pmx.panes() if p.uid == pane_uid]
        if len(matches) != 1:
            return Resolved(Caller.unknown(), "no pane matches" if not matches else "ambiguous pane identity")
        agent_uid, claude_pid = matches[0].agent_uid, matches[0].claude_pid
        if not agent_uid:
            return Resolved(Caller.unknown(), "this pane is not a managed agent")
        members = pmx.live_members(root)
        member = next((a for a in members if a.uid == agent_uid), None)
    except ProjmuxUnavailable:
        return Resolved(Caller.unknown(), "projmux unavailable")
    if member is None:
        return Resolved(Caller.unknown(), "the pane's agent is not a live, labelled member of this project")
    pms = sum(1 for a in members if a.role == "pm")
    if member.role == "pm":
        return Resolved(Caller.pm(member.uid), "pm", pane_uid, claude_pid, pms)
    if re.fullmatch(ROLE_PATTERN, member.role or ""):
        return Resolved(Caller.lead(member.role, member.uid), "lead", pane_uid, claude_pid, pms)
    return Resolved(Caller.unknown(), "the role label is not a valid role")


class PaneCaller:
    """The production caller of a front door: called, the pane's identity (read-only tools, unchanged); `resolved()`,
    the full answer `layers.proven_caller` checks for every non-read-only call (D108, D109)."""

    def __init__(self, env: Mapping[str, str], root: str | os.PathLike, pmx: Projmux):
        self.env, self.root, self.pmx = env, root, pmx

    def resolved(self) -> Resolved:
        return resolve(self.env, self.root, self.pmx)

    def __call__(self) -> Caller:
        return self.resolved().caller


ANCESTRY_DEPTH = 8  # claude -> sh (the hook) -> sh (the launcher, exec'd into python) leaves room to spare


def descends_from(pid: int, ancestor: int | None, *, proc_root: str | os.PathLike = "/proc") -> bool:
    """D96: `ancestor` is a STRICT ancestor of `pid` within ANCESTRY_DEPTH generations, read from /proc/<pid>/stat
    (the field after the parenthesised command name is the state, then the parent pid). Fail closed: no ancestor
    given, an unreadable /proc, or a chain that ends first is False."""
    if not ancestor or ancestor <= 0:
        return False
    current = pid
    for _ in range(ANCESTRY_DEPTH):
        parent = _ppid(Path(proc_root), current)
        if parent is None or parent <= 0:
            return False
        if parent == ancestor:
            return True
        current = parent
    return False


def _ppid(proc: Path, pid: int) -> int | None:
    try:
        stat = (proc / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        return int(stat.rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None
