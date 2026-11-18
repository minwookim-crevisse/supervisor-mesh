"""The transition table and the pure decision function. PURE: no I/O, no clock, no environment, no subprocess.

`TABLE` is data: one `Row` per row of the TLA model's `Table` constant (27 rows, the model's order). Guards, effects and
triggers are referenced by NAME and resolved in the registries below, so the table reads like section 4 of the design.
Everything that must agree with it is derived from it, never re-listed:
  * the lookup index (verb, actor, phase) -> row                           (`decide`)
  * `model_rows()`: the (verb, caller, from, to) outcomes                  (tests: conformance with the .tla)
  * the `work_transition` verb enum                                        (`surface_verbs`, used by tools.py)
  * which facts to gather unlocked and which CAS stamp to take             (`plan`, via `Row.needs` / `Row.stamp`)

Public surface: TABLE, Row, Count, Reset, plan, decide, reconcile, mesh_steps_due, check_invariants, model_rows,
surface_verbs, args_type, stamp_of, enter_limit, GUARDS, EFFECTS, TRIGGERS.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Callable, Literal

from . import layers
from .model import (
    BUGFIX,
    CREATED,
    NO_FACTS,
    NON_TERMINAL,
    OVERRIDE_TARGETS,
    PREPARE_KIND,
    ActionKind,
    Actor,
    Applied,
    Attempt,
    Caller,
    CallerKind,
    CancelArgs,
    Change,
    Config,
    Contract,
    DepStatus,
    DispatchSend,
    Doc,
    Evidence,
    EvidenceKind,
    Fact,
    Facts,
    FailArgs,
    FailingBefore,
    Failure,
    FailureClass,
    HistoryEntry,
    InvariantViolation,
    Item,
    Links,
    NewArgs,
    NoArgs,
    OverrideArgs,
    PendingAction,
    Phase,
    ReadyArgs,
    ReassignArgs,
    Refusal,
    RefusalCode,
    Refused,
    RejectArgs,
    Reply,
    ReplyArgs,
    Request,
    ReviseArgs,
    RunStatus,
    Scope,
    SplitArgs,
    Stamp,
    StampKind,
    StartArgs,
    SubmitArgs,
    TypeSpec,
    Verb,
    VerifyArgs,
    repro_hash,
)

P = Phase
SAME: Literal["unchanged"] = "unchanged"  # the model's "unchanged" token
WORK_NEW, WORK_DISPATCH, WORK_TRANSITION = "work_new", "work_dispatch", "work_transition"


@dataclass(frozen=True, slots=True)
class Count:
    """+1 on this row; over the limit the row's effect is REPLACED by `enter_limit` (sole writer of blocked(limit))."""

    counter: str  # Counters field
    limit: str  # Config field holding the limit
    over_reason: str  # Failure.reason recorded at the entry
    only_after_first_attempt: bool = False
    skip_if_free: bool = False  # consumes Counters.next_start_free (first start after an unblock / limit resume)


@dataclass(frozen=True, slots=True)
class Reset:
    """Both counters back to 0 when the item LEAVES this phase through this row (blocked(limit) resume, D52)."""

    on_leaving: Phase


@dataclass(frozen=True, slots=True)
class Row:
    id: str  # unique; the model's NoRow_<id> probe name where one exists
    verb: Verb
    actor: Actor
    src: frozenset  # Phase, or None = the item does not exist yet
    dst: frozenset  # Phase, or SAME
    args: type
    effect: str | None  # None = data only in the MVP (Slack watcher rows)
    guards: tuple[str, ...] = ()  # evaluated in order; the first Verdict is the refusal
    needs: frozenset = frozenset()  # Fact names observed UNLOCKED before `decide`
    counters: tuple = ()
    stamp: StampKind = StampKind.NONE  # compared under the lock when `needs` is non-empty
    via: str | None = None  # which MCP tool exposes the row
    trigger: str | None = None  # (mesh) rows only: which items the row applies to now


def row(
    id_,
    verb,
    actor,
    src,
    dst,
    args,
    effect,
    *,
    guards=(),
    needs=(),
    counters=(),
    stamp=StampKind.NONE,
    via=None,
    trigger=None,
) -> Row:
    return Row(
        id_,
        verb,
        actor,
        frozenset(src),
        frozenset(dst),
        args,
        effect,
        tuple(guards),
        frozenset(needs),
        tuple(counters),
        stamp,
        via,
        trigger,
    )


NT = NON_TERMINAL
V, A = StampKind.VERSION, StampKind.ATTEMPT
Pm, Own, Mesh = Actor.PM, Actor.LEAD_OWN, Actor.MESH
RETRY = Count("retry", "retry_limit", "retry", only_after_first_attempt=True, skip_if_free=True)
ENV = Count("environment_unblock", "environment_limit", "environment")

# Section 4, one Row per row of the model's Table, in the model's order. Kept compact on purpose (`fmt: off`): the table
# should read like section 4 of the design.
# fmt: off
TABLE: tuple[Row, ...] = (
    row("new", Verb.NEW, Pm, {None}, {P.DRAFT}, NewArgs, "new_item", via=WORK_NEW),
    row("draft", Verb.DRAFT, Actor.SLACK_WATCHER, {None}, {P.DRAFT}, NoArgs, None),                 # data only
    row("attach", Verb.ATTACH, Actor.SLACK_WATCHER, NT, {SAME}, NoArgs, None),                      # data only
    row("reply", Verb.REPLY, Pm, NT, {SAME}, ReplyArgs, "attach_terminal_reply",
        guards=("event_unconsumed",), needs={Fact.OPERATOR_EVENT}, stamp=V, via=WORK_TRANSITION),
    row("revise", Verb.REVISE, Pm, {P.READY}, {P.DRAFT}, ReviseArgs, "revise", guards=("reason_given",),
        via=WORK_TRANSITION),
    row("ready", Verb.READY, Pm, {P.DRAFT, P.REWORK, P.BLOCKED_AWAITING_USER, P.BLOCKED_LIMIT}, {P.READY}, ReadyArgs,
        "ready",
        guards=("class_route", "contract_revised_on_resume", "contract_complete", "depends_wellformed",
                "depends_not_cancelled", "fresh_reply_on_limit_resume", "repro_fails_at_base"),
        needs={Fact.REPRO}, counters=(Reset(P.BLOCKED_LIMIT),), stamp=V, via=WORK_TRANSITION),
    row("dispatch", Verb.DISPATCH, Pm, {P.READY}, {SAME}, DispatchSend, "dispatch",
        guards=("role_has_live_lead",), needs={Fact.LEAD_AGENT}, stamp=V, via=WORK_DISPATCH),
    row("start", Verb.START, Own, {P.READY}, {P.RUNNING, P.BLOCKED_LIMIT}, StartArgs, "start",
        guards=("depends_satisfied", "lead_is_live", "head_known", "config_as_pinned"),
        needs={Fact.LEAD_LIVE, Fact.HEAD},
        counters=(RETRY,), stamp=V, via=WORK_TRANSITION),
    row("submit", Verb.SUBMIT, Own, {P.RUNNING}, {P.VERIFYING}, SubmitArgs, "submit",
        guards=("attempt_current", "commit_changed_and_known"), needs={Fact.COMMIT_EXISTS, Fact.COMMIT_DESCENDS},
        stamp=V, via=WORK_TRANSITION),
    row("verify", Verb.VERIFY, Own, {P.VERIFYING}, {P.VERIFIED}, VerifyArgs, "verify",
        guards=("attempt_current", "verification_passes", "evidence_kinds_present", "inputs_unchanged",
                "failing_before_matches"),
        needs={Fact.VERIFICATION}, stamp=A, via=WORK_TRANSITION),
    row("fail", Verb.FAIL, Own, {P.RUNNING, P.VERIFYING}, {P.REWORK, P.BLOCKED_AWAITING_USER, P.BLOCKED_ENVIRONMENT},
        FailArgs, "fail", guards=("attempt_current", "class_allowed_for_caller", "too_large_only_with_conflict"),
        via=WORK_TRANSITION),
    row("fail_pm", Verb.FAIL, Pm, {P.RUNNING, P.VERIFYING}, {P.BLOCKED_ENVIRONMENT}, FailArgs, "fail",
        guards=("class_allowed_for_caller", "too_large_only_with_conflict", "lead_absent_or_dead"),
        needs={Fact.LEAD_LIVE}, stamp=V, via=WORK_TRANSITION),
    row("reassign", Verb.REASSIGN, Pm, {P.REWORK}, {P.READY}, ReassignArgs, "reassign",
        guards=("class_route", "depends_not_cancelled"), via=WORK_TRANSITION),
    row("split", Verb.SPLIT, Pm, {P.REWORK}, {P.BLOCKED_CHILDREN}, SplitArgs, "split", guards=("split_allowed",),
        via=WORK_TRANSITION),
    row("mesh_verifying", Verb.MESH, Mesh, {P.BLOCKED_CHILDREN}, {P.VERIFYING}, NoArgs, "mesh_children_done",
        guards=("merged_commit_known",), needs={Fact.MERGED_COMMIT}, stamp=V, trigger="children_all_closed"),
    row("mesh_rework", Verb.MESH, Mesh, {P.BLOCKED_CHILDREN}, {P.REWORK}, NoArgs, "mesh_child_cancelled",
        trigger="has_cancelled_child"),
    row("mesh_verified", Verb.MESH, Mesh, {P.VERIFYING}, {P.VERIFIED}, NoArgs, "mesh_parent_verify_pass",
        guards=("verification_passes", "evidence_kinds_present", "inputs_unchanged"),
        needs={Fact.VERIFICATION}, stamp=A, trigger="parent_run_due"),
    row("mesh_verify_fail", Verb.MESH, Mesh, {P.VERIFYING}, {P.REWORK}, NoArgs, "mesh_parent_verify_fail",
        guards=("parent_run_failed",), needs={Fact.VERIFICATION}, stamp=A, trigger="parent_run_due"),
    row("mesh_dependency", Verb.MESH, Mesh, {P.READY}, {P.BLOCKED_AWAITING_USER}, NoArgs,
        "mesh_dependency_cancelled", trigger="ready_dependent_of_cancelled"),
    row("mesh_follow_up", Verb.MESH, Mesh, {P.CLOSED}, {SAME}, NoArgs, "mesh_follow_up", trigger="closed_with_flag"),
    row("unblock", Verb.UNBLOCK, Pm, {P.BLOCKED_ENVIRONMENT}, {P.READY, P.BLOCKED_LIMIT}, NoArgs, "unblock",
        guards=("depends_not_cancelled", "precheck_passes"), needs={Fact.PRECHECK}, counters=(ENV,), stamp=V,
        via=WORK_TRANSITION),
    row("reject", Verb.REJECT, Pm, {P.VERIFIED}, {P.REWORK, P.BLOCKED_AWAITING_USER}, RejectArgs, "reject",
        guards=("reject_has_trigger",), via=WORK_TRANSITION),
    row("ack", Verb.ACK, Pm, NT, {SAME}, NoArgs, "ack", guards=("has_unacked_replies",), via=WORK_TRANSITION),
    row("close", Verb.CLOSE, Pm, {P.VERIFIED}, {P.CLOSED}, NoArgs, "close", guards=("close_gates",),
        via=WORK_TRANSITION),
    row("cancel", Verb.CANCEL, Pm, NT | {P.CANCELLED}, {P.CANCELLED}, CancelArgs, "cancel", via=WORK_TRANSITION),
    row("revalidate", Verb.REVALIDATE, Pm, {P.VERIFIED, P.CLOSED}, {SAME}, NoArgs, "revalidate_flag"),   # data only
    row("override", Verb.OVERRIDE, Actor.OPERATOR, CREATED, CREATED - {P.BLOCKED_LIMIT}, OverrideArgs, "override",
        guards=("override_target_allowed", "event_unconsumed", "event_fresh", "override_names_item"),
        needs={Fact.OPERATOR_EVENT},
        stamp=V, via=WORK_TRANSITION),
)
# fmt: on


# ---------------------------------------------------------------- derived structures
MESH_ROWS = tuple(x for x in TABLE if x.verb is Verb.MESH)
BY_ID = {x.id: x for x in TABLE}
INDEX: dict = {}
for _row in (x for x in TABLE if x.verb is not Verb.MESH):
    for _src in _row.src:
        _key = (_row.verb, _row.actor, _src)
        assert _key not in INDEX, f"ambiguous table rows {INDEX[_key].id} / {_row.id}"
        INDEX[_key] = _row
assert len(BY_ID) == len(TABLE), "duplicate row ids"


def model_rows() -> frozenset:
    """(verb, caller, from, to) outcomes with "(none)" for no item and "unchanged" expanded to the from-state: the
    same expansion as the .tla's RowTo, so equality with the parsed model Table is the conformance test."""
    out = set()
    for x in TABLE:
        for s in x.src:
            frm = "(none)" if s is None else s.value
            for d in x.dst:
                out.add((x.verb.value, x.actor.value, frm, frm if d == SAME else P(d).value))
    return frozenset(out)


def surface_verbs(tool: str) -> tuple[str, ...]:
    """Unique verbs of the rows a tool exposes, in table order (the `work_transition` verb enum)."""
    seen: list[str] = []
    for x in TABLE:
        if x.via == tool and x.verb.value not in seen:
            seen.append(x.verb.value)
    return tuple(seen)


def args_type(verb: Verb) -> type:
    """One args class per verb: the rows of a verb must agree."""
    classes = {x.args for x in TABLE if x.verb is verb}
    assert len(classes) == 1, verb
    return next(iter(classes))


# ------------------------------------------------------------ guards, effects, triggers: registries (names -> logic)
@dataclass(frozen=True, slots=True)
class Ctx:
    """Everything a guard or an effect may read: the FRESH document, the item, the request, facts and config values."""

    doc: Doc
    item: Item | None
    req: Request
    facts: Facts
    cfg: Config
    row: Row


@dataclass(frozen=True, slots=True)
class Verdict:
    code: RefusalCode
    detail: str


@dataclass(frozen=True, slots=True)
class Guard:
    name: str
    codes: frozenset
    check: Callable[[Ctx], Verdict | None]


GUARDS: dict[str, Guard] = {}
EFFECTS: dict[str, Callable[[Ctx], Doc]] = {}
TRIGGERS: dict[str, Callable[[Doc], tuple[str, ...]]] = {}


def guard(*codes: RefusalCode):
    """Declares the refusal codes a guard may return; `decide` fails loudly on any other code."""

    def wrap(fn):
        GUARDS[fn.__name__] = Guard(fn.__name__, frozenset(codes), fn)
        return fn

    return wrap


def effect(fn):
    EFFECTS[fn.__name__] = fn
    return fn


def trigger(fn):
    TRIGGERS[fn.__name__] = fn
    return fn


# ---------------------------------------------------------------- planning and deciding
@dataclass(frozen=True, slots=True)
class Plan:
    row: Row
    needs: frozenset
    stamp: Stamp | None  # None when the row needs no outside facts: one critical section, no CAS


def stamp_of(item: Item, kind: StampKind) -> tuple:
    """The part of the item a compare-and-set compares (section 8, D76)."""
    if kind is StampKind.VERSION:
        return (item.version,)
    if kind is StampKind.ATTEMPT:
        a = item.current_attempt
        return (item.phase.value, a.id if a else 0, item.contract.version, a.commit if a else None)
    return ()


def actor_for(caller, item: Item | None, verb: Verb) -> Actor | None:
    """Caller -> the table's caller for THIS item (D82). The operator is never inferred: a PM calling `override` is
    matched as `operator` and the row's event guards decide."""
    if caller.kind is CallerKind.MESH:
        return Actor.MESH
    if caller.kind is CallerKind.PM:
        return Actor.OPERATOR if verb is Verb.OVERRIDE else Actor.PM
    if caller.kind is CallerKind.LEAD:
        return Actor.LEAD_OWN if item is not None and item.contract.role == caller.role else Actor.LEAD_OTHER
    return None


def _refuse(
    req: Request, code: RefusalCode, detail: str, row: Row | None = None, actor: Actor | None = None
) -> Refused:
    return Refused(
        Refusal(
            req.verb.value,
            code,
            detail,
            req.at,
            req.caller.kind.value,
            actor.value if actor else (row.actor.value if row else None),
            req.item_id,
            row.id if row else None,
            req.caller.agent_uid,
        )
    )


def _resolve(doc: Doc, req: Request) -> tuple[Row, Item | None] | Refused:
    """Which row applies to this request, or why none does. Refuses: unknown caller, unknown item, not in the table."""
    if req.caller.kind is CallerKind.UNKNOWN:
        return _refuse(req, RefusalCode.UNKNOWN_CALLER, "the caller has no role label (D82): no verbs")
    item = doc.items.get(req.item_id or "")
    if req.verb is not Verb.NEW and item is None:
        return _refuse(req, RefusalCode.UNKNOWN_ITEM, f"no item {req.item_id!r}")
    if req.verb is Verb.MESH:
        mesh_row = BY_ID.get(req.row_id or "")
        if (
            req.caller.kind is not CallerKind.MESH
            or mesh_row is None
            or mesh_row.verb is not Verb.MESH
            or item.phase not in mesh_row.src
            or req.item_id not in TRIGGERS[mesh_row.trigger](doc)
        ):
            return _refuse(req, RefusalCode.NOT_IN_TABLE, "no (mesh) row is due for this item")
        return mesh_row, item
    actor = actor_for(req.caller, item, req.verb)
    row_ = INDEX.get((req.verb, actor, item.phase if item else None))
    if row_ is None or row_.effect is None:
        where = item.phase.value if item else "(none)"
        return _refuse(
            req,
            RefusalCode.NOT_IN_TABLE,
            f"{req.verb.value} x {actor.value} x {where} is not in the table",
            actor=actor,
        )
    if not isinstance(req.args, row_.args):
        return _refuse(req, RefusalCode.ARGS_INVALID, f"{req.verb.value} takes {row_.args.__name__}", row_)
    return row_, item


def plan(doc: Doc, req: Request) -> Plan | Refused:
    """Lock-free look at a snapshot: which row applies, what to observe unlocked and compare later. Never writes."""
    res = _resolve(doc, req)
    if isinstance(res, Refused):
        return res
    row_, item = res
    stamp = Stamp(row_.stamp, stamp_of(item, row_.stamp)) if row_.needs and item is not None else None
    return Plan(row_, row_.needs, stamp)


def _put(doc: Doc, *items: Item) -> Doc:
    return replace(doc, items={**doc.items, **{i.id: i for i in items}})


# The reasons the (mesh) rows record in history (they take no args): (the row's own item, the items it changes beside)
MESH_REASONS = {
    "mesh_dependency": ("dependency_cancelled", None),  # D71
    "mesh_rework": (None, "parent_rework"),  # D66: the remaining active children of the parent are cancelled
}


def _stamp_changes(old: Doc, new: Doc, req: Request, row_: Row, primary: str | None) -> Doc:
    """Uniform bookkeeping for EVERY item an effect changed: version +1 and one history entry. Effects never touch
    `version` or `history`, so a new row cannot forget them."""
    items = dict(new.items)
    for item_id, n in new.items.items():
        o = old.items.get(item_id)
        if o == n:
            continue
        primary_reason, side_reason = MESH_REASONS.get(row_.id, (None, None))
        if item_id == primary:
            note = primary_reason or getattr(req.args, "reason", "") or getattr(req.args, "note", "")
        else:
            note = side_reason or f"side effect of {primary} ({row_.id})"
        entry = HistoryEntry(
            req.at, req.verb, row_.id, row_.actor, req.caller.agent_uid, o.phase if o else None, n.phase, note
        )
        items[item_id] = replace(n, version=o.version + 1 if o else 1, history=n.history + (entry,))
    return replace(new, items=items)


def _apply_counters(ctx: Ctx) -> tuple[Ctx, str | None]:
    """Section 6, declaratively: +1 per Count, reset on leaving a phase; over a limit the caller replaces the effect."""
    item, over = ctx.item, None
    for c in ctx.row.counters:
        if isinstance(c, Reset):
            if item.phase is c.on_leaving:
                item = replace(item, counters=replace(item.counters, retry=0, environment_unblock=0))
            continue
        counted = (not c.only_after_first_attempt or bool(item.attempts)) and not (
            c.skip_if_free and item.counters.next_start_free
        )
        value = getattr(item.counters, c.counter) + (1 if counted else 0)
        item = replace(item, counters=replace(item.counters, **{c.counter: value}))
        if value > getattr(ctx.cfg, c.limit):
            over = c.over_reason
    return replace(ctx, doc=_put(ctx.doc, item), item=item), over


def decide(doc: Doc, req: Request, facts: Facts, cfg: Config, stamp: Stamp | None) -> Applied | Refused:
    """Under the lock, on the FRESH document: (1) the row again, (2) CAS stamp, (3) guards, (4) counters, (5) effect,
    (6) uniform bookkeeping, (7) the row's post-conditions on the effect's result, (8) reconcile (the fact-free (mesh)
    rows), (9) the document invariants. A refusal writes nothing; a failed post-condition or invariant raises
    InvariantViolation, and the caller never gets a document to write."""
    res = _resolve(doc, req)
    if isinstance(res, Refused):
        return res
    row_, item = res
    if stamp is not None and item is not None and stamp != Stamp(row_.stamp, stamp_of(item, row_.stamp)):
        return _refuse(req, RefusalCode.STALE, "the item moved since the facts were gathered: result discarded", row_)
    ctx = Ctx(doc, item, req, facts, cfg, row_)
    for name in row_.guards:
        g = GUARDS[name]
        verdict = g.check(ctx)
        if verdict is not None:
            if verdict.code not in g.codes:
                raise InvariantViolation(f"guard {g.name} returned undeclared code {verdict.code}")
            return _refuse(req, verdict.code, verdict.detail, row_)
    ctx, over = _apply_counters(ctx) if row_.counters else (ctx, None)
    after = enter_limit(ctx, over) if over else EFFECTS[row_.effect](ctx)
    primary = next(iter(set(after.items) - set(doc.items)), None) if req.verb is Verb.NEW else req.item_id
    stepped = _stamp_changes(doc, after, req, row_, primary)
    _post_conditions(doc, stepped, row_, primary)
    final = reconcile(stepped, req.at)
    changes = tuple(
        Change(i, doc.items[i].phase if i in doc.items else None, final.items[i].phase)
        for i in sorted(final.items, key=lambda i: (i != primary, i))
        if doc.items.get(i) != final.items[i]
    )
    violations = check_invariants(final)
    if violations:
        raise InvariantViolation("; ".join(f"inv {v.invariant}: {v.detail}" for v in violations))
    return Applied(final, row_.id, primary, changes, noop=not changes)


@dataclass(frozen=True, slots=True)
class Violation:
    invariant: int
    item_id: str | None
    detail: str


def _post_conditions(old: Doc, new: Doc, row_: Row, primary: str | None) -> None:
    """The row, not the effect, decides where the primary item may land; other changes are declared side effects."""
    item = new.items.get(primary)
    before = old.items.get(primary)
    if item is None:
        raise InvariantViolation(f"{row_.id}: no primary item")
    if before is None or item != before:  # an unchanged primary is a no-op (cancel of a cancelled item)
        landed = SAME if before is not None and item.phase is before.phase else item.phase
        if landed not in row_.dst:
            raise InvariantViolation(
                f"{row_.id}: item landed in {item.phase.value}, the table allows {sorted(map(str, row_.dst))}"
            )
    for other_id, n in new.items.items():
        o = old.items.get(other_id)
        if other_id == primary or o == n:
            continue
        declared = (o is None and n.phase is Phase.DRAFT and primary in (n.links.parent, n.links.follows)) or (
            o is not None
            and o.phase in NON_TERMINAL
            and n.phase is Phase.CANCELLED
            and _descends_from(new, other_id, primary)
        )
        if not declared:
            raise InvariantViolation(f"{row_.id}: {other_id} changed and is not a declared side effect of {primary}")


def _descends_from(doc: Doc, item_id: str, ancestor: str) -> bool:
    seen = set()
    while item_id not in seen:
        seen.add(item_id)
        parent = doc.items[item_id].links.parent if item_id in doc.items else None
        if parent is None:
            return False
        if parent == ancestor:
            return True
        item_id = parent
    return False


@dataclass(frozen=True, slots=True)
class Due:
    row: Row
    item_id: str


def _due(doc: Doc, *, needs_facts: bool) -> tuple[Due, ...]:
    """(mesh) rows whose trigger holds now, in table order and then item id. STATELESS: recomputed from the document, so
    a crash between a write and its follow-up step loses nothing."""
    return tuple(Due(r, i) for r in MESH_ROWS if bool(r.needs) is needs_facts for i in TRIGGERS[r.trigger](doc))


def mesh_steps_due(doc: Doc) -> tuple[Due, ...]:
    """The (mesh) rows that NEED facts (children done: the merged commit; the split parent's own verification run)."""
    return _due(doc, needs_facts=True)


def reconcile(doc: Doc, at: str = "") -> Doc:
    """Applies the (mesh) rows that need no facts until none is due. Deterministic and terminating: every application
    strictly moves an item or creates the one follow-up its trigger then stops matching."""
    for _ in range(4 * len(doc.items) + 16):
        due = _due(doc, needs_facts=False)
        if not due:
            return doc
        row_, item_id = due[0].row, due[0].item_id
        step = Request(Verb.MESH, Caller.mesh(), NoArgs(), at, item_id, row_.id)
        after = EFFECTS[row_.effect](Ctx(doc, doc.items[item_id], step, NO_FACTS, Config(), row_))
        doc = _stamp_changes(doc, after, step, row_, item_id)
    raise InvariantViolation("reconcile did not reach a fixpoint")


def enter_limit(ctx: Ctx, reason: str) -> Doc:
    """THE ONLY writer of blocked(limit), FailureClass.LIMIT and limit_seq (invariant 4, D73). Stamps the entry with
    the next mesh seq (D74) and leaves a pending action so the PM sees it."""
    item = ctx.item
    seq = ctx.doc.seq + 1
    attempt = item.current_attempt
    failure = Failure(FailureClass.LIMIT, reason, (), attempt.id if attempt else None, "mesh", seq)
    action = PendingAction(_next_action_id(item, "a"), ActionKind.PM_ATTENTION, {"reason": f"limit: {reason}"})
    limited = replace(
        item,
        phase=Phase.BLOCKED_LIMIT,
        limit_seq=seq,
        failures=item.failures + (failure,),
        pending_actions=item.pending_actions + (action,),
    )
    return replace(_put(ctx.doc, limited), seq=seq)


def check_invariants(doc: Doc) -> tuple[Violation, ...]:
    """The doc-level half of section 10, run as a post-condition of every write: the invariants that are STATE
    properties (4 limit only by the mesh, 6 verified is bound, 7 closed has no unacknowledged reply, 8 depends resolve
    and are acyclic, 9 a closed split parent has evidence on its own current attempt, 13 no active child under a
    parent in rework or cancelled, 14 no ready item depends on a cancelled one). Transition properties (1, 5, 10, 11,
    15 and the close gates) are guaranteed by the guards and proven by the walk in the tests.

    Invariant 8 is checked from `ready` on: unlike the model, a draft may carry unvalidated depends (`new` records
    them), and a cancelled item is out of the graph."""
    out: list[Violation] = []

    def bad(n: int, item: Item, detail: str) -> None:
        out.append(Violation(n, item.id, detail))

    for item in doc.items.values():
        if item.phase is Phase.BLOCKED_LIMIT and not (item.failures and item.failures[-1].cls is FailureClass.LIMIT):
            bad(4, item, "blocked(limit) without a limit record")
        if any(f.cls is FailureClass.LIMIT and f.actor != "mesh" for f in item.failures):
            bad(4, item, "a limit record not written by the mesh")
        problem = item.binding_error()
        if problem:
            bad(6, item, problem)
        if item.phase is Phase.CLOSED and any(not r.acknowledged for r in item.replies):
            bad(7, item, "closed with an unacknowledged reply")
        if item.phase not in (Phase.DRAFT, Phase.CANCELLED):
            problem = _depends_problem(doc, item.id, item.contract.depends)
            if problem:
                bad(8, item, problem)
        if item.phase is Phase.CLOSED and item.links.children:
            if item.current_attempt is None or item.current_attempt.evidence is None:
                bad(9, item, "a closed split parent without its own verification")
        if item.phase in (Phase.REWORK, Phase.CANCELLED) and any(
            ch in doc.items and not doc.items[ch].phase.terminal for ch in item.links.children
        ):
            bad(13, item, "an active child under a parent in rework or cancelled")
        if item.phase is Phase.READY and any(doc.dep_status(d) is DepStatus.CANCELLED for d in item.contract.depends):
            bad(14, item, "ready with a cancelled dependency")
    return tuple(out)


@effect
def new_item(c: Ctx) -> Doc:
    a = c.req.args
    item_id, next_id = _new_id(c.doc)
    contract = Contract(0, a.goal, a.done_criteria, a.role, a.type, dict(a.type_inputs), a.scope, a.depends, a.repro)
    return replace(_put(c.doc, Item(item_id, Phase.DRAFT, 0, contract)), next_id=next_id)


# ---------------------------------------------------------------- shared rule data and helpers
# Section 5 as data: where each class sends an item, and which verbs lead back out of `rework`.
ROUTE_OF = {
    FailureClass.DEFECT: Phase.REWORK,
    FailureClass.CONFLICT: Phase.REWORK,
    FailureClass.AMBIGUITY: Phase.BLOCKED_AWAITING_USER,
    FailureClass.ENVIRONMENT: Phase.BLOCKED_ENVIRONMENT,
}
WAY_BACK = {Verb.READY: {FailureClass.CONFLICT, FailureClass.AMBIGUITY}, Verb.REASSIGN: {FailureClass.DEFECT}}


def _patched(c: Ctx) -> Contract:
    """The contract `ready` would record: the current one with the optional patch applied (version not yet bumped)."""
    patch = getattr(c.req.args, "patch", None)
    if patch is None:
        return c.item.contract
    changes = {f.name: getattr(patch, f.name) for f in fields(patch) if getattr(patch, f.name) is not None}
    return replace(c.item.contract, **changes)


def _verdict(code: RefusalCode, detail: str) -> Verdict:
    return Verdict(code, detail)


def _depends_problem(doc: Doc, item_id: str, depends: tuple[str, ...]) -> str | None:
    """Unknown id, self-dependency or a cycle through the recorded depends (invariant 8)."""
    for d in depends:
        if d == item_id:
            return f"{item_id} depends on itself"
        if d not in doc.items and d not in doc.legacy:
            return f"unknown dependency {d}"
    seen, stack = set(), list(depends)
    while stack:
        d = stack.pop()
        if d == item_id:
            return f"dependency cycle through {item_id}"
        if d in seen or d not in doc.items:
            continue
        seen.add(d)
        stack.extend(doc.items[d].contract.depends)
    return None


# ---------------------------------------------------------------- guards: ready / reassign / revise
@guard(RefusalCode.CLASS_ROUTE)
def class_route(c: Ctx) -> Verdict | None:
    """Section 5 way back: from rework, the latest failure's class must allow THIS verb."""
    if c.item.phase is not Phase.REWORK:
        return None
    failure = c.item.latest_failure
    if failure is None or failure.cls not in WAY_BACK[c.req.verb]:
        got = failure.cls.value if failure else "no failure record"
        return _verdict(RefusalCode.CLASS_ROUTE, f"{c.req.verb.value} is not the way back for {got}")
    return None


@guard(RefusalCode.CONTRACT_NOT_REVISED)
def contract_revised_on_resume(c: Ctx) -> Verdict | None:
    """Section 5's way back names WHAT the PM revises (stricter than the model's abstraction):
    conflict (rework)            scope or depends
    ambiguity (awaiting_user)    done criteria
    dependency_cancelled (D71)   depends
    blocked(limit)               anything in the contract (plus the fresh reply)"""
    k, old = _patched(c), c.item.contract
    phase = c.item.phase
    if phase is Phase.REWORK:
        failure = c.item.latest_failure
        if (
            failure is not None
            and failure.cls is FailureClass.CONFLICT
            and (k.scope, k.depends)
            == (
                old.scope,
                old.depends,
            )
        ):
            return _verdict(RefusalCode.CONTRACT_NOT_REVISED, "a conflict comes back with a revised scope or depends")
    elif phase is Phase.BLOCKED_AWAITING_USER:
        entry = next((h for h in reversed(c.item.history) if h.to_phase is Phase.BLOCKED_AWAITING_USER), None)
        if entry is not None and entry.row == "mesh_dependency":
            if k.depends == old.depends:
                return _verdict(
                    RefusalCode.CONTRACT_NOT_REVISED, "a cancelled dependency comes back with revised depends"
                )
        elif k.done_criteria == old.done_criteria:
            return _verdict(RefusalCode.CONTRACT_NOT_REVISED, "an ambiguity comes back with revised done criteria")
    elif phase is Phase.BLOCKED_LIMIT and k == old:
        return _verdict(
            RefusalCode.CONTRACT_NOT_REVISED, "resuming needs a revised contract (done criteria or depends)"
        )
    return None


@guard(RefusalCode.REASON_REQUIRED)
def reason_given(c: Ctx) -> Verdict | None:
    if not c.req.args.reason.strip():
        return _verdict(RefusalCode.REASON_REQUIRED, "revise needs a reason")
    return None


@guard(RefusalCode.CONTRACT_INCOMPLETE)
def contract_complete(c: Ctx) -> Verdict | None:
    k = _patched(c)
    spec = c.cfg.types.get(k.type)
    if not k.goal.strip():
        return _verdict(RefusalCode.CONTRACT_INCOMPLETE, "no goal")
    if not k.done_criteria or any(not x.strip() for x in k.done_criteria):
        return _verdict(RefusalCode.CONTRACT_INCOMPLETE, "no done criteria")
    if not k.role:
        return _verdict(RefusalCode.CONTRACT_INCOMPLETE, "no role")
    if spec is None:
        return _verdict(RefusalCode.CONTRACT_INCOMPLETE, f"unknown type {k.type!r}")
    missing = [key for key in spec.required_inputs if not k.type_inputs.get(key)]
    if missing:
        return _verdict(RefusalCode.CONTRACT_INCOMPLETE, f"type {k.type} needs inputs {missing}")
    if k.type == BUGFIX and not k.repro:
        return _verdict(RefusalCode.CONTRACT_INCOMPLETE, "a bugfix needs a repro command (D46)")
    return None


@guard(RefusalCode.DEPENDS_INVALID)
def depends_wellformed(c: Ctx) -> Verdict | None:
    problem = _depends_problem(c.doc, c.item.id, _patched(c).depends)
    return _verdict(RefusalCode.DEPENDS_INVALID, problem) if problem else None


@guard(RefusalCode.DEPENDS_CANCELLED)
def depends_not_cancelled(c: Ctx) -> Verdict | None:
    """D81: ready, reassign and unblock refuse a cancelled dependency, so invariant 14 stays strict."""
    for d in _patched(c).depends:
        if c.doc.dep_status(d) is DepStatus.CANCELLED:
            return _verdict(RefusalCode.DEPENDS_CANCELLED, f"dependency {d} is cancelled")
    return None


@guard(RefusalCode.NO_FRESH_REPLY)
def fresh_reply_on_limit_resume(c: Ctx) -> Verdict | None:
    """D63 / D74: a reply with seq above limit_seq that no resume consumed yet."""
    if c.item.phase is Phase.BLOCKED_LIMIT and not _fresh_replies(c.item):
        return _verdict(RefusalCode.NO_FRESH_REPLY, "no fresh, unconsumed operator reply since the limit was reached")
    return None


def _fresh_replies(item: Item) -> list[Reply]:
    return [r for r in item.replies if item.limit_seq is not None and r.seq > item.limit_seq and not r.consumed]


@guard(RefusalCode.FACT_UNAVAILABLE, RefusalCode.REPRO_NOT_REPRODUCED, RefusalCode.REPRO_NOT_RUNNABLE)
def repro_fails_at_base(c: Ctx) -> Verdict | None:
    """Bugfix only (D46, D55): the repro must FAIL at the base commit. A pass is not reproduced; 126/127, a timeout or a
    spawn error is not runnable and refused."""
    if _patched(c).type != BUGFIX:
        return None
    repro = c.facts.repro
    if repro is None:
        return _verdict(RefusalCode.FACT_UNAVAILABLE, "ready needs the repro run at the base commit")
    if repro.verdict == "passes":
        return _verdict(RefusalCode.REPRO_NOT_REPRODUCED, "the repro passes at the base commit: not reproduced")
    if repro.verdict == "not_runnable":
        return _verdict(RefusalCode.REPRO_NOT_RUNNABLE, "the repro could not run (126/127, timeout or spawn error)")
    return None


# ---------------------------------------------------------------- effects: revise / ready
@effect
def revise(c: Ctx) -> Doc:
    return _put(c.doc, replace(c.item, phase=Phase.DRAFT))


def _pin(item: Item, cfg: Config) -> Item:
    """D100 / D106: every PM verb that enters `ready` (ready, reassign, unblock) pins the CURRENT verification config
    hash; `start` and `verify` check against it. A config-only change needs no revise: the next entry re-pins."""
    return replace(item, pinned_config_hash=cfg.verify_hash())


@effect
def ready(c: Ctx) -> Doc:
    item = _pin(c.item, c.cfg)
    contract = replace(_patched(c), version=item.contract.version + 1)
    changes: dict = {"phase": Phase.READY, "contract": contract}
    if contract.type == BUGFIX:
        repro = c.facts.repro
        changes["failing_before"] = FailingBefore(
            contract.version,
            repro_hash(contract.repro),
            repro.base_commit,
            repro.exit_code if repro.exit_code is not None else 1,
        )
    if item.phase is Phase.BLOCKED_LIMIT:  # D63: consume the reply, counters were reset
        fresh = max(_fresh_replies(item), key=lambda r: r.seq)
        changes["replies"] = tuple(replace(r, consumed=True) if r == fresh else r for r in item.replies)
        changes["counters"] = replace(item.counters, next_start_free=True)
    return _put(c.doc, replace(item, **changes))


# ---------------------------------------------------------------- guards: dispatch / start / submit / verify
def _next_action_id(item: Item, letter: str) -> str:
    n = sum(1 for a in item.pending_actions if a.action_id.startswith(f"{item.id}-{letter}")) + 1
    return f"{item.id}-{letter}{n}"


@guard(RefusalCode.ROLE_HAS_NO_LEAD)
def role_has_live_lead(c: Ctx) -> Verdict | None:
    if c.facts.lead_agent is None:
        return _verdict(
            RefusalCode.ROLE_HAS_NO_LEAD, f"no live agent labelled role={c.item.contract.role}; spawn one first"
        )
    return None


@guard(RefusalCode.DEPENDENCY_UNSATISFIED)
def depends_satisfied(c: Ctx) -> Verdict | None:
    """Invariant 8: a dependency is satisfied only by verified or closed (or a legacy row mapped done)."""
    for d in c.item.contract.depends:
        if c.doc.dep_status(d) is not DepStatus.SATISFIED:
            return _verdict(RefusalCode.DEPENDENCY_UNSATISFIED, f"dependency {d} is not verified or closed")
    return None


@guard(RefusalCode.FACT_UNAVAILABLE, RefusalCode.LEAD_NOT_LIVE)
def lead_is_live(c: Ctx) -> Verdict | None:
    if c.facts.lead_live is None:
        return _verdict(RefusalCode.FACT_UNAVAILABLE, "start needs to know that the lead's agent is live")
    if not c.facts.lead_live:
        return _verdict(RefusalCode.LEAD_NOT_LIVE, "the lead's agent is not live")
    return None


@guard(RefusalCode.FACT_UNAVAILABLE)
def head_known(c: Ctx) -> Verdict | None:
    if not c.facts.head:
        return _verdict(RefusalCode.FACT_UNAVAILABLE, "start records the project HEAD as the attempt's base commit")
    return None


@guard(RefusalCode.INPUTS_CHANGED)
def config_as_pinned(c: Ctx) -> Verdict | None:
    """D100: the verification config must be the one pinned when the item entered `ready` (an item recorded before
    pinning existed has no pin and records the current hash, as before)."""
    pinned = c.item.pinned_config_hash
    if pinned is not None and pinned != c.cfg.verify_hash():
        return _verdict(
            RefusalCode.INPUTS_CHANGED,
            "the verification config changed since the item entered ready: the PM re-enters ready to pin it",
        )
    return None


@guard(RefusalCode.ATTEMPT_STALE)
def attempt_current(c: Ctx) -> Verdict | None:
    """Section 8: older attempt ids are rejected. A lead must name the attempt `start` returned."""
    attempt = c.item.current_attempt
    named = getattr(c.req.args, "attempt_id", None)
    if attempt is None or named != attempt.id:
        return _verdict(
            RefusalCode.ATTEMPT_STALE, f"the current attempt is {attempt.id if attempt else 'none'}, got {named}"
        )
    return None


@guard(
    RefusalCode.COMMIT_UNCHANGED,
    RefusalCode.FACT_UNAVAILABLE,
    RefusalCode.COMMIT_UNKNOWN,
    RefusalCode.COMMIT_NOT_DESCENDANT,
)
def commit_changed_and_known(c: Ctx) -> Verdict | None:
    if c.req.args.commit == c.item.current_attempt.base_commit:
        return _verdict(RefusalCode.COMMIT_UNCHANGED, "the submitted commit equals the base commit")
    if c.facts.commit_exists is None:
        return _verdict(RefusalCode.FACT_UNAVAILABLE, "submit needs to know the commit exists")
    if not c.facts.commit_exists:
        return _verdict(RefusalCode.COMMIT_UNKNOWN, "the submitted commit does not exist")
    if c.item.current_attempt.base_commit is None:
        return None  # no base was recorded at start: nothing to descend from
    if c.facts.commit_descends is None:
        return _verdict(RefusalCode.FACT_UNAVAILABLE, "submit needs to know the commit descends from the base")
    if not c.facts.commit_descends:
        return _verdict(RefusalCode.COMMIT_NOT_DESCENDANT, "the submitted commit does not descend from the base commit")
    return None


def _run_problem(c: Ctx) -> Verdict | None:
    """Verification evidence rules (section 7) shared by `verify` and the split parent's own run: every command
    passed, the required evidence kinds are present, and nothing the run depended on moved (config, contract version,
    commit)."""
    v = c.facts.verification
    if v is None:
        return _verdict(
            RefusalCode.FACT_UNAVAILABLE, "no verification result: a lead's prose report never reaches verified"
        )
    prepare = next((r for r in v.runs if r.kind == PREPARE_KIND), None)
    if prepare is not None and prepare.status is not RunStatus.PASS:
        return _verdict(
            RefusalCode.PREPARE_FAILED,
            f"verify.prepare {prepare.status.value} (exit {prepare.exit_code}): an environment problem, not a defect;"
            " report it with fail cls=environment (D91)",
        )
    if not v.runs or any(r.status is not RunStatus.PASS for r in v.runs):
        matched = sorted({r.kind for r in v.runs if r.evidence})
        return _verdict(
            RefusalCode.VERIFICATION_FAILED,
            "not every verification command passed (not_run and timeout are not a pass)"
            + (f"; fail_if_output matched in: {', '.join(matched)} (see the evidence line)" if matched else ""),
        )
    spec = c.cfg.types.get(c.item.contract.type, TypeSpec())
    missing = sorted(set(spec.required_evidence) - {r.kind for r in v.runs})
    if missing:
        return _verdict(RefusalCode.EVIDENCE_MISSING, f"required evidence kinds not produced: {missing}")
    a = c.item.current_attempt
    now = c.cfg.verify_hash()
    pinned = c.item.pinned_config_hash
    if not (v.config_hash_before == v.config_hash_after == a.config_hash == now) or pinned not in (None, now):
        return _verdict(RefusalCode.INPUTS_CHANGED, "the verification config changed since start or during the run")
    if a.contract_version != c.item.contract.version or v.commit != a.commit:
        return _verdict(RefusalCode.INPUTS_CHANGED, "the contract version or the commit differs from the attempt's")
    return None


@guard(RefusalCode.FACT_UNAVAILABLE, RefusalCode.VERIFICATION_FAILED, RefusalCode.PREPARE_FAILED)
def verification_passes(c: Ctx) -> Verdict | None:
    v = c.facts.verification
    if v is None or not v.runs or any(r.status is not RunStatus.PASS for r in v.runs):
        return _run_problem(c)
    return None


@guard(RefusalCode.EVIDENCE_MISSING)
def evidence_kinds_present(c: Ctx) -> Verdict | None:
    problem = _run_problem(c)
    return problem if problem and problem.code is RefusalCode.EVIDENCE_MISSING else None


@guard(RefusalCode.INPUTS_CHANGED)
def inputs_unchanged(c: Ctx) -> Verdict | None:
    problem = _run_problem(c)
    return problem if problem and problem.code is RefusalCode.INPUTS_CHANGED else None


@guard(RefusalCode.FAILING_BEFORE_MISMATCH)
def failing_before_matches(c: Ctx) -> Verdict | None:
    """Bugfix: the failing-before record must belong to the current contract version and repro (D46, D55, D56)."""
    k = c.item.contract
    if k.type != BUGFIX:
        return None
    fb = c.item.failing_before
    if fb is None or fb.contract_version != k.version or fb.repro_hash != repro_hash(k.repro or ()):
        return _verdict(
            RefusalCode.FAILING_BEFORE_MISMATCH, "failing_before does not match the current contract version and repro"
        )
    return None


# ---------------------------------------------------------------- effects: dispatch / start / submit / verify
def _with_attempt(item: Item, attempt: Attempt, **changes) -> Item:
    return replace(item, attempts=item.attempts[:-1] + (attempt,), **changes)


@effect
def dispatch(c: Ctx) -> Doc:
    action_id = _next_action_id(c.item, "d")
    payload = {
        "role": c.item.contract.role,
        "target_uid": c.facts.lead_agent.uid,
        "source_uid": c.req.caller.agent_uid or "",
        "text_sha256": c.req.args.text_sha256,
        "message_ref": f"mesh-{action_id}",  # stable: a replay sends the same ref, projmux deduplicates (O6)
    }
    action = PendingAction(action_id, ActionKind.DISPATCH_SEND, payload)
    return _put(c.doc, replace(c.item, pending_actions=c.item.pending_actions + (action,)))


@effect
def start(c: Ctx) -> Doc:
    item = c.item
    attempt = Attempt(
        item.attempts[-1].id + 1 if item.attempts else 1,
        c.req.caller.agent_uid,
        c.facts.head,
        None,
        item.contract.version,
        c.cfg.verify_hash(),
    )
    return _put(
        c.doc,
        replace(
            item,
            phase=Phase.RUNNING,
            attempts=item.attempts + (attempt,),
            counters=replace(item.counters, next_start_free=False),
        ),
    )


@effect
def submit(c: Ctx) -> Doc:
    return _put(
        c.doc, _with_attempt(c.item, replace(c.item.current_attempt, commit=c.req.args.commit), phase=Phase.VERIFYING)
    )


@effect
def verify(c: Ctx) -> Doc:
    a = c.item.current_attempt
    ev = Evidence(
        EvidenceKind.VERIFICATION,
        a.commit,
        c.item.contract.version,
        a.config_hash,
        c.facts.verification.runs,
        c.doc.seq,
    )
    return _put(c.doc, _with_attempt(c.item, replace(a, evidence=ev), phase=Phase.VERIFIED, revalidate=False))


# ---------------------------------------------------------------- guards and effects: fail / reassign / unblock
LEAD_CLASSES = frozenset({"defect", "conflict", "ambiguity", "environment"})  # `limit` is not reportable (invariant 4)
PM_CLASSES = frozenset({"environment"})


@guard(RefusalCode.CLASS_NOT_ALLOWED)
def class_allowed_for_caller(c: Ctx) -> Verdict | None:
    """The core does not assume a boundary parse ran: `limit`, or any unknown class, is refused here too."""
    allowed = PM_CLASSES if c.row.actor is Actor.PM else LEAD_CLASSES
    if c.req.args.cls not in allowed:
        return _verdict(
            RefusalCode.CLASS_NOT_ALLOWED, f"{c.row.actor.value} may report {sorted(allowed)}, not {c.req.args.cls!r}"
        )
    return None


@guard(RefusalCode.CLASS_NOT_ALLOWED)
def too_large_only_with_conflict(c: Ctx) -> Verdict | None:
    a = c.req.args
    if a.reason is not None and (a.reason != "too_large" or a.cls != "conflict"):
        return _verdict(RefusalCode.CLASS_NOT_ALLOWED, "reason too_large goes with class conflict only")
    return None


@guard(RefusalCode.FACT_UNAVAILABLE, RefusalCode.LEAD_STILL_LIVE)
def lead_absent_or_dead(c: Ctx) -> Verdict | None:
    """D45: the PM reports an environment failure only while the assigned lead's agent is not live."""
    if c.facts.lead_live is None:
        return _verdict(RefusalCode.FACT_UNAVAILABLE, "fail by the PM needs to know whether the lead is live")
    if c.facts.lead_live:
        return _verdict(RefusalCode.LEAD_STILL_LIVE, "the lead is live: it reports its own failure")
    return None


@guard(RefusalCode.FACT_UNAVAILABLE, RefusalCode.PRECHECK_REFUSED)
def precheck_passes(c: Ctx) -> Verdict | None:
    """A refused precheck is a guard refusal: it writes nothing to the item (D68)."""
    if c.facts.precheck is None:
        return _verdict(RefusalCode.FACT_UNAVAILABLE, "unblock needs the environment precheck result")
    if not c.facts.precheck.passes:
        return _verdict(RefusalCode.PRECHECK_REFUSED, c.facts.precheck.detail or "the environment precheck failed")
    return None


@effect
def fail(c: Ctx) -> Doc:
    a, item = c.req.args, c.item
    attempt_id = a.attempt_id if a.attempt_id is not None else (item.current_attempt.id if item.attempts else None)
    actor = "pm" if c.row.actor is Actor.PM else "lead"
    cls = FailureClass(a.cls)
    changes: dict = {
        "phase": ROUTE_OF[cls],
        "failures": item.failures + (Failure(cls, a.reason, a.evidence, attempt_id, actor, c.doc.seq),),
    }
    if cls is FailureClass.ENVIRONMENT:
        changes["pending_actions"] = item.pending_actions + (
            PendingAction(_next_action_id(item, "a"), ActionKind.PM_ATTENTION, {"reason": "environment failure"}),
        )
    return _put(c.doc, replace(item, **changes))


@effect
def reassign(c: Ctx) -> Doc:
    return _put(c.doc, replace(_pin(c.item, c.cfg), phase=Phase.READY))


@effect
def unblock(c: Ctx) -> Doc:
    """Only a PASSING precheck reaches here; the counter (+1, or the over-limit route) is the row's Count rule."""
    item = _pin(c.item, c.cfg)
    return _put(c.doc, replace(item, phase=Phase.READY, counters=replace(item.counters, next_start_free=True)))


# ------------------------------------------------------- guards and effects: reply / ack / close / reject / revalidate
@guard(RefusalCode.FACT_UNAVAILABLE, RefusalCode.EVENT_UNKNOWN, RefusalCode.EVENT_REUSED)
def event_unconsumed(c: Ctx) -> Verdict | None:
    """The cited operator event exists (recorded by the PM-pane hook, so the PM cannot fabricate one) and no earlier
    reply or override used it (D63: single use)."""
    fact = c.facts.operator_event
    if fact is None:
        return _verdict(RefusalCode.FACT_UNAVAILABLE, "the cited operator event was not looked up")
    return _unbound(layers.bind_event(c.doc, fact.event, fresh=False))


def _unbound(bound) -> Verdict | None:
    return _verdict(bound.code, bound.detail) if isinstance(bound, layers.Unbound) else None


@guard(RefusalCode.NOTHING_TO_ACK)
def has_unacked_replies(c: Ctx) -> Verdict | None:
    if all(r.acknowledged for r in c.item.replies):
        return _verdict(RefusalCode.NOTHING_TO_ACK, "no unacknowledged replies")
    return None


@guard(RefusalCode.UNACKED_REPLIES, RefusalCode.REVALIDATE_FLAG)
def close_gates(c: Ctx) -> Verdict | None:
    """Invariant 7."""
    if any(not r.acknowledged for r in c.item.replies):
        return _verdict(RefusalCode.UNACKED_REPLIES, "unacknowledged replies: ack them first")
    if c.item.revalidate:
        return _verdict(RefusalCode.REVALIDATE_FLAG, "the item carries a revalidate flag: re-verify or reject it")
    return None


@guard(RefusalCode.CLASS_NOT_ALLOWED, RefusalCode.REJECT_NO_TRIGGER)
def reject_has_trigger(c: Ctx) -> Verdict | None:
    """A reject answers an attached reply (any class), a revalidate flag (class defect only), or the PM's own evidence
    review, which needs a written reason (D101; any of the three classes)."""
    cls = c.req.args.cls
    if cls not in ("defect", "conflict", "ambiguity"):
        return _verdict(RefusalCode.CLASS_NOT_ALLOWED, f"reject takes defect, conflict or ambiguity, not {cls!r}")
    own_review = bool(c.req.args.reason.strip())
    if not (c.item.replies or (c.item.revalidate and cls == "defect") or own_review):
        return _verdict(
            RefusalCode.REJECT_NO_TRIGGER,
            "nothing to reject on: no attached reply, no revalidate flag for a defect, and no written reason for a"
            " reject on the PM's own evidence review (D101)",
        )
    return None


@effect
def attach_terminal_reply(c: Ctx) -> Doc:
    event = c.facts.operator_event.event
    item = replace(c.item, replies=c.item.replies + (Reply(event.seq, "terminal", event.id),))
    return layers.consume(_put(c.doc, item), event, "reply", item.id)


@effect
def ack(c: Ctx) -> Doc:
    return _put(c.doc, replace(c.item, replies=tuple(replace(r, acknowledged=True) for r in c.item.replies)))


@effect
def close(c: Ctx) -> Doc:
    return _put(c.doc, replace(c.item, phase=Phase.CLOSED))


@effect
def reject(c: Ctx) -> Doc:
    a, item = c.req.args, c.item
    cls = FailureClass(a.cls)
    attempt = item.current_attempt
    failure = Failure(cls, None, (a.reason,) if a.reason else (), attempt.id if attempt else None, "pm", c.doc.seq)
    return _put(c.doc, replace(item, phase=ROUTE_OF[cls], failures=item.failures + (failure,)))


@effect
def revalidate_flag(c: Ctx) -> Doc:
    """Data only in the MVP (no tool exposes it): sets the flag the close gate and the follow-up row read."""
    return c.doc if c.item.revalidate else _put(c.doc, replace(c.item, revalidate=True))


# ---------------------------------------------------------------- (mesh) rows that need no facts: triggers and effects
def _has_follow_up(doc: Doc, item_id: str) -> bool:
    return any(i.links.follows == item_id for i in doc.items.values())


@trigger
def closed_with_flag(doc: Doc) -> tuple[str, ...]:
    """D70: a revalidate flag on a closed item creates ONE follow-up draft (a burst yields a single item)."""
    return tuple(
        sorted(
            i.id
            for i in doc.items.values()
            if i.phase is Phase.CLOSED and i.revalidate and not _has_follow_up(doc, i.id)
        )
    )


@trigger
def has_cancelled_child(doc: Doc) -> tuple[str, ...]:
    return tuple(
        sorted(
            i.id
            for i in doc.items.values()
            if i.phase is Phase.BLOCKED_CHILDREN
            and any(doc.items[ch].phase is Phase.CANCELLED for ch in i.links.children if ch in doc.items)
        )
    )


@trigger
def ready_dependent_of_cancelled(doc: Doc) -> tuple[str, ...]:
    return tuple(
        sorted(
            i.id
            for i in doc.items.values()
            if i.phase is Phase.READY and any(doc.dep_status(d) is DepStatus.CANCELLED for d in i.contract.depends)
        )
    )


def _new_id(doc: Doc) -> tuple[str, int]:
    n = doc.next_id
    while f"WO-{n:04d}" in doc.items or f"WO-{n:04d}" in doc.legacy:
        n += 1
    return f"WO-{n:04d}", n + 1


@effect
def mesh_follow_up(c: Ctx) -> Doc:
    k = c.item.contract
    item_id, next_id = _new_id(c.doc)
    follow = Item(
        item_id,
        Phase.DRAFT,
        0,
        Contract(0, k.goal, k.done_criteria, k.role, k.type, dict(k.type_inputs), k.scope, (), k.repro),
        links=Links(follows=c.item.id),
    )
    return replace(_put(c.doc, follow), next_id=next_id)


# ---------------------------------------------------- split / cancel cascades / the (mesh) rows that need facts
@guard(RefusalCode.SPLIT_TOO_FEW_CHILDREN, RefusalCode.CLASS_ROUTE)
def split_allowed(c: Ctx) -> Verdict | None:
    failure = c.item.latest_failure
    if failure is None or (failure.cls, failure.reason) != (FailureClass.CONFLICT, "too_large"):
        return _verdict(RefusalCode.CLASS_ROUTE, "a split needs a conflict failure with reason too_large")
    if len(c.req.args.children) < 2:
        return _verdict(RefusalCode.SPLIT_TOO_FEW_CHILDREN, "a split needs at least two child drafts")
    return None


@guard(RefusalCode.FACT_UNAVAILABLE)
def merged_commit_known(c: Ctx) -> Verdict | None:
    if not c.facts.merged_commit:
        return _verdict(RefusalCode.FACT_UNAVAILABLE, "the merged commit of the children is not known")
    return None


@guard(RefusalCode.FACT_UNAVAILABLE, RefusalCode.VERIFICATION_PASSED)
def parent_run_failed(c: Ctx) -> Verdict | None:
    """Selection guard of the (mesh) parent-verify-fail row: it applies exactly when the pass row would not."""
    if c.facts.verification is None:
        return _verdict(RefusalCode.FACT_UNAVAILABLE, "the parent's own verification has not run")
    problem = _run_problem(c)
    if problem is None:
        return _verdict(RefusalCode.VERIFICATION_PASSED, "the parent's own verification passed")
    if problem.code is RefusalCode.PREPARE_FAILED:  # D91: an environment problem is never recorded as a defect
        return _verdict(RefusalCode.FACT_UNAVAILABLE, "the parent's verify.prepare failed: environment, not a defect")
    return None


@trigger
def children_all_closed(doc: Doc) -> tuple[str, ...]:
    return tuple(
        sorted(
            i.id
            for i in doc.items.values()
            if i.phase is Phase.BLOCKED_CHILDREN
            and i.links.children
            and all(ch in doc.items and doc.items[ch].phase is Phase.CLOSED for ch in i.links.children)
        )
    )


@trigger
def parent_run_due(doc: Doc) -> tuple[str, ...]:
    """A split parent in `verifying` whose current attempt is the mesh's own (no lead) and has no evidence yet."""
    return tuple(
        sorted(
            i.id
            for i in doc.items.values()
            if i.phase is Phase.VERIFYING
            and i.links.children
            and i.current_attempt is not None
            and i.current_attempt.lead_uid is None
            and i.current_attempt.evidence is None
        )
    )


def _cancel_tree(doc: Doc, item_id: str) -> Doc:
    """Cancels an item and, recursively, its active children (D66). Finished items are left alone."""
    item = doc.items[item_id]
    if item.phase.terminal:
        return doc
    doc = _put(doc, replace(item, phase=Phase.CANCELLED))
    for child in item.links.children:
        if child in doc.items:
            doc = _cancel_tree(doc, child)
    return doc


@effect
def cancel(c: Ctx) -> Doc:
    """The item and its active children; ready dependents are moved by the (mesh) dependency row in the same write."""
    return c.doc if c.item.phase is Phase.CANCELLED else _cancel_tree(c.doc, c.item.id)


@effect
def split(c: Ctx) -> Doc:
    doc, parent = c.doc, c.item
    new_ids = []
    for draft in c.req.args.children:
        child_id, next_id = _new_id(doc)
        child = Item(
            child_id,
            Phase.DRAFT,
            0,
            Contract(0, draft.goal, draft.done_criteria, draft.role, draft.type, {}, Scope(), (), None),
            links=Links(parent=parent.id),
        )
        doc = replace(_put(doc, child), next_id=next_id)
        new_ids.append(child_id)
    gone = tuple(ch for ch in parent.links.children if doc.items[ch].phase is Phase.CANCELLED)
    links = replace(
        parent.links,
        children=tuple(ch for ch in parent.links.children if ch not in gone) + tuple(new_ids),
        former_children=parent.links.former_children + gone,
    )
    return _put(doc, replace(parent, phase=Phase.BLOCKED_CHILDREN, links=links))


@effect
def mesh_children_done(c: Ctx) -> Doc:
    item = c.item
    attempt = Attempt(
        item.attempts[-1].id + 1 if item.attempts else 1,
        None,
        None,
        c.facts.merged_commit,
        item.contract.version,
        c.cfg.verify_hash(),
    )
    return _put(c.doc, replace(item, phase=Phase.VERIFYING, attempts=item.attempts + (attempt,)))


@effect
def mesh_child_cancelled(c: Ctx) -> Doc:
    doc = _put(c.doc, replace(c.item, phase=Phase.REWORK))
    for child in c.item.links.children:
        doc = _cancel_tree(doc, child)
    return doc


@effect
def mesh_dependency_cancelled(c: Ctx) -> Doc:
    return _put(c.doc, replace(c.item, phase=Phase.BLOCKED_AWAITING_USER))


@effect
def mesh_parent_verify_pass(c: Ctx) -> Doc:
    a = c.item.current_attempt
    ev = Evidence(
        EvidenceKind.MERGED, a.commit, c.item.contract.version, a.config_hash, c.facts.verification.runs, c.doc.seq
    )
    return _put(c.doc, _with_attempt(c.item, replace(a, evidence=ev), phase=Phase.VERIFIED, revalidate=False))


@effect
def mesh_parent_verify_fail(c: Ctx) -> Doc:
    item = c.item
    failure = Failure(FailureClass.DEFECT, None, (), item.current_attempt.id, "mesh", c.doc.seq)
    return _put(c.doc, replace(item, phase=Phase.REWORK, failures=item.failures + (failure,)))


# ---------------------------------------------------------------- override (operator only, D73)
@guard(
    RefusalCode.OVERRIDE_TARGET, RefusalCode.DEPENDS_INVALID, RefusalCode.UNACKED_REPLIES, RefusalCode.REVALIDATE_FLAG
)
def override_target_allowed(c: Ctx) -> Verdict | None:
    """Any state but blocked(limit) (D73) and not the current one. An override skips the table's guards, not the
    invariants: past draft the depends must be well formed, and closed still needs its close gates."""
    to = c.req.args.to
    if to not in {p.value for p in OVERRIDE_TARGETS} or to == c.item.phase.value:
        return _verdict(
            RefusalCode.OVERRIDE_TARGET, f"cannot override to {to!r} (blocked(limit) is mesh-only; no no-op moves)"
        )
    parent = c.doc.items.get(c.item.links.parent or "")
    if (
        parent is not None
        and parent.phase is not Phase.BLOCKED_CHILDREN
        and to not in (Phase.CLOSED.value, Phase.CANCELLED.value)
    ):
        return _verdict(
            RefusalCode.OVERRIDE_TARGET,
            f"parent {parent.id} is in {parent.phase.value}: it is not waiting for its children",
        )
    if to not in (Phase.DRAFT.value, Phase.CANCELLED.value):
        problem = _depends_problem(c.doc, c.item.id, c.item.contract.depends)
        if problem:
            return _verdict(RefusalCode.DEPENDS_INVALID, problem)
    if to == Phase.CLOSED.value:
        return close_gates(c)
    return None


@guard(RefusalCode.EVENT_STALE)
def event_fresh(c: Ctx) -> Verdict | None:
    """Freshness is by seq only (D74: wall-clock is never compared): the cited event must be the newest operator
    event, so an older prompt cannot be replayed to authorise a later override."""
    return _unbound(layers.bind_event(c.doc, c.facts.operator_event.event, fresh=True))


@guard(RefusalCode.EVENT_NOT_BOUND)
def override_names_item(c: Ctx) -> Verdict | None:
    """D98: the operator event names the item id as a whole token (the D85 name path), so a fresh prompt about
    something else cannot authorise an override of this item."""
    return _unbound(
        layers.bind_event(
            c.doc,
            c.facts.operator_event.event,
            fresh=True,
            tokens=(c.item.id,),
            unbound=f"the operator event must name the item {c.item.id} (D98)",
        )
    )


@effect
def override(c: Ctx) -> Doc:
    to, doc, item = Phase(c.req.args.to), c.doc, c.item
    event = c.facts.operator_event.event
    if to is Phase.CANCELLED:
        doc = _cancel_tree(doc, item.id)
    else:
        changes: dict = {"phase": to}
        if to is Phase.VERIFIED:  # D69: a flag never predates the evidence it sits on
            changes["revalidate"] = False
        attempt = item.current_attempt
        if to in (Phase.VERIFIED, Phase.CLOSED) and item.binding_error(Phase.VERIFIED) is not None:
            ev = Evidence(EvidenceKind.OVERRIDE, None, item.contract.version, c.cfg.verify_hash(), (), doc.seq)
            changes["attempts"] = item.attempts + (
                Attempt(
                    attempt.id + 1 if attempt else 1, None, None, None, item.contract.version, c.cfg.verify_hash(), ev
                ),
            )
        if to is Phase.REWORK:  # the operator's rework still needs a way back (inv 2)
            changes["failures"] = item.failures + (
                Failure(
                    FailureClass.DEFECT,
                    None,
                    ("operator override",),
                    attempt.id if attempt else None,
                    "operator",
                    doc.seq,
                ),
            )
        if to is not Phase.BLOCKED_CHILDREN:  # an active child is only legitimate under a parent that waits for it
            for child in item.links.children:
                doc = _cancel_tree(doc, child)
        doc = _put(doc, replace(item, **changes))
    return layers.consume(doc, event, "override", item.id)
