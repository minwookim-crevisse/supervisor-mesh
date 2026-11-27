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
                      bound: the D85 name path or the D97/D141 proposal path), `consume` / `set_use_status` (the
                      single-use ledger), `propose`, `use_proposal`, `recover_attempting`
  retry               `retry`: bounded attempts with exponential backoff, only while the outcome is transient
  output checks       `output_failure`: a `fail_if_output` literal substring in a line of a passing command's output
                      (D92, D115: never a regex)
  JSON input          `parse_json`: THE parser of every JSON mesh reads (a tool call line, --json, a hook payload,
                      state.json, the logs, projmux output): one nesting-depth bound, never a RecursionError (F8)
  caller proof        `proven_caller` (D108 ancestry, D109 PM pin) for every non-read-only call; `pm_gate`
  operator proof      `transcript_prover` (D110 at cite time, D123): a candidate event is the latest real user
                      prompt of the PM transcript
  confirmation        `confirm` (D113, D121): a one-time code shown only in a tmux popup on the PM pane's client;
                      the plaintext crosses only a one-shot FIFO, the typed text comes back raw through another
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, NoReturn, Sequence, TextIO, TypeVar

from .model import (
    Caller,
    CallerKind,
    Doc,
    EventProof,
    EventUse,
    MeshError,
    NotInitialized,
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
    """Before mesh_init there is nowhere to record a refusal, and nothing is created. A .mesh/ that resolves outside
    the project (a symlink, D111) is never written either."""
    if store.dir.is_dir() and contained(store.dir.parent, store.dir.name):
        store.append_refusal(refusal)


def refuse(store: Store, refusal: Refusal) -> NoReturn:
    record_refusal(store, refusal)
    raise RefusedError(refusal)


def tool_refusal(
    subject: str, code: RefusalCode, detail: str, at: str, caller: Caller, item_id=None, rule: str | None = None
) -> Refusal:
    return Refusal(subject, code, detail, at, caller.kind.value, None, item_id, None, caller.agent_uid, rule)


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


Prover = Callable[[OperatorEvent, EventProof], "tuple[str, str] | None"]


def resolve_event(store: Store, event_id: str | None, prove: Prover | None = None) -> OperatorEvent | None:
    """THE lookup of a cited operator event: only hook-recorded, genuine events (`Store.get_event`). D123: a CANDIDATE
    is proven here, at its first citation: `prove` (`transcript_prover`) reads the transcript and answers
    ("entry", entry hash) | ("failed", code) | None (could not run: projmux unavailable; nothing is marked). The
    result is committed under the lock on the fresh document by `settle_candidate` (newest candidate, entry ledger);
    `bind_event` then refuses an unproven event. Without `prove` a candidate stays unproven (fail closed)."""
    event = store.get_event(event_id) if event_id else None
    if event is None or prove is None:
        return event
    proof = store.read().doc.event_proofs.get(str(event.seq))
    if proof is None or proof.status != "candidate":
        return event
    outcome = prove(event, proof)
    if outcome is not None:
        with store.locked() as txn:
            doc = settle_candidate(txn.doc, event, outcome)
            if doc is not txn.doc:
                txn.commit(doc)
    return event


def settle_candidate(doc: Doc, event: OperatorEvent, outcome: tuple[str, str]) -> Doc:
    """D123 on the fresh document: a candidate becomes proven only when it is still a candidate, it is the NEWEST
    candidate of its session (rule 4) and its entry has backed no other event (rule 5: the entry ledger, one usable
    event per real transcript entry); else it is failed for good."""
    key = str(event.seq)
    proof = doc.event_proofs.get(key)
    if proof is None or proof.status != "candidate":
        return doc
    kind, value = outcome
    if kind == "entry":
        newer = any(
            p.session_sha256 == proof.session_sha256 and int(k) > event.seq for k, p in doc.event_proofs.items()
        )
        if newer:
            kind, value = "failed", "newer_candidate"
        elif value in doc.transcript_entries:
            kind, value = "failed", "transcript_entry_reused"
    if kind == "entry":
        proofs = {**doc.event_proofs, key: replace(proof, status="proven")}
        return replace(doc, event_proofs=proofs, transcript_entries={**doc.transcript_entries, value: event.seq})
    return replace(doc, event_proofs={**doc.event_proofs, key: replace(proof, status="failed", why=value)})


@dataclass(frozen=True, slots=True)
class Bound:
    event: OperatorEvent
    via: str  # "event" (no binding asked) | "name" | "proposal"
    proposal_id: str | None = None


@dataclass(frozen=True, slots=True)
class Unbound:
    code: RefusalCode
    detail: str
    rule: str | None = None  # D141: the failing rule, also at the end of `detail` (a fixed code, never prompt text)


def _unbound(code: RefusalCode, detail: str, rule: str) -> Unbound:
    return Unbound(code, with_rule(detail, rule), rule)


def bind_event(
    doc: Doc,
    event: OperatorEvent | None,
    *,
    fresh: bool,
    tokens: Sequence[str] | None = None,
    proposal: tuple[str, str] | None = None,
    ttl_s: float = 600,
    unbound: str = "the operator event does not name what it authorises",
) -> Bound | Unbound:
    """THE operator-event binding. In order: the event exists (hook-recorded), is unused (single use, D63), is proven
    when it is a candidate (D123: `resolve_event` ran the proof), is the newest operator prompt when `fresh` (by mesh
    seq, D74), and, when `tokens` is given, is bound:
      name path      the event text names one of `tokens` as a whole token (`names`)
      proposal path  `proposal` = (role, exact text), D97 as amended by D141: the event text holds exactly one
                     proposal id `P-<n>` (a whole token, case-insensitive) and at most PROMPT_MAX characters, and
                     that NAMED proposal has exactly that role and text, is unused, was stored before the event and
                     at most `ttl_s` before the event's record time (a proposal without `at` is expired). Any other
                     prompt, a "no" included, approves nothing.
    A refusal carries the failing rule (D141), a fixed code."""
    if event is None:
        return Unbound(RefusalCode.EVENT_UNKNOWN, "no operator event with that id was recorded by the PM-pane hook")
    if event.id in doc.event_uses:
        return Unbound(RefusalCode.EVENT_REUSED, f"operator event {event.id} was already used")
    proof = doc.event_proofs.get(str(event.seq))
    if proof is not None and proof.status != "proven":  # D123: a candidate needs its transcript proof
        if proof.why == "transcript_entry_reused":
            return Unbound(RefusalCode.EVENT_REUSED, "its transcript entry already backed another operator event")
        return _unbound(
            RefusalCode.EVENT_UNPROVEN,
            "the operator event is not backed by the latest real user prompt of the PM transcript (D110, D123)",
            proof.why or "proof_not_run",
        )
    if fresh and event.seq != doc.last_operator_seq:
        return _unbound(
            RefusalCode.EVENT_STALE, "a newer operator prompt was recorded after it: cite the latest one", "stale"
        )
    if tokens is None:
        return Bound(event, "event")
    if any(names(event.text, t) for t in tokens):
        return Bound(event, "name")
    ids = proposal_ids(event.text) if proposal is not None else []
    rule = _approval_rule(doc, event, proposal, ids, ttl_s) if ids else "not_named"
    if rule is None:
        return Bound(event, "proposal", ids[0])
    return _unbound(RefusalCode.EVENT_NOT_BOUND, unbound, rule)


PROMPT_MAX = 80  # D141: the characters of an approving prompt (a P-id inside a long message approves nothing)
_PID = re.compile(rf"(?<![{_TOKEN}])[Pp]-([0-9]+)(?![A-Za-z0-9_:-])(?!\.+[A-Za-z0-9_])")


def proposal_ids(event_text: str) -> list[str]:
    """D97 + D141: every proposal id token `P-<n>` in the text, case-insensitive (`p-205` is `P-205`), in order."""
    return [f"P-{m.group(1)}" for m in _PID.finditer(event_text)]


def _approval_rule(doc: Doc, event: OperatorEvent, proposal, ids: list[str], ttl_s: float) -> str | None:
    """D141: None when the event approves the proposal it names, else the failing rule."""
    if len(ids) != 1:
        return "more_than_one_id"
    if len(event.text) > PROMPT_MAX:
        return "prompt_too_long"
    prop = doc.proposals.get(ids[0])
    if prop is None or (prop.role, prop.command) != proposal:
        return "not_named"
    if prop.used:
        return "proposal_used"
    if prop.seq >= event.seq:
        return "not_first_after_proposal"  # the prompt came before the proposal
    stored, recorded = _utc(prop.at), _utc(event.at)
    if stored is None or recorded is None or (recorded - stored).total_seconds() > ttl_s:
        return "proposal_expired"
    return None


def use_proposal(doc: Doc, proposal_id: str) -> Doc:
    """D141: mark the approved proposal used, and with it every other unused proposal of the same role and command
    (one approval never leaves a sibling to approve the same keypress again)."""
    prop = doc.proposals[proposal_id]
    used = {
        k: replace(p, used=True)
        for k, p in doc.proposals.items()
        if k == proposal_id or (not p.used and (p.role, p.command) == (prop.role, prop.command))
    }
    return replace(doc, proposals={**doc.proposals, **used})


def consume(doc: Doc, event: OperatorEvent, kind: str, ref: str, status: str | None = None) -> Doc:
    """Mark the event used (the single-use ledger shared by reply, override, pane_command and companions_install)."""
    return replace(doc, event_uses={**doc.event_uses, event.id: EventUse(event.id, kind, ref, event.seq, status)})


def set_use_status(doc: Doc, event_id: str, status: str) -> Doc:
    return replace(doc, event_uses={**doc.event_uses, event_id: replace(doc.event_uses[event_id], status=status)})


def propose(store: Store, role: str, command: str, by_uid: str | None, at: str) -> Proposal:
    """D85 proposal path: the exact text under the next mesh seq, id `P-<seq>` (D97: the approval must name it),
    stored with its UTC time `at` (D141: the start of its proposal_ttl_s)."""
    with store.locked() as txn:
        seq = txn.doc.seq + 1
        prop = Proposal(f"P-{seq}", seq, role, command, by_uid, at=at)
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


def poll_until(
    check: Callable[[], bool],
    timeout_s: float,
    step_s: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """True as soon as `check()` holds, checked at once and then every `step_s`; False once `timeout_s` has passed
    without it (D125). `check` may raise: the caller handles it."""
    deadline = clock() + timeout_s
    while not check():
        if clock() >= deadline:
            return False
        sleep(step_s)
    return True


# ---------------------------------------------------------------- output checks (D92)
MAX_EVIDENCE = 200  # characters of a matched line kept as evidence
_ABS_PATH = re.compile(r"(?<![\w.~-])/(?:[^\s/\"':]+/)*[^\s/\"':]+")


MAX_PATTERN = 1000  # characters of one fail_if_output literal (D115)


def output_failure(patterns: Sequence[str], out: str, err: str, worktree: str | os.PathLike) -> str | None:
    """The first output line (stdout, then stderr) containing a `fail_if_output` entry as a LITERAL substring (D115:
    `in`, never a regex: a config-supplied pattern cannot stall the server), as evidence: worktree paths made
    relative, any other absolute path replaced by `<path>`, at most MAX_EVIDENCE characters. None: no match."""
    if not patterns:
        return None
    wt = str(worktree).rstrip("/")
    for line in f"{out}\n{err}".split("\n"):
        if any(p in line for p in patterns):
            shown = line.replace(wt + "/", "").replace(wt, ".")
            shown = _ABS_PATH.sub("<path>", shown).strip()
            return shown[:MAX_EVIDENCE]
    return None


# ---------------------------------------------------------------- JSON input (F8)
MAX_JSON_DEPTH = 64  # state.json nests about 10 deep; anything near the interpreter's recursion limit is hostile
_JSON_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[\[\]{}]')


class JsonTooDeep(ValueError):  # noqa: N818
    """Nested deeper than MAX_JSON_DEPTH: refused before the parser could recurse (a ValueError like bad JSON)."""


def parse_json(text: str | bytes, max_depth: int = MAX_JSON_DEPTH) -> Any:
    """THE JSON parser (D95): the depth is counted on the raw text first (string contents skipped), so a hostile
    nesting is a ValueError, never a RecursionError or a traceback."""
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    depth = 0
    for m in _JSON_TOKEN.finditer(text):
        tok = m.group()
        if tok in "[{":
            depth += 1
            if depth > max_depth:
                raise JsonTooDeep(f"JSON nested deeper than {max_depth}")
        elif tok in "]}":
            depth -= 1
    return json.loads(text)


MAX_LINE = 4 * 1024 * 1024  # bytes of one JSON-RPC line


def bounded_lines(reader: Any, max_bytes: int = MAX_LINE) -> Iterable[bytes | None]:
    """The protocol's lines, each at most `max_bytes`; a longer one is consumed to its newline and yields None (the
    caller answers a parse error), so one hostile line neither fills memory nor ends the server."""
    while True:
        line = reader.readline(max_bytes + 1)
        if not line:
            return
        if len(line) > max_bytes and not line.endswith(b"\n"):
            while line and not line.endswith(b"\n"):
                line = reader.readline(max_bytes)
            yield None
            continue
        yield line


# ---------------------------------------------------------------- write containment (D111)
def contained(root: str | os.PathLike, rel: str | os.PathLike) -> bool:
    """`rel` under `root` resolves (realpath: every symlink on the way followed, a dangling one too) inside `root`.
    THE check before every mesh_init and tracker write: a symlinked .claude/, .mesh/, docs/ or file is refused."""
    base = os.path.realpath(root)
    real = os.path.realpath(os.path.join(base, rel))
    return real == base or real.startswith(base + os.sep)


def contained_file(root: str | os.PathLike, rel: str | os.PathLike) -> RefusalCode | None:
    """None when `rel` resolves (realpath) to a regular file inside `root` (D117 (4): the work order `work_dispatch`
    points at); else OUTSIDE_ROOT (it resolves outside, a symlink included) or WORK_ORDER_MISSING (no such file)."""
    if not contained(root, rel):
        return RefusalCode.OUTSIDE_ROOT
    real = os.path.realpath(os.path.join(os.path.realpath(root), rel))
    return None if os.path.isfile(real) else RefusalCode.WORK_ORDER_MISSING


# ---------------------------------------------------------------- caller proof (D108, D109)
ANCESTRY_WHY = (
    "this process does not descend from the claude process of the pane its environment names (D108): run mesh from "
    "that pane's own Claude session"
)
PM_AMBIGUOUS_WHY = (
    "more than one live role=pm agent in this project (D109): the operator removes the role=pm label from every agent "
    "but the PM (or stops it), then retry"
)
NOT_PINNED_WHY = (
    "this project's state.json has no pinned PM (D109): the operator has the PM run mesh_init (dry run, then apply) "
    "from the PM pane to pin it"
)
OTHER_PM_WHY = (
    "this pane is not the pinned PM (D109). To move the pin: stop the pinned PM, the operator types `re-pin <this "
    "agent's uid>` in this pane, then this PM runs mesh_init apply and the operator confirms the popup code (D114)"
)


PM_GATE_RULES = {PM_AMBIGUOUS_WHY: "pm_ambiguous", NOT_PINNED_WHY: "pm_not_pinned", OTHER_PM_WHY: "not_pinned_pm"}
# D141: the fixed rule of each `pm_gate` answer (the hook_error codes of the same cases)


def caller_rule(caller: Caller) -> str:
    """D141: why `caller` is not the PM, as the fixed code a caller_not_pm refusal carries: an UNKNOWN caller's own
    rule (resolve, ancestry or pm_gate) or `unknown_caller` (an injected UNKNOWN without one); any other caller
    `not_pm_role`."""
    if caller.kind is CallerKind.UNKNOWN:
        return caller.rule or "unknown_caller"
    return "not_pm_role"


def with_rule(detail: str, rule: str) -> str:
    return f"{detail} (rule: {rule})"


def pm_gate(store: Store, agent_uid: str | None, live_pms: int, *, unpinned_ok: bool = False) -> str | None:
    """D109: why a role=pm caller may NOT act as the PM, or None. Exactly one live role=pm agent, and it is the pinned
    one. `unpinned_ok` (mesh_init only): the pin itself is decided by mesh_init (first pin, migration, re-pin)."""
    if live_pms != 1:
        return PM_AMBIGUOUS_WHY
    if unpinned_ok:
        return None
    try:
        doc = store.read().doc
    except NotInitialized:
        return None  # no project state yet: nothing but mesh_init can act, and mesh_init pins
    if doc.pm_uid is None:
        return NOT_PINNED_WHY
    return None if doc.pm_uid == agent_uid else OTHER_PM_WHY


def proven_caller(resolve: Callable[[], Caller], store: Store, tool: str | None = None) -> Caller:
    """The caller of a NON-read-only call. A production resolver (`identity.PaneCaller`) is checked: the calling
    process descends from the named pane's claude process (D108; no claude pid = fail closed) and a PM passes
    `pm_gate` (D109). A failed check is UNKNOWN with a `why` the refusal shows and its fixed `rule` (D141). An
    injected caller (tests) is taken as given."""
    resolved = getattr(resolve, "resolved", None)
    if resolved is None:
        return resolve()
    from . import identity  # identity -> projmux -> layers: imported at call time

    r = resolved()
    caller = r.caller
    if caller.kind is CallerKind.UNKNOWN:
        return caller
    if not identity.descends_from(os.getpid(), r.claude_pid):
        return Caller.unknown(ANCESTRY_WHY, "not_descendant")
    if caller.kind is CallerKind.PM:
        why = pm_gate(store, caller.agent_uid, r.live_pms, unpinned_ok=tool == "mesh_init")
        if why:
            return Caller.unknown(why, PM_GATE_RULES[why])
    return caller


# ---------------------------------------------------------------- operator proof (D110)
TRANSCRIPT_TAIL = 8 * 1024 * 1024  # bytes read from the end of the transcript: the latest prompt is near the end
TRANSCRIPT_LINE_MAX = 1024 * 1024  # a longer line is skipped unparsed
_SESSION = re.compile(r"[A-Za-z0-9_-]{1,128}")


def transcript_dirs(root: str | os.PathLike, env: Mapping[str, str]) -> set[str]:
    """The user's Claude projects directory for this project (realpath): `<config>/projects/<slug>`, the slug being
    the project path with every character outside [A-Za-z0-9] replaced by '-' (as given and as resolved)."""
    base = env.get("CLAUDE_CONFIG_DIR") or (os.path.join(env["HOME"], ".claude") if env.get("HOME") else None)
    if not base:
        return set()
    paths = {str(root), os.path.realpath(root)}
    return {os.path.realpath(os.path.join(base, "projects", re.sub(r"[^A-Za-z0-9]", "-", p))) for p in paths}


def _prompt_text(entry: Any) -> str | None:
    """The text of a REAL operator prompt entry, else None. Structural and positive (D123, the Claude Code 2.1.327
    shapes): type user, isMeta not true, not a sidechain, origin.kind == "human" (required: no origin or no kind is not
    a prompt; peer messages, task notifications, auto-continuations and any unknown kind are not), and the content a
    string or text-only blocks (never a tool_result). Never decided by the text. D141 (F2): a prompt typed while the
    PM was busy is an `attachment` entry (`_queued_prompt`)."""
    if isinstance(entry, dict) and entry.get("type") == "attachment":
        return _queued_prompt(entry)
    if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("isMeta") is True:
        return None
    if entry.get("isSidechain") is True:
        return None
    origin = entry.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != "human":
        return None
    msg = entry.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content and all(isinstance(b, dict) and b.get("type") == "text" for b in content):
        texts = [b.get("text") for b in content]
        return "\n".join(texts) if all(isinstance(t, str) for t in texts) else None
    return None


def _queued_prompt(entry: dict) -> str | None:
    """D141 (F2): a queued operator prompt: `attachment.type == "queued_command"`, the origin INSIDE the attachment
    with kind human, `humanTurn` exactly true, `commandMode == "prompt"`, isMeta not true (attachment or entry), not
    a sidechain, and `attachment.prompt` a string. Peer deliveries (origin peer, isMeta) and task notifications are
    not prompts."""
    att = entry.get("attachment")
    if entry.get("isSidechain") is True or entry.get("isMeta") is True or not isinstance(att, dict):
        return None
    origin = att.get("origin")
    if att.get("type") != "queued_command" or not isinstance(origin, dict) or origin.get("kind") != "human":
        return None
    if att.get("humanTurn") is not True or att.get("commandMode") != "prompt" or att.get("isMeta") is True:
        return None
    prompt = att.get("prompt")
    return prompt if isinstance(prompt, str) else None


_SLASH_TAG = re.compile(
    r"<command-message>[^\n]*?</command-message>\n<command-name>/([A-Za-z0-9_][A-Za-z0-9_.:-]*)</command-name>"
    r"(?:\n<command-args>(.*?)</command-args>)?",
    re.DOTALL,
)


def _slash_form(text: str, prompt: str) -> bool:
    """D141 (F1): a human slash prompt is written to the transcript in tag form; when the WHOLE entry text
    fullmatches it and the hook prompt starts with `/`, it equals the hook prompt as `/NAME ARGS`, whitespace
    collapsed on both sides. The reversed tag order and any other text around the tags are not this form."""
    m = _SLASH_TAG.fullmatch(text) if prompt.startswith("/") else None
    if m is None:
        return False
    return " ".join(f"/{m.group(1)} {m.group(2) or ''}".split()) == " ".join(prompt.split())


def _latest_prompt_entry(path: str, prompt: str) -> tuple[str, Any] | None:
    """(uuid, timestamp) of the LATEST real prompt entry when its text equals `prompt`, else None. Streams the tail
    line by line; keeps only (uuid, equal?, timestamp) of the newest prompt seen, never a line's text."""
    latest: tuple[str | None, bool, Any] | None = None
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size > TRANSCRIPT_TAIL:
            fh.seek(size - TRANSCRIPT_TAIL)
            fh.readline()  # a partial line
        while True:
            line = fh.readline(TRANSCRIPT_LINE_MAX + 1)
            if not line:
                break
            if len(line) > TRANSCRIPT_LINE_MAX and not line.endswith(b"\n"):
                while line and not line.endswith(b"\n"):  # skip the rest of an oversized line, unparsed
                    line = fh.readline(TRANSCRIPT_LINE_MAX)
                continue
            try:
                entry = parse_json(line)
            except ValueError:
                continue
            text = _prompt_text(entry)
            if text is not None:
                uuid = entry.get("uuid")
                same = text == prompt or (entry.get("type") == "user" and _slash_form(text, prompt))
                latest = (uuid if isinstance(uuid, str) and uuid else None, same, entry.get("timestamp"))
    if latest is None or not latest[1] or latest[0] is None:
        return None
    return latest[0], latest[2]


def session_proof(
    payload: Mapping[str, Any], live_session: Callable[[str], tuple[str | None, str | None]], agent_uid: str
) -> str | None:
    """D118: None when the hook's `session_id` equals the agent's live session id from projmux and its
    `transcript_path` resolves (realpath) to projmux's transcript path; else a hook_error code. No session id or no
    transcript path from projmux, or projmux unavailable, fails closed. Neither value is returned, logged or kept."""
    try:
        live_id, live_path = live_session(agent_uid)
    except MeshError:
        return "projmux_unavailable"
    session, path = payload.get("session_id"), payload.get("transcript_path")
    if not live_id or not live_path or not isinstance(session, str) or not isinstance(path, str) or not path:
        return "session_not_pm"
    if not hmac.compare_digest(session.encode("utf-8"), live_id.encode("utf-8")):
        return "session_not_pm"
    return None if os.path.realpath(path) == os.path.realpath(live_path) else "session_not_pm"


CITE_SKEW_S = 5.0  # D123: the entry may be stamped this much before the candidate's record time (a binding rule)


def _utc(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo is not None else None


def _transcript_file(session: str, path: str, root: str | os.PathLike, env: Mapping[str, str]) -> str | None:
    """The transcript path (realpath) when it is a regular file `<session>.jsonl` directly under this project's
    Claude projects directory, else None."""
    if not _SESSION.fullmatch(session) or not path:
        return None
    real = os.path.realpath(path)
    if os.path.dirname(real) not in transcript_dirs(root, env) or os.path.basename(real) != f"{session}.jsonl":
        return None
    try:
        return real if stat.S_ISREG(os.stat(real).st_mode) else None
    except OSError:
        return None


def session_sha256(session: str) -> str:
    return hashlib.sha256(session.encode("utf-8")).hexdigest()


def transcript_prover(
    root: str | os.PathLike, env: Mapping[str, str], live_session: Callable[[str], tuple[str | None, str | None]]
) -> Prover:
    """D110 at cite time (D123). For a candidate: projmux's live session of the recording PM agent must still be the
    one the hook saw (by hash), its transcript a regular file in this project's Claude projects dir, and the LATEST
    real operator prompt there (`_prompt_text`) must equal the event text and be stamped no earlier than the record
    time minus CITE_SKEW_S. Answers ("entry", sha256(session:uuid)) | ("failed", code) | None (projmux unavailable or
    the transcript unreadable: nothing is marked). Nothing from the transcript is kept, logged or returned but that
    hash."""

    def prove(event: OperatorEvent, proof: EventProof) -> tuple[str, str] | None:
        if not event.agent_uid:
            return "failed", "session_not_pm"
        try:
            session, path = live_session(event.agent_uid)
        except MeshError:
            return None
        if not session or not path:
            return None
        if not hmac.compare_digest(session_sha256(session), proof.session_sha256):
            return "failed", "session_not_pm"
        real = _transcript_file(session, path, root, env)
        if real is None:
            return "failed", "transcript_refused"
        try:
            found = _latest_prompt_entry(real, event.text)
        except OSError:
            return None
        if found is None:
            return "failed", "prompt_not_in_transcript"
        uuid, stamped = found
        entry_at, recorded = _utc(stamped), _utc(event.at)
        if entry_at is None or recorded is None or entry_at < recorded - timedelta(seconds=CITE_SKEW_S):
            return "failed", "prompt_too_old"
        return "entry", hashlib.sha256(f"{session}:{uuid}".encode("utf-8")).hexdigest()

    return prove


# ---------------------------------------------------------------- confirmation (D113)
PRIVILEGED_COMMANDS = frozenset(  # D113 + D122: a fixed list, matched case-folded (no alias table)
    {"permissions", "config", "login", "logout", "mcp", "plugin", "hooks", "allowed-tools", "settings", "plugins",
     "permission"}
)  # fmt: skip
CONFIRM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
CONFIRM_LEN = 8
CONFIRM_HELPER = Path(__file__).resolve().with_name("confirm.py")
CONFIRM_JOIN_S = 15.0  # after the dir is gone: how long to wait for the popup to close
CONFIRM_RETURN_S = 5.0  # D124: after an answer arrived, how long past the deadline the popup may take to return


CONFIRM_DIR_VAR = "MESH_CONFIRM_DIR"  # the popup's env: the work dir (a path, not a secret; never in the helper argv)
CONFIRM_MAGIC = "mesh-confirm/1"
CONFIRM_NO_CODE = "mesh-confirm/no-code"  # D124: the popup's helper got no code (the third outcome)
CONFIRM_POLL_S = 0.05
CONFIRM_MAX_ANSWER = 256  # bytes: a longer answer is malformed


CONFIRM_PREFIX = "mesh-confirm-"  # <system temp>/mesh-confirm-*: one confirmation's work dir (D121)
OWNER_FILE = "owner"  # beside a mesh temp dir's content: mesh's marker and the owner pid (D122)
OWNER_TAG = "supervisor-mesh owner"


def mark_owned(parent: str | os.PathLike, pid: int | None = None) -> None:
    """Mark a fresh mesh temp dir as mesh's and name its owner process (the reaper leaves a live owner alone)."""
    fd = os.open(os.path.join(parent, OWNER_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(f"{OWNER_TAG} {os.getpid() if pid is None else pid}\n")


def marked_owner(parent: str | os.PathLike) -> int | None:
    """The owner pid of a dir carrying mesh's marker (a regular `owner` file of the right shape), else None."""
    path = os.path.join(parent, OWNER_FILE)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        with open(path, encoding="ascii") as fh:
            text = fh.read(128)
    except (OSError, UnicodeDecodeError):
        return None
    tag, _, pid = text.strip().rpartition(" ")
    return int(pid) if tag == OWNER_TAG and pid.isascii() and pid.isdigit() and len(pid) < 10 else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


def dead_mesh_dir(path: str | os.PathLike, prefix: str) -> bool:
    """THE reaper rule (D122, both the verify worktrees and the confirm dirs): `path` is exactly
    `<system temp>/<prefix>*`, a real dir (no symlink) owned by this uid with mode 0700, carries mesh's marker, and
    its owner process is gone. Anything else, a live owner above all, is left alone."""
    path = Path(path)
    if not path.name.startswith(prefix):
        return False
    if os.path.realpath(path.parent) != os.path.realpath(tempfile.gettempdir()):
        return False
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        return False
    owner = marked_owner(path)
    return owner is not None and not _pid_alive(owner)


def reap_confirm_dirs() -> int:
    """Recovery: remove the confirm work dirs a killed mesh left behind (`dead_mesh_dir`), each by its exact path."""
    tmp = tempfile.gettempdir()
    removed = 0
    try:
        names = os.listdir(tmp)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(tmp, name)
        if name.startswith(CONFIRM_PREFIX) and dead_mesh_dir(path, CONFIRM_PREFIX):
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def _proc_holders(*fifos: os.stat_result) -> list[set[int]] | None:
    """For each FIFO, the pids (other than this process) holding it open, from /proc; None where there is no /proc."""
    if not os.path.isdir("/proc/self/fd"):
        return None
    me, want = os.getpid(), [(f.st_dev, f.st_ino) for f in fifos]
    held: list[set[int]] = [set() for _ in fifos]
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                st = os.stat(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            for i, w in enumerate(want):
                if key == w:
                    held[i].add(int(pid))
    return held


def _is_helper(pid: int) -> bool:
    """The pid is alive and runs the confirm helper (its argv[1], from /proc where there is one)."""
    try:
        os.kill(pid, 0)
    except (OSError, OverflowError):
        return False
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except FileNotFoundError:
        return not os.path.isdir("/proc/self")
    except OSError:
        return False
    return len(argv) > 1 and argv[1] == str(CONFIRM_HELPER).encode()


def _confirm_work(work: str) -> tuple[str, str] | None:
    """The fresh work dir: a 0700 dir of ours, empty; then mesh's marker (D122) and the two FIFOs made here (O_EXCL and
    mkfifo refuse an existing name)."""
    st = os.lstat(work)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        return None
    if os.listdir(work):
        return None
    mark_owned(work)
    code_p, answer_p = os.path.join(work, "code"), os.path.join(work, "answer")
    try:
        os.mkfifo(code_p, 0o600)
        os.mkfifo(answer_p, 0o600)
    except FileExistsError:
        return None
    return code_p, answer_p


def _same_fifo(path: str, want: os.stat_result) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISFIFO(st.st_mode) and (st.st_dev, st.st_ino) == (want.st_dev, want.st_ino)


def _parse(line: bytes) -> tuple[str, int, str] | None:
    """One answer line: (`typed`, pid, raw typed text) for `<magic> <pid> <typed>`, (`no_code`, pid, "") for
    `<no-code magic> <pid>` (D124), else None (malformed)."""
    try:
        parts = line.decode("utf-8").split(" ", 2)
    except UnicodeDecodeError:
        return None
    if len(parts) == 3 and parts[0] == CONFIRM_MAGIC:
        kind = "typed"
    elif len(parts) == 2 and parts[0] == CONFIRM_NO_CODE:
        kind = "no_code"
    else:
        return None
    pid_s = parts[1]
    if not (pid_s.isascii() and pid_s.isdigit()) or len(pid_s) > 9:
        return None
    return kind, int(pid_s), parts[2] if kind == "typed" else ""


def _received(pid: int, answer: os.stat_result) -> str | None:
    """D121, checked the moment mesh reads the answer: it comes from a live helper pid that alone holds `answer`."""
    held = _proc_holders(answer)
    holders = None if held is None else held[0]
    if holders is not None and holders - {pid}:
        return "second_writer"
    if not _is_helper(pid) or (holders is not None and pid not in holders):
        return "popup_gone"
    return None


def _judge(typed: str, want: str) -> str | None:
    typed = typed.strip().upper()
    if not typed:
        return "none"
    return None if hmac.compare_digest(hashlib.sha256(typed.encode("utf-8")).hexdigest(), want) else "wrong"


class _Watch:
    """D124 (b): every process other than mesh seen holding `code` or `answer`, on any poll, ever; and the helper the
    code was handed to (`bound`: the sole other holder of `code` when mesh wrote it, running the helper)."""

    def __init__(self) -> None:
        self.seen: set[int] = set()
        self.bound: int | None = None
        self.proc = os.path.isdir("/proc/self/fd")

    def look(self, *fifos: os.stat_result) -> list[set[int]] | None:
        held = _proc_holders(*fifos)
        for pids in held or ():
            self.seen |= pids
        return held

    def crowded(self) -> bool:
        return len(self.seen) > 1


def confirm(
    popup: Callable[[list[str], Mapping[str, str], float], bool],
    timeout_s: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    """D113/D121/D124: None when the operator typed the one-time code into the popup, else why not (unavailable |
    none | wrong | timeout | tampered | second_writer | popup_gone | malformed | no_code | no_answer). The code is made
    here (`secrets`) and only its sha256 is kept; the plaintext reaches the popup helper once through the FIFO `code`
    in a fresh 0700 dir (never a regular file, never an argv: the helper gets the dir in its env); the operator's RAW
    typed text comes back through the FIFO `answer`, which mesh holds open for reading, and mesh hashes and compares
    it. One answer, only while the helper pid lives and alone holds `answer`; a pre-existing or planted entry, a second
    writer or a second write is refused; the dir is removed at once. D124: mesh judges only after the popup it started
    has returned (every line that arrives before that is read and counted); more than one process ever holding `code`
    or `answer` is refused (the code is withheld when two hold `code`); the popup's helper reporting `no code` is
    refused; only the helper the code was handed to can approve. A same-user process can still race the FIFOs (D96)."""
    work = tempfile.mkdtemp(prefix=CONFIRM_PREFIX)
    afd, runner_thread = None, None
    try:
        made = _confirm_work(work)
        if made is None:
            return "tampered"
        code_p, answer_p = made
        code = "".join(secrets.choice(CONFIRM_ALPHABET) for _ in range(CONFIRM_LEN)).encode("ascii")
        want = hashlib.sha256(code).hexdigest()
        afd = os.open(answer_p, os.O_RDONLY | os.O_NONBLOCK)
        answer, code_st = os.fstat(afd), os.lstat(code_p)
        if not _same_fifo(answer_p, answer) or not stat.S_ISFIFO(code_st.st_mode):
            return "tampered"
        returned: list[bool] = []
        argv = [sys.executable, str(CONFIRM_HELPER), str(int(timeout_s))]
        runner_thread = threading.Thread(
            target=lambda: returned.append(bool(popup(argv, {CONFIRM_DIR_VAR: work}, timeout_s))), daemon=True
        )
        deadline = clock() + timeout_s
        runner_thread.start()
        watch, buf, got = _Watch(), b"", None  # got: (pid, typed) of the one answer, checked as it arrived
        while True:
            sleep(CONFIRM_POLL_S)
            done = bool(returned)  # sampled before the read: whatever the popup's helper wrote is in the FIFO by now
            if sorted(os.listdir(work)) != ["answer", "code", OWNER_FILE] or not (
                _same_fifo(answer_p, answer) and _same_fifo(code_p, code_st)
            ):
                return "tampered"
            watch.look(answer, code_st)
            if watch.crowded():
                return "second_writer"
            if code:  # written once, as soon as a reader has the FIFO open
                try:
                    cfd = os.open(code_p, os.O_WRONLY | os.O_NONBLOCK)
                except OSError:  # ENXIO: nobody reads yet
                    cfd = None
                if cfd is not None:
                    try:
                        held = watch.look(code_st)  # the reader(s) the code is about to reach
                        if watch.crowded():
                            return "second_writer"  # withheld: two processes wait for it
                        if held is not None and len(held[0]) == 1:
                            pid = next(iter(held[0]))
                            watch.bound = pid if _is_helper(pid) else None
                        if _same_fifo(code_p, os.fstat(cfd)):
                            os.write(cfd, code + b"\n")
                    finally:
                        os.close(cfd)
                    code = b""
            try:
                buf += os.read(afd, CONFIRM_MAX_ANSWER + 1)
            except BlockingIOError:
                pass
            if len(buf) > CONFIRM_MAX_ANSWER:
                return "malformed"
            if got is not None and buf:
                return "second_writer"  # anything after the one answer, at any time before the popup returned
            if b"\n" in buf:
                try:
                    buf += os.read(afd, CONFIRM_MAX_ANSWER + 1)  # anything already behind the first line
                except BlockingIOError:
                    pass
                line, _, rest = buf.partition(b"\n")
                if rest:
                    return "second_writer"
                parsed = _parse(line)
                if parsed is None:
                    return "malformed"
                kind, pid, typed = parsed
                if kind == "no_code":
                    return "no_code"
                why = _received(pid, answer)
                if why is not None:
                    return why
                watch.seen.add(pid)
                if watch.crowded():
                    return "second_writer"
                got, buf = (pid, typed), b""
            if done:
                if buf:
                    return "malformed"  # a partial line and its writer gone
                if not returned[0]:
                    return "unavailable"
                if got is None:
                    return "no_answer"
                if watch.proc and got[0] != watch.bound:  # not the helper the code was handed to
                    return "popup_gone"
                return _judge(got[1], want)
            if clock() >= deadline + (CONFIRM_RETURN_S if got is not None else 0):
                return "timeout"
    finally:
        if afd is not None:
            os.close(afd)
        shutil.rmtree(work, ignore_errors=True)
        if runner_thread is not None:
            runner_thread.join(CONFIRM_JOIN_S)


# ---------------------------------------------------------------- the tool layer
@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: dict


def once_per_request(ctx, *, read_only: bool = False, tool: str | None = None):
    """The caller of ONE request, resolved once (the first time it is asked) and then fixed for that request: every
    check in a tool sees the same identity, and projmux is asked once. A non-read-only request (the default: fail
    closed) gets the proven caller (`proven_caller`, D108/D109); a read-only one the pane's identity as before."""
    resolve, memo = ctx.caller, []

    def caller() -> Caller:
        if not memo:
            memo.append(resolve() if read_only else proven_caller(resolve, ctx.store, tool))
        return memo[0]

    return replace(ctx, caller=caller)


def _error(
    code: str, message: str, hint: str | None = None, reason: str | None = None, rule: str | None = None
) -> ToolResult:
    error = {"code": code, "message": message}
    if reason:
        error["reason"] = reason
    if rule:
        error["rule"] = rule  # D141: the failing binding or proof rule
    if hint:
        error["hint"] = hint
    return ToolResult(False, {"ok": False, "error": error})


def tool_layer(
    name: str, args_type: type, fn: Callable[[Any, Any], dict], read_only: bool = False
) -> Callable[[Any, Mapping | None], ToolResult]:
    """Wraps a tool function: parse the arguments, resolve the caller once, run, and map every outcome to ONE shape. A
    refusal or a failure is an error RESULT (the model sees it and can adapt). Nothing but codes and fixed messages
    leaves the process: no env values, no paths, no tracebacks."""

    def run(ctx, arguments: Mapping[str, Any] | None) -> ToolResult:
        try:
            ctx = once_per_request(ctx, read_only=read_only, tool=name)
            data = fn(ctx, from_json(args_type, dict(arguments or {})))
            return ToolResult(True, {"ok": True, **data})
        except RefusedError as exc:
            return _error("refused", exc.refusal.detail, reason=exc.refusal.code.value, rule=exc.refusal.rule)
        except MeshError as exc:
            return _error(exc.code, str(exc), exc.hint)
        except Exception as exc:  # a bug: one stderr line, the error TYPE and a fixed id (a message can carry a path)
            log(f"internal error {type(exc).__name__} [E-TOOL]")
            return _error("internal", "internal error: see the MCP server log")

    run.__mesh_layers__ = TOOL_LAYERS
    run.__name__ = f"{name}_layered"
    return run
