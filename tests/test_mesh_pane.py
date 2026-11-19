"""WO-0022 step 4 (W1): `pane_command` (D84, D85, D86, synthesis graft 1). The pure parts (command vetting, the D85 name
match, the screen classifier) on plain values; the guards and the keypress protocol through the tool layer with
projmux and tmux as FAKE executables on PATH. The real tmux is never driven."""

import json
import sys
import unittest

from _helpers import ROOT
from _mesh_fakes import BUSY_SCREEN, PERMISSION_SCREEN, RULE, TRUST_SCREEN, ready_screen

sys.path.insert(0, str(ROOT))
from test_mesh_state import item_in  # noqa: E402
from test_mesh_wire import WireCase  # noqa: E402

from mesh import pane, tmux, tools  # noqa: E402
from mesh.model import InvalidArgs, Phase  # noqa: E402


class VetCommandTests(unittest.TestCase):
    def test_one_line_starting_with_a_slash_is_accepted_unchanged(self):
        for text in ("/compact", "/mattpocock-skills:tdd add the parser", "/config \uc124\uc815"):
            self.assertEqual(pane.vet_command(text), text)

    def test_a_newline_or_control_or_format_character_is_refused(self):
        bad = ["/a\nb", "/a\rb", "/a\x00", "/a\x1b[2J", "/a\tb", "/a\x7f", "/a b", "/a b", "/a‮b", "/a​b", "/a\x85b"]
        for text in bad:
            with self.assertRaises(InvalidArgs, msg=repr(text)):
                pane.vet_command(text)

    def test_no_slash_an_empty_name_or_an_overlong_line_is_refused(self):
        for text in ("", "compact", " /compact", "/", "/ x", "/" + "a" * 600):
            with self.assertRaises(InvalidArgs, msg=repr(text)):
                pane.vet_command(text)

    def test_the_command_name_is_the_first_token_without_the_slash(self):
        self.assertEqual(pane.command_name("/mattpocock-skills:tdd add x"), "mattpocock-skills:tdd")
        self.assertEqual(pane.command_name("/compact"), "compact")


class NameMatchTests(unittest.TestCase):
    """D85 name path: a whole token, tolerant of trailing Korean particles."""

    def test_whole_token_case_one_a_prefix_or_a_longer_word_does_not_name_config(self):
        for text in ("run /c please", "configure it", "/configure", "reconfig", "/c", "config-ish"):
            self.assertFalse(pane.names(text, "config"), text)

    def test_whole_token_case_two_config_does_not_name_config_evil(self):
        for text in ("run /config", "/config now", "use config"):
            self.assertFalse(pane.names(text, "config-evil"), text)
        self.assertTrue(pane.names("run /config-evil", "config-evil"))

    def test_the_name_with_or_without_its_slash_and_with_trailing_korean_particles_names_it(self):
        for text in (
            "/config",
            "please run /config.",
            "config\ub97c \uc2e4\ud589\ud574",
            "/config\ub97c \uc5f4\uc5b4\uc918",
            "(/config)",
            "/config,",
        ):
            self.assertTrue(pane.names(text, "config"), text)

    def test_a_namespaced_command_is_named_only_by_its_full_name(self):
        self.assertTrue(pane.names("/mattpocock-skills:tdd \ub3cc\ub824", "mattpocock-skills:tdd"))
        self.assertFalse(pane.names("run tdd", "mattpocock-skills:tdd"))
        self.assertFalse(pane.names("/skills:tdd", "mattpocock-skills:tdd"))
        self.assertFalse(pane.names("a/config", "config"))  # part of a path


class ClassifyTests(unittest.TestCase):
    def test_an_empty_prompt_box_is_idle(self):
        self.assertIs(tmux.classify(ready_screen("● done").replace("{box}", "")), tmux.ScreenKind.IDLE)
        self.assertEqual(tmux.prompt_text(ready_screen().replace("{box}", "/compact")), "/compact")

    def test_a_typed_but_unsent_box_is_not_idle(self):
        self.assertIs(tmux.classify(ready_screen().replace("{box}", "half typed")), tmux.ScreenKind.DIRTY)

    def test_busy_trust_and_permission_screens_are_never_idle(self):
        self.assertIs(tmux.classify(BUSY_SCREEN.replace("{box}", "")), tmux.ScreenKind.BUSY)
        self.assertIs(tmux.classify(TRUST_SCREEN), tmux.ScreenKind.TRUST_PROMPT)
        self.assertIs(tmux.classify(PERMISSION_SCREEN), tmux.ScreenKind.PERMISSION_PROMPT)
        self.assertIs(tmux.classify("garbage\nscreen"), tmux.ScreenKind.UNKNOWN)

    def test_a_dialog_under_an_old_box_is_the_dialog(self):
        old = "\n".join(["", RULE, "❯ ", RULE, *PERMISSION_SCREEN.splitlines()])
        self.assertIs(tmux.classify(old), tmux.ScreenKind.PERMISSION_PROMPT)


class PaneCase(WireCase):
    def setUp(self):
        super().setUp()
        self.seed(item_in(Phase.RUNNING))
        self.pm_agent()
        self.lead = self.fakes.agent("be-uid", "backend", self.root, pane="pane-be", tmux_id="%1")
        self.fakes.set(popup="right")  # D113: a privileged command (/config) is confirmed in the PM pane's popup

    def event(self, text):
        return self.store.record_operator_event(text, "2026-11-18T00:00:00Z").id

    def pc(self, command, event_id=None, role="backend", caller=None, **kw):
        args = {"role": role, "command": command, **({"event_id": event_id} if event_id else {}), **kw}
        return tools.call(self.ctx(caller), "pane_command", args)

    def refused(self, result, reason):
        self.assertFalse(result.ok, result.data)
        self.assertEqual(
            (result.data["error"]["code"], result.data["error"]["reason"]), ("refused", reason), result.data
        )

    def keys(self, pane_id="%1"):
        return [a for a in self.fakes.calls("tmux") if "send-keys" in a and a[a.index("-t") + 1] == pane_id]

    def submitted(self, pane_id="%1"):
        return self.fakes.load()["tmux"][pane_id].get("submitted", [])

    def receipts(self):
        path = self.root / ".mesh" / "events.jsonl"
        rows = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
        return [r for r in rows if r["type"] == "receipt"]

    def screen(self, pane_id="%1", **changes):
        st = self.fakes.load()
        st["tmux"][pane_id].update(changes)
        self.fakes.save()


class PaneCommandRequiredTests(PaneCase):
    """The PM's required tests (brief W1, pane_command)."""

    def test_an_event_without_the_command_name_is_refused(self):
        ev = self.event("please tidy up the lead's context")
        self.refused(self.pc("/compact", ev), "event_not_bound")
        self.assertEqual((self.keys(), self.store.read().doc.event_uses), ([], {}))
        self.assertEqual(
            [(r["subject"], r["code"]) for r in self.store.refusals()], [("pane_command", "event_not_bound")]
        )

    def test_approval_of_a_proposal_is_accepted_once(self):
        proposed = self.pc("/compact keep the plan", propose=True)
        self.assertTrue(proposed.ok, proposed.data)
        self.assertEqual(self.keys(), [])  # a proposal types nothing
        no_id = self.event("\uc751 \uadf8\ub807\uac8c \ud574")
        self.refused(self.pc("/compact keep the plan", no_id), "event_not_bound")
        proposed = self.pc("/compact keep the plan", propose=True)  # D97: a prompt without the id approved nothing
        ev = self.event(f"{proposed.data['proposal']['id']} \uc751 \uadf8\ub807\uac8c \ud574")  # D97: names the id
        done = self.pc("/compact keep the plan", ev)
        self.assertTrue(done.ok, done.data)
        self.assertEqual(done.data["receipt"]["via"], "proposal")
        self.refused(self.pc("/compact keep the plan", ev), "event_reused")
        self.refused(self.pc("/compact keep the plan", self.event("yes again")), "event_not_bound")
        self.assertEqual(self.submitted(), ["/compact keep the plan"])

    def test_a_receipt_id_is_rejected_as_an_operator_event(self):
        done = self.pc("/compact", self.event("run /compact"))
        self.assertTrue(done.ok, done.data)
        self.refused(self.pc("/compact", done.data["receipt"]["id"]), "event_unknown")
        self.store.append_receipt({"id": "ev-000099", "seq": 99, "type": "operator", "text": "/compact"})
        self.refused(self.pc("/compact", "ev-000099"), "event_unknown")  # a receipt line never becomes an event
        self.assertEqual(self.submitted(), ["/compact"])

    def test_a_newline_or_control_character_is_refused(self):
        ev = self.event("run /compact")
        for text in ("/compact\n/evil", "/compact\r", "/compact\x1b[A", "/compact\u2028x", "/compact\u202e"):
            self.refused(self.pc(text, ev), "command_invalid")
        self.assertEqual(self.keys(), [])
        self.assertEqual(self.store.read().doc.event_uses, {})

    def test_a_target_outside_the_project_or_the_pms_own_pane_is_refused(self):
        ev = self.event("run /compact on qa, and pm")
        self.fakes.agent("qa-away", "qa", self.tmp, pane="pane-qa", tmux_id="%2")  # another repo
        self.refused(self.pc("/compact", ev, role="qa"), "target_invalid")
        self.refused(self.pc("/compact", ev, role="pm"), "target_invalid")
        st = self.fakes.load()  # the PM's own agent and pane, labelled as the lead
        st["agents"] = [a for a in st["agents"] if a["metadata"]["uid"] != "be-uid"]
        st["agents"][0]["metadata"]["labels"]["role"] = "backend"
        self.fakes.save()
        self.refused(self.pc("/compact", ev), "target_invalid")
        self.fakes.agent("other-uid", "backend", self.root, pane="pane-pm-uid", tmux_id="%3")  # the caller's pane
        self.refused(self.pc("/compact", ev), "target_invalid")
        self.assertEqual(self.keys("%0") + self.keys("%2") + self.keys("%3"), [])

    def test_a_non_idle_pane_or_a_trust_or_permission_prompt_is_refused(self):
        from _mesh_fakes import BUSY_SCREEN

        ev = self.event("run /compact")
        for screen in (BUSY_SCREEN, TRUST_SCREEN, PERMISSION_SCREEN, ready_screen().replace("{box}", "draft")):
            self.screen(screen=screen)
            self.refused(self.pc("/compact", ev), "pane_not_idle")
        self.screen(screen=ready_screen())
        for kind in ("approval_required", "input_required", "in_progress"):
            self.fakes.set(interaction={"be-uid": kind})
            self.refused(self.pc("/compact", ev), "pane_not_idle")
        self.assertEqual(self.keys(), [])
        self.assertEqual(self.store.read().doc.event_uses, {})

    def test_the_two_whole_token_cases(self):
        for text, command in (
            ("/c \ud574\uc918", "/config"),
            ("configure it", "/config"),
            ("run /config", "/config-evil"),
        ):
            self.refused(self.pc(command, self.event(text)), "event_not_bound")
        self.assertEqual(self.keys(), [])
        done = self.pc("/config", self.event("/config\ub97c \uc5f4\uc5b4\uc918"))
        self.assertTrue(done.ok, done.data)
        self.assertEqual(done.data["receipt"]["via"], "name")

    def test_a_rewritten_proposal_or_a_later_prompt_is_refused(self):
        self.pc("/compact keep the plan", propose=True)
        self.refused(self.pc("/compact drop the plan", self.event("ok")), "event_not_bound")  # rewritten text
        self.pc("/compact keep the plan", propose=True)
        self.event("hmm, wait")
        later = self.event("ok now")
        self.refused(self.pc("/compact keep the plan", later), "event_not_bound")  # not the very next prompt
        self.assertEqual(self.keys(), [])


class PaneCommandProtocolTests(PaneCase):
    def test_the_keypress_protocol_types_literally_confirms_then_presses_enter_and_leaves_receipts(self):
        ev = self.event("please run /mattpocock-skills:tdd \uc9c0\uae08")
        result = self.pc("/mattpocock-skills:tdd add the parser", ev, item_id="WO-0001")
        self.assertTrue(result.ok, result.data)
        tmux_calls = [a for a in self.fakes.calls("tmux") if a[a.index("-t") + 1] == "%1"]
        self.assertEqual(
            [a[2] for a in tmux_calls], ["capture-pane", "capture-pane", "send-keys", "capture-pane", "send-keys"]
        )
        self.assertEqual(
            tmux_calls[2],
            ["-L", "projmux", "send-keys", "-t", "%1", "-l", "--", "/mattpocock-skills:tdd add the parser"],
        )
        self.assertEqual(tmux_calls[4], ["-L", "projmux", "send-keys", "-t", "%1", "Enter"])
        self.assertEqual(self.submitted(), ["/mattpocock-skills:tdd add the parser"])
        use = self.store.read().doc.event_uses[ev]
        self.assertEqual((use.kind, use.ref, use.status), ("pane_command", "be-uid", "done"))
        (receipt,) = self.receipts()
        self.assertEqual(
            {k: receipt[k] for k in ("event_id", "target_uid", "command", "outcome", "item_id")},
            {
                "event_id": ev,
                "target_uid": "be-uid",
                "command": "/mattpocock-skills:tdd add the parser",
                "outcome": "done",
                "item_id": "WO-0001",
            },
        )
        self.assertEqual(result.data["receipt"]["id"], receipt["id"])
        entry = self.store.read().doc.items["WO-0001"].history[-1]
        self.assertEqual((entry.verb, entry.row, entry.actor.value), (None, "pane_command", "pm"))
        self.assertIn(receipt["id"], entry.note)
        (message,) = self.fakes.load()["messages"]
        self.assertEqual(message["args"][:6], ["agent", "message", "send", "uid:be-uid", "--source", "uid:pm-uid"])
        self.assertIn("operator-instructed", message["args"][-1])
        self.assertTrue(result.data["receipt"]["lead_notified"])

    def test_text_that_does_not_reach_the_prompt_is_never_submitted(self):
        (self.root / ".mesh" / "config.toml").write_text("[pane_command]\nrender_wait_s = 0.2\n")  # D125: short poll
        self.screen(swallow=True)
        ev = self.event("run /compact")
        result = self.pc("/compact", ev)
        self.assertEqual(result.data["error"]["code"], "pane_aborted")
        self.assertNotIn(["-L", "projmux", "send-keys", "-t", "%1", "Enter"], self.fakes.calls("tmux"))
        self.assertEqual(self.store.read().doc.event_uses[ev].status, "aborted")
        self.assertEqual(self.receipts()[0]["outcome"], "aborted_text_not_on_prompt")
        self.refused(self.pc("/compact", ev), "event_reused")  # at most once: never retried

    def test_the_pane_is_classified_again_after_the_event_is_consumed(self):
        from _mesh_fakes import BUSY_SCREEN

        self.screen(next_screens=[ready_screen(), BUSY_SCREEN])  # idle at the guard, busy at the re-check
        ev = self.event("run /compact")
        result = self.pc("/compact", ev)
        self.assertEqual(result.data["error"]["code"], "pane_aborted")
        self.assertEqual(self.keys(), [])
        self.assertEqual(self.receipts()[0]["outcome"], "aborted_not_idle")

    def test_a_crash_after_attempting_is_marked_unknown_on_recovery_and_never_replayed(self):
        ev = self.event("run /compact")
        original = tmux.Tmux.type_literal

        def crash(self_, pane_id, text):
            raise SystemExit("killed mid-keypress")

        tmux.Tmux.type_literal = crash
        try:
            with self.assertRaises(SystemExit):
                tools.pane_command(self.ctx(), tools.PaneCommandArgs("backend", "/compact", ev))
        finally:
            tmux.Tmux.type_literal = original
        self.assertEqual(self.store.read().doc.event_uses[ev].status, "attempting")
        self.assertEqual(pane.recover(self.store), [ev])
        self.assertEqual(self.store.read().doc.event_uses[ev].status, "unknown")
        self.refused(self.pc("/compact", ev), "event_reused")
        self.assertEqual(self.submitted(), [])

    def test_a_stale_event_a_lead_caller_and_a_missing_event_are_refused(self):
        old = self.event("run /compact")
        self.event("something newer")
        self.refused(self.pc("/compact", old), "event_stale")
        self.refused(self.pc("/compact", self.event("run /compact"), caller="lead:backend:be-uid"), "caller_not_pm")
        self.refused(self.pc("/compact"), "event_not_bound")
        self.refused(self.pc("/compact", "ev-424242"), "event_unknown")
        self.assertEqual(self.keys(), [])


class FakeClock:
    """D125: a monotonic clock that only `sleep` advances (no real waiting)."""

    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class RenderWaitTests(PaneCase):
    """D125: after typing, poll the capture (100 ms steps, up to render_wait_s) until the box holds the text."""

    def direct(self, command, wait=3.0):
        ctx, clock = self.ctx(), FakeClock()
        pc = pane.PaneCommand(
            ctx.store, ctx.pmx, ctx.tmux, ctx.root, None, ctx.clock(), render_wait_s=wait, now=clock.now,
            sleep=clock.sleep,
        )  # fmt: skip
        ev = self.event(f"run {command}")
        try:
            return pc.send(ctx.caller(), "backend", command, ev, None), clock
        except pane.PaneAborted as exc:
            return exc, clock

    def typed(self):
        return [a for a in self.keys() if "-l" in a]

    def enters(self):
        return [a for a in self.keys() if a[-1] == "Enter"]

    def clears(self):
        return [a for a in self.keys() if a[-1] == "C-u"]

    def test_the_box_fills_after_several_captures_then_enter_is_pressed_once(self):
        self.screen(render_after=4)
        result, clock = self.direct("/compact")
        self.assertIsInstance(result, dict, result)
        self.assertEqual(self.receipts()[0]["outcome"], "done")
        self.assertEqual((len(self.typed()), len(self.enters()), self.clears()), (1, 1, []))
        self.assertEqual(self.submitted(), ["/compact"])
        self.assertEqual(clock.sleeps, [0.1] * 4)  # five captures after typing: four waits of 100 ms

    def test_a_box_that_never_fills_is_cleared_and_aborted_within_render_wait_s(self):
        self.screen(swallow=True)
        result, clock = self.direct("/compact", wait=2.0)
        self.assertIsInstance(result, pane.PaneAborted)
        self.assertEqual(self.receipts()[0]["outcome"], "aborted_text_not_on_prompt")
        self.assertEqual((len(self.typed()), self.enters(), len(self.clears())), (1, [], 1))
        self.assertTrue(set(clock.sleeps) <= {0.1}, clock.sleeps)
        self.assertGreaterEqual(clock.t, 2.0 - 1e-9)
        self.assertLessEqual(clock.t, 2.0 + 0.1 + 1e-9)  # bounded by render_wait_s (one step of slack)

    def test_a_box_that_fills_with_other_text_is_never_entered(self):
        other = ready_screen().replace("{box}", "something else entirely")
        self.screen(swallow=True, next_screens=[ready_screen(), ready_screen(), other])
        result, clock = self.direct("/compact", wait=1.0)
        self.assertIsInstance(result, pane.PaneAborted)
        self.assertEqual(self.receipts()[0]["outcome"], "aborted_text_not_on_prompt")
        self.assertEqual((len(self.typed()), self.enters(), len(self.clears())), (1, [], 1))
        self.assertLessEqual(clock.t, 1.0 + 0.1 + 1e-9)

    def test_a_tmux_failure_while_polling_clears_and_aborts(self):
        self.screen(render_after=2)
        ctx, clock = self.ctx(), FakeClock()
        calls = {"n": 0}
        original = tmux.Tmux.capture

        def flaky(self_, pane_id, lines=60):
            calls["n"] += 1
            if calls["n"] == 4:  # guard, re-check, first poll ok; the second poll fails
                raise tmux.TmuxFailed("tmux capture-pane failed (exit 1)")
            return original(self_, pane_id, lines)

        tmux.Tmux.capture = flaky
        try:
            pc = pane.PaneCommand(
                ctx.store, ctx.pmx, ctx.tmux, ctx.root, None, ctx.clock(), render_wait_s=3, now=clock.now,
                sleep=clock.sleep,
            )  # fmt: skip
            with self.assertRaises(pane.PaneAborted):
                pc.send(ctx.caller(), "backend", "/compact", self.event("run /compact"), None)
        finally:
            tmux.Tmux.capture = original
        self.assertEqual(self.receipts()[0]["outcome"], "aborted_tmux_failed")
        self.assertEqual((len(self.typed()), self.enters()), (1, []))

    def test_the_tool_reads_render_wait_s_from_the_config(self):
        (self.root / ".mesh" / "config.toml").write_text("[pane_command]\nrender_wait_s = 1\n")
        self.screen(swallow=True)
        result = self.pc("/compact", self.event("run /compact"))
        self.assertEqual(result.data["error"]["code"], "pane_aborted")
        polls = [a for a in self.fakes.calls("tmux") if a[2] == "capture-pane"]
        self.assertGreater(len(polls), 4)  # guard, re-check, more than one poll, the clear's capture
        self.assertLessEqual(len(polls), 2 + 11 + 1)  # at most 1 s / 0.1 s + 1 polls


class RenderWaitConfigTests(unittest.TestCase):
    def test_render_wait_s_is_parsed_with_a_default_of_three(self):
        from mesh import defaults, store

        self.assertEqual(store.parse_config("").pane_render_wait_s, 3)
        self.assertEqual(store.parse_config("[pane_command]\nrender_wait_s = 1.5\n").pane_render_wait_s, 1.5)
        self.assertEqual(store.parse_config("[pane_command]\nrender_wait_s = 30\n").pane_render_wait_s, 30)
        self.assertEqual(store.parse_config(defaults.config_toml("ts")).pane_render_wait_s, 3)
        self.assertIn("# [pane_command]\n# render_wait_s = 3", defaults.config_toml(None))

    def test_invalid_render_wait_s_values_are_refused(self):
        from mesh import store

        for value in ("0", "-1", "true", "false", '"3"', "31", "1e9", "nan", "inf"):
            with self.assertRaises(InvalidArgs, msg=value):
                store.parse_config(f"[pane_command]\nrender_wait_s = {value}\n")


if __name__ == "__main__":
    unittest.main()
