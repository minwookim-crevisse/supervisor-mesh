import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from _helpers import ROOT

SLACK = ROOT / "templates/slack"
TOKEN_RE = re.compile(r"xox[abprs]-|\bC0[A-Z0-9]{8,}\b")


class SlackTemplateTests(unittest.TestCase):
    def test_no_token_or_channel_literals(self):
        for p in SLACK.iterdir():
            self.assertIsNone(TOKEN_RE.search(p.read_text()), p.name)

    def test_env_example_is_empty(self):
        for line in (SLACK / "env.example").read_text().splitlines():
            if line.startswith(("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID")):
                self.assertTrue(line.endswith("="), line)

    def test_scripts_read_env_at_runtime(self):
        for name in ("slack_watch.sh", "slack_send.sh"):
            text = (SLACK / name).read_text()
            self.assertIn("read_env SLACK_BOT_TOKEN", text)
            self.assertEqual(subprocess.run(["bash", "-n", str(SLACK / name)]).returncode, 0)

    def test_send_fails_cleanly_without_config(self):
        with tempfile.TemporaryDirectory() as d:
            msg = Path(d) / "m.txt"
            msg.write_text("hello")
            (Path(d) / ".env").write_text("OTHER=1\n")
            r = subprocess.run(["bash", str(SLACK / "slack_send.sh"), str(msg), str(Path(d) / ".env")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("missing", r.stderr)


if __name__ == "__main__":
    unittest.main()
