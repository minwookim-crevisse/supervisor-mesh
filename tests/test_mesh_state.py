"""Acceptance 1 (core part): the transition table, the pure decision function and the section-10 invariants."""

import ast
import random
import sys
import unittest
from dataclasses import replace
from unittest import mock

from _helpers import ROOT
from _mesh_tla import load_probe_names, load_table, parse_table

sys.path.insert(0, str(ROOT))
from mesh import state, tools  # noqa: E402
from mesh.model import (
    CREATED,
    NO_FACTS,
    NON_TERMINAL,
    Actor,
    Applied,
    Attempt,
    Caller,
    CancelArgs,
    ChildDraft,
    Command,
    CommandRun,  # noqa: E402
    Config,
    Contract,
    ContractPatch,
    Counters,
    DispatchSend,
    EventFact,
    Evidence,
    EvidenceKind,
    Facts,
    FailArgs,
    Failure,
    FailureClass,
    InvariantViolation,
    Item,
    LeadAgent,
    LegacyRow,
    Links,
    NewArgs,
    NoArgs,
    OperatorEvent,
    OverrideArgs,
    Phase,
    PrecheckOutcome,
    ReadyArgs,
    ReassignArgs,
    RefusalCode,
    Refused,
    RejectArgs,
    Reply,
    ReplyArgs,
    ReproOutcome,
    Request,
    ReviseArgs,
    RunStatus,
    Scope,
    SplitArgs,
    StartArgs,
    SubmitArgs,
    TypeSpec,
    Verb,
    VerificationResult,
    VerifyArgs,
    empty_doc,
    repro_hash,
)

TLA = ROOT / "tests/formal/WorkItemState.tla"
AT = "2026-01-01T00:00:00Z"
SHA = "a" * 40
PM, UNKNOWN = Caller.pm("pm-uid"), Caller.unknown()
BACKEND, FRONTEND = Caller.lead("backend", "be-uid"), Caller.lead("frontend", "fe-uid")
HEAD, COMMIT = "b" * 40, "c" * 40
CFG = Config(verify_commands=(Command(("make", "check"), "tests"),))
LIVE = Facts(lead_live=True, head=HEAD)


def passing(commit=COMMIT, cfg=CFG, kinds=("tests",), status=RunStatus.PASS, before=None, after=None):
    runs = tuple(CommandRun(("run", k), k, status, 0, AT, AT) for k in kinds)
    return Facts(
        verification=VerificationResult("run-1", commit, before or cfg.verify_hash(), after or cfg.verify_hash(), runs)
    )


def req(verb, caller, args=NoArgs(), item_id=None, row_id=None):
    return Request(verb, caller, args, AT, item_id, row_id)


def new_args(**kw):
    base = dict(goal="ship it", done_criteria=("tests pass",), role="backend", type="feature")
    return NewArgs(**{**base, **kw})


def contract(**kw):
    base = dict(
        version=0,
        goal="ship it",
        done_criteria=("tests pass",),
        role="backend",
        type="feature",
        type_inputs={},
        scope=Scope(),
        depends=(),
        repro=None,
    )
    return Contract(**{**base, **kw})


def item_in(phase, item_id="WO-0001", **kw):
    """An item built directly in `phase`; verified and blocked(limit) get the bookkeeping their invariants need."""
    if phase is Phase.VERIFIED:
        ev = Evidence(EvidenceKind.VERIFICATION, SHA, 1, "h", (), 0)
        kw.setdefault("attempts", (Attempt(1, "be-uid", None, SHA, 1, "h", ev),))
        kw.setdefault("contract", contract(version=1))
    if phase is Phase.BLOCKED_LIMIT:
        kw.setdefault("limit_seq", 1)
        kw.setdefault("failures", (Failure(FailureClass.LIMIT, "retry", (), 1, "mesh", 1),))
    return Item(item_id, phase, kw.pop("version", 1), kw.pop("contract", contract()), **kw)


def doc_of(*items, **kw):
    kw.setdefault("pm_uid", "pm-uid")  # D109: the tests' PM agent is pinned, as mesh_init apply would pin it
    return replace(empty_doc(), items={i.id: i for i in items}, next_id=len(items) + 1, **kw)


class Sim:
    """Drives the pure core the way the engine does, without a store: Doc in, Doc out."""

    def __init__(self, cfg=None, doc=None):
        self.doc, self.cfg, self.last = doc or empty_doc(), cfg or Config(), None

    def do(self, verb, caller, args=NoArgs(), item_id=None, facts=NO_FACTS, stamp=None, row_id=None):
        out = state.decide(self.doc, req(verb, caller, args, item_id, row_id), facts, self.cfg, stamp)
        if isinstance(out, Applied) and not out.noop:
            self.doc = out.doc
        self.last = out
        return out

    def ok(self, *a, **kw):
        out = self.do(*a, **kw)
        assert isinstance(out, Applied), out
        return out

    def item(self, item_id="WO-0001"):
        return self.doc.items[item_id]


class TlaParserTests(unittest.TestCase):
    def test_reads_every_outcome_of_the_model_table(self):
        triples = load_table(TLA)
        self.assertEqual(len(triples), 208)
        self.assertIn(("start", "lead (own role)", "ready", "blocked(limit)"), triples)
        self.assertIn(("ack", "pm", "blocked(children)", "blocked(children)"), triples)  # "unchanged" expanded

    def test_refuses_syntax_it_does_not_understand(self):
        text = TLA.read_text(encoding="utf-8").replace('Blocked("limit")', 'Frobnicate("limit")', 1)
        with self.assertRaises(ValueError):
            parse_table(text)


class TableShapeTests(unittest.TestCase):
    def test_table_projection_equals_the_tla_table(self):
        self.assertEqual(state.model_rows(), load_table(TLA))

    def test_27_rows_with_unique_ids_that_name_the_models_probes(self):
        ids = [r.id for r in state.TABLE]
        self.assertEqual(len(ids), 27)
        self.assertEqual(len(set(ids)), 27)
        outcome_of = {
            "start_limit": "start",
            "fail_rework": "fail",
            "fail_awaiting": "fail",
            "fail_environment": "fail",
            "unblock_limit": "unblock",
            "reject_awaiting": "reject",
        }
        probes = {outcome_of.get(p, p) for p in load_probe_names(TLA)}
        self.assertEqual(probes, set(ids))

    def test_inv2_every_non_terminal_state_has_an_exit_that_is_not_cancel_or_override(self):
        rows = state.model_rows()
        for p in NON_TERMINAL:
            exits = [t for t in rows if t[2] == p.value and t[0] not in ("cancel", "override") and t[3] != p.value]
            self.assertTrue(exits, p)

    def test_inv3_every_state_is_reachable_from_draft_without_override(self):
        rows, seen, grew = state.model_rows(), {"draft"}, True
        while grew:
            grew = False
            for verb, _caller, src, dst in rows:
                if verb != "override" and src in seen and dst not in seen:
                    seen.add(dst)
                    grew = True
        self.assertEqual(seen, {p.value for p in CREATED})

    def test_inv4_only_start_and_unblock_enter_limit_and_both_are_counted(self):
        entering = {(v, c) for v, c, src, dst in state.model_rows() if dst == Phase.BLOCKED_LIMIT.value and src != dst}
        self.assertEqual(entering, {("start", Actor.LEAD_OWN.value), ("unblock", Actor.PM.value)})
        for row in state.TABLE:
            if row.verb.value in ("start", "unblock"):
                self.assertTrue(any(isinstance(c, state.Count) for c in row.counters), row.id)


# model caller -> the table caller a request from it is matched as (an independent statement of D82 / section 4)
def expected_actor(caller, item, verb):
    if caller.kind.value == "mesh":
        return Actor.MESH
    if caller.kind.value == "pm":
        return Actor.OPERATOR if verb is Verb.OVERRIDE else Actor.PM
    return Actor.LEAD_OWN if item is not None and item.contract.role == caller.role else Actor.LEAD_OTHER


class RefusalTests(unittest.TestCase):
    """Invariant 1 (pure half): every verb x caller x state combination the table does not list is refused and changes
    nothing. The recorded half is in test_mesh_store.py."""

    def test_unlisted_combinations_are_refused_and_leave_the_document_unchanged(self):
        listed = state.model_rows()
        callers = [PM, BACKEND, FRONTEND, Caller.mesh()]
        checked = 0
        for phase in [None, *Phase]:
            doc = empty_doc() if phase is None else doc_of(item_in(phase))
            item = doc.items.get("WO-0001")
            for verb in Verb:
                for caller in callers:
                    actor = expected_actor(caller, item, verb)
                    frm = "(none)" if phase is None else phase.value
                    if (verb.value, actor.value, frm) in {(v, c, f) for v, c, f, _t in listed}:
                        continue
                    out = state.decide(doc, req(verb, caller, NoArgs(), "WO-0001"), NO_FACTS, Config(), None)
                    self.assertIsInstance(out, Refused, (verb, caller, phase))
                    expected = (
                        RefusalCode.UNKNOWN_ITEM if item is None and verb is not Verb.NEW else RefusalCode.NOT_IN_TABLE
                    )
                    self.assertEqual(out.refusal.code, expected, (verb, caller, phase))
                    checked += 1
        self.assertGreater(checked, 1000)

    def test_unknown_caller_is_refused_for_every_verb(self):
        doc = doc_of(item_in(Phase.READY))
        for verb in Verb:
            out = state.decide(doc, req(verb, UNKNOWN, NoArgs(), "WO-0001"), NO_FACTS, Config(), None)
            self.assertIsInstance(out, Refused, verb)
            self.assertEqual(out.refusal.code, RefusalCode.UNKNOWN_CALLER, verb)

    def test_lead_of_another_role_cannot_start_an_item(self):
        doc = doc_of(item_in(Phase.READY))
        out = state.decide(doc, req(Verb.START, FRONTEND, NoArgs(), "WO-0001"), NO_FACTS, Config(), None)
        self.assertEqual(out.refusal.code, RefusalCode.NOT_IN_TABLE)
        self.assertEqual(out.refusal.actor, Actor.LEAD_OTHER.value)


class NewTests(unittest.TestCase):
    def test_pm_creates_a_draft_with_the_next_id(self):
        sim = Sim()
        out = sim.ok(Verb.NEW, PM, new_args())
        self.assertEqual(out.item_id, "WO-0001")
        item = sim.item()
        self.assertEqual((item.phase, item.version, item.contract.version), (Phase.DRAFT, 1, 0))
        self.assertEqual(item.contract.role, "backend")
        self.assertEqual(sim.doc.next_id, 2)
        self.assertEqual(
            [(h.verb, h.row, h.from_phase, h.to_phase) for h in item.history], [(Verb.NEW, "new", None, Phase.DRAFT)]
        )

    def test_a_lead_cannot_create_items(self):
        out = Sim().do(Verb.NEW, BACKEND, new_args())
        self.assertEqual(out.refusal.code, RefusalCode.NOT_IN_TABLE)

    def test_new_skips_ids_taken_by_legacy_tracker_rows(self):
        legacy = LegacyRow("WO-0001", "old", ("qa",), "", (), "PASS", "pass", (), 0)
        sim = Sim(doc=replace(empty_doc(), legacy={"WO-0001": legacy}))
        self.assertEqual(sim.ok(Verb.NEW, PM, new_args()).item_id, "WO-0002")

    def test_wrong_args_type_is_refused(self):
        out = Sim().do(Verb.NEW, PM, NoArgs())
        self.assertEqual(out.refusal.code, RefusalCode.ARGS_INVALID)

    def test_counters_start_at_zero(self):
        sim = Sim()
        sim.ok(Verb.NEW, PM, new_args())
        self.assertEqual(sim.item().counters, Counters())


FAILS = Facts(repro=ReproOutcome("fails", SHA, 1))


class ReadyTests(unittest.TestCase):
    def draft(self, sim=None, **kw):
        sim = sim or Sim()
        sim.ok(Verb.NEW, PM, new_args(**kw))
        return sim

    def test_ready_completes_the_contract_and_bumps_its_version(self):
        sim = self.draft()
        sim.ok(Verb.READY, PM, ReadyArgs(), "WO-0001")
        self.assertEqual((sim.item().phase, sim.item().contract.version), (Phase.READY, 1))

    def test_revise_returns_to_draft_and_the_next_ready_bumps_again(self):
        sim = self.draft()
        sim.ok(Verb.READY, PM, ReadyArgs(), "WO-0001")
        sim.ok(Verb.REVISE, PM, ReviseArgs("scope changed"), "WO-0001")
        self.assertEqual(sim.item().phase, Phase.DRAFT)
        sim.ok(Verb.READY, PM, ReadyArgs(), "WO-0001")
        self.assertEqual(sim.item().contract.version, 2)

    def test_an_incomplete_contract_is_refused_and_a_patch_can_complete_it(self):
        for kw in (dict(goal=" "), dict(done_criteria=()), dict(type="no-such-type")):
            out = self.draft(**kw).do(Verb.READY, PM, ReadyArgs(), "WO-0001")
            self.assertEqual(out.refusal.code, RefusalCode.CONTRACT_INCOMPLETE, kw)
        sim = self.draft(goal="")
        sim.ok(Verb.READY, PM, ReadyArgs(ContractPatch(goal="now it has one")), "WO-0001")
        self.assertEqual(sim.item().contract.goal, "now it has one")

    def test_type_inputs_required_by_the_config_must_be_present(self):
        cfg = Config(types={"feature": TypeSpec(required_inputs=("design_doc",))})
        sim = Sim(cfg=cfg)
        self.draft(sim)
        self.assertEqual(sim.do(Verb.READY, PM, ReadyArgs(), "WO-0001").refusal.code, RefusalCode.CONTRACT_INCOMPLETE)
        sim.ok(Verb.READY, PM, ReadyArgs(ContractPatch(type_inputs={"design_doc": "docs/x.md"})), "WO-0001")

    def test_inv8_unknown_self_and_cyclic_depends_are_refused_at_ready(self):
        for depends in (("WO-0042",), ("WO-0001",)):
            out = self.draft(depends=depends).do(Verb.READY, PM, ReadyArgs(), "WO-0001")
            self.assertEqual(out.refusal.code, RefusalCode.DEPENDS_INVALID, depends)
        sim = self.draft(depends=("WO-0002",))  # WO-0001 -> WO-0002 is fine while it is a draft
        self.draft(sim, depends=("WO-0001",))  # WO-0002 -> WO-0001 closes the cycle
        self.assertEqual(sim.do(Verb.READY, PM, ReadyArgs(), "WO-0002").refusal.code, RefusalCode.DEPENDS_INVALID)

    def test_inv8_a_legacy_tracker_row_is_a_known_dependency(self):
        legacy = LegacyRow("WO-0007", "old", ("qa",), "", (), "PASS", "pass", (), 0)
        sim = Sim(doc=replace(empty_doc(), legacy={"WO-0007": legacy}))
        self.draft(sim, depends=("WO-0007",))
        sim.ok(Verb.READY, PM, ReadyArgs(), "WO-0001")

    def test_inv8_a_cancelled_dependency_is_refused_at_ready(self):  # D81
        dep = item_in(Phase.CANCELLED, item_id="WO-0001")
        sim = Sim(doc=doc_of(dep))
        sim.ok(Verb.NEW, PM, new_args(depends=("WO-0001",)))
        out = sim.do(Verb.READY, PM, ReadyArgs(), "WO-0002")
        self.assertEqual(out.refusal.code, RefusalCode.DEPENDS_CANCELLED)

    def test_bugfix_needs_a_repro_that_fails_at_the_base_commit(self):  # D46 / D55
        cases = {"passes": RefusalCode.REPRO_NOT_REPRODUCED, "not_runnable": RefusalCode.REPRO_NOT_RUNNABLE}
        for verdict, code in cases.items():
            sim = self.draft(type="bugfix", repro=("pytest", "-k", "bug"))
            out = sim.do(Verb.READY, PM, ReadyArgs(), "WO-0001", Facts(repro=ReproOutcome(verdict, SHA, 0)))
            self.assertEqual(out.refusal.code, code, verdict)
        sim = self.draft(type="bugfix", repro=("pytest", "-k", "bug"))
        self.assertEqual(sim.do(Verb.READY, PM, ReadyArgs(), "WO-0001").refusal.code, RefusalCode.FACT_UNAVAILABLE)
        sim.ok(Verb.READY, PM, ReadyArgs(), "WO-0001", FAILS)
        fb = sim.item().failing_before
        self.assertEqual(
            (fb.contract_version, fb.repro_hash, fb.base_commit, fb.exit_code),
            (1, repro_hash(("pytest", "-k", "bug")), SHA, 1),
        )

    def test_a_bugfix_without_a_repro_is_an_incomplete_contract(self):
        out = self.draft(type="bugfix").do(Verb.READY, PM, ReadyArgs(), "WO-0001", FAILS)
        self.assertEqual(out.refusal.code, RefusalCode.CONTRACT_INCOMPLETE)


def ready_sim(cfg=CFG, **kw):
    sim = Sim(cfg)
    sim.ok(Verb.NEW, PM, new_args(**kw))
    sim.ok(Verb.READY, PM, ReadyArgs(), "WO-0001", FAILS if kw.get("type") == "bugfix" else NO_FACTS)
    return sim


def running_sim(cfg=CFG, **kw):
    sim = ready_sim(cfg, **kw)
    sim.ok(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)
    return sim


def verifying_sim(cfg=CFG, **kw):
    sim = running_sim(cfg, **kw)
    sim.ok(Verb.SUBMIT, BACKEND, SubmitArgs(1, COMMIT), "WO-0001", Facts(commit_exists=True, commit_descends=True))
    return sim


def verify(sim, facts, attempt=1):
    return sim.do(Verb.VERIFY, BACKEND, VerifyArgs(attempt), "WO-0001", facts)


class StartTests(unittest.TestCase):
    def test_the_lead_of_the_items_role_starts_an_attempt_that_records_the_contract_and_config(self):
        sim = ready_sim()
        sim.ok(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)
        item = sim.item()
        self.assertEqual(item.phase, Phase.RUNNING)
        a = item.attempts[0]
        self.assertEqual(
            (a.id, a.lead_uid, a.base_commit, a.commit, a.contract_version, a.config_hash),
            (1, "be-uid", HEAD, None, 1, CFG.verify_hash()),
        )
        self.assertEqual(item.counters.retry, 0)  # the first attempt is not a retry

    def test_start_needs_a_live_lead_and_a_known_head(self):
        sim = ready_sim()
        cases = [
            (NO_FACTS, RefusalCode.FACT_UNAVAILABLE),
            (Facts(lead_live=False, head=HEAD), RefusalCode.LEAD_NOT_LIVE),
            (Facts(lead_live=True), RefusalCode.FACT_UNAVAILABLE),
        ]
        for facts, code in cases:
            self.assertEqual(sim.do(Verb.START, BACKEND, StartArgs(), "WO-0001", facts).refusal.code, code)
        self.assertEqual(sim.item().phase, Phase.READY)

    def test_inv8_a_dependency_is_satisfied_only_by_verified_or_closed(self):
        for phase, ok in (
            (Phase.DRAFT, False),
            (Phase.RUNNING, False),
            (Phase.REWORK, False),
            (Phase.VERIFIED, True),
            (Phase.CLOSED, True),
        ):
            dep = item_in(phase, item_id="WO-0001")
            doc = doc_of(
                dep, item_in(Phase.READY, item_id="WO-0002", contract=contract(version=1, depends=("WO-0001",)))
            )
            out = Sim(CFG, doc).do(Verb.START, BACKEND, StartArgs(), "WO-0002", LIVE)
            self.assertEqual(isinstance(out, Applied), ok, phase)
            if not ok:
                self.assertEqual(out.refusal.code, RefusalCode.DEPENDENCY_UNSATISFIED)

    def test_inv8_a_legacy_row_satisfies_a_dependency_only_when_its_status_is_done(self):
        for status, ok in (("closed", True), ("pass", True), ("open", False), ("fail", False), ("legacy", False)):
            legacy = LegacyRow("WO-0007", "old", ("qa",), "", (), status.upper(), status, (), 0)
            doc = replace(
                doc_of(item_in(Phase.READY, contract=contract(version=1, depends=("WO-0007",)))),
                legacy={"WO-0007": legacy},
            )
            out = Sim(CFG, doc).do(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)
            self.assertEqual(isinstance(out, Applied), ok, status)


SEND = DispatchSend("a" * 64)


class DispatchTests(unittest.TestCase):
    def test_dispatch_records_a_pending_send_without_text_or_paths_and_keeps_the_item_ready(self):
        sim = ready_sim()
        out = sim.ok(Verb.DISPATCH, PM, SEND, "WO-0001", Facts(lead_agent=LeadAgent("be-uid")))
        item = sim.item()
        self.assertEqual((item.phase, out.noop), (Phase.READY, False))
        action = item.pending_actions[0]
        self.assertEqual((action.action_id, action.kind.value, action.done), ("WO-0001-d1", "dispatch_send", False))
        self.assertEqual(
            dict(action.payload),
            {
                "role": "backend",
                "target_uid": "be-uid",
                "source_uid": "pm-uid",
                "text_sha256": "a" * 64,
                "message_ref": "mesh-WO-0001-d1",
            },
        )
        sim.ok(Verb.DISPATCH, PM, SEND, "WO-0001", Facts(lead_agent=LeadAgent("be-uid")))
        self.assertEqual([a.action_id for a in sim.item().pending_actions], ["WO-0001-d1", "WO-0001-d2"])

    def test_dispatch_needs_a_live_lead_of_the_role(self):
        out = ready_sim().do(Verb.DISPATCH, PM, SEND, "WO-0001", Facts(lead_agent=None))
        self.assertEqual(out.refusal.code, RefusalCode.ROLE_HAS_NO_LEAD)


class SubmitTests(unittest.TestCase):
    def test_submit_records_the_changed_commit(self):
        sim = verifying_sim()
        self.assertEqual((sim.item().phase, sim.item().attempts[0].commit), (Phase.VERIFYING, COMMIT))

    def test_submit_refuses_an_old_attempt_an_unchanged_or_unknown_commit(self):
        sim = running_sim()
        cases = [
            (SubmitArgs(0, COMMIT), Facts(commit_exists=True, commit_descends=True), RefusalCode.ATTEMPT_STALE),
            (SubmitArgs(1, HEAD), Facts(commit_exists=True, commit_descends=True), RefusalCode.COMMIT_UNCHANGED),
            (SubmitArgs(1, COMMIT), Facts(commit_exists=False), RefusalCode.COMMIT_UNKNOWN),
            (SubmitArgs(1, COMMIT), NO_FACTS, RefusalCode.FACT_UNAVAILABLE),
        ]
        for args, facts, code in cases:
            self.assertEqual(sim.do(Verb.SUBMIT, BACKEND, args, "WO-0001", facts).refusal.code, code)


class VerifyTests(unittest.TestCase):
    """Invariant 6: `verified` never exists without evidence bound to the commit, contract version and config hash."""

    def test_a_passing_run_binds_the_evidence_to_commit_contract_version_and_config_hash(self):
        sim = verifying_sim()
        sim.doc = replace(sim.doc, seq=7)
        sim.ok(Verb.VERIFY, BACKEND, VerifyArgs(1), "WO-0001", passing())
        item = sim.item()
        ev = item.attempts[-1].evidence
        self.assertEqual(item.phase, Phase.VERIFIED)
        self.assertEqual(
            (ev.kind, ev.commit, ev.contract_version, ev.config_hash, ev.recorded_seq),
            (EvidenceKind.VERIFICATION, COMMIT, 1, CFG.verify_hash(), 7),
        )
        self.assertEqual([r.status for r in ev.runs], [RunStatus.PASS])

    def test_a_run_that_does_not_pass_or_is_not_bound_is_refused_and_the_item_stays_verifying(self):
        cfg_x = Config(verify_commands=(Command(("make", "other"), "tests"),))
        types = {"feature": TypeSpec(required_evidence=("tests", "lint"))}
        cases = {
            "failed": (passing(status=RunStatus.FAIL), RefusalCode.VERIFICATION_FAILED),
            "not run": (passing(status=RunStatus.NOT_RUN), RefusalCode.VERIFICATION_FAILED),
            "timeout": (passing(status=RunStatus.TIMEOUT), RefusalCode.VERIFICATION_FAILED),
            "no commands ran": (passing(kinds=()), RefusalCode.VERIFICATION_FAILED),
            "no result": (NO_FACTS, RefusalCode.FACT_UNAVAILABLE),
            "other commit": (passing(commit=HEAD), RefusalCode.INPUTS_CHANGED),
            "config edited mid-run": (passing(after="x" * 64), RefusalCode.INPUTS_CHANGED),
            "config edited since start": (passing(cfg=cfg_x), RefusalCode.INPUTS_CHANGED),
        }
        for name, (facts, code) in cases.items():
            sim = verifying_sim()
            if name == "config edited since start":
                sim.cfg = cfg_x
            self.assertEqual(verify(sim, facts).refusal.code, code, name)
            self.assertEqual(sim.item().phase, Phase.VERIFYING, name)
        sim = verifying_sim(cfg=Config(types=types, verify_commands=CFG.verify_commands))
        self.assertEqual(verify(sim, passing(cfg=sim.cfg, kinds=("tests",))).refusal.code, RefusalCode.EVIDENCE_MISSING)
        sim.ok(Verb.VERIFY, BACKEND, VerifyArgs(1), "WO-0001", passing(cfg=sim.cfg, kinds=("tests", "lint")))

    def test_verify_by_an_old_attempt_is_refused(self):
        self.assertEqual(verify(verifying_sim(), passing(), attempt=9).refusal.code, RefusalCode.ATTEMPT_STALE)

    def test_a_bugfix_verifies_only_against_its_recorded_failing_before(self):
        sim = verifying_sim(type="bugfix", repro=("pytest", "-k", "bug"))
        sim.ok(Verb.VERIFY, BACKEND, VerifyArgs(1), "WO-0001", passing())
        sim = verifying_sim(type="bugfix", repro=("pytest", "-k", "bug"))
        item = sim.item()
        sim.doc = replace(
            sim.doc, items={item.id: replace(item, failing_before=replace(item.failing_before, repro_hash="0" * 64))}
        )
        self.assertEqual(verify(sim, passing()).refusal.code, RefusalCode.FAILING_BEFORE_MISMATCH)

    def test_inv6_the_item_type_refuses_to_exist_verified_without_bound_evidence(self):
        good = item_in(Phase.VERIFIED)
        ev = good.attempts[0].evidence
        att = good.attempts[0]
        bad = {
            "no attempt": dict(attempts=()),
            "no evidence": dict(attempts=(replace(att, evidence=None),)),
            "other commit": dict(attempts=(replace(att, evidence=replace(ev, commit=HEAD)),)),
            "other contract version": dict(attempts=(replace(att, evidence=replace(ev, contract_version=9)),)),
            "other config hash": dict(attempts=(replace(att, evidence=replace(ev, config_hash="zz")),)),
            "attempt on an old contract": dict(attempts=(replace(att, contract_version=0),)),
            "never submitted": dict(attempts=(replace(att, commit=None),)),
        }
        for name, kw in bad.items():
            with self.assertRaises(InvariantViolation, msg=name):
                replace(good, **kw)
        replace(
            good, attempts=(replace(att, evidence=replace(ev, kind=EvidenceKind.OVERRIDE, commit=None)),)
        )  # explicit override


DEAD, PRECHECK_OK = Facts(lead_live=False), Facts(precheck=PrecheckOutcome(True))


def fail(sim, cls, caller=BACKEND, facts=NO_FACTS, attempt=None, **kw):
    attempt = attempt if attempt is not None else sim.item().attempts[-1].id
    return sim.do(Verb.FAIL, caller, FailArgs(cls, attempt_id=attempt, **kw), "WO-0001", facts)


class FailTests(unittest.TestCase):
    def test_every_class_goes_where_section_5_routes_it_and_is_recorded(self):  # D65
        routes = {
            "defect": Phase.REWORK,
            "conflict": Phase.REWORK,
            "ambiguity": Phase.BLOCKED_AWAITING_USER,
            "environment": Phase.BLOCKED_ENVIRONMENT,
        }
        for cls, phase in routes.items():
            for build in (running_sim, verifying_sim):
                sim = build()
                fail(sim, cls, evidence=("log line",))
                f = sim.item().failures[-1]
                self.assertEqual(
                    (sim.item().phase, f.cls.value, f.actor, f.attempt_id, f.evidence),
                    (phase, cls, "lead", 1, ("log line",)),
                    (cls, build.__name__),
                )

    def test_an_environment_failure_asks_the_pm_to_look(self):
        sim = running_sim()
        fail(sim, "environment")
        self.assertEqual([a.kind.value for a in sim.item().pending_actions], ["pm_attention"])

    def test_inv4_no_caller_can_report_the_limit_class(self):
        sim = running_sim()
        self.assertEqual(fail(sim, "limit").refusal.code, RefusalCode.CLASS_NOT_ALLOWED)
        self.assertEqual(fail(sim, "limit", caller=PM, facts=DEAD).refusal.code, RefusalCode.CLASS_NOT_ALLOWED)
        self.assertEqual(sim.item().phase, Phase.RUNNING)

    def test_too_large_goes_with_conflict_only(self):
        sim = running_sim()
        self.assertEqual(fail(sim, "defect", reason="too_large").refusal.code, RefusalCode.CLASS_NOT_ALLOWED)
        fail(sim, "conflict", reason="too_large")
        self.assertEqual(sim.item().failures[-1].reason, "too_large")

    def test_a_lead_must_name_the_current_attempt_and_the_items_role(self):
        sim = running_sim()
        self.assertEqual(fail(sim, "defect", attempt=7).refusal.code, RefusalCode.ATTEMPT_STALE)
        self.assertEqual(fail(sim, "defect", caller=FRONTEND).refusal.code, RefusalCode.NOT_IN_TABLE)

    def test_the_pm_reports_environment_only_when_the_lead_is_absent_or_dead(self):  # D45
        sim = running_sim()
        pm_fail = lambda facts, cls="environment": sim.do(Verb.FAIL, PM, FailArgs(cls), "WO-0001", facts)  # noqa: E731
        self.assertEqual(pm_fail(Facts(lead_live=True)).refusal.code, RefusalCode.LEAD_STILL_LIVE)
        self.assertEqual(pm_fail(NO_FACTS).refusal.code, RefusalCode.FACT_UNAVAILABLE)
        self.assertEqual(pm_fail(DEAD, "defect").refusal.code, RefusalCode.CLASS_NOT_ALLOWED)
        pm_fail(DEAD)
        self.assertEqual((sim.item().phase, sim.item().failures[-1].actor), (Phase.BLOCKED_ENVIRONMENT, "pm"))


class WayBackTests(unittest.TestCase):
    def test_a_defect_comes_back_through_reassign_only(self):
        sim = running_sim()
        fail(sim, "defect")
        self.assertEqual(sim.do(Verb.READY, PM, ReadyArgs(), "WO-0001").refusal.code, RefusalCode.CLASS_ROUTE)
        before = sim.item().contract
        sim.ok(Verb.REASSIGN, PM, ReassignArgs(), "WO-0001")
        self.assertEqual((sim.item().phase, sim.item().contract), (Phase.READY, before))  # reassign does not touch it
        self.assertEqual(len(sim.item().failures), 1)  # failures[] is append-only

    def test_a_conflict_comes_back_through_ready_only(self):
        sim = running_sim()
        fail(sim, "conflict")
        self.assertEqual(sim.do(Verb.REASSIGN, PM, ReassignArgs(), "WO-0001").refusal.code, RefusalCode.CLASS_ROUTE)
        sim.ok(Verb.READY, PM, ReadyArgs(ContractPatch(scope=Scope(write_set=("w",)))), "WO-0001")  # F17
        self.assertEqual((sim.item().phase, sim.item().contract.version), (Phase.READY, 2))

    def test_an_ambiguity_needs_revised_done_criteria_to_come_back(self):
        sim = running_sim()
        fail(sim, "ambiguity")
        self.assertEqual(sim.do(Verb.READY, PM, ReadyArgs(), "WO-0001").refusal.code, RefusalCode.CONTRACT_NOT_REVISED)
        sim.ok(Verb.READY, PM, ReadyArgs(ContractPatch(done_criteria=("clearer",))), "WO-0001")
        self.assertEqual((sim.item().phase, sim.item().contract.done_criteria), (Phase.READY, ("clearer",)))

    def test_reassign_refuses_a_cancelled_dependency(self):  # D81
        rework = item_in(
            Phase.REWORK,
            item_id="WO-0002",
            failures=(Failure(FailureClass.DEFECT, None, (), 1, "lead", 0),),
            contract=contract(version=1, depends=("WO-0001",)),
        )
        sim = Sim(CFG, doc_of(item_in(Phase.CANCELLED), rework))
        self.assertEqual(
            sim.do(Verb.REASSIGN, PM, ReassignArgs(), "WO-0002").refusal.code, RefusalCode.DEPENDS_CANCELLED
        )


class CounterTests(unittest.TestCase):
    def back_to_ready(self, sim, how):
        if how == "reassign":
            fail(sim, "defect")
            sim.ok(Verb.REASSIGN, PM, ReassignArgs(), "WO-0001")
        elif how == "ready":
            fail(sim, "conflict")
            revised = Scope(write_set=(f"w{sim.item().contract.version}",))  # a new revision each time (F17)
            sim.ok(Verb.READY, PM, ReadyArgs(ContractPatch(scope=revised)), "WO-0001")
        else:
            fail(sim, "environment")
            return sim.do(Verb.UNBLOCK, PM, NoArgs(), "WO-0001", PRECHECK_OK)

    def test_inv5_the_retry_limit_holds_whichever_verb_returns_the_item_to_ready(self):
        limits = Config(retry_limit=2, environment_limit=3, verify_commands=CFG.verify_commands)
        for how in ("reassign", "ready", "unblock"):
            sim, starts = running_sim(limits), 1
            while True:
                out = self.back_to_ready(sim, how)
                if sim.item().phase is Phase.BLOCKED_LIMIT:
                    break
                out = sim.do(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)
                starts += isinstance(out, Applied) and sim.item().phase is Phase.RUNNING
                if sim.item().phase is Phase.BLOCKED_LIMIT:
                    break
            self.assertLessEqual(starts, 1 + limits.retry_limit + limits.environment_limit, how)
            self.assertEqual(sim.item().phase, Phase.BLOCKED_LIMIT, how)

    def test_a_retry_over_the_limit_enters_blocked_limit_with_a_mesh_record(self):
        sim = running_sim(Config(retry_limit=1, verify_commands=CFG.verify_commands))
        for _ in range(1):  # attempt 2 is retry 1 of 1: allowed
            fail(sim, "defect")
            sim.ok(Verb.REASSIGN, PM, ReassignArgs(), "WO-0001")
            sim.ok(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)
        fail(sim, "defect")
        sim.ok(Verb.REASSIGN, PM, ReassignArgs(), "WO-0001")
        sim.doc = replace(sim.doc, seq=4)
        out = sim.ok(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)  # attempt 3: retry 2 > 1
        item = sim.item()
        self.assertEqual((item.phase, item.limit_seq, sim.doc.seq, item.counters.retry), (Phase.BLOCKED_LIMIT, 5, 5, 2))
        self.assertEqual(
            (item.failures[-1].cls, item.failures[-1].reason, item.failures[-1].actor, item.failures[-1].seq),
            (FailureClass.LIMIT, "retry", "mesh", 5),
        )
        self.assertEqual(len(item.attempts), 2)  # the over-limit start made no attempt
        self.assertEqual(out.changes[0].to_phase, Phase.BLOCKED_LIMIT)
        self.assertEqual(item.pending_actions[-1].kind.value, "pm_attention")

    def test_the_first_start_after_an_unblock_is_not_a_retry(self):
        sim = running_sim()
        fail(sim, "environment")
        sim.ok(Verb.UNBLOCK, PM, NoArgs(), "WO-0001", PRECHECK_OK)
        self.assertEqual((sim.item().phase, sim.item().counters), (Phase.READY, Counters(0, 1, True)))
        sim.ok(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)
        self.assertEqual(sim.item().counters, Counters(0, 1, False))

    def test_a_refused_precheck_writes_nothing_and_the_fourth_passing_unblock_enters_the_limit(self):  # D68
        sim = running_sim()
        fail(sim, "environment")
        self.assertEqual(
            sim.do(Verb.UNBLOCK, PM, NoArgs(), "WO-0001", Facts(precheck=PrecheckOutcome(False))).refusal.code,
            RefusalCode.PRECHECK_REFUSED,
        )
        self.assertEqual(sim.do(Verb.UNBLOCK, PM, NoArgs(), "WO-0001").refusal.code, RefusalCode.FACT_UNAVAILABLE)
        self.assertEqual(sim.item().counters.environment_unblock, 0)
        for n in range(1, 4):
            sim.ok(Verb.UNBLOCK, PM, NoArgs(), "WO-0001", PRECHECK_OK)
            self.assertEqual((sim.item().phase, sim.item().counters.environment_unblock), (Phase.READY, n))
            sim.ok(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)
            fail(sim, "environment")
        sim.ok(Verb.UNBLOCK, PM, NoArgs(), "WO-0001", PRECHECK_OK)
        item = sim.item()
        self.assertEqual(
            (item.phase, item.failures[-1].cls, item.failures[-1].reason),
            (Phase.BLOCKED_LIMIT, FailureClass.LIMIT, "environment"),
        )

    def test_unblock_refuses_a_cancelled_dependency(self):  # D81
        env = item_in(Phase.BLOCKED_ENVIRONMENT, item_id="WO-0002", contract=contract(version=1, depends=("WO-0001",)))
        sim = Sim(CFG, doc_of(item_in(Phase.CANCELLED), env))
        self.assertEqual(
            sim.do(Verb.UNBLOCK, PM, NoArgs(), "WO-0002", PRECHECK_OK).refusal.code, RefusalCode.DEPENDS_CANCELLED
        )


class LimitResumeTests(unittest.TestCase):
    """Invariant 10: a user reply unlocks blocked(limit) at most once and only if its mesh seq is above limit_seq."""

    def limited(self, replies):
        return Sim(
            CFG,
            doc_of(
                item_in(
                    Phase.BLOCKED_LIMIT,
                    limit_seq=5,
                    replies=replies,
                    counters=Counters(3, 1, False),
                    contract=contract(version=1),
                )
            ),
        )

    def test_only_a_fresh_unconsumed_reply_unlocks_and_the_contract_must_be_revised(self):
        revised = ReadyArgs(ContractPatch(done_criteria=("narrower",)))
        old = Reply(4, "terminal", "ev-old")  # before the limit entry (seq 5): not fresh
        used = Reply(9, "terminal", "ev-used", consumed=True)
        fresh = Reply(8, "terminal", "ev-new")
        sim = self.limited((old, used))
        self.assertEqual(sim.do(Verb.READY, PM, revised, "WO-0001").refusal.code, RefusalCode.NO_FRESH_REPLY)
        sim = self.limited((old, used, fresh))
        self.assertEqual(sim.do(Verb.READY, PM, ReadyArgs(), "WO-0001").refusal.code, RefusalCode.CONTRACT_NOT_REVISED)
        sim.ok(Verb.READY, PM, revised, "WO-0001")
        item = sim.item()
        self.assertEqual((item.phase, item.contract.version, item.counters), (Phase.READY, 2, Counters(0, 0, True)))
        self.assertEqual(
            [(r.ref, r.consumed) for r in item.replies], [("ev-old", False), ("ev-used", True), ("ev-new", True)]
        )

    def test_the_resumed_item_cannot_be_resumed_again_by_the_same_reply(self):
        sim = self.limited((Reply(8, "terminal", "ev-new"),))
        sim.ok(Verb.READY, PM, ReadyArgs(ContractPatch(done_criteria=("narrower",))), "WO-0001")
        limited_again = replace(sim.item(), phase=Phase.BLOCKED_LIMIT, limit_seq=20)  # a later limit entry
        sim.doc = replace(sim.doc, items={"WO-0001": limited_again})
        out = sim.do(Verb.READY, PM, ReadyArgs(ContractPatch(done_criteria=("again",))), "WO-0001")
        self.assertEqual(out.refusal.code, RefusalCode.NO_FRESH_REPLY)


def event_facts(n=3, found=True, text="the operator typed this"):
    event = OperatorEvent(f"ev-{n:06d}", n, AT, text, "pm-uid") if found else None
    return Facts(operator_event=EventFact(event))


def verified_sim(cfg=CFG, **kw):
    sim = verifying_sim(cfg, **kw)
    sim.ok(Verb.VERIFY, BACKEND, VerifyArgs(1), "WO-0001", passing(cfg=cfg))
    return sim


class ReplyTests(unittest.TestCase):
    def test_a_hook_recorded_event_becomes_a_reply_and_never_releases_a_block(self):
        sim = Sim(CFG, doc_of(item_in(Phase.BLOCKED_LIMIT)))
        sim.ok(Verb.REPLY, PM, ReplyArgs("ev-000003"), "WO-0001", event_facts(3))
        item = sim.item()
        self.assertEqual(item.phase, Phase.BLOCKED_LIMIT)
        self.assertEqual(item.replies, (Reply(3, "terminal", "ev-000003"),))
        self.assertEqual(
            (sim.doc.event_uses["ev-000003"].kind, sim.doc.event_uses["ev-000003"].ref), ("reply", "WO-0001")
        )

    def test_an_unknown_missing_or_reused_event_is_refused(self):
        sim = Sim(CFG, doc_of(item_in(Phase.READY)))
        self.assertEqual(
            sim.do(Verb.REPLY, PM, ReplyArgs("ev-000003"), "WO-0001", event_facts(found=False)).refusal.code,
            RefusalCode.EVENT_UNKNOWN,
        )
        self.assertEqual(
            sim.do(Verb.REPLY, PM, ReplyArgs("ev-000003"), "WO-0001").refusal.code, RefusalCode.FACT_UNAVAILABLE
        )
        sim.ok(Verb.REPLY, PM, ReplyArgs("ev-000003"), "WO-0001", event_facts(3))
        self.assertEqual(
            sim.do(Verb.REPLY, PM, ReplyArgs("ev-000003"), "WO-0001", event_facts(3)).refusal.code,
            RefusalCode.EVENT_REUSED,
        )

    def test_a_lead_cannot_attach_replies_and_finished_items_take_none(self):
        sim = Sim(CFG, doc_of(item_in(Phase.READY), item_in(Phase.CLOSED, item_id="WO-0002")))
        self.assertEqual(
            sim.do(Verb.REPLY, BACKEND, ReplyArgs("ev-000003"), "WO-0001", event_facts(3)).refusal.code,
            RefusalCode.NOT_IN_TABLE,
        )
        self.assertEqual(
            sim.do(Verb.REPLY, PM, ReplyArgs("ev-000003"), "WO-0002", event_facts(3)).refusal.code,
            RefusalCode.NOT_IN_TABLE,
        )


class CloseTests(unittest.TestCase):
    def reply_on(self, sim, n=3):
        sim.ok(Verb.REPLY, PM, ReplyArgs(f"ev-{n:06d}"), "WO-0001", event_facts(n))

    def test_inv7_close_waits_for_every_reply_to_be_acknowledged(self):
        sim = verified_sim()
        self.reply_on(sim)
        self.assertEqual(sim.do(Verb.CLOSE, PM, NoArgs(), "WO-0001").refusal.code, RefusalCode.UNACKED_REPLIES)
        sim.ok(Verb.ACK, PM, NoArgs(), "WO-0001")
        self.assertTrue(all(r.acknowledged for r in sim.item().replies))
        sim.ok(Verb.CLOSE, PM, NoArgs(), "WO-0001")
        self.assertEqual(sim.item().phase, Phase.CLOSED)

    def test_ack_needs_something_to_acknowledge(self):
        self.assertEqual(verified_sim().do(Verb.ACK, PM, NoArgs(), "WO-0001").refusal.code, RefusalCode.NOTHING_TO_ACK)

    def test_inv7_close_refuses_a_revalidate_flag(self):
        sim = verified_sim()
        sim.ok(Verb.REVALIDATE, PM, NoArgs(), "WO-0001")
        self.assertTrue(sim.item().revalidate)
        self.assertEqual(sim.do(Verb.CLOSE, PM, NoArgs(), "WO-0001").refusal.code, RefusalCode.REVALIDATE_FLAG)

    def test_only_the_pm_closes_and_only_a_verified_item(self):
        sim = verified_sim()
        self.assertEqual(sim.do(Verb.CLOSE, BACKEND, NoArgs(), "WO-0001").refusal.code, RefusalCode.NOT_IN_TABLE)
        self.assertEqual(running_sim().do(Verb.CLOSE, PM, NoArgs(), "WO-0001").refusal.code, RefusalCode.NOT_IN_TABLE)


class RejectTests(unittest.TestCase):
    def test_reject_needs_a_trigger_a_reply_or_a_revalidate_flag(self):
        sim = verified_sim()
        self.assertEqual(
            sim.do(Verb.REJECT, PM, RejectArgs("defect"), "WO-0001").refusal.code, RefusalCode.REJECT_NO_TRIGGER
        )
        sim.ok(Verb.REVALIDATE, PM, NoArgs(), "WO-0001")
        self.assertEqual(
            sim.do(Verb.REJECT, PM, RejectArgs("conflict"), "WO-0001").refusal.code, RefusalCode.REJECT_NO_TRIGGER
        )  # a flag alone justifies a defect only
        sim.ok(Verb.REJECT, PM, RejectArgs("defect", "drifted"), "WO-0001")
        self.assertEqual((sim.item().phase, sim.item().failures[-1].evidence), (Phase.REWORK, ("drifted",)))

    def test_every_class_a_reply_justifies_goes_where_section_5_routes_it(self):  # D65
        for cls, phase in (
            ("defect", Phase.REWORK),
            ("conflict", Phase.REWORK),
            ("ambiguity", Phase.BLOCKED_AWAITING_USER),
        ):
            sim = verified_sim()
            sim.ok(Verb.REPLY, PM, ReplyArgs("ev-000003"), "WO-0001", event_facts(3))
            sim.ok(Verb.REJECT, PM, RejectArgs(cls), "WO-0001")
            self.assertEqual(
                (sim.item().phase, sim.item().failures[-1].cls.value, sim.item().failures[-1].actor),
                (phase, cls, "pm"),
                cls,
            )

    def test_reject_cannot_name_the_limit_class(self):
        sim = verified_sim()
        sim.ok(Verb.REPLY, PM, ReplyArgs("ev-000003"), "WO-0001", event_facts(3))
        self.assertEqual(
            sim.do(Verb.REJECT, PM, RejectArgs("limit"), "WO-0001").refusal.code, RefusalCode.CLASS_NOT_ALLOWED
        )


class RevalidateTests(unittest.TestCase):
    def test_inv15_a_passing_verification_leaves_no_flag_from_before_it(self):  # D69
        sim = verified_sim()
        sim.ok(Verb.REVALIDATE, PM, NoArgs(), "WO-0001")
        sim.ok(Verb.REJECT, PM, RejectArgs("defect"), "WO-0001")
        sim.ok(Verb.REASSIGN, PM, ReassignArgs(), "WO-0001")
        sim.ok(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)
        sim.ok(
            Verb.SUBMIT, BACKEND, SubmitArgs(2, "d" * 40), "WO-0001", Facts(commit_exists=True, commit_descends=True)
        )
        self.assertTrue(sim.item().revalidate)
        sim.ok(Verb.VERIFY, BACKEND, VerifyArgs(2), "WO-0001", passing(commit="d" * 40))
        self.assertEqual((sim.item().phase, sim.item().revalidate), (Phase.VERIFIED, False))
        sim.ok(Verb.CLOSE, PM, NoArgs(), "WO-0001")

    def test_revalidating_twice_changes_nothing_the_second_time(self):
        sim = verified_sim()
        sim.ok(Verb.REVALIDATE, PM, NoArgs(), "WO-0001")
        version = sim.item().version
        out = sim.ok(Verb.REVALIDATE, PM, NoArgs(), "WO-0001")
        self.assertEqual((out.noop, sim.item().version), (True, version))

    def test_a_flag_on_a_closed_item_creates_exactly_one_follow_up_draft(self):  # D70
        sim = verified_sim()
        sim.ok(Verb.CLOSE, PM, NoArgs(), "WO-0001")
        self.assertEqual(len(sim.doc.items), 1)
        sim.ok(Verb.REVALIDATE, PM, NoArgs(), "WO-0001")
        follow = sim.item("WO-0002")
        self.assertEqual(
            (follow.phase, follow.links.follows, follow.contract.goal, follow.contract.version),
            (Phase.DRAFT, "WO-0001", "ship it", 0),
        )
        self.assertEqual(sim.item().phase, Phase.CLOSED)
        sim.ok(Verb.REVALIDATE, PM, NoArgs(), "WO-0001")
        self.assertEqual(len(sim.doc.items), 2)


MERGED = "e" * 40


def two_children():
    return SplitArgs(
        (ChildDraft("part one", ("a",), "backend", "feature"), ChildDraft("part two", ("b",), "backend", "feature"))
    )


def split_sim(cfg=CFG):
    """A parent that failed as too_large and was split in two: WO-0001 blocked(children), WO-0002 and WO-0003 drafts."""
    sim = running_sim(cfg)
    fail(sim, "conflict", reason="too_large")
    sim.ok(Verb.SPLIT, PM, two_children(), "WO-0001")
    return sim


def close_children(sim):
    for cid in ("WO-0002", "WO-0003"):
        sim.doc = replace(sim.doc, items={**sim.doc.items, cid: replace(sim.item(cid), phase=Phase.CLOSED)})


class CancelTests(unittest.TestCase):
    def test_cancel_records_the_reason_and_is_idempotent(self):
        sim = running_sim()
        sim.ok(Verb.CANCEL, PM, CancelArgs("not needed"), "WO-0001")
        self.assertEqual((sim.item().phase, sim.item().history[-1].note), (Phase.CANCELLED, "not needed"))
        out = sim.ok(Verb.CANCEL, PM, CancelArgs("again"), "WO-0001")
        self.assertTrue(out.noop)

    def test_a_closed_item_cannot_be_cancelled(self):
        out = Sim(CFG, doc_of(item_in(Phase.CLOSED))).do(Verb.CANCEL, PM, CancelArgs("x"), "WO-0001")
        self.assertEqual(out.refusal.code, RefusalCode.NOT_IN_TABLE)

    def test_inv14_cancelling_an_item_moves_its_ready_dependents_to_awaiting_user_in_the_same_write(self):  # D71
        dep = item_in(Phase.READY, item_id="WO-0002", contract=contract(version=1, depends=("WO-0001",)))
        sim = Sim(
            CFG,
            doc_of(
                item_in(Phase.RUNNING),
                dep,
                item_in(Phase.DRAFT, item_id="WO-0003", contract=contract(depends=("WO-0001",))),
            ),
        )
        out = sim.ok(Verb.CANCEL, PM, CancelArgs("dropped"), "WO-0001")
        self.assertEqual(
            (sim.item("WO-0002").phase, sim.item("WO-0003").phase), (Phase.BLOCKED_AWAITING_USER, Phase.DRAFT)
        )
        self.assertEqual(
            [(c.item_id, c.to_phase) for c in out.changes],
            [("WO-0001", Phase.CANCELLED), ("WO-0002", Phase.BLOCKED_AWAITING_USER)],
        )
        self.assertEqual(sim.item("WO-0002").history[-1].row, "mesh_dependency")

    def test_inv13_cancelling_a_split_parent_cancels_its_active_children_but_not_closed_ones(self):  # D66
        sim = split_sim()
        sim.doc = replace(sim.doc, items={**sim.doc.items, "WO-0003": replace(sim.item("WO-0003"), phase=Phase.CLOSED)})
        sim.ok(Verb.CANCEL, PM, CancelArgs("abandon"), "WO-0001")
        self.assertEqual(
            [sim.item(i).phase for i in ("WO-0001", "WO-0002", "WO-0003")],
            [Phase.CANCELLED, Phase.CANCELLED, Phase.CLOSED],
        )

    def test_inv13_cancelling_a_child_sends_the_parent_back_to_rework_and_cancels_its_siblings(self):  # D66
        sim = split_sim()
        out = sim.ok(Verb.CANCEL, PM, CancelArgs("child is wrong"), "WO-0002")
        self.assertEqual(
            [sim.item(i).phase for i in ("WO-0001", "WO-0002", "WO-0003")],
            [Phase.REWORK, Phase.CANCELLED, Phase.CANCELLED],
        )
        self.assertEqual(sim.item("WO-0001").history[-1].row, "mesh_rework")
        self.assertEqual(out.changes[0].item_id, "WO-0002")


class SplitTests(unittest.TestCase):
    def test_a_too_large_conflict_splits_into_linked_child_drafts_and_keeps_failures(self):  # D75
        sim = split_sim()
        parent = sim.item()
        self.assertEqual((parent.phase, parent.links.children), (Phase.BLOCKED_CHILDREN, ("WO-0002", "WO-0003")))
        self.assertEqual(len(parent.failures), 1)
        child = sim.item("WO-0002")
        self.assertEqual((child.phase, child.links.parent, child.contract.goal), (Phase.DRAFT, "WO-0001", "part one"))

    def test_a_split_needs_two_children_and_a_too_large_conflict(self):
        sim = running_sim()
        fail(sim, "defect")
        self.assertEqual(sim.do(Verb.SPLIT, PM, two_children(), "WO-0001").refusal.code, RefusalCode.CLASS_ROUTE)
        sim = running_sim()
        fail(sim, "conflict", reason="too_large")
        one = SplitArgs(two_children().children[:1])
        self.assertEqual(sim.do(Verb.SPLIT, PM, one, "WO-0001").refusal.code, RefusalCode.SPLIT_TOO_FEW_CHILDREN)

    def test_a_re_split_unlinks_cancelled_children_to_former_children(self):  # D67
        sim = split_sim()
        sim.ok(Verb.CANCEL, PM, CancelArgs("child is wrong"), "WO-0002")
        sim.ok(Verb.SPLIT, PM, two_children(), "WO-0001")
        parent = sim.item()
        self.assertEqual(
            (parent.phase, parent.links.children, parent.links.former_children),
            (Phase.BLOCKED_CHILDREN, ("WO-0004", "WO-0005"), ("WO-0002", "WO-0003")),
        )


class MeshRowTests(unittest.TestCase):
    """Invariant 9: a split parent closes only through its own verification and the normal close gates."""

    def mesh(self, sim, row_id, facts=NO_FACTS, caller=Caller.mesh()):
        return sim.do(Verb.MESH, caller, NoArgs(), "WO-0001", facts, row_id=row_id)

    def test_nothing_is_due_until_every_child_is_closed_and_then_the_merged_commit_is_needed(self):
        sim = split_sim()
        self.assertEqual(state.mesh_steps_due(sim.doc), ())
        close_children(sim)
        self.assertEqual(
            [(d.row.id, d.item_id) for d in state.mesh_steps_due(sim.doc)], [("mesh_verifying", "WO-0001")]
        )
        self.assertEqual(self.mesh(sim, "mesh_verifying").refusal.code, RefusalCode.FACT_UNAVAILABLE)
        self.mesh_to_verified(sim)

    def mesh_to_verified(self, sim):
        self.mesh(sim, "mesh_verifying", Facts(merged_commit=MERGED))
        a = sim.item().current_attempt
        self.assertEqual(
            (sim.item().phase, a.lead_uid, a.commit, a.contract_version), (Phase.VERIFYING, None, MERGED, 1)
        )
        self.assertEqual([d.row.id for d in state.mesh_steps_due(sim.doc)], ["mesh_verified", "mesh_verify_fail"])
        self.assertEqual(
            self.mesh(sim, "mesh_verify_fail", passing(commit=MERGED)).refusal.code, RefusalCode.VERIFICATION_PASSED
        )
        sim.ok(Verb.MESH, Caller.mesh(), NoArgs(), "WO-0001", passing(commit=MERGED), row_id="mesh_verified")
        ev = sim.item().current_attempt.evidence
        self.assertEqual((sim.item().phase, ev.kind, ev.commit), (Phase.VERIFIED, EvidenceKind.MERGED, MERGED))
        sim.ok(Verb.CLOSE, PM, NoArgs(), "WO-0001")
        self.assertEqual(sim.item().phase, Phase.CLOSED)

    def test_the_parents_own_failing_run_sends_it_back_to_rework_as_a_defect(self):  # D72
        sim = split_sim()
        close_children(sim)
        self.mesh(sim, "mesh_verifying", Facts(merged_commit=MERGED))
        failing = passing(commit=MERGED, status=RunStatus.FAIL)
        self.assertEqual(self.mesh(sim, "mesh_verified", failing).refusal.code, RefusalCode.VERIFICATION_FAILED)
        self.assertEqual(self.mesh(sim, "mesh_verify_fail").refusal.code, RefusalCode.FACT_UNAVAILABLE)
        sim.ok(Verb.MESH, Caller.mesh(), NoArgs(), "WO-0001", failing, row_id="mesh_verify_fail")
        f = sim.item().failures[-1]
        self.assertEqual((sim.item().phase, f.cls, f.actor), (Phase.REWORK, FailureClass.DEFECT, "mesh"))

    def test_only_the_engines_mesh_caller_runs_a_mesh_row_and_only_when_its_trigger_holds(self):
        sim = split_sim()
        for caller in (PM, BACKEND):
            self.assertEqual(
                self.mesh(sim, "mesh_verifying", Facts(merged_commit=MERGED), caller).refusal.code,
                RefusalCode.NOT_IN_TABLE,
            )
        self.assertEqual(
            self.mesh(sim, "mesh_verifying", Facts(merged_commit=MERGED)).refusal.code, RefusalCode.NOT_IN_TABLE
        )
        self.assertEqual(self.mesh(sim, "no_such_row").refusal.code, RefusalCode.NOT_IN_TABLE)


def override(sim, to, facts=None, item_id="WO-0001", caller=PM, reason="operator said so"):
    if sim.doc.last_operator_seq < 3:  # the PM-pane hook recorded ev-000003 as the operator's newest prompt
        sim.doc = replace(sim.doc, last_operator_seq=3)
    return sim.do(
        Verb.OVERRIDE,
        caller,
        OverrideArgs(to.value if isinstance(to, Phase) else to, reason, "ev-000003"),
        item_id,
        facts or event_facts(3, text=f"the operator typed this about {item_id}"),  # D98: names the item
    )


class OverrideTests(unittest.TestCase):
    def test_the_pm_citing_a_fresh_operator_event_overrides_and_the_use_is_recorded(self):
        sim = running_sim()
        out = override(sim, Phase.CANCELLED)
        self.assertEqual(sim.item().phase, Phase.CANCELLED)
        h = sim.item().history[-1]
        self.assertEqual(
            (h.verb, h.actor, h.actor_uid, h.note), (Verb.OVERRIDE, Actor.OPERATOR, "pm-uid", "operator said so")
        )
        self.assertEqual((sim.doc.event_uses["ev-000003"].kind, out.row_id), ("override", "override"))

    def test_d73_an_override_never_enters_blocked_limit_or_stays_where_it_is(self):
        sim = running_sim()
        for target in ("blocked(limit)", "running", "no-such-phase"):
            self.assertEqual(override(sim, target).refusal.code, RefusalCode.OVERRIDE_TARGET, target)
        self.assertEqual(sim.item().phase, Phase.RUNNING)

    def test_an_override_needs_an_unknown_free_unused_and_latest_event(self):
        sim = running_sim()
        self.assertEqual(
            override(sim, Phase.CANCELLED, event_facts(found=False)).refusal.code, RefusalCode.EVENT_UNKNOWN
        )
        sim.doc = replace(sim.doc, last_operator_seq=4)  # a newer prompt was recorded after ev-000003
        self.assertEqual(override(sim, Phase.CANCELLED, event_facts(3)).refusal.code, RefusalCode.EVENT_STALE)
        self.assertEqual(sim.item().phase, Phase.RUNNING)
        sim.doc = replace(sim.doc, last_operator_seq=3)
        self.assertEqual(override(sim, Phase.CANCELLED, Facts()).refusal.code, RefusalCode.FACT_UNAVAILABLE)
        sim.ok(Verb.REPLY, PM, ReplyArgs("ev-000003"), "WO-0001", event_facts(3))
        self.assertEqual(override(sim, Phase.CANCELLED).refusal.code, RefusalCode.EVENT_REUSED)

    def test_freshness_follows_the_last_operator_seq_not_the_mesh_seq(self):  # D74: other writes bump seq too
        sim = running_sim()
        sim.doc = replace(sim.doc, seq=sim.doc.seq + 10, last_operator_seq=3)  # e.g. a limit entry took a seq
        self.assertIsInstance(override(sim, Phase.CANCELLED), Applied)

    def test_only_the_pm_pane_can_override_never_a_lead_or_an_unknown_caller(self):
        sim = running_sim()
        self.assertEqual(override(sim, Phase.CANCELLED, caller=BACKEND).refusal.code, RefusalCode.NOT_IN_TABLE)
        self.assertEqual(override(sim, Phase.CANCELLED, caller=UNKNOWN).refusal.code, RefusalCode.UNKNOWN_CALLER)

    def test_an_override_into_verified_or_closed_leaves_explicit_override_evidence(self):
        for to in (Phase.VERIFIED, Phase.CLOSED):
            sim = running_sim()
            override(sim, to)
            a = sim.item().current_attempt
            self.assertEqual((sim.item().phase, a.evidence.kind, a.id), (to, EvidenceKind.OVERRIDE, 2), to)

    def test_an_override_into_verified_does_not_reuse_evidence_bound_to_an_older_contract(self):  # walk finding
        sim = verified_sim()
        sim.ok(Verb.REPLY, PM, ReplyArgs("ev-000004"), "WO-0001", event_facts(4))
        sim.ok(Verb.REJECT, PM, RejectArgs("conflict"), "WO-0001")
        sim.ok(Verb.READY, PM, ReadyArgs(ContractPatch(scope=Scope(write_set=("w",)))), "WO-0001")  # contract v2
        override(sim, Phase.VERIFIED)
        item = sim.item()
        self.assertEqual(
            (item.phase, item.current_attempt.evidence.kind, item.current_attempt.id),
            (Phase.VERIFIED, EvidenceKind.OVERRIDE, 2),
        )

    def test_an_override_into_closed_still_refuses_unacknowledged_replies_and_a_flag(self):
        sim = verified_sim()
        sim.ok(Verb.REPLY, PM, ReplyArgs("ev-000004"), "WO-0001", event_facts(4))
        self.assertEqual(override(sim, Phase.CLOSED).refusal.code, RefusalCode.UNACKED_REPLIES)
        sim.ok(Verb.ACK, PM, NoArgs(), "WO-0001")
        sim.ok(Verb.REVALIDATE, PM, NoArgs(), "WO-0001")
        self.assertEqual(override(sim, Phase.CLOSED).refusal.code, RefusalCode.REVALIDATE_FLAG)

    def test_an_override_cannot_smuggle_a_malformed_dependency_past_draft(self):
        sim = Sim(CFG, doc_of(item_in(Phase.DRAFT, contract=contract(depends=("WO-0042",)))))
        self.assertEqual(override(sim, Phase.READY).refusal.code, RefusalCode.DEPENDS_INVALID)

    def test_an_override_to_ready_with_a_cancelled_dependency_lands_in_awaiting_user(self):  # inv 14
        dep = item_in(Phase.DRAFT, item_id="WO-0002", contract=contract(depends=("WO-0001",)))
        sim = Sim(CFG, doc_of(item_in(Phase.CANCELLED), dep))
        override(sim, Phase.READY, item_id="WO-0002")
        self.assertEqual(sim.item("WO-0002").phase, Phase.BLOCKED_AWAITING_USER)

    def test_an_override_of_a_split_parent_out_of_its_children_cancels_them(self):  # inv 13
        for to in (Phase.REWORK, Phase.CANCELLED, Phase.VERIFIED, Phase.READY):
            sim = split_sim()
            override(sim, to)
            self.assertEqual([sim.item(i).phase for i in ("WO-0002", "WO-0003")], [Phase.CANCELLED] * 2, to)

    def test_an_override_cannot_revive_a_child_under_a_parent_that_is_not_waiting_for_it(self):  # walk finding 3
        sim = split_sim()
        sim.ok(Verb.CANCEL, PM, CancelArgs("child is wrong"), "WO-0002")  # parent -> rework, children cancelled
        sim.ok(Verb.READY, PM, ReadyArgs(ContractPatch(scope=Scope(write_set=("w",)))), "WO-0001")  # F17: conflict
        self.assertEqual(sim.item().phase, Phase.READY)
        self.assertEqual(override(sim, Phase.READY, item_id="WO-0002").refusal.code, RefusalCode.OVERRIDE_TARGET)

    def test_an_override_cannot_revive_a_child_under_a_parent_that_already_left_blocked_children(
        self,
    ):  # inv 13 (walk finding)
        sim = split_sim()
        sim.ok(Verb.CANCEL, PM, CancelArgs("child is wrong"), "WO-0002")  # parent -> rework, both children cancelled
        self.assertEqual(sim.item().phase, Phase.REWORK)
        self.assertEqual(override(sim, Phase.READY, item_id="WO-0002").refusal.code, RefusalCode.OVERRIDE_TARGET)
        override(sim, Phase.CLOSED, item_id="WO-0002")  # finishing it is harmless
        self.assertEqual(sim.item("WO-0002").phase, Phase.CLOSED)

    def test_an_override_into_rework_gives_it_a_way_back(self):
        sim = running_sim()
        override(sim, Phase.REWORK)
        sim.ok(Verb.REASSIGN, PM, ReassignArgs(), "WO-0001")
        self.assertEqual(sim.item().phase, Phase.READY)


class CasTests(unittest.TestCase):
    """Invariant 11: a stale result (an attempt or version mismatch) is never written. Section 8, D76."""

    def stamps(self, sim, verb, caller, args):
        plan = state.plan(sim.doc, req(verb, caller, args, "WO-0001"))
        return plan.stamp

    def test_plan_stamps_verify_by_attempt_fields_and_the_other_fact_rows_by_version(self):
        sim = verifying_sim()
        verify_stamp = self.stamps(sim, Verb.VERIFY, BACKEND, VerifyArgs(1))
        self.assertEqual((verify_stamp.kind.value, verify_stamp.value), ("attempt", ("verifying", 1, 1, COMMIT)))
        self.assertEqual(self.stamps(sim, Verb.FAIL, PM, FailArgs("environment")).kind.value, "version")
        self.assertIsNone(self.stamps(sim, Verb.FAIL, BACKEND, FailArgs("defect", attempt_id=1)))  # no unlocked phase

    def test_a_reply_written_during_an_unlocked_verify_does_not_discard_it(self):  # D76
        sim = verifying_sim()
        stamp = self.stamps(sim, Verb.VERIFY, BACKEND, VerifyArgs(1))
        sim.ok(Verb.REPLY, PM, ReplyArgs("ev-000003"), "WO-0001", event_facts(3))  # version +1
        out = sim.do(Verb.VERIFY, BACKEND, VerifyArgs(1), "WO-0001", passing(), stamp)
        self.assertIsInstance(out, Applied, out)

    def test_a_newer_attempt_discards_the_verify_result_and_writes_nothing(self):
        sim = verifying_sim()
        stamp = self.stamps(sim, Verb.VERIFY, BACKEND, VerifyArgs(1))
        moved = sim.item()
        moved = replace(moved, attempts=(replace(moved.attempts[0], id=2),), version=moved.version + 3)
        sim.doc = replace(sim.doc, items={"WO-0001": moved})
        out = sim.do(Verb.VERIFY, BACKEND, VerifyArgs(1), "WO-0001", passing(), stamp)
        self.assertEqual(out.refusal.code, RefusalCode.STALE)
        self.assertEqual(sim.item(), moved)

    def test_a_version_stamped_row_is_stale_after_any_write_to_the_item(self):
        sim = ready_sim()
        stamp = self.stamps(sim, Verb.START, BACKEND, StartArgs())
        sim.ok(Verb.REVISE, PM, ReviseArgs("r"), "WO-0001")
        sim.ok(Verb.READY, PM, ReadyArgs(), "WO-0001")
        out = sim.do(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE, stamp)
        self.assertEqual(out.refusal.code, RefusalCode.STALE)
        self.assertEqual(sim.item().phase, Phase.READY)


def failing_record(actor="mesh"):
    return Failure(FailureClass.LIMIT, "retry", (), 1, actor, 1)


def violations(*items, **kw):
    return {(v.invariant, v.item_id) for v in state.check_invariants(doc_of(*items, **kw))}


class InvariantCheckTests(unittest.TestCase):
    """`check_invariants` is the doc-level half of section 10: it runs as a post-condition of every write."""

    def test_a_well_formed_document_has_no_violations(self):
        self.assertEqual(
            state.check_invariants(doc_of(item_in(Phase.READY), item_in(Phase.VERIFIED, item_id="WO-0002"))), ()
        )

    def test_inv4_blocked_limit_needs_a_mesh_written_limit_record(self):
        self.assertEqual(violations(item_in(Phase.BLOCKED_LIMIT, failures=())), {(4, "WO-0001")})
        self.assertEqual(violations(item_in(Phase.BLOCKED_LIMIT, failures=(failing_record("pm"),))), {(4, "WO-0001")})
        self.assertEqual(violations(item_in(Phase.BLOCKED_LIMIT, failures=(failing_record(),))), set())
        self.assertEqual(violations(item_in(Phase.REWORK, failures=(failing_record("lead"),))), {(4, "WO-0001")})

    def test_inv7_a_closed_item_has_no_unacknowledged_reply(self):
        self.assertEqual(violations(item_in(Phase.CLOSED, replies=(Reply(1, "terminal", "ev-1"),))), {(7, "WO-0001")})
        self.assertEqual(violations(item_in(Phase.CLOSED, replies=(Reply(1, "terminal", "ev-1", True),))), set())

    def test_inv8_depends_past_draft_resolve_and_are_acyclic(self):
        def with_depends(phase, *deps, item_id="WO-0001"):
            return item_in(phase, item_id=item_id, contract=contract(depends=deps))

        self.assertEqual(violations(with_depends(Phase.READY, "WO-0042")), {(8, "WO-0001")})
        self.assertEqual(violations(with_depends(Phase.READY, "WO-0001")), {(8, "WO-0001")})
        self.assertEqual(violations(with_depends(Phase.DRAFT, "WO-0042")), set())  # a draft may still be wrong
        cyc = (with_depends(Phase.READY, "WO-0002"), with_depends(Phase.READY, "WO-0001", item_id="WO-0002"))
        self.assertEqual(violations(*cyc), {(8, "WO-0001"), (8, "WO-0002")})

    def test_inv9_a_closed_split_parent_carries_its_own_evidence(self):
        links = Links(children=("WO-0002",))
        child = item_in(Phase.CLOSED, item_id="WO-0002", links=Links(parent="WO-0001"))
        own = Evidence(EvidenceKind.MERGED, SHA, 1, "h", (), 0)
        merged = (Attempt(2, None, None, SHA, 1, "h", own),)
        self.assertEqual(violations(item_in(Phase.CLOSED, links=links), child), {(9, "WO-0001")})
        self.assertEqual(violations(item_in(Phase.CLOSED, links=links, attempts=merged), child), set())
        lead_run = item_in(Phase.VERIFIED).attempts  # a re-assigned parent verified by a lead
        self.assertEqual(violations(item_in(Phase.CLOSED, links=links, attempts=lead_run), child), set())

    def test_inv13_no_active_child_under_a_parent_in_rework_or_cancelled(self):
        for phase in (Phase.REWORK, Phase.CANCELLED):
            parent = item_in(phase, links=Links(children=("WO-0002",)))
            self.assertEqual(
                violations(parent, item_in(Phase.DRAFT, item_id="WO-0002", links=Links(parent="WO-0001"))),
                {(13, "WO-0001")},
                phase,
            )
            self.assertEqual(
                violations(parent, item_in(Phase.CLOSED, item_id="WO-0002", links=Links(parent="WO-0001"))),
                set(),
                phase,
            )

    def test_inv14_a_ready_item_never_depends_on_a_cancelled_item(self):
        ready = item_in(Phase.READY, item_id="WO-0002", contract=contract(version=1, depends=("WO-0001",)))
        self.assertEqual(violations(item_in(Phase.CANCELLED), ready), {(14, "WO-0002")})

    def test_a_failed_post_condition_raises_and_nothing_is_returned(self):
        sim = Sim(CFG, doc_of(item_in(Phase.RUNNING, replies=(Reply(1, "terminal", "ev-1"),))))

        def corrupt(c):  # an effect that closes with an unacknowledged reply
            return replace(c.doc, items={c.item.id: replace(c.item, phase=Phase.CLOSED)})

        with mock.patch.dict(state.EFFECTS, {"cancel": corrupt}), self.assertRaises(InvariantViolation):
            sim.do(Verb.CANCEL, PM, CancelArgs("x"), "WO-0001")

    def test_an_effect_that_lands_outside_the_rows_destinations_or_touches_strangers_raises(self):
        sim = Sim(CFG, doc_of(item_in(Phase.RUNNING), item_in(Phase.READY, item_id="WO-0002")))

        def wrong_phase(c):
            return replace(c.doc, items={**c.doc.items, c.item.id: replace(c.item, phase=Phase.READY)})

        def stranger(c):
            other = replace(c.doc.items["WO-0002"], phase=Phase.DRAFT)
            return replace(c.doc, items={**c.doc.items, "WO-0002": other})

        for bad in (wrong_phase, stranger):
            with mock.patch.dict(state.EFFECTS, {"cancel": bad}), self.assertRaises(InvariantViolation):
                sim.do(Verb.CANCEL, PM, CancelArgs("x"), "WO-0001")

    def test_a_guard_may_only_return_a_code_it_declared(self):
        sim = Sim(CFG, doc_of(item_in(Phase.RUNNING)))
        rogue = state.Guard(
            "close_gates", frozenset({RefusalCode.NOTHING_TO_ACK}), lambda c: state.Verdict(RefusalCode.STALE, "x")
        )
        with mock.patch.dict(state.GUARDS, {"has_unacked_replies": rogue}), self.assertRaises(InvariantViolation):
            sim.do(Verb.ACK, PM, NoArgs(), "WO-0001")


class RegistryTests(unittest.TestCase):
    """The table names its guards, effects and triggers; the registries and the table must not drift apart."""

    def test_every_name_the_table_uses_is_defined_and_every_definition_is_used(self):
        self.assertEqual({g for r in state.TABLE for g in r.guards}, set(state.GUARDS))
        self.assertEqual({r.effect for r in state.TABLE if r.effect}, set(state.EFFECTS))
        self.assertEqual({r.trigger for r in state.TABLE if r.trigger}, set(state.TRIGGERS))
        self.assertTrue(all((r.trigger is not None) == (r.verb is Verb.MESH) for r in state.TABLE))

    def test_every_refusal_code_is_reachable_and_no_guard_names_a_missing_one(self):
        generic = tools.TOOL_CODES | {
            RefusalCode.NOT_IN_TABLE,
            RefusalCode.UNKNOWN_CALLER,
            RefusalCode.UNKNOWN_ITEM,
            RefusalCode.STALE,
            RefusalCode.ARGS_INVALID,
        }
        declared = set().union(*(g.codes for g in state.GUARDS.values()))
        from mesh import pane  # the tool-level codes outside the table (pane_command)

        self.assertEqual(declared | generic | pane.CODES, set(RefusalCode))
        self.assertFalse(declared & generic)
        self.assertFalse(pane.CODES & (declared | generic))

    def test_rows_of_one_verb_agree_on_their_args_and_the_data_only_rows_are_exactly_the_slack_ones(self):
        for verb in Verb:
            self.assertTrue(state.args_type(verb))
        self.assertEqual({r.id for r in state.TABLE if r.effect is None}, {"draft", "attach"})
        self.assertEqual({r.actor for r in state.TABLE if r.effect is None}, {Actor.SLACK_WATCHER})

    def test_work_transition_exposes_exactly_the_specs_fifteen_verbs_derived_from_the_table(self):
        spec = [
            "reply",
            "revise",
            "ready",
            "start",
            "submit",
            "verify",
            "fail",
            "reassign",
            "split",
            "unblock",
            "reject",
            "ack",
            "close",
            "cancel",
            "override",
        ]
        self.assertEqual(state.surface_verbs(state.WORK_TRANSITION), tuple(spec))
        self.assertEqual(state.surface_verbs(state.WORK_NEW), ("new",))
        self.assertEqual(state.surface_verbs(state.WORK_DISPATCH), ("dispatch",))


class RandomWalkTests(unittest.TestCase):
    """The table walk for invariants 1, 4, 5, 7, 8, 10, 11, 13, 14, 15: seeded random requests (valid and not) through
    `decide`, every applied write checked against the section-10 properties that are about TRANSITIONS."""

    ROLES = ("backend", "frontend")
    listed = [(v, c, f) for v, c, f, _t in sorted(state.model_rows()) if v not in ("mesh", "draft", "attach")]

    def gen_args(self, rng, doc, verb, item):
        a = item.current_attempt if item else None
        cur = a.id if a else 0
        pick = rng.choice
        if verb is Verb.NEW:
            return new_args(
                role=pick(self.ROLES),
                depends=tuple(rng.sample(sorted(doc.items), k=min(len(doc.items), rng.randint(0, 2)))),
                type=pick(("feature", "bugfix")),
                repro=pick((None, ("t",))),
            )
        return {
            Verb.READY: lambda: ReadyArgs(
                pick(
                    (
                        None,
                        ContractPatch(done_criteria=(f"c{rng.randint(0, 99)}",)),
                        ContractPatch(scope=Scope(write_set=(f"w{rng.randint(0, 99)}",))),
                    )
                )
            ),
            Verb.REVISE: lambda: ReviseArgs("r"),
            Verb.SUBMIT: lambda: SubmitArgs(pick((cur,) * 5 + (cur + 1,)), pick((COMMIT,) * 5 + (HEAD,))),
            Verb.VERIFY: lambda: VerifyArgs(pick((cur,) * 5 + (0,))),
            Verb.FAIL: lambda: FailArgs(
                pick(("defect", "conflict", "ambiguity", "environment", "limit")),
                pick((cur, cur, cur, None)),
                pick((None, None, "too_large")),
            ),
            Verb.SPLIT: lambda: two_children(),
            Verb.REJECT: lambda: RejectArgs(pick(("defect", "conflict", "ambiguity", "limit"))),
            Verb.START: lambda: StartArgs(),
            Verb.DISPATCH: lambda: SEND,
            Verb.REASSIGN: lambda: ReassignArgs(),
            Verb.CANCEL: lambda: CancelArgs("c"),
            Verb.REPLY: lambda: ReplyArgs(f"ev-{self.event_n:06d}"),
            Verb.OVERRIDE: lambda: OverrideArgs(pick([p.value for p in Phase]), "o", f"ev-{self.event_n:06d}"),
        }.get(verb, lambda: NoArgs())()

    def gen_facts(self, rng, cfg, item):
        a = item.current_attempt if item else None
        commit = a.commit if a and a.commit else COMMIT
        named = item.id if item else "t"  # D98: an override's event names the item
        event = OperatorEvent(f"ev-{self.event_n:06d}", self.event_n + 1, AT, f"t {named}")
        status = rng.choice((RunStatus.PASS,) * 5 + (RunStatus.FAIL,))
        return Facts(
            head=HEAD,
            lead_live=rng.choice((True,) * 4 + (False,)),
            lead_agent=LeadAgent("be-uid"),
            commit_exists=rng.choice((True,) * 5 + (False,)),
            commit_descends=rng.choice((True,) * 5 + (False, None)),
            repro=ReproOutcome(rng.choice(("fails",) * 4 + ("passes",)), HEAD, 1),
            precheck=PrecheckOutcome(rng.choice((True,) * 4 + (False,))),
            verification=passing(commit=commit, cfg=cfg, status=status).verification,
            operator_event=EventFact(event),
            merged_commit=MERGED,
        )

    def pick_request(self, rng, doc):
        """Mostly a verb the table lists for some item's phase (so the walk gets through the lifecycle); some junk."""
        listed = self.listed
        if rng.random() < 0.15 or not doc.items:
            verb = rng.choice([v for v in Verb if v is not Verb.MESH])
            caller = rng.choice((PM, PM, BACKEND, BACKEND, FRONTEND, UNKNOWN))
            return verb, caller, (rng.choice(sorted(doc.items) + ["WO-0099"]) if doc.items else "WO-0001")
        if len(doc.items) < 5 and rng.random() < 0.15:
            return Verb.NEW, PM, None
        item = doc.items[rng.choice(sorted(doc.items))]
        options = [
            x
            for x in listed
            if x[2] == item.phase.value and (x[0] not in ("override", "cancel") or rng.random() < 0.04)
        ]
        verb, actor, _ = rng.choice(options or [(Verb.CANCEL.value, "pm", "")])
        caller = (
            BACKEND
            if actor == "lead (own role)" and item.contract.role == "backend"
            else FRONTEND
            if actor == "lead (own role)"
            else PM
        )
        return Verb(verb), caller, item.id

    def walk(self, seed, steps=900, split_parent=False):
        rng, cfg = random.Random(seed), Config(retry_limit=2, environment_limit=2, verify_commands=CFG.verify_commands)
        sim, starts, self.event_n = Sim(cfg, split_sim(cfg).doc if split_parent else None), {}, 1
        for _ in range(steps):
            self.event_n += rng.choice((0, 1, 1))
            newest = self.event_n + 1 + (rng.random() < 0.33)  # the cited event is the newest prompt 2 times in 3
            sim.doc = replace(sim.doc, last_operator_seq=max(sim.doc.last_operator_seq, newest))
            before = sim.doc
            if rng.random() < 0.3 and state.mesh_steps_due(before):  # the engine's drain
                due = rng.choice(state.mesh_steps_due(before))
                verb, caller, item_id, row_id = Verb.MESH, Caller.mesh(), due.item_id, due.row.id
            else:
                (verb, caller, item_id), row_id = self.pick_request(rng, before), None
            item = before.items.get(item_id)
            args = self.gen_args(rng, before, verb, item) if verb is not Verb.MESH else NoArgs()
            plan = state.plan(before, req(verb, caller, args, item_id, row_id))
            stamp = plan.stamp if not isinstance(plan, Refused) and rng.random() < 0.3 else None
            out = sim.do(verb, caller, args, item_id, self.gen_facts(rng, cfg, item), stamp, row_id)
            if isinstance(out, Refused):
                self.assertIs(sim.doc, before)  # inv 1: a refusal changes nothing
                continue
            self.check_transition(cfg, before, sim.doc, out, starts)
        return {h.row for item in sim.doc.items.values() for h in item.history}  # primary AND reconciled rows

    def check_transition(self, cfg, before, after, out, starts):
        for item_id, now in after.items.items():
            was = before.items.get(item_id)
            if was is None or was == now:
                continue
            row = now.history[-1].row
            if now.phase is Phase.BLOCKED_LIMIT and was.phase is not Phase.BLOCKED_LIMIT:
                self.assertIn(row, ("start", "unblock"), "inv 4")
            if now.phase is Phase.CLOSED and was.phase is not Phase.CLOSED:
                self.assertTrue(all(r.acknowledged for r in now.replies), "inv 7")
                if row != "override":
                    self.assertFalse(was.revalidate, "inv 7")
            if now.phase is Phase.VERIFIED and was.phase is not Phase.VERIFIED:
                self.assertFalse(now.revalidate, "inv 15 / D69")
            if was.phase is Phase.BLOCKED_LIMIT and now.phase is Phase.READY and row == "ready":
                used = [r for r, o in zip(now.replies, was.replies) if r.consumed and not o.consumed]
                self.assertEqual(len(used), 1, "inv 10: exactly one reply is consumed")
                self.assertGreater(used[0].seq, was.limit_seq, "inv 10: it is fresher than the limit entry")
            if row == "start" and now.phase is Phase.RUNNING:
                self.assertTrue(all(before.dep_status(d).value == "satisfied" for d in now.contract.depends), "inv 8")
                starts[item_id] = starts.get(item_id, 0) + 1
            if was.phase is Phase.BLOCKED_LIMIT and now.phase is Phase.READY:
                starts[item_id] = 0
            self.assertLessEqual(starts.get(item_id, 0), 1 + cfg.retry_limit + cfg.environment_limit, "inv 5")

    def test_seeded_walks_hold_every_transition_property_and_exercise_the_table(self):
        hit = set()
        for seed in range(4):
            hit |= self.walk(seed, steps=700)
        for seed in range(8):  # walks that start from a split parent waiting for two children
            hit |= self.walk(seed, steps=450, split_parent=True)
        data_only = {"draft", "attach"}  # no caller maps to the Slack watcher in the MVP
        by_direct_tests = {"mesh_verifying", "mesh_verified", "mesh_verify_fail"}  # MeshRowTests + the drain test
        self.assertEqual({r.id for r in state.TABLE} - data_only - by_direct_tests - hit, set())


class ImportContractTests(unittest.TestCase):
    """Invariant 12 as far as the core goes: the pure core does no I/O and reads no clock; nothing in mesh/ starts a
    subprocess except `mesh/runner.py` (the one process runner, argv only). Parsed, not grepped: a comment or a string
    cannot pass or fail it."""

    IO = {"os", "subprocess", "pathlib", "fcntl", "socket", "time", "tomllib", "shutil", "tempfile", "io", "sys"}
    CLOCK = {("datetime", "now"), ("datetime", "utcnow"), ("datetime", "today"), ("date", "today"), ("time", "time")}

    def tree(self, name):
        return ast.parse((ROOT / "mesh" / f"{name}.py").read_text(encoding="utf-8"))

    def imports(self, name):
        out = set()
        for n in ast.walk(self.tree(name)):
            if isinstance(n, ast.Import):
                out |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                out.add(n.module.split(".")[0])
        return out

    def test_model_and_state_import_no_io_module(self):
        for name in ("model", "state"):
            self.assertEqual(self.imports(name) & self.IO, set(), name)

    def test_only_the_runner_imports_subprocess(self):
        importers = {p.stem for p in sorted((ROOT / "mesh").glob("*.py")) if "subprocess" in self.imports(p.stem)}
        self.assertEqual(importers, {"runner"})

    def test_every_process_runs_through_the_runner_with_a_timeout(self):
        """`runner.run(argv, timeout, ...)`: no call site in mesh/ may leave the timeout out."""
        sites = 0
        for path in sorted((ROOT / "mesh").glob("*.py")):
            for n in ast.walk(self.tree(path.stem)):
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "run"
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "runner"
                ):
                    sites += 1
                    self.assertTrue(len(n.args) >= 2 or any(k.arg == "timeout" for k in n.keywords), path.name)
        self.assertGreater(sites, 0)
        for name in ("git", "verify"):
            imported = {a.name for n in ast.walk(self.tree(name)) if isinstance(n, ast.ImportFrom) for a in n.names}
            self.assertIn("runner", imported, name)

    def test_no_mesh_module_passes_shell_true(self):
        for path in sorted((ROOT / "mesh").glob("*.py")):
            shells = [
                k
                for n in ast.walk(self.tree(path.stem))
                if isinstance(n, ast.Call)
                for k in n.keywords
                if k.arg == "shell"
            ]
            self.assertEqual(shells, [], path.name)

    def test_the_core_reads_no_wall_clock(self):  # the time is an injected value (Request.at)
        for name in ("model", "state", "store", "engine"):
            calls = {
                (n.func.value.id, n.func.attr)
                for n in ast.walk(self.tree(name))
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
            }
            self.assertEqual(calls & self.CLOCK, set(), name)


if __name__ == "__main__":
    unittest.main()
