"""The cross-cutting layers (D95): each concern is implemented ONCE, here, and every tool and verb goes through it.
A test (tests/test_mesh_review_w5.py) asserts that no other module re-implements one of them and that every entry of
`tools.REGISTRY` is wrapped by `tool_layer`.

  tool layer          `tool_layer(name, args, fn)`: the caller resolved once per request, the arguments parsed, every
                      outcome mapped to ONE result shape (refusal, mesh error, internal error)
  stdout / stderr     `log` is the only stderr writer (one line, an error TYPE and a fixed id, never a message, an
                      argument, an env value or a path); `claim_stdout` reserves fd 1 for the MCP protocol;
                      `quiet_stdout` sends a hook's stray output to stderr
  refusal recording   `record_refusal` / `refuse`: the only caller of `Store.append_refusal`
  receipts            `write_receipt`: the only caller of `Store.append_receipt` (one seq, one locked write)
  operator binding    `resolve_event` (the only caller of `Store.get_event`), `bind_event` (exists, unused, fresh,
                      bound: the D85 name path or the D97 proposal path), `consume` / `set_use_status` (the single-use
                      ledger), `propose`, `recover_attempting`
  retry               `retry`: bounded attempts with exponential backoff, only while the outcome is transient
  output checks       `output_failure`: a `fail_if_output` regex matching a line of a passing command's output (D92)
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, NoReturn, Sequence, TextIO, TypeVar

from .model import (
    Caller,
    Doc,
    EventUse,
    MeshError,
    OperatorEvent,
    Proposal,
    Refusal,
    RefusalCode,
    from_json,
)

if TYPE_CHECKING:  # the layer calls the store it is handed; it never opens one (state.py imports this module)
    from .store import Store

T = TypeVar("T")
TOOL_LAYERS = ("caller once per request", "argument parsing", "refusal recording", "error mapping", "stderr discipline")


# ---------------------------------------------------------------- stdout / stderr discipline
def log(line: str) -> None:
    """THE stderr writer. Callers pass fixed text plus an error type name: never a message, a path or a value."""
    sys.stderr.write(f"supervisor-mesh: {line}\n")


def claim_stdout() -> TextIO:
    """The only writer of the protocol stream. Everything else that writes to fd 1 or sys.stdout lands on stderr."""
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return out


def quiet_stdout() -> None:
    """A UserPromptSubmit hook's stdout would be added to the prompt: anything printed goes to stderr."""
    sys.stdout = sys.stderr


# ---------------------------------------------------------------- refusal recording
class RefusedError(MeshError):  # noqa: N818
    """A refusal reached the caller: it was recorded in .mesh/refusals.jsonl (when .mesh/ exists) and nothing else was
    written."""

    code = "refused"

    def __init__(self, refusal: Refusal):
        super().__init__(refusal.detail)
        self.refusal = refusal


def record_refusal(store: Store, refusal: Refusal) -> None:
    """Before mesh_init there is nowhere to record a refusal, and nothing is created."""
    if store.dir.is_dir():
        store.append_refusal(refusal)


def refuse(store: Store, refusal: Refusal) -> NoReturn:
    record_refusal(store, refusal)
    raise RefusedError(refusal)


def tool_refusal(subject: str, code: RefusalCode, detail: str, at: str, caller: Caller, item_id=None) -> Refusal:
    return Refusal(subject, code, detail, at, caller.kind.value, None, item_id, None, caller.agent_uid)


# ---------------------------------------------------------------- receipts / audit log
def write_receipt(
    store: Store,
    at: str,
    tool: str,
    fields: Mapping[str, Any],
    *,
    event_id: str | None = None,
    status: str | None = None,
    edit: Callable[[Doc, dict], Doc] | None = None,
) -> dict:
    """One locked write: the receipt takes the next mesh seq (`rc-<seq>`), the cited event's use gets its final
    status, `edit` may add to the document (an item's history), and the receipt line joins events.jsonl (type
    `receipt`: never read back as an operator event)."""
    with store.locked() as txn:
        seq = txn.doc.seq + 1
        receipt = {"id": f"rc-{seq:06d}", "seq": seq, "at": at, "tool": tool, **fields}
        doc = replace(txn.doc, seq=seq)
        if event_id is not None and status is not None:
            doc = set_use_status(doc, event_id, status)
        if edit is not None:
            doc = edit(doc, receipt)
        txn.commit(doc)
        store.append_receipt(receipt)
    return receipt


# ---------------------------------------------------------------- operator binding (D63, D74, D85, D96-D98)
_TOKEN = r"A-Za-z0-9_.:/-"


def names(event_text: str, name: str) -> bool:
    """D85 whole-token match: `name`, optionally led by `/`, not inside a longer token. A token character
    ([A-Za-z0-9_.:/-]) before it, one of [A-Za-z0-9_:-] after it, or a dot followed by a name character
    (`/config.evil`) makes it part of something else; any other character is a boundary, so a trailing Korean particle
    (`/config` + U+B97C) or sentence punctuation (`/config.`) still names it."""
    rx = rf"(?<![{_TOKEN}])/?{re.escape(name)}(?![A-Za-z0-9_:-])(?!\.+[A-Za-z0-9_])"
    return re.search(rx, event_text) is not None


def resolve_event(store: Store, event_id: str | None) -> OperatorEvent | None:
    """THE lookup of a cited operator event: only hook-recorded, genuine events (`Store.get_event`)."""
    return store.get_event(event_id) if event_id else None


@dataclass(frozen=True, slots=True)
class Bound:
    event: OperatorEvent
    via: str  # "event" (no binding asked) | "name" | "proposal"
    proposal_id: str | None = None


@dataclass(frozen=True, slots=True)
class Unbound:
    code: RefusalCode
    detail: str


def bind_event(
    doc: Doc,
    event: OperatorEvent | None,
    *,
    fresh: bool,
    tokens: Sequence[str] | None = None,
    proposal: tuple[str, str] | None = None,
    events: Iterable[OperatorEvent] = (),
    unbound: str = "the operator event does not name what it authorises",
) -> Bound | Unbound:
    """THE operator-event binding. In order: the event exists (hook-recorded), is unused (single use, D63), is the
    newest operator prompt when `fresh` (by mesh seq, D74), and, when `tokens` is given, is bound:
      name path      the event text names one of `tokens` as a whole token (`names`)
      proposal path  `proposal` = (role, exact text): the latest proposal before the event has exactly that text and
                     role, is unused, the event is the FIRST operator prompt after it, and the event text names the
                     proposal id `P-<n>` as a whole token (D97: any other prompt, a "no" included, approves nothing)
    `events` are the genuine operator events (for "the first prompt after the proposal")."""
    if event is None:
        return Unbound(RefusalCode.EVENT_UNKNOWN, "no operator event with that id was recorded by the PM-pane hook")
    if event.id in doc.event_uses:
        return Unbound(RefusalCode.EVENT_REUSED, f"operator event {event.id} was already used")
    if fresh and event.seq != doc.last_operator_seq:
        return Unbound(RefusalCode.EVENT_STALE, "a newer operator prompt was recorded after it: cite the latest one")
    if tokens is None:
        return Bound(event, "event")
    if any(names(event.text, t) for t in tokens):
        return Bound(event, "name")
    if proposal is not None:
        role, command = proposal
        before = [p for p in doc.proposals.values() if p.seq < event.seq]
        prop = max(before, key=lambda p: p.seq) if before else None
        if (
            prop is not None
            and not prop.used
            and prop.role == role
            and prop.command == command
            and names(event.text, prop.id)
            and not any(prop.seq < e.seq < event.seq for e in events)
        ):
            return Bound(event, "proposal", prop.id)
    return Unbound(RefusalCode.EVENT_NOT_BOUND, unbound)


def consume(doc: Doc, event: OperatorEvent, kind: str, ref: str, status: str | None = None) -> Doc:
    """Mark the event used (the single-use ledger shared by reply, override, pane_command and companions_install)."""
    return replace(doc, event_uses={**doc.event_uses, event.id: EventUse(event.id, kind, ref, event.seq, status)})


def set_use_status(doc: Doc, event_id: str, status: str) -> Doc:
    return replace(doc, event_uses={**doc.event_uses, event_id: replace(doc.event_uses[event_id], status=status)})


def propose(store: Store, role: str, command: str, by_uid: str | None) -> Proposal:
    """D85 proposal path: the exact text under the next mesh seq, id `P-<seq>` (D97: the approval must name it)."""
    with store.locked() as txn:
        seq = txn.doc.seq + 1
        prop = Proposal(f"P-{seq}", seq, role, command, by_uid)
        txn.commit(replace(txn.doc, seq=seq, proposals={**txn.doc.proposals, prop.id: prop}))
    return prop


def recover_attempting(store: Store) -> list[str]:
    """A crash after ATTEMPTING (a keypress or an install): it may or may not have happened. Mark it UNKNOWN; never
    replay it (the event stays used)."""
    with store.locked() as txn:
        stuck = [eid for eid, u in txn.doc.event_uses.items() if u.status == "attempting"]
        doc = txn.doc
        for eid in stuck:
            doc = set_use_status(doc, eid, "unknown")
        if stuck:
            txn.commit(doc)
    return stuck


# ---------------------------------------------------------------- retry
def retry(
    attempt: Callable[[int], T],
    *,
    retries: int,
    transient: Callable[[T], bool],
    backoff_s: float,
    sleep: Callable[[float], None] = time.sleep,
) -> list[T]:
    """Runs `attempt(n)` (n = 1..) until its outcome is not transient, at most 1 + `retries` times, sleeping
    backoff_s * 2^(n-1) between attempts. Returns every outcome, in order (the caller records each)."""
    outcomes: list[T] = []
    for n in range(1, retries + 2):
        outcome = attempt(n)
        outcomes.append(outcome)
        if not transient(outcome) or n == retries + 1:
            break
        if backoff_s > 0:
            sleep(backoff_s * 2 ** (n - 1))
    return outcomes


# ---------------------------------------------------------------- output checks (D92)
MAX_EVIDENCE = 200  # characters of a matched line kept as evidence
_ABS_PATH = re.compile(r"(?<![\w.~-])/(?:[^\s/\"':]+/)*[^\s/\"':]+")


def output_failure(patterns: Sequence[str], out: str, err: str, worktree: str | os.PathLike) -> str | None:
    """The first output line (stdout, then stderr) a `fail_if_output` regex matches, as evidence: worktree paths made
    relative, any other absolute path replaced by `<path>`, at most MAX_EVIDENCE characters. None: no match."""
    if not patterns:
        return None
    compiled = [re.compile(p) for p in patterns]
    wt = str(worktree).rstrip("/")
    for line in f"{out}\n{err}".split("\n"):
        if any(rx.search(line) for rx in compiled):
            shown = line.replace(wt + "/", "").replace(wt, ".")
            shown = _ABS_PATH.sub("<path>", shown).strip()
            return shown[:MAX_EVIDENCE]
    return None


# ---------------------------------------------------------------- the tool layer
@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: dict


def once_per_request(ctx):
    """The caller of ONE request, resolved once (the first time it is asked) and then fixed for that request: every
    check in a tool sees the same identity, and projmux is asked once."""
    resolve, memo = ctx.caller, []

    def caller() -> Caller:
        if not memo:
            memo.append(resolve())
        return memo[0]

    return replace(ctx, caller=caller)


def _error(code: str, message: str, hint: str | None = None, reason: str | None = None) -> ToolResult:
    error = {"code": code, "message": message}
    if reason:
        error["reason"] = reason
    if hint:
        error["hint"] = hint
    return ToolResult(False, {"ok": False, "error": error})


def tool_layer(
    name: str, args_type: type, fn: Callable[[Any, Any], dict]
) -> Callable[[Any, Mapping | None], ToolResult]:
    """Wraps a tool function: parse the arguments, resolve the caller once, run, and map every outcome to ONE shape. A
    refusal or a failure is an error RESULT (the model sees it and can adapt). Nothing but codes and fixed messages
    leaves the process: no env values, no paths, no tracebacks."""

    def run(ctx, arguments: Mapping[str, Any] | None) -> ToolResult:
        try:
            ctx = once_per_request(ctx)
            data = fn(ctx, from_json(args_type, dict(arguments or {})))
            return ToolResult(True, {"ok": True, **data})
        except RefusedError as exc:
            return _error("refused", exc.refusal.detail, reason=exc.refusal.code.value)
        except MeshError as exc:
            return _error(exc.code, str(exc), exc.hint)
        except Exception as exc:  # a bug: one stderr line, the error TYPE and a fixed id (a message can carry a path)
            log(f"internal error {type(exc).__name__} [E-TOOL]")
            return _error("internal", "internal error: see the MCP server log")

    run.__mesh_layers__ = TOOL_LAYERS
    run.__name__ = f"{name}_layered"
    return run
