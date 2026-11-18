"""projmux adapter: the JSON of `projmux get ... -o json` never leaves this file; callers get domain values.

ONE live-member rule (D82; `live_members`): an agent is a live member of a project when it carries a `role` label,
its `realpath(cwd)` is the project root (`CwdRule.EXACT`; `UNDER` also admits a subdirectory, for `pane_command`
targets, D84) and its phase is `Running`. There is no `role-<role>` name fallback: authority rests on the label only,
and never on the label alone (several repos can share one projmux project).

Flags pinned from `projmux --help` (0.18.0), read-only:
  get agents [-o json]                 -> {"items": [agent]}  agent: metadata.{uid,name,labels.role},
                                          spec.workspace.cwd, status.{phase,paneRef}
  get panes -A -o json                 -> {"items": [pane]}   pane: metadata.uid, status.activation.{agentUID,runtimeID,
                                          claude.process.pid}
  agent status get uid:<a>             -> "<kind> observedAt=... ..." kind in unknown|idle|in_progress|
                                          approval_required|input_required|response_complete
  agent message send <agent-ref> [--source <agent-ref>] [--message-ref <ref>] -- <text>
                                          exits non-zero after printing a failed, refused, expired or stale receipt
Every failure (spawn error, timeout, non-zero exit, unusable JSON) is `ProjmuxUnavailable`: the caller fails closed.
Error text names the subcommand and exit code only, never projmux's own output (it can carry paths and names).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from . import layers, runner
from .model import MeshError

TIMEOUT_S = 15.0


class ProjmuxUnavailable(MeshError):  # noqa: N818
    code = "projmux_unavailable"


class CwdRule(StrEnum):
    EXACT = "exact"
    UNDER = "under"


@dataclass(frozen=True, slots=True)
class Agent:
    uid: str
    name: str
    role: str | None  # metadata.labels.role
    cwd: str  # spec.workspace.cwd, as reported
    phase: str  # status.phase; only "Running" is live
    pane_uid: str | None  # status.paneRef
    worker_of: str | None = None  # metadata.labels.worker-of: the lead role that spawned this worker (D102)
    interaction: str | None = None  # status.interaction.kind
    activity_at: str | None = None  # the newest of status.interaction / progress observedAt, lastTransitionAt (D102)


@dataclass(frozen=True, slots=True)
class Pane:
    uid: str
    agent_uid: str | None  # status.activation.agentUID
    tmux_id: str | None  # status.activation.runtimeID, e.g. "%7"
    claude_pid: int | None = None  # status.activation.claude.process.pid: the pane's claude process (D96)


@dataclass(frozen=True, slots=True)
class Sent:
    delivered: bool
    code: int | None
    reason: str | None = None  # the receipt's reason token for a non-delivered send (never projmux's other output)


_REASON = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# D93: the refusals worth resending with the SAME message-ref (projmux deduplicates on it). Matched against the WHOLE
# reason token of the receipt (`<ref>\t<state>\t<reason>\t...`), conservatively: a readiness timeout, a stale route,
# a provider pre-write refusal. Anything else (an unknown target, a stale turn, a bare timeout) is terminal.
_TRANSIENT = (
    re.compile(r"(?:[a-z0-9-]+-)?readiness-(?:[a-z0-9-]+-)?timeout"),
    re.compile(r"(?:[a-z0-9-]+-)?route-stale"),
    re.compile(r"(?:[a-z0-9-]+-)?prewrite-refused"),
)


def transient(reason: str | None) -> bool:
    return bool(reason) and any(rx.fullmatch(reason) for rx in _TRANSIENT)


def receipt_reason(out: str, message_ref: str) -> str | None:
    """The reason field of the receipt line for `message_ref`, when it is a plain token."""
    for line in out.splitlines():
        fields = line.split("\t")
        if len(fields) >= 3 and fields[0] == message_ref and _REASON.fullmatch(fields[2]):
            return fields[2]
    return None


def _real(path: str | os.PathLike) -> str:
    return os.path.realpath(path)


def cwd_matches(cwd: str, root: str | os.PathLike, rule: CwdRule) -> bool:
    if not cwd:
        return False
    here, top = _real(cwd), _real(root)
    if rule is CwdRule.EXACT:
        return here == top
    return here == top or Path(top) in Path(here).parents


class Projmux:
    def __init__(self, env: Mapping[str, str] | None = None, exe: str = "projmux"):
        self.env = dict(env) if env is not None else None
        self.exe = exe

    def _run(self, *args: str) -> runner.Result:
        return runner.run([self.exe, *args], timeout=TIMEOUT_S, env=self.env)

    def _items(self, *args: str) -> list[dict]:
        r = self._run(*args)
        what = " ".join(args[:2])
        if not r.ok:
            raise ProjmuxUnavailable(f"projmux {what} failed (exit {r.code})", hint="is projmux running?")
        try:
            data = layers.parse_json(r.out)
        except ValueError:
            raise ProjmuxUnavailable(f"projmux {what}: output is not JSON") from None
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise ProjmuxUnavailable(f"projmux {what}: no item list")
        return items

    def agents(self) -> tuple[Agent, ...]:
        out = []
        for item in self._items("get", "agents", "-o", "json"):
            meta, spec, status = (_obj(item, k) for k in ("metadata", "spec", "status"))
            uid = meta.get("uid")
            if not isinstance(uid, str) or not uid:
                raise ProjmuxUnavailable("projmux get agents: an agent without a uid")
            labels = _obj(meta, "labels")
            role, worker_of = labels.get("role"), labels.get("worker-of")
            stamps = [
                _str(_obj(status, "interaction").get("observedAt")),
                _str(_obj(status, "progress").get("observedAt")),
                _str(status.get("lastTransitionAt")),
            ]
            out.append(
                Agent(
                    uid,
                    _str(meta.get("name")),
                    role if isinstance(role, str) and role else None,
                    _str(_obj(spec, "workspace").get("cwd")),
                    _str(status.get("phase")),
                    status.get("paneRef") if isinstance(status.get("paneRef"), str) else None,
                    worker_of if isinstance(worker_of, str) and worker_of else None,
                    _str(_obj(status, "interaction").get("kind")) or None,
                    max((s for s in stamps if s), key=_instant, default=None),
                )
            )
        return tuple(out)

    def panes(self) -> tuple[Pane, ...]:
        out = []
        for item in self._items("get", "panes", "-A", "-o", "json"):
            uid = _obj(item, "metadata").get("uid")
            if not isinstance(uid, str) or not uid:
                raise ProjmuxUnavailable("projmux get panes: a pane without a uid")
            act = _obj(_obj(item, "status"), "activation")
            pid = _obj(_obj(act, "claude"), "process").get("pid")
            claude_pid = pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None
            out.append(Pane(uid, _str(act.get("agentUID")) or None, _str(act.get("runtimeID")) or None, claude_pid))
        return tuple(out)

    def live_members(
        self,
        root: str | os.PathLike,
        *,
        role: str | None = None,
        cwd: CwdRule = CwdRule.EXACT,
        agents: tuple[Agent, ...] | None = None,
    ) -> tuple[Agent, ...]:
        """THE live-member rule. `role` None: every labelled live member. `agents`: an answer already fetched."""
        return tuple(
            a
            for a in (self.agents() if agents is None else agents)
            if a.role is not None
            and (role is None or a.role == role)
            and a.phase == "Running"
            and cwd_matches(a.cwd, root, cwd)
        )

    def interaction(self, agent_uid: str) -> str:
        """projmux's semantic interaction kind for an agent. No answer (a failure, an empty reply) is
        ProjmuxUnavailable: the caller fails closed, it never reads silence as idle."""
        r = self._run("agent", "status", "get", f"uid:{agent_uid}")
        words = r.out.split() if r.ok else []
        if not words:
            raise ProjmuxUnavailable(f"projmux agent status get failed (exit {r.code})")
        return words[0]

    def send(self, target_uid: str, source_uid: str | None, text: str, message_ref: str) -> Sent:
        """argv only (no shell, so backticks and `$` in the text are inert). A non-zero exit is a non-delivered
        receipt (failed, refused, expired or stale)."""
        args = ["agent", "message", "send", f"uid:{target_uid}"]
        if source_uid:
            args += ["--source", f"uid:{source_uid}"]
        r = self._run(*args, "--message-ref", message_ref, "--", text)
        return Sent(r.ok, r.code, None if r.ok else receipt_reason(r.out, message_ref))


def instant(stamp: str | None) -> float | None:
    """An RFC 3339 time (projmux writes nanoseconds and `Z`) as epoch seconds; None when it does not parse."""
    if not stamp:
        return None
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)", stamp)
    if not m:
        return None
    zone = "+00:00" if m.group(3) == "Z" else m.group(3)
    try:
        return datetime.fromisoformat(m.group(1) + zone).timestamp() + float("0." + (m.group(2) or "0"))
    except ValueError:
        return None


def _instant(stamp: str) -> float:
    return instant(stamp) or 0.0


def _obj(d: dict, key: str) -> dict:
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def _str(v: object) -> str:
    return v if isinstance(v, str) else ""
