import subprocess
import unittest

from _helpers import ROOT


class ScrubTests(unittest.TestCase):
    def test_repo_is_clean(self):
        r = subprocess.run([str(ROOT / "scripts/scrub.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
