"""The status layer (D95): what `mesh_status` and `work_dispatch` WARN about, computed once, here, read-only. Nothing in
this module writes, refuses or nudges: nudging stays a PM action.

  overlaps   D94: two items whose contract write scopes (path globs) or declared shared `resources` overlap, at least
             one of them running or verifying (the other running, verifying or ready). Enforced serialization is
             stage 2: an overlap is a warning, never a refusal.
  stalls     D102: who to nudge (see `stalls`): a worker that finished while its lead did not notice, a quiet lead, an
             unsent or unanswered dispatch.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from .model import ActionKind, Config, Doc, Item, Phase
from .projmux import Agent, CwdRule, cwd_matches, instant

ACTIVE = (Phase.RUNNING, Phase.VERIFYING)
_WILD = re.compile(r"[*?\[]")


# ---------------------------------------------------------------- D94 overlaps
def _static_prefix(glob: str) -> tuple[str, ...]:
    """The path segments before the first wildcard: `src/api/**` -> (src, api)."""
    parts = []
    for seg in glob.strip("/").split("/"):
        if _WILD.search(seg):
            break
        parts.append(seg)
    return tuple(parts)


def globs_overlap(a: str, b: str) -> bool:
    """Conservative (a warning may be spurious, a real overlap is never missed for path-prefix globs): one matches the
    other literally, or one's static prefix contains the other's."""
    if fnmatch.fnmatchcase(a, b) or fnmatch.fnmatchcase(b, a):
        return True
    pa, pb = _static_prefix(a), _static_prefix(b)
    wild_a, wild_b = bool(_WILD.search(a)), bool(_WILD.search(b))
    shorter = min(len(pa), len(pb))
    if pa[:shorter] != pb[:shorter]:
        return False
    return (wild_a and len(pa) <= len(pb)) or (wild_b and len(pb) <= len(pa))


def overlap(a: Item, b: Item) -> dict | None:
    sa, sb = a.contract.scope, b.contract.scope
    write = sorted({x for x in sa.write_set for y in sb.write_set if globs_overlap(x, y)})
    resources = sorted(set(sa.resources) & set(sb.resources))
    if not write and not resources:
        return None
    return {"write_scope": write, "resources": resources}


def overlaps_with(doc: Doc, item: Item) -> list[dict]:
    """D94 at dispatch: every OTHER running / verifying item that overlaps `item` (its globs, from `item`'s side)."""
    out = []
    for other in sorted(doc.items.values(), key=lambda i: i.id):
        if other.id != item.id and other.phase in ACTIVE:
            found = overlap(item, other)
            if found:
                out.append({"item_id": other.id, **found})
    return out


def overlaps(doc: Doc) -> list[dict]:
    """D94 in mesh_status: each overlapping pair once, at least one side active, the other active or ready."""
    live = sorted((i for i in doc.items.values() if i.phase in (*ACTIVE, Phase.READY)), key=lambda i: i.id)
    out = []
    for n, a in enumerate(live):
        for b in live[n + 1 :]:
            if a.phase not in ACTIVE and b.phase not in ACTIVE:
                continue
            found = overlap(a, b)
            if found:
                out.append({"items": [a.id, b.id], **found})
    return out


# ---------------------------------------------------------------- D102 stalls
IDLE = ("idle", "response_complete")


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def stalls(doc: Doc, agents: tuple[Agent, ...], root: str | os.PathLike, now: str, cfg: Config) -> list[dict]:
    """Who to nudge, read-only (D102). For each item:
      worker_done          running / verifying: an idle worker of its role (label `worker-of=<role>`) and the item's
                           report file is newer than the lead's last activity: the lead missed the worker finishing
      lead_quiet           running / verifying: the lead's last activity is older than `cfg.stall_minutes`
      lead_absent          running / verifying: no live lead of the role at all (nudge the PM: respawn or fail)
      dispatch_unsent      ready with an undelivered dispatch (nudge the PM: call work_dispatch again)
      dispatch_unanswered  ready, the order delivered, and the lead quiet that long (it never started)
    Each names `nudge` ("lead <role>" or "pm"). No path beyond the project-relative report path."""
    now_s = instant(now)
    stall_minutes = cfg.stall_minutes
    quiet_s = stall_minutes * 60
    running = [a for a in agents if a.phase == "Running"]
    out: list[dict] = []

    def lead_of(role: str) -> Agent | None:
        found = [a for a in running if a.role == role and cwd_matches(a.cwd, root, CwdRule.EXACT)]
        return found[0] if len(found) == 1 else None

    def quiet(lead: Agent) -> bool:
        at = instant(lead.activity_at)
        return now_s is not None and (at is None or now_s - at >= quiet_s)

    def add(kind: str, item: Item, nudge: str, **more) -> None:
        out.append({"kind": kind, "item_id": item.id, "role": item.contract.role, "nudge": nudge, **more})

    for item in sorted(doc.items.values(), key=lambda i: i.id):
        role = item.contract.role
        lead = lead_of(role)
        if item.phase in ACTIVE:
            if lead is None:
                add("lead_absent", item, "pm")
                continue
            report = item.contract.scope.report
            done = _mtime(Path(root) / report) if report and not Path(report).is_absolute() else None
            lead_at = instant(lead.activity_at)
            workers = [
                w
                for w in running
                if w.worker_of == role and w.interaction in IDLE and cwd_matches(w.cwd, root, CwdRule.UNDER)
            ]
            if workers and done is not None and (lead_at is None or done > lead_at):
                add("worker_done", item, f"lead {role}", report=report, workers=sorted(w.name for w in workers))
            elif quiet(lead):
                add("lead_quiet", item, f"lead {role}", minutes=stall_minutes)
        elif item.phase is Phase.READY:
            sends = [
                a for a in item.pending_actions if a.kind is ActionKind.DISPATCH_SEND and "superseded" not in a.payload
            ]
            if any(not a.done for a in sends):
                add("dispatch_unsent", item, "pm")
            elif sends and lead is not None and quiet(lead):
                add("dispatch_unanswered", item, f"lead {role}", minutes=stall_minutes)
    return out
