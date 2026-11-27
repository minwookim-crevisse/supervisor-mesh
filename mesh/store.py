"""`.mesh/` on disk: state.json (lock + atomic replace), config.toml (read), and the two append-only logs.

One writer per file, no split state:
  state.json      written ONLY by `Store.locked()` -> `Txn.commit` (items, seq, event uses, legacy rows)
  refusals.jsonl  appended ONLY by `Store.append_refusal`: a refusal never touches state.json, so it cannot touch an
                  item or its version (invariant 1, D68)
  events.jsonl    appended ONLY by `Store.record_operator_event` (operator prompts recorded by the PM-pane hook),
                  `Store.append_receipt` (tool receipts: type "receipt", never read back as an operator event) and
                  `Store.append_hook_error` (type "hook_error": the hook failed and recorded nothing)
Both logs may hold operator prompt text: they are meant to be gitignored; state.json is meant to be committed.

state.json protocol (section 8):
  read()    lock-free. `os.replace` makes every read a complete old-or-new document.
  locked()  flock(.mesh/state.lock, LOCK_EX | LOCK_NB) polled until `timeout_s`, then Busy (retry). Held for ONE
            read-check-write; commands run before it (facts) and after it (effects), never inside.
  commit()  tmp file in the same directory, fsync, os.replace, fsync of the directory. Compare-and-set is NOT here:
            it is a pure stamp comparison in `state.decide`; the lock makes read-check-write atomic, the stamp
            detects that the world moved during the unlocked phase.
Output is deterministic (sorted keys, indent 1, trailing newline): state.json is committed, so a no-op write must
produce no diff. `state` / `blocked_reason` are written next to `phase` for readers (jq, humans); on load they are
cross-checked and a contradictory pair is a load error, not a guess. A file with a NEWER schema_version is readable and
never written (SchemaTooNew).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterator

from . import layers
from .model import (
    DEFAULT_TYPES,
    PREPARE_KIND,
    SCHEMA_VERSION,
    Busy,
    Command,
    Config,
    Doc,
    EventProof,
    InvalidArgs,
    InvariantViolation,
    MeshError,
    NotInitialized,
    OperatorEvent,
    Refusal,
    SchemaTooNew,
    TypeSpec,
    empty_doc,
    from_json,
    item_json,
    to_json,
)

__all__ = [
    "Store",
    "Txn",
    "Snapshot",
    "StateCorrupt",
    "NotInitialized",
    "InvalidArgs",
    "Busy",
    "SchemaTooNew",
    "parse_config",
    "dump_doc",
    "load_doc",
]


FILE_MODE = 0o600  # every file under .mesh/ is owner-only: events.jsonl and refusals.jsonl hold operator prompt text


class StateCorrupt(MeshError):  # noqa: N818
    code = "state_corrupt"


class HookRefused(Exception):  # noqa: N818
    """The hook records nothing; `code` is the hook_error line's fixed error code (never a value)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Snapshot:
    doc: Doc
    writable: bool  # False: state.json was written by a newer plugin


# ----------------------------------------------------------- state.json <-> Doc: the only place that knows the shape
def dump_doc(doc: Doc) -> str:
    raw = to_json(doc)
    raw["items"] = {item_id: item_json(item) for item_id, item in doc.items.items()}
    return json.dumps(raw, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def load_doc(raw: object, *, strict: bool = True) -> Doc:
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), dict):
        raise StateCorrupt("state.json is not a mesh document")
    for item_id, item in raw["items"].items():
        if not isinstance(item, dict) or not isinstance(item.get("phase"), str):
            raise StateCorrupt(f"item {item_id}: no phase")
        phase = item["phase"]
        expected = ("blocked", phase[8:-1]) if phase.startswith("blocked(") else (phase, None)
        derived = (item.pop("state", expected[0]), item.pop("blocked_reason", expected[1]))
        if derived != expected:
            raise StateCorrupt(f"item {item_id}: state/blocked_reason {derived} contradict phase {phase!r}")
    try:
        doc = from_json(Doc, raw, strict=strict)
    except (InvalidArgs, InvariantViolation) as exc:
        raise StateCorrupt(f"state.json: {exc}") from exc
    bad = doc.override_errors()  # D112 (F6): a hand-made override has no operator event behind it
    if bad:
        raise StateCorrupt(f"state.json: {bad[0]}")
    return doc


# ---------------------------------------------------------------- config.toml as a value
def parse_config(text: str) -> Config:
    """tomllib + validation at the boundary. Unknown keys are ignored here (init reports them, nothing uses them)."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise InvalidArgs(f"config.toml: {exc}") from exc
    limits = raw.get("limits", {})
    for key in ("retry", "environment_unblock"):
        if key in limits and (isinstance(limits[key], bool) or not isinstance(limits[key], int) or limits[key] < 0):
            raise InvalidArgs(f"config.toml: limits.{key} must be a non-negative integer")
    types = dict(DEFAULT_TYPES)
    for name, spec in raw.get("types", {}).items():
        types[name] = from_json(TypeSpec, spec, path=f"types.{name}")
    verify = raw.get("verify", {})
    shared = _patterns(verify.get("fail_if_output", []), "verify.fail_if_output")
    commands = tuple(
        replace(c, fail_if_output=tuple(dict.fromkeys(c.fail_if_output + shared))) for c in _commands(raw, "verify")
    )
    if any(c.kind == PREPARE_KIND for c in commands):
        raise InvalidArgs(f"config.toml: the verify kind {PREPARE_KIND!r} is reserved for verify.prepare")
    precheck = _commands(raw, "precheck")
    defaults = Config()
    repro_timeout = raw.get("repro", {}).get("timeout_s", defaults.repro_timeout_s)
    _positive_int(repro_timeout, "repro.timeout_s")
    dispatch = raw.get("dispatch", {})
    retry = dispatch.get("retry", defaults.dispatch_retry)
    if isinstance(retry, bool) or not isinstance(retry, int) or not 0 <= retry <= 10:
        raise InvalidArgs("config.toml: dispatch.retry must be an integer from 0 to 10")
    backoff = dispatch.get("backoff_s", defaults.dispatch_backoff_s)
    if isinstance(backoff, bool) or not isinstance(backoff, (int, float)) or not 0 <= backoff <= 60:
        raise InvalidArgs("config.toml: dispatch.backoff_s must be a number from 0 to 60")
    stall = raw.get("status", {}).get("stall_minutes", defaults.stall_minutes)
    _positive_int(stall, "status.stall_minutes")
    confirm_timeout = raw.get("confirm", {}).get("timeout_s", defaults.confirm_timeout_s)
    _positive_int(confirm_timeout, "confirm.timeout_s")
    render_wait = raw.get("pane_command", {}).get("render_wait_s", defaults.pane_render_wait_s)
    if isinstance(render_wait, bool) or not isinstance(render_wait, (int, float)) or not 0 < render_wait <= 30:
        raise InvalidArgs("config.toml: pane_command.render_wait_s must be a number above 0 and at most 30")
    ttl = raw.get("pane_command", {}).get("proposal_ttl_s", defaults.pane_proposal_ttl_s)
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not 0 < ttl <= 3600:
        raise InvalidArgs("config.toml: pane_command.proposal_ttl_s must be a number above 0 and at most 3600")
    return Config(
        limits.get("retry", defaults.retry_limit),
        limits.get("environment_unblock", defaults.environment_limit),
        types,
        commands,
        precheck,
        repro_timeout,
        _prepare(verify),
        shared,
        retry,
        float(backoff),
        stall,
        confirm_timeout,
        render_wait,
        ttl,
    )


def _prepare(verify: dict) -> Command | None:
    """`[verify] prepare = [argv...]` (+ `prepare_timeout_s`, default 600): D91."""
    if "prepare" not in verify:
        return None
    argv = verify["prepare"]
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv) or not argv[0]:
        raise InvalidArgs("config.toml: verify.prepare must be a non-empty argv list with a program first")
    timeout = verify.get("prepare_timeout_s", 600)
    _positive_int(timeout, "verify.prepare_timeout_s")
    return Command(tuple(argv), PREPARE_KIND, timeout)


def _patterns(value: object, path: str) -> tuple[str, ...]:
    """D115: a list of non-empty literal strings, each at most `layers.MAX_PATTERN` characters (never compiled)."""
    if not isinstance(value, list) or not all(isinstance(p, str) and p for p in value):
        raise InvalidArgs(f"config.toml: {path} must be a list of non-empty strings (literal substrings)")
    if any(len(p) > layers.MAX_PATTERN for p in value):
        raise InvalidArgs(f"config.toml: {path}: an entry is longer than {layers.MAX_PATTERN} characters")
    return tuple(value)


def _positive_int(value: object, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidArgs(f"config.toml: {path} must be a positive integer")


KIND_PATTERN = r"[a-z0-9_-]+"  # an evidence kind; also a log file name under .mesh/runs/


def _commands(raw: dict, table: str) -> tuple[Command, ...]:
    """`[[<table>.commands]]` argv (non-empty, a non-empty program) + kind (`KIND_PATTERN`) (+ timeout_s, a positive
    integer, default 120 s). Checked here, at parse time: a bad entry is a config error, never a run-time surprise."""
    out = []
    for i, c in enumerate(raw.get(table, {}).get("commands", [])):
        at = f"{table}.commands[{i}]"
        cmd = from_json(Command, c, path=at)
        _positive_int(cmd.timeout_s, f"{at}.timeout_s")
        if not cmd.argv or not cmd.argv[0]:
            raise InvalidArgs(f"config.toml: {at}.argv must be a non-empty list with a program first")
        if not re.fullmatch(KIND_PATTERN, cmd.kind):
            raise InvalidArgs(f"config.toml: {at}.kind must match {KIND_PATTERN}")
        _patterns(list(cmd.fail_if_output), f"{at}.fail_if_output")
        out.append(cmd)
    return tuple(out)


def event_sha256(event: OperatorEvent) -> str:
    """The hash state.json keeps per operator event: the whole record, canonical JSON."""
    return hashlib.sha256(json.dumps(to_json(event), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- the store
class Txn:
    """The state under the lock. `doc` is the FRESH document read after the lock was taken."""

    def __init__(self, store: "Store", snap: Snapshot):
        self._store, self.snapshot = store, snap

    @property
    def doc(self) -> Doc:
        return self.snapshot.doc

    def commit(self, doc: Doc) -> None:
        if not self.snapshot.writable:
            raise SchemaTooNew("state.json was written by a newer plugin", hint="update the supervisor-mesh plugin")
        self._store._write(doc)
        self.snapshot = Snapshot(doc, True)


class Store:
    def __init__(self, mesh_dir: Path):
        self.dir = Path(mesh_dir)
        self.state_path = self.dir / "state.json"
        self.lock_path = self.dir / "state.lock"
        self.refusals_path = self.dir / "refusals.jsonl"
        self.events_path = self.dir / "events.jsonl"
        self.config_path = self.dir / "config.toml"

    def initialized(self) -> bool:
        return self.state_path.exists()

    def create(self, doc: Doc | None = None, timeout_s: float = 5.0) -> bool:
        """The first state.json (mesh_init), written under the lock so two concurrent inits cannot both write it.
        False when one exists: an existing document is never replaced."""
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._flock(timeout_s):
            if self.state_path.exists():
                return False
            self._write(doc or empty_doc())
            return True

    # -- state.json
    def read(self) -> Snapshot:
        try:
            text = self.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise NotInitialized("no .mesh/state.json", hint="run mesh_init") from None
        try:
            raw = layers.parse_json(text)
        except ValueError as exc:
            raise StateCorrupt(f"state.json is not JSON: {exc}") from exc
        version = raw.get("schema_version") if isinstance(raw, dict) else None
        if isinstance(version, bool) or not isinstance(version, int):
            raise StateCorrupt("state.json has no integer schema_version")
        if version > SCHEMA_VERSION:
            try:
                return Snapshot(load_doc(raw, strict=False), False)
            except StateCorrupt as exc:
                raise SchemaTooNew(
                    f"state.json schema {version} is newer than this plugin ({SCHEMA_VERSION})",
                    hint="update the supervisor-mesh plugin",
                ) from exc
        if version < SCHEMA_VERSION:
            raise StateCorrupt(f"state.json schema {version} is older than {SCHEMA_VERSION} and no migration exists")
        return Snapshot(load_doc(raw), True)

    @contextmanager
    def locked(self, timeout_s: float = 5.0) -> Iterator[Txn]:
        with self._flock(timeout_s):
            for stale in self.dir.glob("state.json.*.tmp"):  # a crashed writer's leftovers; we hold the lock
                stale.unlink(missing_ok=True)
            yield Txn(self, self.read())

    @contextmanager
    def _flock(self, timeout_s: float) -> Iterator[None]:
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, FILE_MODE)
        try:
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Busy("another writer holds .mesh/state.lock", hint="retry") from None
                    time.sleep(0.02)
            yield
        finally:
            os.close(fd)  # closing the descriptor releases the flock

    def _write(self, doc: Doc) -> None:
        tmp = self.dir / f"state.json.{os.getpid()}.tmp"
        try:
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE), "w", encoding="utf-8") as handle:
                handle.write(dump_doc(doc))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.state_path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        dir_fd = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    # -- config.toml
    def read_config(self) -> Config:
        try:
            return parse_config(self.config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Config()

    # -- append-only logs
    def _append(self, path: Path, record: dict) -> None:
        line = (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
        try:
            os.write(fd, line)  # one write() per line: appends do not interleave
            os.fsync(fd)
        finally:
            os.close(fd)

    def _lines(self, path: Path) -> list[dict]:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        out = []
        for line in text.split("\n"):  # not splitlines(): U+2028 / U+2029 / U+0085 inside a JSON string are text
            try:
                out.append(layers.parse_json(line))
            except ValueError:
                continue  # a torn last line (or a hostile nesting, F8) never blocks a read
        return out

    def append_refusal(self, refusal: Refusal) -> None:
        self._append(self.refusals_path, to_json(refusal))

    def refusals(self, last: int | None = None) -> list[dict]:
        rows = self._lines(self.refusals_path)
        return rows if last is None else rows[-last:]

    def record_operator_event(
        self,
        text: str,
        at: str,
        agent_uid: str | None = None,
        *,
        candidate: str | None = None,
        check: Callable[[Doc], str | None] | None = None,
        edit: Callable[[Doc, OperatorEvent], Doc] | None = None,
    ) -> OperatorEvent:
        """Section 9a: the hook path. The seq and the record's hash are taken under the lock and committed BEFORE the
        line is appended, so a crash in between loses the event but never reuses a seq. D112 (F7): the seq is above
        every seq already in events.jsonl, so a line planted ahead never shares a seq with a genuine event. D123:
        `candidate` (the sha256 of the hook's session id) makes it a CANDIDATE whose D110 proof runs at its first
        citation; `check` re-decides on the fresh document (an error code raises HookRefused) and `edit` adds to the
        commit (a pending re-pin)."""
        with self.locked() as txn:
            doc = txn.doc
            why = check(doc) if check is not None else None
            if why is not None:
                raise HookRefused(why)
            logged = [r.get("seq") for r in self._lines(self.events_path)]
            seq = max([doc.seq, *(n for n in logged if isinstance(n, int) and not isinstance(n, bool))]) + 1
            event = OperatorEvent(f"ev-{seq:06d}", seq, at, text, agent_uid)
            hashes = {**doc.operator_event_sha256, str(seq): event_sha256(event)}
            doc = replace(doc, seq=seq, last_operator_seq=seq, operator_event_sha256=hashes)
            if candidate is not None:
                doc = replace(doc, event_proofs={**doc.event_proofs, str(seq): EventProof(candidate)})
            if edit is not None:
                doc = edit(doc, event)
            txn.commit(doc)
            self._append(self.events_path, {"type": "operator", **to_json(event)})
        return event

    def append_receipt(self, receipt: dict) -> None:
        """A tool receipt (pane_command). Its own record type: `get_event` never returns it, so a receipt id can
        never be cited as an operator event."""
        self._append(self.events_path, {**receipt, "type": "receipt"})

    def append_hook_error(self, at: str, error: str) -> None:
        """Section 9a: the hook never blocks a prompt; a failure leaves this line (an error class, never the prompt
        text, an env value or a path) and records no operator event."""
        self._append(self.events_path, {"type": "hook_error", "at": at, "error": error})

    def operator_events(self) -> list[OperatorEvent]:
        """The operator events that are genuine (see `get_event`), in log order."""
        return self._operator_events()

    def _operator_events(self) -> list[OperatorEvent]:
        """An `operator` line counts only when (1) its id is `ev-<seq:06d>`, (2) its seq is at most
        `last_operator_seq`, (3) its record hashes to the value state.json holds for that seq (written under the lock
        when the hook recorded it) and (4) it is the first such line for that seq. A line anyone appended or edited
        outside `record_operator_event` fails (3); a forged copy with the same seq no longer hides the genuine line."""
        rows = [r for r in self._lines(self.events_path) if r.get("type") == "operator"]
        try:
            doc = self.read().doc
        except NotInitialized:
            return []
        parsed: list[OperatorEvent] = []
        for row in rows:
            try:
                parsed.append(from_json(OperatorEvent, {k: v for k, v in row.items() if k != "type"}))
            except InvalidArgs:
                continue
        out, seen = [], set()
        for e in parsed:  # D112 (F7): a copy with the same seq and other text matches no hash and hides nothing
            if (
                e.seq not in seen
                and e.id == f"ev-{e.seq:06d}"
                and 0 < e.seq <= doc.last_operator_seq
                and doc.operator_event_sha256.get(str(e.seq)) == event_sha256(e)
            ):
                seen.add(e.seq)
                out.append(e)
        return out

    def get_event(self, event_id: str) -> OperatorEvent | None:
        return next((e for e in self._operator_events() if e.id == event_id), None)
