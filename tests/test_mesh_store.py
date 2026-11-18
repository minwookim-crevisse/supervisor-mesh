"""Acceptance 1 (core part): the store (lock, atomic replace, logs, schema version) and the engine (one path)."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

from _helpers import ROOT
from test_mesh_state import AT, BACKEND, CFG, PM, SHA, UNKNOWN, Sim, contract, doc_of, item_in, new_args, passing, req

sys.path.insert(0, str(ROOT))
from mesh import engine, state, store  # noqa: E402
from mesh.model import (
    SCHEMA_VERSION,
    Applied,
    Attempt,
    Busy,
    Command,
    Config,
    EventFact,
    Fact,
    Facts,
    NoArgs,
    OperatorEvent,
    OverrideArgs,
    Phase,
    ReadyArgs,  # noqa: E402
    RefusalCode,
    Refused,
    ReplyArgs,
    SchemaTooNew,
    StartArgs,
    Verb,
    VerifyArgs,
    empty_doc,
)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mesh_dir = Path(self.tmp.name) / ".mesh"
        self.store = store.Store(self.mesh_dir)
        self.assertTrue(self.store.create())

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, doc):
        with self.store.locked() as txn:
            txn.commit(doc)

    @property
    def state_bytes(self):
        return (self.mesh_dir / "state.json").read_bytes()


class StateFileTests(StoreCase):
    def test_create_writes_an_empty_deterministic_document_and_never_replaces_one(self):
        first = self.state_bytes
        self.assertFalse(self.store.create())
        self.assertEqual(self.state_bytes, first)
        raw = json.loads(first)
        self.assertEqual((raw["schema_version"], raw["seq"], raw["items"]), (SCHEMA_VERSION, 0, {}))
        self.assertTrue(first.endswith(b"\n"))

    def test_a_document_round_trips_and_a_second_write_is_byte_identical(self):
        sim = Sim()
        sim.ok(Verb.NEW, PM, new_args())
        sim.ok(Verb.NEW, PM, new_args(role="frontend", depends=("WO-0001",)))
        self.seed(sim.doc)
        self.assertEqual(self.store.read().doc, sim.doc)
        before = self.state_bytes
        self.seed(sim.doc)
        self.assertEqual(self.state_bytes, before)
        item = json.loads(before)["items"]["WO-0001"]
        self.assertEqual((item["state"], item["blocked_reason"], item["phase"]), ("draft", None, "draft"))

    def test_blocked_state_is_written_with_its_derived_reason_for_readers(self):
        self.seed(doc_of(item_in(Phase.BLOCKED_LIMIT)))
        item = json.loads(self.state_bytes)["items"]["WO-0001"]
        self.assertEqual((item["state"], item["blocked_reason"]), ("blocked", "limit"))
        self.assertEqual(self.store.read().doc.items["WO-0001"].phase, Phase.BLOCKED_LIMIT)

    def test_a_contradictory_state_and_phase_is_a_load_error_not_a_guess(self):
        self.seed(doc_of(item_in(Phase.READY)))
        raw = json.loads(self.state_bytes)
        raw["items"]["WO-0001"]["state"] = "running"
        (self.mesh_dir / "state.json").write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(store.StateCorrupt):
            self.store.read()

    def test_commit_fsyncs_the_file_and_then_the_directory(self):
        seen = []
        real = os.fsync
        with mock.patch(
            "mesh.store.os.fsync", side_effect=lambda fd: (seen.append(stat.S_ISDIR(os.fstat(fd).st_mode)), real(fd))[1]
        ):
            self.seed(doc_of(item_in(Phase.READY)))
        self.assertEqual(seen, [False, True])

    def test_a_crash_before_the_replace_keeps_the_old_document_and_leaves_no_tmp_file(self):
        self.seed(doc_of(item_in(Phase.READY)))
        before = self.state_bytes
        with mock.patch("mesh.store.os.replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError), self.store.locked() as txn:
                txn.commit(empty_doc())
        self.assertEqual(self.state_bytes, before)
        self.assertEqual([p.name for p in self.mesh_dir.glob("*.tmp")], [])

    def test_a_newer_schema_is_readable_but_never_written(self):
        self.seed(doc_of(item_in(Phase.READY)))
        raw = json.loads(self.state_bytes)
        raw["schema_version"] = SCHEMA_VERSION + 1
        raw["from_the_future"] = {"x": 1}
        (self.mesh_dir / "state.json").write_text(json.dumps(raw), encoding="utf-8")
        snap = self.store.read()
        self.assertFalse(snap.writable)
        self.assertIn("WO-0001", snap.doc.items)
        before = self.state_bytes
        with self.assertRaises(SchemaTooNew), self.store.locked() as txn:
            txn.commit(txn.doc)
        self.assertEqual(self.state_bytes, before)

    def test_reading_without_init_says_so(self):
        with self.assertRaises(store.NotInitialized):
            store.Store(Path(self.tmp.name) / "other" / ".mesh").read()


HOLDER = textwrap.dedent("""
    import fcntl, sys, time
    fd = open(sys.argv[1], "a")
    fcntl.flock(fd, fcntl.LOCK_EX)
    print("locked", flush=True)
    time.sleep(30)
""")


class LockTests(StoreCase):
    def hold_lock_in_another_process(self):
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", HOLDER, str(self.mesh_dir / "state.lock")], stdout=subprocess.PIPE, text=True
        )
        self.addCleanup(lambda: (proc.kill(), proc.wait(), proc.stdout.close()))
        self.assertEqual(proc.stdout.readline().strip(), "locked")
        return proc

    def test_a_second_writer_times_out_with_busy_while_another_process_holds_the_lock(self):
        self.hold_lock_in_another_process()
        started = time.monotonic()
        with self.assertRaises(Busy):
            with self.store.locked(timeout_s=0.3):
                self.fail("must not get the lock")
        self.assertLess(time.monotonic() - started, 3)

    def test_reading_never_waits_for_the_lock(self):
        self.hold_lock_in_another_process()
        started = time.monotonic()
        self.store.read()
        self.assertLess(time.monotonic() - started, 0.5)

    def test_create_runs_under_the_lock(self):  # two concurrent inits cannot both write the first document
        (self.mesh_dir / "state.json").unlink()
        self.hold_lock_in_another_process()
        with self.assertRaises(Busy):
            self.store.create(timeout_s=0.3)
        self.assertFalse((self.mesh_dir / "state.json").exists())

    def test_the_lock_is_released_when_the_block_ends_or_raises(self):
        with self.assertRaises(RuntimeError), self.store.locked():
            raise RuntimeError("boom")
        with self.store.locked(timeout_s=0.2):
            pass


class LogTests(StoreCase):
    def test_refusals_are_appended_and_never_rewritten(self):
        out = Sim().do(Verb.START, BACKEND, NoArgs(), "WO-0001")  # unknown item: a refusal value
        out2 = Sim(doc=doc_of(item_in(Phase.READY))).do(Verb.CLOSE, BACKEND, NoArgs(), "WO-0001")
        self.store.append_refusal(out.refusal)
        self.store.append_refusal(out2.refusal)
        lines = (self.mesh_dir / "refusals.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x)["code"] for x in lines], ["unknown_item", "not_in_table"])
        self.assertEqual(json.loads(lines[1])["subject"], "close")
        self.assertEqual(len(self.store.refusals()), 2)

    def test_operator_events_take_their_seq_under_the_lock_and_never_collide(self):
        events, errors = [], []

        def record(n):
            try:
                events.append(self.store.record_operator_event(f"prompt {n}", AT, "pm-uid"))
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=record, args=(n,)) for n in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(sorted(e.seq for e in events), list(range(1, 9)))
        self.assertEqual(self.store.read().doc.seq, 8)
        self.assertEqual(len(set(e.id for e in events)), 8)
        self.assertEqual(self.store.read().doc.last_operator_seq, 8)  # bumped under the same lock as the seq
        self.assertEqual(json.loads(self.state_bytes)["last_operator_seq"], 8)  # persisted in state.json
        newest = max(events, key=lambda e: e.seq)
        self.assertEqual(self.store.get_event(newest.id), newest)
        self.assertIsNone(self.store.get_event("ev-999999"))


class FileModeTests(unittest.TestCase):
    def test_state_lock_and_both_logs_are_owner_only_whatever_the_umask(self):  # events hold operator prompt text
        old_umask = os.umask(0)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                s = store.Store(Path(tmp) / ".mesh")
                s.create()
                s.record_operator_event("the operator typed this", AT, "pm-uid")
                s.append_refusal(Sim().do(Verb.START, BACKEND, NoArgs(), "WO-0001").refusal)
                with s.locked() as txn:  # a later commit keeps the mode
                    txn.commit(txn.doc)
                modes = {
                    name: stat.S_IMODE(os.stat(s.dir / name).st_mode)
                    for name in ("state.json", "state.lock", "refusals.jsonl", "events.jsonl")
                }
        finally:
            os.umask(old_umask)
        self.assertEqual(modes, dict.fromkeys(modes, 0o600))


class ConfigTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        self.assertEqual((Config().retry_limit, Config().environment_limit), (2, 3))
        cfg = store.parse_config(
            '[limits]\nretry = 5\n[types.feature]\nrequired_evidence = ["tests"]\n'
            '[[verify.commands]]\nargv = ["make", "check"]\nkind = "tests"\n'
        )
        self.assertEqual((cfg.retry_limit, cfg.environment_limit), (5, 3))
        self.assertEqual(cfg.types["feature"].required_evidence, ("tests",))
        self.assertIn("bugfix", cfg.types)
        self.assertEqual(cfg.verify_commands, (Command(("make", "check"), "tests"),))

    def test_the_verification_hash_follows_evidence_inputs_only(self):
        base = store.parse_config('[[verify.commands]]\nargv = ["make", "check"]\nkind = "tests"\n')
        limits = store.parse_config(
            '[limits]\nretry = 9\n[[verify.commands]]\nargv = ["make", "check"]\nkind = "tests"\n'
        )
        other = store.parse_config('[[verify.commands]]\nargv = ["make", "lint"]\nkind = "tests"\n')
        self.assertEqual(base.verify_hash(), limits.verify_hash())
        self.assertNotEqual(base.verify_hash(), other.verify_hash())

    def test_a_missing_file_means_defaults_and_a_bad_file_is_invalid_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = store.Store(Path(tmp) / ".mesh")
            s.create()
            self.assertEqual(s.read_config(), Config())
            (Path(tmp) / ".mesh" / "config.toml").write_text("[limits\n", encoding="utf-8")
            with self.assertRaises(store.InvalidArgs):
                s.read_config()


class EngineTests(StoreCase):
    def engine(self, facts=None, on_commit=None, cfg=CFG):
        return engine.Engine(self.store, facts=facts, cfg=cfg, on_commit=on_commit)

    def test_a_verb_goes_through_one_path_and_lands_in_state_json(self):
        out = self.engine().execute(req(Verb.NEW, PM, new_args()))
        self.assertIsInstance(out, Applied)
        item = self.store.read().doc.items["WO-0001"]
        self.assertEqual((item.phase, item.version), (Phase.DRAFT, 1))

    def test_a_refused_verb_is_recorded_and_leaves_state_json_byte_identical(self):
        eng = self.engine()
        eng.execute(req(Verb.NEW, PM, new_args()))
        before = self.state_bytes
        refused = [
            eng.execute(req(Verb.CLOSE, BACKEND, NoArgs(), "WO-0001")),  # not in the table for a lead
            eng.execute(req(Verb.CLOSE, UNKNOWN, NoArgs(), "WO-0001")),  # unknown caller
            eng.execute(req(Verb.CLOSE, PM, NoArgs(), "WO-0099")),
        ]  # unknown item
        self.assertTrue(all(isinstance(r, Refused) for r in refused))
        self.assertEqual(self.state_bytes, before)
        self.assertEqual([r["code"] for r in self.store.refusals()], ["not_in_table", "unknown_caller", "unknown_item"])
        self.assertEqual(self.store.refusals()[0]["actor"], "lead (own role)")

    def test_a_guard_refusal_writes_nothing_to_the_item_and_is_recorded(self):  # D68
        eng = self.engine()
        eng.execute(req(Verb.NEW, PM, new_args(goal="")))
        before = self.state_bytes
        out = eng.execute(req(Verb.READY, PM, ReadyArgs(), "WO-0001"))
        self.assertEqual(out.refusal.code, RefusalCode.CONTRACT_INCOMPLETE)
        self.assertEqual(self.state_bytes, before)
        self.assertEqual(self.store.refusals()[-1]["code"], "contract_incomplete")

    def test_facts_are_gathered_with_the_lock_free(self):
        self.seed(doc_of(item_in(Phase.READY)))
        probes = []

        def provider(needs, doc, request, cfg):
            with self.store.locked(timeout_s=0.2):  # would raise Busy if the engine held the lock
                probes.append((request.verb, needs, cfg))
            return Facts(lead_live=True, head="c" * 40)

        out = self.engine(provider).execute(req(Verb.START, BACKEND, StartArgs(), "WO-0001"))
        self.assertIsInstance(out, Applied, out)
        self.assertEqual(probes, [(Verb.START, frozenset({Fact.HEAD, Fact.LEAD_LIVE}), CFG)])  # only what the row needs

    def test_the_engine_owns_the_operator_event_fact_and_a_provider_cannot_forge_it(self):
        self.seed(doc_of(item_in(Phase.READY)))
        asked, forged = [], OperatorEvent("ev-000009", 9, AT, "forged", "pm-uid")

        def provider(needs, doc, request, cfg):
            asked.append(needs)
            return Facts(operator_event=EventFact(forged))

        eng = self.engine(provider)
        out = eng.execute(req(Verb.OVERRIDE, PM, OverrideArgs("cancelled", "o", forged.id), "WO-0001"))
        self.assertEqual((out.refusal.code, asked), (RefusalCode.EVENT_UNKNOWN, []))  # looked up in the store only
        event = self.store.record_operator_event("cancel WO-0001", AT, "pm-uid")  # D98: names the item
        out = eng.execute(req(Verb.OVERRIDE, PM, OverrideArgs("cancelled", "o", event.id), "WO-0001"))
        self.assertIsInstance(out, Applied, out)
        self.assertEqual(asked, [])

    def test_a_stale_attempt_stamp_is_refused_and_writes_nothing(self):  # invariant 11
        verifying = item_in(
            Phase.VERIFYING,
            contract=contract(version=1),
            attempts=(Attempt(2, "be-uid", "b" * 40, SHA, 1, CFG.verify_hash()),),
        )
        self.seed(doc_of(verifying))
        moved = replace(verifying, attempts=(Attempt(3, "be-uid", "b" * 40, SHA, 1, CFG.verify_hash()),), version=5)

        def provider(needs, doc, request, cfg):  # another writer wins between plan and decide
            self.seed(doc_of(moved))
            return passing(SHA)

        out = self.engine(provider).execute(req(Verb.VERIFY, BACKEND, VerifyArgs(attempt_id=2), "WO-0001"))
        self.assertIsInstance(out, Refused)
        self.assertEqual(out.refusal.code, RefusalCode.STALE)
        self.assertEqual(self.store.read().doc.items["WO-0001"], moved)  # nothing of the verify landed
        self.assertEqual(self.store.refusals()[-1]["code"], "stale")

    def test_a_write_landing_while_facts_are_gathered_makes_the_result_stale_and_it_writes_nothing(self):
        self.seed(doc_of(item_in(Phase.READY)))
        competing = []

        def provider(needs, doc, request, cfg):  # another session writes the item during EVERY gather
            event = self.store.record_operator_event(f"noted {len(competing)}", AT, "pm-uid")
            competing.append(self.engine().execute(req(Verb.REPLY, PM, ReplyArgs(event.id), "WO-0001")))
            return Facts(lead_live=True, head="c" * 40)

        out = self.engine(provider).execute(req(Verb.START, BACKEND, StartArgs(), "WO-0001"))
        self.assertTrue(all(isinstance(c, Applied) for c in competing), competing)
        self.assertEqual(len(competing), 2)  # the plan was retried once (VERSION stamp), then refused
        self.assertEqual(out.refusal.code, RefusalCode.STALE)
        self.assertEqual(self.state_bytes.decode("utf-8"), store.dump_doc(competing[-1].doc))  # only competing writes
        self.assertEqual(self.store.read().doc.items["WO-0001"].phase, Phase.READY)
        self.assertEqual(self.store.refusals()[-1]["code"], "stale")

    def test_a_reply_written_during_an_unlocked_verify_does_not_discard_it(self):  # D76
        verifying = item_in(
            Phase.VERIFYING,
            contract=contract(version=1),
            attempts=(Attempt(2, "be-uid", "b" * 40, SHA, 1, CFG.verify_hash()),),
        )
        self.seed(doc_of(verifying))
        event = self.store.record_operator_event("looks fine", AT, "pm-uid")

        def provider(needs, doc, request, cfg):  # the reply needs only the operator event: no provider call
            eng.execute(req(Verb.REPLY, PM, ReplyArgs(event.id), "WO-0001"))  # a reply-side write: version +1
            return passing(SHA)

        eng = self.engine(provider)
        out = eng.execute(req(Verb.VERIFY, BACKEND, VerifyArgs(attempt_id=2), "WO-0001"))
        self.assertIsInstance(out, Applied, out)
        item = self.store.read().doc.items["WO-0001"]
        self.assertEqual((item.phase, len(item.replies)), (Phase.VERIFIED, 1))

    def test_a_version_stale_row_is_re_planned_once(self):
        self.seed(doc_of(item_in(Phase.READY)))
        event = self.store.record_operator_event("hello", AT, "pm-uid")
        calls = []

        def provider(needs, doc, request, cfg):
            calls.append(request.verb.value)  # the reply needs only the operator event: the engine reads it itself
            if calls.count("start") == 1:  # first pass: a concurrent reply bumps the version
                eng.execute(req(Verb.REPLY, PM, ReplyArgs(event.id), "WO-0001"))
            return Facts(lead_live=True, head="c" * 40)

        eng = self.engine(provider)
        out = eng.execute(req(Verb.START, BACKEND, StartArgs(), "WO-0001"))
        self.assertIsInstance(out, Applied, out)
        self.assertEqual(calls, ["start", "start"])
        self.assertEqual(self.store.refusals(), [])  # the first stale attempt was not a refusal

    def test_drain_runs_the_due_mesh_rows_that_need_facts_through_the_same_path(self):  # D72
        merged = "e" * 40
        done = [item_in(Phase.CLOSED, item_id=f"WO-000{n}", contract=contract(version=1)) for n in (2, 3)]
        done = [replace(d, links=replace(d.links, parent="WO-0001")) for d in done]
        parent = item_in(
            Phase.BLOCKED_CHILDREN,
            contract=contract(version=1),
            links=replace(item_in(Phase.READY).links, children=("WO-0002", "WO-0003")),
        )
        self.seed(doc_of(parent, *done))

        def provider(needs, doc, request, cfg):
            return Facts(merged_commit=merged) if Fact.MERGED_COMMIT in needs else passing(merged)

        eng = self.engine(provider)
        outcomes = eng.drain(AT)
        self.assertEqual([o.row_id for o in outcomes], ["mesh_verifying", "mesh_verified"])
        self.assertEqual(self.store.read().doc.items["WO-0001"].phase, Phase.VERIFIED)
        self.assertEqual(eng.drain(AT), ())  # nothing left: idempotent
        self.assertEqual(self.store.refusals(), [])  # selection misses are not noise

    def split_parent_with_a_last_verified_child(self):
        done = replace(item_in(Phase.CLOSED, item_id="WO-0002", contract=contract(version=1)))
        last = item_in(Phase.VERIFIED, item_id="WO-0003")
        children = [replace(c, links=replace(c.links, parent="WO-0001")) for c in (done, last)]
        parent = item_in(
            Phase.BLOCKED_CHILDREN,
            contract=contract(version=1),
            links=replace(item_in(Phase.READY).links, children=("WO-0002", "WO-0003")),
        )
        self.seed(doc_of(parent, *children))

    def test_due_mesh_steps_are_drained_after_a_write(self):  # D72: no caller has to remember the follow-up
        self.split_parent_with_a_last_verified_child()
        merged = "e" * 40

        def provider(needs, doc, request, cfg):
            return Facts(merged_commit=merged) if Fact.MERGED_COMMIT in needs else passing(merged)

        out = self.engine(provider).execute(req(Verb.CLOSE, PM, NoArgs(), "WO-0003"))
        self.assertIsInstance(out, Applied, out)
        self.assertEqual(out.row_id, "close")  # the caller gets ITS outcome, not the drained ones
        parent = self.store.read().doc.items["WO-0001"]
        self.assertEqual(parent.phase, Phase.VERIFIED)
        self.assertEqual([h.row for h in parent.history[-2:]], ["mesh_verifying", "mesh_verified"])
        self.assertEqual(self.store.refusals(), [])

    def test_a_failing_drain_never_turns_the_callers_committed_write_into_an_error(self):
        self.split_parent_with_a_last_verified_child()
        merged = "e" * 40

        def provider(needs, doc, request, cfg):
            return Facts(merged_commit=merged) if Fact.MERGED_COMMIT in needs else passing(merged)

        eng = self.engine(provider)
        for error in (Busy("another writer holds .mesh/state.lock", hint="retry"), RuntimeError("boom")):
            with self.subTest(error=type(error).__name__):
                self.split_parent_with_a_last_verified_child()
                with mock.patch.object(eng, "drain", side_effect=error), mock.patch("sys.stderr") as err:
                    out = eng.execute(req(Verb.CLOSE, PM, NoArgs(), "WO-0003"))
                self.assertIsInstance(out, Applied, out)  # the caller must not retry a write that landed
                self.assertEqual(out.row_id, "close")
                doc = self.store.read().doc
                self.assertEqual(doc.items["WO-0003"].phase, Phase.CLOSED)  # its write is on disk
                self.assertEqual(doc.items["WO-0001"].phase, Phase.BLOCKED_CHILDREN)  # the due rows stay due
                logged = "".join(c.args[0] for c in err.write.call_args_list)
                self.assertEqual(logged.count("\n"), 1, logged)  # one line
                self.assertIn(type(error).__name__, logged)
        self.assertEqual([o.row_id for o in eng.drain(AT)], ["mesh_verifying", "mesh_verified"])  # recomputed later

    def test_a_due_mesh_step_whose_fact_is_not_available_yet_stays_due_and_records_no_refusal(self):
        self.split_parent_with_a_last_verified_child()
        out = self.engine().execute(req(Verb.CLOSE, PM, NoArgs(), "WO-0003"))  # no adapter: merged_commit unknown
        self.assertIsInstance(out, Applied, out)
        self.assertEqual(self.store.read().doc.items["WO-0001"].phase, Phase.BLOCKED_CHILDREN)
        self.assertEqual(self.store.refusals(), [])
        self.assertEqual([d.row.id for d in state.mesh_steps_due(self.store.read().doc)], ["mesh_verifying"])

    def test_the_post_commit_hook_runs_after_the_lock_is_released_and_never_fails_the_write(self):
        seen = []

        def hook(outcome):
            with self.store.locked(timeout_s=0.2):  # would raise Busy if the engine still held it
                seen.append(outcome.row_id)

        self.engine(on_commit=hook).execute(req(Verb.NEW, PM, new_args()))
        self.assertEqual(seen, ["new"])

        def broken(outcome):
            raise RuntimeError("effect failed")

        with mock.patch("sys.stderr"):
            out = self.engine(on_commit=broken).execute(req(Verb.NEW, PM, new_args()))
        self.assertIsInstance(out, Applied)
        self.assertEqual(len(self.store.read().doc.items), 2)

    def test_an_older_event_cannot_authorise_an_override_once_a_newer_operator_event_was_recorded(self):
        self.seed(doc_of(item_in(Phase.READY)))
        older = self.store.record_operator_event("cancel it", AT, "pm-uid")
        locked, raced = self.store.locked, []

        @contextmanager
        def newer_event_lands_first(*a, **kw):  # facts are already gathered; the newer prompt wins the lock first
            if not raced:
                raced.append(None)  # once: recording the event takes the lock through this same wrapper
                raced[0] = self.store.record_operator_event("actually, wait", AT, "pm-uid")
            with locked(*a, **kw) as txn:
                yield txn

        with mock.patch.object(self.store, "locked", newer_event_lands_first):
            out = self.engine().execute(
                req(Verb.OVERRIDE, PM, OverrideArgs("cancelled", "operator said so", older.id), "WO-0001")
            )
        self.assertEqual(len(raced), 1)
        self.assertIsInstance(out, Refused, out)
        self.assertEqual(out.refusal.code, RefusalCode.EVENT_STALE)
        self.assertEqual(self.store.read().doc.items["WO-0001"].phase, Phase.READY)
        self.assertEqual(self.store.read().doc.last_operator_seq, raced[0].seq)

    def test_a_newer_schema_refuses_every_verb_but_reads_on(self):
        self.seed(doc_of(item_in(Phase.READY)))
        raw = json.loads(self.state_bytes)
        raw["schema_version"] = SCHEMA_VERSION + 1
        (self.mesh_dir / "state.json").write_text(json.dumps(raw), encoding="utf-8")
        before = self.state_bytes
        with self.assertRaises(SchemaTooNew):
            self.engine().execute(req(Verb.NEW, PM, new_args()))
        self.assertEqual(self.state_bytes, before)
        self.assertIn("WO-0001", self.store.read().doc.items)


if __name__ == "__main__":
    unittest.main()
