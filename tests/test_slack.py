import json
import re
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
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


class MockSlack(BaseHTTPRequestHandler):
    posts: list = []
    auth: list = []

    def log_message(self, *args):
        pass

    def _reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        MockSlack.auth.append(self.headers.get("Authorization"))
        MockSlack.posts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self._reply({"ok": True, "ts": "1.000001"})

    def do_GET(self):
        now = time.time() + 5
        self._reply({"ok": True, "messages": [
            {"ts": f"{now:.6f}", "text": "operator says hi\nsecond line"},
            {"ts": f"{now + 1:.6f}", "text": "from a bot", "bot_id": "B1"},
        ]})


class SlackMockServerTests(unittest.TestCase):
    """Runs the real scripts against a local fake Slack API (SLACK_API_BASE override)."""

    def setUp(self):
        MockSlack.posts, MockSlack.auth = [], []
        self.server = HTTPServer(("127.0.0.1", 0), MockSlack)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / ".env"
        self.env.write_text("SLACK_BOT_TOKEN=test-not-a-token\nSLACK_CHANNEL_ID=TESTCHAN\nSLACK_POLL_SECONDS=1\n"
                            f"SLACK_API_BASE=http://127.0.0.1:{self.server.server_port}\n")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_send_posts_file_content(self):
        msg = Path(self.tmp.name) / "m.txt"
        msg.write_text("done: 3 of 3 checks green")
        r = subprocess.run(["bash", str(SLACK / "slack_send.sh"), str(msg), str(self.env)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sent ts=1.000001", r.stdout)
        self.assertEqual(MockSlack.posts, [{"channel": "TESTCHAN", "text": "done: 3 of 3 checks green"}])
        self.assertEqual(MockSlack.auth, ["Bearer test-not-a-token"])

    def test_watch_emits_human_messages_only(self):
        env = {"PATH": "/usr/bin:/bin", "SLACK_STATE_DIR": str(Path(self.tmp.name) / "state")}
        proc = subprocess.Popen(["bash", str(SLACK / "slack_watch.sh"), str(self.env)],
                                stdout=subprocess.PIPE, text=True, env=env)
        try:
            line = proc.stdout.readline()
        finally:
            proc.kill()
            proc.wait()
            proc.stdout.close()
        self.assertEqual(line.strip(), "[Slack] operator says hi second line")


if __name__ == "__main__":
    unittest.main()
