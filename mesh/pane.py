"""`pane_command` (D84, D85, D86): the PM types ONE operator-named slash command into a lead's pane.

Not a section-4 verb. It consumes a hook-recorded operator event (the single-use ledger `Doc.event_uses`, shared with
`reply` and `override`) and leaves receipts: a separate `receipt` record in `.mesh/events.jsonl` (never an operator
event: `Store.get_event` reads operator records only) and, when an item is named, one entry in its history.

Guards (all must hold; a refusal is recorded in refusals.jsonl with subject `pane_command`):
  caller   the PM (D82)
  text     `vet_command`: one line, starts with `/`, a name token, no control or format character (Cc, Cf, Zl, Zp),
           at most MAX_LEN characters. No allowlist or denylist (D86): the operator binding is the only control.
  target   the ONE live agent of THIS project with that role label (cwd UNDER the root, D84); never the caller's own
           agent or pane, never a `role=pm` agent
  event    exists, unused, fresh (the newest operator prompt, by mesh seq: D74), and bound (D85):
             name path      the event text names the command as a whole token (`names`)
             proposal path  the latest proposal before the event has exactly this text and role, is unused, and the
                            event is the FIRST operator prompt after it
  pane     projmux reports no running turn or open question, and the tmux screen is an idle, empty prompt

Protocol (at most once: a keypress is not idempotent):
  1  guards on a snapshot (unlocked: projmux, tmux)
  2  locked write: re-check the event against the fresh document; mark it used, status ATTEMPTING (and the proposal
     used)
  3  re-classify the pane                                    not idle -> ABORTED, nothing typed
  4  `tmux send-keys -l -- <text>`
  5  poll the capture (D125: up to render_wait_s)            not there -> ABORTED, Enter is NOT pressed (an Enter on a
                                                             dialog would answer it); C-u clears what was typed
  6  Enter
  7  locked write: status DONE; the receipt (target uid, command, event id, seq) into item history and events.jsonl
  8  message the lead that the keypress was operator-instructed
A crash after 2 leaves ATTEMPTING; `recover` marks it UNKNOWN and it is never replayed (the event stays used).
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, replace
from typing import Callable

from . import layers
from .layers import names  # noqa: F401  (the D85 whole-token match lives in the binding layer)
from .model import (
    Actor,
    Caller,
    CallerKind,
    Doc,
    HistoryEntry,
    InvalidArgs,
    MeshError,
    OperatorEvent,
    RefusalCode,
)
from .projmux import Agent, CwdRule, Projmux, ProjmuxUnavailable
from .store import Store
from .tmux import ScreenKind, Tmux, TmuxFailed, classify, holds, prompt_text, valid_pane_id

MAX_LEN = 512
POLL_S = 0.1  # D125: the capture step while the typed text renders into the input box
TOOL = "pane_command"
CODES = frozenset(
    {
        RefusalCode.CALLER_NOT_PM,
        RefusalCode.COMMAND_INVALID,
        RefusalCode.TARGET_INVALID,
        RefusalCode.PANE_NOT_IDLE,
    }
)  # the tool-level codes (outside the transition table); EVENT_NOT_BOUND is shared with `override` (D98)
IDLE_KINDS = frozenset({"idle", "response_complete"})  # fail closed: any other (or no) projmux kind is not idle


# ---------------------------------------------------------------- pure parts
def vet_command(text: str) -> str:
    """The text unchanged, or InvalidArgs. A control or format character could submit, erase or hide what is typed."""
    if not isinstance(text, str) or not text.startswith("/"):
        raise InvalidArgs("the command must start with '/'")
    if len(text) > MAX_LEN:
        raise InvalidArgs(f"the command is longer than {MAX_LEN} characters")
    for ch in text:
        if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"):
            raise InvalidArgs("the command must be one line without control or format characters")
    if not re.match(r"/[A-Za-z0-9_][A-Za-z0-9_.:-]*(?:\s|$)", text):
        raise InvalidArgs("the command needs a name right after '/'")
    return text


def command_name(text: str) -> str:
    return text.split(None, 1)[0][1:]


def privileged(command: str) -> bool:
    """D113/D122: a privilege-changing slash command (its name case-folded: `/CONFIG` is `/config`)."""
    return command_name(command).casefold() in layers.PRIVILEGED_COMMANDS


# ---------------------------------------------------------------- the tool's outcomes
PaneRefused = layers.RefusedError


class PaneAborted(MeshError):  # noqa: N818
    """The event was consumed and the keypress did not complete: never retried (at most once)."""

    code = "pane_aborted"


@dataclass(frozen=True, slots=True)
class Target:
    agent: Agent
    tmux_id: str


class PaneCommand:
    def __init__(
        self, store: Store, pmx: Projmux, tmux: Tmux, root, own_pane: str | None, at: str,
        confirm: Callable[[], None] | None = None, prove: layers.Prover | None = None, render_wait_s: float = 3,
        now: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
    ):  # fmt: skip
        self.store, self.pmx, self.tmux, self.root, self.own_pane, self.at = store, pmx, tmux, root, own_pane, at
        self.confirm = confirm  # D113: raises the refusal unless the operator typed the popup code
        self.prove = prove  # D123: the cite-time transcript proof of a candidate event
        self.render_wait_s, self.now, self.sleep = render_wait_s, now, sleep  # D125

    # -- refusals
    def _refuse(self, caller: Caller, code: RefusalCode, detail: str, item_id: str | None = None):
        layers.refuse(self.store, layers.tool_refusal(TOOL, code, detail, self.at, caller, item_id))

    # -- propose (D85 proposal path): store the exact text, type nothing
    def propose(self, caller: Caller, role: str, command: str) -> dict:
        self._caller_and_text(caller, role, command)
        prop = layers.propose(self.store, role, command, caller.agent_uid)
        return {
            "proposal": {"id": prop.id, "seq": prop.seq, "role": role, "command": command},
            "next": f"ask the operator to approve this exact command by naming {prop.id} in their very next prompt "
            "(D97: a prompt without the id approves nothing), then call again with that prompt's event id",
        }

    def _caller_and_text(self, caller: Caller, role: str, command: str) -> None:
        if caller.kind is not CallerKind.PM:
            self._refuse(caller, RefusalCode.CALLER_NOT_PM, "only the PM types into a lead's pane (D84)")
        try:
            vet_command(command)
        except InvalidArgs as exc:
            self._refuse(caller, RefusalCode.COMMAND_INVALID, str(exc))
        if role == "pm":
            self._refuse(caller, RefusalCode.TARGET_INVALID, "never a role=pm agent")

    # -- send
    def send(self, caller: Caller, role: str, command: str, event_id: str | None, item_id: str | None) -> dict:
        self._caller_and_text(caller, role, command)
        if item_id is not None and item_id not in self.store.read().doc.items:
            self._refuse(caller, RefusalCode.TARGET_INVALID, f"no item {item_id}", item_id)
        if not event_id:
            self._refuse(caller, RefusalCode.EVENT_NOT_BOUND, "cite the operator event that names this command")
        event = layers.resolve_event(self.store, event_id, self.prove)
        self._check_event(caller, self.store.read().doc, event, role, command, item_id)
        target = self._target(caller, role, item_id)
        kind = self._pane_kind(target)
        if kind is not ScreenKind.IDLE:
            self._refuse(
                caller,
                RefusalCode.PANE_NOT_IDLE,
                f"the lead's pane is not idle ({kind.value}); a trust or permission prompt is never answered",
                item_id,
            )
        if privileged(command):  # D113/D122: a privilege-changing command
            if self.confirm is None:
                self._refuse(caller, RefusalCode.CONFIRM_FAILED, "no confirmation channel (D113)", item_id)
            self.confirm()
        with self.store.locked() as txn:  # 2: consume, ATTEMPTING
            binding = self._check_event(caller, txn.doc, event, role, command, item_id)
            doc = layers.consume(txn.doc, event, TOOL, target.agent.uid, "attempting")
            if binding.proposal_id:
                prop = doc.proposals[binding.proposal_id]
                doc = replace(doc, proposals={**doc.proposals, prop.id: replace(prop, used=True)})
            txn.commit(doc)
        outcome = self._keypress(target, command)
        receipt = self._finish(caller, event, target, command, outcome, binding, item_id)
        if outcome != "done":
            raise PaneAborted(
                f"the keypress did not complete ({outcome}); receipt {receipt['id']}; "
                "the event is used and nothing is retried",
                hint="ask the operator again",
            )
        notified = self.pmx.send(
            target.agent.uid,
            caller.agent_uid,
            f"[supervisor-mesh] operator-instructed keypress: the operator (event {event.id}) instructed the PM to "
            f"type `{command}` into this pane (receipt {receipt['id']}). It is not a work order.",
            f"mesh-{receipt['id']}",
        ).delivered
        return {"receipt": {**receipt, "lead_notified": notified}}

    def _check_event(self, caller, doc: Doc, event: OperatorEvent | None, role, command, item_id) -> layers.Bound:
        bound = layers.bind_event(
            doc,
            event,
            fresh=True,
            tokens=(command_name(command),),
            proposal=(role, command),
            events=self.store.operator_events(),
            unbound="the operator event neither names the command nor approves, as the very next prompt naming its "
            "id P-<n>, the PM's latest proposal of exactly this text (D85, D97)",
        )
        if isinstance(bound, layers.Unbound):
            self._refuse(caller, bound.code, bound.detail, item_id)
        return bound

    def _target(self, caller: Caller, role: str, item_id) -> Target:
        try:
            members = [
                a
                for a in self.pmx.live_members(self.root, role=role, cwd=CwdRule.UNDER)
                if a.uid != caller.agent_uid and (self.own_pane is None or a.pane_uid != self.own_pane)
            ]
            if len(members) != 1:
                why = "no live agent with that role in this project" if not members else "several live agents"
                self._refuse(caller, RefusalCode.TARGET_INVALID, f"{why} (never the PM's own pane)", item_id)
            agent = members[0]
            pane = next((p for p in self.pmx.panes() if p.uid == agent.pane_uid and p.agent_uid == agent.uid), None)
        except ProjmuxUnavailable:
            self._refuse(caller, RefusalCode.FACT_UNAVAILABLE, "projmux is unavailable", item_id)
        if pane is None or not pane.tmux_id:
            self._refuse(caller, RefusalCode.TARGET_INVALID, "the lead's pane has no tmux pane", item_id)
        if not valid_pane_id(pane.tmux_id):  # D116
            self._refuse(
                caller, RefusalCode.TARGET_INVALID, "the lead's tmux pane id is not of the form %<digits>", item_id
            )
        return Target(agent, pane.tmux_id)

    def _pane_kind(self, target: Target) -> ScreenKind:
        try:
            if self.pmx.interaction(target.agent.uid) not in IDLE_KINDS:
                return ScreenKind.BUSY
            return classify(self.tmux.capture(target.tmux_id))
        except (ProjmuxUnavailable, TmuxFailed):
            return ScreenKind.UNKNOWN

    def _keypress(self, target: Target, command: str) -> str:
        if self._pane_kind(target) is not ScreenKind.IDLE:  # 3
            return "aborted_not_idle"
        try:  # from the first key on, part of the text may be in the lead's input box: every abort clears it
            self.tmux.type_literal(target.tmux_id, command)  # 4
            if not self._rendered(target, command):  # 5
                self._clear(target)
                return "aborted_text_not_on_prompt"
            self.tmux.enter(target.tmux_id)  # 6
        except TmuxFailed:
            self._clear(target)
            return "aborted_tmux_failed"
        return "done"

    def _rendered(self, target: Target, command: str) -> bool:
        """D125: the box may show the typed text only after the slash menu renders. Poll the capture every POLL_S up
        to render_wait_s until it holds exactly the text; other text in the box is never entered (it times out)."""
        return layers.poll_until(
            lambda: holds(self.tmux.capture(target.tmux_id), command),
            self.render_wait_s,
            POLL_S,
            sleep=self.sleep,
            clock=self.now,
        )

    def _clear(self, target: Target) -> None:
        """An abort after typing: C-u erases the input line, so the lead's box is not left holding our text (never
        Enter). D116: only when the prompt line is visible: a C-u into a trust or permission dialog could act on it.
        Best effort: a failure here is already an abort."""
        try:
            screen = self.tmux.capture(target.tmux_id)
            if prompt_text(screen) is None or classify(screen) in (
                ScreenKind.TRUST_PROMPT,
                ScreenKind.PERMISSION_PROMPT,
            ):
                return
            self.tmux.clear_line(target.tmux_id)
        except TmuxFailed:
            pass

    def _finish(self, caller, event, target: Target, command, outcome, binding: layers.Bound, item_id) -> dict:
        def history(doc: Doc, receipt: dict) -> Doc:
            item = doc.items.get(item_id or "")
            if item is None:
                return doc
            entry = HistoryEntry(
                self.at,
                None,
                TOOL,
                Actor.PM,
                caller.agent_uid,
                item.phase,
                item.phase,
                f"receipt {receipt['id']} ({outcome}): {command} -> {target.agent.uid}, event {event.id}",
            )
            changed = replace(item, version=item.version + 1, history=item.history + (entry,))
            return replace(doc, items={**doc.items, item.id: changed})

        fields = {
            "event_id": event.id,
            "target_uid": target.agent.uid,
            "command": command,
            "outcome": outcome,
            "via": binding.via,
            "item_id": item_id,
        }
        status = "done" if outcome == "done" else "aborted"
        return layers.write_receipt(self.store, self.at, TOOL, fields, event_id=event.id, status=status, edit=history)


recover = layers.recover_attempting  # a crash after ATTEMPTING: UNKNOWN, never replayed
