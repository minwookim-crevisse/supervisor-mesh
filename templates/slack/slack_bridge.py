#!/usr/bin/env python3
"""supervisor-mesh Slack bridge — away-mode channel between the operator and a Claude session.

  slack_bridge.py [--env .env] check                      verify token kind, bot identity and channel membership
  slack_bridge.py [--env .env] send FILE [--thread TS]    post the CONTENT of FILE (no shell escaping)
  slack_bridge.py [--env .env] watch [--once]             print each new human message as one line

Config is read at RUNTIME from the project's local .env (gitignored). Nothing secret lives in this file,
and the token travels only in an HTTP header — never in argv, so it never shows up in `ps`.

  SLACK_BOT_TOKEN=<Bot User OAuth Token, xoxb-...>     required
  SLACK_CHANNEL_ID=<channel id>                        required
  SLACK_ALLOWED_USERS=U123,U456                        required for watch: only these user ids are emitted
  SLACK_POLL_SECONDS=20                                watch poll interval
  SLACK_MAX_BACKLOG_SECONDS=3600                       on restart, replay at most this much missed history
  SLACK_THREAD_TTL_SECONDS=86400                       follow replies in threads started within this window
  SLACK_THREAD_SCAN_SECONDS=60                         re-scan that window for new replies at most this often
  SLACK_STATE_DIR=<dir>                                optional: default ~/.local/state/supervisor-mesh-slack
  SLACK_API_BASE=https://slack.com/api                 optional: tests use http://127.0.0.1:<port>
Blank optional keys mean the default; `export KEY=value` lines are accepted.

watch output (stdout, one line per event, ascending ts, Monitor-friendly):
  [Slack user=U123] text
  [Slack thread=1712345678.000100 user=U123] reply text        (reply with: send FILE --thread 1712345678.000100)
  [slack_bridge warning] …   state was unreadable; replaying up to the backlog cap
  [slack_bridge error] …     transient (once per distinct error, keeps polling) or permanent (watch exits 2)
New top-level messages arrive within one poll; replies to older threads within SLACK_THREAD_SCAN_SECONDS.
Delivery is at-least-once: lines are printed before the position is saved.

send refuses .env* files and any text holding the token or a Slack token pattern.
Policy: aggregates/counts only in messages — never real customer data, individual amounts, secrets or tokens.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException
from pathlib import Path

HUMAN_SUBTYPES = {None, "file_share", "thread_broadcast", "me_message"}  # anything else (joins, bots) is skipped
MAX_TEXT = 1800
DEFAULTS = {"SLACK_POLL_SECONDS": 20, "SLACK_MAX_BACKLOG_SECONDS": 3600,
            "SLACK_THREAD_TTL_SECONDS": 86400, "SLACK_THREAD_SCAN_SECONDS": 60}
TOKEN_RE = re.compile(r"(xox[abeprs][.-]|xapp-)[A-Za-z0-9-]{8,}")
API_BASE = "https://slack.com/api"
LOCAL_BASE_RE = re.compile(r"http://(127\.0\.0\.1|localhost):\d{1,5}")
PERMANENT = {"invalid_auth", "token_revoked", "account_inactive", "not_authed",
             "not_in_channel", "channel_not_found", "missing_scope"}
RETRY_DELAYS = (1, 2)  # seconds between attempts: 429 always, 5xx / network faults on GET only
TIMEOUT = 30
SKEW = 5.0  # seconds of clock skew tolerated between this host and Slack


class SlackError(RuntimeError):
    def __init__(self, msg: str, code: str | None = None):
        super().__init__(msg)
        self.code = code


def num(env: dict[str, str], key: str) -> float:
    return float(env.get(key) or DEFAULTS[key])


def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        hint = ", ".join(p.name for p in path.parent.glob(".env*")
                         if p.is_file() and not p.name.endswith((".example", ".sample")))
        raise SlackError(f"{path} not found" + (f" (found: {hint} — rename to {path.name})" if hint else ""))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as e:
        raise SlackError(f"cannot read {path}: {type(e).__name__}") from None
    env = {}
    for raw in lines:
        line = raw.strip().replace("\r", "")
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("\"'")
    for key in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID"):
        if not env.get(key):
            raise SlackError(f"{key} missing in {path}")
    if not env["SLACK_BOT_TOKEN"].isascii():
        raise SlackError(f"SLACK_BOT_TOKEN in {path} contains non-ASCII characters")
    for key in DEFAULTS:
        try:
            num(env, key)
        except ValueError:
            raise SlackError(f"{key} in {path} must be a number") from None
    return env


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a 3xx surfaces as HTTPError: the Authorization header never follows a redirect


class Slack:
    def __init__(self, env: dict[str, str]):
        self.token = env["SLACK_BOT_TOKEN"]
        self.base = (env.get("SLACK_API_BASE") or API_BASE).rstrip("/")
        if self.base != API_BASE and not LOCAL_BASE_RE.fullmatch(self.base):
            raise SlackError(f"SLACK_API_BASE must be {API_BASE} (or http://127.0.0.1:<port> for tests)")
        self.opener = urllib.request.build_opener(_NoRedirect)

    def call(self, method: str, http: str = "GET", **params) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"}
        url = f"{self.base}/{method}"
        data = None
        if http == "GET":
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        else:
            headers["Content-Type"] = "application/json; charset=utf-8"
            data = json.dumps(params).encode()
        for attempt in range(len(RETRY_DELAYS) + 1):
            retry = attempt < len(RETRY_DELAYS)
            try:
                with self.opener.open(urllib.request.Request(url, data=data, headers=headers), timeout=TIMEOUT) as r:
                    body = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if retry and (e.code == 429 or e.code >= 500 and http == "GET"):  # a POST may have been posted
                    after = (e.headers or {}).get("Retry-After", "") if e.code == 429 else ""
                    time.sleep(min(int(after), 60) if after.isdigit() else RETRY_DELAYS[attempt])
                    continue
                raise SlackError(f"{method}: HTTP {e.code}") from None
            except (OSError, HTTPException) as e:  # refused, reset, dropped, timed out
                if retry and http == "GET":  # never re-send a POST whose response was lost
                    time.sleep(RETRY_DELAYS[attempt])
                    continue
                raise SlackError(f"{method}: {getattr(e, 'reason', None) or type(e).__name__}") from None
            except ValueError as e:  # non-JSON body
                raise SlackError(f"{method}: unreadable response ({type(e).__name__})") from None
        if not isinstance(body, dict):
            raise SlackError(f"{method}: unreadable response")
        if not body.get("ok"):
            err = str(body.get("error", "unknown error"))
            raise SlackError(f"{method}: {err}", code=err)
        return body

    def paged(self, method: str, key: str, **params) -> list[dict]:
        out, cursor = [], None
        while True:
            body = self.call(method, cursor=cursor, **params)
            out += body.get(key) or []
            cursor = (body.get("response_metadata") or {}).get("next_cursor")
            if not body.get("has_more") or not cursor:
                return out


# ---------------------------------------------------------------- check / send

def cmd_check(slack: Slack, env: dict[str, str]) -> int:
    m = re.match(r"(xox[a-z]|xapp)[.-]", slack.token)
    if not m:
        print("WARN token type is unrecognised, expected a Bot User OAuth Token (xoxb-)")
    elif m.group(1) != "xoxb":
        print(f"WARN token type is {m.group(0)}, expected a Bot User OAuth Token (xoxb-)")
    me = slack.call("auth.test")
    print(f"auth ok: team={me.get('team')} bot_user={me.get('user')} bot_user_id={me.get('user_id')}")
    info = slack.call("conversations.info", channel=env["SLACK_CHANNEL_ID"]).get("channel") or {}
    member = info.get("is_member")
    print(f"channel ok: private={info.get('is_private')} bot_is_member={member}")
    if not member:
        print("FAIL invite the bot to the channel (/invite @<bot>) — it cannot read history otherwise")
        return 1
    slack.call("conversations.history", channel=env["SLACK_CHANNEL_ID"], limit=1)
    print("history ok: read access confirmed")
    return 0


def cmd_send(slack: Slack, env: dict[str, str], file: Path, thread: str | None) -> int:
    if any(name.startswith(".env") for name in (file.name, file.resolve().name)):
        raise SlackError(f"refusing to send {file.name}: .env files hold secrets")
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, ValueError) as e:
        raise SlackError(f"cannot read {file}: {type(e).__name__}") from None
    if slack.token in text or TOKEN_RE.search(text):
        raise SlackError(f"refusing to send {file.name}: it contains a Slack token")
    body = slack.call("chat.postMessage", http="POST", channel=env["SLACK_CHANNEL_ID"], text=text, thread_ts=thread)
    print(f"sent ts={body.get('ts')}" + (f" thread={thread}" if thread else ""))
    return 0


# ---------------------------------------------------------------- watch

def state_dir(env: dict[str, str], env_path: Path) -> Path:
    root = Path(env.get("SLACK_STATE_DIR") or Path.home() / ".local/state/supervisor-mesh-slack")
    key = hashlib.sha256(f"{env_path.resolve()}|{env['SLACK_CHANNEL_ID']}".encode()).hexdigest()[:16]
    d = root / key
    try:
        d.mkdir(parents=True, exist_ok=True)
        st = d.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise SlackError(f"state dir {d} is not a directory owned by you")
        d.chmod(0o700)
    except OSError as e:
        raise SlackError(f"state dir {d}: {e.strerror or type(e).__name__}") from None
    return d


def load_state(path: Path) -> dict | None:
    """{} when there is no state file yet; None when it exists but is unreadable or corrupt."""
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    try:
        float(state["floor"])
        float(state.get("scan", 0))
        for k, v in state.get("threads", {}).items():
            float(k), float(v)
    except (TypeError, KeyError, ValueError, AttributeError):
        return None
    return state


def save_state(path: Path, state: dict) -> None:
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-")  # unique name, mode 0600
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        raise SlackError(f"cannot save state: {e.strerror or type(e).__name__}") from None


def allowed_users(env: dict[str, str]) -> set[str]:
    return {u.strip() for u in env.get("SLACK_ALLOWED_USERS", "").split(",") if u.strip()}


def is_human(m: dict, bot_user: str | None, allowed: set[str]) -> bool:
    if m.get("bot_id") or m.get("subtype") not in HUMAN_SUBTYPES or m.get("user") == bot_user:
        return False
    return not allowed or m.get("user") in allowed


def clean(s: str) -> str:
    return "".join(" " if unicodedata.category(c) in ("Cc", "Zl", "Zp") else c for c in s)


def render(m: dict, thread: str | None = None) -> str:
    text = str(m.get("text") or "")
    files = [str(f.get("name") or f.get("title") or "file") for f in m.get("files") or []]
    if files:
        text += f" [files: {', '.join(files)}]"
    text = clean(text).replace("[Slack", "(Slack")  # one line per event; no forged prefix inside the payload
    where = f" thread={clean(str(thread))}" if thread else ""
    return f"[Slack{where} user={clean(str(m.get('user', '?')))}] {text[:MAX_TEXT]}"


def poll(slack: Slack, env: dict[str, str], state: dict, bot_user: str | None, now: float,
         start_floor: float) -> tuple[list[str], dict]:
    """One poll. Returns (lines in ts order, new state); `state` itself is never modified.

    state = {floor: newest top-level ts seen, threads: {parent_ts: newest reply ts processed}, scan: last full scan}.
    History is fetched from `floor`; the whole thread window is re-scanned at most every SLACK_THREAD_SCAN_SECONDS.
    """
    channel = env["SLACK_CHANNEL_ID"]
    ttl = num(env, "SLACK_THREAD_TTL_SECONDS")
    allowed = allowed_users(env)
    floor = float(state["floor"])
    last_scan = float(state.get("scan", 0))
    threads: dict[str, str] = dict(state.get("threads", {}))
    scan = now - last_scan >= num(env, "SLACK_THREAD_SCAN_SECONDS")
    oldest = min(floor, now - ttl) if scan else floor
    msgs = slack.paged("conversations.history", "messages", channel=channel, oldest=f"{oldest:.6f}", limit=200)
    out, newest = [], floor
    for m in msgs:
        ts = float(m.get("ts", 0))
        newest = max(newest, ts)
        if ts > floor and is_human(m, bot_user, allowed):
            out.append((ts, render(m, thread=m.get("thread_ts") if m.get("subtype") == "thread_broadcast" else None)))
        latest = m.get("latest_reply")
        if not latest or m.get("thread_ts", m.get("ts")) != m.get("ts"):
            continue  # not a thread parent
        seen = threads.get(m["ts"])
        # saved position, but never older than the backlog cap; an untracked parent had no replies at the last scan
        since = max(float(seen), start_floor) if seen else max(start_floor, min(floor, last_scan - SKEW))
        pos = max(float(latest), float(seen or 0))
        if float(latest) > since:
            replies = slack.paged("conversations.replies", "messages", channel=channel, ts=m["ts"],
                                  oldest=f"{since:.6f}", limit=200)
            for r in replies:
                rts = float(r.get("ts", 0))
                if r.get("ts") == m["ts"] or rts <= since:
                    continue
                pos = max(pos, rts)
                if r.get("subtype") != "thread_broadcast" and is_human(r, bot_user, allowed):
                    out.append((rts, render(r, thread=m["ts"])))  # broadcasts were already emitted from history
        threads[m["ts"]] = f"{pos:.6f}"
    new_state = {"floor": f"{newest:.6f}", "scan": now if scan else last_scan,
                 "threads": {k: v for k, v in threads.items() if float(k) >= now - ttl}}
    return [line for _, line in sorted(out, key=lambda x: x[0])], new_state


def cmd_watch(slack: Slack, env: dict[str, str], env_path: Path, once: bool) -> int:
    if not allowed_users(env):
        raise SlackError("SLACK_ALLOWED_USERS is empty — set it in .env to the Slack user id(s) allowed to steer "
                         "this session; watch refuses to start without it")
    d = state_dir(env, env_path)
    try:
        lock = os.open(d / "watch.lock", os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        raise SlackError(f"cannot open {d / 'watch.lock'}: {e.strerror}") from None
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SlackError("another watcher is already running for this project and channel") from None
        return _watch(slack, env, d / "state.json", once)
    finally:
        os.close(lock)


def _watch(slack: Slack, env: dict[str, str], path: Path, once: bool) -> int:
    now = time.time()
    backlog = num(env, "SLACK_MAX_BACKLOG_SECONDS")
    start_floor = now - backlog  # the cap: nothing older is replayed, top-level or thread
    state = load_state(path)
    if state is None:
        print(f"[slack_bridge warning] state file {path.name} unreadable or corrupt; "
              f"replaying up to {backlog:.0f}s of history", flush=True)
        state = {"floor": f"{start_floor:.6f}"}
    elif not state:
        state = {"floor": f"{now - SKEW:.6f}", "scan": now}  # first run: start (almost) now
    state = {**state, "floor": f"{max(float(state['floor']), start_floor):.6f}"}
    save_state(path, state)
    interval = num(env, "SLACK_POLL_SECONDS")
    bot_user, last_err = None, None
    while True:
        try:
            if bot_user is None:
                bot_user = slack.call("auth.test").get("user_id") or ""
            lines, new_state = poll(slack, env, state, bot_user, time.time(), start_floor)
            for line in lines:
                print(line, flush=True)
            state, last_err = new_state, None
            save_state(path, state)
        except Exception as e:  # last resort: a transient fault must never kill the watcher
            if isinstance(e, SlackError) and e.code in PERMANENT:
                print(f"[slack_bridge error] {e} — permanent, watch stopped; fix .env or the bot, then re-arm",
                      flush=True)
                return 2
            msg = str(e) if isinstance(e, SlackError) else f"{type(e).__name__}: {e}"
            if msg != last_err:
                print(f"[slack_bridge error] {msg} (transient; still polling)", flush=True)
                last_err = msg
        if once:
            return 0
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="supervisor-mesh Slack bridge")
    ap.add_argument("--env", default=".env", help="path to the local .env (default: ./.env)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    s = sub.add_parser("send")
    s.add_argument("file", type=Path)
    s.add_argument("--thread", help="parent message ts to reply in a thread")
    w = sub.add_parser("watch")
    w.add_argument("--once", action="store_true", help="poll once and exit (debug / tests)")
    a = ap.parse_args(argv)
    env_path = Path(a.env)
    try:
        env = load_env(env_path)
        slack = Slack(env)
        if a.cmd == "check":
            return cmd_check(slack, env)
        if a.cmd == "send":
            return cmd_send(slack, env, a.file, a.thread)
        return cmd_watch(slack, env, env_path, a.once)
    except SlackError as e:
        print(f"slack_bridge: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
