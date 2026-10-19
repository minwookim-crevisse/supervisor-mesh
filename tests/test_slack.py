import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from _helpers import ROOT

SLACK = ROOT / "templates/slack"
sys.path.insert(0, str(SLACK))
import slack_bridge  # noqa: E402

# a real token / channel id, not the bare prefix (one token pattern repo-wide: bridge, scrub, tests)
TOKEN_RE = re.compile(r"(xox[abeprs][.-]|xapp-)[A-Za-z0-9-]{8,}|\bC0[A-Z0-9]{8,}\b")
BOT = "UBOT"


class FakeSlack:
    """In-memory Slack: newest-first history and replies with cursor pagination (max 20 per page), threads,
    broadcasts, channels, posts, and per-method faults injected via `inject`."""

    page_cap = 20

    def __init__(self):
        self.messages: list[dict] = []
        self.replies: dict[str, list[dict]] = {}
        self.posts: list[dict] = []
        self.auth: list[str] = []
        self.is_member = True
        self.clock = time.time()
        # method -> faults consumed in order: "err:<code>" (ok:false) | "429:<Retry-After>" | "<5xx>" | "drop"
        self.inject: dict[str, list[str]] = {}

    def tick(self) -> str:
        self.clock = max(self.clock, time.time()) + 0.001
        return f"{self.clock:.6f}"

    def say(self, text, user="UOP", channel="CTEST", **extra) -> str:
        ts = self.tick()
        self.messages.append({"ts": ts, "user": user, "text": text, "channel": channel, **extra})
        return ts

    def reply(self, parent_ts, text, user="UOP", broadcast=False) -> str:
        ts = self.tick()
        r = {"ts": ts, "user": user, "text": text, "thread_ts": parent_ts}
        parent = next(m for m in self.messages if m["ts"] == parent_ts)
        if broadcast:  # "also send to channel": same ts in the thread and in history
            r["subtype"] = "thread_broadcast"
            self.messages.append({**r, "channel": parent.get("channel", "CTEST")})
        self.replies.setdefault(parent_ts, []).append(r)
        parent.update(thread_ts=parent_ts, latest_reply=ts, reply_count=len(self.replies[parent_ts]))
        return ts

    def handle(self, method, params, body):
        if method == "auth.test":
            return {"ok": True, "user": "bridge", "user_id": BOT, "team": "test"}
        if method == "conversations.info":
            return {"ok": True, "channel": {"is_member": self.is_member, "is_private": False}}
        if method == "chat.postMessage":
            self.posts.append(body)
            return {"ok": True, "ts": self.say(body["text"], user=BOT, bot_id="B1", channel=body["channel"])}
        if method == "conversations.history":
            rows = sorted((m for m in self.messages if float(m["ts"]) > float(params.get("oldest", 0))
                           and m.get("channel", "CTEST") == params.get("channel")), key=lambda m: -float(m["ts"]))
        elif method == "conversations.replies":
            parent = [m for m in self.messages if m["ts"] == params["ts"]]
            rows = parent + [r for r in self.replies.get(params["ts"], []) if float(r["ts"]) > float(params["oldest"])]
        else:
            return {"ok": False, "error": "unknown_method"}
        start = int(params.get("cursor") or 0)
        size = min(int(params.get("limit", 100)), self.page_cap)
        page = rows[start:start + size]
        more = start + size < len(rows)
        return {"ok": True, "messages": page, "has_more": more,
                "response_metadata": {"next_cursor": str(start + size) if more else ""}}


def make_handler(fake: FakeSlack):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _serve(self, body=None):
            fake.auth.append(self.headers.get("Authorization"))
            url = urllib.parse.urlparse(self.path)
            method = url.path.strip("/")
            params = dict(urllib.parse.parse_qsl(url.query))
            fault = fake.inject[method].pop(0) if fake.inject.get(method) else None
            if fault == "drop":  # close without any response
                self.close_connection = True
                return
            status, extra = 200, {}
            if fault is None:
                reply = fake.handle(method, params, body)
            elif fault.startswith("err:"):
                reply = {"ok": False, "error": fault[4:]}
            elif fault.startswith("429:"):
                status, extra, reply = 429, {"Retry-After": fault[4:]}, {"ok": False, "error": "ratelimited"}
            else:
                status, reply = int(fault), {"ok": False, "error": "server_error"}
            data = json.dumps(reply).encode()
            self.send_response(status)
            for k, v in extra.items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._serve()

        def do_POST(self):
            self._serve(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

    return Handler


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSlack()
        self.server = HTTPServer(("127.0.0.1", 0), make_handler(self.fake))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env = self.write_env(self.dir / "proj")
        p = mock.patch.object(slack_bridge, "RETRY_DELAYS", (0, 0))  # retries stay real, waits do not
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def write_env(self, proj: Path, extra: str = "", token: str = "xoxb-test") -> Path:
        proj.mkdir(parents=True, exist_ok=True)
        env = proj / ".env"
        env.write_text(f"SLACK_BOT_TOKEN={token}\nSLACK_CHANNEL_ID=CTEST\n"
                       f"SLACK_API_BASE=http://127.0.0.1:{self.server.server_port}\n"
                       f"SLACK_STATE_DIR={self.dir / 'state'}\n"
                       f"SLACK_ALLOWED_USERS=UOP\nSLACK_THREAD_SCAN_SECONDS=0\n{extra}")
        return env

    def bridge(self, *args, env=None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = slack_bridge.main(["--env", str(env or self.env), *args])
        return code, out.getvalue(), err.getvalue()

    def watch(self, env=None) -> list[str]:
        code, out, err = self.bridge("watch", "--once", env=env)
        self.assertEqual(code, 0, err)
        return out.splitlines()

    def watch_loop(self, polls: int) -> tuple[int, list[str]]:
        """Run the real watch loop (not --once) for `polls` polls; returns (exit code, stdout lines)."""
        env = self.write_env(self.dir / "proj", "SLACK_POLL_SECONDS=7\n")

        class Clock:  # stands in for the bridge's `time`: the poll-interval sleep ends the loop after N polls
            n = 0
            time = staticmethod(time.time)

            def sleep(self, seconds):
                if seconds == 7:
                    self.n += 1
                    if self.n >= polls:
                        raise KeyboardInterrupt

        with mock.patch.object(slack_bridge, "time", Clock()):
            code, out, _ = self.bridge("watch", env=env)
        return code, out.splitlines()

    def state_file(self) -> Path:
        return next((self.dir / "state").glob("*/state.json"))


class TemplateHygieneTests(unittest.TestCase):
    def test_no_token_or_channel_literals(self):
        for p in SLACK.iterdir():
            if p.is_file():
                self.assertIsNone(TOKEN_RE.search(p.read_text()), p.name)

    def test_env_example_is_empty(self):
        for line in (SLACK / "env.example").read_text().splitlines():
            if line.startswith(("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID")):
                self.assertTrue(line.endswith("="), line)


class SendAndCheckTests(BridgeTestCase):
    def test_send_posts_file_content_with_bearer_header(self):
        msg = self.dir / "m.txt"
        msg.write_text("done: 3 of 3 checks green")
        code, out, _ = self.bridge("send", str(msg))
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^sent ts=\d+\.\d{6}")
        expected = {"channel": "CTEST", "text": "done: 3 of 3 checks green", "thread_ts": None}
        self.assertEqual(self.fake.posts, [expected])
        self.assertEqual(set(self.fake.auth), {"Bearer xoxb-test"})

    def test_send_into_thread(self):
        parent = self.fake.say("question?")
        msg = self.dir / "m.txt"
        msg.write_text("answer")
        code, out, _ = self.bridge("send", str(msg), "--thread", parent)
        self.assertEqual(code, 0)
        self.assertIn(f"thread={parent}", out)
        self.assertEqual(self.fake.posts[0]["thread_ts"], parent)

    def test_missing_keys_fail_cleanly(self):
        env = self.dir / "bad" / ".env"
        env.parent.mkdir()
        env.write_text("OTHER=1\n")
        code, _, err = self.bridge("check", env=env)
        self.assertEqual(code, 1)
        self.assertIn("SLACK_BOT_TOKEN missing", err)

    def test_misnamed_env_file_is_pointed_out(self):
        proj = self.dir / "misnamed"
        proj.mkdir()
        (proj / ".env.txt").write_text("SLACK_BOT_TOKEN=x\n")
        code, _, err = self.bridge("check", env=proj / ".env")
        self.assertEqual(code, 1)
        self.assertIn(".env.txt", err)

    def test_check_passes_and_flags_problems(self):
        code, out, _ = self.bridge("check")
        self.assertEqual(code, 0)
        self.assertIn("bot_is_member=True", out)
        self.assertNotIn("WARN", out)
        self.fake.is_member = False
        code, out, _ = self.bridge("check", env=self.write_env(self.dir / "p2", token="xoxp-user"))
        self.assertEqual(code, 1)
        self.assertIn("WARN token type is xoxp-", out)
        self.assertIn("invite the bot", out)

    def test_check_probes_history_read_access(self):
        code, out, _ = self.bridge("check")
        self.assertEqual(code, 0)
        self.assertIn("history ok", out)
        self.fake.inject["conversations.history"] = ["err:missing_scope"]
        code, out, err = self.bridge("check")
        self.assertEqual(code, 1)
        self.assertNotIn("history ok", out)
        self.assertIn("conversations.history: missing_scope", err)


class TokenConfinementTests(BridgeTestCase):
    """Row J / Q-06: the token travels only in the Authorization header — never in a URL, a body, argv or the env."""

    SPAWNERS = [(subprocess, "Popen")] + [(os, n) for n in (
        "system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp", "putenv",
        "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
        "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe") if hasattr(os, n)]

    def test_token_only_in_the_authorization_header(self):
        token = "xo" + "xb-" + "q6" * 8  # built at runtime: no token literal in the repo
        self.env = self.write_env(self.dir / "tok", token=token)
        sent, spawned = [], []
        real_open = urllib.request.OpenerDirector.open

        def record(opener, req, *args, **kwargs):
            sent.append(req)
            return real_open(opener, req, *args, **kwargs)

        def forbidden(name):
            def run(*args, **kwargs):
                spawned.append(name)
                raise OSError(f"{name} is not allowed in the bridge")
            return run

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(urllib.request.OpenerDirector, "open", record))
            for mod, name in self.SPAWNERS:
                stack.enter_context(mock.patch.object(mod, name, forbidden(name)))
            code, out, err = self.bridge("check")
            self.assertEqual(code, 0, err)
            self.watch()
            parent = self.fake.say("question?")
            msg = self.dir / "m.txt"
            msg.write_text("answer")
            code, out2, err2 = self.bridge("send", str(msg), "--thread", parent)
            self.assertEqual(code, 0, err2)
            self.fake.reply(parent, "follow-up")
            self.assertEqual(self.watch(), ["[Slack user=UOP] question?",
                                            f"[Slack thread={parent} user=UOP] follow-up"])
        self.assertEqual(spawned, [], "the bridge must not spawn processes or touch the environment")
        methods = {urllib.parse.urlparse(r.full_url).path.strip("/") for r in sent}
        self.assertTrue({"auth.test", "conversations.info", "conversations.history", "chat.postMessage",
                         "conversations.replies"} <= methods, methods)
        for req in sent:
            headers = dict(req.header_items())
            self.assertEqual(headers.get("Authorization"), f"Bearer {token}")
            self.assertNotIn(token, req.full_url)
            self.assertNotIn(token.encode(), req.data or b"")
            self.assertEqual([k for k, v in headers.items() if token in v], ["Authorization"])
        self.assertEqual(set(self.fake.auth), {f"Bearer {token}"})
        self.assertFalse([k for k, v in os.environ.items() if token in v])
        self.assertNotIn(token, out + err + out2 + err2)


class WatchTests(BridgeTestCase):
    def test_emits_humans_with_sender_and_skips_noise(self):
        self.watch()  # first run: start from now
        self.fake.say("operator says hi\nsecond line")
        self.fake.say("from a bot", user="UX", bot_id="B9")
        self.fake.say("joined", subtype="channel_join")
        self.fake.say("see attached", subtype="file_share", files=[{"name": "report.txt"}])
        self.fake.say("echo of our own post", user=BOT)
        self.assertEqual(self.watch(), [
            "[Slack user=UOP] operator says hi second line",
            "[Slack user=UOP] see attached [files: report.txt]",
        ])

    def test_follows_pagination(self):
        self.watch()
        for i in range(50):
            self.fake.say(f"m{i}")
        lines = self.watch()
        self.assertEqual(len(lines), 50)
        self.assertEqual(lines[0], "[Slack user=UOP] m0")
        self.assertEqual(lines[-1], "[Slack user=UOP] m49")

    def test_restart_resumes_without_loss_or_duplicates(self):
        self.watch()
        self.fake.say("sent while the watcher was down")
        self.assertEqual(self.watch(), ["[Slack user=UOP] sent while the watcher was down"])
        self.assertEqual(self.watch(), [])

    def test_backlog_cap_drops_stale_orders(self):
        self.watch()
        self.fake.say("stale order")
        state = next((self.dir / "state").glob("*/state.json"))
        data = json.loads(state.read_text())
        data["floor"] = f"{time.time() - 7200:.6f}"  # watcher was down for 2h
        state.write_text(json.dumps(data))
        for m in self.fake.messages:
            m["ts"] = f"{time.time() - 5400:.6f}"  # message is 90 min old
        self.assertEqual(self.watch(), [], "messages older than SLACK_MAX_BACKLOG_SECONDS must not replay")

    def test_state_is_separate_per_project(self):
        other = self.write_env(self.dir / "other")
        self.watch()
        self.watch(env=other)
        self.assertEqual(len(list((self.dir / "state").glob("*/state.json"))), 2)

    def test_state_is_separate_per_channel(self):
        self.watch()
        self.fake.say("on test")
        self.write_env(self.dir / "proj", "SLACK_CHANNEL_ID=CTWO\n")  # same .env path, other channel
        self.fake.say("on two", channel="CTWO")
        self.assertEqual(self.watch(), ["[Slack user=UOP] on two"])
        self.write_env(self.dir / "proj")
        self.assertEqual(self.watch(), ["[Slack user=UOP] on test"], "a second channel must not move this floor")
        self.assertEqual(len(list((self.dir / "state").glob("*/state.json"))), 2)

    def test_thread_positions_older_than_ttl_are_pruned(self):
        self.watch()
        data = json.loads(self.state_file().read_text())
        expired = f"{time.time() - 2 * 86400:.6f}"
        data["threads"] = {expired: expired}
        self.state_file().write_text(json.dumps(data))
        p = self.fake.say("parent")
        self.fake.reply(p, "reply")
        self.assertEqual(len(self.watch()), 2)
        self.assertEqual(set(json.loads(self.state_file().read_text())["threads"]), {p})

    def test_thread_broadcast_emitted_once_with_its_thread(self):
        self.watch()
        p = self.fake.say("parent")
        self.fake.reply(p, "also sent to channel", broadcast=True)
        self.fake.reply(p, "thread only")
        self.assertEqual(self.watch(), ["[Slack user=UOP] parent",
                                        f"[Slack thread={p} user=UOP] also sent to channel",
                                        f"[Slack thread={p} user=UOP] thread only"])
        self.assertEqual(self.watch(), [])

    def test_follows_replies_pagination(self):
        self.watch()
        p = self.fake.say("parent")
        for i in range(30):
            self.fake.reply(p, f"r{i}")
        lines = self.watch()
        self.assertEqual(len(lines), 31)
        self.assertEqual(lines[1], f"[Slack thread={p} user=UOP] r0")
        self.assertEqual(lines[-1], f"[Slack thread={p} user=UOP] r29")

    def test_first_arm_skips_existing_history_and_thread_replies(self):
        now = time.time()
        p, r = (f"{now - s:.6f}" for s in (7200, 1800))
        self.fake.messages += [{"ts": f"{now - 600:.6f}", "user": "UOP", "text": "before arming"},
                               {"ts": p, "user": "UOP", "text": "old parent", "thread_ts": p,
                                "latest_reply": r, "reply_count": 1}]
        self.fake.replies[p] = [{"ts": r, "user": "UOP", "text": "old reply", "thread_ts": p}]
        self.assertEqual(self.watch(), [], "the first arm must not replay the backlog")
        self.assertEqual(self.watch(), [])
        self.fake.reply(p, "new reply")
        self.assertEqual(self.watch(), [f"[Slack thread={p} user=UOP] new reply"])

    def test_long_text_is_truncated(self):
        self.watch()
        self.fake.say("x" * 5000)
        self.assertEqual(self.watch(), ["[Slack user=UOP] " + "x" * slack_bridge.MAX_TEXT])

    def test_thread_replies_including_replies_to_our_posts(self):
        self.watch()
        msg = self.dir / "m.txt"
        msg.write_text("Blocked: which DB?")
        _, out, _ = self.bridge("send", str(msg))
        ours = out.split("ts=")[1].strip()
        self.fake.reply(ours, "use the staging one")
        self.assertEqual(self.watch(), [f"[Slack thread={ours} user=UOP] use the staging one"])
        self.assertEqual(self.watch(), [])
        self.fake.reply(ours, "and run the audit")
        self.assertEqual(self.watch(), [f"[Slack thread={ours} user=UOP] and run the audit"])

    def test_allowed_users_filter(self):
        env = self.write_env(self.dir / "strict", extra="SLACK_ALLOWED_USERS=UOP, UOK\n")
        self.watch(env=env)
        self.fake.say("from operator")
        self.fake.say("from someone else", user="UZZ")
        self.fake.say("from a second operator", user="UOK")
        self.assertEqual(self.watch(env=env), ["[Slack user=UOP] from operator",
                                               "[Slack user=UOK] from a second operator"])


class FaultTests(BridgeTestCase):
    """Q-07: Slack faults — ok:false, 429, 5xx, dropped connections — lose nothing and never kill watch."""

    def errors(self, lines):
        return [x for x in lines if x.startswith("[slack_bridge error]")]

    def test_mid_poll_ok_false_loses_nothing(self):
        self.watch()
        p = self.fake.say("parent")
        self.fake.reply(p, "reply")
        self.fake.say("top")
        self.fake.inject["conversations.replies"] = ["err:ratelimited"]  # history ok, then the thread fails
        code, lines = self.watch_loop(2)
        self.assertEqual(code, 130)
        self.assertEqual(self.errors(lines), ["[slack_bridge error] conversations.replies: ratelimited "
                                              "(transient; still polling)"])
        self.assertEqual([x for x in lines if x.startswith("[Slack")], [
            "[Slack user=UOP] parent", f"[Slack thread={p} user=UOP] reply", "[Slack user=UOP] top"])

    def test_429_waits_retry_after_then_retries(self):
        self.watch()
        self.fake.say("after a rate limit")
        self.fake.inject["conversations.history"] = ["429:3"]
        with mock.patch.object(slack_bridge.time, "sleep") as sleep:
            self.assertEqual(self.watch(), ["[Slack user=UOP] after a rate limit"])
        self.assertEqual(sleep.call_args_list, [mock.call(3)])
        self.assertEqual(self.fake.inject["conversations.history"], [])

    def test_5xx_and_dropped_connection_are_retried_then_reported(self):
        for kind in ("503", "drop"):
            self.watch()
            self.fake.say(f"after {kind} x2")
            self.fake.inject["conversations.history"] = [kind] * 2  # within the retry budget
            self.assertEqual(self.watch(), [f"[Slack user=UOP] after {kind} x2"], kind)
            self.fake.say(f"after {kind} x3")
            self.fake.inject["conversations.history"] = [kind] * 3  # budget exhausted: one error, nothing lost
            code, out, err = self.bridge("watch", "--once")
            self.assertEqual(code, 0, err)
            self.assertEqual(len(self.errors(out.splitlines())), 1, out)
            self.assertNotIn("[Slack ", out)
            self.assertEqual(self.watch(), [f"[Slack user=UOP] after {kind} x3"], kind)


if __name__ == "__main__":
    unittest.main()
