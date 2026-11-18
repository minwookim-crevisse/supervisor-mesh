"""The eleven MVP tools and the one registry both front doors read. `REGISTRY` is the single list: `tools/list` and
`call` are generated from it, and every input schema is derived from the args dataclasses (`model.json_schema`) and,
for `work_transition`, from the transition table. No JSON-RPC here and no wire types: a tool takes PARSED args and
returns a plain dict; `call` turns refusals and errors into one error shape.

All eleven are real: `mesh_status`, `mesh_metrics` (the D90 counters, `mesh/metrics.py`), `work_list`, `work_show`,
`work_new`, `work_dispatch`, `work_transition`, `pane_command` (projmux / tmux adapters), `mesh_init` (scaffold +
.mesh/ bootstrap + legacy import, `mesh/init.py`), `tracker_render` (`mesh/tracker.py`) and `companions_install`
(D88, `mesh/companions.py`).

Access ('any', 'pm', 'per table') is documentation. ENFORCEMENT is the table for every verb (who may do what from which
phase: one source) and `read_only` for the tools that never write. An UNKNOWN caller reads; every verb is refused and
recorded (D82). Identity comes from the calling projmux pane (`identity`) only.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from . import (
    actions,
    companions,
    dispatch,
    facts,
    git,
    identity,
    init,
    layers,
    metrics,
    pane,
    projmux,
    state,
    status,
    tracker,
    verify,
)
from .engine import Engine
from .model import (
    ITEM_ID_PATTERN,
    ROLE_PATTERN,
    Caller,
    CallerKind,
    Config,
    DispatchSend,
    InvalidArgs,
    Item,
    MeshError,
    NewArgs,
    NotInitialized,
    Phase,
    RefusalCode,
    Refused,
    Request,
    UnknownItem,
    Verb,
    from_json,
    item_json,
    json_schema,
)
from .projmux import Projmux, ProjmuxUnavailable
from .store import Store
from .tmux import Tmux, TmuxFailed, valid_pane_id

ToolRefused = layers.RefusedError  # one refusal type for every tool (D95)
ToolResult = layers.ToolResult
once_per_request = layers.once_per_request


class UnknownTool(KeyError):  # noqa: N818
    pass


# tool-level refusal codes outside the transition table (pane_command's are `pane.CODES`)
TOOL_CODES = frozenset({RefusalCode.CONFIRM_FAILED, RefusalCode.RE_PIN_REFUSED, RefusalCode.OUTSIDE_ROOT})


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- the caller (D82)
def caller_from_env(
    env: Mapping[str, str], root: str | Path | None = None, pmx: Projmux | None = None
) -> Callable[[], Caller]:
    """Who is calling, resolved by the returned callable from the calling projmux pane (`identity`, D82). There is NO
    environment override: no variable makes a caller PM or a lead. Without a project root every caller is UNKNOWN.
    Tests inject `Context.caller` in process, or put a fake projmux on PATH for a child process."""
    if root is None:
        return Caller.unknown
    resolver = pmx or Projmux(env)
    return identity.PaneCaller(env, root, resolver)


@dataclass
class Context:
    """What a tool may touch: the store, the engine, the caller (the calling pane, or injected in process), the clock
    and the adapters. Built once per server process. `root` is the project root; `env` is the server's environment
    (handed to projmux / tmux only, never echoed)."""

    store: Store
    engine: Engine
    caller: Callable[[], Caller]
    clock: Callable[[], str] = utc_now
    root: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    pmx: Projmux | None = None
    tmux: Tmux | None = None

    @classmethod
    def for_project(
        cls,
        root: Path,
        env: Mapping[str, str],
        clock: Callable[[], str] = utc_now,
        caller: Callable[[], Caller] | None = None,
    ) -> "Context":
        """`caller` is the in-process injection point (tests); a front door leaves it None: the calling pane."""
        root = Path(root)
        store = Store(root / ".mesh")
        pmx = Projmux(env)
        provider = facts.chain(facts.projmux_facts(pmx, root), facts.git_facts(root, env, store.read_config))
        engine = Engine(store, facts=provider)
        who = caller or caller_from_env(env, root, pmx)
        return cls(store, engine, who, clock, root, dict(env), pmx, Tmux(env))


# ---------------------------------------------------------------- args of the tools that are not verbs
_ID = {"pattern": ITEM_ID_PATTERN}


@dataclass(frozen=True, slots=True)
class NoToolArgs:
    pass


@dataclass(frozen=True, slots=True)
class StatusArgs:
    metrics: bool = field(default=False, metadata={"doc": "also return the D90 counters (as mesh_metrics)"})


@dataclass(frozen=True, slots=True)
class ListArgs:
    phase: Phase | None = None
    role: str | None = field(default=None, metadata={"pattern": ROLE_PATTERN})


@dataclass(frozen=True, slots=True)
class ShowArgs:
    item_id: str = field(metadata=_ID)


@dataclass(frozen=True, slots=True)
class TransitionArgs:
    item_id: str = field(metadata=_ID)
    verb: str = field(metadata={"doc": "a section-4 verb; the enum is derived from the transition table"})
    args: Mapping[str, Any] = field(default_factory=dict, metadata={"doc": "per-verb arguments"})


@dataclass(frozen=True, slots=True)
class DispatchArgs:
    item_id: str = field(metadata=_ID)


@dataclass(frozen=True, slots=True)
class InitArgs:
    stack: Literal["auto", "ts", "python", "none"] = "auto"
    apply: bool = False
    plan_id: str | None = None


@dataclass(frozen=True, slots=True)
class RenderArgs:
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class PaneCommandArgs:
    role: str = field(metadata={"pattern": ROLE_PATTERN, "doc": "the target lead's role label"})
    command: str = field(metadata={"doc": "one line starting with '/', typed literally"})
    event_id: str | None = field(
        default=None, metadata={"doc": "the hook-recorded operator event that names or approves the command"}
    )
    propose: bool = field(default=False, metadata={"doc": "store the exact text as a proposal; types nothing"})
    item_id: str | None = field(default=None, metadata={**_ID, "doc": "the receipt also joins this item's history"})


@dataclass(frozen=True, slots=True)
class InstallArgs:
    event_id: str
    only: Literal["pstack", "mattpocock-skills"] | None = None


# ---------------------------------------------------------------- the registry
@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    args: type
    fn: Callable[[Context, Any], dict]
    access: Literal["any", "pm", "per table"]
    read_only: bool = False
    schema_hook: Callable[[dict], dict] | None = None
    run: Callable[[Context, Mapping[str, Any] | None], layers.ToolResult] | None = None  # fn wrapped by the layers

    def input_schema(self) -> dict:
        schema = json_schema(self.args)
        return self.schema_hook(schema) if self.schema_hook else schema


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, args: type, access: str, *, read_only: bool = False, schema_hook=None):
    def register(fn):
        REGISTRY[name] = Tool(
            name, description, args, fn, access, read_only, schema_hook, layers.tool_layer(name, args, fn, read_only)
        )
        return fn

    return register


def _transition_schema(schema: dict) -> dict:
    """The verb enum and the per-verb args text come from the table: no second list to keep in step."""
    verbs = list(state.surface_verbs(state.WORK_TRANSITION))
    schema["properties"]["verb"]["enum"] = verbs
    parts = []
    for verb in verbs:
        actors = sorted({r.actor.value for r in state.TABLE if r.verb.value == verb and r.via == state.WORK_TRANSITION})
        shown = ", ".join(
            f.name + ("" if f.default is MISSING and f.default_factory is MISSING else "?")
            for f in fields(state.args_type(Verb(verb)))
        )
        parts.append(f"{verb} [{' | '.join(actors)}] {{{shown}}}")
    schema["properties"]["args"]["description"] = "per verb: " + "; ".join(parts)
    return schema


# ---------------------------------------------------------------- results
def _summary(item: Item) -> dict:
    attempt = item.current_attempt
    return {
        "id": item.id,
        "phase": item.phase.value,
        "role": item.contract.role,
        "type": item.contract.type,
        "version": item.version,
        "goal": item.contract.goal,
        "attempt": attempt.id if attempt else None,
    }


def _phase_name(phase: Phase | None) -> str | None:
    return phase.value if phase else None


def _apply(ctx: Context, verb: Verb, args: Any, item_id: str | None) -> dict:
    """One verb through the engine's one path. A refusal is raised as ToolRefused (already recorded). An operator
    override needs the D113 code first."""
    if verb is Verb.OVERRIDE and ctx.caller().kind is CallerKind.PM:
        _confirm(ctx, ctx.caller(), verb.value, item_id)
    out = ctx.engine.execute(Request(verb, ctx.caller(), args, ctx.clock(), item_id))
    if isinstance(out, Refused):
        raise ToolRefused(out.refusal)
    item = out.doc.items[out.item_id]
    first = out.changes[0] if out.changes else None
    return {
        "row": out.row_id,
        "item_id": out.item_id,
        "from": _phase_name(first.from_phase) if first else item.phase.value,
        "to": item.phase.value,
        "noop": out.noop,
        "changes": [
            {"item_id": c.item_id, "from": _phase_name(c.from_phase), "to": c.to_phase.value} for c in out.changes
        ],
        "item": item_json(item),
    }


# ---------------------------------------------------------------- the tools
class PlanChanged(MeshError):  # noqa: N818
    code = "plan_changed"


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _pm_only(ctx: Context, subject: str, caller: Caller) -> None:
    """The pm tools outside the transition table. A refusal is recorded when .mesh/ exists (before mesh_init there
    is nowhere to record it); an UNKNOWN caller is told how to become the PM (D82)."""
    if caller.kind is CallerKind.PM:
        return
    hint = caller.why or init.PM_LABEL_HINT
    detail = f"{subject} is a PM tool" + (f"; {hint}" if caller.kind is CallerKind.UNKNOWN else "")
    _refuse(ctx, subject, RefusalCode.CALLER_NOT_PM, detail, caller)


def _refuse(ctx: Context, subject: str, code: RefusalCode, detail: str, caller: Caller):
    layers.refuse(ctx.store, layers.tool_refusal(subject, code, detail, ctx.clock(), caller))


def _rows(rows) -> list[dict]:
    return [{"action": r.action, "path": r.path, **({"note": r.note} if r.note else {})} for r in rows]


@tool(
    "mesh_init",
    "Scaffold the 4-pillar harness and bootstrap .mesh/ (state, config, launcher, the PM-pane operator-event hook in "
    ".claude/settings.json) and import an existing docs/tracker.md once as legacy rows. Two separate calls: without "
    "apply it writes nothing and returns the plan and its plan_id (any caller); show it to the operator, then call "
    "again with apply=true and that plan_id (PM only). Never overwrites a file.",
    InitArgs,
    "pm",
)
def mesh_init(ctx: Context, args: InitArgs) -> dict:
    root = _root(ctx)
    caller = ctx.caller()
    repin = None
    if args.apply:
        _pm_only(ctx, "mesh_init", caller)
        if not args.plan_id:
            raise InvalidArgs("apply needs the plan_id of the dry run the operator saw")
        repin = _pin_check(ctx, caller)
    try:
        result = init.mesh_init(root, PLUGIN_ROOT, args.stack, args.apply, args.plan_id)
    except init.PlanChanged as exc:
        raise PlanChanged(str(exc), hint="run mesh_init without apply and show the new plan") from None
    except init.OutsideRoot as exc:
        raise OutsideRoot(str(exc), hint="replace the symlink with a real file or directory in the project") from None
    if result.applied:
        _pin(ctx, caller, repin)
    have = companions.present(_home(ctx.env), root)
    install = companions.plan_for(have)
    nxt = []
    if caller.kind is not CallerKind.PM:
        nxt.append(init.PM_LABEL_HINT)
    if result.exit_code == 3:
        nxt.append(
            "the stack is ambiguous (no single package.json / pyproject.toml): ask the operator ts | python | none"
        )
    elif not result.applied:
        nxt.append(f"show these rows to the operator, then call mesh_init with apply=true and plan_id={result.plan_id}")
    else:
        nxt.append("fill docs/harness/part-b-checklist.md (Part B: ask the operator, never invent values)")
    if install["steps"]:
        nxt.append(
            "missing companions: the operator names `companions_install` (the whole plan) or one plugin name in a "
            "prompt in the PM pane; then call companions_install with that prompt's event id"
        )
    return {
        "applied": result.applied,
        "stack": result.stack,
        "exit_code": result.exit_code,
        "plan_id": result.plan_id,
        "rows": _rows(result.rows),
        "summary": result.summary(),
        "legacy_import": result.legacy,
        "companions": have,
        "install_plan": install,
        "next": nxt,
    }


class OutsideRoot(MeshError):  # noqa: N818
    code = RefusalCode.OUTSIDE_ROOT.value


def _pin_check(ctx: Context, caller: Caller):
    """D109 / D114 at mesh_init apply: None (no pin yet, or the caller is the pinned PM with no re-pin asked), or the
    pending re-pin to apply. A re-pin needs the operator's hook-recorded `re-pin <this uid>` from this pane (unused),
    the pinned PM agent not Running (when it is another agent) and the D113 code; anything else is refused and the
    pin is kept."""
    try:
        doc = ctx.store.read().doc
    except NotInitialized:
        return None
    rp = doc.pm_repin if doc.pm_repin is not None and doc.pm_repin.agent_uid == caller.agent_uid else None
    if doc.pm_uid is None or (doc.pm_uid == caller.agent_uid and rp is None):
        return None
    if rp is None:
        _refuse(ctx, "mesh_init", RefusalCode.CALLER_NOT_PM, f"mesh_init apply: {layers.OTHER_PM_WHY}", caller)
    bound = layers.bind_event(
        doc, layers.resolve_event(ctx.store, rp.event_id), fresh=False, tokens=("re-pin",),
        unbound="the re-pin event must name `re-pin` (D109)",
    )  # fmt: skip
    if isinstance(bound, layers.Unbound):
        _refuse(ctx, "mesh_init", bound.code, bound.detail, caller)
    if doc.pm_uid != caller.agent_uid:
        root, pmx = _adapters(ctx)
        try:
            running = any(a.uid == doc.pm_uid and a.phase == "Running" for a in pmx.agents())
        except ProjmuxUnavailable:
            running = True  # fail closed
        if running:
            _refuse(
                ctx, "mesh_init", RefusalCode.RE_PIN_REFUSED,
                "the pinned PM agent is still Running (D114): stop it first, then re-run mesh_init apply", caller,
            )  # fmt: skip
    _confirm(ctx, caller, "mesh_init")
    return rp


def _pin(ctx: Context, caller: Caller, repin) -> None:
    """mesh_init apply pins the PM (D109): the first apply, a pre-D109 state.json, or a checked re-pin."""
    if caller.kind is not CallerKind.PM or not caller.agent_uid or not ctx.store.initialized():
        return
    with ctx.store.locked() as txn:
        doc = txn.doc
        if repin is not None:
            event = layers.resolve_event(ctx.store, repin.event_id)
            doc = layers.consume(doc, event, "re_pin", repin.agent_uid, "done")
            doc = replace(doc, pm_uid=repin.agent_uid, pm_session=repin.session, pm_repin=None)
        elif doc.pm_uid is None:
            doc = replace(doc, pm_uid=caller.agent_uid)
        else:
            return
        txn.commit(doc)


def _pm_popup(ctx: Context) -> Callable[[list[str], float], bool]:
    """The D113 popup on the PM pane's tmux client (the caller's own pane, as projmux reports it)."""

    def popup(argv: list[str], timeout_s: float) -> bool:
        pane_uid = ctx.env.get(identity.PANE_VAR)
        if not pane_uid or ctx.pmx is None:
            return False
        try:
            tmux_id = next((p.tmux_id for p in ctx.pmx.panes() if p.uid == pane_uid), None)
            if not valid_pane_id(tmux_id):  # D116
                return False
            return (ctx.tmux or Tmux(ctx.env)).popup(tmux_id, argv, timeout_s)
        except (ProjmuxUnavailable, TmuxFailed):
            return False

    return popup


def _confirm(ctx: Context, caller: Caller, subject: str, item_id: str | None = None) -> None:
    """D113: refused unless the operator types the one-time popup code."""
    why = layers.confirm(_pm_popup(ctx), _config(ctx).confirm_timeout_s)
    if why is not None:
        layers.refuse(
            ctx.store,
            layers.tool_refusal(
                subject,
                RefusalCode.CONFIRM_FAILED,
                f"the operator did not confirm the one-time code in the PM pane's popup ({why}); "
                "ask them to retry (D113)",
                ctx.clock(),
                caller,
                item_id,
            ),
        )


def _root(ctx: Context) -> Path:
    if ctx.root is None:
        raise MeshError("this context has no project root")
    return ctx.root


@tool(
    "mesh_status",
    "Items by state, live leads (role and presence), pending actions, your identity, which D87 companion plugins "
    "are installed, the hook launcher's health (ok | missing | stale | not_executable) and the last recorded "
    "operator event (id, seq), scope overlaps between active items (D94) and stalls with who to nudge (D102); with "
    "metrics=true also the D90 counters. Read-only; works for any caller.",
    StatusArgs,
    "any",
    read_only=True,
)
def mesh_status(ctx: Context, args: StatusArgs) -> dict:
    caller = ctx.caller()
    try:
        doc = ctx.store.read().doc
        initialized = True
    except NotInitialized:
        doc, initialized = None, False
    items = sorted(doc.items.values(), key=lambda i: i.id) if doc else []
    by_state: dict[str, list[str]] = {}
    for item in items:
        by_state.setdefault(item.phase.value, []).append(item.id)
    roles = {i.contract.role for i in items if not i.phase.terminal}
    live: set[str] | None = None
    agents: tuple = ()
    if ctx.pmx is not None and ctx.root is not None:
        try:
            agents = ctx.pmx.agents()  # asked once: the leads and the D102 stall layer read the same answer
            live = {a.role for a in ctx.pmx.live_members(ctx.root, agents=agents) if a.role != "pm"}
        except ProjmuxUnavailable:
            live = None
    out = {
        "initialized": initialized,
        "identity": {"kind": caller.kind.value, "role": caller.role},
        "items": by_state,
        "leads": [{"role": r, "present": None if live is None else r in live} for r in sorted(roles | (live or set()))],
        "pending_actions": [
            {"item_id": i.id, "action_id": a.action_id, "kind": a.kind.value, "attempts": attempt_log(a)}
            for i in items
            for a in i.pending_actions
            if not a.done
        ],
        "companions": companions.present(_home(ctx.env), ctx.root) if ctx.root else {},
        "launcher": init.launcher_health(ctx.root, PLUGIN_ROOT) if ctx.root else "missing",
        "last_event": _last_event(ctx) if initialized else None,
        "overlaps": status.overlaps(doc) if doc else [],
        "stalls": status.stalls(doc, agents, ctx.root, ctx.clock(), _config(ctx))
        if doc and ctx.root and live is not None
        else [],
    }
    if live is None:
        out["projmux"] = "unavailable"
    if args.metrics:
        out["metrics"] = metrics.counters(doc, ctx.store.operator_events()) if doc else None
    return out


@tool(
    "mesh_metrics",
    "The D90 counters from recorded history: entries into rework per item, PASS reversals (verified or closed -> "
    "rework) and operator interventions (operator events; receipts never count). Indicative only, never a gate. "
    "Read-only; works for any caller.",
    NoToolArgs,
    "any",
    read_only=True,
)
def mesh_metrics(ctx: Context, args: NoToolArgs) -> dict:
    return metrics.counters(ctx.store.read().doc, ctx.store.operator_events())


def _last_event(ctx: Context) -> dict | None:
    """The newest genuine operator event: id and seq only (never its text): proof the PM-pane hook is recording."""
    events = ctx.store.operator_events()
    return {"id": events[-1].id, "seq": events[-1].seq} if events else None


def _home(env: Mapping[str, str]) -> Path | None:
    return Path(env["HOME"]) if env.get("HOME") else None


@tool(
    "work_list",
    "List work items, optionally by phase or role. Read-only; works for any caller.",
    ListArgs,
    "any",
    read_only=True,
)
def work_list(ctx: Context, args: ListArgs) -> dict:
    items = ctx.store.read().doc.items.values()
    chosen = [
        i
        for i in items
        if (args.phase is None or i.phase == args.phase) and (args.role is None or i.contract.role == args.role)
    ]
    return {"items": [_summary(i) for i in sorted(chosen, key=lambda i: i.id)]}


@tool("work_show", "One full item record. Read-only; works for any caller.", ShowArgs, "any", read_only=True)
def work_show(ctx: Context, args: ShowArgs) -> dict:
    item = ctx.store.read().doc.items.get(args.item_id)
    if item is None:
        raise UnknownItem(f"no item {args.item_id}")
    return {"item": item_json(item)}


@tool("work_new", "Create a draft work item (verb new). PM only.", NewArgs, "pm")
def work_new(ctx: Context, args: NewArgs) -> dict:
    return _apply(ctx, Verb.NEW, args, None)


@tool(
    "work_dispatch",
    "Send the work order (standard header + the item's contract) to the item's role lead over projmux. PM only. "
    "The send is recorded before it runs; a transient projmux refusal is retried (bounded, same message-ref, every "
    "attempt recorded); a failed send stays pending and the next call replays it with the same message-ref. Returns "
    "warnings for running items whose write scope or resources overlap (never a refusal).",
    DispatchArgs,
    "pm",
)
def work_dispatch(ctx: Context, args: DispatchArgs) -> dict:
    root, pmx = _adapters(ctx)
    item = ctx.store.read().doc.items.get(args.item_id)
    if item is not None and ctx.caller().kind is CallerKind.PM:
        for action in actions.open_sends(item):
            replayed = _send_order(ctx, item, action)
            if not replayed.get("superseded"):
                warnings = status.overlaps_with(ctx.store.read().doc, item)
                return {"item_id": item.id, "dispatch": {**replayed, "replayed": True}, "warnings": warnings}
    ref = f"mesh-{state.next_action_id(item, 'd')}" if item is not None else ""
    text = dispatch.compose(item, root, ref) if item is not None else ""
    if len(text.encode("utf-8")) > dispatch.MAX_MESSAGE:
        raise InvalidArgs(
            f"the composed message is larger than {dispatch.MAX_MESSAGE} bytes (the projmux limit); shorten the "
            "work-order or report path"
        )
    sha = dispatch.text_sha256(text) if item is not None else "0" * 64
    out = _apply(ctx, Verb.DISPATCH, DispatchSend(sha), args.item_id)
    doc = ctx.store.read().doc
    item = doc.items[args.item_id]
    out["warnings"] = status.overlaps_with(doc, item)  # D94: a warning, never a refusal
    pending = actions.open_sends(item)
    if not pending:  # another session (a recovering server) sent it between our commit and this read
        return {**out, "dispatch": {"sent": False, "replayed": False, "note": "already sent by another session"}}
    return {**out, "dispatch": {**_send_order(ctx, item, pending[-1]), "replayed": False}}


def replay_dispatches(ctx: Context) -> list[dict]:
    """Section 8 recovery: every recorded, unsent dispatch is sent again with its recorded message-ref (projmux
    deduplicates on it, O6). An order that no longer matches its recorded hash, or an item that left `ready`, is
    marked superseded and not sent."""
    _adapters(ctx)
    return [
        _send_order(ctx, item, action)
        for item in ctx.store.read().doc.items.values()
        for action in actions.open_sends(item)
    ]


def recover(ctx: Context) -> None:
    """Run once per server, after the MCP handshake (section 8). Any server removes the verify worktrees a killed run
    left behind (their owner process is gone). Only the PM's server acts on the PM's actions: a
    keypress or install caught mid-way becomes UNKNOWN (never replayed), every recorded, unsent dispatch is replayed,
    and the due (mesh) rows are drained (a crash between a write and its follow-up left them due). A lead's or an
    unknown caller's server takes none of these actions. A project without .mesh/ has nothing to recover."""
    if not ctx.store.initialized():
        return
    if ctx.root is not None:  # any caller's server: only worktrees whose owner process is gone (F8)
        git.reap_worktrees(ctx.root, verify.child_env(ctx.env))
    if ctx.caller().kind is not CallerKind.PM:
        return
    layers.recover_attempting(ctx.store)
    if ctx.root is not None and ctx.pmx is not None:
        replay_dispatches(ctx)
    ctx.engine.drain(ctx.clock())


def _send_order(ctx: Context, item: Item, action) -> dict:
    root, pmx = _adapters(ctx)
    payload = action.payload
    text = dispatch.compose(item, root, payload["message_ref"])
    why = None
    if item.phase is not Phase.READY:
        why = "item left ready"
    elif dispatch.text_sha256(text) != payload.get("text_sha256"):
        why = "order changed"
    if why:
        actions.mark_done(ctx.store, item.id, action.action_id, {"superseded": why})
        return {"action_id": action.action_id, "sent": False, "superseded": True}
    cfg = _config(ctx)
    attempts = attempt_log(action)

    def attempt(n: int):  # D93: every attempt is recorded on the pending action before the next one
        sent = pmx.send(payload["target_uid"], payload.get("source_uid") or None, text, payload["message_ref"])
        attempts.append("delivered" if sent.delivered else sent.reason or f"exit {sent.code}")
        actions.annotate(ctx.store, item.id, action.action_id, {"attempts": ",".join(attempts)}, done=sent.delivered)
        return sent

    outcomes = layers.retry(
        attempt,
        retries=cfg.dispatch_retry,
        transient=lambda s: not s.delivered and projmux.transient(s.reason),
        backoff_s=cfg.dispatch_backoff_s,
    )
    return {
        "action_id": action.action_id,
        "message_ref": payload["message_ref"],
        "sent": outcomes[-1].delivered,
        "attempts": attempts[-len(outcomes) :],
    }


def attempt_log(action) -> list[str]:
    """The D93 attempts recorded on a dispatch action (comma-joined in its payload: a payload value is a string)."""
    raw = action.payload.get("attempts", "")
    return raw.split(",") if raw else []


def _config(ctx: Context) -> Config:
    try:
        return ctx.store.read_config()
    except MeshError:  # a config that no longer parses: the defaults (a dispatch is no verification)
        return Config()


def _adapters(ctx: Context) -> tuple[Path, Projmux]:
    if ctx.root is None or ctx.pmx is None:
        raise MeshError("this context has no project root or projmux adapter")
    return ctx.root, ctx.pmx


@tool(
    "work_transition",
    "Apply a lifecycle verb to an item. The transition table decides who may do what from which phase "
    "and a refusal is recorded; arguments are validated per verb.",
    TransitionArgs,
    "per table",
    schema_hook=_transition_schema,
)
def work_transition(ctx: Context, args: TransitionArgs) -> dict:
    exposed = state.surface_verbs(state.WORK_TRANSITION)
    if args.verb not in exposed:
        raise InvalidArgs(f"verb {args.verb!r} is not exposed by work_transition: {', '.join(exposed)}")
    verb = Verb(args.verb)
    return _apply(ctx, verb, from_json(state.args_type(verb), dict(args.args), path="$.args"), args.item_id)


@tool(
    "tracker_render",
    "Regenerate docs/tracker.md from state.json (items and imported legacy rows). Only the region between the mesh "
    "markers is replaced; the first render over a hand-written file replaces its table and keeps the original under "
    ".mesh/. dry_run=true writes nothing. PM only.",
    RenderArgs,
    "pm",
)
def tracker_render(ctx: Context, args: RenderArgs) -> dict:
    root = _root(ctx)
    _pm_only(ctx, "tracker_render", ctx.caller())
    out = tracker.render_file(root, ctx.store.read().doc, dry_run=args.dry_run)
    return {
        "changed": out.changed,
        "written": out.changed and not args.dry_run,
        "first_render": out.first_render,
        "backup": f".mesh/{out.backup}" if out.backup else None,
        "path": str(tracker.TRACKER),
        "region": out.region,
    }


@tool(
    "pane_command",
    "Type one operator-named slash command into a lead's pane (D84, D85). PM only. Cite the hook-recorded operator "
    "event that names the command, or propose=true first (it returns an id P-<n>) and cite the operator's very next "
    "prompt, which must name that id. The "
    "pane must be idle; trust and permission prompts are never answered; the keypress is never retried.",
    PaneCommandArgs,
    "pm",
)
def pane_command(ctx: Context, args: PaneCommandArgs) -> dict:
    root, pmx = _adapters(ctx)
    caller = ctx.caller()
    pc = pane.PaneCommand(
        ctx.store,
        pmx,
        ctx.tmux or Tmux(ctx.env),
        root,
        ctx.env.get(identity.PANE_VAR) or None,
        ctx.clock(),
        confirm=lambda: _confirm(ctx, caller, "pane_command"),
    )
    if args.propose:
        return pc.propose(caller, args.role, args.command)
    return pc.send(caller, args.role, args.command, args.event_id, args.item_id)


@tool(
    "companions_install",
    "Install the missing D88 companion plugins (pstack, mattpocock-skills) at PROJECT scope with fixed claude CLI "
    "commands. PM only. Cite the hook-recorded operator event (fresh, single use) that names `companions_install` "
    "(the whole plan) or the one plugin in `only`. Returns each command's exit code and each plugin's installed "
    "version; gstack is not a plugin: its install command is printed for the operator, never run.",
    InstallArgs,
    "pm",
)
def companions_install(ctx: Context, args: InstallArgs) -> dict:
    root = _root(ctx)
    caller = ctx.caller()
    _pm_only(ctx, "companions_install", caller)
    home = _home(ctx.env)
    have = companions.present(home, root)
    manual = companions.plan_for(have)["manual"]
    event = layers.resolve_event(ctx.store, args.event_id)
    tokens = ("companions_install",) if args.only is None else ("companions_install", args.only)
    _check_install_event(ctx, caller, ctx.store.read().doc, event, tokens)
    plugins = companions.wanted(have, args.only)
    if not plugins:
        return {"receipt": None, "installed": [], "manual": manual, "note": "nothing to install; the event is unused"}
    _confirm(ctx, caller, "companions_install")  # D113
    with ctx.store.locked() as txn:  # consume the event before anything runs (single use)
        _check_install_event(ctx, caller, txn.doc, event, tokens)
        txn.commit(layers.consume(txn.doc, event, "companions_install", ",".join(plugins), "attempting"))
    ran = companions.run_plan(plugins, dict(ctx.env), root, home)
    receipt = layers.write_receipt(
        ctx.store,
        ctx.clock(),
        "companions_install",
        {"event_id": event.id, **ran, "manual": manual},
        event_id=event.id,
        status="done",
    )
    return {"receipt": receipt, "installed": [p for p, v in ran["versions"].items() if v], "manual": manual}


def _check_install_event(ctx: Context, caller: Caller, doc, event, tokens: tuple[str, ...]) -> None:
    bound = layers.bind_event(
        doc,
        event,
        fresh=True,
        tokens=tokens,
        unbound="the operator event must name `companions_install` (the whole plan) or the plugin in `only` (D88)",
    )
    if isinstance(bound, layers.Unbound):
        _refuse(ctx, "companions_install", bound.code, bound.detail, caller)


# ---------------------------------------------------------------- the one entry for both front doors
def call(ctx: Context, name: str, arguments: Mapping[str, Any] | None) -> layers.ToolResult:
    """Runs one tool through its layers (`layers.tool_layer`): a refusal or a failure is an error RESULT; only an
    unknown tool name raises (a protocol error for the front door)."""
    tool_ = REGISTRY.get(name)
    if tool_ is None:
        raise UnknownTool(name)
    return tool_.run(ctx, arguments)
