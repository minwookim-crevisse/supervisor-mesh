"""WO-0022 step 4 (W3): the scaffold seam (`mesh/init.py`), `mesh_init` (dry run / apply), the section-9a hook merge and
the legacy tracker import it runs. projmux / tmux / claude are fakes; HOME is a temp dir."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from _helpers import ROOT

sys.path.insert(0, str(ROOT / "skills" / "init"))
sys.path.insert(0, str(ROOT))
import scaffold  # noqa: E402

from mesh import init  # noqa: E402


def printed(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = scaffold.main(argv)
    return code, buf.getvalue()


class ScaffoldSeamTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_wrapper_is_the_mesh_init_main_and_keeps_the_marker(self):
        self.assertIs(scaffold.main, init.main)
        self.assertEqual(scaffold.AGENTS_MARKER, "<!-- supervisor-mesh:harness -->")

    def test_the_structured_result_renders_to_the_printed_lines(self):
        for stack in ("ts", "python", "none"):
            target = self.dir / stack
            target.mkdir()
            argv = ["--target", str(target), "--stack", stack]
            preview = init.scaffold(target, stack, dry_run=True)
            self.assertEqual(printed([*argv, "--dry-run"]), (0, init.render_text(preview)), stack)
            self.assertTrue(all(r.action in ("CREATE", "MERGE", "SKIP") for r in preview.rows))
            self.assertTrue(all(not Path(r.path).is_absolute() for r in preview.rows))
            code, out = printed(argv)  # apply: the same rows as the preview, without the dry-run tag
            self.assertEqual(out, init.render_text(preview).replace(", dry-run)", ")"), stack)
            rerun = init.scaffold(target, stack, dry_run=False)
            self.assertEqual({r.action for r in rerun.rows}, {"SKIP"}, stack)
            self.assertEqual(printed(argv), (0, init.render_text(rerun)), stack)

    def test_a_dry_run_writes_nothing_and_an_ambiguous_stack_is_exit_3(self):
        result = init.scaffold(self.dir, "auto", dry_run=True)
        self.assertEqual((result.exit_code, result.stack, result.rows), (3, None, ()))
        self.assertTrue(init.render_text(result).startswith("STACK AMBIGUOUS"))
        result = init.scaffold(self.dir, "none", dry_run=True)
        self.assertEqual(list(self.dir.iterdir()), [])
        self.assertGreater(len(result.rows), 10)


TRACKER = """# Tracker

| id | title | role | depends | status | report |
|---|---|---|---|---|---|
| WO-0002 | Later row first | qa | WO-0001 | waiting | — |
| WO-0001 | First | security, backend | — | FAIL (2 🔴) | `docs/reports/a.md`, `docs/reports/b.md` |

Prose after the table.
"""


class MeshInitToolTests(unittest.TestCase):
    """`mesh_init` through the tool layer, as the PM (an injected caller) unless a test says otherwise."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "proj"
        self.root.mkdir()
        (self.tmp / "home").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def call(self, args, caller="pm:pm-uid"):
        from _mesh_fakes import Fakes, parse_caller

        from mesh import tools

        env = Fakes(self.tmp / "fakes").env({"PATH": "/usr/bin:/bin", "HOME": str(self.tmp / "home")})
        who = parse_caller(caller)
        ctx = tools.Context.for_project(self.root, env, clock=lambda: "2026-11-18T00:00:00Z", caller=lambda: who)
        return tools.call(ctx, "mesh_init", args)

    def tree(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))

    def test_the_dry_run_writes_nothing_and_returns_the_plan_and_its_id(self):
        out = self.call({"stack": "none"})
        self.assertTrue(out.ok, out.data)
        self.assertEqual(self.tree(), [])
        self.assertFalse(out.data["applied"])
        self.assertRegex(out.data["plan_id"], r"^[0-9a-f]{16}$")
        paths = [r["path"] for r in out.data["rows"]]
        for rel in (
            "AGENTS.md",
            ".mesh/state.json",
            ".mesh/config.toml",
            ".mesh/.gitignore",
            ".mesh/bin/mesh",
            ".claude/settings.json",
        ):
            self.assertIn(rel, paths)
        self.assertNotIn(str(self.tmp), json.dumps(out.data))

    def test_apply_needs_the_plan_id_of_the_dry_run_and_refuses_a_changed_plan(self):
        plan = self.call({"stack": "none"}).data["plan_id"]
        missing = self.call({"stack": "none", "apply": True})
        self.assertEqual(missing.data["error"]["code"], "invalid_args")
        (self.root / "AGENTS.md").write_text("# mine\n")
        changed = self.call({"stack": "none", "apply": True, "plan_id": plan})
        self.assertEqual(changed.data["error"]["code"], "plan_changed")
        self.assertFalse((self.root / ".mesh").exists())
        plan = self.call({"stack": "none"}).data["plan_id"]
        applied = self.call({"stack": "none", "apply": True, "plan_id": plan})
        self.assertTrue(applied.ok, applied.data)
        self.assertTrue(applied.data["applied"])
        self.assertTrue((self.root / ".mesh" / "state.json").is_file())
        self.assertEqual((self.root / ".mesh" / "config.toml").read_text().count('operator_channel = "terminal"'), 1)
        launcher = self.root / ".mesh" / "bin" / "mesh"
        self.assertTrue(launcher.stat().st_mode & 0o100)
        self.assertIn("mesh/cli.py", launcher.read_text())
        self.assertIn("bin/", (self.root / ".mesh" / ".gitignore").read_text().splitlines())

    def apply(self, caller="pm:pm-uid"):
        plan = self.call({"stack": "none"}, caller).data["plan_id"]
        return self.call({"stack": "none", "apply": True, "plan_id": plan}, caller)

    def test_a_rerun_skips_every_row(self):
        self.apply()
        again = self.call({"stack": "none"}).data
        self.assertEqual({r["action"] for r in again["rows"]}, {"SKIP"})

    def test_the_legacy_tracker_is_imported_once_raw_text_kept_never_verified_and_the_file_untouched(self):
        (self.root / "docs").mkdir()
        (self.root / "docs" / "tracker.md").write_text(TRACKER)
        preview = self.call({"stack": "none"}).data["legacy_import"]
        self.assertEqual(preview["status"], "will import")
        self.assertEqual(
            preview["mapping"],
            [
                {"id": "WO-0002", "status_raw": "waiting", "status": "open"},
                {"id": "WO-0001", "status_raw": "FAIL (2 🔴)", "status": "fail"},
            ],
        )
        self.assertEqual(self.apply().data["legacy_import"]["status"], "imported")
        self.assertEqual((self.root / "docs" / "tracker.md").read_text(), TRACKER)
        state = json.loads((self.root / ".mesh" / "state.json").read_text())
        self.assertEqual(state["items"], {})
        self.assertEqual(state["legacy"]["WO-0001"]["roles"], ["security", "backend"])
        self.assertEqual(state["legacy"]["WO-0001"]["reports"], ["docs/reports/a.md", "docs/reports/b.md"])
        self.assertEqual(state["legacy"]["WO-0002"]["order"], 0)
        again = self.call({"stack": "none"}).data["legacy_import"]
        self.assertEqual((again["status"], again["mapping"]), ("already imported", []))

    def test_the_hook_merge_is_additive_marker_guarded_and_never_replaces_a_key(self):
        settings = self.root / ".claude" / "settings.json"
        settings.parent.mkdir()
        mine = {
            "permissions": {"allow": ["Bash(ls:*)"]},
            "hooks": {
                "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo mine"}]}],
                "Stop": [{"hooks": []}],
            },
        }
        settings.write_text(json.dumps(mine, indent=4))
        self.apply()
        data = json.loads(settings.read_text())
        self.assertEqual(data["permissions"]["allow"], mine["permissions"]["allow"])  # kept; D96 adds `deny`
        self.assertEqual(data["permissions"]["deny"], list(init.DENY_RULES))
        self.assertEqual(data["hooks"]["Stop"], mine["hooks"]["Stop"])
        prompt = data["hooks"]["UserPromptSubmit"]
        self.assertEqual(prompt[0], mine["hooks"]["UserPromptSubmit"][0])
        self.assertEqual(prompt[1]["hooks"][0]["command"], init.HOOK_COMMAND)
        self.assertIn('"$CLAUDE_PROJECT_DIR"/.mesh/bin/mesh event record --source terminal', init.HOOK_COMMAND)
        self.assertNotIn(str(self.tmp), settings.read_text())
        self.apply()
        self.assertEqual(len(json.loads(settings.read_text())["hooks"]["UserPromptSubmit"]), 2)

    def test_settings_that_are_not_json_are_skipped_never_overwritten(self):
        settings = self.root / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text("{ not json")
        out = self.apply()
        self.assertTrue(out.ok, out.data)
        self.assertEqual(settings.read_text(), "{ not json")
        row = next(r for r in out.data["rows"] if r["path"] == ".claude/settings.json")
        self.assertEqual(row["action"], "SKIP")

    def test_an_unlabelled_caller_is_told_to_label_the_pm_pane_and_cannot_apply(self):
        out = self.call({"stack": "none"}, caller="unknown")
        self.assertTrue(out.ok, out.data)
        self.assertTrue(any("--label role=pm" in s for s in out.data["next"]))
        refused = self.apply(caller="unknown")
        self.assertEqual(refused.data["error"]["code"], "refused")
        self.assertIn("--label role=pm", refused.data["error"]["message"])
        self.assertEqual(self.tree(), [])
        pm = self.call({"stack": "none"}).data
        self.assertFalse(any("--label role=pm" in s for s in pm["next"]))

    def test_missing_companions_show_the_d88_install_plan(self):
        out = self.call({"stack": "none"}).data
        self.assertEqual(out["companions"], {"gstack": False, "mattpocock-skills": False, "pstack": False})
        plan = out["install_plan"]
        self.assertEqual(
            [s["plugin"] for s in plan["steps"]], ["pstack", "pstack", "mattpocock-skills", "mattpocock-skills"]
        )
        self.assertTrue(all("--scope project" in s["command"] for s in plan["steps"]))
        self.assertEqual(plan["manual"][0]["plugin"], "gstack")
        self.assertTrue(any("companions_install" in s for s in out["next"]))

    def test_an_ambiguous_stack_is_reported_not_guessed(self):
        out = self.call({})
        self.assertEqual((out.ok, out.data["exit_code"], out.data["rows"]), (True, 3, []))


if __name__ == "__main__":
    unittest.main()
