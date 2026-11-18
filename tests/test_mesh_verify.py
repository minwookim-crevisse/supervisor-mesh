"""WO-0022 step 4 (W2): the git adapter, the verify runner (a disposable worktree at a validated sha, argv only, a
per-command timeout with a process-group kill, an environment allowlist, logs under .mesh/runs/) and the git /
verification facts (HEAD, COMMIT_EXISTS, REPRO, PRECHECK, VERIFICATION, MERGED_COMMIT). Real git in temp repos; no
projmux, tmux or claude is ever called."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from _helpers import ROOT

sys.path.insert(0, str(ROOT))
from test_mesh_state import contract, doc_of, item_in  # noqa: E402

from mesh import facts, git, store, tools, verify  # noqa: E402  # noqa: E402
from mesh.engine import Engine  # noqa: E402
from mesh.model import (  # noqa: E402
    Applied,
    Attempt,
    Caller,
    Command,
    ContractPatch,
    EvidenceKind,
    Fact,
    Facts,
    InvalidArgs,
    NoArgs,
    Phase,
    ReadyArgs,
    RefusalCode,
    Refused,
    Request,
    RunStatus,
    StartArgs,
    SubmitArgs,
    Verb,
    VerifyArgs,
)
from mesh.store import Store  # noqa: E402

PY = sys.executable


def sh_git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, env=git_env())
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout.strip()


def git_env(home=None):
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home or tempfile.gettempdir()),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }


class RepoCase(unittest.TestCase):
    """A real git repo in a temp dir with one commit (`tracked.txt`, a `.gitignore` that ignores `.mesh/` as init
    does) and `.mesh/` created."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = git_env(self.home)
        sh_git(self.root, "init", "-q", "-b", "main")
        (self.root / "tracked.txt").write_text("original\n")
        (self.root / ".gitignore").write_text(".mesh/\n")
        sh_git(self.root, "add", ".")
        sh_git(self.root, "commit", "-q", "-m", "base")
        self.base = sh_git(self.root, "rev-parse", "HEAD")
        (self.root / ".mesh").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def commit_file(self, name, text, msg="change"):
        (self.root / name).write_text(text)
        sh_git(self.root, "add", name)
        sh_git(self.root, "commit", "-q", "-m", msg)
        return sh_git(self.root, "rev-parse", "HEAD")

    def status(self):
        return sh_git(self.root, "status", "--porcelain=v1", "-uall")


class GitTests(RepoCase):
    def test_head_is_the_shared_trees_commit(self):
        self.assertEqual(git.head(self.root, self.env), self.base)

    def test_head_outside_a_repo_is_unknown(self):
        self.assertIsNone(git.head(self.tmp, self.env))

    def test_commit_exists_for_a_real_commit_and_not_for_an_unknown_sha(self):
        self.assertTrue(git.commit_exists(self.root, self.base, self.env))
        self.assertFalse(git.commit_exists(self.root, "0" * 40, self.env))

    def test_a_non_sha_is_never_handed_to_git(self):
        for bad in ("HEAD", "--output=x", self.base[:12], self.base.upper(), self.base + "\n"):
            with self.assertRaises(ValueError, msg=bad):
                git.commit_exists(self.root, bad, self.env)

    def test_a_tree_object_is_not_a_commit(self):
        tree = sh_git(self.root, "rev-parse", "HEAD^{tree}")
        self.assertFalse(git.commit_exists(self.root, tree, self.env))


def py(code, kind="tests", timeout_s=120):
    return Command((PY, "-c", code), kind, timeout_s)


class VerifyRunnerTests(RepoCase):
    def run_at(self, sha, *commands, env=None):
        return verify.run_at(self.root, sha, commands, env or self.env)

    def worktrees(self):
        return [
            ln for ln in sh_git(self.root, "worktree", "list", "--porcelain").splitlines() if ln.startswith("worktree ")
        ]

    def test_status_mapping_pass_fail_and_126_127_spawn_error_are_not_run(self):
        _, runs = self.run_at(
            self.base,
            py("pass"),
            py("import sys; sys.exit(3)"),
            py("import sys; sys.exit(126)"),
            py("import sys; sys.exit(127)"),
            Command((str(self.tmp / "no-such-tool"),), "lint"),
        )
        self.assertEqual(
            [(r.status, r.exit_code) for r in runs],
            [
                (RunStatus.PASS, 0),
                (RunStatus.FAIL, 3),
                (RunStatus.NOT_RUN, 126),
                (RunStatus.NOT_RUN, 127),
                (RunStatus.NOT_RUN, None),
            ],
        )
        self.assertEqual([r.kind for r in runs], ["tests"] * 4 + ["lint"])
        self.assertTrue(all(r.started_at and r.ended_at for r in runs))

    def test_commands_run_at_the_given_commit_not_at_the_shared_trees_head(self):
        c1 = self.commit_file("marker.txt", "one\n")
        self.commit_file("marker.txt", "two\n")
        _, runs = self.run_at(c1, py("import sys; sys.exit(0 if open('marker.txt').read() == 'one\\n' else 1)"))
        self.assertEqual(runs[0].status, RunStatus.PASS)
        self.assertEqual((self.root / "marker.txt").read_text(), "two\n")

    def test_the_worktree_is_removed_afterwards_even_when_a_command_fails(self):
        seen = self.tmp / "seen.txt"
        _, runs = self.run_at(
            self.base, py(f"import os; open({str(seen)!r}, 'w').write(os.getcwd()); raise SystemExit(1)")
        )
        self.assertEqual(runs[0].status, RunStatus.FAIL)
        self.assertFalse(Path(seen.read_text()).exists())
        self.assertEqual(len(self.worktrees()), 1)

    def test_cleanup_removes_only_its_own_worktree_and_never_prunes_others(self):
        other = self.tmp / "other-session-wt"
        sh_git(self.root, "worktree", "add", "--detach", "--quiet", str(other), self.base)
        shutil.rmtree(other)  # its directory is temporarily missing: a `worktree prune` would drop its metadata
        seen = self.tmp / "verify-wt.txt"
        self.run_at(self.base, py(f"import os; open({str(seen)!r}, 'w').write(os.getcwd())"))
        listed = self.worktrees()
        self.assertIn(f"worktree {other}", listed)
        self.assertNotIn(f"worktree {seen.read_text()}", listed)
        self.assertEqual(len(listed), 2)

    def test_an_unknown_commit_raises_and_leaves_no_worktree(self):
        with self.assertRaises(verify.WorktreeUnavailable):
            self.run_at("0" * 40, py("pass"))
        self.assertEqual(len(self.worktrees()), 1)
        with self.assertRaises(ValueError):
            self.run_at("HEAD", py("pass"))

    def test_logs_go_under_mesh_runs(self):
        run_id, _ = self.run_at(self.base, py("print('hello from the run')"))
        logs = sorted((self.root / ".mesh" / "runs" / run_id).iterdir())
        self.assertEqual(len(logs), 1)
        self.assertIn("hello from the run", logs[0].read_text())

    def test_the_child_gets_only_the_allowlisted_environment(self):
        env = {**self.env, "SECRET_TOKEN": "s3cret", "PMX_INTERNAL_ACTIVATION_PANE_UID": "pane", "SLACK_BOT_TOKEN": "x"}
        dropped = {"SECRET_TOKEN", "PMX_INTERNAL_ACTIVATION_PANE_UID", "SLACK_BOT_TOKEN", "GIT_AUTHOR_NAME"}
        probe = f"import os, sys; sys.exit(1 if {dropped!r} & set(os.environ) else 0 if 'PATH' in os.environ else 2)"
        _, runs = self.run_at(self.base, py(probe), env=env)
        self.assertEqual(runs[0].status, RunStatus.PASS)
        self.assertEqual(verify.child_env(env).keys() - set(verify.ENV_ALLOW), set())


class SharedTreeUntouchedTests(RepoCase):
    """Invariant 12: no command run changes the shared working tree."""

    def test_a_command_that_writes_leaves_the_shared_trees_git_status_unchanged(self):
        (self.root / "untracked-before.txt").write_text("the operator's own scratch\n")
        before = (self.status(), sh_git(self.root, "rev-parse", "HEAD"), (self.root / "tracked.txt").read_text())
        writer = (
            "import os, pathlib; pathlib.Path('tracked.txt').write_text('clobbered'); "
            "pathlib.Path('new.txt').write_text('x'); os.makedirs('build/out', exist_ok=True); "
            "pathlib.Path('build/out/a.o').write_text('y'); os.remove('.gitignore')"
        )
        _, runs = verify.run_at(self.root, self.base, (py(writer),), self.env)
        self.assertEqual(runs[0].status, RunStatus.PASS)
        after = (self.status(), sh_git(self.root, "rev-parse", "HEAD"), (self.root / "tracked.txt").read_text())
        self.assertEqual(after, before)
        self.assertIn("?? untracked-before.txt", before[0])
        self.assertFalse((self.root / "new.txt").exists() or (self.root / "build").exists())


class TimeoutTests(RepoCase):
    def test_a_command_past_its_timeout_is_killed_with_its_whole_process_group(self):
        marker = self.tmp / "grandchild-survived.txt"
        grandchild = f"import time; time.sleep(2); open({str(marker)!r}, 'w').write('alive')"
        hang = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(60)"
        t0 = time.monotonic()
        _, runs = verify.run_at(self.root, self.base, (py(hang, timeout_s=1), py("pass", kind="lint")), self.env)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertEqual([(r.status, r.exit_code) for r in runs], [(RunStatus.TIMEOUT, None), (RunStatus.PASS, 0)])
        time.sleep(2.5)
        self.assertFalse(marker.exists(), "the grandchild outlived the timeout: the process group was not killed")


class ConfigTests(unittest.TestCase):
    TOML = (
        '[[verify.commands]]\nargv = ["make", "check"]\nkind = "tests"\ntimeout_s = 30\n'
        '[[verify.commands]]\nargv = ["ruff", "check"]\nkind = "lint"\n'
        '[[precheck.commands]]\nargv = ["docker", "info"]\nkind = "env"\n'
        "[repro]\ntimeout_s = 45\n"
    )

    def test_per_command_timeouts_precheck_commands_and_the_repro_timeout(self):
        cfg = store.parse_config(self.TOML)
        self.assertEqual([c.timeout_s for c in cfg.verify_commands], [30, 120])
        self.assertEqual(cfg.precheck_commands, (Command(("docker", "info"), "env"),))
        self.assertEqual(cfg.repro_timeout_s, 45)
        self.assertEqual(store.parse_config("").repro_timeout_s, 120)

    def test_a_timeout_changes_the_verify_hash_and_the_precheck_does_not(self):
        base = store.parse_config(self.TOML)
        slower = store.parse_config(self.TOML.replace("timeout_s = 30", "timeout_s = 31"))
        no_precheck = store.parse_config(self.TOML.split("[[precheck")[0] + "[repro]\ntimeout_s = 45\n")
        self.assertNotEqual(base.verify_hash(), slower.verify_hash())
        self.assertEqual(base.verify_hash(), no_precheck.verify_hash())

    def test_a_non_positive_or_non_integer_timeout_is_refused(self):
        for bad in ("0", "-1", "true", '"10"', "1.5"):
            with self.assertRaises(InvalidArgs, msg=bad):
                store.parse_config(f'[[verify.commands]]\nargv = ["x"]\nkind = "t"\ntimeout_s = {bad}\n')
        with self.assertRaises(InvalidArgs):
            store.parse_config("[repro]\ntimeout_s = 0\n")


AT = "2026-11-18T00:00:00Z"
PM, BACKEND = Caller.pm("pm-uid"), Caller.lead("backend", "be-uid")


class FactsCase(RepoCase):
    """The production git / verification provider behind the real engine and store, in a real temp repo."""

    def setUp(self):
        super().setUp()
        self.store = Store(self.root / ".mesh")
        self.store.create()
        self.live = lambda needs, doc, req, cfg: Facts(lead_live=True)

    def config(self, text):
        self.store.config_path.write_text(text)

    def engine(self):
        return Engine(
            self.store, facts=facts.chain(self.live, facts.git_facts(self.root, self.env, self.store.read_config))
        )

    def seed(self, *items):
        with self.store.locked() as txn:
            txn.commit(doc_of(*items))

    def do(self, verb, caller, args=NoArgs(), item_id="WO-0001"):
        return self.engine().execute(Request(verb, caller, args, AT, item_id))

    def item(self, item_id="WO-0001"):
        return self.store.read().doc.items[item_id]

    def verifying(self, commit, **kw):
        cfg = self.store.read_config()
        attempt = Attempt(1, "be-uid", self.base, commit, 1, cfg.verify_hash())
        self.seed(item_in(Phase.VERIFYING, contract=contract(version=1), attempts=(attempt,), **kw))

    def assert_refused(self, out, code):
        self.assertIsInstance(out, Refused, out)
        self.assertEqual(out.refusal.code, code, out.refusal.detail)


class HeadAndCommitFactTests(FactsCase):
    def test_start_records_the_real_head_and_submit_checks_the_commit_exists(self):
        self.seed(item_in(Phase.READY, contract=contract(version=1)))
        self.assertIsInstance(self.do(Verb.START, BACKEND, StartArgs()), Applied)
        self.assertEqual(self.item().current_attempt.base_commit, self.base)
        self.assert_refused(self.do(Verb.SUBMIT, BACKEND, SubmitArgs(1, "0" * 40)), RefusalCode.COMMIT_UNKNOWN)
        work = self.commit_file("feature.txt", "done\n")
        self.assertIsInstance(self.do(Verb.SUBMIT, BACKEND, SubmitArgs(1, work)), Applied)
        self.assertEqual(self.item().phase, Phase.VERIFYING)

    def test_merged_commit_is_the_shared_trees_head(self):
        provide = facts.git_facts(self.root, self.env, self.store.read_config)
        got = provide(
            frozenset({Fact.MERGED_COMMIT, Fact.HEAD}), doc_of(), Request(Verb.MESH, Caller.mesh(), NoArgs(), AT), None
        )
        self.assertEqual((got.merged_commit, got.head), (self.base, self.base))

    def test_facts_not_asked_for_are_not_observed(self):
        provide = facts.git_facts(self.root, self.env, self.store.read_config)
        got = provide(frozenset({Fact.LEAD_LIVE}), doc_of(), Request(Verb.MESH, Caller.mesh(), NoArgs(), AT), None)
        self.assertEqual(got, facts.NO_FACTS)


def toml_commands(*commands, table="verify"):
    out = ""
    for c in commands:
        argv = ", ".join(json.dumps(a) for a in c.argv)  # a JSON string is a valid TOML basic string
        out += f'[[{table}.commands]]\nargv = [{argv}]\nkind = "{c.kind}"\ntimeout_s = {c.timeout_s}\n'
    return out


class VerificationFactTests(FactsCase):
    def test_a_passing_run_at_the_submitted_commit_reaches_verified_with_bound_evidence(self):
        work = self.commit_file("feature.txt", "done\n")
        self.commit_file("later.txt", "moved on\n")  # HEAD moves past the submitted commit
        probe = "import sys; sys.exit(0 if open('feature.txt').read() == 'done\\n' else 1)"
        self.config(toml_commands(py(probe), py("pass", kind="lint")))
        self.verifying(work)
        out = self.do(Verb.VERIFY, BACKEND, VerifyArgs(1))
        self.assertIsInstance(out, Applied, out)
        item = self.item()
        ev = item.current_attempt.evidence
        self.assertEqual(item.phase, Phase.VERIFIED)
        self.assertEqual(
            (ev.kind, ev.commit, ev.contract_version, ev.config_hash),
            (EvidenceKind.VERIFICATION, work, 1, self.store.read_config().verify_hash()),
        )
        self.assertEqual(
            [(r.kind, r.status, r.exit_code) for r in ev.runs],
            [("tests", RunStatus.PASS, 0), ("lint", RunStatus.PASS, 0)],
        )
        self.assertTrue(any((self.root / ".mesh" / "runs").iterdir()))

    def test_a_config_edited_during_the_run_is_refused_as_inputs_changed(self):
        work = self.commit_file("feature.txt", "done\n")
        cfg_path = self.store.config_path
        extra = '[[verify.commands]]\nargv = ["true"]\nkind = "extra"\n'
        edit = f"open({str(cfg_path)!r}, 'a').write({extra!r})"
        self.config(toml_commands(py(edit)))
        self.verifying(work)
        self.assert_refused(self.do(Verb.VERIFY, BACKEND, VerifyArgs(1)), RefusalCode.INPUTS_CHANGED)
        self.assertIn("extra", cfg_path.read_text())
        self.assertEqual(self.item().phase, Phase.VERIFYING)
        self.assertIsNone(self.item().current_attempt.evidence)

    def test_a_config_changed_since_start_is_refused_as_inputs_changed(self):
        work = self.commit_file("feature.txt", "done\n")
        self.config(toml_commands(py("pass")))
        self.verifying(work)
        self.config(toml_commands(py("pass"), py("pass", kind="lint")))
        self.assert_refused(self.do(Verb.VERIFY, BACKEND, VerifyArgs(1)), RefusalCode.INPUTS_CHANGED)
        self.assertEqual(self.item().phase, Phase.VERIFYING)

    def test_a_failing_or_missing_tool_never_reaches_verified(self):
        work = self.commit_file("feature.txt", "done\n")
        for cmd in (py("raise SystemExit(1)"), Command((str(self.tmp / "missing-tool"),), "tests")):
            self.config(toml_commands(cmd))
            self.verifying(work)
            self.assert_refused(self.do(Verb.VERIFY, BACKEND, VerifyArgs(1)), RefusalCode.VERIFICATION_FAILED)
            self.assertEqual(self.item().phase, Phase.VERIFYING)

    def test_a_stale_attempt_id_runs_nothing(self):
        work = self.commit_file("feature.txt", "done\n")
        self.config(toml_commands(py("pass")))
        self.verifying(work)
        self.assert_refused(self.do(Verb.VERIFY, BACKEND, VerifyArgs(7)), RefusalCode.ATTEMPT_STALE)
        self.assertFalse((self.root / ".mesh" / "runs").exists())

    def test_the_split_parents_own_run_is_at_its_merged_commit(self):
        merged = self.commit_file("merged.txt", "children\n")
        self.config(toml_commands(py("import os; raise SystemExit(0 if os.path.exists('merged.txt') else 1)")))
        cfg = self.store.read_config()
        parent = item_in(
            Phase.VERIFYING,
            contract=contract(version=1),
            attempts=(Attempt(1, None, None, merged, 1, cfg.verify_hash()),),
        )
        provide = facts.git_facts(self.root, self.env, self.store.read_config)
        got = provide(
            frozenset({Fact.VERIFICATION}),
            doc_of(parent),
            Request(Verb.MESH, Caller.mesh(), NoArgs(), AT, "WO-0001"),
            cfg,
        )
        self.assertEqual(got.verification.commit, merged)
        self.assertEqual([r.status for r in got.verification.runs], [RunStatus.PASS])


class ReproFactTests(FactsCase):
    """D46 / section 7: a bugfix's repro must FAIL at the base commit (the shared tree's HEAD) before `ready`."""

    def draft(self, code):
        self.seed(item_in(Phase.DRAFT, contract=contract(type="bugfix", repro=(PY, "-c", code))))

    def test_a_failing_repro_records_failing_before_at_the_base_commit(self):
        self.draft("raise SystemExit(4)")
        self.assertIsInstance(self.do(Verb.READY, PM, ReadyArgs()), Applied)
        fb = self.item().failing_before
        self.assertEqual(
            (self.item().phase, fb.base_commit, fb.exit_code, fb.contract_version), (Phase.READY, self.base, 4, 1)
        )

    def test_a_passing_repro_is_not_reproduced(self):
        self.draft("pass")
        self.assert_refused(self.do(Verb.READY, PM, ReadyArgs()), RefusalCode.REPRO_NOT_REPRODUCED)
        self.assertEqual(self.item().phase, Phase.DRAFT)

    def test_126_127_spawn_error_and_timeout_are_not_runnable(self):
        self.config("[repro]\ntimeout_s = 1\n")
        for code in ("raise SystemExit(127)", "raise SystemExit(126)", "import time; time.sleep(30)"):
            self.draft(code)
            self.assert_refused(self.do(Verb.READY, PM, ReadyArgs()), RefusalCode.REPRO_NOT_RUNNABLE)
        self.seed(item_in(Phase.DRAFT, contract=contract(type="bugfix", repro=(str(self.tmp / "nope"),))))
        self.assert_refused(self.do(Verb.READY, PM, ReadyArgs()), RefusalCode.REPRO_NOT_RUNNABLE)

    def test_the_repro_in_the_ready_patch_is_the_one_that_runs(self):
        self.draft("pass")
        out = self.do(Verb.READY, PM, ReadyArgs(ContractPatch(repro=(PY, "-c", "raise SystemExit(2)"))))
        self.assertIsInstance(out, Applied, out)
        self.assertEqual(self.item().failing_before.exit_code, 2)

    def test_a_feature_runs_no_repro(self):
        self.seed(item_in(Phase.DRAFT, contract=contract()))
        self.assertIsInstance(self.do(Verb.READY, PM, ReadyArgs()), Applied)
        self.assertFalse((self.root / ".mesh" / "runs").exists())


class PrecheckFactTests(FactsCase):
    """`unblock` (D68): the environment precheck runs at HEAD; a refused precheck writes nothing to the item."""

    def blocked(self):
        self.seed(item_in(Phase.BLOCKED_ENVIRONMENT, contract=contract(version=1)))

    def unblock(self):
        return self.do(Verb.UNBLOCK, PM)

    def test_passing_precheck_commands_unblock(self):
        self.config(toml_commands(py("pass", kind="env"), table="precheck"))
        self.blocked()
        self.assertIsInstance(self.unblock(), Applied)
        self.assertEqual((self.item().phase, self.item().counters.environment_unblock), (Phase.READY, 1))

    def test_a_failing_precheck_command_refuses_and_writes_nothing(self):
        self.config(toml_commands(py("pass", kind="env"), py("raise SystemExit(1)", kind="docker"), table="precheck"))
        self.blocked()
        version = self.item().version
        out = self.unblock()
        self.assert_refused(out, RefusalCode.PRECHECK_REFUSED)
        self.assertIn("docker", out.refusal.detail)
        self.assertEqual((self.item().phase, self.item().version), (Phase.BLOCKED_ENVIRONMENT, version))

    def test_without_precheck_commands_the_verification_tools_must_be_runnable(self):
        self.config(toml_commands(py("raise SystemExit(1)")))  # a failing test is a defect, not the environment
        self.blocked()
        self.assertIsInstance(self.unblock(), Applied)
        self.config(toml_commands(Command((str(self.tmp / "missing-tool"),), "tests")))
        self.blocked()
        self.assert_refused(self.unblock(), RefusalCode.PRECHECK_REFUSED)

    def test_nothing_configured_refuses(self):
        self.blocked()
        self.assert_refused(self.unblock(), RefusalCode.PRECHECK_REFUSED)


class ProseNeverVerifiedTests(FactsCase):
    """Section 7: a lead's prose report never reaches `verified`; only commands the mesh ran count."""

    def ctx(self, caller):
        return tools.Context(self.store, self.engine(), lambda: caller, root=self.root, env=self.env)

    def test_a_report_in_the_verify_arguments_is_refused_at_the_boundary(self):
        work = self.commit_file("feature.txt", "done\n")
        self.config(toml_commands(py("raise SystemExit(1)")))
        self.verifying(work)
        res = tools.call(
            self.ctx(BACKEND),
            "work_transition",
            {"item_id": "WO-0001", "verb": "verify", "args": {"attempt_id": 1, "report": "all tests pass, verified"}},
        )
        self.assertFalse(res.ok)
        self.assertEqual(res.data["error"]["code"], "invalid_args")
        self.assertEqual(self.item().phase, Phase.VERIFYING)

    def test_the_leads_claim_is_ignored_when_the_mesh_run_fails(self):
        work = self.commit_file("feature.txt", "done\n")
        self.config(toml_commands(py("raise SystemExit(1)")))
        self.verifying(work)
        res = tools.call(
            self.ctx(BACKEND), "work_transition", {"item_id": "WO-0001", "verb": "verify", "args": {"attempt_id": 1}}
        )
        self.assertFalse(res.ok)
        self.assertEqual(res.data["error"]["reason"], "verification_failed")
        self.assertEqual(self.item().phase, Phase.VERIFYING)

    def test_no_configured_command_means_nothing_was_proven(self):
        work = self.commit_file("feature.txt", "done\n")
        self.verifying(work)
        self.assert_refused(self.do(Verb.VERIFY, BACKEND, VerifyArgs(1)), RefusalCode.VERIFICATION_FAILED)
        self.assertEqual(self.item().phase, Phase.VERIFYING)


class ProductionWiringTests(RepoCase):
    def test_the_project_context_chains_the_git_provider_after_projmux(self):
        Store(self.root / ".mesh").create()
        ctx = tools.Context.for_project(self.root, self.env)
        req = Request(Verb.MESH, Caller.mesh(), NoArgs(), AT)
        got = ctx.engine.facts(frozenset({Fact.HEAD, Fact.MERGED_COMMIT}), doc_of(), req, ctx.store.read_config())
        self.assertEqual((got.head, got.merged_commit), (self.base, self.base))


if __name__ == "__main__":
    unittest.main()
