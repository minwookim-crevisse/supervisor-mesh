"""D90: three indicative counters, read from what was RECORDED (item history in state.json, operator events in
events.jsonl), never from the current state alone. Indicative only, never a gate: nothing reads them back.

  rework                  entries into rework per item: a history entry whose to-phase is rework and whose from-phase
                          is not (a receipt entry, rework -> rework, is not an entry)
  pass_reversals          entries into rework from verified or closed (a reject, an operator override)
  operator_interventions  operator events recorded by the PM-pane hook; receipts and hook_error lines never count
"""

from __future__ import annotations

from typing import Iterable

from .model import Doc, OperatorEvent, Phase

NOTE = "indicative only, never a gate (D90)"
PASSED = (Phase.VERIFIED, Phase.CLOSED)


def counters(doc: Doc, operator_events: Iterable[OperatorEvent]) -> dict:
    rework: dict[str, int] = {}
    reversals: dict[str, int] = {}
    for item_id in sorted(doc.items):
        entries = [
            e for e in doc.items[item_id].history if e.to_phase is Phase.REWORK and e.from_phase is not Phase.REWORK
        ]
        rework[item_id] = len(entries)
        reversals[item_id] = sum(1 for e in entries if e.from_phase in PASSED)
    return {
        "rework": {"total": sum(rework.values()), "by_item": rework},
        "pass_reversals": {"total": sum(reversals.values()), "by_item": reversals},
        "operator_interventions": sum(1 for _ in operator_events),
        "note": NOTE,
    }
