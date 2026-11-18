"""WO-0022 step 5, fix round W5: D91-D102 (spec rows) after the R1 fixes.

  D95   each cross-cutting concern is ONE layer (mesh/layers.py) applied to every tool
  D96   operator-event provenance: the hook's parent-process check, host deny rules written by mesh_init
  D97   a proposal is approved only by a prompt naming its id `P-<n>`
  D98   an override event names the item id
  D100  the verification config hash is pinned at `ready`, checked at `start` and `verify`
  D101  the PM rejects a verified item on its own review (reason + class)
  D91   verify.prepare        D92 fail_if_output        D93 dispatch retry        D94 overlap warning
  D102  the completion signal (skills) and mesh_status stall detection
projmux, tmux and claude are FAKE executables on PATH; git is real in temp repos."""

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path

from _helpers import ROOT
from _mesh_fakes import Fakes, parse_caller
from test_mesh_pane import PaneCase
from test_mesh_state import (
    BACKEND,
    CFG,
    LIVE,
    PM,
    override,
    ready_sim,
    running_sim,
    verified_sim,
)

sys.path.insert(0, str(ROOT))
from mesh import identity, init, layers, tools  # noqa: E402
from mesh.model import (  # noqa: E402
    Command,
    Config,
    EventFact,
    Facts,
    FailArgs,
    NoArgs,
    OperatorEvent,
    Phase,
    PrecheckOutcome,
    ReassignArgs,
    RefusalCode,
    RejectArgs,
    StartArgs,
    Verb,
)
from mesh.store import Store  # noqa: E402

CLI = ROOT / "mesh" / "cli.py"

MESH = ROOT / "mesh"
LAYER_FILE = "layers.py"


def _calls(tree: ast.AST) -> list[tuple[str, int]]:
    """(callee name, line) for every call: `f(...)` -> f, `x.y.f(...)` -> f."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
            if name:
                out.append((name, node.lineno))
    return out


def _function_bodies(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _modules():
    for path in sorted(MESH.glob("*.py")):
        yield path.name, ast.parse(path.read_text(encoding="utf-8"))


class D95OneImplementationPerConcernTests(unittest.TestCase):
    """Each concern is implemented once, in mesh/layers.py; every other module calls the layer."""

    # concern -> the calls only the layer (and the store that defines the primitive) may make
    ONLY_IN_LAYER = {
        "refusal recording": {"append_refusal"},
        "receipts / audit log": {"append_receipt"},
        "operator-event resolution": {"get_event"},
        "operator-event consumption": {"EventUse"},
        "retry / backoff": {"sleep"},
        "stdout claim": {"dup2"},
    }
    ALLOWED = {
        "append_refusal": {"store.py"},
        "append_receipt": {"store.py"},
        "get_event": {"store.py"},
        "sleep": {"store.py"},
    }  # store.py: the primitive's definition, and the lock's poll

    def test_each_concern_has_exactly_one_implementation(self):
        offenders = []
        for name, tree in _modules():
            if name == LAYER_FILE:
                continue
            for callee, line in _calls(tree):
                for concern, names in self.ONLY_IN_LAYER.items():
                    if callee in names and name not in self.ALLOWED.get(callee, set()):
                        offenders.append(f"{name}:{line} {callee} ({concern})")
        self.assertEqual(offenders, [])
        layer_calls = {c for c, _ in _calls(ast.parse((MESH / LAYER_FILE).read_text(encoding="utf-8")))}
        for concern, names in self.ONLY_IN_LAYER.items():
            self.assertTrue(names & layer_calls, f"the layer does not implement {concern}")

    def test_output_checks_are_one_function_the_runner_calls(self):
        """D92: only the layer matches patterns against a command's output (`.out` / `.err`); the runner calls it."""
        offenders, callers = [], set()
        for name, tree in _modules():
            if name == LAYER_FILE:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "output_failure":
                    offenders.append(f"{name}: defines output_failure")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr in ("search", "match", "fullmatch", "findall") and any(
                        isinstance(a, ast.Attribute) and a.attr in ("out", "err") for a in node.args
                    ):
                        offenders.append(f"{name}:{node.lineno} matches run output")
                    if node.func.attr == "output_failure":
                        callers.add(name)
        self.assertEqual(offenders, [])
        self.assertEqual(callers, {"verify.py"})

    def test_only_the_layer_writes_to_stderr_inside_a_function(self):
        offenders = []
        for name, tree in _modules():
            if name == LAYER_FILE:
                continue
            for fn in _function_bodies(tree):
                for node in ast.walk(fn):
                    if isinstance(node, ast.Attribute) and node.attr == "stderr" and _is_sys(node.value):
                        offenders.append(f"{name}:{node.lineno} sys.stderr in {fn.name}")
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                        offenders.append(f"{name}:{node.lineno} print in {fn.name}")
        self.assertEqual(offenders, [])

    def test_operator_binding_is_one_function_used_by_reply_override_pane_command_and_companions_install(self):
        users = set()
        for name, tree in _modules():
            if name == LAYER_FILE:
                continue
            if any(callee == "bind_event" for callee, _ in _calls(tree)):
                users.add(name)
        self.assertEqual(users, {"state.py", "pane.py", "tools.py"})
        state_src = (MESH / "state.py").read_text(encoding="utf-8")
        for guard in ("event_unconsumed", "event_fresh", "override_names_item"):
            self.assertIn(f"def {guard}", state_src)
        for name, tree in _modules():  # nobody re-implements the whole-token match or the freshness rule
            if name == LAYER_FILE:
                continue
            src = (MESH / name).read_text(encoding="utf-8")
            self.assertNotRegex(src, r"!=\s*[\w.]*last_operator_seq", name)
            self.assertNotIn("event_uses={**", src, name)

    def test_every_registry_tool_is_wrapped_by_the_layers(self):
        self.assertEqual(len(tools.REGISTRY), 11)
        for t in tools.REGISTRY.values():
            self.assertEqual(getattr(t.run, "__mesh_layers__", None), layers.TOOL_LAYERS, t.name)
        tree = ast.parse((MESH / "tools.py").read_text(encoding="utf-8"))
        self.assertNotIn("fn", {c for c, _ in _calls(tree)})  # `call` goes through `run`, never the bare fn

    def test_a_tool_result_names_no_internal_error_detail(self):
        boom = layers.tool_layer("x", tools.NoToolArgs, lambda ctx, a: 1 / 0)
        import contextlib
        import io

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = boom(_NullCtx(), {})
        self.assertEqual((out.ok, out.data["error"]["code"]), (False, "internal"))
        self.assertEqual(err.getvalue(), "supervisor-mesh: internal error ZeroDivisionError [E-TOOL]\n")


@dataclass
class _NullCtx:
    caller: object = lambda: None


def _is_sys(node) -> bool:
    return isinstance(node, ast.Name) and node.id == "sys"


# ================================================================ step 2: operator binding semantics
class D97ProposalApprovalNamesTheIdTests(PaneCase):
    def test_an_approval_must_name_the_proposal_id_as_a_whole_token(self):
        proposed = self.pc("/compact keep the plan", propose=True)
        pid = proposed.data["proposal"]["id"]
        self.assertRegex(pid, r"^P-\d+$")
        self.refused(self.pc("/compact keep the plan", self.event("yes, do it")), "event_not_bound")
        self.pc("/compact keep the plan", propose=True)
        pid = self.store.read().doc.proposals and max(self.store.read().doc.proposals.values(), key=lambda p: p.seq).id
        self.refused(self.pc("/compact keep the plan", self.event(f"ok {pid}0")), "event_not_bound")  # longer token
        self.pc("/compact keep the plan", propose=True)
        pid = max(self.store.read().doc.proposals.values(), key=lambda p: p.seq).id
        done = self.pc("/compact keep the plan", self.event(f"{pid} \uc2b9\uc778"))
        self.assertTrue(done.ok, done.data)
        self.assertEqual(done.data["receipt"]["via"], "proposal")


class D98OverrideNamesTheItemTests(unittest.TestCase):
    def facts(self, text, n=3):
        return Facts(operator_event=EventFact(OperatorEvent(f"ev-{n:06d}", n, "t", text, "pm-uid")))

    def test_an_override_event_must_name_the_item_id(self):
        sim = running_sim()
        out = override(sim, Phase.CANCELLED, facts=self.facts("cancel it"))
        self.assertEqual(out.refusal.code, RefusalCode.EVENT_NOT_BOUND)
        out = override(sim, Phase.CANCELLED, facts=self.facts("cancel WO-00012"))
        self.assertEqual(out.refusal.code, RefusalCode.EVENT_NOT_BOUND)
        override(sim, Phase.CANCELLED, facts=self.facts("WO-0001\uc744 \ucde8\uc18c\ud574"))
        self.assertEqual(sim.item().phase, Phase.CANCELLED)


CFG2 = Config(verify_commands=(Command(("make", "check-all"), "tests"),))


class D100ConfigHashPinnedAtReadyTests(unittest.TestCase):
    def test_ready_pins_the_hash_and_start_refuses_a_changed_config(self):
        sim = ready_sim()
        self.assertEqual(sim.item().pinned_config_hash, CFG.verify_hash())
        sim.cfg = CFG2
        out = sim.do(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)
        self.assertEqual(out.refusal.code, RefusalCode.INPUTS_CHANGED)
        sim.cfg = CFG
        sim.ok(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)
        self.assertEqual(sim.item().current_attempt.config_hash, CFG.verify_hash())

    def test_verify_refuses_a_pin_that_differs_from_the_attempt(self):
        from test_mesh_state import COMMIT, passing, verifying_sim

        sim = verifying_sim()
        item = sim.item()
        sim.doc = replace(sim.doc, items={item.id: replace(item, pinned_config_hash="0" * 64)})
        out = sim.do(Verb.VERIFY, BACKEND, __import__("mesh.model").model.VerifyArgs(1), "WO-0001", passing(COMMIT))
        self.assertEqual(out.refusal.code, RefusalCode.INPUTS_CHANGED)


class D106EveryReadyEntryRePinsTests(unittest.TestCase):
    """PM add-on (state model section 4 rows reassign / unblock, D106): every PM verb that enters `ready` pins."""

    def rework_sim(self):
        sim = running_sim()
        sim.ok(Verb.FAIL, BACKEND, FailArgs("defect", 1), "WO-0001")
        return sim

    def test_reassign_re_pins_and_a_start_after_the_config_change_is_accepted(self):
        sim = self.rework_sim()
        sim.cfg = CFG2  # a config-only change: no revise needed
        sim.ok(Verb.REASSIGN, PM, ReassignArgs(), "WO-0001")
        self.assertEqual(sim.item().pinned_config_hash, CFG2.verify_hash())
        sim.ok(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)
        self.assertEqual(sim.item().current_attempt.config_hash, CFG2.verify_hash())

    def test_unblock_re_pins_and_a_start_after_the_config_change_is_accepted(self):
        sim = running_sim()
        sim.ok(Verb.FAIL, BACKEND, FailArgs("environment", 1), "WO-0001")
        sim.cfg = CFG2
        sim.ok(Verb.UNBLOCK, PM, NoArgs(), "WO-0001", Facts(precheck=PrecheckOutcome(True)))
        self.assertEqual(sim.item().pinned_config_hash, CFG2.verify_hash())
        sim.ok(Verb.START, BACKEND, StartArgs(), "WO-0001", LIVE)

    def test_one_pin_helper_serves_ready_reassign_and_unblock(self):
        from mesh import state

        src = (MESH / "state.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("pinned_config_hash="), 1)  # the helper, nowhere else
        for effect in ("ready", "reassign", "unblock"):
            self.assertIn("_pin(", ast.get_source_segment(src, _fn(src, effect)), effect)
        self.assertTrue(callable(state._pin))


def _fn(src: str, name: str):
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == name)


class D101PmSelfReviewRejectTests(unittest.TestCase):
    def test_the_pm_rejects_a_verified_item_on_its_own_review_with_a_reason_and_a_class(self):
        routes = (("defect", Phase.REWORK), ("conflict", Phase.REWORK), ("ambiguity", Phase.BLOCKED_AWAITING_USER))
        for cls, phase in routes:
            sim = verified_sim()
            sim.ok(Verb.REJECT, PM, RejectArgs(cls, "evidence review: the log shows 0 tests ran"), "WO-0001")
            self.assertEqual((sim.item().phase, sim.item().failures[-1].actor), (phase, "pm"), cls)

    def test_without_a_reason_there_is_still_no_trigger(self):
        sim = verified_sim()
        for reason in ("", "   "):
            out = sim.do(Verb.REJECT, PM, RejectArgs("defect", reason), "WO-0001")
            self.assertEqual(out.refusal.code, RefusalCode.REJECT_NO_TRIGGER)


# ---------------------------------------------------------------- D96 provenance
class D96ProcAncestryTests(unittest.TestCase):
    def proc(self, tree):
        root = Path(self._tmp.name)
        for pid, (ppid, comm) in tree.items():
            d = root / str(pid)
            d.mkdir()
            (d / "stat").write_text(f"{pid} ({comm}) S {ppid} 1 1 0 -1\n")
        return root

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_claude_pid_must_be_a_near_ancestor(self):
        proc = self.proc({40: (30, "python3"), 30: (20, "sh"), 20: (10, "claude"), 10: (1, "bash"), 1: (0, "init")})
        self.assertTrue(identity.descends_from(40, 20, proc_root=proc))
        self.assertFalse(identity.descends_from(40, 99, proc_root=proc))
        self.assertFalse(identity.descends_from(40, 40, proc_root=proc))  # not itself
        self.assertFalse(identity.descends_from(40, None, proc_root=proc))  # unknown: fail closed
        self.assertFalse(identity.descends_from(40, 20, proc_root=Path(self._tmp.name) / "missing"))

    def test_the_real_proc_sees_the_parent(self):
        self.assertTrue(identity.descends_from(os.getpid(), os.getppid()))


class D96HookParentCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self.fakes = Fakes(self.tmp / "fakes")
        self.store = Store(self.root / ".mesh")
        self.store.create()

    def tearDown(self):
        self._tmp.cleanup()

    def hook(self, claude_pid):
        self.fakes.agent("pm-uid", "pm", self.root, pane="pane-pm", claude_pid=claude_pid)
        base = {k: v for k, v in os.environ.items() if not k.startswith(("MESH_", "CLAUDE_", "PMX_"))}
        base.update(CLAUDE_PROJECT_DIR=str(self.root), PMX_INTERNAL_ACTIVATION_PANE_UID="pane-pm")
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "run /config"})
        out = subprocess.run(
            [sys.executable, str(CLI), "event", "record", "--source", "terminal"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
            env=self.fakes.env(base),
        )
        self.assertEqual((out.returncode, out.stdout), (0, ""), out.stderr)
        return [e.text for e in self.store.operator_events()]

    def test_a_process_outside_the_pm_panes_claude_records_nothing(self):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertEqual(self.hook(sleeper.pid), [])  # the PM pane's claude is not an ancestor of the caller
        finally:
            sleeper.kill()
            sleeper.wait()
        errors = [json.loads(x) for x in self.store.events_path.read_text().splitlines()]
        self.assertEqual([e.get("error") for e in errors], ["parent_not_pm_claude"])

    def test_no_claude_pid_fails_closed(self):
        self.assertEqual(self.hook(0), [])

    def test_the_pm_panes_claude_as_an_ancestor_records_the_event(self):
        self.assertEqual(self.hook(os.getpid()), ["run /config"])


class D96HostDenyRulesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "proj"
        self.root.mkdir()
        (self.tmp / "home").mkdir()
        self.settings = self.root / ".claude" / "settings.json"

    def tearDown(self):
        self._tmp.cleanup()

    def apply(self):
        env = Fakes(self.tmp / "fakes").env({"PATH": "/usr/bin:/bin", "HOME": str(self.tmp / "home")})
        who = parse_caller("pm:pm-uid")
        ctx = tools.Context.for_project(self.root, env, clock=lambda: "t", caller=lambda: who)
        plan = tools.call(ctx, "mesh_init", {"stack": "none"}).data["plan_id"]
        out = tools.call(ctx, "mesh_init", {"stack": "none", "apply": True, "plan_id": plan})
        self.assertTrue(out.ok, out.data)
        return out

    def test_mesh_init_writes_the_deny_rules_additively(self):
        self.settings.parent.mkdir()
        mine = {"permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(rm -rf:*)"], "defaultMode": "plan"}}
        self.settings.write_text(json.dumps(mine))
        self.apply()
        perms = json.loads(self.settings.read_text())["permissions"]
        self.assertEqual((perms["allow"], perms["defaultMode"]), (["Bash(ls:*)"], "plan"))
        self.assertEqual(perms["deny"], ["Bash(rm -rf:*)", *init.DENY_RULES])
        self.assertIn("Bash(*mesh* event record*)", init.DENY_RULES)
        self.assertIn("Write(.mesh/**)", init.DENY_RULES)
        self.assertIn("Edit(.mesh/**)", init.DENY_RULES)
        self.apply()
        self.assertEqual(json.loads(self.settings.read_text())["permissions"]["deny"].count("Write(.mesh/**)"), 1)

    def test_a_fresh_settings_file_gets_the_hook_and_the_deny_rules(self):
        self.apply()
        data = json.loads(self.settings.read_text())
        self.assertEqual(data["permissions"]["deny"], list(init.DENY_RULES))
        self.assertEqual(data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"], init.HOOK_COMMAND)

    def test_an_existing_mesh_hook_with_an_old_command_is_refreshed(self):
        self.settings.parent.mkdir()
        old = '"$CLAUDE_PROJECT_DIR"/.mesh/bin/mesh event record --source terminal'
        mine = {
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": "echo mine"}]},
                    {"hooks": [{"type": "command", "command": old, "timeout": 10}]},
                ]
            }
        }
        self.settings.write_text(json.dumps(mine))
        out = self.apply()
        prompt = json.loads(self.settings.read_text())["hooks"]["UserPromptSubmit"]
        self.assertEqual(len(prompt), 2)
        self.assertEqual(prompt[0]["hooks"][0]["command"], "echo mine")
        self.assertEqual(prompt[1]["hooks"][0]["command"], init.HOOK_COMMAND)
        row = next(r for r in out.data["rows"] if r["path"] == ".claude/settings.json")
        self.assertEqual(row["action"], "MERGE")


# ================================================================ step 3: verify prepare / output checks, dispatch
from test_mesh_state import contract, item_in  # noqa: E402
from test_mesh_verify import FactsCase, py, toml_commands  # noqa: E402
from test_mesh_wire import WireCase  # noqa: E402

from mesh import defaults, projmux, store, verify  # noqa: E402
from mesh.model import Applied, RunStatus, Scope, VerifyArgs  # noqa: E402


class D91VerifyPrepareTests(FactsCase):
    def prepare(self, code, timeout_s=120):
        return f"[verify]\nprepare = {json.dumps([sys.executable, '-c', code])}\nprepare_timeout_s = {timeout_s}\n"

    def test_prepare_runs_in_the_worktree_before_the_commands(self):
        work = self.commit_file("feature.txt", "done\n")
        self.config(
            self.prepare("open('deps.txt', 'w').write('ok')")
            + toml_commands(py("import sys; sys.exit(0 if open('deps.txt').read() == 'ok' else 1)"))
        )
        self.verifying(work)
        out = self.do(Verb.VERIFY, BACKEND, VerifyArgs(1))
        self.assertIsInstance(out, Applied, out)
        self.assertEqual(
            [(r.kind, r.status) for r in self.item().current_attempt.evidence.runs],
            [("prepare", RunStatus.PASS), ("tests", RunStatus.PASS)],
        )
        self.assertFalse((self.root / "deps.txt").exists())  # the shared tree is untouched
        self.assertEqual(self.status(), "")

    def test_a_failed_prepare_is_class_environment_and_no_command_runs(self):
        work = self.commit_file("feature.txt", "done\n")
        marker = self.tmp / "ran"
        self.config(self.prepare("import sys; sys.exit(3)") + toml_commands(py(f"open({str(marker)!r}, 'w')")))
        self.verifying(work)
        out = self.do(Verb.VERIFY, BACKEND, VerifyArgs(1))
        self.assert_refused(out, RefusalCode.PREPARE_FAILED)
        self.assertIn("environment", out.refusal.detail)
        self.assertFalse(marker.exists())

    def test_a_prepare_timeout_is_killed_and_is_a_prepare_failure(self):
        work = self.commit_file("feature.txt", "done\n")
        self.config(self.prepare("import time; time.sleep(60)", timeout_s=1) + toml_commands(py("pass")))
        self.verifying(work)
        self.assert_refused(self.do(Verb.VERIFY, BACKEND, VerifyArgs(1)), RefusalCode.PREPARE_FAILED)

    def test_prepare_is_part_of_the_config_hash(self):
        plain = store.parse_config(toml_commands(py("pass")))
        prepared = store.parse_config(self.prepare("pass") + toml_commands(py("pass")))
        self.assertNotEqual(plain.verify_hash(), prepared.verify_hash())
        self.assertEqual(plain.verify_hash(), Config(verify_commands=plain.verify_commands).verify_hash())


class D92FailIfOutputTests(FactsCase):
    def test_exit_0_with_a_matching_line_fails_with_the_line_as_bounded_relative_evidence(self):
        work = self.commit_file("feature.txt", "done\n")
        noisy = "import os; print('scan'); print(os.getcwd() + '/src: 0 modules, 0 dependencies cruised' + 'x' * 400)"
        self.config(toml_commands(py(noisy)) + 'fail_if_output = ["0 modules, 0 dependencies cruised"]\n')
        self.verifying(work)
        out = self.do(Verb.VERIFY, BACKEND, VerifyArgs(1))
        self.assert_refused(out, RefusalCode.VERIFICATION_FAILED)
        run_id = next((self.root / ".mesh" / "runs").iterdir()).name
        self.assertTrue(run_id)
        _, (run,) = verify.run_at(self.root, work, self.store.read_config().verify_commands, self.env)
        self.assertEqual((run.status, run.exit_code), (RunStatus.FAIL, 0))
        self.assertTrue(run.evidence.startswith("src: 0 modules, 0 dependencies cruised"), run.evidence)
        self.assertLessEqual(len(run.evidence), layers.MAX_EVIDENCE)
        self.assertNotIn(str(self.tmp), run.evidence)

    def test_no_match_or_a_non_zero_exit_keeps_the_exit_status(self):
        commands = store.parse_config(
            toml_commands(py("print('12 modules cruised')")) + 'fail_if_output = ["0 modules"]\n'
        ).verify_commands
        _, (run,) = verify.run_at(self.root, self.base, commands, self.env)
        self.assertEqual((run.status, run.evidence), (RunStatus.PASS, None))

    def test_an_absolute_path_outside_the_worktree_is_never_kept(self):
        line = layers.output_failure(["boom"], "boom at /etc/secret/file.txt and ~/x", "", "/tmp/wt")
        self.assertEqual(line, "boom at <path> and ~/x")

    def test_a_bad_regex_is_a_config_error(self):
        with self.assertRaises(Exception) as cm:
            store.parse_config(toml_commands(py("pass")) + 'fail_if_output = ["("]\n')
        self.assertIn("fail_if_output", str(cm.exception))

    def test_the_ts_defaults_are_data_applied_by_mesh_init_for_stack_ts(self):
        self.assertEqual(defaults.TS_FAIL_IF_OUTPUT, ("0 modules, 0 dependencies cruised", "No test files found"))
        cfg = store.parse_config(defaults.config_toml("ts"))
        self.assertEqual(cfg.verify_fail_if_output, defaults.TS_FAIL_IF_OUTPUT)
        self.assertEqual(store.parse_config(defaults.config_toml("none")).verify_fail_if_output, ())
        merged = store.parse_config(defaults.config_toml("ts") + toml_commands(py("pass")))
        self.assertEqual(merged.verify_commands[0].fail_if_output, defaults.TS_FAIL_IF_OUTPUT)

    def test_mesh_init_writes_the_ts_defaults(self):
        root = self.tmp / "ts"
        root.mkdir()
        (root / "package.json").write_text("{}\n")
        env = Fakes(self.tmp / "fakes").env({"PATH": "/usr/bin:/bin", "HOME": str(self.home)})
        who = parse_caller("pm:pm-uid")
        ctx = tools.Context.for_project(root, env, clock=lambda: "t", caller=lambda: who)
        plan = tools.call(ctx, "mesh_init", {"stack": "ts"}).data["plan_id"]
        self.assertTrue(tools.call(ctx, "mesh_init", {"stack": "ts", "apply": True, "plan_id": plan}).ok)
        cfg = Store(root / ".mesh").read_config()
        self.assertEqual(cfg.verify_fail_if_output, defaults.TS_FAIL_IF_OUTPUT)


class D93DispatchRetryTests(WireCase):
    def setUp(self):
        super().setUp()
        self.seed(item_in(Phase.READY, contract=contract(version=1)))
        self.pm_agent()
        self.fakes.agent("be-uid", "backend", self.root)
        self.store.config_path.write_text("[dispatch]\nretry = 3\nbackoff_s = 0\n")

    def dispatch(self):
        return tools.call(self.ctx(), "work_dispatch", {"item_id": "WO-0001"})

    def refs(self):
        return [m["args"][m["args"].index("--message-ref") + 1] for m in self.fakes.load().get("messages", [])]

    def test_a_transient_refusal_is_retried_with_the_same_message_ref_and_every_attempt_is_recorded(self):
        self.fakes.set(send_plan=[[1, "readiness-timeout"], [1, "explicit-reply-route-stale"], [0, ""]])
        out = self.dispatch()
        self.assertTrue(out.ok, out.data)
        self.assertEqual(out.data["dispatch"]["sent"], True)
        self.assertEqual(
            out.data["dispatch"]["attempts"], ["readiness-timeout", "explicit-reply-route-stale", "delivered"]
        )
        self.assertEqual(self.refs(), ["mesh-WO-0001-d1"] * 3)
        (action,) = self.store.read().doc.items["WO-0001"].pending_actions
        self.assertEqual(
            (action.done, action.payload["attempts"]), (True, "readiness-timeout,explicit-reply-route-stale,delivered")
        )

    def test_a_terminal_refusal_stops_at_once_and_mesh_status_shows_the_attempts(self):
        self.fakes.set(send_plan=[[1, "unknown-target"], [0, ""]])
        out = self.dispatch()
        self.assertEqual((out.data["dispatch"]["sent"], out.data["dispatch"]["attempts"]), (False, ["unknown-target"]))
        self.assertEqual(len(self.refs()), 1)
        status = tools.call(self.ctx(), "mesh_status", {}).data
        (pending,) = status["pending_actions"]
        self.assertEqual(pending["attempts"], ["unknown-target"])

    def test_the_retry_is_bounded(self):
        self.fakes.set(send_plan=[[1, "provider-prewrite-refused"]] * 10)
        out = self.dispatch()
        self.assertEqual(len(out.data["dispatch"]["attempts"]), 4)  # the first send + retry = 3
        self.assertEqual(len(self.refs()), 4)

    def test_the_transient_match_is_conservative(self):
        for reason in (
            "readiness-timeout",
            "route-stale",
            "explicit-reply-target-route-stale",
            "provider-prewrite-refused",
        ):
            self.assertTrue(projmux.transient(reason), reason)
        for reason in (
            "",
            None,
            "refused",
            "stale-turn",
            "timeout",
            "unknown-message",
            "helper-stale",
            "x route-stale y",
        ):
            self.assertFalse(projmux.transient(reason), reason)

    def test_the_default_is_three_retries(self):
        self.assertEqual((Config().dispatch_retry, Config().dispatch_backoff_s > 0), (3, True))


class D94OverlapWarningTests(WireCase):
    def setUp(self):
        super().setUp()
        self.pm_agent()
        self.fakes.agent("be-uid", "backend", self.root)

    def item(self, item_id, phase, write=(), resources=()):
        return item_in(
            phase, item_id=item_id, contract=contract(version=1, scope=Scope(write_set=write, resources=resources))
        )

    def test_dispatch_warns_on_an_overlapping_running_item_and_does_not_refuse(self):
        self.seed(
            self.item("WO-0001", Phase.READY, write=("src/api/**",), resources=("package-lock.json",)),
            self.item("WO-0002", Phase.RUNNING, write=("src/api/users.py",)),
            self.item("WO-0003", Phase.RUNNING, write=("docs/**",), resources=("package-lock.json",)),
            self.item("WO-0004", Phase.RUNNING, write=("src/web/**",)),
        )
        out = tools.call(self.ctx(), "work_dispatch", {"item_id": "WO-0001"})
        self.assertTrue(out.ok, out.data)
        self.assertEqual(
            [(w["item_id"], w["write_scope"], w["resources"]) for w in out.data["warnings"]],
            [("WO-0002", ["src/api/**"], []), ("WO-0003", [], ["package-lock.json"])],
        )
        status = tools.call(self.ctx(), "mesh_status", {}).data
        self.assertEqual(
            sorted((o["items"][0], o["items"][1]) for o in status["overlaps"]),
            [("WO-0001", "WO-0002"), ("WO-0001", "WO-0003")],
        )

    def test_no_overlap_no_warning(self):
        self.seed(
            self.item("WO-0001", Phase.READY, write=("a/**",)), self.item("WO-0002", Phase.RUNNING, write=("b/**",))
        )
        out = tools.call(self.ctx(), "work_dispatch", {"item_id": "WO-0001"})
        self.assertEqual(out.data.get("warnings"), [])


# ================================================================ step 4: D102 completion signal and stalls
from test_mesh_wire import skill_header  # noqa: E402

from mesh import dispatch, status  # noqa: E402


class D102CompletionSignalTextTests(unittest.TestCase):
    def test_the_dispatch_header_tells_the_lead_to_spawn_workers_with_a_completion_step_and_to_wait(self):
        for text in (dispatch.HEADER, skill_header()):
            self.assertIn("-done", text)
            self.assertIn("projmux agent wait", text)
            self.assertIn("--until idle --timeout", text)
            self.assertIn("projmux agent message send", text)
        self.assertEqual(dispatch.HEADER, skill_header())

    def test_the_run_skill_says_the_same(self):
        text = (ROOT / "skills" / "run" / "SKILL.md").read_text(encoding="utf-8")
        for part in ("<WO>-done", "projmux agent wait", "--until idle --timeout", "worker-of=", "stalls"):
            self.assertIn(part, text)


NOW = "2026-11-18T01:00:00Z"


class D102StallTests(WireCase):
    def setUp(self):
        super().setUp()
        self.pm_agent()

    def ctx_at(self, now=NOW):
        base = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "PMX_INTERNAL_ACTIVATION_PANE_UID": "pane-pm-uid"}
        who = parse_caller("pm:pm-uid")
        return tools.Context.for_project(self.root, self.fakes.env(base), clock=lambda: now, caller=lambda: who)

    def agent(self, uid, *, role=None, worker_of=None, kind="response_complete", at="2026-11-18T00:59:00Z"):
        self.fakes.agent(uid, role, self.root)
        st = self.fakes.load()
        a = next(x for x in st["agents"] if x["metadata"]["uid"] == uid)
        if worker_of:
            a["metadata"]["labels"]["worker-of"] = worker_of
        a["status"]["interaction"] = {"kind": kind, "observedAt": at}
        self.fakes.save()

    def stalls(self, now=NOW):
        out = tools.call(self.ctx_at(now), "mesh_status", {})
        self.assertTrue(out.ok, out.data)
        return out.data["stalls"]

    def test_an_idle_worker_with_a_report_newer_than_its_leads_last_activity(self):
        report = self.root / "docs" / "reports" / "WO-0001-backend.md"
        self.seed(
            item_in(Phase.RUNNING, contract=contract(version=1, scope=Scope(report=str(report.relative_to(self.root)))))
        )
        self.agent("be-uid", role="backend", at="2026-11-18T00:50:00Z")
        self.agent("w1", worker_of="backend", kind="idle")
        report.parent.mkdir(parents=True)
        report.write_text("PASS\n")
        os.utime(report, (1794961200, 1794961200))  # 2026-11-18T00:20:00Z: older than the lead's activity
        self.assertEqual([s["kind"] for s in self.stalls()], [])
        os.utime(report, (1794963300, 1794963300))  # 2026-11-18T00:55:00Z: newer
        (stall,) = self.stalls()
        self.assertEqual((stall["kind"], stall["item_id"], stall["nudge"]), ("worker_done", "WO-0001", "lead backend"))
        self.assertNotIn(str(self.root), json.dumps(stall))

    def test_a_running_or_verifying_item_whose_lead_is_quiet_for_stall_minutes(self):
        self.seed(
            item_in(Phase.RUNNING), item_in(Phase.VERIFYING, item_id="WO-0002", contract=contract(version=1, role="qa"))
        )
        self.agent("be-uid", role="backend", at="2026-11-18T00:44:00Z", kind="idle")  # 16 min
        self.agent("qa-uid", role="qa", at="2026-11-18T00:50:00Z", kind="idle")  # 10 min
        got = [(s["kind"], s["item_id"], s["nudge"]) for s in self.stalls()]
        self.assertEqual(got, [("lead_quiet", "WO-0001", "lead backend")])
        self.store.config_path.write_text("[status]\nstall_minutes = 5\n")
        got = [(s["kind"], s["item_id"]) for s in self.stalls()]
        self.assertEqual(got, [("lead_quiet", "WO-0001"), ("lead_quiet", "WO-0002")])

    def test_a_pending_dispatch_without_a_reply(self):
        from mesh.model import ActionKind, PendingAction

        sent = PendingAction("WO-0001-d1", ActionKind.DISPATCH_SEND, {"message_ref": "mesh-WO-0001-d1"}, True)
        unsent = PendingAction("WO-0002-d1", ActionKind.DISPATCH_SEND, {"message_ref": "mesh-WO-0002-d1"}, False)
        self.seed(
            item_in(Phase.READY, contract=contract(version=1), pending_actions=(sent,)),
            item_in(Phase.READY, item_id="WO-0002", contract=contract(version=1, role="qa"), pending_actions=(unsent,)),
        )
        self.agent("be-uid", role="backend", at="2026-11-18T00:30:00Z", kind="idle")
        self.agent("qa-uid", role="qa")
        got = sorted((s["kind"], s["item_id"], s["nudge"]) for s in self.stalls())
        self.assertEqual(
            got, [("dispatch_unanswered", "WO-0001", "lead backend"), ("dispatch_unsent", "WO-0002", "pm")]
        )

    def test_detection_is_read_only_and_one_status_layer(self):
        self.seed(item_in(Phase.RUNNING))
        self.agent("be-uid", role="backend", at="2026-11-18T00:00:00Z")
        before = self.store.state_path.read_text()
        self.assertEqual(len(self.stalls()), 1)
        self.assertEqual(self.store.state_path.read_text(), before)
        self.assertEqual([c for c in self.fakes.calls("projmux") if c[:3] == ["agent", "message", "send"]], [])
        self.assertTrue(callable(status.stalls))
        src = (MESH / "tools.py").read_text(encoding="utf-8")
        self.assertIn("status.stalls(", src)
        self.assertNotIn("stall_minutes", src)


if __name__ == "__main__":
    unittest.main()
