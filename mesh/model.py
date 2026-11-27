"""Vocabulary and records of the work-item state machine (state model sections 1, 3). PURE: no I/O, no clock.

Everything here is frozen: a change is a new value (`dataclasses.replace`), never a mutation, so the pure core
(`state.decide`) hands back a new `Doc` and the engine decides whether to write it.

Invariants that live in the TYPES (hard to misuse beats a runtime check beats prose):
  * one `Phase` enum folds `blocked(reason)` into the state (the TLA model's `PS()` names): a blocked state without a
    reason, or a reason without a blocked state, cannot be built;
  * `ReportedClass` (what a caller may name in `fail`) has no `limit`: invariant 4 is a type. `FailureClass.LIMIT`
    exists only in stored records, written by `state.enter_limit`;
  * `Item.__post_init__` checks invariant 6 (`verified` carries evidence bound to the current commit, contract
    version and config hash) on every construction path, including `replace` and loading state.json.
"""

from __future__ import annotations

import collections.abc
import hashlib
import json
import re
import types
import typing
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from enum import Enum, StrEnum
from typing import Any, Literal, Mapping

ITEM_ID_PATTERN = r"WO-[0-9]{4,}"  # ASCII digits only: `\d` also matches Arabic-Indic, fullwidth, ...
ROLE_PATTERN = r"[a-z][a-z0-9-]*"
SHA_PATTERN = r"[0-9a-f]{40}"


# ---------------------------------------------------------------- errors (domain outcomes are values; these are not)
# The failure names are the spec's vocabulary (Busy, SchemaTooNew, NotInitialized): N818 is waived on purpose.
class MeshError(Exception):
    """Base of the failures that are not a refusal: the front doors map `code` to an `isError` result."""

    code = "internal"

    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


class InvalidArgs(MeshError):  # noqa: N818
    code = "invalid_args"


class UnknownItem(MeshError):  # noqa: N818
    code = "unknown_item"


class Busy(MeshError):  # noqa: N818
    code = "busy"


class SchemaTooNew(MeshError):  # noqa: N818
    code = "schema_too_new"


class NotInitialized(MeshError):  # noqa: N818
    code = "not_initialized"


class InvariantViolation(MeshError):  # noqa: N818
    """A post-condition of a write failed: a bug in the table or an effect. Nothing was written."""

    code = "internal"


# ---------------------------------------------------------------- vocabularies (values are the TLA model's strings)
class Phase(StrEnum):
    """Section 1 states with the blocked reason folded in; exactly the model's `PS(rec)` names."""

    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    CLOSED = "closed"
    REWORK = "rework"
    BLOCKED_AWAITING_USER = "blocked(awaiting_user)"
    BLOCKED_ENVIRONMENT = "blocked(environment)"
    BLOCKED_LIMIT = "blocked(limit)"
    BLOCKED_CHILDREN = "blocked(children)"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (Phase.CLOSED, Phase.CANCELLED)

    @property
    def blocked_reason(self) -> str | None:
        """Derived for readers of state.json (section 3 `blocked_reason`); never stored on its own."""
        return self.value[8:-1] if self.value.startswith("blocked(") else None


NON_TERMINAL = frozenset(p for p in Phase if not p.terminal)
CREATED = frozenset(Phase)
OVERRIDE_TARGETS = frozenset(Phase) - {Phase.BLOCKED_LIMIT}  # D73, derived and not listed


class Actor(StrEnum):
    """The table's callers (model `Callers`). `LEAD_OWN` / `LEAD_OTHER` are relative to the item's role."""

    PM = "pm"
    SLACK_WATCHER = "slack watcher"
    LEAD_OWN = "lead (own role)"
    LEAD_OTHER = "lead (other role)"
    MESH = "mesh"
    OPERATOR = "operator"


class Verb(StrEnum):
    """Model `AllVerbs`; every (mesh) row shares `MESH` and is told apart by its row id."""

    NEW = "new"
    DRAFT = "draft"
    ATTACH = "attach"
    REPLY = "reply"
    REVISE = "revise"
    READY = "ready"
    DISPATCH = "dispatch"
    START = "start"
    SUBMIT = "submit"
    VERIFY = "verify"
    FAIL = "fail"
    REASSIGN = "reassign"
    SPLIT = "split"
    MESH = "mesh"
    UNBLOCK = "unblock"
    REJECT = "reject"
    ACK = "ack"
    CLOSE = "close"
    CANCEL = "cancel"
    REVALIDATE = "revalidate"
    OVERRIDE = "override"


class FailureClass(StrEnum):
    DEFECT = "defect"
    CONFLICT = "conflict"
    AMBIGUITY = "ambiguity"
    ENVIRONMENT = "environment"
    LIMIT = "limit"  # stored only: `state.enter_limit` is its sole writer (invariant 4)


ReportedClass = Literal["defect", "conflict", "ambiguity", "environment"]  # what `fail` accepts: no limit
RejectClass = Literal["defect", "conflict", "ambiguity"]  # what `reject` accepts
OverrideTarget = Literal[tuple(p.value for p in Phase if p is not Phase.BLOCKED_LIMIT)]  # D73 in the schema enum


class Fact(StrEnum):
    """Outside-world observations a row may ask for. Gathered UNLOCKED, handed to `decide` as values (section 8)."""

    HEAD = "head"
    LEAD_LIVE = "lead_live"
    LEAD_AGENT = "lead_agent"
    COMMIT_EXISTS = "commit_exists"
    COMMIT_DESCENDS = "commit_descends"  # the submitted commit has the attempt's base commit as an ancestor
    REPRO = "repro"
    PRECHECK = "precheck"
    VERIFICATION = "verification"
    OPERATOR_EVENT = "operator_event"
    MERGED_COMMIT = "merged_commit"


class StampKind(StrEnum):
    NONE = "none"  # no unlocked phase: one critical section
    VERSION = "version"  # section 8: the record version guards every write that is not a verify
    ATTEMPT = (
        "attempt"  # D76: (phase, attempt id, contract version, submitted commit); reply-side writes do not stale it
    )


class CallerKind(StrEnum):
    PM = "pm"
    LEAD = "lead"
    UNKNOWN = "unknown"
    MESH = "mesh"  # internal only: the engine's own (mesh) rows; never produced by an identity adapter


@dataclass(frozen=True, slots=True)
class Caller:
    """Who is calling (D82), injected by the front door. UNKNOWN: read tools work, every verb is refused."""

    kind: CallerKind
    role: str | None = None  # LEAD: its role label
    agent_uid: str | None = None  # recorded in history; never echoed in public refusal text
    why: str | None = None  # UNKNOWN only: what the operator can do about it (D108, D109); fixed text, no values
    # UNKNOWN only: WHY it is not resolved or not proven (D141, a fixed code). Diagnostic: not part of who it is
    rule: str | None = field(default=None, compare=False)

    @staticmethod
    def pm(agent_uid: str | None = None) -> "Caller":
        return Caller(CallerKind.PM, None, agent_uid)

    @staticmethod
    def lead(role: str, agent_uid: str | None = None) -> "Caller":
        return Caller(CallerKind.LEAD, role, agent_uid)

    @staticmethod
    def unknown(why: str | None = None, rule: str | None = None) -> "Caller":
        return Caller(CallerKind.UNKNOWN, why=why, rule=rule)

    @staticmethod
    def mesh() -> "Caller":
        return Caller(CallerKind.MESH)


# ---------------------------------------------------------------- per-verb args (one frozen dataclass per verb)
NONBLANK = r"[\s\S]*\S[\s\S]*"  # at least one non-space character
# Size bounds on caller text (F19): an order is ONE argv string to projmux (Linux caps one argument at 128 KiB)
MAX_TEXT = 4000  # a goal, one done criterion, one path
MAX_CRITERIA = 50  # done criteria per item
MAX_PATHS = 200  # entries of a read or write set


def _bounded(chars: int | None = None, items: int | None = None, **meta) -> dict:
    out = dict(meta)
    if chars is not None:
        out["max_length"] = chars
    if items is not None:
        out["max_items"] = items
    return out


def _pattern(p: str) -> dict:
    return {"pattern": p}


@dataclass(frozen=True, slots=True)
class NoArgs:
    pass


@dataclass(frozen=True, slots=True)
class Scope:
    read_set: tuple[str, ...] = field(default=(), metadata=_bounded(MAX_TEXT, MAX_PATHS))
    write_set: tuple[str, ...] = field(default=(), metadata=_bounded(MAX_TEXT, MAX_PATHS))
    report: str | None = field(default=None, metadata=_bounded(MAX_TEXT))
    work_order: str | None = field(
        default=None,
        metadata=_bounded(MAX_TEXT, doc="the work-order file the lead reads first (default docs/work-orders/<id>.md)"),
    )
    resources: tuple[str, ...] = field(
        default=(),
        metadata=_bounded(MAX_TEXT, MAX_PATHS, doc="shared resources the work touches (node_modules, a lockfile): D94"),
    )


@dataclass(frozen=True, slots=True)
class NewArgs:
    goal: str = field(metadata=_bounded(MAX_TEXT))
    done_criteria: tuple[str, ...] = field(metadata=_bounded(MAX_TEXT, MAX_CRITERIA))
    role: str = field(metadata=_pattern(ROLE_PATTERN))
    type: str
    depends: tuple[str, ...] = field(default=(), metadata=_pattern(ITEM_ID_PATTERN))
    type_inputs: Mapping[str, str] = field(default_factory=dict)
    scope: Scope = Scope()
    repro: tuple[str, ...] | None = field(
        default=None, metadata={"doc": "bugfix only: argv that must fail at the base commit"}
    )


@dataclass(frozen=True, slots=True)
class ContractPatch:
    """Edits `ready` applies atomically before it bumps the contract version: one way to change a contract."""

    goal: str | None = field(default=None, metadata=_bounded(MAX_TEXT))
    done_criteria: tuple[str, ...] | None = field(default=None, metadata=_bounded(MAX_TEXT, MAX_CRITERIA))
    role: str | None = field(default=None, metadata=_pattern(ROLE_PATTERN))
    scope: Scope | None = None
    type_inputs: Mapping[str, str] | None = None
    depends: tuple[str, ...] | None = field(default=None, metadata=_pattern(ITEM_ID_PATTERN))
    repro: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class DispatchSend:
    """What `work_dispatch` hands the `dispatch` row: the sha256 of the order it composed (the text itself is never
    stored, DEC-0008). Not caller-supplied: the tool computes it from the item record."""

    text_sha256: str = field(metadata=_pattern(r"[0-9a-f]{64}"))


@dataclass(frozen=True, slots=True)
class ReadyArgs:
    patch: ContractPatch | None = None


@dataclass(frozen=True, slots=True)
class ReviseArgs:
    reason: str = field(metadata={**_pattern(NONBLANK), "doc": "why the contract goes back to draft (required)"})


@dataclass(frozen=True, slots=True)
class StartArgs:
    pass


@dataclass(frozen=True, slots=True)
class SubmitArgs:
    attempt_id: int = field(metadata={"doc": "the id `start` returned; an older attempt is refused (section 8)"})
    commit: str = field(metadata=_pattern(SHA_PATTERN))


@dataclass(frozen=True, slots=True)
class VerifyArgs:
    attempt_id: int


@dataclass(frozen=True, slots=True)
class FailArgs:
    cls: ReportedClass
    attempt_id: int | None = None
    reason: Literal["too_large"] | None = None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReassignArgs:
    note: str = ""


@dataclass(frozen=True, slots=True)
class ChildDraft:
    goal: str
    done_criteria: tuple[str, ...]
    role: str = field(metadata=_pattern(ROLE_PATTERN))
    type: str


@dataclass(frozen=True, slots=True)
class SplitArgs:
    children: tuple[ChildDraft, ...]


@dataclass(frozen=True, slots=True)
class RejectArgs:
    cls: RejectClass
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CancelArgs:
    reason: str


@dataclass(frozen=True, slots=True)
class ReplyArgs:
    event_id: str


@dataclass(frozen=True, slots=True)
class OverrideArgs:
    to: OverrideTarget
    reason: str
    event_id: str


# ---------------------------------------------------------------- refusal codes
class RefusalCode(StrEnum):
    """Stable machine reasons. Every member is named by a guard or a generic path; a test fails when one is unused."""

    NOT_IN_TABLE = "not_in_table"  # invariant 1
    UNKNOWN_CALLER = "unknown_caller"  # D82: no label, no verbs
    UNKNOWN_ITEM = "unknown_item"
    STALE = "stale"  # CAS mismatch (D76): the result is discarded
    FACT_UNAVAILABLE = "fact_unavailable"  # the row needs an outside observation nobody supplied
    ARGS_INVALID = "args_invalid"
    CONTRACT_INCOMPLETE = "contract_incomplete"
    DEPENDS_INVALID = "depends_invalid"  # unknown id, self, cycle
    DEPENDS_CANCELLED = "depends_cancelled"  # D81
    DEPENDENCY_UNSATISFIED = "dependency_unsatisfied"
    REPRO_NOT_REPRODUCED = "repro_not_reproduced"
    REPRO_NOT_RUNNABLE = "repro_not_runnable"
    CLASS_ROUTE = "class_route"  # section 5 way back
    CONTRACT_NOT_REVISED = "contract_not_revised"
    NO_FRESH_REPLY = "no_fresh_reply"
    ROLE_HAS_NO_LEAD = "role_has_no_lead"
    LEAD_NOT_LIVE = "lead_not_live"
    LEAD_STILL_LIVE = "lead_still_live"
    ATTEMPT_STALE = "attempt_stale"
    COMMIT_UNCHANGED = "commit_unchanged"
    COMMIT_UNKNOWN = "commit_unknown"
    COMMIT_NOT_DESCENDANT = "commit_not_descendant"  # the submitted commit does not descend from the base commit
    REASON_REQUIRED = "reason_required"  # revise records why the contract went back to draft
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_PASSED = "verification_passed"  # selection guard of the (mesh) parent-verify-fail row
    EVIDENCE_MISSING = "evidence_missing"
    INPUTS_CHANGED = "inputs_changed"
    FAILING_BEFORE_MISMATCH = "failing_before_mismatch"
    CLASS_NOT_ALLOWED = "class_not_allowed"
    SPLIT_TOO_FEW_CHILDREN = "split_too_few_children"
    PRECHECK_REFUSED = "precheck_refused"
    EVENT_UNKNOWN = "event_unknown"
    EVENT_REUSED = "event_reused"
    EVENT_STALE = "event_stale"
    EVENT_UNPROVEN = "event_unproven"  # D123: a candidate event whose transcript proof failed (or could not run yet)
    UNACKED_REPLIES = "unacked_replies"
    NOTHING_TO_ACK = "nothing_to_ack"
    REVALIDATE_FLAG = "revalidate_flag"
    REJECT_NO_TRIGGER = "reject_no_trigger"
    OVERRIDE_TARGET = "override_target"
    PREPARE_FAILED = "prepare_failed"  # D91: verify.prepare did not pass: class environment, not defect
    # tool-level codes outside the transition table (`pane_command`, mesh/pane.py CODES)
    CALLER_NOT_PM = "caller_not_pm"
    COMMAND_INVALID = "command_invalid"
    TARGET_INVALID = "target_invalid"
    EVENT_NOT_BOUND = "event_not_bound"  # D85: the event neither names the command nor approves the proposal
    PANE_NOT_IDLE = "pane_not_idle"
    CONFIRM_FAILED = "confirm_failed"  # D113: no, a wrong or a late out-of-band confirmation code
    RE_PIN_REFUSED = "re_pin_refused"  # D114: the pinned PM is still Running, or no re-pin event backs the call
    OUTSIDE_ROOT = "outside_root"  # D111: a mesh_init / tracker write (D117: a work order) resolves outside the root
    WORK_ORDER_MISSING = "work_order_missing"  # D117 (4): no regular work-order file under the project root


# ---------------------------------------------------------------- records (section 3)
@dataclass(frozen=True, slots=True)
class Contract:
    version: int  # bumped by EVERY `ready` (the model's ReadyRec); the first `ready` makes it 1
    goal: str
    done_criteria: tuple[str, ...]
    role: str
    type: str
    type_inputs: Mapping[str, str]
    scope: Scope
    depends: tuple[str, ...]
    repro: tuple[str, ...] | None  # bugfix only: argv


BUGFIX = "bugfix"


class RunStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    NOT_RUN = "not_run"  # 126/127/spawn error: a missing tool is not_run, never pass (section 7)


@dataclass(frozen=True, slots=True)
class CommandRun:
    argv: tuple[str, ...]
    kind: str  # the evidence kind this command proves ("tests", "lint", ...)
    status: RunStatus
    exit_code: int | None
    started_at: str  # ISO-8601 UTC, reference only
    ended_at: str
    evidence: str | None = None  # D92: the output line a `fail_if_output` regex matched (bounded, worktree-relative)


class EvidenceKind(StrEnum):
    VERIFICATION = "verification"  # the lead's `verify`: commands at the submitted commit
    MERGED = "merged"  # a split parent's own run at the merged commit (D72)
    OVERRIDE = "override"  # an operator override into verified/closed: explicit, never silent


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: EvidenceKind
    commit: str | None
    contract_version: int
    config_hash: str
    runs: tuple[CommandRun, ...]
    recorded_seq: int


@dataclass(frozen=True, slots=True)
class Attempt:
    id: int  # 1..; "older attempt ids are rejected" (section 8)
    lead_uid: str | None  # None for the mesh-run attempt of a split parent and for an override
    base_commit: str | None  # project HEAD at `start`; `submit` needs commit != base_commit
    commit: str | None  # set by `submit` (the merged commit for a split parent)
    contract_version: int
    config_hash: str  # verification config hash recorded at `start` (D47)
    evidence: Evidence | None = None  # THE evidence slot: one place, not duplicated on the item


@dataclass(frozen=True, slots=True)
class Failure:
    cls: FailureClass
    reason: str | None  # "too_large" | "retry" | "environment" | None
    evidence: tuple[str, ...]
    attempt_id: int | None
    actor: str  # "lead" | "pm" | "mesh"
    seq: int


@dataclass(frozen=True, slots=True)
class FailingBefore:
    contract_version: int
    repro_hash: str
    base_commit: str | None
    exit_code: int


@dataclass(frozen=True, slots=True)
class Reply:
    seq: int  # mesh receipt seq (D74): the ONLY ordering key
    source: Literal["terminal", "slack"]
    ref: str  # operator event id | slack ts, reference only
    acknowledged: bool = False
    consumed: bool = False  # a limit resume consumed it (D63)


@dataclass(frozen=True, slots=True)
class Counters:
    retry: int = 0
    environment_unblock: int = 0
    next_start_free: bool = False  # the first start after an environment unblock / limit resume is not counted


@dataclass(frozen=True, slots=True)
class Links:
    parent: str | None = None
    children: tuple[str, ...] = ()
    former_children: tuple[str, ...] = ()
    follows: str | None = None


class ActionKind(StrEnum):
    DISPATCH_SEND = "dispatch_send"  # replayable; projmux deduplicates on the message ref (O6)
    PM_ATTENTION = "pm_attention"  # a limit / environment entry the PM must see (the Slack question is out of the MVP)


@dataclass(frozen=True, slots=True)
class PendingAction:
    action_id: str
    kind: ActionKind
    payload: Mapping[str, str]  # no absolute paths and no message text: those are composed at send time
    done: bool = False


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    at: str  # ISO-8601 UTC, reference only (ordering is by position and by seq elsewhere)
    verb: Verb | None  # None: a tool receipt outside the table (`row` names the tool, e.g. pane_command)
    row: str
    actor: Actor
    actor_uid: str | None
    from_phase: Phase | None
    to_phase: Phase
    note: str = ""


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    phase: Phase
    version: int  # record version: the CAS stamp of every write that is not a verify
    contract: Contract
    attempts: tuple[Attempt, ...] = ()
    counters: Counters = Counters()
    failures: tuple[Failure, ...] = ()  # append-only (D75)
    failing_before: FailingBefore | None = None
    replies: tuple[Reply, ...] = ()
    limit_seq: int | None = None  # mesh seq at the blocked(limit) entry (D74)
    links: Links = Links()
    revalidate: bool = False  # section 3 `flags.revalidate`
    pending_actions: tuple[PendingAction, ...] = ()
    history: tuple[HistoryEntry, ...] = ()
    pinned_config_hash: str | None = None  # the verification config hash pinned on entering `ready` (D100, D106)

    def __post_init__(self) -> None:
        problem = self.binding_error()
        if problem:
            raise InvariantViolation(f"{self.id}: {problem}")

    @property
    def current_attempt(self) -> Attempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def latest_failure(self) -> Failure | None:
        return self.failures[-1] if self.failures else None

    def binding_error(self, phase: Phase | None = None) -> str | None:
        """Invariant 6 plus the blocked(limit) bookkeeping; the one place that knows the binding rule. `phase` asks the
        question for another phase ("would this item be a valid `verified`?")."""
        phase = phase or self.phase
        if phase is Phase.BLOCKED_LIMIT and self.limit_seq is None:
            return "blocked(limit) without limit_seq"
        if phase is not Phase.VERIFIED:
            return None
        attempt = self.current_attempt
        ev = attempt.evidence if attempt else None
        if ev is None:
            return "verified without evidence on the current attempt"
        if ev.kind is EvidenceKind.OVERRIDE:
            return None  # explicit operator override: flagged by its kind
        if attempt.commit is None or ev.commit != attempt.commit:
            return "evidence is not bound to the submitted commit"
        if not ev.contract_version == self.contract.version == attempt.contract_version:
            return "evidence is not bound to the current contract version"
        if ev.config_hash != attempt.config_hash:
            return "evidence is not bound to the attempt's verification config hash"
        return None


def item_json(item: "Item") -> dict:
    """The one JSON view of an item (state.json and `work_show`): the record plus `state` / `blocked_reason` from
    the phase for readers (jq, humans). They are never stored on their own: on load they are cross-checked."""
    raw = to_json(item)
    reason = item.phase.blocked_reason
    raw["state"] = "blocked" if reason else item.phase.value
    raw["blocked_reason"] = reason
    return raw


@dataclass(frozen=True, slots=True)
class LegacyRow:
    """An imported hand-written tracker row. NOT an Item: the table never sees it, so the 27 rows stay exact.
    Never `verified`, never deleted. A raw status that maps unambiguously to done satisfies a dependency."""

    id: str
    title: str
    roles: tuple[str, ...]  # "security, backend, qa" -> three
    depends_raw: str  # kept verbatim ("batch 3 closed", "none (parallel to WO-0016)")
    depends: tuple[str, ...]  # exact ids only; prose yields ()
    status_raw: str  # "FAIL (2 findings)" verbatim
    status: Literal["open", "dispatched", "pass", "fail", "closed", "n/a", "legacy"]
    reports: tuple[str, ...]
    order: int  # original row position


class DepStatus(StrEnum):
    SATISFIED = "satisfied"  # a verified/closed item, or a legacy row mapped closed/pass
    CANCELLED = "cancelled"
    PENDING = "pending"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EventUse:
    """Single-use ledger (D63): one hook-recorded operator event authorises exactly one reply, override,
    pane_command or companions_install."""

    event_id: str
    kind: Literal["reply", "override", "pane_command", "companions_install", "re_pin"]
    ref: str  # the item id (reply, override) | the target agent uid (pane_command, re_pin) | the plan (companions)
    at_seq: int
    status: Literal["attempting", "done", "aborted", "unknown"] | None = None  # pane_command keypress protocol


@dataclass(frozen=True, slots=True)
class EventProof:
    """D123: the hook records a CANDIDATE (the transcript entry is written only after the hook returns); the D110 proof
    runs when a tool first cites it. `session_sha256`: sha256 of the hook's (D118-checked) session id, never the id.
    A failed candidate stays failed; `why` is a fixed code."""

    session_sha256: str
    status: Literal["candidate", "proven", "failed"] = "candidate"
    why: str | None = None


@dataclass(frozen=True, slots=True)
class Proposal:
    """D85 proposal path: the exact command the PM proposed, stored under the mesh seq BEFORE the operator's prompt
    approves it. Single use. D141: `at` (UTC) starts its `proposal_ttl_s`; a stored proposal without it is expired."""

    id: str
    seq: int
    role: str
    command: str
    by_uid: str | None
    used: bool = False
    at: str | None = None


@dataclass(frozen=True, slots=True)
class RePin:
    """D109/D114: a hook-recorded `re-pin <uid>` prompt from an unpinned role=pm pane, waiting for that PM's
    mesh_init apply (which checks D114 and the D113 code, then moves the pin). Never a pin by itself."""

    event_id: str
    agent_uid: str
    session: str | None = None  # legacy (pre-D118): never written or read; kept so an older state.json still loads


SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Doc:
    """`.mesh/state.json` as a value. `seq` is THE monotonic mesh seq (D74). `last_operator_seq` is the seq of the
    newest operator event; the store bumps it under the lock when it records one, so override freshness is decided
    against the FRESH document, never against a lookup made before the lock."""

    schema_version: int
    seq: int
    next_id: int
    items: Mapping[str, Item]
    legacy: Mapping[str, LegacyRow] = field(default_factory=dict)
    event_uses: Mapping[str, EventUse] = field(default_factory=dict)
    last_operator_seq: int = 0
    proposals: Mapping[str, Proposal] = field(default_factory=dict)
    # str(seq) -> sha256 of the operator event record, written under the lock in the SAME commit that takes the seq:
    # events.jsonl is append-only but not trusted on its own (a line appended or edited by anyone else matches no hash)
    operator_event_sha256: Mapping[str, str] = field(default_factory=dict)
    # D109: the PM agent uid mesh_init apply pinned; only that uid acts as the PM. D118: the PM's Claude session is
    # never pinned; every operator event is checked against projmux's live session of that agent. `pm_session` is
    # legacy (never written or read), kept so an older state.json still loads. `transcript_entries`:
    # sha256(session:uuid) of every transcript entry that backed an event -> its seq (one event per entry; never the
    # uuid, the text or the path)
    pm_uid: str | None = None
    pm_session: str | None = None
    transcript_entries: Mapping[str, int] = field(default_factory=dict)
    pm_repin: RePin | None = None
    # D123: str(seq) -> the proof record of a hook-recorded candidate event. An event without one (recorded through the
    # store API in process, or before D123) is not a candidate
    event_proofs: Mapping[str, EventProof] = field(default_factory=dict)

    def override_errors(self) -> list[str]:
        """D112 (F6): an item whose current evidence is an operator override needs the override use of an operator
        event for that item, recorded at or before the evidence (a hand-made override has none)."""
        bad = []
        for item_id, item in self.items.items():
            attempt = item.current_attempt
            ev = attempt.evidence if attempt else None
            if ev is None or ev.kind is not EvidenceKind.OVERRIDE:
                continue
            uses = self.event_uses.values()
            if not any(u.kind == "override" and u.ref == item_id and u.at_seq <= ev.recorded_seq for u in uses):
                bad.append(f"{item_id}: override evidence without a matching operator event use")
        return bad

    def dep_status(self, item_id: str) -> DepStatus:
        """One answer for 'is this dependency satisfied' over items AND legacy rows (invariant 8)."""
        item = self.items.get(item_id)
        if item is not None:
            if item.phase is Phase.CANCELLED:
                return DepStatus.CANCELLED
            return DepStatus.SATISFIED if item.phase in (Phase.VERIFIED, Phase.CLOSED) else DepStatus.PENDING
        legacy = self.legacy.get(item_id)
        if legacy is not None:
            return DepStatus.SATISFIED if legacy.status in ("closed", "pass") else DepStatus.PENDING
        return DepStatus.UNKNOWN


def empty_doc() -> Doc:
    return Doc(SCHEMA_VERSION, 0, 1, {})


# ---------------------------------------------------------------- facts: outside observations as values
@dataclass(frozen=True, slots=True)
class LeadAgent:
    uid: str


@dataclass(frozen=True, slots=True)
class ReproOutcome:
    """Section 7: a pass is not reproduced; 126/127, timeout or spawn error is not runnable; other non-zero fails."""

    verdict: Literal["fails", "passes", "not_runnable"]
    base_commit: str | None = None
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class PrecheckOutcome:
    passes: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Commands run in a disposable worktree AT `commit`. The config hash is taken before AND after the run: a config
    edited mid-run shows as before != after and is refused as `inputs_changed` (O3)."""

    run_id: str
    commit: str | None
    config_hash_before: str
    config_hash_after: str
    runs: tuple[CommandRun, ...]


@dataclass(frozen=True, slots=True)
class OperatorEvent:
    """A prompt recorded by the PM-pane UserPromptSubmit hook (section 9a); `seq` is stamped under the store lock."""

    id: str
    seq: int
    at: str
    text: str
    agent_uid: str | None = None


@dataclass(frozen=True, slots=True)
class EventFact:
    """The cited operator event as looked up: `event` None means looked up and absent (EVENT_UNKNOWN), which is not
    the same as not looked up (Facts.operator_event None, FACT_UNAVAILABLE). Freshness is not a fact: `decide`
    compares `event.seq` with the fresh `Doc.last_operator_seq`."""

    event: OperatorEvent | None


@dataclass(frozen=True, slots=True)
class Facts:
    head: str | None = None
    lead_live: bool | None = None
    lead_agent: LeadAgent | None = None
    commit_exists: bool | None = None
    commit_descends: bool | None = None
    repro: ReproOutcome | None = None
    precheck: PrecheckOutcome | None = None
    verification: VerificationResult | None = None
    operator_event: EventFact | None = None
    merged_commit: str | None = None


NO_FACTS = Facts()
assert {f.value for f in Fact} == {f.name for f in fields(Facts)}, "Fact enum and Facts fields drifted"


# ---------------------------------------------------------------- config as a value (the store parses the toml)
@dataclass(frozen=True, slots=True)
class TypeSpec:
    required_inputs: tuple[str, ...] = ()  # keys of Contract.type_inputs (presence only, D42)
    required_evidence: tuple[str, ...] = ()  # CommandRun.kind values that must have passed


@dataclass(frozen=True, slots=True)
class Command:
    argv: tuple[str, ...]  # argv only, never a shell string
    kind: str
    timeout_s: int = 120  # per command (section 7 default); part of the verify hash: it can change an outcome
    fail_if_output: tuple[str, ...] = ()  # D92: regexes; exit 0 with a matching output line is a FAIL


DEFAULT_TYPES = {name: TypeSpec() for name in ("feature", BUGFIX, "refactor", "docs", "research", "ops")}


@dataclass(frozen=True, slots=True)
class Config:
    retry_limit: int = 2
    environment_limit: int = 3  # section 6 default
    types: Mapping[str, TypeSpec] = field(default_factory=lambda: dict(DEFAULT_TYPES))
    verify_commands: tuple[Command, ...] = ()
    precheck_commands: tuple[Command, ...] = ()  # `unblock` environment precheck; not evidence: not in the hash
    repro_timeout_s: int = 120  # section 7 default
    verify_prepare: Command | None = None  # D91: runs in the worktree before the commands (kind "prepare")
    verify_fail_if_output: tuple[str, ...] = ()  # D92: [verify] level, merged into every verify command at parse
    dispatch_retry: int = 3  # D93: resends of a transiently refused order (same message-ref)
    dispatch_backoff_s: float = 1.0  # D93: the first wait, doubled per attempt
    stall_minutes: int = 15  # D102: no lead activity on a running / verifying item for this long is a stall
    confirm_timeout_s: int = 120  # D113: how long the operator has to type the popup's code
    pane_render_wait_s: float = 15  # D125: how long pane_command waits for the typed text to reach the input box
    pane_proposal_ttl_s: float = 600  # D141: how long a pane_command proposal can be approved by its P-id

    def verify_hash(self) -> str:
        """sha256 over the canonical JSON of the evidence-relevant inputs (D47). Limits do not change it; an empty
        `fail_if_output` and an absent `prepare` leave it as it was before they existed."""
        body = {
            "commands": [_command_json(c) for c in self.verify_commands],
            "evidence": {k: list(v.required_evidence) for k, v in sorted(self.types.items())},
        }
        if self.verify_prepare is not None:
            body["prepare"] = _command_json(self.verify_prepare)
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


PREPARE_KIND = "prepare"  # the evidence kind of the D91 prepare run; reserved: no verify command may use it


def _command_json(c: Command) -> dict:
    raw = to_json(c)
    if not raw.get("fail_if_output"):
        raw.pop("fail_if_output", None)
    return raw


def repro_hash(argv: tuple[str, ...]) -> str:
    return hashlib.sha256(json.dumps(list(argv), separators=(",", ":")).encode()).hexdigest()


# ---------------------------------------------------------------- request and decisions
@dataclass(frozen=True, slots=True)
class Request:
    verb: Verb
    caller: Caller
    args: Any
    at: str  # injected ISO-8601 UTC; the core never reads a clock
    item_id: str | None = None  # None only for NEW
    row_id: str | None = None  # (mesh) requests only: which mesh row


@dataclass(frozen=True, slots=True)
class Refusal:
    """What `.mesh/refusals.jsonl` records (the model's refusal log: caller, verb, item, reason). It lives OUTSIDE
    state.json, so a refusal never touches an item or its version (invariant 1, D68)."""

    subject: str  # a Verb value, or a tool name for tools outside the table
    code: RefusalCode
    detail: str
    at: str
    caller_kind: str | None = None
    actor: str | None = None  # the table caller the request was matched as
    item_id: str | None = None
    row_id: str | None = None
    actor_uid: str | None = None
    rule: str | None = None  # D141: the failing binding, proof or caller rule (a fixed code, never prompt text)


@dataclass(frozen=True, slots=True)
class Stamp:
    kind: StampKind
    value: tuple


@dataclass(frozen=True, slots=True)
class Change:
    item_id: str
    from_phase: Phase | None
    to_phase: Phase


@dataclass(frozen=True, slots=True)
class Applied:
    doc: Doc  # the new document value; the engine writes it unless `noop`
    row_id: str
    item_id: str  # the primary item
    changes: tuple[Change, ...]  # primary first, then side effects
    noop: bool = False


@dataclass(frozen=True, slots=True)
class Refused:
    refusal: Refusal


# ---------------------------------------------------------------- JSON codec: ONE place that knows the JSON shape
_NONE = type(None)
_UNIONS = (typing.Union, types.UnionType)
_MAPPINGS = (collections.abc.Mapping, dict)


def to_json(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_json(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Mapping):
        return {str(k): to_json(v) for k, v in obj.items()}
    if isinstance(obj, (tuple, list, frozenset, set)):
        return [to_json(v) for v in obj]
    return obj


def _fail(path: str, message: str):
    raise InvalidArgs(f"{path}: {message}")


def from_json(tp: Any, data: Any, *, path: str = "$", strict: bool = True) -> Any:
    """JSON -> the dataclass tree `tp`. Strict: wrong types, bad enum members, failed patterns and unknown keys are
    InvalidArgs naming the JSON path. Not strict (a state.json written by a newer plugin): unknown keys are ignored."""
    origin = typing.get_origin(tp)
    if tp is Any:
        return data
    if origin in _UNIONS:
        inner = [a for a in typing.get_args(tp) if a is not _NONE]
        if data is None and len(inner) < len(typing.get_args(tp)):
            return None
        if len(inner) != 1:
            _fail(path, "unsupported union")
        return from_json(inner[0], data, path=path, strict=strict)
    if origin is Literal:
        if data not in typing.get_args(tp):
            _fail(path, f"must be one of {list(typing.get_args(tp))}")
        return data
    if origin is tuple:
        if not isinstance(data, list):
            _fail(path, "must be an array")
        return tuple(
            from_json(typing.get_args(tp)[0], v, path=f"{path}[{i}]", strict=strict) for i, v in enumerate(data)
        )
    if origin in _MAPPINGS or tp is dict:
        if not isinstance(data, dict):
            _fail(path, "must be an object")
        vt = typing.get_args(tp)[1] if typing.get_args(tp) else Any
        return {k: from_json(vt, v, path=f"{path}.{k}", strict=strict) for k, v in data.items()}
    if isinstance(tp, type) and is_dataclass(tp):
        return _dataclass_from_json(tp, data, path, strict)
    if isinstance(tp, type) and issubclass(tp, Enum):
        try:
            return tp(data)
        except ValueError:
            _fail(path, f"must be one of {[m.value for m in tp]}")
    if tp is bool:
        if not isinstance(data, bool):
            _fail(path, "must be a boolean")
        return data
    if tp is int:
        if isinstance(data, bool) or not isinstance(data, int):
            _fail(path, "must be an integer")
        return data
    if tp is str:
        if not isinstance(data, str):
            _fail(path, "must be a string")
        return data
    raise TypeError(f"unsupported type {tp!r} at {path}")


def _check_bounds(f, value: Any, path: str) -> None:
    most, chars = f.metadata.get("max_items"), f.metadata.get("max_length")
    if most is not None and isinstance(value, tuple) and len(value) > most:
        _fail(path, f"at most {most} entries")
    if chars is not None:
        for text in value if isinstance(value, tuple) else (value,):
            if isinstance(text, str) and len(text) > chars:
                _fail(path, f"at most {chars} characters each")


def _dataclass_from_json(cls: type, data: Any, path: str, strict: bool) -> Any:
    if not isinstance(data, dict):
        _fail(path, "must be an object")
    hints = typing.get_type_hints(cls)
    names = {f.name for f in fields(cls)}
    if strict and (extra := sorted(set(data) - names)):
        _fail(path, f"unknown field(s) {extra}")
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            if f.default is MISSING and f.default_factory is MISSING:
                _fail(path, f"missing required field {f.name!r}")
            continue
        value = from_json(hints[f.name], data[f.name], path=f"{path}.{f.name}", strict=strict)
        _check_bounds(f, value, f"{path}.{f.name}")
        pattern = f.metadata.get("pattern")
        if pattern:
            for text in value if isinstance(value, tuple) else (value,):
                if isinstance(text, str) and not re.fullmatch(pattern, text):
                    _fail(f"{path}.{f.name}", f"must match {pattern}")
        kwargs[f.name] = value
    return cls(**kwargs)


def json_schema(tp: Any) -> dict:
    """JSON Schema derived from the SAME dataclasses that parse the arguments: no second list to keep in step."""
    origin = typing.get_origin(tp)
    if origin in _UNIONS:
        inner = [a for a in typing.get_args(tp) if a is not _NONE]
        return json_schema(inner[0])
    if origin is Literal:
        return {"enum": list(typing.get_args(tp))}
    if origin is tuple:
        return {"type": "array", "items": json_schema(typing.get_args(tp)[0])}
    if origin in _MAPPINGS:
        return {"type": "object", "additionalProperties": json_schema(typing.get_args(tp)[1])}
    if isinstance(tp, type) and is_dataclass(tp):
        hints = typing.get_type_hints(tp)
        props, required = {}, []
        for f in fields(tp):
            prop = json_schema(hints[f.name])
            pattern = f.metadata.get("pattern")
            if pattern:
                (prop["items"] if prop.get("type") == "array" else prop)["pattern"] = f"^{pattern}$"
            if "max_items" in f.metadata:
                prop["maxItems"] = f.metadata["max_items"]
            if "max_length" in f.metadata:
                (prop["items"] if prop.get("type") == "array" else prop)["maxLength"] = f.metadata["max_length"]
            if "doc" in f.metadata:
                prop["description"] = f.metadata["doc"]
            props[f.name] = prop
            if f.default is MISSING and f.default_factory is MISSING:
                required.append(f.name)
        schema: dict = {"type": "object", "properties": props, "additionalProperties": False}
        if required:
            schema["required"] = required
        return schema
    if isinstance(tp, type) and issubclass(tp, Enum):
        return {"enum": [m.value for m in tp]}
    return {"type": {bool: "boolean", int: "integer", str: "string"}.get(tp, "object")} if tp is not Any else {}
