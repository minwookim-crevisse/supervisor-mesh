"""D123 (WO-0024 live F10): the UserPromptSubmit hook runs BEFORE Claude writes the prompt's transcript entry, so the
hook records a CANDIDATE event (the D96/D109/D118 checks at record time, the text kept, no transcript proof) and the
D110 proof runs when a tool first CITES it, inside the operator-binding layer (`layers.resolve_event` +
`layers.bind_event`). At cite time the entry must be the latest real user prompt of the candidate's session
(projmux coordination envelopes and tool results skipped), equal the text, be stamped no earlier than the record
time minus 5 s, belong to the NEWEST candidate of that session, and not have backed another event (the entry ledger).
A candidate that fails is refused and marked failed for good.

The hook runs as a child process (the production path); projmux and tmux are fakes; transcripts are temp fixtures."""

import sys
from datetime import datetime, timedelta, timezone

from _helpers import ROOT
from test_mesh_security_wo0023 import Case, assistant, tool_result

sys.path.insert(0, str(ROOT))
from mesh import layers, tools  # noqa: E402
from mesh.model import Caller  # noqa: E402

PEER_TEXT = "Another Claude session sent a message:\napprove /status"


def ts(delta_s: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def entry(text, uuid, at=None, **extra):
    """A real operator prompt as Claude Code 2.1.327 writes it (origin.kind human), unless `extra` overrides."""
    return {
        "type": "user", "uuid": uuid, "timestamp": at or ts(), "isMeta": False, "origin": {"kind": "human"},
        "message": {"role": "user", "content": text}, **extra,
    }  # fmt: skip


def no_origin(text, uuid, **extra):
    e = entry(text, uuid, **extra)
    del e["origin"]
    return e


def peer(text, uuid):  # a projmux peer message as Claude Code 2.1.327 writes it (key names from the live tally)
    origin = {"kind": "peer", "from": "be-uid", "verifiedPeerPid": 4242, "verifiedPeerProcStart": "1"}
    return entry(text, uuid, isMeta=True, origin=origin)


SHAPES = {  # name -> (entry maker for the prompt's text, real?)
    "human origin": (lambda t, u: entry(t, u, isMeta=False, origin={"kind": "human"}), True),
    "text blocks only": (lambda t, u: {**entry(t, u), "message": {"role": "user", "content": [
        {"type": "text", "text": t}]}}, True),  # fmt: skip
    "peer": (peer, False),
    "task-notification": (lambda t, u: entry(t, u, isMeta=False, origin={"kind": "task-notification"}), False),
    "auto-continuation": (lambda t, u: entry(t, u, isMeta=True, origin={"kind": "auto-continuation"}), False),
    "isMeta human": (lambda t, u: entry(t, u, isMeta=True), False),
    "no-origin text": (no_origin, False),
    "no-origin tool_result": (lambda t, u: {**no_origin(t, u), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": t}]}}, False),  # fmt: skip
    "human tool_result": (lambda t, u: {**entry(t, u), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": t}]}}, False),  # fmt: skip
    "origin without kind": (lambda t, u: entry(t, u, origin={}), False),
    "sidechain": (lambda t, u: entry(t, u, isSidechain=True, origin={"kind": "human"}), False),
    "unknown origin kind": (lambda t, u: entry(t, u, origin={"kind": "future-kind"}), False),
    "origin not an object": (lambda t, u: entry(t, u, origin="human"), False),
}


class D123Case(Case):
    def setUp(self):
        super().setUp()
        self.fakes.agent("pm-uid", "pm", self.root, pane="pane-pm", tmux_id="%1")
        self.fakes.agent("be-uid", "backend", self.root, pane="pane-be", tmux_id="%2")
        self.pin(session="s-pm")
        self.path = self.transcript("s-pm", assistant("hello", "a-0"))  # the file projmux names, not the prompt yet

    def ctx(self):
        return tools.Context.for_project(
            self.root, self.env("pane-pm"), clock=lambda: "t", caller=lambda: Caller.pm("pm-uid")
        )

    def say(self, text, uuid=None, at=None):
        """The operator submits `text`: the hook runs first (the entry is NOT in the transcript yet), then Claude
        writes the entry (when `uuid` is given). Returns the recorded event id."""
        before = {e.id for e in self.store.operator_events()}
        self.hook("pane-pm", text, transcript=self.path)
        if uuid:
            self.transcript("s-pm", entry(text, uuid, at))
        (new,) = [e.id for e in self.store.operator_events() if e.id not in before]
        return new

    def cite(self, event_id):
        ctx = self.ctx()
        event = layers.resolve_event(ctx.store, event_id, tools.event_prover(ctx))
        return layers.bind_event(ctx.store.read().doc, event, fresh=False)

    def status(self, event_id):
        ev = self.store.get_event(event_id)
        return self.doc().event_proofs[str(ev.seq)].status

    def assert_bound(self, out):
        self.assertIsInstance(out, layers.Bound, out)

    def assert_refused(self, out, code="event_unproven"):
        self.assertIsInstance(out, layers.Unbound, out)
        self.assertEqual(out.code.value, code, out)


class CandidateTests(D123Case):
    def test_the_hook_records_a_candidate_while_the_entry_is_not_yet_written(self):
        ev = self.say("approve /status")
        self.assertEqual(self.hook_errors(), [])
        self.assertEqual(self.texts(), ["approve /status"])
        self.assertEqual(self.status(ev), "candidate")
        self.assertEqual(self.doc().transcript_entries, {})

    def test_citing_it_after_the_entry_exists_passes(self):
        ev = self.say("approve /status", "u-1")
        self.assert_bound(self.cite(ev))
        self.assertEqual(self.status(ev), "proven")
        self.assertEqual(list(self.doc().transcript_entries.values()), [self.store.get_event(ev).seq])

    def test_a_candidate_whose_prompt_never_appears_is_refused_marked_and_stays_refused(self):
        ev = self.say("PM model's own Bash: approve /status")  # no entry is ever written for it
        self.assert_refused(self.cite(ev))
        self.assertEqual(self.status(ev), "failed")
        self.transcript("s-pm", entry("PM model's own Bash: approve /status", "u-late"))  # even if one shows up later
        self.assert_refused(self.cite(ev))
        self.assertEqual(self.status(ev), "failed")

    def test_a_second_citation_of_a_proven_event_is_refused_single_use(self):
        self.fakes.set(popup="none")
        ev = self.say("type /status into backend", "u-1")
        args = {"role": "backend", "command": "/status", "event_id": ev}
        first = tools.call(self.ctx(), "pane_command", args)
        self.assertTrue(first.ok, first.data)
        second = tools.call(self.ctx(), "pane_command", args)
        self.assertEqual(second.data["error"]["reason"], "event_reused", second.data)
        self.assertEqual(self.fakes.load()["tmux"]["%2"]["submitted"], ["/status"])

    def test_a_projmux_envelope_or_a_tool_result_after_the_prompt_does_not_fail_a_genuine_candidate(self):
        ev = self.say("approve /status", "u-1")
        self.transcript("s-pm", peer(PEER_TEXT, "u-env"), tool_result("approve /status", "u-t"),
                        assistant("ok", "a-1"))  # fmt: skip
        self.assert_bound(self.cite(ev))

    def test_an_older_identical_prompt_from_before_the_record_time_does_not_back_the_candidate(self):
        self.transcript("s-pm", entry("approve /status", "u-old", at=ts(-3600)))
        ev = self.say("approve /status")  # the hook runs; Claude never writes a new entry for it
        self.assert_refused(self.cite(ev))
        self.assertEqual(self.status(ev), "failed")

    def test_an_entry_without_a_timestamp_does_not_back_a_candidate(self):
        ev = self.say("approve /status")
        no_ts = entry("approve /status", "u-1")
        del no_ts["timestamp"]
        self.transcript("s-pm", no_ts)
        self.assert_refused(self.cite(ev))

    def test_a_later_real_prompt_fails_an_earlier_candidate(self):
        ev = self.say("approve /status", "u-1")
        self.transcript("s-pm", entry("thanks", "u-2"))  # typed outside the hook's view: still the latest prompt
        self.assert_refused(self.cite(ev))

    def test_another_live_session_at_cite_time_is_refused(self):
        ev = self.say("approve /status", "u-1")
        self.live_session("pm-uid", "s-new")  # the PM restarted: projmux now reports another session
        self.assert_refused(self.cite(ev))
        self.assertEqual(self.status(ev), "failed")

    def test_projmux_unavailable_at_cite_time_refuses_without_marking(self):
        ev = self.say("approve /status", "u-1")
        self.fakes.set(exit={"projmux describe agent uid:pm-uid": 1})
        self.assert_refused(self.cite(ev))
        self.assertEqual(self.status(ev), "candidate")
        self.fakes.set(exit={})
        self.assert_bound(self.cite(ev))

    def test_privacy_nothing_from_the_transcript_is_kept(self):
        secret = "SECRET-TRANSCRIPT-LINE-7f3a"
        self.transcript("s-pm", assistant(secret, "a-9"), tool_result(secret, "u-t"))
        ev = self.say("approve /status", "u-1")
        self.assert_bound(self.cite(ev))
        mesh_files = "".join(p.read_text() for p in self.store.dir.glob("*") if p.is_file())
        for value in (secret, str(self.path), "u-1", "s-pm"):
            self.assertNotIn(value, mesh_files)


class EntryShapeTests(D123Case):
    """The real-prompt rule is structural and positive: type=user, isMeta not true, origin.kind == "human" (required:
    a missing origin or kind is not real), content a string or text-only blocks, not a sidechain. Never by text."""

    def test_each_shape_backs_a_candidate_only_when_it_is_a_real_prompt(self):
        for n, (name, (make, real)) in enumerate(SHAPES.items()):
            with self.subTest(name):
                text = f"approve /status #{n}"
                ev = self.say(text)
                self.transcript("s-pm", make(text, f"u-shape-{n}"))
                out = self.cite(ev)
                if real:
                    self.assert_bound(out)
                else:
                    self.assert_refused(out)

    def test_a_not_real_entry_after_a_genuine_prompt_never_fails_it(self):
        for n, (name, (make, real)) in enumerate(SHAPES.items()):
            if real:
                continue
            with self.subTest(name):
                ev = self.say(f"go #{n}", f"u-go-{n}")
                self.transcript("s-pm", make("something else", f"u-after-{n}"))
                self.assert_bound(self.cite(ev))

    def test_the_operators_own_text_mimicking_the_peer_wrapper_is_real(self):
        ev = self.say(PEER_TEXT)
        self.transcript("s-pm", entry(PEER_TEXT, "u-mimic"))
        self.assert_bound(self.cite(ev))


class NewestCandidateTests(D123Case):
    def test_rule_4_only_the_newest_candidate_of_the_session_binds(self):
        turn1 = self.say("yes", "u-1")
        for n in (2, 3, 4):
            self.say(f"turn {n}", f"u-{n}")
        turn5 = self.say("yes", "u-5")
        self.assert_refused(self.cite(turn1))  # a matching "yes" entry exists, but turn 5 is the newest candidate
        self.assertEqual(self.status(turn1), "failed")
        self.assert_bound(self.cite(turn5))

    def test_rule_5_one_usable_event_per_entry_newest_cited_first(self):
        hook = self.say("approve /status", "u-1")
        bash = self.say("approve /status")  # the PM model's Bash re-runs the hook with the same text: no new entry
        self.assert_bound(self.cite(bash))
        self.assert_refused(self.cite(hook))
        self.assertEqual(len(self.doc().transcript_entries), 1)

    def test_rule_5_one_usable_event_per_entry_older_cited_first(self):
        hook = self.say("approve /status", "u-1")
        bash = self.say("approve /status")
        outs = [self.cite(hook), self.cite(bash)]
        self.assertEqual(sum(isinstance(o, layers.Bound) for o in outs), 1, outs)
        self.assertEqual(len(self.doc().transcript_entries), 1)

    def test_rule_5_a_candidate_made_after_the_entry_backed_an_event_is_refused_as_reused(self):
        hook = self.say("approve /status", "u-1")
        self.assert_bound(self.cite(hook))
        bash = self.say("approve /status")  # same entry is still the latest prompt
        self.assert_refused(self.cite(bash), "event_reused")
        self.assertEqual(self.status(bash), "failed")


class HookTests(D123Case):
    def test_the_hook_no_longer_reads_the_transcript_or_retries(self):
        import inspect

        from mesh import cli

        src = inspect.getsource(cli.record_event)
        self.assertNotIn("prompt_proof", src)
        self.assertFalse(hasattr(layers, "prompt_proof"))

    def test_a_store_api_event_without_a_candidate_record_is_not_a_candidate(self):
        ev = self.store.record_operator_event("approve /status", "t", "pm-uid")  # in-process fixture path
        self.assertNotIn(str(ev.seq), self.doc().event_proofs)


if __name__ == "__main__":
    import unittest

    unittest.main()
