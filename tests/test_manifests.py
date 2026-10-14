import json
import re
import unittest

from _helpers import ROOT

KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        self.market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())

    def test_plugin_identity(self):
        self.assertEqual(self.plugin["name"], "supervisor-mesh")
        self.assertRegex(self.plugin["name"], KEBAB)
        self.assertRegex(self.plugin["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(self.plugin["license"], "MIT")
        self.assertNotIn("<", json.dumps(self.plugin), "placeholder left in plugin.json")

    def test_marketplace_points_at_repo_root(self):
        self.assertEqual(self.market["name"], "supervisor-mesh")
        [entry] = self.market["plugins"]
        self.assertEqual(entry["name"], self.plugin["name"])
        self.assertIn(entry["source"], (".", "./"))
        self.assertNotIn("version", entry, "pre-release: marketplace entry must omit version (DEC-0001)")

    def test_license_and_gitignore(self):
        self.assertTrue((ROOT / "LICENSE").read_text().startswith("MIT License"))
        ignored = (ROOT / ".gitignore").read_text().splitlines()
        for line in (".env", ".auth/", "*.sqlite"):
            self.assertIn(line, ignored)


if __name__ == "__main__":
    unittest.main()
