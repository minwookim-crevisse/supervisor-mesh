"""The project view (D135): what state this project is in, as `mesh_status` shows it. Three parts:
  Snapshot      every fact `mesh_status` reads, taken once by the tool (`tools._status_snapshot`): the caller, the
                store, ONE projmux `get agents` answer (the caller's identity and the leads read the same answer), the
                companions, the launcher, the operator events and the D102 stalls (those read report mtimes and
                realpaths, so they are taken with the rest of the I/O)
  project_view  Snapshot -> View, pure: no store, no projmux, no filesystem, no clock
  render        View -> the `mesh_status` dict (key names, shapes and order as before the move, WO-0044)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from . import metrics, status
from .model import Caller, Doc, OperatorEvent


@dataclass(frozen=True, slots=True)
class Snapshot:
    caller: Caller
    doc: Doc | None  # None: not initialized
    live: frozenset[str] | None  # the live non-pm lead roles; None: projmux unavailable (or no adapter)
    companions: Mapping[str, bool]
    launcher: str  # ok | missing | stale | not_executable
    events: tuple[OperatorEvent, ...]  # the genuine operator events (empty when not initialized)
    stalls: tuple[dict, ...]  # D102, from the same agents answer (empty without a doc or projmux)
    metrics: bool  # the D90 counters were asked for


@dataclass(frozen=True, slots=True)
class Lead:
    role: str
    present: bool | None  # None: projmux unavailable


@dataclass(frozen=True, slots=True)
class View:
    initialized: bool
    identity: tuple[str, str | None]  # (kind, role)
    items: tuple[tuple[str, tuple[str, ...]], ...]  # (phase, item ids), phases in first-seen order of the sorted ids
    leads: tuple[Lead, ...]
    pending_actions: tuple[dict, ...]
    companions: Mapping[str, bool]
    launcher: str
    last_event: dict | None  # {id, seq} of the newest operator event, never its text
    overlaps: tuple[dict, ...]
    stalls: tuple[dict, ...]
    projmux_available: bool
    metrics: dict | None
    metrics_asked: bool


def attempt_log(action) -> list[str]:
    """The D93 attempts recorded on a dispatch action (comma-joined in its payload: a payload value is a string)."""
    raw = action.payload.get("attempts", "")
    return raw.split(",") if raw else []


def project_view(snap: Snapshot) -> View:
    doc = snap.doc
    items = sorted(doc.items.values(), key=lambda i: i.id) if doc else []
    by_state: dict[str, list[str]] = {}
    for item in items:
        by_state.setdefault(item.phase.value, []).append(item.id)
    roles = {i.contract.role for i in items if not i.phase.terminal}
    live = snap.live
    return View(
        initialized=doc is not None,
        identity=(snap.caller.kind.value, snap.caller.role),
        items=tuple((phase, tuple(ids)) for phase, ids in by_state.items()),
        leads=tuple(Lead(r, None if live is None else r in live) for r in sorted(roles | (live or set()))),
        pending_actions=tuple(
            {"item_id": i.id, "action_id": a.action_id, "kind": a.kind.value, "attempts": attempt_log(a)}
            for i in items
            for a in i.pending_actions
            if not a.done
        ),
        companions=snap.companions,
        launcher=snap.launcher,
        last_event={"id": snap.events[-1].id, "seq": snap.events[-1].seq} if doc and snap.events else None,
        overlaps=tuple(status.overlaps(doc)) if doc else (),
        stalls=snap.stalls,
        projmux_available=live is not None,
        metrics=metrics.counters(doc, snap.events) if snap.metrics and doc else None,
        metrics_asked=snap.metrics,
    )


def render(view: View) -> dict:
    kind, role = view.identity
    out = {
        "initialized": view.initialized,
        "identity": {"kind": kind, "role": role},
        "items": {phase: list(ids) for phase, ids in view.items},
        "leads": [{"role": lead.role, "present": lead.present} for lead in view.leads],
        "pending_actions": [dict(a) for a in view.pending_actions],
        "companions": dict(view.companions),
        "launcher": view.launcher,
        "last_event": view.last_event,
        "overlaps": list(view.overlaps),
        "stalls": list(view.stalls),
    }
    if not view.projmux_available:
        out["projmux"] = "unavailable"
    if view.metrics_asked:
        out["metrics"] = view.metrics
    return out
