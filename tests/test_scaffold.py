import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from _helpers import ROOT

sys.path.insert(0, str(ROOT / "skills/init"))
import scaffold  # noqa: E402

PILLARS = ["AGENTS.md", "docs/decisions/0001-decision-log.md", "docs/conventions/glossary.md",
           "tests/README.md", "scripts/audit.sh", "scripts/gc/check_structure.py",
           ".github/workflows/housekeeping.yml", ".gitignore"]


def run(target: Path, *args: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = scaffold.main(["--target", str(target), *args])
    return code, buf.getvalue()


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ambiguous_stack_asks(self):
        code, out = run(self.dir)
        self.assertEqual(code, 3)
        self.assertIn("STACK AMBIGUOUS", out)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_dry_run_writes_nothing(self):
        code, out = run(self.dir, "--stack", "none", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("CREATE  AGENTS.md", out)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_four_pillars_then_idempotent(self):
        run(self.dir, "--stack", "none")
        for rel in PILLARS:
            self.assertTrue((self.dir / rel).exists(), rel)
        snapshot = {p: p.read_bytes() for p in self.dir.rglob("*") if p.is_file()}
        code, out = run(self.dir, "--stack", "none")
        self.assertEqual(code, 0)
        self.assertIn("created=0 merged=0", out)
        self.assertEqual(snapshot, {p: p.read_bytes() for p in self.dir.rglob("*") if p.is_file()})

    def test_existing_files_preserved_and_merged_once(self):
        (self.dir / "AGENTS.md").write_text("# My project\nkeep me\n")
        (self.dir / ".gitignore").write_text("node_modules/\n.env\n")
        (self.dir / "tests").mkdir()
        (self.dir / "tests/README.md").write_text("mine\n")
        run(self.dir, "--stack", "none")
        run(self.dir, "--stack", "none")
        agents = (self.dir / "AGENTS.md").read_text()
        self.assertTrue(agents.startswith("# My project\nkeep me\n"))
        self.assertEqual(agents.count(scaffold.AGENTS_MARKER), 1)
        ignore = (self.dir / ".gitignore").read_text().splitlines()
        self.assertEqual(ignore.count(".env"), 1)
        self.assertIn("node_modules/", ignore)
        self.assertEqual((self.dir / "tests/README.md").read_text(), "mine\n")

    def test_typescript_stack(self):
        (self.dir / "package.json").write_text(json.dumps({"name": "x", "scripts": {"lint": "mine"}}, indent=2))
        code, out = run(self.dir)
        self.assertEqual(code, 0)
        self.assertIn("stack: ts", out)
        self.assertTrue((self.dir / ".dependency-cruiser.cjs").exists())
        scripts = json.loads((self.dir / "package.json").read_text())["scripts"]
        self.assertEqual(scripts["lint"], "mine", "existing script must not be overwritten")
        self.assertIn("typecheck", scripts)

    def test_python_stack(self):
        (self.dir / "pyproject.toml").write_text('[project]\nname = "x"\n')
        run(self.dir)
        run(self.dir)
        text = (self.dir / "pyproject.toml").read_text()
        self.assertTrue(text.startswith('[project]\nname = "x"\n'))
        self.assertEqual(text.count("[tool.ruff]"), 1)
        self.assertTrue((self.dir / ".importlinter").exists())


if __name__ == "__main__":
    unittest.main()
