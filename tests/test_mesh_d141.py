"""WO-0049 / D141 (hotfix 0.2.2, amends D85, D97, D123): pane_command approvals that work.

F4 an idle pane may show only a faint suggestion (a second `-e` capture decides it), refusal detail (the failing rule),
F2 a human `queued_command` attachment is a real prompt, F1 a human slash prompt in tag form compares as `/name args`,
F3 a P-id approves the NAMED unused proposal within `proposal_ttl_s`. Fakes only: never the real tmux or projmux, and
the transcripts are synthetic."""

import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from _helpers import ROOT
from _mesh_fakes import ready_screen
from _pane_fixtures import (
    CARRIED_STYLE_STYLED,
    FAINT_BOLD_STYLED,
    INVERSE_FIRST_STYLED,
    MIXED_STYLED,
    OTHER_ESCAPE_STYLED,
    SUGGESTION_PLAIN,
    SUGGESTION_STYLED,
    TYPED_STYLED,
    WORDS,
    plain,
)

sys.path.insert(0, str(ROOT))
from test_mesh_d123 import D123Case, entry, ts  # noqa: E402
from test_mesh_pane import PaneCase  # noqa: E402

from mesh import identity, projmux, tmux, tools  # noqa: E402
from mesh.model import Caller, InvalidArgs, Proposal, from_json  # noqa: E402


# ---------------------------------------------------------------- F4: the faint suggestion
class SuggestionClassifyTests(unittest.TestCase):
    def test_the_real_capture_is_dirty_on_the_plain_screen_and_a_suggestion_on_the_styled_one(self):
        self.assertIs(tmux.classify(SUGGESTION_PLAIN), tmux.ScreenKind.DIRTY)  # classify on the plain capture
        self.assertEqual(tmux.prompt_text(SUGGESTION_PLAIN), WORDS)
        self.assertTrue(tmux.suggestion_only(SUGGESTION_STYLED, WORDS))

    def test_typed_inverse_first_cell_mixed_or_other_styling_is_not_a_suggestion(self):
        for styled in (TYPED_STYLED, INVERSE_FIRST_STYLED, MIXED_STYLED, OTHER_ESCAPE_STYLED, FAINT_BOLD_STYLED,
                       CARRIED_STYLE_STYLED):  # fmt: skip
            self.assertEqual(tmux.prompt_text(plain(styled)), WORDS)
            self.assertFalse(tmux.suggestion_only(styled, WORDS), repr(styled))

    def test_the_styled_box_must_hold_the_same_text_as_the_plain_capture(self):
        self.assertFalse(tmux.suggestion_only(SUGGESTION_STYLED, "other words"))
        self.assertFalse(tmux.suggestion_only("garbage\nscreen", WORDS))
        self.assertFalse(tmux.suggestion_only(SUGGESTION_STYLED.replace(WORDS, ""), ""))  # an empty body: no cells

    def test_holds_still_checks_the_exact_typed_text(self):
        self.assertFalse(tmux.holds(SUGGESTION_PLAIN, "/compact"))
        self.assertTrue(tmux.holds(SUGGESTION_PLAIN.replace(WORDS, "/compact"), "/compact"))

    def test_the_try_placeholder_rule_stays(self):
        self.assertIs(tmux.classify(ready_screen().replace("{box}", 'Try "fix the bug"')), tmux.ScreenKind.IDLE)


class SuggestionPaneTests(PaneCase):
    def suggest(self, styled_line_body=f"\x1b[2m{WORDS}\x1b[0m"):
        self.screen(suggest=WORDS, suggest_styled=styled_line_body)

    def test_a_pane_showing_only_a_faint_suggestion_takes_the_command(self):
        self.suggest()
        done = self.pc("/compact", self.event("run /compact"))
        self.assertTrue(done.ok, done.data)
        self.assertEqual(self.submitted(), ["/compact"])
        styled = [a for a in self.fakes.calls("tmux") if a[2] == "capture-pane" and "-e" in a]
        self.assertEqual(len(styled), 2)  # the guard and the re-check; the D125 polls use the plain capture
        self.assertEqual(styled[0], ["-L", "projmux", "capture-pane", "-e", "-p", "-J", "-t", "%1", "-S", "-60"])

    def test_a_suggestion_that_is_not_all_faint_is_refused_as_not_idle(self):
        self.suggest(f"\x1b[7m{WORDS[0]}\x1b[27m{WORDS[1:]}")
        self.refused(self.pc("/compact", self.event("run /compact")), "pane_not_idle")
        self.assertEqual((self.keys(), self.store.read().doc.event_uses), ([], {}))

    def test_an_empty_idle_box_needs_no_styled_capture(self):
        done = self.pc("/compact", self.event("run /compact"))
        self.assertTrue(done.ok, done.data)
        self.assertEqual([a for a in self.fakes.calls("tmux") if "-e" in a], [])


# ---------------------------------------------------------------- refusal detail: the failing rule
class RuleAssertions:
    def assert_rule(self, result, reason, rule, *secret):
        """Refused with `reason` and `rule` (in the error and in the message), recorded with the rule, and none of
        the `secret` texts (prompt text) in the message or the refusal log."""
        self.assertFalse(result.ok, result.data)
        err = result.data["error"]
        self.assertEqual((err["code"], err["reason"], err.get("rule")), ("refused", reason, rule), err)
        self.assertIn(f"rule: {rule}", err["message"])
        last = self.store.refusals()[-1]
        self.assertEqual((last["code"], last["rule"]), (reason, rule))
        for text in secret:
            self.assertNotIn(text, err["message"])
            self.assertNotIn(text, json.dumps(self.store.refusals()))


class ProofRuleTests(D123Case, RuleAssertions):
    def pc(self, command, event_id):
        self.fakes.set(popup="none")
        return tools.call(self.ctx(), "pane_command", {"role": "backend", "command": command, "event_id": event_id})

    def test_a_prompt_that_never_reached_the_transcript_names_prompt_not_in_transcript(self):
        ev = self.say("approve /status zebra-41")
        self.assert_rule(self.pc("/status", ev), "event_unproven", "prompt_not_in_transcript", "zebra-41")

    def test_an_old_identical_entry_names_prompt_too_old(self):
        self.transcript("s-pm", entry("approve /status zebra-42", "u-old", at=ts(-3600)))
        ev = self.say("approve /status zebra-42")
        self.assert_rule(self.pc("/status", ev), "event_unproven", "prompt_too_old", "zebra-42")

    def test_an_older_candidate_names_newer_candidate(self):
        old = self.say("approve /status zebra-43", "u-1")
        self.say("something newer", "u-2")
        self.say("approve /status zebra-43", "u-3")  # the same text again: the latest entry equals the old one's
        result = self.pc("/status", old)  # the proof fails first (a newer candidate), before freshness
        self.assert_rule(result, "event_unproven", "newer_candidate", "zebra-43", "something newer")


class BindingRuleTests(PaneCase, RuleAssertions):
    def test_an_event_that_names_nothing_names_not_named(self):
        ev = self.event("please tidy up zebra-44")
        self.assert_rule(self.pc("/compact", ev), "event_not_bound", "not_named", "zebra-44")

    def test_a_stale_event_names_stale(self):
        old = self.event("run /compact zebra-45")
        self.event("newer zebra-46")
        self.assert_rule(self.pc("/compact", old), "event_stale", "stale", "zebra-45", "zebra-46")

    def test_a_used_proposal_names_proposal_used(self):
        pid = self.pc("/compact keep", propose=True).data["proposal"]["id"]
        self.assertTrue(self.pc("/compact keep", self.event(f"{pid} ok")).ok)
        again = self.event(f"{pid} zebra-47 again")
        self.assert_rule(self.pc("/compact keep", again), "event_not_bound", "proposal_used", "zebra-47")

    def test_a_p_id_typed_before_its_proposal_names_not_first_after_proposal(self):
        seq = self.store.read().doc.seq
        ev = self.event(f"P-{seq + 2} zebra-48")  # the event takes seq+1, the proposal seq+2
        self.assertEqual(self.pc("/compact keep", propose=True).data["proposal"]["id"], f"P-{seq + 2}")
        self.assert_rule(self.pc("/compact keep", ev), "event_not_bound", "not_first_after_proposal", "zebra-48")

    def test_a_refusal_without_a_rule_has_no_rule_field(self):
        result = self.pc("/compact", self.event("run /compact"), role="pm")  # target_invalid: no rule
        self.assertNotIn("rule", result.data["error"])
        self.assertIsNone(self.store.refusals()[-1]["rule"])


# ---------------------------------------------------------------- refusal detail: why the caller is not the PM
NOT_A_PID = 9_999_991  # above pid_max: never an ancestor (D108)
NEW = {"goal": "g", "done_criteria": ["x"], "role": "backend", "type": "feature"}
PANES = ("pane-nobody", "pane-pm-uid", "pane-be", "pane-pm2")  # never logged
UIDS = ("pm-uid", "be-uid", "pm2-uid")  # the refusal log's actor_uid already records the caller's (unchanged)
SECRETS = PANES + UIDS


class CallerRuleTests(PaneCase, RuleAssertions):
    """Every caller_not_pm refusal names WHY the caller is not the PM (a fixed code): the resolve case, the D108
    ancestry check, the D109 pm_gate case, a lead, or an injected UNKNOWN."""

    def assert_rule(self, result, reason, rule, *secret):
        """As RuleAssertions, with the pane uids kept out of the log too; agent uids stay out of the message and the
        rule (the log's actor_uid field records the caller as before)."""
        super().assert_rule(result, reason, rule, *[s for s in secret if s not in UIDS])
        for text in (*UIDS, str(self.root), str(self.home)):
            self.assertNotIn(text, result.data["error"]["message"])
            self.assertNotIn(text, rule)

    def env(self, pane):
        base = {"PATH": "/usr/bin:/bin", "HOME": str(self.home)}
        return self.fakes.env(base, **({"PMX_INTERNAL_ACTIVATION_PANE_UID": pane} if pane else {}))

    def pane_ctx(self, pane):  # the production resolver (identity.PaneCaller), never an injected caller
        return tools.Context.for_project(self.root, self.env(pane), clock=lambda: "2026-11-18T00:00:00Z")

    def resolve(self, pane):
        return identity.resolve(self.env(pane), self.root, projmux.Projmux(self.env(pane)))

    def assert_unknown(self, pane, rule):
        r = self.resolve(pane)
        self.assertEqual((r.caller, r.caller.rule), (Caller.unknown(), rule), pane)  # who is accepted: unchanged
        for secret in (*SECRETS, str(self.root), str(self.home)):
            self.assertNotIn(secret, r.caller.rule)

    def set_pid(self, pane_uid, pid):
        for p in self.fakes.state["panes"]:
            if p["metadata"]["uid"] == pane_uid:
                p["status"]["activation"]["claude"]["process"]["pid"] = pid
        self.fakes.save()

    def pin(self, uid):
        with self.store.locked() as txn:
            txn.commit(replace(txn.doc, pm_uid=uid))

    # -- identity.resolve: one fixed rule per UNKNOWN case
    def test_no_pane_env_var_is_no_pane_env(self):
        self.assert_unknown(None, "no_pane_env")
        self.assert_unknown("", "no_pane_env")

    def test_no_matching_pane_is_no_pane(self):
        self.assert_unknown("pane-nobody", "no_pane")

    def test_two_matching_panes_is_ambiguous_pane(self):
        self.fakes.state["panes"].append({"metadata": {"uid": "pane-be"}, "status": {"activation": {}}})
        self.fakes.save()
        self.assert_unknown("pane-be", "ambiguous_pane")

    def test_a_pane_without_an_agent_is_not_managed(self):
        self.fakes.state["panes"].append({"metadata": {"uid": "pane-shell"}, "status": {"activation": {}}})
        self.fakes.save()
        self.assert_unknown("pane-shell", "not_managed")

    def test_a_failing_projmux_is_projmux_unavailable(self):
        self.fakes.set(exit={"projmux get panes -A": 1})
        self.assert_unknown("pane-be", "projmux_unavailable")

    def test_an_agent_that_is_not_a_live_member_is_not_member(self):
        self.fakes.agent("off-uid", "qa", self.root, pane="pane-off", phase="Offline")
        self.fakes.agent("away-uid", "qa", self.tmp, pane="pane-away")  # another repo
        self.assert_unknown("pane-off", "not_member")
        self.assert_unknown("pane-away", "not_member")

    def test_an_invalid_role_label_is_invalid_role(self):
        self.fakes.agent("bad-uid", "Bad Role", self.root, pane="pane-bad")
        self.assert_unknown("pane-bad", "invalid_role")

    def test_a_resolved_pm_or_lead_carries_no_rule(self):
        self.assertIsNone(self.resolve("pane-pm-uid").caller.rule)
        self.assertIsNone(self.resolve("pane-be").caller.rule)

    # -- the rule reaches the refusal
    def test_pane_command_from_an_unresolved_pane_names_the_resolve_rule(self):
        result = tools.call(self.pane_ctx("pane-nobody"), "pane_command", {"role": "backend", "command": "/status"})
        self.assert_rule(result, "caller_not_pm", "no_pane", *SECRETS)
        self.assertIn("only the PM types into a lead's pane (D84)", result.data["error"]["message"])  # kept
        self.assertEqual((self.keys(), self.store.refusals()[-1]["subject"]), ([], "pane_command"))

    def test_a_pm_tool_from_an_unresolved_pane_names_the_resolve_rule(self):
        result = tools.call(self.pane_ctx(None), "tracker_render", {})
        self.assert_rule(result, "caller_not_pm", "no_pane_env", *SECRETS)
        self.assertIn("tracker_render is a PM tool", result.data["error"]["message"])  # kept

    def test_a_verb_from_an_unresolved_pane_names_the_resolve_rule(self):
        result = tools.call(self.pane_ctx("pane-nobody"), "work_list", {})  # read-only: still works
        self.assertTrue(result.ok, result.data)
        result = tools.call(self.pane_ctx("pane-nobody"), "work_new", NEW)
        self.assert_rule(result, "unknown_caller", "no_pane", *SECRETS)
        self.assertIn("no verbs", result.data["error"]["message"])  # kept

    def test_the_ancestry_check_is_not_descendant(self):
        self.set_pid("pane-pm-uid", NOT_A_PID)
        result = tools.call(self.pane_ctx("pane-pm-uid"), "pane_command", {"role": "backend", "command": "/status"})
        self.assert_rule(result, "caller_not_pm", "not_descendant", *SECRETS)
        self.assert_rule(tools.call(self.pane_ctx("pane-pm-uid"), "tracker_render", {}), "caller_not_pm",
                         "not_descendant", *SECRETS)  # fmt: skip

    def test_two_live_pms_is_pm_ambiguous(self):
        self.fakes.agent("pm2-uid", "pm", self.root, pane="pane-pm2")
        result = tools.call(self.pane_ctx("pane-pm-uid"), "pane_command", {"role": "backend", "command": "/status"})
        self.assert_rule(result, "caller_not_pm", "pm_ambiguous", *SECRETS)
        result = tools.call(self.pane_ctx("pane-pm-uid"), "tracker_render", {})
        self.assert_rule(result, "caller_not_pm", "pm_ambiguous", *SECRETS)
        self.assertIn("role=pm", result.data["error"]["message"])  # the D109 prose is kept

    def test_no_pin_is_pm_not_pinned(self):
        self.pin(None)
        result = tools.call(self.pane_ctx("pane-pm-uid"), "tracker_render", {})
        self.assert_rule(result, "caller_not_pm", "pm_not_pinned", *SECRETS)
        self.assertIn("mesh_init", result.data["error"]["message"])

    def test_another_pinned_pm_is_not_pinned_pm(self):
        self.pin("pm2-uid")
        result = tools.call(self.pane_ctx("pane-pm-uid"), "pane_command", {"role": "backend", "command": "/status"})
        self.assert_rule(result, "caller_not_pm", "not_pinned_pm", *SECRETS)

    def test_mesh_init_apply_by_another_pm_is_not_pinned_pm(self):
        self.pin("pm2-uid")
        ctx = self.pane_ctx("pane-pm-uid")
        plan = tools.call(ctx, "mesh_init", {"stack": "none"})
        self.assertTrue(plan.ok, plan.data)
        args = {"stack": "none", "apply": True, "plan_id": plan.data["plan_id"]}
        result = tools.call(self.pane_ctx("pane-pm-uid"), "mesh_init", args)
        self.assert_rule(result, "caller_not_pm", "not_pinned_pm", *SECRETS)
        self.assertEqual(self.store.read().doc.pm_uid, "pm2-uid")  # the pin is kept

    def test_a_lead_is_not_pm_role(self):
        result = tools.call(self.pane_ctx("pane-be"), "pane_command", {"role": "backend", "command": "/status"})
        self.assert_rule(result, "caller_not_pm", "not_pm_role", *SECRETS)
        self.assert_rule(tools.call(self.pane_ctx("pane-be"), "tracker_render", {}), "caller_not_pm", "not_pm_role",
                         *SECRETS)  # fmt: skip
        injected = self.pc("/status", caller="lead:backend:be-uid")
        self.assert_rule(injected, "caller_not_pm", "not_pm_role", *SECRETS)

    def test_an_injected_unknown_is_unknown_caller(self):
        self.assert_rule(self.pc("/status", caller="unknown"), "caller_not_pm", "unknown_caller", *SECRETS)
        result = tools.call(self.ctx("unknown"), "tracker_render", {})
        self.assert_rule(result, "caller_not_pm", "unknown_caller", *SECRETS)
        result = tools.call(self.ctx("unknown"), "work_new", NEW)
        self.assert_rule(result, "unknown_caller", "unknown_caller", *SECRETS)

    def test_the_genuine_pm_still_passes(self):
        result = tools.call(self.pane_ctx("pane-pm-uid"), "tracker_render", {})
        self.assertTrue(result.ok, result.data)


# ---------------------------------------------------------------- F2: a queued human prompt
def queued(text, uuid, at=None, entry_extra=None, **attachment):
    """A prompt typed while the PM was busy, as Claude Code writes it: an `attachment` entry whose attachment is a
    `queued_command` with the origin INSIDE the attachment (synthetic: no real transcript content)."""
    att = {"type": "queued_command", "prompt": text, "origin": {"kind": "human"}, "humanTurn": True,
           "commandMode": "prompt", "isMeta": False, **attachment}  # fmt: skip
    return {"type": "attachment", "uuid": uuid, "timestamp": at or ts(), "isSidechain": False, "attachment": att,
            **(entry_extra or {})}  # fmt: skip


QUEUED_NOT_REAL = {
    "queued peer": {"origin": {"kind": "peer", "from": "be-uid"}, "isMeta": True},
    "queued task-notification": {
        "origin": {"kind": "task-notification"},
        "humanTurn": False,
        "commandMode": "task-notification",
    },
    "humanTurn missing": {"humanTurn": None},
    "humanTurn a string": {"humanTurn": "true"},
    "commandMode bash": {"commandMode": "bash"},
    "isMeta true": {"isMeta": True},
    "prompt not a string": {"prompt": ["x"]},
    "no origin": {"origin": None},
    "another attachment type": {"type": "edited_text_file"},
}


class QueuedPromptTests(D123Case):
    def test_a_queued_human_prompt_backs_its_candidate(self):
        ev = self.say("approve /status while busy")
        self.transcript("s-pm", queued("approve /status while busy", "u-q1"))
        self.assert_bound(self.cite(ev))

    def test_every_other_queued_shape_is_not_a_real_prompt(self):
        for n, (name, change) in enumerate(QUEUED_NOT_REAL.items()):
            with self.subTest(name):
                text = f"approve /status q{n}"
                ev = self.say(text)
                e = queued(text, f"u-qn-{n}", **change)
                e["attachment"] = {k: v for k, v in e["attachment"].items() if v is not None}
                self.transcript("s-pm", e)
                self.assert_refused(self.cite(ev))

    def test_the_origin_must_be_inside_the_attachment_and_the_entry_not_a_sidechain(self):
        ev = self.say("approve /status outside")
        e = queued("approve /status outside", "u-qo", entry_extra={"origin": {"kind": "human"}})
        del e["attachment"]["origin"]
        self.transcript("s-pm", e)
        self.assert_refused(self.cite(ev))
        ev = self.say("approve /status side")
        self.transcript("s-pm", queued("approve /status side", "u-qs", entry_extra={"isSidechain": True}))
        self.assert_refused(self.cite(ev))

    def test_latest_is_by_file_order_not_by_timestamp(self):
        ev = self.say("approve /status first")
        self.transcript("s-pm", queued("approve /status first", "u-qa", at=ts(2)),
                        entry("thanks", "u-qb", at=ts(-1)))  # fmt: skip  # written later, stamped earlier
        self.assert_refused(self.cite(ev))
        ev = self.say("approve /status second")
        self.transcript("s-pm", entry("older words", "u-qc", at=ts(3)), queued("approve /status second", "u-qd"))
        self.assert_bound(self.cite(ev))

    def test_one_usable_event_per_queued_entry_uuid(self):
        hook = self.say("approve /status once")
        self.transcript("s-pm", queued("approve /status once", "u-q1"))
        self.assert_bound(self.cite(hook))
        bash = self.say("approve /status once")  # the same queued entry is still the latest prompt
        self.assert_refused(self.cite(bash), "event_reused")


class PidCaseTests(PaneCase):
    def test_a_lowercase_p_id_names_the_proposal(self):
        pid = self.pc("/compact keep", propose=True).data["proposal"]["id"]
        done = self.pc("/compact keep", self.event(f"{pid.lower()} ok"))
        self.assertTrue(done.ok, done.data)
        self.assertEqual(done.data["receipt"]["via"], "proposal")

    def test_command_name_matching_stays_case_sensitive(self):
        result = self.pc("/compact", self.event("run /COMPACT"))
        err = result.data["error"]
        self.assertEqual((err["reason"], err["rule"]), ("event_not_bound", "not_named"))


# ---------------------------------------------------------------- F1: a slash prompt in tag form
def tag(name, args=None, message=None):
    """How Claude Code writes a typed slash command into the transcript (synthetic)."""
    body = f"<command-message>{message or name}</command-message>\n<command-name>/{name}</command-name>"
    return body + (f"\n<command-args>{args}</command-args>" if args is not None else "")


class SlashTagTests(D123Case):
    def cite_with(self, prompt, content, **extra):
        ev = self.say(prompt)
        self.transcript("s-pm", entry(content, f"u-{len(self.store.operator_events())}", **extra))
        return self.cite(ev)

    def test_the_tag_form_with_args_compares_as_name_and_args_whitespace_collapsed(self):
        self.assert_bound(self.cite_with("/ns:name  some   args", tag("ns:name", "some args")))
        running = tag("ns:name", "keep\n  it", message="ns:name is running")
        self.assert_bound(self.cite_with("/ns:name keep it", running))

    def test_the_tag_form_without_args_compares_as_the_bare_command(self):
        self.assert_bound(self.cite_with("/status", tag("status")))

    def test_other_args_another_name_or_a_hook_prompt_without_a_slash_is_refused(self):
        self.assert_refused(self.cite_with("/ns:name some args", tag("ns:name", "other args")))
        self.assert_refused(self.cite_with("/ns:other some args", tag("ns:name", "some args")))
        self.assert_refused(self.cite_with("ns:name some args", tag("ns:name", "some args")))

    def test_the_reversed_order_without_origin_or_extra_text_is_refused(self):
        reversed_tag = "<command-name>/status</command-name>\n<command-message>status</command-message>"
        ev = self.say("/status")
        e = entry(reversed_tag, "u-rev")
        del e["origin"]
        self.transcript("s-pm", e)
        self.assert_refused(self.cite(ev))
        self.assert_refused(self.cite_with("/status", reversed_tag))  # reversed with a human origin: no fullmatch
        self.assert_refused(self.cite_with("/status", tag("status") + "\nmore"))
        self.assert_refused(self.cite_with("/status", "x " + tag("status")))

    def test_a_tag_form_from_a_peer_stays_rejected(self):
        self.assert_refused(self.cite_with("/status", tag("status"), isMeta=True, origin={"kind": "peer"}))


class SentenceFormTextTests(unittest.TestCase):
    """F1: a bare `/skill` typed in the PM pane runs that skill in the PM, so the PM asks for the sentence form."""

    def test_the_tool_text_the_run_skill_and_the_pm_agent_ask_for_the_command_inside_a_sentence(self):
        texts = {
            "tool": tools.REGISTRY["pane_command"].description,
            "run skill": (ROOT / "skills" / "run" / "SKILL.md").read_text(),
            "pm agent": (ROOT / "agents" / "pm-orchestrator.md").read_text(),
        }
        for name, text in texts.items():
            with self.subTest(name):
                self.assertIn("inside a sentence", text)
                self.assertIn("`backend: /ns:name args`", text)


# ---------------------------------------------------------------- F3: a P-id approves the named proposal until expiry
T0 = datetime(2026, 11, 18, tzinfo=timezone.utc)  # the PaneCase clock: every proposal is stored at T0


def at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProposalUntilExpiryTests(PaneCase, RuleAssertions):
    def ev(self, text, seconds):
        return self.store.record_operator_event(text, at(seconds)).id

    def propose(self, command="/compact keep"):
        return self.pc(command, propose=True).data["proposal"]["id"]

    def test_a_proposal_is_stored_with_its_time(self):
        pid = self.propose()
        self.assertEqual(self.store.read().doc.proposals[pid].at, at(0))

    def test_a_p_id_after_another_prompt_within_the_ttl_approves(self):
        pid = self.propose()
        self.ev("hmm, let me think zebra-50", 10)
        done = self.pc("/compact keep", self.ev(f"{pid} ok", 599))
        self.assertTrue(done.ok, done.data)
        self.assertEqual(done.data["receipt"]["via"], "proposal")
        self.assertEqual(self.submitted(), ["/compact keep"])

    def test_a_p_id_past_the_ttl_is_refused_as_expired(self):
        pid = self.propose()
        self.ev("something else", 10)
        self.assert_rule(self.pc("/compact keep", self.ev(f"{pid} ok zebra-51", 601)), "event_not_bound",
                         "proposal_expired", "zebra-51")  # fmt: skip

    def test_the_ttl_comes_from_the_config(self):
        (self.root / ".mesh" / "config.toml").write_text("[pane_command]\nproposal_ttl_s = 120\n")
        pid = self.propose()
        self.assert_rule(self.pc("/compact keep", self.ev(f"{pid} ok", 121)), "event_not_bound", "proposal_expired")
        self.assertTrue(self.pc("/compact keep", self.ev(f"{pid} ok", 119)).ok)

    def test_a_proposal_without_at_is_expired(self):
        pid = self.propose()
        with self.store.locked() as txn:  # a proposal stored by 0.2.1 has no `at`
            prop = txn.doc.proposals[pid]
            txn.commit(replace(txn.doc, proposals={pid: replace(prop, at=None)}))
        self.assert_rule(self.pc("/compact keep", self.ev(f"{pid} ok", 1)), "event_not_bound", "proposal_expired")

    def test_a_state_file_proposal_without_the_key_loads_as_expired(self):
        data = {"id": "P-3", "seq": 3, "role": "backend", "command": "/compact", "by_uid": None, "used": False}
        self.assertIsNone(from_json(Proposal, data).at)

    def test_two_p_ids_or_the_same_id_twice_are_refused(self):
        a, b = self.propose(), self.propose("/compact other")
        self.assert_rule(self.pc("/compact keep", self.ev(f"{a} or {b} zebra-52", 1)), "event_not_bound",
                         "more_than_one_id", "zebra-52")  # fmt: skip
        self.assert_rule(self.pc("/compact keep", self.ev(f"{a} {a.lower()}", 2)), "event_not_bound",
                         "more_than_one_id")  # fmt: skip

    def test_a_prompt_longer_than_80_characters_is_refused(self):
        pid = self.propose()
        long = f"{pid} " + "x" * (80 - len(pid))
        self.assertEqual(len(long), 81)
        self.assert_rule(self.pc("/compact keep", self.ev(long, 1)), "event_not_bound", "prompt_too_long", long)
        self.assertTrue(self.pc("/compact keep", self.ev(long[:80], 2)).ok)

    def test_the_named_proposal_binds_not_the_latest(self):
        first, latest = self.propose("/compact keep"), self.propose("/compact other")
        done = self.pc("/compact keep", self.ev(f"{first} ok", 1))
        self.assertTrue(done.ok, done.data)
        props = self.store.read().doc.proposals
        self.assertEqual((props[first].used, props[latest].used), (True, False))
        self.assertEqual(self.submitted(), ["/compact keep"])

    def test_the_named_proposal_must_have_the_exact_role_and_text(self):
        pid = self.propose("/compact keep")
        self.assert_rule(self.pc("/compact other", self.ev(f"{pid} ok", 1)), "event_not_bound", "not_named")
        self.assert_rule(self.pc("/compact keep", self.ev("P-999 ok", 2)), "event_not_bound", "not_named")
        self.assertEqual(self.keys(), [])

    def test_a_use_expires_the_same_command_siblings(self):
        older, newer, other = self.propose(), self.propose(), self.propose("/compact other")
        self.assertTrue(self.pc("/compact keep", self.ev(f"{newer} ok", 1)).ok)
        props = self.store.read().doc.proposals
        self.assertEqual((props[older].used, props[newer].used, props[other].used), (True, True, False))
        self.assert_rule(self.pc("/compact keep", self.ev(f"{older} ok", 2)), "event_not_bound", "proposal_used")
        self.assertTrue(self.pc("/compact other", self.ev(f"{other} ok", 3)).ok)
        self.assertEqual(self.submitted(), ["/compact keep", "/compact other"])

    def test_freshness_still_holds(self):
        pid = self.propose()
        ev = self.ev(f"{pid} ok", 1)
        self.ev("a newer prompt", 2)
        self.assert_rule(self.pc("/compact keep", ev), "event_stale", "stale")

    def test_the_texts_no_longer_say_very_next(self):
        self.assertNotIn("very next", tools.REGISTRY["pane_command"].description)
        nxt = self.pc("/compact keep", propose=True).data["next"]
        self.assertNotIn("very next", nxt)
        self.assertIn("600", nxt)


class ProposalTtlConfigTests(unittest.TestCase):
    def test_proposal_ttl_s_is_parsed_with_a_default_of_600(self):
        from mesh import defaults, store

        self.assertEqual(store.parse_config("").pane_proposal_ttl_s, 600)
        self.assertEqual(store.parse_config("[pane_command]\nproposal_ttl_s = 90.5\n").pane_proposal_ttl_s, 90.5)
        self.assertEqual(store.parse_config("[pane_command]\nproposal_ttl_s = 3600\n").pane_proposal_ttl_s, 3600)
        self.assertEqual(store.parse_config(defaults.config_toml("ts")).pane_proposal_ttl_s, 600)
        self.assertIn("# render_wait_s = 15 ", defaults.config_toml(None))
        self.assertIn("# proposal_ttl_s = 600", defaults.config_toml(None))

    def test_invalid_proposal_ttl_s_values_are_refused(self):
        from mesh import store

        for value in ("0", "-1", "true", "false", '"600"', "3601", "1e9", "nan", "inf"):
            with self.assertRaises(InvalidArgs, msg=value):
                store.parse_config(f"[pane_command]\nproposal_ttl_s = {value}\n")

    def test_the_verify_hash_does_not_change_with_proposal_ttl_s(self):
        from mesh import defaults, store

        base = defaults.config_toml(None)
        before = store.parse_config(base).verify_hash()
        after = store.parse_config(base + "[pane_command]\nproposal_ttl_s = 60\n").verify_hash()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
