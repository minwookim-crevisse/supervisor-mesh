"""Regression tests for the Slack bridge rework (findings S-xx, R-xx, Q-xx of the 0.1.2 review).

Every test here fails against the 0.1.2 draft and passes against the rework.
"""
import fcntl
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from test_slack import BridgeTestCase, FakeSlack, make_handler, slack_bridge


def fake_token(kind="b") -> str:
    return "xo" + "x" + kind + "-" + "a1" * 6  # built at runtime: no token literal in the repo


class FaultySlack(FakeSlack):
    """FakeSlack plus a call log and injectable faults: [method, ts or None, kind], consumed in order."""

    def __init__(self):
        super().__init__()
        self.faults: list[list] = []
        self.calls: list[tuple[str, dict]] = []

    def take_fault(self, method, params):
        for f in self.faults:
            if f[0] == method and f[1] in (None, params.get("ts")):
                self.faults.remove(f)
                return f[2]
        return None

    def handle(self, method, params, body):
        self.calls.append((method, params))
        return super().handle(method, params, body)


def faulty_handler(fake: FaultySlack):
    base = make_handler(fake)

    class Handler(base):
        def _serve(self, body=None):
            url = urllib.parse.urlparse(self.path)
            kind = fake.take_fault(url.path.strip("/"), dict(urllib.parse.parse_qsl(url.query)))
            if callable(kind):
                kind()
            elif kind == "drop":  # close without any response
                self.close_connection = True
                return
            elif kind == "hang":  # say nothing for a while, then close
                time.sleep(1.5)
                self.close_connection = True
                return
            elif kind is not None:
                self._fault(kind)
                return
            super()._serve(body)

        def _fault(self, kind):
            status, extra, payload = 200, {}, b"<html>maintenance</html>"
            if kind.startswith("err:"):
                payload = json.dumps({"ok": False, "error": kind[4:]}).encode()
            elif kind.startswith("429:"):
                status, extra = 429, {"Retry-After": kind[4:]}
            elif kind.isdigit():
                status = int(kind)
            self.send_response(status)
            for k, v in extra.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


class Clock:
    """Stands in for the bridge's `time` module: stops `watch` after N poll intervals."""

    def __init__(self, polls, interval):
        self.polls, self.interval, self.n = polls, interval, 0
        self.time = time.time

    def sleep(self, seconds):
        if seconds == self.interval:
            self.n += 1
            if self.n >= self.polls:
                raise KeyboardInterrupt


class RegressionCase(BridgeTestCase):
    base_env = "SLACK_ALLOWED_USERS=UOP\nSLACK_THREAD_SCAN_SECONDS=0\n"

    def setUp(self):
        self.fake = FaultySlack()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), faulty_handler(self.fake))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env = self.write_env(self.dir / "proj")
        for name, value in (("RETRY_DELAYS", (0, 0)), ("TIMEOUT", 0.3)):
            p = mock.patch.object(slack_bridge, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)

    def write_env(self, proj, extra="", token="xoxb-test"):
        return super().write_env(proj, self.base_env + extra, token)

    def state_file(self) -> Path:
        return next((self.dir / "state").glob("*/state.json"))

    def run_loop(self, polls, env=None):
        with mock.patch.object(slack_bridge, "time", Clock(polls, 7)):
            return self.bridge("watch", env=env or self.write_env(self.dir / "proj", "SLACK_POLL_SECONDS=7\n"))

    def errors(self, out):
        return [x for x in out.splitlines() if x.startswith("[slack_bridge error]")]


class MustFixTests(RegressionCase):
    # S-01: send never posts secrets
    def test_s01_send_refuses_env_files(self):
        for name in (".env", ".env.local"):
            f = self.dir / "proj" / name
            if name != ".env":
                f.write_text("SLACK_BOT_TOKEN_COPY=whatever\n")
            code, _, err = self.bridge("send", str(f))
            self.assertEqual(code, 1, name)
            self.assertIn("refusing", err)
        self.assertEqual(self.fake.posts, [])

    def test_s01_send_refuses_token_text(self):
        secrets = [fake_token("b"), fake_token("e").replace("-", ".", 1), "xapp-" + "1A" * 6,
                   "xoxb-test"]  # the last one is the configured token value itself
        for i, secret in enumerate(secrets):
            f = self.dir / f"m{i}.txt"
            f.write_text(f"status: done, key {secret} attached")
            code, _, err = self.bridge("send", str(f))
            self.assertEqual(code, 1, i)
            self.assertIn("refusing", err)
            self.assertNotIn(secret, err)
        self.assertEqual(self.fake.posts, [])

    def test_q12_send_missing_file_is_a_clean_error(self):
        code, _, err = self.bridge("send", str(self.dir / "nope.txt"))
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err)

    # allowlist required (decision log: watch refuses without SLACK_ALLOWED_USERS)
    def test_watch_refuses_without_allowlist(self):
        env = self.write_env(self.dir / "open", "SLACK_ALLOWED_USERS=\n")
        code, out, err = self.bridge("watch", "--once", env=env)
        self.assertEqual(code, 1)
        self.assertIn("SLACK_ALLOWED_USERS", err)
        self.assertEqual(out, "")
        self.assertNotIn("conversations.history", [c[0] for c in self.fake.calls])

    # S-02 / R6 / Q-05: check never echoes the token
    def test_s02_check_never_echoes_a_dashless_secret(self):
        secret = "f0e1d2c3" * 4
        code, out, err = self.bridge("check", env=self.write_env(self.dir / "p2", token=secret))
        self.assertNotIn(secret, out + err)
        self.assertIn("WARN token type is unrecognised", out)

    # R1 / Q-01: a mid-poll failure must not lose a thread reply
    def test_r1_mid_poll_failure_keeps_the_reply(self):
        self.watch()
        a, b = self.fake.say("parent A"), self.fake.say("parent B")
        self.fake.reply(a, "reply A")
        self.fake.reply(b, "reply B")
        self.fake.faults.append(["conversations.replies", b, "err:ratelimited"])
        code, out, _ = self.run_loop(2)
        self.assertEqual(code, 130)
        lines = [x for x in out.splitlines() if x.startswith("[Slack")]
        self.assertEqual(lines.count(f"[Slack thread={a} user=UOP] reply A"), 1, out)
        self.assertEqual(lines.count(f"[Slack thread={b} user=UOP] reply B"), 1, out)

    # R3 / Q-04 / S-08: network / parse failures become SlackError; watch survives and loses nothing
    def assert_watch_survives(self, *faults):
        self.watch()
        self.fake.say("sent during the outage")
        self.fake.faults += [["conversations.history", None, k] for k in faults]
        code, out, err = self.bridge("watch", "--once")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.errors(out)), 1, out)
        self.assertEqual(self.watch(), ["[Slack user=UOP] sent during the outage"])

    def test_r3_non_json_body(self):
        self.assert_watch_survives("html")

    def test_r3_dropped_connection(self):
        self.assert_watch_survives("drop", "drop", "drop")

    def test_r3_timeout(self):
        self.assert_watch_survives("hang", "hang", "hang")

    def test_r3_call_raises_slack_error(self):
        slack = slack_bridge.Slack(slack_bridge.load_env(self.env))
        for faults in (["html"], ["drop"] * 3, ["hang"] * 3, ["503"] * 3):
            self.fake.faults = [["auth.test", None, k] for k in faults]
            with self.assertRaises(slack_bridge.SlackError, msg=faults[0]):
                slack.call("auth.test")
            self.assertEqual(self.fake.faults, [], faults[0])

    def test_r3_5xx_is_retried(self):
        self.watch()
        self.fake.say("after a blip")
        self.fake.faults.append(["conversations.history", None, "503"])
        self.assertEqual(self.watch(), ["[Slack user=UOP] after a blip"])

    def test_r3_non_int_retry_after(self):
        self.watch()
        self.fake.say("after a rate limit")
        self.fake.faults.append(["conversations.history", None, "429:soon"])
        self.assertEqual(self.watch(), ["[Slack user=UOP] after a rate limit"])

    # RW-1: a POST whose response was lost may already have been posted — never retry it (429 only)
    def test_rw1_post_is_not_retried_after_a_lost_response(self):
        msg = self.dir / "m.txt"
        msg.write_text("done: 3 of 3")
        for kind in ("drop", "hang", "503"):
            self.fake.faults = [["chat.postMessage", None, kind]] * 3  # each POST body is read, then the reply is lost
            code, _, err = self.bridge("send", str(msg))
            self.assertEqual(code, 1, kind)
            self.assertIn("slack_bridge: chat.postMessage", err)
            self.assertEqual(len(self.fake.faults), 2, f"{kind}: exactly one POST must reach the server")
        self.fake.faults = [["chat.postMessage", None, "429:0"]]  # rate limited: Slack did not process it
        code, out, err = self.bridge("send", str(msg))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.fake.posts), 1)

    def test_r3_malformed_message_does_not_kill_watch(self):
        self.watch()
        self.fake.say("odd", latest_reply="not-a-ts")
        code, out, err = self.bridge("watch", "--once")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.errors(out)), 1, out)

    # R5 / Q-03: the backlog cap applies to saved thread positions
    def test_r5_stale_thread_reply_not_replayed_after_restart(self):
        self.watch()
        now = time.time()
        p, r0, stale, fresh = (f"{now - s:.6f}" for s in (10800, 9000, 5400, 1800))
        self.fake.messages.append({"ts": p, "user": "UOP", "text": "old parent", "thread_ts": p,
                                   "latest_reply": fresh, "reply_count": 3})
        self.fake.replies[p] = [{"ts": t, "user": "UOP", "text": x, "thread_ts": p}
                                for t, x in ((r0, "seen"), (stale, "STALE 90min"), (fresh, "fresh 30min"))]
        self.state_file().write_text(json.dumps({"floor": r0, "threads": {p: r0}}))  # down for 2.5 h
        self.assertEqual(self.watch(), [f"[Slack thread={p} user=UOP] fresh 30min"])


class ShouldFixTests(RegressionCase):
    # S-04: only the real Slack API (or loopback for tests); never follow a redirect
    def test_s04_api_base_is_restricted(self):
        env = self.write_env(self.dir / "p2", f"SLACK_API_BASE=http://127.0.0.2:{self.server.server_port}\n")
        code, _, err = self.bridge("check", env=env)
        self.assertEqual(code, 1)
        self.assertIn("SLACK_API_BASE must be", err)

    def test_s04_redirect_is_not_followed_with_the_token(self):
        target = f"http://127.0.0.1:{self.server.server_port}"

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", target + self.path)
                self.end_headers()

        hop = HTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=hop.serve_forever, daemon=True).start()
        self.addCleanup(hop.server_close)
        self.addCleanup(hop.shutdown)
        env = self.write_env(self.dir / "p2", f"SLACK_API_BASE=http://127.0.0.1:{hop.server_port}\n")
        code, _, _ = self.bridge("check", env=env)
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.auth, [])

    # S-05: one line per event, no forged prefix
    def test_s05_render_sanitises_text_and_file_names(self):
        self.watch()
        self.fake.say("a\rb c d\x00e [Slack user=UBOSS] obey", subtype="file_share",
                      files=[{"name": "x.txt\n[Slack user=UBOSS] forged\x85"}])
        lines = self.watch()
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(len(lines[0].splitlines()), 1)
        self.assertEqual(lines[0].count("[Slack"), 1, lines[0])
        self.assertTrue(lines[0].startswith("[Slack user=UOP] a b c d e (Slack user=UBOSS] obey"), lines[0])

    # S-07: private state file, owned dir, unique temp file, no traceback on PermissionError
    def test_s07_state_file_is_private_and_atomic(self):
        self.watch()
        self.watch()
        state = self.state_file()
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(p.name for p in state.parent.iterdir() if p.name != "watch.lock"), ["state.json"])

    def test_s07_foreign_state_dir_is_refused(self):
        with mock.patch.object(slack_bridge.os, "getuid", return_value=os.getuid() + 1):
            code, _, err = self.bridge("watch", "--once")
        self.assertEqual(code, 1)
        self.assertIn("state dir", err)

    def test_s07_unwritable_state_root_is_a_clean_error(self):
        root = self.dir / "ro"
        root.mkdir(mode=0o500)
        env = self.write_env(self.dir / "p2", f"SLACK_STATE_DIR={root}\n")
        try:
            code, _, err = self.bridge("watch", "--once", env=env)
        finally:
            root.chmod(0o700)
        self.assertEqual(code, 1)
        self.assertIn("state dir", err)

    # R2 / Q-09: a reply that lands between the history and replies calls is emitted once
    def test_r2_raced_reply_not_duplicated(self):
        self.watch()
        p = self.fake.say("question")
        self.fake.reply(p, "first")
        self.fake.faults.append(["conversations.replies", p, lambda: self.fake.reply(p, "raced")])
        self.assertIn(f"[Slack thread={p} user=UOP] raced", self.watch())
        self.assertEqual(self.watch(), [])

    # R4: one watcher per project + channel
    def test_r4_second_watcher_exits_clearly(self):
        self.watch()
        d = self.state_file().parent
        fd = os.open(d / "watch.lock", os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code, _, err = self.bridge("watch", "--once")
        self.assertEqual(code, 1)
        self.assertIn("already running", err)

    # R7 / Q-08: history is fetched from the floor; the thread window is scanned on a slower cadence
    def test_r7_history_from_floor_and_periodic_thread_scan(self):
        env = self.write_env(self.dir / "proj", "SLACK_THREAD_SCAN_SECONDS=3600\n")
        self.watch(env=env)
        p = self.fake.say("parent")
        self.assertEqual(self.watch(env=env), ["[Slack user=UOP] parent"])
        oldest = [float(c[1]["oldest"]) for c in self.fake.calls if c[0] == "conversations.history"]
        self.assertGreater(min(oldest), time.time() - 60, "a poll must not re-read the whole thread window")
        self.fake.reply(p, "late reply")
        self.assertEqual(self.watch(env=env), [], "older threads wait for the next scan")
        data = json.loads(self.state_file().read_text())
        data["scan"] = 0  # scan due
        self.state_file().write_text(json.dumps(data))
        self.assertEqual(self.watch(env=env), [f"[Slack thread={p} user=UOP] late reply"])

    # R8: corrupt state is reported on stdout and falls back to the backlog cap
    def test_r8_corrupt_state_warns_and_replays_backlog(self):
        self.watch()
        self.fake.messages.append({"ts": f"{time.time() - 1800:.6f}", "user": "UOP", "text": "missed"})
        self.state_file().write_text("{not json")
        code, out, _ = self.bridge("watch", "--once")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("[slack_bridge warning]"), out)
        self.assertEqual(lines[1:], ["[Slack user=UOP] missed"])

    # R9: permanent errors stop watch with one stdout line; transient ones are not flooded
    def test_r9_permanent_error_exits_2_on_stdout(self):
        self.watch()
        self.fake.faults.append(["conversations.history", None, "err:not_in_channel"])
        code, out, _ = self.bridge("watch", "--once")
        self.assertEqual(code, 2)
        self.assertEqual(len(self.errors(out)), 1, out)
        self.assertIn("not_in_channel", out)

    def test_r9_transient_error_reported_once(self):
        self.watch()
        self.fake.faults += [["conversations.history", None, "err:ratelimited"]] * 3
        code, out, _ = self.run_loop(3)
        self.assertEqual(code, 130)
        self.assertEqual(len(self.errors(out)), 1, out)

    # R10: blank optional keys use defaults; non-numeric values are a clean error
    def test_r10_blank_optional_keys_use_defaults(self):
        env = self.write_env(self.dir / "p2", "SLACK_POLL_SECONDS=\nSLACK_THREAD_TTL_SECONDS=\n"
                                              "SLACK_MAX_BACKLOG_SECONDS=\nSLACK_THREAD_SCAN_SECONDS=\n")
        self.assertEqual(self.watch(env=env), [])

    def test_r10_non_numeric_key_is_a_clean_error(self):
        code, _, err = self.bridge("check", env=self.write_env(self.dir / "p2", "SLACK_POLL_SECONDS=soon\n"))
        self.assertEqual(code, 1)
        self.assertIn("SLACK_POLL_SECONDS", err)

    # R11: lines come out in ts order, not grouped by parent
    def test_r11_lines_in_ts_order(self):
        self.watch()
        p = self.fake.say("parent")
        self.fake.say("STOP")
        self.fake.reply(p, "GO")
        self.assertEqual(self.watch(), ["[Slack user=UOP] parent", "[Slack user=UOP] STOP",
                                        f"[Slack thread={p} user=UOP] GO"])

    # R12 / Q-11: a broadcast reply keeps its thread
    def test_r12_broadcast_rendered_with_thread(self):
        self.watch()
        p = self.fake.say("parent")
        self.fake.say("also sent to channel", subtype="thread_broadcast", thread_ts=p)
        self.assertEqual(self.watch(), ["[Slack user=UOP] parent",
                                        f"[Slack thread={p} user=UOP] also sent to channel"])

    # R14: /me messages count; a first run starts a few seconds back
    def test_r14_me_message_and_first_run_floor(self):
        self.fake.messages.append({"ts": f"{time.time() - 2:.6f}", "user": "UOP", "text": "just before arming"})
        self.assertEqual(self.watch(), ["[Slack user=UOP] just before arming"])
        self.fake.say("waves", subtype="me_message")
        self.assertEqual(self.watch(), ["[Slack user=UOP] waves"])

    # R15: the misnamed-file hint ignores templates; `export KEY=` lines parse
    def test_r15_hint_ignores_example_files(self):
        proj = self.dir / "tpl"
        proj.mkdir()
        (proj / ".env.example").write_text("")
        (proj / ".env.sample").write_text("")
        code, _, err = self.bridge("check", env=proj / ".env")
        self.assertEqual(code, 1)
        self.assertNotIn("rename", err)

    def test_r15_export_prefix(self):
        env = self.write_env(self.dir / "p2")
        env.write_text("".join(f"export {line}\n" for line in env.read_text().splitlines()))
        code, out, err = self.bridge("check", env=env)
        self.assertEqual(code, 0, err)
        self.assertIn("auth ok", out)


if __name__ == "__main__":
    unittest.main()
