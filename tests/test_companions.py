import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/init/companions.py"
SKILL = ROOT / "skills/init/SKILL.md"
COMPANIONS = ["ready", "office-hours", "plan-eng-review", "karpathy-guidelines"]
UNRELATED = ["secret-tool", "private-mkt", "hidden-helper", "corp-internal", "zz-distinct-install-dir", "9.9.9-rc"]


def run(*args: str, home: Path | None = None) -> subprocess.CompletedProcess:
    """`--home` always points at a temp dir: the real ~/.claude/skills is never consulted by a test."""
    with tempfile.TemporaryDirectory() as empty:
        argv = [*args, "--home", str(home or empty)]
        return subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, timeout=30)


def expected(installed: set[str]) -> list[str]:
    return [f"companion {n}: {'installed' if n in installed else 'missing'}" for n in COMPANIONS]


class CompanionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def plugin_dir(self, name: str, skills: list[str]) -> str:
        path = self.dir / "zz-distinct-install-dir" / name
        for s in skills:
            (path / "skills" / s).mkdir(parents=True)
            (path / "skills" / s / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def assert_clean(self, proc: subprocess.CompletedProcess, installed: set[str]) -> None:
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), expected(installed))
        self.assertEqual(proc.stderr, "")
        both = proc.stdout + proc.stderr
        for leak in [*UNRELATED, str(self.dir), "Traceback", "installed_plugins"]:
            self.assertNotIn(leak, both)

    def test_registry_reports_only_companions(self):
        reg = {
            "version": 2,
            "plugins": {
                "secret-tool@private-mkt": [
                    {"scope": "user", "installPath": self.plugin_dir("secret-tool", ["hidden-helper"]),
                     "version": "9.9.9-rc"}],
                "corp-internal@private-mkt": [
                    {"scope": "project", "installPath": self.plugin_dir("corp-internal", ["office-hours"])}],
                "karpathy-guidelines@private-mkt": [
                    {"scope": "user", "installPath": self.plugin_dir("kg", [])}],
            },
        }
        path = self.dir / "registry.json"
        path.write_text(json.dumps(reg), encoding="utf-8")
        self.assert_clean(run("--registry", str(path)), {"office-hours", "karpathy-guidelines"})

    def test_dict_entry_shape_tolerated(self):
        reg = {"plugins": {"secret-tool@private-mkt": {"installPath": self.plugin_dir("st", ["ready"])},
                           "corp-internal@private-mkt": "not-an-entry"}}
        path = self.dir / "registry.json"
        path.write_text(json.dumps(reg), encoding="utf-8")
        self.assert_clean(run("--registry", str(path)), {"ready"})

    def test_missing_registry_all_missing(self):
        self.assert_clean(run("--registry", str(self.dir / "nope" / "installed_plugins.json")), set())

    def test_malformed_registry_all_missing(self):
        path = self.dir / "registry.json"
        path.write_text("{not json secret-tool@private-mkt", encoding="utf-8")
        self.assert_clean(run("--registry", str(path)), set())

    def test_wrong_top_level_shape_all_missing(self):
        for body in ("[1, 2]", '{"plugins": 7}', '{"plugins": ["secret-tool@private-mkt"]}', "null"):
            path = self.dir / "registry.json"
            path.write_text(body, encoding="utf-8")
            self.assert_clean(run("--registry", str(path)), set())


    def skill(self, base: Path, name: str) -> Path:
        (base / name).mkdir(parents=True)
        (base / name / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
        return base / name

    def test_a_gstack_skill_under_the_home_skills_dir_counts(self):
        home = self.dir / "home"
        self.skill(home / ".claude" / "skills" / "gstack", "office-hours")
        registry = self.dir / "nope.json"
        self.assert_clean(run("--registry", str(registry), home=home), {"office-hours"})

    def test_a_symlinked_skill_dir_counts(self):
        home = self.dir / "home"
        target = self.skill(self.dir / "zz-distinct-install-dir", "pe")
        (home / ".claude" / "skills").mkdir(parents=True)
        (home / ".claude" / "skills" / "plan-eng-review").symlink_to(target, target_is_directory=True)
        self.assert_clean(run("--registry", str(self.dir / "nope.json"), home=home), {"plan-eng-review"})

    def test_the_default_registry_is_under_home(self):
        home = self.dir / "home"
        (home / ".claude" / "plugins").mkdir(parents=True)
        reg = {"plugins": {"secret-tool@private-mkt": [{"installPath": self.plugin_dir("st", ["ready"])}]}}
        (home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(reg), encoding="utf-8")
        self.assert_clean(run(home=home), {"ready"})


class SkillTextTests(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")

    def step(self, n: int) -> str:
        m = re.search(rf"^{n}\. (.*?)(?=^\d+\. |^## )", self.text, re.S | re.M)
        self.assertIsNotNone(m, f"step {n}")
        return m.group(1)

    def test_step6_uses_helper_not_registry(self):
        s6 = self.step(6)
        self.assertIn("skills/init/companions.py", s6)
        self.assertNotIn("Read `~/.claude/plugins/installed_plugins.json`", self.text)
        self.assertRegex(s6, r"(?i)do not (read|cat|print)")
        self.assertIn("never install", s6)

    def test_preview_and_apply_not_chained(self):
        s23 = self.step(2) + self.step(3)
        self.assertRegex(s23, r"(?i)separate")
        self.assertRegex(s23, r"(?i)never chain")
        self.assertIn("--dry-run &&", s23)  # the forbidden form is named explicitly


if __name__ == "__main__":
    unittest.main()
