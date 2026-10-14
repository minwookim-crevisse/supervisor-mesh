# Regression: SM-QA-01 — init --stack python crashed (FileNotFoundError) when pyproject.toml was absent,
# and an existing [tool.ruff] made init skip [tool.mypy] (type gate silently missing).
# Found by live QA panes F and E on 2026-10-14. Report: docs/qa/2026-10-14-live-qa.md
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

import tomllib
from _helpers import ROOT

sys.path.insert(0, str(ROOT / "skills/init"))
import scaffold  # noqa: E402


def run(target: Path, *args: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = scaffold.main(["--target", str(target), *args])
    return code, buf.getvalue()


class ScaffoldRegression01(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_python_stack_without_pyproject_creates_it(self):
        for dry in (["--dry-run"], []):
            code, out = run(self.dir, "--stack", "python", *dry)
            self.assertEqual(code, 0, out)
        cfg = tomllib.loads((self.dir / "pyproject.toml").read_text())
        self.assertEqual(sorted(cfg["tool"]), ["mypy", "ruff"])
        self.assertIn("CREATE  pyproject.toml", out)

    def test_existing_ruff_still_gets_mypy(self):
        (self.dir / "pyproject.toml").write_text('[project]\nname = "x"\n\n[tool.ruff]\nline-length = 100\n')
        run(self.dir)
        run(self.dir)
        text = (self.dir / "pyproject.toml").read_text()
        cfg = tomllib.loads(text)
        self.assertEqual(cfg["tool"]["ruff"]["line-length"], 100, "existing ruff config must be kept")
        self.assertTrue(cfg["tool"]["mypy"]["strict"])
        self.assertEqual(text.count("[tool.mypy]"), 1)

    def test_ts_stack_without_package_json_does_not_crash(self):
        code, out = run(self.dir, "--stack", "ts")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.dir / "package.json").exists(), "init must not invent a package.json")
        self.assertIn("SKIP    package.json", out)


if __name__ == "__main__":
    unittest.main()
