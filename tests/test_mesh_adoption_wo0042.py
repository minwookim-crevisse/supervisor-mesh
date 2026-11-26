"""WO-0042 (adoption feedback A1-A4, A6, A7): D131 the seeded verify entrypoint, D132 `work_new` checks the type,
D134 the git-author hint and the Ship state-files line. Real bash and real git in temp dirs; no projmux / tmux / claude.
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import ROOT
from test_mesh_review_r1 import InProcess

sys.path.insert(0, str(ROOT / "skills" / "init"))
sys.path.insert(0, str(ROOT))
import scaffold  # noqa: E402

from mesh import defaults, init, mcp_server, state, store, tools  # noqa: E402
from mesh.model import DEFAULT_TYPES, Config, NewArgs, RefusalCode, TypeSpec, Verb  # noqa: E402

VERIFY = "scripts/verify.sh"
BASH = "/bin/bash"
ALLOWED = ", ".join(sorted(DEFAULT_TYPES))


def printed(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = scaffold.main(argv)
    return code, buf.getvalue()


def apply_init(root: Path, stack: str = "none") -> init.MeshInitResult:
    plan = init.mesh_init(root, tools.PLUGIN_ROOT, stack, apply=False, plan_id=None).plan_id
    return init.mesh_init(root, tools.PLUGIN_ROOT, stack, apply=True, plan_id=plan)


class TempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "proj"
        self.root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------- D131
class D131ConfigTests(unittest.TestCase):
    def test_a_fresh_config_has_exactly_one_verify_command_bash_scripts_verify_sh(self):
        for stack in ("ts", "python", "none", None):
            text = defaults.config_toml(stack)
            cfg = store.parse_config(text)
            self.assertEqual([c.argv for c in cfg.verify_commands], [("bash", VERIFY)], stack)
            self.assertEqual(text.count("\n[[verify.commands]]"), 1, stack)
            self.assertIn("pinned at start", text, stack)
            self.assertIn("change checks inside scripts/verify.sh", text, stack)
        ts = store.parse_config(defaults.config_toml("ts")).verify_commands[0]
        for pattern in defaults.TS_FAIL_IF_OUTPUT:
            self.assertIn(pattern, ts.fail_if_output)


class D131InitTests(TempDir):
    def test_init_creates_an_executable_verify_script(self):
        code, out = printed(["--target", str(self.root), "--stack", "none"])
        self.assertEqual(code, 0)
        self.assertIn(f"CREATE  {VERIFY}\n", out)
        script = self.root / VERIFY
        self.assertEqual(script.stat().st_mode & 0o777, 0o755)
        text = script.read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("set -euo pipefail", text)
        self.assertEqual(text, (init.TEMPLATES / "verify" / "verify.sh").read_text())

    def test_an_existing_verify_script_is_never_overwritten(self):
        (self.root / "scripts").mkdir()
        (self.root / VERIFY).write_text("#!/bin/sh\nmine\n")
        result = apply_init(self.root)
        self.assertIn(init.Row("SKIP", VERIFY, "exists"), result.rows)
        self.assertEqual((self.root / VERIFY).read_text(), "#!/bin/sh\nmine\n")

    def test_mesh_init_writes_the_one_command_config_and_never_rewrites_an_existing_one(self):
        apply_init(self.root)
        cfg = store.Store(self.root / ".mesh").read_config()
        self.assertEqual([c.argv for c in cfg.verify_commands], [("bash", VERIFY)])
        other = self.tmp / "other"
        (other / ".mesh").mkdir(parents=True)
        mine = 'operator_channel = "terminal"\n# my own config, no verify yet\n'
        (other / ".mesh" / "config.toml").write_text(mine)
        result = apply_init(other)
        self.assertIn(init.Row("SKIP", ".mesh/config.toml", "exists"), result.rows)
        self.assertEqual((other / ".mesh" / "config.toml").read_text(), mine)


class D131ScriptRunTests(TempDir):
    """The seeded script under real bash on a tree mesh_init produced. PATH holds only a bin dir of our own (python3
    plus the fakes a test adds), so a real npm / ruff / pytest on the machine is never run."""

    def setUp(self):
        super().setUp()
        apply_init(self.root)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.bin / "python3").symlink_to(sys.executable)
        self.log = self.tmp / "calls.log"

    def fake(self, name: str, code: int = 0) -> None:
        path = self.bin / name
        path.write_text(f'#!{BASH}\necho "{name} $*" >> "{self.log}"\nexit {code}\n')
        path.chmod(0o755)

    def run_script(self) -> subprocess.CompletedProcess:
        env = {"PATH": str(self.bin), "HOME": str(self.tmp)}
        return subprocess.run([BASH, VERIFY], cwd=self.root, env=env, capture_output=True, text=True, timeout=60)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_a_docs_only_tree_passes(self):
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("no doc drift", res.stdout)
        self.assertIn("no structure drift", res.stdout)

    def test_seeded_docs_drift_fails_it(self):
        with (self.root / "AGENTS.md").open("a") as fh:
            fh.write("\nSee `docs/no-such-file.md`.\n")
        res = self.run_script()
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("DOC DRIFT", res.stdout)
        self.assertNotIn("no structure drift", res.stdout)  # the first failure stops the script

    def test_seeded_structure_drift_fails_it(self):
        (self.root / "temp_notes.md").write_text("x\n")
        res = self.run_script()
        self.assertNotEqual(res.returncode, 0, res.stdout)

    def test_package_json_runs_npm_ci_and_only_the_scripts_that_exist(self):
        self.fake("npm")
        (self.root / "package.json").write_text('{"name": "x", "scripts": {"test": "vitest run", "lint": "eslint ."}}')
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.calls(), ["npm ci", "npm run lint", "npm run test"])

    def test_a_failing_npm_script_fails_it(self):
        self.fake("npm", code=1)
        (self.root / "package.json").write_text('{"name": "x", "scripts": {"test": "x"}}')
        res = self.run_script()
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(self.calls(), ["npm ci"])

    def test_pyproject_runs_ruff_and_pytest_when_installed_and_skips_them_when_not(self):
        (self.root / "pyproject.toml").write_text('[project]\nname = "x"\n')
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.calls(), [])
        self.assertIn("ruff not installed", res.stdout)
        self.assertIn("pytest not installed", res.stdout)
        self.fake("ruff")
        self.fake("pytest")
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.calls(), ["ruff check .", "pytest -q"])


# ---------------------------------------------------------------- D132
class D132StateTests(unittest.TestCase):
    def test_new_refuses_an_unknown_type_with_contract_incomplete_and_lists_the_allowed_types(self):
        from test_mesh_state import PM, Sim, new_args

        out = Sim().do(Verb.NEW, PM, new_args(type="chore"))
        self.assertEqual(out.refusal.code, RefusalCode.CONTRACT_INCOMPLETE)
        self.assertIn(f"unknown type 'chore'; allowed: {ALLOWED}", out.refusal.detail)
        types = {**DEFAULT_TYPES, "chore": TypeSpec()}
        sim = Sim(cfg=Config(types=types))
        sim.ok(Verb.NEW, PM, new_args(type="chore"))
        self.assertEqual(sim.item().contract.type, "chore")


class D132ToolTests(InProcess):
    def new(self, **over):
        args = {"goal": "g", "done_criteria": ["c"], "role": "backend", "type": "feature", **over}
        return tools.call(self.ctx(), "work_new", args)

    def test_work_new_refuses_an_unknown_type_and_writes_no_item(self):
        self.store.create()
        out = self.new(type="chore")
        self.assertFalse(out.ok)
        err = out.data["error"]
        self.assertEqual((err["code"], err["reason"]), ("refused", "contract_incomplete"))
        self.assertIn(f"allowed: {ALLOWED}", err["message"])
        self.assertEqual(self.store.read().doc.items, {})
        self.assertEqual([r["code"] for r in self.store.refusals()], ["contract_incomplete"])

    def test_a_custom_type_declared_in_the_config_is_accepted(self):
        self.store.create()
        (self.root / ".mesh" / "config.toml").write_text("[types.chore]\nrequired_evidence = []\n")
        out = self.new(type="chore")
        self.assertTrue(out.ok, out.data)
        self.assertEqual(self.store.read().doc.items["WO-0001"].contract.type, "chore")

    def listed_enum(self) -> list[str]:
        listed = {t["name"]: t for t in mcp_server._tools_list(self.ctx())["tools"]}
        return listed["work_new"]["inputSchema"]["properties"]["type"]["enum"]

    def test_the_work_new_schema_lists_the_allowed_types_as_an_enum(self):
        schema = tools.REGISTRY["work_new"].input_schema()
        self.assertEqual(schema["properties"]["type"]["enum"], sorted(DEFAULT_TYPES))
        self.assertEqual(
            {k: v for k, v in schema.items() if k != "properties"},
            {k: v for k, v in tools.json_schema(NewArgs).items() if k != "properties"},
        )
        self.store.create()
        (self.root / ".mesh" / "config.toml").write_text("[types.chore]\n")
        self.assertEqual(self.listed_enum(), sorted([*DEFAULT_TYPES, "chore"]))
        (self.root / ".mesh" / "config.toml").write_text("not = = toml\n")  # unparsable: the defaults, never a crash
        self.assertEqual(self.listed_enum(), sorted(DEFAULT_TYPES))
        self.assertIn("new", [r.id for r in state.TABLE if "type_known" in r.guards])


# ---------------------------------------------------------------- D134
class D134AuthorHintTests(TempDir):
    """A temp repo; HOME and GIT_CONFIG_GLOBAL point at an empty file and the system config is off, so the machine's
    real git config is never read."""

    def setUp(self):
        super().setUp()
        empty = self.tmp / "gitconfig"
        empty.write_text("")
        self.env = {"HOME": str(self.tmp), "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"}
        with mock.patch.dict(os.environ, self.env):
            subprocess.run(["git", "init", "-q", str(self.root)], check=True, timeout=30)

    def test_the_hint_is_printed_and_returned_when_user_name_is_unset_and_absent_when_set(self):
        argv = ["--target", str(self.root), "--stack", "none", "--dry-run"]
        with mock.patch.dict(os.environ, self.env):
            code, out = printed(argv)
            self.assertEqual(code, 0)
            self.assertIn(init.AUTHOR_HINT + "\n", out)
            self.assertTrue(out.endswith(init.AUTHOR_HINT + "\n"))
            preview = init.scaffold(self.root, "none", dry_run=True)
            self.assertEqual(out, init.render_text(preview))
            self.assertIn(init.AUTHOR_HINT, init.mesh_init(self.root, tools.PLUGIN_ROOT, "none", False, None).hints)
            subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test Author"], check=True, timeout=30)
            code, out = printed(argv)
            self.assertNotIn(init.AUTHOR_HINT, out)
            self.assertEqual(init.mesh_init(self.root, tools.PLUGIN_ROOT, "none", False, None).hints, ())
        self.assertNotIn("Test Author", out)


class D134ToolHintTests(InProcess):
    def test_the_hint_reaches_the_mesh_init_tool_result_through_the_servers_env(self):
        empty = self.tmp / "gitconfig"
        empty.write_text("")
        git_env = {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, timeout=30, env={**self.ctx().env, **git_env})
        out = tools.call(self.ctx(**git_env), "mesh_init", {"stack": "none"})
        self.assertTrue(out.ok, out.data)
        self.assertIn(init.AUTHOR_HINT, out.data["next"])
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test Author"], check=True, timeout=30)
        out = tools.call(self.ctx(**git_env), "mesh_init", {"stack": "none"})
        self.assertNotIn(init.AUTHOR_HINT, out.data["next"])


class D134ShipLineTests(unittest.TestCase):
    def test_the_ship_lead_commits_state_json_with_each_batch_and_never_the_ignored_logs(self):
        text = (ROOT / "agents" / "role-lead-ship.md").read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if ".mesh/state.json" in line]
        self.assertEqual(len(lines), 1)
        self.assertIn("each batch commit", lines[0])
        self.assertIn("git-ignored", lines[0])
        self.assertNotIn("events.jsonl", text)  # D134 as narrowed: event text is operator prompts (D123), never tracked


if __name__ == "__main__":
    unittest.main()
