"""The one orchestration path for every verb, from every caller (MCP, CLI, the engine's own mesh rows).

  execute(req):
    1  snap  = store.read()                              lock-free
    2  plan  = state.plan(snap.doc, req)                 pure; a refusal here is recorded and returned
    3  facts = gather(plan.needs, snap.doc, req, cfg)    UNLOCKED: the operator event from the store itself; every
                                                         other fact from the injected provider, asked only for those
    4  with store.locked():                              SHORT: read-check-write, nothing slow inside
           out = state.decide(fresh_doc, req, facts, cfg, plan.stamp)
           Applied (not a no-op) -> commit
    5  Refused -> appended to refusals.jsonl; a STALE refusal of a VERSION-stamped row is re-planned ONCE
    6  Applied -> the post-commit hook, outside the lock
    7  Applied (not a no-op) -> drain: the due (mesh) rows run through steps 1-6; one not due yet records nothing

No subprocess, no clock and no network in this file: the facts provider and the hook are injected, so the engine is
testable with plain values and the adapters (projmux, git, tmux) arrive later as providers.

Crash points: before the commit nothing happened; after it the write is whole (atomic replace). A hook that never ran
(crash after the commit) leaves its effect to the recovery step that recomputes it from state.json, never to memory.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from . import layers, state
from .model import (
    NO_FACTS,
    Applied,
    Caller,
    Config,
    Doc,
    EventFact,
    Fact,
    Facts,
    NoArgs,
    Refused,
    Request,
    SchemaTooNew,
    StampKind,
    Verb,
)
from .store import Store

FactsProvider = Callable[[frozenset[Fact], Doc, Request, Config], Facts]
CommitHook = Callable[[Applied], None]


def no_facts(needs: frozenset[Fact], doc: Doc, req: Request, cfg: Config) -> Facts:
    """The core alone observes nothing outside `.mesh/`: a row that needs the world (projmux, git, a verification run)
    refuses with `fact_unavailable` until the adapters plug in here. They get the facts to observe (never
    OPERATOR_EVENT: that one is mesh-owned and read from the store by the engine, so a provider cannot forge it), the
    lock-free snapshot, the request and the config (verification commands and hash)."""
    return NO_FACTS


class Engine:
    def __init__(
        self,
        store: Store,
        *,
        facts: FactsProvider | None = None,
        cfg: Config | None = None,
        on_commit: CommitHook | None = None,
    ):
        self.store = store
        self.facts = facts or no_facts
        self.cfg = cfg  # None: re-read .mesh/config.toml on every call
        self.on_commit = on_commit

    def execute(self, req: Request) -> Applied | Refused:
        """Domain outcomes are values (Applied | Refused, a refusal already recorded). Busy, SchemaTooNew,
        NotInitialized and InvariantViolation are raised. After a write, the due (mesh) steps it may have made due run
        through the same path (`drain`); the caller gets its own outcome. A drain that fails never reaches the caller:
        its write is committed (an error would make it retry that write), and the due rows stay due for the next
        drain, which recomputes them from state.json."""
        out = self._run(req, record_refusals=True)
        if isinstance(out, Applied) and not out.noop:
            try:
                self.drain(req.at)
            except Exception as exc:  # one line, the error type only: no state or env contents
                layers.log(f"follow-up drain failed ({type(exc).__name__}); due steps stay due")
        return out

    def drain(self, at: str, max_steps: int = 20) -> tuple[Applied, ...]:
        """Runs the (mesh) rows that need facts and are due (children done, the split parent's own verification) through
        the same path as any verb. `state.mesh_steps_due` is stateless, so a crash between a write and its follow-up
        loses nothing: the next drain recomputes it. A due row that does not apply (a selection guard says no, or its
        fact is not available yet) is skipped and not recorded: it is not a caller's refusal."""
        done: list[Applied] = []
        observed: dict = {}  # ONE observation per due item and record version (F10): see `_gather`
        for _ in range(max_steps):
            for due in state.mesh_steps_due(self.store.read().doc):
                step = Request(Verb.MESH, Caller.mesh(), NoArgs(), at, due.item_id, due.row.id)
                out = self._run(step, record_refusals=False, observed=observed)
                if isinstance(out, Applied):
                    done.append(out)
                    break
            else:
                return tuple(done)
        return tuple(done)

    def _run(self, req: Request, *, record_refusals: bool, observed: dict | None = None) -> Applied | Refused:
        cfg = self.cfg or self.store.read_config()
        for attempt in (0, 1):
            snap = self.store.read()
            if not snap.writable:
                raise SchemaTooNew("state.json was written by a newer plugin", hint="update the supervisor-mesh plugin")
            plan = state.plan(snap.doc, req)
            if isinstance(plan, Refused):
                out = plan
                break
            facts = self._gather(plan.needs, snap.doc, req, cfg, observed)
            with self.store.locked() as txn:
                out = state.decide(txn.doc, req, facts, cfg, plan.stamp)
                if isinstance(out, Applied) and not out.noop:
                    txn.commit(out.doc)
            stale = isinstance(out, Refused) and out.refusal.code.value == "stale"
            if stale and plan.stamp is not None and plan.stamp.kind is StampKind.VERSION and attempt == 0:
                continue
            break
        if isinstance(out, Refused):
            if record_refusals:
                layers.record_refusal(self.store, out.refusal)
        elif not out.noop and self.on_commit is not None:
            try:
                self.on_commit(out)
            except Exception as exc:  # the write is whole: an effect never undoes it
                layers.log(f"post-commit hook failed ({type(exc).__name__})")
        return out

    def _gather(
        self, needs: frozenset[Fact], doc: Doc, req: Request, cfg: Config, observed: dict | None = None
    ) -> Facts:
        """`observed` (a drain's memo): the (mesh) rows that compete for one item (a split parent's verified / verify
        fail pair) share ONE observation of the same item version, so a verification runs once per due item and both
        selection guards judge the same result (a flaky second run can neither double the cost nor strand the item)."""
        outside = needs - {Fact.OPERATOR_EVENT}
        item = doc.items.get(req.item_id or "")
        key = (req.item_id, item.version if item else None, outside)
        if observed is not None and key in observed:
            facts = observed[key]
        else:
            facts = self.facts(outside, doc, req, cfg) if outside else NO_FACTS
            if observed is not None:
                observed[key] = facts
        if Fact.OPERATOR_EVENT in needs:
            cited = getattr(req.args, "event_id", None)
            facts = replace(facts, operator_event=EventFact(layers.resolve_event(self.store, cited)))
        else:
            facts = replace(facts, operator_event=None)  # never from a provider
        return facts
