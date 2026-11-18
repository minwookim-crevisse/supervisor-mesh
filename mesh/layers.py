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
  output checks       `output_failure`: a `fail_if_output` literal substring in a line of a passing command's output
                      (D92, D115: never a regex)
  JSON input          `parse_json`: THE parser of every JSON mesh reads (a tool call line, --json, a hook payload,
                      state.json, the logs, projmux output): one nesting-depth bound, never a RecursionError (F8)
  caller proof        `proven_caller` (D108 ancestry, D109 PM pin) for every non-read-only call; `pm_gate`
  operator proof      `prompt_proof` (D110): the hook's prompt is the latest real user prompt of the PM transcript
  confirmation        `confirm` (D113): a one-time code shown only in a tmux popup on the PM pane's client
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
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, NoReturn, Sequence, TextIO, TypeVar

from .model import (
    Caller,
    CallerKind,
    Doc,
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
    compiled = [re.compile(p) for p in patterns]
    wt = str(worktree).rstrip("/")
    for line in f"{out}\n{err}".split("\n"):
        if any(rx.search(line) for rx in compiled):
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
    `pm_gate` (D109). A failed check is UNKNOWN with a `why` the refusal shows. An injected caller (tests) is taken
    as given."""
    resolved = getattr(resolve, "resolved", None)
    if resolved is None:
        return resolve()
    from . import identity  # identity -> projmux -> layers: imported at call time

    r = resolved()
    caller = r.caller
    if caller.kind is CallerKind.UNKNOWN:
        return caller
    if not identity.descends_from(os.getpid(), r.claude_pid):
        return Caller.unknown(ANCESTRY_WHY)
    if caller.kind is CallerKind.PM:
        why = pm_gate(store, caller.agent_uid, r.live_pms, unpinned_ok=tool == "mesh_init")
        if why:
            return Caller.unknown(why)
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
    """The text of a real user prompt entry, else None (an assistant line, a tool result, a meta or sidechain line)."""
    if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    msg = entry.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content and all(isinstance(b, dict) for b in content):
        if any(b.get("type") == "tool_result" for b in content):
            return None
        texts = [b.get("text") for b in content if b.get("type") == "text" and isinstance(b.get("text"), str)]
        return "\n".join(texts) if texts else None
    return None


def _latest_prompt_entry(path: str, prompt: str) -> str | None:
    """The uuid of the LATEST user prompt entry when its text equals `prompt`, else None. Streams the tail line by
    line; keeps only (uuid, equal?) of the newest prompt seen, never a line's text."""
    latest: tuple[str | None, bool] | None = None
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
                latest = (uuid if isinstance(uuid, str) and uuid else None, text == prompt)
    if latest is None or not latest[1] or latest[0] is None:
        return None
    return latest[0]


def prompt_proof(
    payload: Mapping[str, Any],
    prompt: str,
    root: str | os.PathLike,
    env: Mapping[str, str],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str | None, str | None]:
    """D110: (entry hash, None) when the hook's prompt equals the latest real user prompt in the session's transcript,
    else (None, error code). The transcript path must resolve to a regular file `<session_id>.jsonl` directly under
    this project's Claude projects directory. It is read only to find that one entry: nothing from it is kept,
    logged or returned but sha256(session:uuid). The hook may run a moment before Claude writes the entry: a
    mismatch is re-read a few times (bounded)."""
    session, path = payload.get("session_id"), payload.get("transcript_path")
    if not isinstance(session, str) or not _SESSION.fullmatch(session) or not isinstance(path, str) or not path:
        return None, "transcript_refused"
    real = os.path.realpath(path)
    if os.path.dirname(real) not in transcript_dirs(root, env) or os.path.basename(real) != f"{session}.jsonl":
        return None, "transcript_refused"
    try:
        if not stat.S_ISREG(os.stat(real).st_mode):
            return None, "transcript_refused"
    except OSError:
        return None, "transcript_refused"

    def attempt(n: int) -> str | None:
        try:
            return _latest_prompt_entry(real, prompt)
        except OSError:
            return None

    uuid = retry(attempt, retries=3, transient=lambda u: u is None, backoff_s=0.1, sleep=sleep)[-1]
    if uuid is None:
        return None, "prompt_not_in_transcript"
    return hashlib.sha256(f"{session}:{uuid}".encode("utf-8")).hexdigest(), None


# ---------------------------------------------------------------- confirmation (D113)
PRIVILEGED_COMMANDS = frozenset({"permissions", "config", "login", "logout", "mcp", "plugin", "hooks"})
CONFIRM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
CONFIRM_LEN = 8
CONFIRM_HELPER = Path(__file__).resolve().with_name("confirm.py")


def confirm(
    popup: Callable[[list[str], float], bool],
    timeout_s: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    """D113: None when the operator typed the one-time code into the popup, else why not (unavailable | none | wrong
    | timeout). The code is made here (`secrets`), handed to the popup helper through a 0600 file in a fresh 0700
    temp dir (the helper reads and removes it at once), and never appears in an argv, a log, stdout or .mesh/: mesh
    keeps only its sha256, in memory, for this one call. The helper writes back the sha256 of what was typed."""
    work = tempfile.mkdtemp(prefix="mesh-confirm-")
    try:
        code = "".join(secrets.choice(CONFIRM_ALPHABET) for _ in range(CONFIRM_LEN))
        want = hashlib.sha256(code.encode("ascii")).hexdigest()
        fd = os.open(os.path.join(work, "code"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, code.encode("ascii"))
        finally:
            os.close(fd)
        del code
        deadline = clock() + timeout_s
        if not popup([sys.executable, str(CONFIRM_HELPER), work, str(int(timeout_s))], timeout_s):
            return "unavailable"
        answer = os.path.join(work, "answer")
        while not os.path.exists(answer):
            if clock() >= deadline:
                return "timeout"
            sleep(0.05)
        with open(answer, encoding="ascii", errors="replace") as fh:
            got = fh.read().strip()
        if not got:
            return "none"
        return None if hmac.compare_digest(got, want) else "wrong"
    finally:
        shutil.rmtree(work, ignore_errors=True)


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


def _error(code: str, message: str, hint: str | None = None, reason: str | None = None) -> ToolResult:
    error = {"code": code, "message": message}
    if reason:
        error["reason"] = reason
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
            return _error("refused", exc.refusal.detail, reason=exc.refusal.code.value)
        except MeshError as exc:
            return _error(exc.code, str(exc), exc.hint)
        except Exception as exc:  # a bug: one stderr line, the error TYPE and a fixed id (a message can carry a path)
            log(f"internal error {type(exc).__name__} [E-TOOL]")
            return _error("internal", "internal error: see the MCP server log")

    run.__mesh_layers__ = TOOL_LAYERS
    run.__name__ = f"{name}_layered"
    return run
