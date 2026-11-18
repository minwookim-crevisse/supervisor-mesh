"""WO-0022 step 4 (W1): the process runner, the projmux adapter, production caller identity (D82) and the projmux facts
provider. projmux is a FAKE executable on PATH (tests/_mesh_fakes.py); the real one is never called."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from _helpers import ROOT
from _mesh_fakes import Fakes

sys.path.insert(0, str(ROOT))
from test_mesh_state import doc_of, item_in  # noqa: E402

from mesh import facts, identity, projmux, runner, tools  # noqa: E402
from mesh.model import (  # noqa: E402
    NO_FACTS,
    Attempt,
    Caller,
    Config,
    Fact,
    Facts,
    LeadAgent,
    NoArgs,
    Phase,
    Request,
    StartArgs,
    Verb,
)
from mesh.projmux import CwdRule  # noqa: E402


class RunnerTests(unittest.TestCase):
    def test_a_command_returns_its_exit_code_and_output(self):
        r = runner.run(
            [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"], timeout=10
        )
        self.assertEqual((r.code, r.out, r.err, r.spawn_error, r.timed_out), (3, "out\n", "err\n", False, False))
        self.assertFalse(r.ok)

    def test_a_missing_executable_is_a_spawn_error_result_not_an_exception(self):
        r = runner.run(["/nonexistent/mesh-no-such-tool"], timeout=5)
        self.assertTrue(r.spawn_error)
        self.assertIsNone(r.code)
        self.assertFalse(r.ok)

    def test_a_timeout_kills_the_whole_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "late"
            child = f"import time; time.sleep(3); open({str(marker)!r}, 'w').write('x')"
            code = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
            started = time.monotonic()
            r = runner.run([sys.executable, "-c", code], timeout=0.5)
            self.assertTrue(r.timed_out)
            self.assertLess(time.monotonic() - started, 10)
            time.sleep(3.5)
            self.assertFalse(marker.exists(), "the grandchild outlived the timeout")

    def test_argv_must_be_a_list_of_strings_never_a_shell_string(self):
        with self.assertRaises(TypeError):
            runner.run("echo hi", timeout=5)

    def test_env_and_cwd_are_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = runner.run(
                [sys.executable, "-c", "import os; print(os.getcwd(), os.environ.get('MESH_X'))"],
                timeout=10,
                cwd=tmp,
                env={"MESH_X": "y", "PATH": os.environ.get("PATH", "")},
            )
            self.assertEqual(r.out.split(), [os.path.realpath(tmp), "y"])


class FakesTests(unittest.TestCase):
    def test_the_fakes_answer_and_log_every_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fakes(Path(tmp))
            f.agent("a1", "backend", Path(tmp))
            r = runner.run(["projmux", "get", "agents", "-o", "json"], timeout=10, env=f.env())
            self.assertEqual(r.code, 0)
            self.assertEqual(f.calls("projmux"), [["get", "agents", "-o", "json"]])


class ProjmuxCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "proj"
        (self.root / "sub").mkdir(parents=True)
        self.fakes = Fakes(self.tmp / "fakes")
        self.pmx = projmux.Projmux(self.fakes.env())

    def tearDown(self):
        self._tmp.cleanup()


class ProjmuxParseTests(ProjmuxCase):
    def test_agents_parse_into_domain_values(self):
        self.fakes.agent("a1", "backend", self.root, pane="p1")
        self.fakes.agent("a2", None, self.tmp, phase="Offline", name="scratch")
        agents = self.pmx.agents()
        self.assertEqual(
            [(a.uid, a.name, a.role, a.cwd, a.phase, a.pane_uid) for a in agents],
            [
                ("a1", "agent-a1", "backend", str(self.root), "Running", "p1"),
                ("a2", "scratch", None, str(self.tmp), "Offline", "pane-a2"),
            ],
        )
        self.assertEqual(self.fakes.calls("projmux"), [["get", "agents", "-o", "json"]])

    def test_panes_parse_with_their_agent_and_tmux_id(self):
        self.fakes.agent("a1", "backend", self.root, pane="p1", tmux_id="%7")
        panes = self.pmx.panes()
        self.assertEqual([(p.uid, p.agent_uid, p.tmux_id) for p in panes], [("p1", "a1", "%7")])
        self.assertEqual(self.fakes.calls("projmux"), [["get", "panes", "-A", "-o", "json"]])

    def test_unusable_output_or_a_failing_projmux_is_an_error_not_a_guess(self):
        for raw in ("not json", ["a list"], {"items": "nope"}, {"items": [{"metadata": "x"}]}):
            self.fakes.state["agents_raw"] = raw
            self.fakes.save()
            with self.assertRaises(projmux.ProjmuxUnavailable, msg=raw):
                self.pmx.agents()
        del self.fakes.state["agents_raw"]
        self.fakes.state["exit"] = {"projmux get agents -o": 1}
        self.fakes.save()
        with self.assertRaises(projmux.ProjmuxUnavailable):
            self.pmx.agents()
        missing = projmux.Projmux({"PATH": str(self.tmp / "empty")})
        with self.assertRaises(projmux.ProjmuxUnavailable):
            missing.agents()


class LiveMemberRuleTests(ProjmuxCase):
    def test_one_rule_role_label_running_and_cwd_is_the_project_root(self):
        self.fakes.agent("ok", "backend", self.root)
        self.fakes.agent("offline", "backend", self.root, phase="Offline")
        self.fakes.agent("other-repo", "backend", self.tmp)
        self.fakes.agent("subdir", "backend", self.root / "sub")
        self.fakes.agent("unlabelled", None, self.root, name="role-backend")  # no name fallback (D82)
        self.fakes.agent("fe", "frontend", self.root)
        self.assertEqual([a.uid for a in self.pmx.live_members(self.root, role="backend")], ["ok"])
        self.assertEqual([a.uid for a in self.pmx.live_members(self.root)], ["ok", "fe"])

    def test_under_admits_a_subdirectory_of_the_root_and_nothing_outside_it(self):
        self.fakes.agent("ok", "backend", self.root)
        self.fakes.agent("subdir", "backend", self.root / "sub")
        self.fakes.agent("sibling", "backend", Path(str(self.root) + "-evil"))
        self.fakes.agent("parent", "backend", self.tmp)
        got = self.pmx.live_members(self.root, role="backend", cwd=CwdRule.UNDER)
        self.assertEqual([a.uid for a in got], ["ok", "subdir"])

    def test_cwd_is_compared_after_realpath(self):
        link = self.tmp / "link"
        link.symlink_to(self.root)
        self.fakes.agent("via-link", "backend", link)
        self.assertEqual([a.uid for a in self.pmx.live_members(self.root, role="backend")], ["via-link"])


class ProjmuxSendTests(ProjmuxCase):
    def test_send_runs_the_exact_argv_and_reports_delivery(self):
        sent = self.pmx.send("t-uid", "s-uid", "hello $(x) `y`", "mesh-WO-0001-d1")
        self.assertTrue(sent.delivered)
        self.assertEqual(
            self.fakes.calls("projmux", "agent", "message", "send"),
            [
                [
                    "agent",
                    "message",
                    "send",
                    "uid:t-uid",
                    "--source",
                    "uid:s-uid",
                    "--message-ref",
                    "mesh-WO-0001-d1",
                    "--",
                    "hello $(x) `y`",
                ]
            ],
        )

    def test_a_refused_receipt_is_not_delivered(self):
        self.fakes.state["send_code"] = 1
        self.fakes.save()
        self.assertFalse(self.pmx.send("t", "s", "x", "r").delivered)

    def test_interaction_kind_is_read_from_agent_status(self):
        self.fakes.agent("a1", "backend", self.root, interaction="approval_required")
        self.assertEqual(self.pmx.interaction("a1"), "approval_required")
        with self.assertRaises(projmux.ProjmuxUnavailable):  # F15: no answer is never read as idle
            self.pmx.interaction("nobody")


class IdentityTests(ProjmuxCase):
    """D82 in production: PMX_INTERNAL_ACTIVATION_PANE_UID -> pane -> agent -> labels, through the live-member rule."""

    def env(self, pane="pane-me", **extra):
        return self.fakes.env(PMX_INTERNAL_ACTIVATION_PANE_UID=pane, **extra) if pane else self.fakes.env(**extra)

    def resolve(self, pane="pane-me", **extra):
        return tools.caller_from_env(self.env(pane, **extra), self.root)()

    def test_the_pm_label_is_the_pm_and_any_other_role_label_is_a_lead(self):
        self.fakes.agent("me", "pm", self.root, pane="pane-me")
        self.assertEqual(self.resolve(), Caller.pm("me"))
        self.fakes.state["agents"][0]["metadata"]["labels"]["role"] = "backend"
        self.fakes.save()
        self.assertEqual(self.resolve(), Caller.lead("backend", "me"))  # per request: no stale answer

    def test_every_other_case_is_unknown(self):
        self.fakes.agent("me", None, self.root, pane="pane-me", name="role-pm")  # unlabelled; no name fallback
        self.fakes.agent("off", "pm", self.root, pane="pane-off", phase="Offline")
        self.fakes.agent("away", "pm", self.tmp, pane="pane-away")  # another repo
        self.fakes.agent("bad", "Bad Role", self.root, pane="pane-bad")
        self.fakes.state["panes"].append({"metadata": {"uid": "pane-shell"}, "status": {"activation": {}}})
        self.fakes.save()
        for pane in (None, "", "pane-me", "pane-off", "pane-away", "pane-bad", "pane-shell", "pane-nobody"):
            self.assertEqual(self.resolve(pane), Caller.unknown(), pane)

    def test_an_ambiguous_pane_or_a_failing_projmux_is_unknown(self):
        self.fakes.agent("me", "pm", self.root, pane="pane-me")
        self.fakes.state["panes"].append({"metadata": {"uid": "pane-me"}, "status": {"activation": {"agentUID": "x"}}})
        self.fakes.save()
        self.assertEqual(self.resolve(), Caller.unknown())
        self.fakes.state["panes"].pop()
        self.fakes.state["exit"] = {"projmux get panes -A": 1}
        self.fakes.save()
        self.assertEqual(self.resolve(), Caller.unknown())

    def test_no_environment_override_exists(self):  # F1: the old test seam has no effect
        self.fakes.agent("me", "backend", self.root, pane="pane-me")
        self.assertEqual(self.resolve(MESH_TEST_MODE="1", MESH_TEST_CALLER="pm:x"), Caller.lead("backend", "me"))
        self.assertEqual(
            self.fakes.calls("projmux")[-2:], [["get", "panes", "-A", "-o", "json"], ["get", "agents", "-o", "json"]]
        )

    def test_the_resolver_names_its_reason_without_echoing_env_values(self):
        why = identity.resolve(self.env("pane-nobody"), self.root, self.pmx)
        self.assertEqual(why.caller, Caller.unknown())
        self.assertNotIn("pane-nobody", why.reason)


class ProjmuxFactsTests(ProjmuxCase):
    """The production provider's projmux half: LEAD_AGENT (dispatch) and LEAD_LIVE (start, fail by the PM)."""

    def ask(self, needs, item, caller, verb=Verb.START, args=StartArgs()):
        provider = facts.projmux_facts(self.pmx, self.root)
        return provider(frozenset(needs), doc_of(item), Request(verb, caller, args, "t", item.id), Config())

    def test_lead_agent_is_the_one_live_member_of_the_items_role(self):
        self.fakes.agent("be", "backend", self.root)
        self.fakes.agent("fe", "frontend", self.root)
        self.fakes.agent("be-away", "backend", self.tmp)
        got = self.ask({Fact.LEAD_AGENT}, item_in(Phase.READY), Caller.pm("pm"), Verb.DISPATCH, NoArgs())
        self.assertEqual(got, Facts(lead_agent=LeadAgent("be")))

    def test_no_or_several_live_members_is_no_lead_agent(self):
        got = self.ask({Fact.LEAD_AGENT}, item_in(Phase.READY), Caller.pm("pm"), Verb.DISPATCH, NoArgs())
        self.assertEqual(got.lead_agent, None)
        self.fakes.agent("be1", "backend", self.root)
        self.fakes.agent("be2", "backend", self.root)
        got = self.ask({Fact.LEAD_AGENT}, item_in(Phase.READY), Caller.pm("pm"), Verb.DISPATCH, NoArgs())
        self.assertEqual(got.lead_agent, None)

    def test_lead_live_for_start_is_the_calling_lead_being_a_live_member_of_the_role(self):
        self.fakes.agent("be", "backend", self.root)
        item = item_in(Phase.READY)
        self.assertEqual(self.ask({Fact.LEAD_LIVE}, item, Caller.lead("backend", "be")), Facts(lead_live=True))
        self.assertEqual(self.ask({Fact.LEAD_LIVE}, item, Caller.lead("backend", "ghost")), Facts(lead_live=False))

    def test_lead_live_for_the_pm_is_the_attempts_lead(self):
        self.fakes.agent("be2", "backend", self.root)
        item = item_in(Phase.RUNNING, attempts=(Attempt(1, "be1", "b" * 40, None, 1, "h"),))
        self.assertEqual(self.ask({Fact.LEAD_LIVE}, item, Caller.pm("pm"), Verb.FAIL).lead_live, False)
        self.fakes.agent("be1", "backend", self.root)
        self.assertEqual(self.ask({Fact.LEAD_LIVE}, item, Caller.pm("pm"), Verb.FAIL).lead_live, True)

    def test_projmux_failing_leaves_the_fact_unavailable_and_unasked_facts_cost_nothing(self):
        self.fakes.state["exit"] = {"projmux get agents -o": 1}
        self.fakes.save()
        self.assertEqual(
            self.ask({Fact.LEAD_LIVE, Fact.LEAD_AGENT}, item_in(Phase.READY), Caller.lead("backend", "x")), NO_FACTS
        )
        before = len(self.fakes.calls("projmux"))
        self.assertEqual(self.ask({Fact.HEAD}, item_in(Phase.READY), Caller.lead("backend", "x")), NO_FACTS)
        self.assertEqual(len(self.fakes.calls("projmux")), before)

    def test_chain_merges_providers_field_by_field(self):
        a = lambda needs, doc, req, cfg: Facts(head="h")  # noqa: E731
        b = lambda needs, doc, req, cfg: Facts(lead_live=True)  # noqa: E731
        got = facts.chain(a, b)(frozenset(), doc_of(item_in(Phase.READY)), None, Config())
        self.assertEqual(got, Facts(head="h", lead_live=True))


if __name__ == "__main__":
    unittest.main()
