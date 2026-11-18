"""Pending external effects (section 8: written before execution, replayed on start) and their completion marks.

A completion mark is bookkeeping of an effect that already happened, not a transition: it flips `done` (and may add a
payload note) under the lock and changes nothing else, so it bumps no record version and stales no compare-and-set.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from .model import ActionKind, Item, PendingAction
from .store import Store


def mark_done(store: Store, item_id: str, action_id: str, note: Mapping[str, str] | None = None) -> None:
    annotate(store, item_id, action_id, note, done=True)


def annotate(store: Store, item_id: str, action_id: str, note: Mapping[str, str] | None, *, done: bool) -> None:
    """Adds `note` to the action's payload and, when `done`, marks it done (a not-done mark never un-does one)."""
    with store.locked() as txn:
        item = txn.doc.items.get(item_id)
        if item is None:
            return
        actions = tuple(
            replace(a, done=a.done or done, payload={**a.payload, **(note or {})}) if a.action_id == action_id else a
            for a in item.pending_actions
        )
        if actions != item.pending_actions:
            txn.commit(replace(txn.doc, items={**txn.doc.items, item_id: replace(item, pending_actions=actions)}))


def open_sends(item: Item) -> tuple[PendingAction, ...]:
    return tuple(a for a in item.pending_actions if a.kind is ActionKind.DISPATCH_SEND and not a.done)
