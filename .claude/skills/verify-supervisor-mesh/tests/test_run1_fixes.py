"""Fixes from the WO-0024 live run 1 (a1bb251): F9 (mesh_init proof needs its fields), F11 (the apply allowance list,
read with hash-diff --allow-file) and F12 (a repo-local git identity in the scratch repo only). Run against an older
smv.py (SMV_UNDER_TEST) to see the red result."""
import hashlib
import json
import subprocess
import unittest

from harness import SKILL, Env

PREFIX = "mcp__plugin_supervisor-mesh_supervisor-mesh__"


def out(r) -> str:
    return r.stdout + r.stderr


def init_transcript(path, calls) -> None:
    """calls: (args, result) per mesh_init call, in order."""
    recs = []
    for n, (args, res) in enumerate(calls):
        tid = f"toolu_init{n}"
        recs.append({"type": "assistant", "timestamp": f"2099-01-01T00:0{n}:00.000Z", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": PREFIX + "mesh_init", "input": args}]}})
        recs.append({"type": "user", "timestamp": f"2099-01-01T00:0{n}:01.000Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": json.dumps(res)}]}]}})
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


AMBIGUOUS = ({"apply": False}, {"ok": True, "applied": False, "exit_code": 3, "plan_id": "", "stack": None})
DRY = ({"apply": False, "stack": "none"}, {"ok": True, "applied": False, "exit_code": 0, "plan_id": "p-1",
                                           "summary": {"create": 16}})
APPLY = ({"apply": True, "stack": "none", "plan_id": "p-1"}, {"ok": True, "applied": True, "exit_code": 0,
                                                             "plan_id": "p-1", "summary": {"create": 16}})


class MeshInitFields(unittest.TestCase):
    def run_calls(self, *calls, args=()):
        e = Env(self.addCleanup)
        t = e.base / "pm.jsonl"
        init_transcript(t, calls)
        e.make_run(pm_transcript=t)
        return e.smv("mcp-calls", *args)

    def test_f9_ambiguous_dry_run_passes_expect_but_not_the_field_check(self):
        self.assertEqual(self.run_calls(AMBIGUOUS, args=("--expect", "mesh_init")).returncode, 0, "the run-1 hole")
        r = self.run_calls(AMBIGUOUS, args=("--mesh-init", "dry-run"))
        self.assertEqual(r.returncode, 1, out(r))
        self.assertEqual(self.run_calls(DRY, args=("--mesh-init", "dry-run")).returncode, 0)

    def test_f9_apply_needs_the_dry_runs_plan_id_and_exit_0(self):
        self.assertEqual(self.run_calls(DRY, APPLY, args=("--mesh-init", "apply")).returncode, 0)
        other = ({**APPLY[0], "plan_id": "p-2"}, {**APPLY[1], "plan_id": "p-2"})
        self.assertEqual(self.run_calls(DRY, other, args=("--mesh-init", "apply")).returncode, 1, "another plan")
        bad = (APPLY[0], {**APPLY[1], "exit_code": 3})
        self.assertEqual(self.run_calls(DRY, bad, args=("--mesh-init", "apply")).returncode, 1, "exit_code 3")
        self.assertEqual(self.run_calls(APPLY, args=("--mesh-init", "apply")).returncode, 1, "no dry run before")

    def test_expect_field(self):
        self.assertEqual(self.run_calls(DRY, args=("--expect-field", "mesh_init:summary.create=16")).returncode, 0)
        self.assertEqual(self.run_calls(DRY, args=("--expect-field", "mesh_init:summary.create=17")).returncode, 1)
        self.assertEqual(self.run_calls(AMBIGUOUS, args=("--expect-field", "mesh_init:plan_id=*")).returncode, 1)


class ApplyAllowance(unittest.TestCase):
    ALLOW = SKILL / "features/mcp-init.allow"

    def test_f11_the_listed_apply_changes_pass_and_a_missing_lock_is_unmet(self):
        e = Env(self.addCleanup)
        e.make_run()
        settings = e.proj / ".claude/settings.json"
        settings.parent.mkdir()
        settings.write_text("{}")
        self.assertEqual(e.smv("hash", "--label", "pre").returncode, 0)
        rows = [x.split(" ", 1) for x in self.ALLOW.read_text().splitlines() if x and not x.startswith("#")]
        for kind, rel in rows:
            target = e.proj / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{kind} {rel}\n")
        self.assertEqual(e.smv("hash", "--label", "post").returncode, 0)
        r = e.smv("hash-diff", "pre", "post", "--allow-file", str(self.ALLOW))
        self.assertEqual(r.returncode, 0, out(r))
        self.assertIn("ONLY ALLOWED CHANGES", out(r))
        self.assertEqual(len(rows), 18, "16 files + .mesh/state.lock + CHANGED .claude/settings.json")
        (e.proj / ".mesh/state.lock").unlink()
        self.assertEqual(e.smv("hash", "--label", "post2").returncode, 0)
        r = e.smv("hash-diff", "pre", "post2", "--allow-file", str(self.ALLOW))
        self.assertEqual(r.returncode, 1, out(r))
        self.assertIn("UNMET ADDED .mesh/state.lock", out(r))

    def test_allow_file_outside_the_skill_dir_is_refused(self):
        e = Env(self.addCleanup)
        e.make_run()
        self.assertEqual(e.smv("hash", "--label", "a").returncode, 0)
        f = e.base / "x.allow"
        f.write_text("ADDED x\n")
        self.assertEqual(e.smv("hash-diff", "a", "a", "--allow-file", str(f)).returncode, 2)


class ScratchIdentity(unittest.TestCase):
    def test_f12_local_identity_in_the_scratch_repo_only(self):
        e = Env(self.addCleanup)
        e.make_run()
        lead_cfg = e.lead / ".git/config"
        before = hashlib.sha256(lead_cfg.read_bytes()).hexdigest()
        strict = {**e.env, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly",
                  "GIT_CONFIG_VALUE_0": "true"}  # no identity guessed from the host: only config counts

        def commit() -> int:
            return subprocess.run(["git", "-C", str(e.proj), "-c", "commit.gpgsign=false", "commit", "-q",
                                   "--allow-empty", "-m", "x"], env=strict, capture_output=True).returncode
        self.assertNotEqual(commit(), 0, "a bare git init has no identity (run 1: exit 128)")
        r = e.py("run = m.Run.load(None)\nsys.exit(m.scratch_identity(run))")
        self.assertEqual(r.returncode, 0, out(r))
        self.assertEqual(commit(), 0)

        def local(key: str) -> str:
            return subprocess.run(["git", "-C", str(e.proj), "config", "--local", "--get", key], env=e.env,
                                  capture_output=True, text=True).stdout.strip()
        self.assertEqual((local("user.name"), local("user.email")), ("smv", "smv-qa"))
        self.assertNotIn("@", local("user.email"))
        self.assertEqual(hashlib.sha256(lead_cfg.read_bytes()).hexdigest(), before, "the lead tree is untouched")
        self.assertFalse((e.home / ".gitconfig").exists(), "never --global")
        self.assertEqual(e.run_json()["git_identity"]["scope"], "local")
        steps = "".join(p.read_text() for p in (e.run_root / "evidence/steps").glob("*git-config*"))
        self.assertIn("config --local user.email smv-qa", steps)

    def test_launch_calls_it_right_after_git_init(self):
        e = Env(self.addCleanup)
        r = e.py("import inspect\nsrc = inspect.getsource(m.launch_steps)\n"
                 "i, j = src.index('\"git-init\"'), src.index('scratch_identity(run)')\n"
                 "print(i < j < src.index('write_snapshot(run, \"baseline\")'))")
        self.assertEqual(r.stdout.strip(), "True", out(r))


if __name__ == "__main__":
    unittest.main()
