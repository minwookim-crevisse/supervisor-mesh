"""WO-0044 (D135, U3): `mesh_status` moved into `mesh/view.py` (one snapshot, a pure `project_view`) and its texts into
`mesh/hints.py`, as a PURE MOVE. The expected values are `mesh_status` outputs CAPTURED AT THE BASE COMMIT (5105b84)
through the tool interface, with a fixed clock, the production caller (the calling projmux pane, `identity`) and the
fake projmux of tests/_mesh_fakes.py. They are literals: never recomputed from the new code. The capture compares the
JSON text (key order included), so a moved key or a changed value fails.

Also: projmux `get agents` is asked ONCE per `mesh_status` call (it was twice: once for the caller, once for the leads),
and the caller reasons / rules of `identity.resolve` and of a caller_not_pm refusal (WO-0049 rework 2) are unchanged.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from _helpers import ROOT
from _mesh_fakes import Fakes
from test_mesh_state import contract, doc_of, item_in

sys.path.insert(0, str(ROOT))
from mesh import identity, init, tools  # noqa: E402
from mesh.model import ActionKind, Actor, HistoryEntry, PendingAction, Phase, Scope, Verb  # noqa: E402
from mesh.projmux import Projmux  # noqa: E402
from mesh.store import Store  # noqa: E402

NOW = "2026-11-18T01:00:00Z"
RECENT = "2026-11-18T00:58:00Z"  # 2 minutes before NOW: not quiet
REPORT_AT = 1794963300  # 2026-11-18T00:55:00Z


class Project:
    """One throwaway project: a root, a temp HOME, the projmux fakes, the store. The PM agent is live by default."""

    def __init__(self, tmp: Path, *, initialized: bool = True, pm: bool = True):
        self.tmp = tmp
        self.root = tmp / "proj"
        self.root.mkdir()
        self.home = tmp / "home"
        self.home.mkdir()
        self.fakes = Fakes(tmp / "fakes")
        self.store = Store(self.root / ".mesh")
        if initialized:
            self.store.create()
        if pm:
            self.agent("pm-uid", "pm")

    def agent(self, uid, role, *, cwd=None, phase="Running", worker_of=None, kind="response_complete", at=RECENT):
        self.fakes.agent(uid, role, cwd or self.root, phase=phase)
        st = self.fakes.load()
        a = next(x for x in st["agents"] if x["metadata"]["uid"] == uid)
        if worker_of:
            a["metadata"]["labels"]["worker-of"] = worker_of
        a["status"]["interaction"] = {"kind": kind, "observedAt": at}
        self.fakes.save()

    def seed(self, *items, **kw):
        with self.store.locked() as txn:
            txn.commit(doc_of(*items, **kw))

    def env(self, pane: str | None = "pane-pm-uid") -> dict:
        base = {"PATH": "/usr/bin:/bin", "HOME": str(self.home)}
        if pane is not None:
            base[identity.PANE_VAR] = pane
        return self.fakes.env(base)

    def ctx(self, pane: str | None = "pane-pm-uid"):
        """The production context: no injected caller, so the caller is the calling pane (`identity.PaneCaller`)."""
        return tools.Context.for_project(self.root, self.env(pane), clock=lambda: NOW)

    def status(self, pane: str | None = "pane-pm-uid", **args) -> str:
        out = tools.call(self.ctx(pane), "mesh_status", args)
        return json.dumps(out.data)

    def agents_asked(self) -> int:
        return len(self.fakes.calls("projmux", "get", "agents"))

    def resolved(self, pane: str | None) -> str:
        r = identity.resolve(self.env(pane), self.root, Projmux(self.env(pane)))
        return json.dumps(
            {
                "kind": r.caller.kind.value,
                "role": r.caller.role,
                "rule": r.caller.rule,
                "reason": r.reason,
                "pane_uid": r.pane_uid,
                "has_pid": r.claude_pid is not None,
                "live_pms": r.live_pms,
            }
        )

    def refusal(self, pane: str | None) -> str:
        """A caller_not_pm refusal shows the caller's rule (D141, WO-0049 rework 2)."""
        out = tools.call(self.ctx(pane), "tracker_render", {"dry_run": True})
        return json.dumps(out.data)


def write_launcher(p: Project) -> None:
    dest = p.root / ".mesh" / "bin" / "mesh"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(init._launcher(tools.PLUGIN_ROOT), encoding="utf-8")
    dest.chmod(0o755)


def rework_history():
    return (
        HistoryEntry(
            "2026-11-17T00:00:00Z", Verb.SUBMIT, "submit", Actor.LEAD_OWN, None, Phase.RUNNING, Phase.VERIFYING
        ),
        HistoryEntry("2026-11-17T01:00:00Z", Verb.FAIL, "fail", Actor.PM, None, Phase.VERIFYING, Phase.REWORK),
        HistoryEntry("2026-11-17T02:00:00Z", Verb.REJECT, "reject", Actor.PM, None, Phase.VERIFIED, Phase.REWORK),
    )


def every_phase(p: Project) -> None:
    roles = {Phase.VERIFYING: "qa", Phase.BLOCKED_ENVIRONMENT: "qa", Phase.CLOSED: "docs", Phase.CANCELLED: "ops"}
    items = []
    for n, phase in enumerate(Phase, start=1):
        kw = {}
        if phase is Phase.REWORK:
            kw["history"] = rework_history()
        if phase is not Phase.VERIFIED:
            kw["contract"] = contract(version=1, role=roles.get(phase, "backend"))
        items.append(item_in(phase, item_id=f"WO-{n:04d}", **kw))
    p.seed(*items)
    p.agent("be-uid", "backend")
    write_launcher(p)
    p.store.record_operator_event("start the run", NOW, "pm-uid")


# ---------------------------------------------------------------- the fixture states: build, then (pane, args)
def s_not_initialized(p):
    return "pane-pm-uid", {}


def s_no_items(p):
    return "pane-pm-uid", {}


def s_every_phase(p):
    every_phase(p)
    return "pane-pm-uid", {}


def s_pending_actions(p):
    send = PendingAction("WO-0001-d1", ActionKind.DISPATCH_SEND, {"role": "backend", "attempts": "1:refused,2:refused"})
    done = PendingAction("WO-0002-d1", ActionKind.DISPATCH_SEND, {"role": "qa"}, done=True)
    attention = PendingAction("WO-0003-a1", ActionKind.PM_ATTENTION, {"reason": "environment"})
    p.seed(
        item_in(Phase.READY, contract=contract(version=1), pending_actions=(send,)),
        item_in(Phase.READY, item_id="WO-0002", contract=contract(version=1, role="qa"), pending_actions=(done,)),
        item_in(Phase.BLOCKED_ENVIRONMENT, item_id="WO-0003", pending_actions=(attention,)),
    )
    p.agent("be-uid", "backend")
    return "pane-pm-uid", {}


def s_stalls(p):
    report = "docs/reports/WO-0001-backend.md"
    sent = PendingAction("WO-0004-d1", ActionKind.DISPATCH_SEND, {"role": "backend"})
    answered = PendingAction("WO-0005-d1", ActionKind.DISPATCH_SEND, {"role": "qa"}, done=True)
    p.seed(
        item_in(Phase.RUNNING, contract=contract(version=1, scope=Scope(report=report))),
        item_in(Phase.VERIFYING, item_id="WO-0002", contract=contract(version=1, role="qa")),
        item_in(Phase.RUNNING, item_id="WO-0003", contract=contract(version=1, role="frontend")),
        item_in(Phase.READY, item_id="WO-0004", contract=contract(version=1), pending_actions=(sent,)),
        item_in(Phase.READY, item_id="WO-0005", contract=contract(version=1, role="qa"), pending_actions=(answered,)),
    )
    p.agent("be-uid", "backend", at="2026-11-18T00:50:00Z")
    p.agent("w1", None, worker_of="backend", kind="idle")
    p.agent("qa-uid", "qa", at="2026-11-18T00:30:00Z", kind="idle")
    path = p.root / report
    path.parent.mkdir(parents=True)
    path.write_text("PASS\n", encoding="utf-8")
    os.utime(path, (REPORT_AT, REPORT_AT))
    return "pane-pm-uid", {}


def s_overlaps(p):
    p.seed(
        item_in(
            Phase.RUNNING, contract=contract(version=1, scope=Scope(write_set=("src/api/**",), resources=("lock",)))
        ),
        item_in(
            Phase.READY,
            item_id="WO-0002",
            contract=contract(version=1, scope=Scope(write_set=("src/api/x.py",), resources=("lock",))),
        ),
        item_in(Phase.READY, item_id="WO-0003", contract=contract(version=1, scope=Scope(write_set=("src/**",)))),
        item_in(
            Phase.VERIFYING, item_id="WO-0004", contract=contract(version=1, scope=Scope(write_set=("docs/a.md",)))
        ),
    )
    p.agent("be-uid", "backend")
    return "pane-pm-uid", {}


def s_leads_0_1_2(p):
    p.seed(
        item_in(Phase.DRAFT, contract=contract(version=1, role="frontend")),  # 0 live leads
        item_in(Phase.DRAFT, item_id="WO-0002"),  # backend: 1 live lead
        item_in(Phase.RUNNING, item_id="WO-0003", contract=contract(version=1, role="qa")),  # 2 live leads
        item_in(Phase.CLOSED, item_id="WO-0004", contract=contract(version=1, role="ops")),  # terminal: not listed
    )
    p.agent("be-uid", "backend")
    p.agent("qa-1", "qa")
    p.agent("qa-2", "qa")
    p.agent("docs-uid", "docs")  # a live lead without an item
    p.agent("fe-stopped", "frontend", phase="Stopped")  # not Running: not live
    p.agent("fe-other", "frontend", cwd=p.tmp)  # another repo: not this project's lead
    return "pane-pm-uid", {}


def s_projmux_unavailable(p):
    p.seed(item_in(Phase.DRAFT), item_in(Phase.RUNNING, item_id="WO-0002"))
    p.agent("be-uid", "backend")
    p.fakes.set(exit={"projmux get agents -o": 1})
    return "pane-pm-uid", {}


def s_projmux_panes_unavailable(p):
    p.seed(item_in(Phase.RUNNING))
    p.agent("be-uid", "backend")
    p.fakes.set(exit={"projmux get panes -A": 1})
    return "pane-pm-uid", {}


def s_caller_pm(p):
    p.seed(item_in(Phase.DRAFT))
    return "pane-pm-uid", {}


def s_caller_lead(p):
    p.seed(item_in(Phase.DRAFT))
    p.agent("be-uid", "backend")
    return "pane-be-uid", {}


def s_caller_unlabelled(p):
    p.seed(item_in(Phase.DRAFT))
    p.agent("nolabel-uid", None)
    return "pane-nolabel-uid", {}


def s_caller_invalid_role(p):
    p.seed(item_in(Phase.DRAFT))
    p.agent("bad-uid", "Not A Role")
    return "pane-bad-uid", {}


def s_caller_outside_projmux(p):
    p.seed(item_in(Phase.DRAFT))
    return None, {}


def s_caller_no_pane(p):
    p.seed(item_in(Phase.DRAFT))
    return "pane-nowhere", {}


def s_caller_not_managed(p):
    p.seed(item_in(Phase.DRAFT))
    st = p.fakes.load()
    st["panes"].append({"metadata": {"uid": "pane-bare"}, "status": {"activation": {}}})
    p.fakes.save()
    return "pane-bare", {}


def s_caller_ambiguous(p):
    p.seed(item_in(Phase.DRAFT))
    st = p.fakes.load()
    st["panes"].append({"metadata": {"uid": "pane-pm-uid"}, "status": {"activation": {"agentUID": "pm-uid"}}})
    p.fakes.save()
    return "pane-pm-uid", {}


def s_caller_other_repo(p):
    p.seed(item_in(Phase.DRAFT))
    p.agent("far-uid", "backend", cwd=p.tmp)
    return "pane-far-uid", {}


def s_metrics_true(p):
    every_phase(p)
    return "pane-pm-uid", {"metrics": True}


def s_metrics_false(p):
    every_phase(p)
    return "pane-pm-uid", {"metrics": False}


def s_metrics_true_not_initialized(p):
    return "pane-pm-uid", {"metrics": True}


STATES = {
    "not_initialized": (s_not_initialized, {"initialized": False}),
    "no_items": (s_no_items, {}),
    "every_phase": (s_every_phase, {}),
    "pending_actions": (s_pending_actions, {}),
    "stalls": (s_stalls, {}),
    "overlaps": (s_overlaps, {}),
    "leads_0_1_2": (s_leads_0_1_2, {}),
    "projmux_unavailable": (s_projmux_unavailable, {}),
    "projmux_panes_unavailable": (s_projmux_panes_unavailable, {}),
    "caller_pm": (s_caller_pm, {}),
    "caller_lead": (s_caller_lead, {}),
    "caller_unlabelled": (s_caller_unlabelled, {}),
    "caller_invalid_role": (s_caller_invalid_role, {}),
    "caller_outside_projmux": (s_caller_outside_projmux, {}),
    "caller_no_pane": (s_caller_no_pane, {}),
    "caller_not_managed": (s_caller_not_managed, {}),
    "caller_ambiguous": (s_caller_ambiguous, {}),
    "caller_other_repo": (s_caller_other_repo, {}),
    "metrics_true": (s_metrics_true, {}),
    "metrics_false": (s_metrics_false, {}),
    "metrics_true_not_initialized": (s_metrics_true_not_initialized, {"initialized": False}),
}
CALLER_STATES = tuple(n for n in STATES if n.startswith("caller_") or n.startswith("projmux_"))


def run_state(tmp: Path, name: str) -> dict:
    """Build the state in a fresh project, then capture mesh_status (and, for the caller states, resolve and a
    caller_not_pm refusal). Returns the JSON texts and the number of `get agents` asked by the one mesh_status call."""
    build, kw = STATES[name]
    p = Project(tmp, **kw)
    pane, args = build(p)
    before = p.agents_asked()
    out = {"status": p.status(pane, **args)}
    out["agents_asked"] = p.agents_asked() - before
    if name in CALLER_STATES:
        out["resolved"] = p.resolved(pane)
        out["refusal"] = p.refusal(pane)
    return out


# ---------------------------------------------------------------- captured at the base commit (5105b84)
# fmt: off
EXPECTED_STATUS = {'not_initialized': {'ok': True,
                     'initialized': False,
                     'identity': {'kind': 'pm', 'role': None},
                     'items': {},
                     'leads': [],
                     'pending_actions': [],
                     'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                     'launcher': 'missing',
                     'last_event': None,
                     'overlaps': [],
                     'stalls': []},
 'no_items': {'ok': True,
              'initialized': True,
              'identity': {'kind': 'pm', 'role': None},
              'items': {},
              'leads': [],
              'pending_actions': [],
              'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
              'launcher': 'missing',
              'last_event': None,
              'overlaps': [],
              'stalls': []},
 'every_phase': {'ok': True,
                 'initialized': True,
                 'identity': {'kind': 'pm', 'role': None},
                 'items': {'draft': ['WO-0001'],
                           'ready': ['WO-0002'],
                           'running': ['WO-0003'],
                           'verifying': ['WO-0004'],
                           'verified': ['WO-0005'],
                           'closed': ['WO-0006'],
                           'rework': ['WO-0007'],
                           'blocked(awaiting_user)': ['WO-0008'],
                           'blocked(environment)': ['WO-0009'],
                           'blocked(limit)': ['WO-0010'],
                           'blocked(children)': ['WO-0011'],
                           'cancelled': ['WO-0012']},
                 'leads': [{'role': 'backend', 'present': True}, {'role': 'qa', 'present': False}],
                 'pending_actions': [],
                 'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                 'launcher': 'ok',
                 'last_event': {'id': 'ev-000001', 'seq': 1},
                 'overlaps': [],
                 'stalls': [{'kind': 'lead_absent', 'item_id': 'WO-0004', 'role': 'qa', 'nudge': 'pm'}]},
 'pending_actions': {'ok': True,
                     'initialized': True,
                     'identity': {'kind': 'pm', 'role': None},
                     'items': {'ready': ['WO-0001', 'WO-0002'], 'blocked(environment)': ['WO-0003']},
                     'leads': [{'role': 'backend', 'present': True}, {'role': 'qa', 'present': False}],
                     'pending_actions': [{'item_id': 'WO-0001',
                                          'action_id': 'WO-0001-d1',
                                          'kind': 'dispatch_send',
                                          'attempts': ['1:refused', '2:refused']},
                                         {'item_id': 'WO-0003',
                                          'action_id': 'WO-0003-a1',
                                          'kind': 'pm_attention',
                                          'attempts': []}],
                     'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                     'launcher': 'missing',
                     'last_event': None,
                     'overlaps': [],
                     'stalls': [{'kind': 'dispatch_unsent',
                                 'item_id': 'WO-0001',
                                 'role': 'backend',
                                 'nudge': 'pm'}]},
 'stalls': {'ok': True,
            'initialized': True,
            'identity': {'kind': 'pm', 'role': None},
            'items': {'running': ['WO-0001', 'WO-0003'],
                      'verifying': ['WO-0002'],
                      'ready': ['WO-0004', 'WO-0005']},
            'leads': [{'role': 'backend', 'present': True},
                      {'role': 'frontend', 'present': False},
                      {'role': 'qa', 'present': True}],
            'pending_actions': [{'item_id': 'WO-0004',
                                 'action_id': 'WO-0004-d1',
                                 'kind': 'dispatch_send',
                                 'attempts': []}],
            'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
            'launcher': 'missing',
            'last_event': None,
            'overlaps': [],
            'stalls': [{'kind': 'worker_done',
                        'item_id': 'WO-0001',
                        'role': 'backend',
                        'nudge': 'lead backend',
                        'report': 'docs/reports/WO-0001-backend.md',
                        'workers': ['agent-w1']},
                       {'kind': 'lead_quiet',
                        'item_id': 'WO-0002',
                        'role': 'qa',
                        'nudge': 'lead qa',
                        'minutes': 15},
                       {'kind': 'lead_absent', 'item_id': 'WO-0003', 'role': 'frontend', 'nudge': 'pm'},
                       {'kind': 'dispatch_unsent', 'item_id': 'WO-0004', 'role': 'backend', 'nudge': 'pm'},
                       {'kind': 'dispatch_unanswered',
                        'item_id': 'WO-0005',
                        'role': 'qa',
                        'nudge': 'lead qa',
                        'minutes': 15}]},
 'overlaps': {'ok': True,
              'initialized': True,
              'identity': {'kind': 'pm', 'role': None},
              'items': {'running': ['WO-0001'], 'ready': ['WO-0002', 'WO-0003'], 'verifying': ['WO-0004']},
              'leads': [{'role': 'backend', 'present': True}],
              'pending_actions': [],
              'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
              'launcher': 'missing',
              'last_event': None,
              'overlaps': [{'items': ['WO-0001', 'WO-0002'],
                            'write_scope': ['src/api/**'],
                            'resources': ['lock']},
                           {'items': ['WO-0001', 'WO-0003'], 'write_scope': ['src/api/**'], 'resources': []}],
              'stalls': []},
 'leads_0_1_2': {'ok': True,
                 'initialized': True,
                 'identity': {'kind': 'pm', 'role': None},
                 'items': {'draft': ['WO-0001', 'WO-0002'], 'running': ['WO-0003'], 'closed': ['WO-0004']},
                 'leads': [{'role': 'backend', 'present': True},
                           {'role': 'docs', 'present': True},
                           {'role': 'frontend', 'present': False},
                           {'role': 'qa', 'present': True}],
                 'pending_actions': [],
                 'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                 'launcher': 'missing',
                 'last_event': None,
                 'overlaps': [],
                 'stalls': [{'kind': 'lead_absent', 'item_id': 'WO-0003', 'role': 'qa', 'nudge': 'pm'}]},
 'projmux_unavailable': {'ok': True,
                         'initialized': True,
                         'identity': {'kind': 'unknown', 'role': None},
                         'items': {'draft': ['WO-0001'], 'running': ['WO-0002']},
                         'leads': [{'role': 'backend', 'present': None}],
                         'pending_actions': [],
                         'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                         'launcher': 'missing',
                         'last_event': None,
                         'overlaps': [],
                         'stalls': [],
                         'projmux': 'unavailable'},
 'projmux_panes_unavailable': {'ok': True,
                               'initialized': True,
                               'identity': {'kind': 'unknown', 'role': None},
                               'items': {'running': ['WO-0001']},
                               'leads': [{'role': 'backend', 'present': True}],
                               'pending_actions': [],
                               'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                               'launcher': 'missing',
                               'last_event': None,
                               'overlaps': [],
                               'stalls': []},
 'caller_pm': {'ok': True,
               'initialized': True,
               'identity': {'kind': 'pm', 'role': None},
               'items': {'draft': ['WO-0001']},
               'leads': [{'role': 'backend', 'present': False}],
               'pending_actions': [],
               'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
               'launcher': 'missing',
               'last_event': None,
               'overlaps': [],
               'stalls': []},
 'caller_lead': {'ok': True,
                 'initialized': True,
                 'identity': {'kind': 'lead', 'role': 'backend'},
                 'items': {'draft': ['WO-0001']},
                 'leads': [{'role': 'backend', 'present': True}],
                 'pending_actions': [],
                 'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                 'launcher': 'missing',
                 'last_event': None,
                 'overlaps': [],
                 'stalls': []},
 'caller_unlabelled': {'ok': True,
                       'initialized': True,
                       'identity': {'kind': 'unknown', 'role': None},
                       'items': {'draft': ['WO-0001']},
                       'leads': [{'role': 'backend', 'present': False}],
                       'pending_actions': [],
                       'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                       'launcher': 'missing',
                       'last_event': None,
                       'overlaps': [],
                       'stalls': []},
 'caller_invalid_role': {'ok': True,
                         'initialized': True,
                         'identity': {'kind': 'unknown', 'role': None},
                         'items': {'draft': ['WO-0001']},
                         'leads': [{'role': 'Not A Role', 'present': True},
                                   {'role': 'backend', 'present': False}],
                         'pending_actions': [],
                         'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                         'launcher': 'missing',
                         'last_event': None,
                         'overlaps': [],
                         'stalls': []},
 'caller_outside_projmux': {'ok': True,
                            'initialized': True,
                            'identity': {'kind': 'unknown', 'role': None},
                            'items': {'draft': ['WO-0001']},
                            'leads': [{'role': 'backend', 'present': False}],
                            'pending_actions': [],
                            'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                            'launcher': 'missing',
                            'last_event': None,
                            'overlaps': [],
                            'stalls': []},
 'caller_no_pane': {'ok': True,
                    'initialized': True,
                    'identity': {'kind': 'unknown', 'role': None},
                    'items': {'draft': ['WO-0001']},
                    'leads': [{'role': 'backend', 'present': False}],
                    'pending_actions': [],
                    'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                    'launcher': 'missing',
                    'last_event': None,
                    'overlaps': [],
                    'stalls': []},
 'caller_not_managed': {'ok': True,
                        'initialized': True,
                        'identity': {'kind': 'unknown', 'role': None},
                        'items': {'draft': ['WO-0001']},
                        'leads': [{'role': 'backend', 'present': False}],
                        'pending_actions': [],
                        'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                        'launcher': 'missing',
                        'last_event': None,
                        'overlaps': [],
                        'stalls': []},
 'caller_ambiguous': {'ok': True,
                      'initialized': True,
                      'identity': {'kind': 'unknown', 'role': None},
                      'items': {'draft': ['WO-0001']},
                      'leads': [{'role': 'backend', 'present': False}],
                      'pending_actions': [],
                      'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                      'launcher': 'missing',
                      'last_event': None,
                      'overlaps': [],
                      'stalls': []},
 'caller_other_repo': {'ok': True,
                       'initialized': True,
                       'identity': {'kind': 'unknown', 'role': None},
                       'items': {'draft': ['WO-0001']},
                       'leads': [{'role': 'backend', 'present': False}],
                       'pending_actions': [],
                       'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                       'launcher': 'missing',
                       'last_event': None,
                       'overlaps': [],
                       'stalls': []},
 'metrics_true': {'ok': True,
                  'initialized': True,
                  'identity': {'kind': 'pm', 'role': None},
                  'items': {'draft': ['WO-0001'],
                            'ready': ['WO-0002'],
                            'running': ['WO-0003'],
                            'verifying': ['WO-0004'],
                            'verified': ['WO-0005'],
                            'closed': ['WO-0006'],
                            'rework': ['WO-0007'],
                            'blocked(awaiting_user)': ['WO-0008'],
                            'blocked(environment)': ['WO-0009'],
                            'blocked(limit)': ['WO-0010'],
                            'blocked(children)': ['WO-0011'],
                            'cancelled': ['WO-0012']},
                  'leads': [{'role': 'backend', 'present': True}, {'role': 'qa', 'present': False}],
                  'pending_actions': [],
                  'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                  'launcher': 'ok',
                  'last_event': {'id': 'ev-000001', 'seq': 1},
                  'overlaps': [],
                  'stalls': [{'kind': 'lead_absent', 'item_id': 'WO-0004', 'role': 'qa', 'nudge': 'pm'}],
                  'metrics': {'rework': {'total': 2,
                                         'by_item': {'WO-0001': 0,
                                                     'WO-0002': 0,
                                                     'WO-0003': 0,
                                                     'WO-0004': 0,
                                                     'WO-0005': 0,
                                                     'WO-0006': 0,
                                                     'WO-0007': 2,
                                                     'WO-0008': 0,
                                                     'WO-0009': 0,
                                                     'WO-0010': 0,
                                                     'WO-0011': 0,
                                                     'WO-0012': 0}},
                              'pass_reversals': {'total': 1,
                                                 'by_item': {'WO-0001': 0,
                                                             'WO-0002': 0,
                                                             'WO-0003': 0,
                                                             'WO-0004': 0,
                                                             'WO-0005': 0,
                                                             'WO-0006': 0,
                                                             'WO-0007': 1,
                                                             'WO-0008': 0,
                                                             'WO-0009': 0,
                                                             'WO-0010': 0,
                                                             'WO-0011': 0,
                                                             'WO-0012': 0}},
                              'operator_interventions': 1,
                              'note': 'indicative only, never a gate (D90)'}},
 'metrics_false': {'ok': True,
                   'initialized': True,
                   'identity': {'kind': 'pm', 'role': None},
                   'items': {'draft': ['WO-0001'],
                             'ready': ['WO-0002'],
                             'running': ['WO-0003'],
                             'verifying': ['WO-0004'],
                             'verified': ['WO-0005'],
                             'closed': ['WO-0006'],
                             'rework': ['WO-0007'],
                             'blocked(awaiting_user)': ['WO-0008'],
                             'blocked(environment)': ['WO-0009'],
                             'blocked(limit)': ['WO-0010'],
                             'blocked(children)': ['WO-0011'],
                             'cancelled': ['WO-0012']},
                   'leads': [{'role': 'backend', 'present': True}, {'role': 'qa', 'present': False}],
                   'pending_actions': [],
                   'companions': {'gstack': False, 'mattpocock-skills': False, 'pstack': False},
                   'launcher': 'ok',
                   'last_event': {'id': 'ev-000001', 'seq': 1},
                   'overlaps': [],
                   'stalls': [{'kind': 'lead_absent', 'item_id': 'WO-0004', 'role': 'qa', 'nudge': 'pm'}]},
 'metrics_true_not_initialized': {'ok': True,
                                  'initialized': False,
                                  'identity': {'kind': 'pm', 'role': None},
                                  'items': {},
                                  'leads': [],
                                  'pending_actions': [],
                                  'companions': {'gstack': False,
                                                 'mattpocock-skills': False,
                                                 'pstack': False},
                                  'launcher': 'missing',
                                  'last_event': None,
                                  'overlaps': [],
                                  'stalls': [],
                                  'metrics': None}}

EXPECTED_RESOLVED = {'projmux_unavailable': {'kind': 'unknown',
                         'role': None,
                         'rule': 'projmux_unavailable',
                         'reason': 'projmux unavailable',
                         'pane_uid': None,
                         'has_pid': False,
                         'live_pms': 0},
 'projmux_panes_unavailable': {'kind': 'unknown',
                               'role': None,
                               'rule': 'projmux_unavailable',
                               'reason': 'projmux unavailable',
                               'pane_uid': None,
                               'has_pid': False,
                               'live_pms': 0},
 'caller_pm': {'kind': 'pm',
               'role': None,
               'rule': None,
               'reason': 'pm',
               'pane_uid': 'pane-pm-uid',
               'has_pid': True,
               'live_pms': 1},
 'caller_lead': {'kind': 'lead',
                 'role': 'backend',
                 'rule': None,
                 'reason': 'lead',
                 'pane_uid': 'pane-be-uid',
                 'has_pid': True,
                 'live_pms': 1},
 'caller_unlabelled': {'kind': 'unknown',
                       'role': None,
                       'rule': 'not_member',
                       'reason': "the pane's agent is not a live, labelled member of this project",
                       'pane_uid': None,
                       'has_pid': False,
                       'live_pms': 0},
 'caller_invalid_role': {'kind': 'unknown',
                         'role': None,
                         'rule': 'invalid_role',
                         'reason': 'the role label is not a valid role',
                         'pane_uid': None,
                         'has_pid': False,
                         'live_pms': 0},
 'caller_outside_projmux': {'kind': 'unknown',
                            'role': None,
                            'rule': 'no_pane_env',
                            'reason': 'not inside a managed projmux pane',
                            'pane_uid': None,
                            'has_pid': False,
                            'live_pms': 0},
 'caller_no_pane': {'kind': 'unknown',
                    'role': None,
                    'rule': 'no_pane',
                    'reason': 'no pane matches',
                    'pane_uid': None,
                    'has_pid': False,
                    'live_pms': 0},
 'caller_not_managed': {'kind': 'unknown',
                        'role': None,
                        'rule': 'not_managed',
                        'reason': 'this pane is not a managed agent',
                        'pane_uid': None,
                        'has_pid': False,
                        'live_pms': 0},
 'caller_ambiguous': {'kind': 'unknown',
                      'role': None,
                      'rule': 'ambiguous_pane',
                      'reason': 'ambiguous pane identity',
                      'pane_uid': None,
                      'has_pid': False,
                      'live_pms': 0},
 'caller_other_repo': {'kind': 'unknown',
                       'role': None,
                       'rule': 'not_member',
                       'reason': "the pane's agent is not a live, labelled member of this project",
                       'pane_uid': None,
                       'has_pid': False,
                       'live_pms': 0}}

EXPECTED_REFUSAL = {'projmux_unavailable': {'ok': False,
                         'error': {'code': 'refused',
                                   'message': 'tracker_render is a PM tool; label the PM pane (D82): start '
                                              'it with `projmux create claude --project <name> --label '
                                              'role=pm`; until then every pm tool is refused and the '
                                              'operator-event hook records nothing (rule: '
                                              'projmux_unavailable)',
                                   'reason': 'caller_not_pm',
                                   'rule': 'projmux_unavailable'}},
 'projmux_panes_unavailable': {'ok': False,
                               'error': {'code': 'refused',
                                         'message': 'tracker_render is a PM tool; label the PM pane (D82): '
                                                    'start it with `projmux create claude --project <name> '
                                                    '--label role=pm`; until then every pm tool is refused '
                                                    'and the operator-event hook records nothing (rule: '
                                                    'projmux_unavailable)',
                                         'reason': 'caller_not_pm',
                                         'rule': 'projmux_unavailable'}},
 'caller_pm': {'ok': True,
               'changed': True,
               'written': False,
               'first_render': True,
               'backup': None,
               'path': 'docs/tracker.md',
               'region': '<!-- mesh:tracker:begin -->\n'
                         '| id | title | role | depends | status | report |\n'
                         '|---|---|---|---|---|---|\n'
                         '| WO-0001 | ship it | backend | — | draft | — |\n'
                         '<!-- mesh:tracker:end -->'},
 'caller_lead': {'ok': False,
                 'error': {'code': 'refused',
                           'message': 'tracker_render is a PM tool (rule: not_pm_role)',
                           'reason': 'caller_not_pm',
                           'rule': 'not_pm_role'}},
 'caller_unlabelled': {'ok': False,
                       'error': {'code': 'refused',
                                 'message': 'tracker_render is a PM tool; label the PM pane (D82): start it '
                                            'with `projmux create claude --project <name> --label role=pm`; '
                                            'until then every pm tool is refused and the operator-event hook '
                                            'records nothing (rule: not_member)',
                                 'reason': 'caller_not_pm',
                                 'rule': 'not_member'}},
 'caller_invalid_role': {'ok': False,
                         'error': {'code': 'refused',
                                   'message': 'tracker_render is a PM tool; label the PM pane (D82): start '
                                              'it with `projmux create claude --project <name> --label '
                                              'role=pm`; until then every pm tool is refused and the '
                                              'operator-event hook records nothing (rule: invalid_role)',
                                   'reason': 'caller_not_pm',
                                   'rule': 'invalid_role'}},
 'caller_outside_projmux': {'ok': False,
                            'error': {'code': 'refused',
                                      'message': 'tracker_render is a PM tool; label the PM pane (D82): '
                                                 'start it with `projmux create claude --project <name> '
                                                 '--label role=pm`; until then every pm tool is refused and '
                                                 'the operator-event hook records nothing (rule: '
                                                 'no_pane_env)',
                                      'reason': 'caller_not_pm',
                                      'rule': 'no_pane_env'}},
 'caller_no_pane': {'ok': False,
                    'error': {'code': 'refused',
                              'message': 'tracker_render is a PM tool; label the PM pane (D82): start it '
                                         'with `projmux create claude --project <name> --label role=pm`; '
                                         'until then every pm tool is refused and the operator-event hook '
                                         'records nothing (rule: no_pane)',
                              'reason': 'caller_not_pm',
                              'rule': 'no_pane'}},
 'caller_not_managed': {'ok': False,
                        'error': {'code': 'refused',
                                  'message': 'tracker_render is a PM tool; label the PM pane (D82): start it '
                                             'with `projmux create claude --project <name> --label role=pm`; '
                                             'until then every pm tool is refused and the operator-event '
                                             'hook records nothing (rule: not_managed)',
                                  'reason': 'caller_not_pm',
                                  'rule': 'not_managed'}},
 'caller_ambiguous': {'ok': False,
                      'error': {'code': 'refused',
                                'message': 'tracker_render is a PM tool; label the PM pane (D82): start it '
                                           'with `projmux create claude --project <name> --label role=pm`; '
                                           'until then every pm tool is refused and the operator-event hook '
                                           'records nothing (rule: ambiguous_pane)',
                                'reason': 'caller_not_pm',
                                'rule': 'ambiguous_pane'}},
 'caller_other_repo': {'ok': False,
                       'error': {'code': 'refused',
                                 'message': 'tracker_render is a PM tool; label the PM pane (D82): start it '
                                            'with `projmux create claude --project <name> --label role=pm`; '
                                            'until then every pm tool is refused and the operator-event hook '
                                            'records nothing (rule: not_member)',
                                 'reason': 'caller_not_pm',
                                 'rule': 'not_member'}}}
# fmt: on


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class ByteIdenticalStatusTests(Case):
    def check(self, name):
        got = run_state(self.tmp, name)
        self.assertEqual(got["status"], json.dumps(EXPECTED_STATUS[name]))
        if name in CALLER_STATES:
            self.assertEqual(got["resolved"], json.dumps(EXPECTED_RESOLVED[name]))
            self.assertEqual(got["refusal"], json.dumps(EXPECTED_REFUSAL[name]))


def _add(name):
    setattr(ByteIdenticalStatusTests, f"test_{name}", lambda self: self.check(name))


for _name in STATES:
    _add(_name)


class OneFetchTests(Case):
    def test_projmux_agents_is_asked_once_per_mesh_status_call(self):
        for name in ("every_phase", "stalls", "caller_pm", "caller_lead", "projmux_unavailable", "caller_unlabelled"):
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(run_state(Path(tmp), name)["agents_asked"], 1)

    def test_resolve_from_the_fetched_agents_answers_as_its_own_fetch_would(self):
        """`identity.resolve(..., agents)`: the captured reason and rule of every caller state, from an answer fetched
        before; a failed fetch (`ProjmuxUnavailable`) resolves as projmux_unavailable once the pane is found."""
        from mesh.projmux import ProjmuxUnavailable

        for name in CALLER_STATES:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                build, kw = STATES[name]
                p = Project(Path(tmp), **kw)
                pane, _ = build(p)
                pmx = Projmux(p.env(pane))
                try:
                    fetched = pmx.agents()
                except ProjmuxUnavailable as exc:
                    fetched = exc
                before = p.agents_asked()
                r = identity.resolve(p.env(pane), p.root, pmx, fetched)
                self.assertEqual(p.agents_asked(), before)  # the answer given is the one used
                got = {"kind": r.caller.kind.value, "rule": r.caller.rule, "reason": r.reason}
                want = EXPECTED_RESOLVED[name]
                self.assertEqual(got, {"kind": want["kind"], "rule": want["rule"], "reason": want["reason"]})
        with tempfile.TemporaryDirectory() as tmp:
            p = Project(Path(tmp))
            r = identity.resolve(p.env(), p.root, Projmux(p.env()), ProjmuxUnavailable("x"))
            self.assertEqual((r.caller.rule, r.reason), ("projmux_unavailable", "projmux unavailable"))


if __name__ == "__main__":
    unittest.main()
