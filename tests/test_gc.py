import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from _helpers import ROOT

sys.path.insert(0, str(ROOT / "scripts/gc"))
import check_docs_drift  # noqa: E402
import check_structure  # noqa: E402


def quiet(fn, argv):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        return fn(argv), buf.getvalue()


class StructureTests(unittest.TestCase):
    def test_flags_junk_and_respects_excludes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "src").mkdir()
            (root / "examples").mkdir()
            (root / "src/temp_run.py").write_text("")
            (root / "src/app_old.py").write_text("")
            (root / "examples/temp_demo.py").write_text("")
            (root / "src/ok.py").write_text("")
            code, out = quiet(check_structure.main, ["--root", d, "--exclude", "examples"])
            self.assertEqual(code, 1)
            self.assertIn("src/temp_run.py", out)
            self.assertIn("src/app_old.py", out)
            self.assertNotIn("examples", out)
            self.assertNotIn("ok.py", out)

    def test_clean_tree_passes(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "main.py").write_text("")
            self.assertEqual(quiet(check_structure.main, ["--root", d])[0], 0)


class DocsDriftTests(unittest.TestCase):
    def test_missing_path_and_deprecated_wording(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "AGENTS.md").write_text("see `real.py` and `gone.py` and `<placeholder.md>`\n")
            (root / "real.py").write_text("x = 'legacy term'\n")
            (root / "docs/conventions").mkdir(parents=True)
            (root / "docs/conventions/glossary.md").write_text("- term: meaning (deprecated: legacy term)\n")
            code, out = quiet(check_docs_drift.main, ["--root", d])
            self.assertEqual(code, 1)
            self.assertIn("gone.py", out)
            self.assertIn("legacy term", out)
            self.assertNotIn("placeholder", out)

    def test_repo_itself_has_no_doc_drift(self):
        code, out = quiet(check_docs_drift.main, ["--root", str(ROOT), "--exclude", "templates", "--exclude", "docs/core"])
        self.assertEqual(code, 0, out)


class DogfoodSyncTests(unittest.TestCase):
    def test_repo_gc_scripts_match_templates(self):
        for name in ("check_docs_drift.py", "check_structure.py"):
            self.assertEqual((ROOT / "scripts/gc" / name).read_bytes(), (ROOT / "templates/gc" / name).read_bytes(),
                             f"scripts/gc/{name} drifted from templates/gc/{name}")


if __name__ == "__main__":
    unittest.main()
