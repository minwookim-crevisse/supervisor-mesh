#!/usr/bin/env python3
"""smv: drive the supervisor-mesh plugin in a projmux Claude pane the way a user does, and keep the evidence.

One run = one directory under the scratch root the caller passes:
  <scratch-root>/<run-id>/proj/       scratch project (git repo + projmux project root); cleanup deletes it
  <scratch-root>/<run-id>/evidence/   commands, redacted pane captures, hashes, transcript extracts; kept

Every subcommand except launch takes --run <scratch-root>/<run-id> (or reads SMV_RUN).

exit: 0 ok · 1 check failed · 2 usage/precondition (also a refused --allow) · 3 pane not ready
      4 cleanup stop: an agent this run did not record, or a process left inside the scratch project
      10 trust prompt · 11 permission prompt · 12 plugin scope menu, or scope-pick could not verify the highlight
      13 unscripted dialog or question · 21 a different 'supervisor-mesh' marketplace is registered · 124 timeout
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import hmac
import json
import os
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

try:
    import fcntl
except ImportError:  # not POSIX: no run.json lock (Known gaps)
    fcntl = None

sys.dont_write_bytecode = True  # the FakeSlack import must never write __pycache__ into the checkout (lead tree)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from smv_screen import (SCOPE_OPTION, box_holds, box_is_empty, drop_map, highlighted, input_box,  # noqa: E402
                        keep_lines, lines_of, menu_options, permission_mode, screen_state)

PLUGIN_ID = "supervisor-mesh@supervisor-mesh"
MARKETPLACE = "supervisor-mesh"
INSTALL_LINE = f"/plugin install {PLUGIN_ID}"
CREATOR = "projmux.io/creator-agent"
# user config files: compared by whole-file sha256 + keyed per-key digests; never copied, values never stored
USER_SETTINGS = "settings.json"
USER_REGISTRY = ("plugins/known_marketplaces.json", "plugins/installed_plugins.json")
RUN_OWNED = {  # registry key paths this run's project-scope declaration and install write: (top key, second key)
    "plugins/known_marketplaces.json": (MARKETPLACE, None),
    "plugins/installed_plugins.json": ("plugins", PLUGIN_ID),
}
SLACK_DEFAULT_STATE = Path.home() / ".local/state/supervisor-mesh-slack"  # the bridge's default SLACK_STATE_DIR
ALLOW_KINDS = ("ADDED", "MISSING", "CHANGED")
SHIPPED = (".claude-plugin", "skills", "agents", "templates", "docs/core")
AGENT_RE = re.compile(r"\bagent-[a-z0-9]{20,}\b")
PROJ_RE = re.compile(r"\bproj-[a-z0-9]{20,}\b")
SUMMARY_RE = re.compile(r"# summary: created=(\d+) merged=(\d+) skipped=(\d+)")
RECEIPT_RE = re.compile(r"\b(message-[0-9a-f]{20,})\s+delivered\b")
INVARIANT = ("<!-- INVARIANT:BEGIN", "<!-- INVARIANT:END -->")
FAKE_TOKEN = "xoxb-" + "fake"  # four characters after the prefix: never a real token shape
FAKE_USER = "UOP"
FAKE_CHANNEL = "CTEST"
DONE = {"response_complete", "idle"}
STOP_CODES = {"trust": 10, "permission": 11, "scope-menu": 12, "dialog": 13}
RESUME = {  # the one resume command per stop, pasteable; SKILL.md quotes these verbatim
    "trust": '"$SMV" wait --ready --settle 3',
    "permission": '"$SMV" wait',
    "dialog": '"$SMV" wait',
    "scope-menu": '"$SMV" scope-pick',
    "scope-pick": '"$SMV" wait --ready && "$SMV" plugin-check',
}
TS_RE = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?")


# ---------------------------------------------------------------- basics

def die(msg: str, code: int = 2):
    sys.stdout.flush()  # keep stdout and stderr in event order when both go to one pipe
    print(f"smv: {msg}", file=sys.stderr)
    sys.exit(code)


BUDGET = {"deadline": None}  # set by launch: every subprocess and wait inside launch ends by this time


def budget(default: float) -> float:
    """Seconds for one call or wait: the default, cut to what the launch budget has left (0 when spent)."""
    deadline = BUDGET["deadline"]
    return default if deadline is None else max(0.0, min(default, deadline - time.time()))


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(s: str | None) -> dt.datetime | None:
    m = TS_RE.search(s or "")
    if not m:
        return None
    base = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    tz = m.group(3) or "Z"
    if tz == "Z":
        zone = dt.timezone.utc
    else:
        delta = dt.timedelta(hours=int(tz[1:3]), minutes=int(tz[4:6]))
        zone = dt.timezone(delta if tz[0] == "+" else -delta)
    return base.replace(microsecond=int((m.group(2) or "0")[:6].ljust(6, "0")), tzinfo=zone)


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")[:60] or "step"


def within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def same_path(a: str | None, b: Path) -> bool:
    return bool(a) and os.path.realpath(a) == os.path.realpath(b)


def sh(argv: list[str], cwd=None, timeout: float = 120, env=None) -> tuple[int, str, str]:
    timeout = budget(timeout)
    if timeout <= 0:
        return 124, "", "launch budget spent before this call (launch --timeout)"
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired as e:
        out, err = (x.decode(errors="replace") if isinstance(x, bytes) else (x or "") for x in (e.stdout, e.stderr))
        return 124, out, err + f"\ntimeout after {timeout:.0f}s"


def jload(text: str, default=None):
    try:
        return json.loads(text)
    except ValueError:
        return default


def repo_root() -> Path:
    env = os.environ.get("SMV_REPO")
    root = Path(env).resolve() if env else Path(__file__).resolve().parents[4]
    if not (root / ".claude-plugin/plugin.json").is_file():
        die(f"not a supervisor-mesh checkout: {root} (run from the checkout, or set SMV_REPO)")
    return root


def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


# ---------------------------------------------------------------- run state

class Run:
    def __init__(self, root: Path):
        self.root = root
        self.proj = root / "proj"
        self.ev = root / "evidence"
        self.file = self.ev / "run.json"
        self.data = json.loads(self.file.read_text())
        self.repo = Path(self.data["repo"])
        self._socket = None
        self.last_step: Path | None = None

    @classmethod
    def create(cls, root: Path, repo: Path, run_id: str) -> "Run":
        ev = root / "evidence"
        for sub in ("steps", "panes", "hashes", "snapshots", "transcripts", "slack"):
            (ev / sub).mkdir(parents=True, exist_ok=True)
        data = {"run_id": run_id, "repo": str(repo), "started": now_iso(), "step": 0, "pm": None,
                "created": {"project": None, "agents": []}, "sends": {}, "last_send": None,
                "marketplace": None, "pids": {}, "allowances": []}
        (ev / "run.json").write_text(json.dumps(data, indent=1))
        return cls(root)

    @classmethod
    def load(cls, path: str | None) -> "Run":
        path = path or os.environ.get("SMV_RUN")
        if not path:
            die("no run: pass --run <scratch-root>/<run-id> or export SMV_RUN")
        root = Path(path).resolve()
        if not (root / "evidence/run.json").is_file():
            die(f"{root} is not an smv run (no evidence/run.json)")
        return cls(root)

    @contextlib.contextmanager
    def locked(self):
        """Exclusive lock on run.json (a lock file next to evidence/): one smv process changes it at a time."""
        with open(self.root / ".run.lock", "a") as f:
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def update(self, fn):
        """Load-modify-save run.json under the lock, re-reading it first, so concurrent smv processes (a wait
        and a fake-slack say, two panes) never lose each other's changes. Returns fn's result; self.data is the
        fresh copy afterwards."""
        with self.locked():
            self.data = json.loads(self.file.read_text())
            result = fn(self.data)
            fd, tmp = tempfile.mkstemp(dir=self.ev, prefix=".run-")
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.file)
        return result

    def next_id(self, label: str) -> str:
        n = self.update(lambda d: d.update(step=d["step"] + 1) or d["step"])
        return f"{n:03d}-{slug(label)}"

    def count_redactions(self, rel: str, dropped: Counter) -> None:
        if dropped:
            self.update(lambda d: d.setdefault("redactions", {}).__setitem__(rel, dict(dropped)))

    def cmd(self, label: str, argv: list[str], cwd=None, timeout: int = 120, echo: bool = True,
            env=None) -> tuple[int, str, str]:
        started = now_iso()
        code, out, err = sh(argv, cwd=cwd, timeout=timeout, env=env)
        sid = self.next_id(label)
        kept_out, d_out = redact(self, out.split("\n"))
        kept_err, d_err = redact(self, err.split("\n"))
        dropped = d_out + d_err
        rec = (f"$ {' '.join(argv)}\n# cwd: {cwd or os.getcwd()}\n# started: {started}  ended: {now_iso()}\n"
               f"# exit: {code}\n# output lines dropped by redaction: {sum(dropped.values())}\n"
               f"--- stdout\n" + "\n".join(kept_out) + "\n--- stderr\n" + "\n".join(kept_err) + "\n")
        self.last_step = self.ev / "steps" / f"{sid}.txt"
        self.last_step.write_text(rec)
        self.count_redactions(f"steps/{sid}.txt", dropped)
        if echo:
            print(f"[{sid}] exit={code}  $ {' '.join(argv)}")
            for line in (out + err).rstrip().splitlines()[-25:]:
                print(f"    {line}")
        return code, out, err

    def note(self, text: str) -> None:
        with (self.ev / "notes.md").open("a") as f:
            f.write(f"- {now_iso()} {text}\n")

    @property
    def project(self) -> str | None:
        return self.data["created"]["project"]

    # projmux views of this run's project only
    def project_json(self) -> dict:
        code, out, _ = sh(["projmux", "describe", "project", f"uid:{self.project}", "-o", "json"])
        return jload(out, {}) if code == 0 else {}

    def project_agents(self, strict: bool = False) -> list[dict]:
        if not self.project:
            return []
        code, out, err = sh(["projmux", "get", "agents", "-p", f"uid:{self.project}", "-o", "json"])
        data = jload(out) if code == 0 else None
        if strict and not isinstance(data, dict):  # a failed listing must never read as "no foreign agents"
            die(f"STOP (4): projmux get agents failed (exit {code}): {err.strip()[:160]}; cannot tell which agents "
                "this run owns", 4)
        return (data or {}).get("items", []) if isinstance(data, dict) else []

    def record_agents(self) -> list[str]:
        """Record an agent only when projmux's creator-agent annotation names an agent this run already recorded
        and its spec.workspace.cwd is the scratch project. Any other agent stays unrecorded: smv never drives or
        deletes it, and cleanup stops (exit 4)."""
        known = self.data["created"]["agents"]
        new: list[tuple[str, str | None]] = []
        items, grew = self.project_agents(), True
        while grew:  # a worker created by a role pane is recorded once that role pane is
            grew = False
            for i in items:
                uid = i["metadata"]["uid"]
                if uid not in known and creator_of(i) in known and same_path(cwd_of(i), self.proj):
                    known.append(uid)
                    new.append((uid, creator_of(i)))
                    grew = True
        if new:
            self.update(lambda d: d["created"]["agents"].extend(u for u, _ in new if u not in d["created"]["agents"]))
            for uid, creator in new:
                print(f"recorded agent {uid} (creator uid:{creator})", file=sys.stderr)
        return [uid for uid, _ in new]

    def foreign_agents(self) -> list[dict]:
        """Agents in this run's project that the run did not record (after applying the creator rule)."""
        self.record_agents()
        known = self.data["created"]["agents"]
        return [i for i in self.project_agents(strict=True) if i["metadata"]["uid"] not in known]

    def agent_json(self, uid: str) -> dict:
        code, out, _ = sh(["projmux", "describe", "agent", f"uid:{uid}", "-p", f"uid:{self.project}", "-o", "json"])
        return jload(out, {}) if code == 0 else {}

    def resolve_agent(self, ref: str | None) -> str:
        ref = ref or "pm"
        if ref == "pm":
            if not self.data["pm"]:
                die("this run has no PM agent yet")
            return self.data["pm"]
        if ref.startswith("role:"):
            self.record_agents()
            rows = live_role_agents(self, ref[5:])
            if len(rows) != 1:
                die(f"{ref}: want exactly one live agent in this run's project, found {len(rows)}")
            uid = rows[0]["metadata"]["uid"]
            if uid not in self.data["created"]["agents"]:
                die(f"{ref} is uid:{uid}, which this run did not record (creator {creator_of(rows[0])}); "
                    "smv never drives it")
            return uid
        uid = ref.removeprefix("uid:")
        if uid not in self.data["created"]["agents"]:
            die(f"{uid} was not created by this run; smv never drives it")
        return uid

    def socket_args(self) -> list[str]:
        if self._socket is None:
            path = ((self.project_json().get("status") or {}).get("session") or {}).get("socketPath")
            self._socket = ["-S", path] if path else ["-L", "projmux"]
        return self._socket

    def pane_runtime(self, pane_uid: str) -> str | None:
        code, out, _ = sh(["projmux", "describe", "pane", f"uid:{pane_uid}", "-p", f"uid:{self.project}", "-o",
                           "json"])
        return ((((jload(out, {}) or {}).get("status") or {}).get("activation") or {}).get("runtimeID")
                if code == 0 else None)

    def pane(self, agent: str, wait: float = 0, required: bool = True) -> str | None:
        """The live tmux pane of a recorded agent. An Offline agent or a dead pane ends the wait at once and is
        reported as such (pane_report), never as an unresolved pane."""
        deadline = time.time() + budget(wait)
        while True:
            st = self.agent_json(agent).get("status") or {}
            if st.get("paneRef"):
                rid = self.pane_runtime(st["paneRef"])
                if rid:
                    pane_rec = {"pane_uid": st["paneRef"], "tmux": rid}
                    self.update(lambda d: d.setdefault("panes", {}).__setitem__(agent, pane_rec))
                    if tmux_dead(self, rid)[0] != "1":
                        return rid
            known = (self.data.get("panes", {}).get(agent) or {}).get("tmux")
            dead = bool(known) and tmux_dead(self, known)[0] in ("1", "gone")
            if st.get("phase") in ("Offline", "Failed") or dead or time.time() >= deadline:
                if required:
                    die(format_report(pane_report(self, agent)), 1)
                return None
            time.sleep(2)

    def capture(self, pane: str) -> str:
        code, out, err = sh(["tmux", *self.socket_args(), "capture-pane", "-p", "-J", "-t", pane, "-S", "-3000"])
        if code != 0:
            die(f"capture-pane {pane} failed: {err.strip()}", 1)
        return out

    def save_pane(self, label: str, text: str, kind: str = "auto", pattern: re.Pattern | None = None) -> Path:
        """Keep a capture as evidence: only the lines the check needs (smv_screen.keep_lines: the input box and
        footer, the scope menu, the bottom dialog, the lines a pattern matched, or a dead pane's last lines),
        after the redaction pass, plus the sha256 of the whole screen. The full screen is hashed in memory and
        never written anywhere."""
        p = self.ev / "panes" / f"{self.next_id(label)}.txt"
        body, dropped = redact_screen(self, text, kind, pattern)
        p.write_text(body)
        self.count_redactions(f"panes/{p.name}", dropped)
        return p

    def keys(self, pane: str, *keys: str, literal: bool = False) -> None:
        code, _, err = sh(["tmux", *self.socket_args(), "send-keys", "-t", pane, *(["-l"] if literal else []), *keys])
        if code != 0:
            die(f"send-keys failed: {err.strip()}", 1)


def tmux_dead(run: Run, tmux_id: str) -> tuple[str, str]:
    """('0'|'1'|'gone', exit status) of a tmux pane; tmux keeps a dead pane (remain-on-exit) until it is killed."""
    code, out, _ = sh(["tmux", *run.socket_args(), "display-message", "-p", "-t", tmux_id,
                       "#{pane_dead} #{pane_dead_status}"], timeout=15)
    parts = out.split()
    return (parts[0], parts[1] if len(parts) > 1 else "-") if code == 0 and parts else ("gone", "-")


def pane_report(run: Run, agent: str) -> dict:
    """What projmux and tmux say about an agent's pane, alive or not: phase, reason, pane uid, tmux id,
    pane_dead and exit status. The pane uid falls back to status.lastTermination, the tmux id to run.json."""
    aj = run.agent_json(agent)
    st = aj.get("status") or {}
    term = st.get("lastTermination") or {}
    known = run.data.get("panes", {}).get(agent) or {}
    pane_uid = st.get("paneRef") or term.get("paneUID") or known.get("pane_uid")
    tmux_id = (run.pane_runtime(pane_uid) if pane_uid else None) or known.get("tmux")
    dead, status = tmux_dead(run, tmux_id) if tmux_id else ("unknown", "-")
    return {"agent": agent, "phase": st.get("phase") or ("not found" if not aj else "-"),
            "reason": st.get("reason") or "-", "pane_uid": pane_uid, "tmux": tmux_id, "pane_dead": dead,
            "exit_status": status}


def format_report(r: dict) -> str:
    head = ("DEAD PANE" if r["pane_dead"] == "1" else "PANE GONE" if r["pane_dead"] == "gone" else
            "OFFLINE" if r["phase"] in ("Offline", "Failed") else "PANE NOT RESOLVED")
    return (f"{head}: agent uid:{r['agent']} phase={r['phase']} reason={r['reason']!r}; pane uid:{r['pane_uid']} "
            f"tmux={r['tmux']} pane_dead={r['pane_dead']} exit_status={r['exit_status']}")


def capture_any(run: Run, agent: str, label: str, kind: str = "auto") -> tuple[Path | None, str, dict]:
    """Capture an agent's pane into evidence/panes/ by its tmux id, also when the pane is dead or the agent
    Offline (capture-pane reads a dead pane). Returns (path or None, text, pane report)."""
    rep = pane_report(run, agent)
    if not rep["tmux"] or rep["pane_dead"] == "gone":
        return None, "", rep
    code, out, _ = sh(["tmux", *run.socket_args(), "capture-pane", "-p", "-J", "-t", rep["tmux"], "-S", "-3000"],
                      timeout=15)
    if code != 0:
        return None, "", rep
    return run.save_pane(label, out, "tail" if rep["pane_dead"] == "1" else kind), out, rep


LOCK_RE = re.compile(r"registry lock", re.I)
HOLDER_RE = re.compile(r"holder: pid (\d+) \(([^)]*)\)")


def lock_hint(text: str) -> bool:
    """Print the registry-lock hint when projmux output or a dead pane mentions the lock."""
    if not LOCK_RE.search(text or ""):
        return False
    holder = HOLDER_RE.search(text)
    who = f" (holder: pid {holder.group(1)}, {holder.group(2)})" if holder else ""
    print(f"HINT: projmux registry lock contended: another projmux writer is busy{who}. Run smv cleanup, wait "
          "until that writer is done, then relaunch. One smv run at a time; never kill another projmux process.")
    return True


def creator_of(item: dict) -> str | None:
    value = (item["metadata"].get("annotations") or {}).get(CREATOR)
    return value.removeprefix("uid:") if value else None


def cwd_of(item: dict) -> str | None:
    return ((item.get("spec") or {}).get("workspace") or {}).get("cwd")


def live_role_agents(run: Run, role: str) -> list[dict]:
    return [i for i in run.project_agents()
            if (i["metadata"].get("labels") or {}).get("role") == role
            and same_path(cwd_of(i), run.proj)
            and (i.get("status") or {}).get("phase") == "Running"]


# ---------------------------------------------------------------- screen + status

def agent_status(uid: str) -> dict | None:
    code, out, _ = sh(["projmux", "agent", "status", "get", f"uid:{uid}"])
    if code != 0 or not out.strip():
        return None
    first, *rest = out.strip().split()
    kv = dict(x.split("=", 1) for x in rest if "=" in x)
    return {"kind": first, "observedAt": kv.get("observedAt"), "lifecycle": kv.get("lifecycle"),
            "source": kv.get("source"), "raw": out.strip()}


def fresh(s: dict, send: dict) -> bool:
    obs, sent = parse_ts(s.get("observedAt")), parse_ts(send.get("at"))
    pre = send.get("pre") or {}
    if not obs or not sent:
        return False
    return obs >= sent.replace(microsecond=0) and (s["kind"], s["observedAt"]) != (pre.get("kind"),
                                                                                   pre.get("observedAt"))


def resume_line(run: Run, agent: str, kind: str) -> str:
    """The pasteable resume command; on a role pane every wait / scope-pick in it names the agent."""
    cmd = RESUME[kind]
    if agent != run.data["pm"]:
        cmd = re.sub(r'("\$SMV" (?:wait|scope-pick))', rf"\1 --agent uid:{agent}", cmd)
    return f"resume: {cmd}"


def mark_stop(run: Run, agent: str, kind: str, code: int, path: Path) -> None:
    """Remember an open stop, so the first usable screen after it records the operator-side answer."""
    rec = {"kind": kind, "code": code, "at": now_iso(), "capture": str(path)}
    run.update(lambda d: d.setdefault("pending_stops", {}).__setitem__(agent, rec))


def record_answer(run: Run, agent: str, text: str) -> None:
    """Called only on a ready screen. After an open stop that means the stop went away on the operator side:
    smv infers an answer from the screen (it never sees the answer itself). Kept in notes.md and run.json."""
    if screen_state(text) != "ready" or not (run.data.get("pending_stops") or {}).get(agent):
        return
    stop = run.update(lambda d: (d.get("pending_stops") or {}).pop(agent, None))
    if not stop:
        return
    path = run.save_pane(f"after-{stop['kind']}", text, "state")
    entry = {**stop, "agent": agent, "resolved_seen": now_iso(), "capture_after": str(path), "inferred": True}
    run.update(lambda d: d.setdefault("operator_answers", []).append(entry))
    run.note(f"operator-side answer (inferred from the screen): the {stop['kind']} stop (exit {stop['code']}) on "
             f"uid:{agent}, shown at {stop['at']} ({stop['capture']}), is gone; the pane was ready again at "
             f"{entry['resolved_seen']} ({path}). Add the operator's own words with smv note --text if they gave any.")
    print(f"recorded the inferred operator-side answer to the {stop['kind']} stop in notes.md")


def stop_message(run: Run, agent: str, state: str, text: str) -> int:
    path = run.save_pane(f"stop-{state}", text, "menu" if state == "scope-menu" else "dialog")
    pane_uid = (run.data.get("panes", {}).get(agent) or {}).get("pane_uid", "?")
    print({
        "trust": "STOP (10): the folder-trust prompt is showing. Never answer it. Ask the operator to answer it "
                 "in the pane; after they confirm, resume.",
        "permission": "STOP (11): a permission prompt is showing. Never answer it. Show the operator the capture "
                      "below and ask; after they answer, resume.",
        "scope-menu": "STOP (12): the plugin scope menu is showing. Right after smv send --text "
                      f"'{INSTALL_LINE}', resume with scope-pick (it presses Enter only on a verified highlight). "
                      "At any other time, or when scope-pick itself exits 12, stop and ask the operator.",
        "dialog": "STOP (13): a dialog or question menu this run did not script is showing. Ask the operator; "
                  "after they answer, resume.",
    }[state])
    print(resume_line(run, agent, state))
    print(f"capture: {path}")
    print(f"operator can view the pane with: projmux focus pane uid:{pane_uid} -p uid:{run.project}"
          "   (moves the operator's own client; smv never runs focus)")
    mark_stop(run, agent, state, STOP_CODES[state], path)
    return STOP_CODES[state]


def screen_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def line_digests(run: Run, text: str) -> list[str]:
    """Keyed digests of every screen line: what was on screen before a send, without keeping the text."""
    key = digest_key(run)
    return [kd(key, line.rstrip()) for line in lines_of(text)]


def new_lines(run: Run, text: str, send: dict | None) -> list[str]:
    """Screen lines that were not on screen before the send (as many times), compared by keyed digest."""
    before, key, out = Counter((send or {}).get("pre_lines") or []), digest_key(run), []
    for line in lines_of(text):
        d = kd(key, line.rstrip())
        if before[d] > 0:
            before[d] -= 1
        else:
            out.append(line)
    return out


def has_hook(s: dict | None) -> bool:
    """projmux reports provider-hook status: a known kind with a real observedAt ('-' means no hook)."""
    return bool(s) and s.get("kind") not in (None, "", "-", "unknown") and s.get("observedAt") not in (None, "", "-")


def wait_loop(run: Run, agent: str, timeout: int, ready: bool = False, expect: str | None = None,
              fail: str | None = None, poll: float = 3.0, settle: int = 0) -> int:
    """Stops come first: a trust, permission, scope-menu or unscripted dialog screen (or an approval / input
    status) ends the wait with its stop code before any --expect / --fail / done check is looked at."""
    pane = run.pane(agent, wait=60)
    send = run.data["sends"].get(agent)
    exp, bad = (re.compile(x) if x else None for x in (expect, fail))
    deadline, ready_since, quiet, last = time.time() + budget(timeout), None, 0, None
    if not (ready or exp or send):
        die("nothing to wait for: no smv send to this agent yet (use --ready or --expect)")
    while True:
        run.record_agents()
        if tmux_dead(run, pane)[0] in ("1", "gone"):  # the pane died while we waited: say so, keep its screen
            path, text, rep = capture_any(run, agent, "dead-pane")
            print(f"{format_report(rep)}\ncapture: {path or 'not possible (pane gone)'}")
            lock_hint(text)
            return 1
        text = run.capture(pane)
        st, s = screen_state(text), agent_status(agent)
        if st in STOP_CODES:
            return stop_message(run, agent, st, text)
        if s and s["kind"] == "approval_required":
            return stop_message(run, agent, "permission", text)
        if s and s["kind"] == "input_required":
            return stop_message(run, agent, "dialog", text)
        fresh_lines = new_lines(run, text, send) if (exp or bad) else []
        if bad and any(bad.search(x) for x in fresh_lines):
            print(f"FAIL: --fail pattern appeared; capture: {run.save_pane('wait-fail', text, 'match', bad)}")
            return 1
        if exp and any(exp.search(x) for x in fresh_lines):
            print(f"OK: --expect pattern appeared; screen={st}; "
                  f"capture: {run.save_pane('wait-expect', text, 'match', exp)}")
            record_answer(run, agent, text)
            return 0
        busy = st != "ready" or bool(s and s["kind"] == "in_progress")
        if ready and not exp:
            ready_since = None if busy else (ready_since or time.time())
            if ready_since and time.time() - ready_since >= settle:
                print(f"READY screen={st} status={s['raw'] if s else 'n/a'}")
                record_answer(run, agent, text)
                return 0
        elif send and not exp:
            if has_hook(s):
                if not busy and s["kind"] in DONE and fresh(s, send):
                    print(f"DONE status={s['raw']}; capture: {run.save_pane('wait-done', text, 'state')}")
                    record_answer(run, agent, text)
                    return 0
            else:  # no provider-hook status: a quiet, ready screen that differs from the raw pre-send screen
                changed = screen_digest(text) != send.get("pre_sha")
                quiet = quiet + 1 if (not busy and text == last and changed) else 0
                if quiet >= 3:
                    print(f"DONE (screen quiet, no hook status); capture: {run.save_pane('wait-done', text, 'state')}")
                    record_answer(run, agent, text)
                    return 0
        last = text
        if time.time() >= deadline:
            print(f"TIMEOUT after {timeout:.0f}s screen={st} status={s['raw'] if s else 'n/a'}; "
                  f"capture: {run.save_pane('wait-timeout', text)}")
            return 124
        time.sleep(poll)


# ---------------------------------------------------------------- snapshots (user scope + lead tree)

def listing(argv: list[str], cwd: Path) -> list[dict]:
    code, out, err = sh(argv, cwd=cwd)
    data = jload(out)
    if code != 0 or not isinstance(data, list):  # a failed listing must never read as "everything removed"
        die(f"{' '.join(argv)} failed (exit {code}): {err.strip()[:200]}", 1)
    return data


def digest_key(run: Run, create: bool = False) -> bytes | None:
    """Per-run HMAC key for per-key and per-row digests, so a short value (true, a mode name) cannot be read back
    from evidence. It lives in the scratch project's .git/ (outside hashes and evidence) and goes with it."""
    p = run.proj / ".git" / "smv-digest-key"
    if create and not p.exists():
        p.write_bytes(os.urandom(32))
        p.chmod(0o600)
    return p.read_bytes() if p.is_file() else None


def kd(key: bytes | None, value) -> str:
    if key is None:
        return "no-key"
    return hmac.new(key, json.dumps(value, sort_keys=True).encode(), "sha256").hexdigest()[:32]


def config_file_digest(path: Path, key: bytes | None) -> dict:
    """Whole-file sha256 plus keyed digests per top-level key and per second-level key of dict values.
    Only key names and digests are kept; the file is never copied and no value is ever stored."""
    if not path.is_file():
        return {"sha256": None, "keys": {}, "sub": {}}
    raw = path.read_bytes()
    data, keys, sub = jload(raw.decode(errors="replace")), {}, {}
    if isinstance(data, dict):
        for k, v in data.items():
            keys[k] = kd(key, v)
            if isinstance(v, dict):
                sub[k] = {s: kd(key, w) for s, w in v.items()}
    return {"sha256": hashlib.sha256(raw).hexdigest(), "keys": keys, "sub": sub}


def changed_key_paths(base: dict, now: dict) -> list[tuple[str, str, str | None]]:
    """(ADDED|MISSING|CHANGED, top key, second-level key or None), deepest level only: never a value."""
    bk, nk, bsub, nsub, rows = base["keys"], now["keys"], base["sub"], now["sub"], []
    for top in sorted(set(bk) | set(nk)):
        if bk.get(top) == nk.get(top):
            continue
        if top in bsub and top in nsub:  # a dict on both sides: name its second-level keys
            b, n = bsub[top], nsub[top]
            rows += [("MISSING" if s not in n else "ADDED" if s not in b else "CHANGED", top, s)
                     for s in sorted(set(b) | set(n)) if b.get(s) != n.get(s)]
        else:
            rows.append(("MISSING" if top not in nk else "ADDED" if top not in bk else "CHANGED", top, None))
    return rows


def key_path(row: tuple[str, str, str | None]) -> str:
    return f"{row[0]} {row[1]}" + (f".{row[2]}" if row[2] is not None else "")


def lead_tree(repo: Path) -> dict:
    """HEAD, git status (ignored entries included) and the content sha256 of every dirty or ignored file: git
    status alone misses a second edit to a file that was already dirty, and never shows a gitignored file
    (.env, __pycache__/, state files) at all. An ignored directory is hashed file by file."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}  # git status must not even refresh the lead tree's index
    _, head, _ = sh(["git", "-C", str(repo), "rev-parse", "HEAD"], env=env)
    code, out, err = sh(["git", "-C", str(repo), "status", "--porcelain=v1", "-z", "-uall", "--ignored=matching"],
                        env=env)
    if code != 0:
        die(f"git status of the lead tree failed (exit {code}): {err.strip()[:200]}", 1)
    status, dirty, fields = [], {}, out.split("\0")
    while fields:
        entry = fields.pop(0)
        if len(entry) < 4:
            continue
        xy, rel = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:
            fields.pop(0)  # -z puts the rename source in its own field
        status.append(f"{xy} {rel}")
        p = repo / rel
        if rel.endswith("/") and p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and not f.is_symlink():
                    dirty[f.relative_to(repo).as_posix()] = sha256_file(f)
        else:
            dirty[rel] = sha256_file(p) if p.is_file() else "-"
    return {"head": head.strip(), "status": sorted(status), "dirty": dirty}


def user_state(run: Run) -> dict:
    """Snapshot of everything a run must not change. Evidence-safe: digests and key paths, never values."""
    key = digest_key(run)
    plugin_rows = sorted([str(p.get("id")), str(p.get("scope")), kd(key, p),
                          p.get("scope") in ("project", "local") and same_path(p.get("projectPath"), run.proj)]
                         for p in listing(["claude", "plugin", "list", "--json"], run.ev))
    mkts = sorted([str(m.get("name")), kd(key, m)]
                  for m in listing(["claude", "plugin", "marketplace", "list", "--json"], run.ev))
    cfg = claude_config_dir()
    slack = None
    if SLACK_DEFAULT_STATE.is_dir():  # name, type and content of every entry, all as one keyed digest
        rows = []
        for p in sorted(SLACK_DEFAULT_STATE.rglob("*")):
            kind = "link" if p.is_symlink() else "dir" if p.is_dir() else "file" if p.is_file() else "other"
            rows.append([p.relative_to(SLACK_DEFAULT_STATE).as_posix(), kind,
                         sha256_file(p) if kind == "file" else (os.readlink(p) if kind == "link" else None)])
        slack = {"entries": len(rows), "contents": kd(key, rows)}
    return {"taken": now_iso(), "keyed": key is not None, "plugin_rows": plugin_rows, "marketplaces": mkts,
            "files": {rel: config_file_digest(cfg / rel, key) for rel in (USER_SETTINGS, *USER_REGISTRY)},
            "slack_default_state": slack, "lead_tree": lead_tree(run.repo)}


def write_snapshot(run: Run, label: str) -> dict:
    snap = user_state(run)
    (run.ev / "snapshots" / f"{label}.json").write_text(json.dumps(snap, indent=1))
    return snap


def compare(base: dict, now: dict, phase: str) -> tuple[list[str], list[str]]:
    """(fails, notes). No flag excuses a fail: user settings, other plugin rows, the marketplace registry rows,
    the default Slack state dir and the lead tree must equal the baseline. phase 'doctor' tolerates this run's
    own install row and a registry entry this run added; phase 'cleanup' requires both gone."""
    fails, notes = [], []
    keyed = bool(base.get("keyed") and now.get("keyed"))
    bs, ns = base["files"][USER_SETTINGS], now["files"][USER_SETTINGS]
    if bs["sha256"] != ns["sha256"]:
        paths = [key_path(r) for r in changed_key_paths(bs, ns)] if keyed else ["(key paths need the digest key)"]
        fails.append(f"user {USER_SETTINGS} changed (sha256 {str(bs['sha256'])[:12]} -> {str(ns['sha256'])[:12]}): "
                     + ", ".join(paths or ["whole file only (formatting)"]))
    for rel in USER_REGISTRY:  # Claude Code bookkeeping (install rows, refresh timestamps): key paths, as notes
        b, n = base["files"][rel], now["files"][rel]
        if b["sha256"] != n["sha256"]:
            rows = changed_key_paths(b, n) if keyed else []
            own = RUN_OWNED[rel]
            mine = [key_path(r) for r in rows if r[1] == own[0] and own[1] in (None, r[2])]
            other = [key_path(r) for r in rows if key_path(r) not in mine]
            notes.append(f"user {rel} changed: run-owned key paths {mine or '-'}; other key paths {other or '-'}")
    bo = Counter(tuple(r[:3]) for r in base["plugin_rows"] if not r[3])
    no = Counter(tuple(r[:3]) for r in now["plugin_rows"] if not r[3])
    fails += [f"plugin row MISSING or CHANGED: {r[0]} ({r[1]} scope)" for r in sorted(bo - no)]
    fails += [f"plugin row ADDED or CHANGED: {r[0]} ({r[1]} scope)" for r in sorted(no - bo)]
    mine = [r for r in now["plugin_rows"] if r[3]]
    if phase == "cleanup" and mine:
        fails.append(f"this run's install row(s) still registered: {[f'{r[0]} ({r[1]})' for r in mine]}")
    elif mine:
        notes.append(f"this run's install row(s): {[f'{r[0]} ({r[1]})' for r in mine]}")
    bm, nm = dict(map(tuple, base["marketplaces"])), dict(map(tuple, now["marketplaces"]))
    for name in sorted(set(bm) | set(nm)):
        if bm.get(name) == nm.get(name):
            continue
        if phase == "doctor" and name == MARKETPLACE and name not in bm:
            notes.append(f"marketplace registry entry '{name}' added by this run's project-scope declaration")
            continue
        state = "MISSING" if name not in nm else ("ADDED" if name not in bm else "CHANGED")
        fails.append(f"marketplace registry row {state}: {name}")
    if base["slack_default_state"] != now["slack_default_state"]:
        b, n = base["slack_default_state"] or {}, now["slack_default_state"] or {}
        fails.append(f"default Slack state dir changed (names, types or contents): entries {b.get('entries', 'none')}"
                     f" -> {n.get('entries', 'none')}")
    bl, nl = base["lead_tree"], now["lead_tree"]
    if bl != nl:
        content = sorted(p for p in set(bl["dirty"]) & set(nl["dirty"]) if bl["dirty"][p] != nl["dirty"][p])
        fails.append(f"lead tree changed (invariant 12 run-time check): HEAD {bl['head'][:12]} -> {nl['head'][:12]}; "
                     f"status +{sorted(set(nl['status']) - set(bl['status']))} "
                     f"-{sorted(set(bl['status']) - set(nl['status']))}; content changed {content}")
    return fails, notes


# ---------------------------------------------------------------- plugin install state

def tree_digest(root: Path) -> dict[str, str]:
    out = {}
    for top in SHIPPED:
        base = root / top
        for p in sorted(base.rglob("*")) if base.is_dir() else []:
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                out[p.relative_to(root).as_posix()] = sha256_file(p)
    return out


def plugin_rows_step(run: Run, label: str, rows: list[dict], code: int) -> None:
    """Evidence form of `claude plugin list --json`: (id, scope, keyed row digest, this run's row), never raw."""
    key = digest_key(run)
    lines = [f"{p.get('id')}\t{p.get('scope')}\t{kd(key, p)}\t"
             f"{'this-run' if same_path(p.get('projectPath'), run.proj) else '-'}" for p in rows]
    (run.ev / "steps" / f"{run.next_id(label)}.txt").write_text(
        f"$ claude plugin list --json   (rows reduced to id, scope, keyed digest)\n# exit: {code}\n"
        + "\n".join(sorted(lines)) + "\n")


def plugin_checks(run: Run) -> list[tuple[str, bool, str]]:
    code, out, err = sh(["claude", "plugin", "list", "--json"], cwd=run.proj)
    rows = jload(out, []) if code == 0 else []
    plugin_rows_step(run, "plugin-list", rows if isinstance(rows, list) else [], code)
    entries = [p for p in (rows if isinstance(rows, list) else []) if p.get("id") == PLUGIN_ID]
    mine = [p for p in entries if p.get("scope") == "project" and same_path(p.get("projectPath"), run.proj)]
    local = [p for p in entries if p.get("scope") == "local" and same_path(p.get("projectPath"), run.proj)]
    want = json.loads((run.repo / ".claude-plugin/plugin.json").read_text())["version"]
    checks = [("plugin list --json", code == 0, err.strip()[:120] or "ok"),
              ("project-scope install for this scratch project", len(mine) == 1, f"{len(mine)} entr(y/ies)"),
              ("no local-scope install for this scratch project", not local, f"{len(local)} entr(y/ies)")]
    if len(mine) == 1:
        p = mine[0]
        checks.append(("installed version == checkout plugin.json", p.get("version") == want,
                       f"installed {p.get('version')} vs checkout {want}"))
        checks.append(("enabled", bool(p.get("enabled")), str(p.get("enabled"))))
        if p.get("installPath") and Path(p["installPath"]).is_dir():
            installed, checkout = tree_digest(Path(p["installPath"])), tree_digest(run.repo)
            drift = sorted(k for k in set(installed) | set(checkout) if installed.get(k) != checkout.get(k))
            checks.append(("installed copy == checkout (shipped dirs)", not drift,
                           f"{len(drift)} differing: {drift[:8]}" if drift else f"{len(checkout)} files identical"))
        else:
            checks.append(("installed copy == checkout (shipped dirs)", False, "no installPath on disk"))
    settings = run.proj / ".claude/settings.json"
    enabled = ((jload(settings.read_text(), {}) if settings.is_file() else {}).get("enabledPlugins") or {})
    checks.append(("scratch .claude/settings.json enables the plugin", enabled.get(PLUGIN_ID) is True,
                   str(enabled.get(PLUGIN_ID))))
    return checks


def print_checks(title: str, checks: list[tuple[str, bool, str]], run: Run | None = None) -> int:
    """Print PASS/FAIL lines and the verdict; with a run, also keep them as a step file."""
    bad = sum(1 for _, ok, _ in checks if not ok)
    text = "".join(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}\n" for name, ok, detail in checks)
    text += f"{title}: {'PASS' if not bad else f'FAIL ({bad})'}\n"
    if run is not None:
        (run.ev / "steps" / f"{run.next_id(title)}.txt").write_text(text + f"# exit: {1 if bad else 0}\n")
    print(text, end="")
    return 0 if not bad else 1


# ---------------------------------------------------------------- hashes + transcripts

def hash_tree(run: Run, exclude: list[str] = ()) -> dict[str, str]:
    skip = {".git", *(e.strip("/") for e in exclude)}
    return {p.relative_to(run.proj).as_posix(): sha256_file(p) for p in sorted(run.proj.rglob("*"))
            if p.is_file() and p.relative_to(run.proj).parts[0] not in skip}


def read_hashes(run: Run, label: str) -> dict[str, str]:
    p = run.ev / "hashes" / f"{slug(label)}.sha256"
    if not p.is_file():
        die(f"no hash list {label} (run: smv hash --label {label})")
    return {rel: h for h, rel in (line.split("  ", 1) for line in p.read_text().splitlines() if line)}


def hash_diff(a: dict, b: dict) -> tuple[list, list, list]:
    """(added, missing, changed) paths from list a to list b."""
    added = sorted(set(b) - set(a))
    missing = sorted(set(a) - set(b))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    return added, missing, changed


def transcript_source(run: Run, agent: str) -> Path | None:
    """The agent's live Claude transcript. smv reads it in place and never copies it into evidence: it holds
    tool_result bodies and whatever the session loaded (user settings, CLAUDE.md, system reminders)."""
    p = ((((run.agent_json(agent).get("status") or {}).get("sessionRef") or {}).get("claude") or {})
         .get("transcriptPath"))
    return Path(p) if p and Path(p).is_file() else None


def home_rel(p: Path | str) -> str:
    home = str(Path.home())
    return "~" + str(p)[len(home):] if str(p) == home or str(p).startswith(home + "/") else str(p)


# scripts/scrub.sh rows reused as drop rules; the patterns are read from that file, never copied here
REDACT_NAMES = ("slack-token", "anthropic-key", "openai-key", "github-token", "aws-key", "jwt", "private-key",
                "slack-channel-id", "supabase-url", "env-assignment", "email")
SCRUB_ROW_RE = re.compile(r"^\s*\[([a-z-]+)\]='(.*)'\s*$")
ACTION_RE = re.compile(r"^(CREATE|MERGE|SKIP) +(\S+)(?: {2}\(.*\))?$")  # scaffold.py: f"{action:<7} {path}"
TAG_RE = re.compile(r"<(command-name|command-args)>(.*?)</\1>", re.S)
REMINDER_RE = re.compile(r"<system-reminder>.*?(?:</system-reminder>|\Z)", re.S)
_PATTERNS: dict = {}


def redaction_patterns(repo: Path) -> dict:
    """Drop rules for every evidence line taken from a transcript or a screen: the token and e-mail rows of
    scripts/scrub.sh (plus its untracked .scrub-local literals), any e-mail address, and the user's Claude
    config paths. Fails closed: without every scrub.sh row nothing is written."""
    if repo not in _PATTERNS:
        src = repo / "scripts/scrub.sh"
        rows = dict(m.groups() for line in (src.read_text().splitlines() if src.is_file() else [])
                    if (m := SCRUB_ROW_RE.match(line)))
        missing = [n for n in REDACT_NAMES if n not in rows]
        if missing:
            die(f"STOP: {src} lacks the redaction pattern(s) {missing}; no transcript or screen evidence is "
                "written without them", 1)
        pats = {n: re.compile(("(?:^|[\\s:])" + rows[n][1:] if rows[n].startswith("^") else rows[n])
                              .replace("[:space:]", r"\s")) for n in REDACT_NAMES}
        pats["email-any"] = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
        pats["claude-config-path"] = re.compile(r"/home/[a-z][a-z0-9_-]+/\.claude\b|~/\.claude\b|"
                                                + re.escape(str(claude_config_dir())) + r"\b")
        local = repo / ".scrub-local"
        lits = [x for x in (local.read_text().splitlines() if local.is_file() else []) if x.strip()]
        if lits:
            pats["local-literal"] = re.compile("|".join(map(re.escape, lits)))
        _PATTERNS[repo] = pats
    return _PATTERNS[repo]


def redact(run: Run, lines: list[str]) -> tuple[list[str], Counter]:
    """Keep the lines no drop rule matches; count the dropped ones per rule (a line counts once)."""
    kept, dropped = [], Counter()
    for line in lines:
        rule = next((n for n, rx in redaction_patterns(run.repo).items() if rx.search(line)), None)
        if rule:
            dropped[rule] += 1
        else:
            kept.append(line)
    return kept, dropped


def redact_screen(run: Run, text: str, kind: str = "auto", pattern: re.Pattern | None = None) -> tuple[str, Counter]:
    """Evidence form of a capture: a header with the whole screen's sha256 and line count, then only the kept
    lines (numbered as on screen). Redaction runs on the whole screen first, joined across lines too, so a token
    the TUI wrapped is caught; a dropped kept line becomes a placeholder."""
    lines = lines_of(text)
    drops = drop_map(lines, redaction_patterns(run.repo))
    kept = keep_lines(text, kind, pattern)
    dropped = Counter(drops[i].split(" ")[0] for i, _ in kept if i in drops)
    head = (f"# screen sha256 {hashlib.sha256(text.encode()).hexdigest()}  lines {len(lines)}  state "
            f"{screen_state(text)}\n# kept {len(kept)} line(s) ({kind}): only what the check needs; the full "
            f"screen is never written; dropped by redaction: {dict(dropped) or 0}\n")
    body = "".join(f"{i + 1:4d}| " + (f"[smv: line dropped by redaction: {drops[i]}]" if i in drops else line)
                   + "\n" for i, line in kept)
    return head + body, dropped

SCAFFOLD_RE = re.compile(r"""([^\s"'`]*skills/init/scaffold\.py)""")
TARGET_RE = re.compile(r"""--target(?:=|\s+)(?:"([^"]*)"|'([^']*)'|(\S+))""")
PROJECT_TARGETS = {".", "./", "${CLAUDE_PROJECT_DIR}", "$CLAUDE_PROJECT_DIR", "$PWD", "${PWD}", "$(pwd)"}


def scaffold_call(command: str, proj: Path | None) -> dict | None:
    """The scaffold.py run inside one Bash command: mode (apply, dry-run, or mixed when one command runs it more
    than once), and whether --target names the scratch project (., the project dir variable, or its path)."""
    segs = [x for x in re.split(r"&&|\|\||;|\n|\|", command) if SCAFFOLD_RE.search(x)]
    if not segs:
        return None
    m = TARGET_RE.search(segs[0])
    target = next((g for g in m.groups() if g is not None), None) if m else None
    ok = target in PROJECT_TARGETS or (bool(target) and proj is not None and os.path.isabs(target)
                                       and same_path(target, proj))
    mode = "mixed" if len(segs) > 1 else ("dry-run" if re.search(r"(?<!\S)--dry-run\b", segs[0]) else "apply")
    return {"mode": mode, "target_ok": ok}


def proof_lines(path: Path, since: dt.datetime | None = None, proj: Path | None = None) -> list[dict]:
    """The whitelisted proof rows of a transcript, each {ts, kind, text[, id, scaffold, error]}: tool_use names
    (a scaffold.py Bash call also gets its mode and target), slash command name and args, the summary lines the
    agent echoed, and the summary and CREATE/MERGE/SKIP lines of the tool result whose tool_use_id is a
    scaffold.py call. No other tool_result body is read; system reminders (CLAUDE.md, loaded context), injected
    skill text and thinking are skipped."""
    out, scaffold_ids = [], set()
    for raw in path.read_text(errors="replace").splitlines():
        rec = jload(raw)
        if not isinstance(rec, dict) or rec.get("type") not in ("user", "assistant") or rec.get("isMeta"):
            continue
        ts = rec.get("timestamp") or "-"
        if since and ((t := parse_ts(rec.get("timestamp"))) is None or t < since):
            continue
        content = (rec.get("message") or {}).get("content")
        blocks = [c for c in (content if isinstance(content, list) else []) if isinstance(c, dict)]
        if rec["type"] == "assistant":
            for c in (c for c in blocks if c.get("type") == "tool_use"):
                cmd = (c.get("input") or {}).get("command") if c.get("name") == "Bash" else None
                call = scaffold_call(cmd, proj) if isinstance(cmd, str) else None
                if call:
                    scaffold_ids.add(c.get("id"))
                out.append({"ts": ts, "kind": "tool_use", "text": str(c.get("name")), "id": c.get("id"),
                            "scaffold": call})
            out += [{"ts": ts, "kind": "assistant summary", "text": ln.strip()[:300]} for c in blocks
                    if c.get("type") == "text" for ln in (c.get("text") or "").splitlines() if SUMMARY_RE.search(ln)]
            continue
        typed = [content] if isinstance(content, str) else [c.get("text") or "" for c in blocks
                                                             if c.get("type") == "text"]
        out += [{"ts": ts, "kind": m.group(1), "text": " ".join(m.group(2).split())[:300]} for t in typed
                for m in TAG_RE.finditer(REMINDER_RE.sub("", t))]
        for c in blocks:
            if c.get("type") != "tool_result" or c.get("tool_use_id") not in scaffold_ids:
                continue  # only the result of a scaffold.py call is read, and only its proof lines
            body = "\n".join(texts(c.get("content")))
            out += [{"ts": ts, "kind": "scaffold result", "text": ln.strip(), "id": c.get("tool_use_id"),
                     "error": bool(c.get("is_error"))} for ln in body.splitlines()
                    if SUMMARY_RE.search(ln) or ACTION_RE.match(ln.strip())]
    return out


def proof_text(r: dict) -> str:
    """One extract line for a proof row; a scaffold.py call shows its mode and target, never its command."""
    extra = ""
    if r["kind"] == "tool_use" and r.get("scaffold"):
        sc = r["scaffold"]
        extra = f" [scaffold.py {sc['mode']}, target {'scratch project' if sc['target_ok'] else 'other'}, " \
                f"id {r.get('id')}]"
    elif r["kind"] == "scaffold result":
        extra = f" [result of {r.get('id')}{', is_error' if r.get('error') else ''}]"
    return f"{r['ts']} {r['kind']}: {r['text']}{extra}"


def record_transcript(run: Run, agent: str, check: str, matches: list[str] = ()) -> tuple[list[str], int]:
    """Transcript evidence of one check: <agent>.meta.json keeps the path (home-relative), sha256 and size of
    every read, and <agent>.extract.txt the redacted proof lines plus every check's redacted match lines.
    Returns (this check's match lines that survived redaction, how many it dropped)."""
    tdir = run.ev / "transcripts"
    meta_p, ext_p = tdir / f"{agent}.meta.json", tdir / f"{agent}.extract.txt"
    meta = jload(meta_p.read_text(), {}) if meta_p.is_file() else {}
    src = transcript_source(run, agent)
    obs = {"at": now_iso(), "check": check}
    if src:
        raw = src.read_bytes()
        obs.update(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
        meta["path"] = home_rel(src)
    else:
        obs["missing"] = True
    meta.setdefault("reads", []).append(obs)
    kept, dropped = redact(run, list(matches))
    meta.setdefault("matches", []).extend(f"{check}: {x}" for x in kept)
    meta["dropped_match_lines"] = dict(Counter(meta.get("dropped_match_lines") or {}) + dropped)
    if src:
        standing, d_standing = redact(run, [proof_text(r) for r in proof_lines(src, None, run.proj)])
        meta["dropped_proof_lines"] = dict(d_standing)
        ext_p.write_text(
            f"# transcript extract uid:{agent}  {meta['path']}  sha256 {obs['sha256']}  size {obs['size']}\n"
            f"# whitelisted proof lines only, then redacted (dropped: proof {meta['dropped_proof_lines']}, "
            f"matches {meta['dropped_match_lines']}); the transcript itself is never copied\n"
            "## proof lines\n" + "".join(f"{x}\n" for x in standing)
            + "## check matches\n" + "".join(f"{x}\n" for x in meta["matches"]))
        meta["extract"] = ext_p.name
    meta_p.write_text(json.dumps(meta, indent=1))
    return kept, sum(dropped.values())


def match_lines(ts: str, s: str, m: re.Match, context: bool) -> list[str]:
    """Proof lines of one match: the matched text only, or (assistant-only) its line with one line of context
    on each side. The rest of the searched text is never written."""
    if not context:
        return [f"{ts} match: {' / '.join(m.group(0).splitlines())[:200]}"]
    lines = s.splitlines()
    first = s.count("\n", 0, m.start())
    last = first + m.group(0).count("\n")
    out = [f"{ts}   before: {lines[first - 1].strip()[:200]}"] if first > 0 else []
    out.append(f"{ts} match: {' / '.join(x.strip() for x in lines[first:last + 1])[:300]}")
    return out + ([f"{ts}   after: {lines[last + 1].strip()[:200]}"] if last + 1 < len(lines) else [])


def texts(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from texts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from texts(v)


def transcript_texts(path: Path, since: dt.datetime | None, assistant_only: bool = False,
                     tool_inputs: bool = False) -> list[tuple[str, str]]:
    """(timestamp, text) of every string in the transcript, searched in memory only. assistant_only yields the
    agent's visible replies and tool-call inputs: no user records, which also carry the injected SKILL.md text (a
    pattern copied from the skill cannot match the skill itself), and no thinking. tool_inputs yields only the
    agent's own tool calls, one JSON object each ({"command": "..."}, {"file_path": "..."}), so an inbound
    message that quotes a command cannot match."""
    out = []
    for line in path.read_text(errors="replace").splitlines():
        rec = jload(line)
        if not isinstance(rec, dict) or ((assistant_only or tool_inputs) and rec.get("type") != "assistant"):
            continue
        ts = parse_ts(rec.get("timestamp"))
        if since and (not ts or ts < since):
            continue
        content = (rec.get("message") or {}).get("content")
        blocks = [c for c in (content if isinstance(content, list) else []) if isinstance(c, dict)]
        if tool_inputs:
            out += [(rec.get("timestamp", ""), json.dumps(c.get("input"), sort_keys=True))
                    for c in blocks if c.get("type") == "tool_use"]
            continue
        if assistant_only:  # visible replies and tool-call inputs; thinking blocks are never searched
            out += [(rec.get("timestamp", ""), c.get("text") or "") for c in blocks if c.get("type") == "text"]
            out += [(rec.get("timestamp", ""), t) for c in blocks if c.get("type") == "tool_use"
                    for t in texts(c.get("input"))]
            continue
        out += [(rec.get("timestamp", ""), s) for s in texts(rec.get("message", rec))]
    return out


def since_mark(run: Run, use_last_send: bool) -> dt.datetime | None:
    return parse_ts(run.data["last_send"]) if use_last_send else None


# ---------------------------------------------------------------- subcommands

def launch_failed(run: Run, what: str, code: int, out: str, err: str, cap: float) -> int:
    """R1: a failed launch step stops the launch. Nothing after it is created."""
    print(f"FAIL: {what} exited {code}; step file: {run.last_step}")
    for line in (out + err).strip().splitlines()[-6:]:
        print(f"    {line}")
    if code == 124:
        print(f"exit 124: the step did not finish within its {cap:.0f} s timeout or the launch budget ran out. "
              "A projmux step that hangs is usually waiting for the registry lock held by another projmux writer.")
    lock_hint(out + err)
    print("launch stopped here: nothing after this step was created. Run: smv cleanup")
    return 1


def cmd_launch(a) -> int:
    BUDGET["deadline"] = time.time() + a.timeout  # --timeout bounds the whole launch, every call and wait in it
    try:
        return launch_steps(a)
    finally:
        BUDGET["deadline"] = None


def launch_steps(a) -> int:
    repo = repo_root()
    root = Path(a.scratch_root).expanduser()
    if not root.is_absolute() or not root.is_dir():
        die("--scratch-root must be an existing absolute directory")
    root = root.resolve()
    if root == Path.home().resolve() or within(root, repo):
        die("--scratch-root must not be the home directory or inside the checkout (the lead tree must not change)")
    if within(root, claude_config_dir()):
        die("--scratch-root must not be inside the Claude config dir")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", a.prefix) or ".." in a.prefix:
        die("--prefix must be one plain file-name word (letters, digits, '.', '_', '-'; no '/', no '..')")
    checks = preflight()
    if print_checks("preflight", checks):
        return 2
    run_id = f"{a.prefix}-{dt.datetime.now(dt.timezone.utc):%Y%m%d%H%M%S}-{os.getpid()}"
    rundir = (root / run_id).resolve()
    if rundir.parent != root or within(rundir, repo) or rundir.exists():
        die(f"run directory {rundir} is not a new directory directly under the scratch root")
    (rundir / "proj").mkdir(parents=True)
    run = Run.create(rundir, repo, run_id)
    print(f"SMV_RUN={rundir}\nEVIDENCE={run.ev}\nlaunch budget: {a.timeout} s")

    def step(label: str, argv: list[str], cap: float) -> tuple[int, str, str, float]:
        cap = budget(cap)
        return (*run.cmd(label, argv, timeout=cap), cap)

    code, out, err, cap = step("git-init", ["git", "init", "-q", str(run.proj)], 30)
    if code != 0:
        return launch_failed(run, "git init", code, out, err, cap)
    digest_key(run, create=True)
    try:
        write_snapshot(run, "baseline")
    except SystemExit:
        print("FAIL: baseline snapshot (see the smv: line above); nothing was created in projmux. Run: smv cleanup")
        return 1
    code, out, err, cap = step("create-project", ["projmux", "create", "project", "--root", str(run.proj),
                                                  "--name", run_id, "-o", "uid"], 60)
    uid = next(iter(PROJ_RE.findall(out + err)), None)
    if uid:  # recorded even when the step failed, so cleanup can remove it
        run.update(lambda d: d["created"].__setitem__("project", uid))
    if code != 0 or not uid:
        return launch_failed(run, "projmux create project", code or 1, out, err, cap)
    code, out, err, cap = step("start-project", ["projmux", "start", "project", f"uid:{uid}", "-o", "receipt"], 120)
    if code != 0:
        return launch_failed(run, "projmux start project", code, out, err, cap)
    code, out, err, cap = step("create-claude", ["projmux", "create", "claude", "--host", "tmux", "--project",
                                                 f"uid:{uid}", "--cwd", str(run.proj), "--name", "pm",
                                                 "--label", f"smv-run={run_id}", "-o", "uid"], 240)
    pm = next(iter(AGENT_RE.findall(out)), None)
    if pm:  # never guess the PM from the project's agent list: only a uid projmux printed counts
        run.update(lambda d: (d["created"]["agents"].append(pm), d.__setitem__("pm", pm)))
    if code != 0 or not pm:
        return launch_failed(run, "projmux create claude", code or 1, out, err, cap)
    pane = run.pane(pm, wait=90, required=False)
    if not pane:  # R2: a dead or Offline pane is reported as exactly that, with its screen kept
        path, text, rep = capture_any(run, pm, "launch-dead-pane")
        print(f"FAIL: {format_report(rep)}\ncapture: {path or 'not possible (no tmux pane left)'}")
        lock_hint(text)
        print("launch stopped here. Run: smv cleanup")
        return 1
    print(f"PM agent=uid:{pm} tmux-pane={pane} (alive) project=uid:{uid} root={run.proj}")
    rc = wait_loop(run, pm, timeout=budget(a.timeout), ready=True, settle=3)
    if rc == 0:
        print(f"pane permission mode: {mode_detail(run, run.capture(pane))}")
    if rc == 124:
        print("launch: the pane did not become ready within the launch budget. Keep the output; run: smv cleanup")
    elif rc not in (0, 10):
        print("launch: FAIL. Keep the output; run: smv cleanup")
    return rc


def preflight() -> list[tuple[str, bool, str]]:
    out = []
    for name, argv in (("projmux", ["projmux", "version"]), ("tmux", ["tmux", "-V"]),
                       ("claude", ["claude", "--version"]), ("git", ["git", "--version"])):
        code, o, e = sh(argv)
        out.append((name, code == 0, (o or e).strip().splitlines()[0] if (o or e).strip() else f"exit {code}"))
    code, o, _ = sh(["claude", "plugin", "--help"])
    out.append(("claude plugin --help lists marketplace + install", code == 0 and "marketplace" in o and
                "install" in o, f"exit {code}"))
    out.append(("python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0]))
    return out


def cmd_doctor(a) -> int:
    run = Run.load(a.run)
    checks = preflight()
    pj = run.project_json() if run.project else {}
    checks.append(("project recorded by this run", bool(run.project), f"uid:{run.project}"))
    checks.append(("project root == scratch project", same_path((pj.get("spec") or {}).get("root"), run.proj),
                   str((pj.get("spec") or {}).get("root"))))
    checks.append(("project runtime live", bool(((pj.get("status") or {}).get("session") or {}).get("live")),
                   str(((pj.get("status") or {}).get("session") or {}).get("name"))))
    pm = run.data["pm"]
    aj = run.agent_json(pm) if pm else {}
    checks.append(("PM agent Running", (aj.get("status") or {}).get("phase") == "Running",
                   f"uid:{pm} phase={(aj.get('status') or {}).get('phase')}"))
    checks.append(("PM agent cwd == scratch project",
                   same_path(((aj.get("spec") or {}).get("workspace") or {}).get("cwd"), run.proj), "spec.workspace"))
    pane = run.pane(pm, wait=10, required=False) if pm and aj else None
    text = run.capture(pane) if pane else ""
    st = screen_state(text) if pane else "no pane"
    s = agent_status(pm) if pm else None
    detail = f"tmux {pane} screen={st}" if pane else (format_report(pane_report(run, pm)) if pm else "no PM agent")
    checks.append(("PM pane usable (not at a prompt)", st in ("ready", "busy"), detail))
    if pane:
        checks.append(("pane permission mode (info)", True, mode_detail(run, text)))
    checks.append(("projmux interaction status (info)", True, s["raw"] if s else "n/a: screen fallback in wait"))
    if not a.pre:
        checks += plugin_checks(run)
    checks += isolation_checks(run, f"doctor-{run.data['step']:03d}", "doctor")
    return print_checks("doctor", checks, run)


def mode_detail(run: Run, text: str) -> str:
    """Record the PM pane's permission mode (footer) in run.json and say what it means for the proof."""
    mode = permission_mode(text)
    run.update(lambda d: d.__setitem__("permission_mode", {"mode": mode, "seen": now_iso()}))
    if mode in ("auto mode", "bypass permissions"):
        return (f"WARN {mode} on: permission prompts may never show, so stop 11 may never fire and this run "
                "cannot prove the skills' allowed-tools coverage; say so in the report")
    return f"{mode}: permission prompts show as usual"


def isolation_checks(run: Run, label: str, phase: str) -> list[tuple[str, bool, str]]:
    """Snapshot now and compare with the baseline: one FAIL line per difference, notes as PASS (info) lines."""
    base_p = run.ev / "snapshots/baseline.json"
    if not base_p.is_file():
        return [("isolation", False, "isolation not checked: no baseline snapshot (launch stopped before it)")]
    fails, notes = compare(json.loads(base_p.read_text()), write_snapshot(run, label), phase)
    out = [("user config, plugin rows, registry, Slack default state, lead tree == baseline", not fails,
            "unchanged" if not fails else f"{len(fails)} difference(s), listed below")]
    out += [("isolation", False, f) for f in fails]
    out += [("isolation (info)", True, n) for n in notes]
    return out


def cmd_wait(a) -> int:
    run = Run.load(a.run)
    return wait_loop(run, run.resolve_agent(a.agent), a.timeout, ready=a.ready, expect=a.expect, fail=a.fail,
                     poll=a.poll, settle=a.settle)


def log_key(run: Run, agent: str, pane: str, what: str) -> None:
    """sends.log: one line per typed text and per key smv sends, with time, agent and pane."""
    with (run.ev / "sends.log").open("a") as f:
        f.write(f"{now_iso()}\tuid:{agent}\t{pane}\t{what}\n")


def box_check(run: Run, agent: str, pane: str, typed: str) -> tuple[str | None, str]:
    """Why an Enter must not be pressed now, or None: re-capture, the screen is ready, projmux reports no
    approval / input request, and the active input box holds exactly the typed text."""
    text = run.capture(pane)
    st, s = screen_state(text), agent_status(agent)
    if s and s["kind"] in ("approval_required", "input_required", "in_progress"):
        return f"projmux status {s['kind']}", text
    if st != "ready":
        return f"screen is {st}, not ready", text
    if not box_holds(input_box(lines_of(text)), typed):
        return "the input box does not hold exactly the typed text", text
    return None, text


def cmd_send(a) -> int:
    """Type one line and press Enter, verifying the screen before every Enter. A second Enter is pressed only
    when the first left the exact line in the box, never on a /plugin line, and only after the same check."""
    run = Run.load(a.run)
    agent = run.resolve_agent(a.agent)
    if any(c in a.text for c in "\n\r") or a.text.startswith("-") or a.text.endswith(";") or not a.text.strip():
        die("--text must be one non-empty line that neither starts with '-' nor ends with ';' (tmux separator)")
    pane = run.pane(agent, wait=30)
    text = run.capture(pane)
    st, s = screen_state(text), agent_status(agent)
    if st in STOP_CODES:
        return stop_message(run, agent, st, text)
    if s and s["kind"] == "approval_required":
        return stop_message(run, agent, "permission", text)
    if s and s["kind"] == "input_required":
        return stop_message(run, agent, "dialog", text)
    if st != "ready" or (s and s["kind"] == "in_progress") or not box_is_empty(input_box(lines_of(text))):
        print(f"NOT READY (3): screen={st} status={s['raw'] if s else 'n/a'} - wait first; "
              f"capture: {run.save_pane('send-refused', text)}")
        return 3
    record_answer(run, agent, text)  # a ready, empty box after an open stop: the stop went away
    before = run.save_pane(f"before-{a.text[:30]}", text, "state")
    pre = {"pre_sha": screen_digest(text), "pre_lines": line_digests(run, text)}
    sent_at = now_iso()

    def not_sent(why: str, screen: str, enters: int) -> int:
        path = run.save_pane("send-not-sent", screen)
        run.note(f"send to uid:{agent} stopped before Enter #{enters + 1}: {why} ({path})")
        print(f"NOT SENT (3): {why}; no Enter pressed now ({enters} pressed). The typed text may still be in the "
              f"input box: ask the operator to clear it, then send again. capture: {path}")
        return 3

    log_key(run, agent, pane, f"TYPE {a.text}")
    run.keys(pane, a.text, literal=True)
    time.sleep(0.8)
    why, screen = box_check(run, agent, pane, a.text)
    if why:
        return not_sent(why, screen, 0)
    log_key(run, agent, pane, "KEY Enter")
    run.keys(pane, "Enter")
    time.sleep(2.0)
    screen = run.capture(pane)
    second = box_holds(input_box(lines_of(screen)), a.text)
    if second:  # the line is still in the box: an autocomplete menu took the first Enter, or the TUI is slow
        if a.text.startswith("/plugin"):
            return not_sent("the line is still in the box after Enter; a second Enter is never pressed on a "
                            "/plugin line (it could pick a menu default)", screen, 1)
        why, screen = box_check(run, agent, pane, a.text)
        if why:
            return not_sent(why, screen, 1)
        log_key(run, agent, pane, "KEY Enter (second: the first left the line in the box)")
        run.keys(pane, "Enter")
    rec = {"at": sent_at, "pre": s, "capture": str(before), "text": a.text, "second_enter": second, **pre}
    run.update(lambda d: (d["sends"].__setitem__(agent, rec), d.__setitem__("last_send", sent_at)))
    print(f"SENT uid:{agent} tmux={pane} at={sent_at}{' (second Enter)' if second else ''}")
    return 0


def cmd_capture(a) -> int:
    """Save the pane into evidence/panes/ by its tmux id: works on a dead pane or an Offline agent too."""
    run = Run.load(a.run)
    agent = run.resolve_agent(a.agent)
    path, text, rep = capture_any(run, agent, a.label)
    if not path:
        print(f"capture not possible: {format_report(rep)}")
        return 1
    print(f"{path}  screen={screen_state(text)}  pane_dead={rep['pane_dead']} exit_status={rep['exit_status']}")
    return 0


def cmd_status(a) -> int:
    """projmux interaction status, the pane report (phase, reason, pane, pane_dead, exit status), the screen."""
    run = Run.load(a.run)
    agent = run.resolve_agent(a.agent)
    s = agent_status(agent)
    path, text, rep = capture_any(run, agent, "status")
    print(f"uid:{agent} status={s['raw'] if s else 'n/a'} phase={rep['phase']} reason={rep['reason']!r} "
          f"pane uid:{rep['pane_uid']} tmux={rep['tmux']} pane_dead={rep['pane_dead']} "
          f"exit_status={rep['exit_status']} screen={screen_state(text) if path else 'not capturable'}")
    if path:
        print(f"capture: {path}")
    return 0


READ_ONLY = {"ls", "cat", "head", "tail", "wc", "grep", "cmp", "diff", "test", "stat", "file", "which",
             "sha256sum", "find", "git"}
GIT_READ_ONLY = {"status", "check-ignore", "diff", "log", "show", "ls-files", "rev-parse"}
FIND_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}


def cmd_refusal(argv: list[str], cwd: Path) -> str | None:
    """Why `smv cmd` refuses argv, or None. Only read-only programs run (no shell, no interpreter), and no
    argument may name user config: every argument is resolved against the command's cwd first."""
    prog = os.path.basename(argv[0])
    if prog not in READ_ONLY or "/" in argv[0]:
        return f"{argv[0]} is not one of the read-only programs {sorted(READ_ONLY)} (no shell, no interpreter)"
    if prog == "git" and (len(argv) < 2 or argv[1] not in GIT_READ_ONLY):
        return f"git {argv[1] if len(argv) > 1 else ''}: only {sorted(GIT_READ_ONLY)}"
    if prog == "find" and FIND_ACTIONS & set(argv):
        return f"find with {sorted(FIND_ACTIONS & set(argv))}"
    homes = [claude_config_dir().resolve(), (Path.home() / ".claude").resolve()]
    for x in argv[1:]:
        if "$" in x or x.startswith("~") or "`" in x:
            return f"argument {x!r}: shell expansion is not done by smv cmd and is refused"
        for part in (x, x.split("=", 1)[-1]):
            target = (cwd / part).resolve() if not os.path.isabs(part) else Path(part).resolve()
            if any(target == h or within(target, h) for h in homes) or ".claude/plugins" in part:
                return f"argument {x!r} names user config (never read into evidence; doctor compares it by digest)"
    return None


def cmd_cmd(a) -> int:
    run = Run.load(a.run)
    argv = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
    if not argv:
        die("usage: smv cmd --label L -- <command...>")
    cwd = {"proj": run.proj, "repo": run.repo, "evidence": run.ev}[a.cwd]
    why = cmd_refusal(argv, cwd)
    if why:
        die(f"smv cmd refused: {why}")
    return run.cmd(a.label, argv, cwd=cwd, timeout=a.timeout)[0]


def cmd_note(a) -> int:
    Run.load(a.run).note(a.text)
    return 0


def cmd_hash(a) -> int:
    run = Run.load(a.run)
    tree = hash_tree(run, a.exclude)
    out = run.ev / "hashes" / f"{slug(a.label)}.sha256"
    out.write_text("".join(f"{h}  {rel}\n" for rel, h in tree.items()))
    print(f"{out}  files={len(tree)}")
    return 0


def allow_refusal(run: Run, spec: str) -> str | None:
    """Why an --allow is refused, or None. Only a relative path inside the hashed scratch tree can be allowed:
    never a user config file, an absolute or '..' path, .git/, or anything that resolves outside the scratch
    project (the lead tree included)."""
    kind, _, rel = spec.strip().partition(" ")
    rel = rel.strip()
    if kind not in ALLOW_KINDS or not rel:
        return f"want '<{'|'.join(ALLOW_KINDS)}> <relative path>'"
    parts = Path(rel).parts
    if rel.startswith("~"):
        return "a home-relative path (user config is never excusable)"
    if "$" in rel:
        return "an unexpanded shell variable"
    if os.path.isabs(rel):
        if within(Path(rel), claude_config_dir()) or within(Path(rel), Path.home() / ".claude"):
            return "a user config file (never excusable)"
        if within(Path(rel), run.repo):
            return "a lead-tree path (never excusable)"
        return "an absolute path"
    if "\\" in rel:
        return "a backslash path"
    if ".." in parts:
        return "a '..' path"
    if parts[0] == ".git":
        return ".git/ is not hashed"
    if parts[:2] == (".claude", "plugins"):
        return "the user plugin registry layout (.claude/plugins/) is never a scratch file"
    if not within((run.proj / rel).resolve(), run.proj):  # a symlink out of the scratch project
        return "resolves outside the scratch project"
    return None  # launch keeps the scratch project outside the checkout and the user config dir


def allow_key(spec: str) -> str:
    """'KIND path' with the path normalised the way hash lists spell it (no ./, no //, no trailing /)."""
    kind, _, rel = spec.strip().partition(" ")
    return f"{kind} {posixpath.normpath(rel.strip())}"


def cmd_hash_diff(a) -> int:
    """Compare two hash lists. Each --allow names one change that must happen; nothing else may change."""
    run = Run.load(a.run)
    sid = run.next_id(f"hash-diff-{a.before}-{a.after}")
    step = run.ev / "steps" / f"{sid}.txt"
    head = f"$ smv hash-diff {a.before} {a.after}" + "".join(f" --allow '{x}'" for x in a.allow) + "\n"
    refused = [(x, why) for x in a.allow if (why := allow_refusal(run, x))]
    record = {"at": now_iso(), "step": sid, "before": a.before, "after": a.after, "allow": a.allow}
    if refused:
        body = "".join(f"REFUSED --allow '{x}': {why}\n" for x, why in refused)
        step.write_text(head + body + "nothing excused\n# exit: 2\n")
        run.update(lambda d: d.setdefault("allowances", []).append({**record, "verdict": "REFUSED",
                                                                     "refused": refused}))
        print(body + "hash-diff: usage error, nothing excused", file=sys.stderr)
        return 2
    added, missing, changed = hash_diff(read_hashes(run, a.before), read_hashes(run, a.after))
    rows = [f"{tag} {r}" for tag, got in (("ADDED", added), ("MISSING", missing), ("CHANGED", changed)) for r in got]
    allowed = {allow_key(x) for x in a.allow}
    out = [f"{r}   (allowed)" if r in allowed else r for r in rows]
    unmet = sorted(allowed - set(rows))
    out += [f"UNMET {x}   (allowed change did not happen)" for x in unmet]
    bad = [r for r in rows if r not in allowed]
    verdict = "DIFFERENT" if bad or unmet else ("IDENTICAL" if not rows else "ONLY ALLOWED CHANGES")
    rc = 0 if verdict != "DIFFERENT" else 1
    text = "".join(f"{x}\n" for x in out) + f"hash-diff {a.before} -> {a.after}: {verdict}\n"
    step.write_text(head + text + f"# exit: {rc}\n")
    if a.allow:  # every allowance a run used is kept in run.json as well as in the step file
        run.update(lambda d: d.setdefault("allowances", []).append({**record, "verdict": verdict}))
    print(text, end="")
    return rc


def cmd_tgrep(a) -> int:
    run = Run.load(a.run)
    agent = run.resolve_agent(a.agent)
    rx = re.compile(a.pattern, re.M)
    deadline = time.time() + a.wait
    while True:
        path = transcript_source(run, agent)
        hits = []
        if path:
            for ts, s in transcript_texts(path, since_mark(run, a.since_last_send), a.assistant_only,
                                          a.tool_inputs):
                hits += [match_lines(ts, s, m, a.assistant_only) for m in rx.finditer(s)]
        if a.absent or len(hits) >= a.min or time.time() >= deadline:
            break
        time.sleep(5)
    if not path:
        print(f"tgrep uid:{agent}: no transcript found")
        return 1  # a missing transcript never proves absence
    need = "need 0 (--absent)" if a.absent else f"need >= {a.min}"
    ok = not hits if a.absent else len(hits) >= a.min
    scope = " tool-inputs" if a.tool_inputs else (" assistant-only" if a.assistant_only else "")
    kept, dropped = record_transcript(run, agent, f"tgrep{scope} /{a.pattern}/", [x for h in hits for x in h])
    text = "".join(f"{x}\n" for x in kept) + (
        f"tgrep uid:{agent}{scope} /{a.pattern}/ hits={len(hits)} ({need}) transcript={home_rel(path)} "
        f"extract=transcripts/{agent}.extract.txt lines-dropped-by-redaction={dropped}\n")
    (run.ev / "steps" / f"{run.next_id('tgrep')}.txt").write_text(text + f"# exit: {0 if ok else 1}\n")
    print(text, end="")
    return 0 if ok else 1


def cmd_msg_status(a) -> int:
    run = Run.load(a.run)
    agent = run.resolve_agent(a.agent)
    path = transcript_source(run, agent)
    found = transcript_texts(path, since_mark(run, True)) if path else []
    refs = sorted({m.group(1) for _, s in found for m in RECEIPT_RE.finditer(s)})
    record_transcript(run, agent, "msg-status receipts", [f"receipt: {r} delivered" for r in refs])
    checks = [("receipt 'message-... delivered' since the last send", bool(refs), ", ".join(refs) or "none")]
    for ref in refs:
        code, out, err = run.cmd(f"message-status-{ref}", ["projmux", "agent", "message", "status", ref, "-o", "json"])
        checks.append((f"projmux agent message status {ref}", code == 0 and "delivered" in out, f"exit {code}"))
    return print_checks("msg-status", checks, run)


def cmd_init_check(a) -> int:
    """The proof is the scaffold.py apply run itself: a Bash tool_use without --dry-run whose --target is the
    scratch project, and the summary and CREATE lines of the tool result with that tool_use_id. A dry-run preview
    or the agent's echo of a summary is info only, never proof."""
    run = Run.load(a.run)
    path = transcript_source(run, run.data["pm"])
    if not path:
        die("PM transcript not found", 1)
    rows = proof_lines(path, since_mark(run, True), run.proj)  # the same whitelisted rows the extract keeps
    record_transcript(run, run.data["pm"], f"init-check {a.mode}")
    calls = {r["id"]: r["scaffold"] for r in rows if r["kind"] == "tool_use" and r.get("scaffold")}
    results: dict[str, list[dict]] = {}
    for r in rows:
        if r["kind"] == "scaffold result":
            results.setdefault(r["id"], []).append(r)

    def summary(uid: str) -> tuple[int, int, int] | None:
        sums = [tuple(int(x) for x in m.groups()) for r in results.get(uid, []) if not r["error"]
                for m in SUMMARY_RE.finditer(r["text"])]
        return sums[-1] if sums else None

    applies = [i for i, c in calls.items() if c["mode"] == "apply" and c["target_ok"]]
    applied = [(i, summary(i)) for i in applies if summary(i)]
    previews = [i for i, c in calls.items() if c["mode"] == "dry-run"]
    others = [i for i, c in calls.items() if c["mode"] == "mixed" or not c["target_ok"]]
    echoes = [r["text"] for r in rows if r["kind"] == "assistant summary"]
    added, removed, changed = hash_diff(read_hashes(run, a.before), read_hashes(run, a.after))
    checks = [("scaffold.py runs since the last send (info)", True,
               f"apply on the scratch project {len(applies)} (with a result summary {len(applied)}), dry-run "
               f"{len(previews)}, other target or several runs in one command {len(others)}; echoed summary lines "
               f"{len(echoes)} (info only, never proof)"),
              ("an apply run (no --dry-run, --target = scratch project) with its own result summary", bool(applied),
               ", ".join(f"{i}: created={c} merged={m} skipped={k}" for i, (c, m, k) in applied) or "none"),
              ("scaffold.py the agent ran (info)", True, scaffold_paths(run, path))]
    if a.mode == "fresh":
        first = applied[0] if applied else None
        creates = {m.group(2) for r in results.get(first[0], []) if (m := ACTION_RE.match(r["text"]))
                   and m.group(1) == "CREATE"} if first else set()
        c, mg, k = first[1] if first else (0, 0, 0)
        checks += [("first apply: created>0 merged=0 skipped=0", bool(first) and c > 0 and mg == 0 and k == 0,
                    str(first[1]) if first else "-"),
                   ("first apply's created count == files added on disk", bool(first) and c == len(added),
                    f"{c if first else '-'} vs {len(added)}"),
                   ("first apply's CREATE list == files added on disk", bool(first) and creates == set(added),
                    f"only-in-output={sorted(creates - set(added))} only-on-disk={sorted(set(added) - creates)}"),
                   ("no pre-existing file changed or removed", not (removed or changed), f"{removed + changed}")]
    else:
        checks += [("every apply: created=0 merged=0", bool(applied) and all(c == 0 and mg == 0
                                                                            for _, (c, mg, _) in applied),
                    str([x for _, x in applied])),
                   ("every file's sha256 unchanged", not (added or removed or changed),
                    f"added={added} removed={removed} changed={changed}")]
    return print_checks(f"init-check {a.mode}", checks, run)


def scaffold_paths(run: Run, transcript: Path) -> str:
    """Which scaffold.py the PM's tool calls ran since the last send: this checkout, the installed copy, or other.
    With a directory marketplace the skill may run the checkout's file; plugin-check proves byte identity only."""
    code, out, _ = sh(["claude", "plugin", "list", "--json"], cwd=run.proj)
    rows = jload(out, []) if code == 0 else []
    install = next((r.get("installPath") for r in (rows if isinstance(rows, list) else [])
                    if r.get("id") == PLUGIN_ID and r.get("scope") == "project"
                    and same_path(r.get("projectPath"), run.proj)), None)
    seen: dict[str, int] = {}
    for _, inp in transcript_texts(transcript, since_mark(run, True), tool_inputs=True):
        for raw in SCAFFOLD_RE.findall(inp):
            p = Path(os.path.expandvars(raw.replace("${CLAUDE_PLUGIN_ROOT}", "$CLAUDE_PLUGIN_ROOT")))
            where = ("checkout" if p.is_absolute() and within(p, run.repo) else
                     "installed copy" if install and p.is_absolute() and within(p, Path(install)) else
                     "plugin root variable" if "CLAUDE_PLUGIN_ROOT" in raw else f"other ({home_rel(raw)})")
            if install and same_path(install, run.repo) and where == "checkout":
                where = "checkout (the install path is the checkout itself)"
            seen[where] = seen.get(where, 0) + 1
    run.update(lambda d: d.__setitem__("scaffold_paths", seen))
    return ", ".join(f"{k}: skills/init/scaffold.py x{v}" for k, v in seen.items()) or "no scaffold.py call seen"


def cmd_agents(a) -> int:
    run = Run.load(a.run)
    run.record_agents()
    rows = [{"uid": i["metadata"]["uid"], "name": i["metadata"].get("name"),
             "labels": i["metadata"].get("labels") or {}, "phase": (i.get("status") or {}).get("phase"),
             "cwd": ((i.get("spec") or {}).get("workspace") or {}).get("cwd"),
             "window": (i["metadata"].get("ownerRef") or {}).get("uid")} for i in run.project_agents()]
    (run.ev / "steps" / f"{run.next_id('agents-' + a.label)}.json").write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(f"uid:{r['uid']}  name={r['name']}  phase={r['phase']}  window={r['window']}  labels={r['labels']}")
    print(f"agents in this run's project: {len(rows)}")
    if a.expect_count is not None and len(rows) != a.expect_count:
        print(f"FAIL: expected {a.expect_count}")
        return 1
    return 0


def cmd_spawn_check(a) -> int:
    run = Run.load(a.run)
    run.record_agents()
    rows = live_role_agents(run, a.role)
    pm_window = (run.agent_json(run.data["pm"]).get("metadata") or {}).get("ownerRef", {}).get("uid")
    checks = [(f"exactly one live role={a.role} agent with cwd == scratch project", len(rows) == 1, f"{len(rows)}")]
    if len(rows) == 1:
        r = rows[0]
        labels = r["metadata"].get("labels") or {}
        checks += [("recorded by this run (creator rule)", r["metadata"]["uid"] in run.data["created"]["agents"],
                    f"creator uid:{creator_of(r)}"),
                   ("label mesh=supervisor-mesh", labels.get("mesh") == "supervisor-mesh", str(labels)),
                   (f"name role-{a.role}", r["metadata"].get("name") == f"role-{a.role}", r["metadata"].get("name")),
                   ("window (info: same as PM unless spawn-roles chose a shared window)", True,
                    f"{(r['metadata'].get('ownerRef') or {}).get('uid')} vs PM {pm_window}")]
        uid, rx = r["metadata"]["uid"], re.compile(rf"ROLE LOADED role-lead-{re.escape(a.role)}\b")
        deadline, hit = time.time() + a.wait, None
        while not hit:
            path = transcript_source(run, uid)
            hit = next(((ts, s, m) for ts, s in transcript_texts(path, None, True) if (m := rx.search(s))),
                       None) if path else None
            if hit or time.time() >= deadline:
                break
            time.sleep(5)
        record_transcript(run, uid, f"spawn-check {a.role}", match_lines(*hit, True) if hit else [])
        checks.append((f"transcript has ROLE LOADED role-lead-{a.role}", bool(hit),
                       f"{hit[0] if hit else None} uid:{uid} (extract: transcripts/{uid}.extract.txt)"))
    return print_checks(f"spawn-check {a.role}", checks, run)


def cmd_invariant(a) -> int:
    run = Run.load(a.run)
    target = run.proj / a.file
    if not within(target, run.proj) or not target.is_file():
        return print_checks("invariant", [(f"{a.file} is a file inside the scratch project", False, "missing")], run)

    def block(text: str) -> str | None:
        """BEGIN marker through END marker, or None when either marker is missing or out of order."""
        start = text.find(INVARIANT[0])
        end = text.find(INVARIANT[1], start + 1) if start >= 0 else -1
        return text[start:end + len(INVARIANT[1])] if end >= 0 else None

    got, want = target.read_text(), (run.repo / "templates/agents/role-lead.md").read_text()
    gb, wb = block(got), block(want)
    name = re.search(r"^name:\s*(\S+)", got, re.M)
    detail = ("template has no INVARIANT BEGIN/END block" if wb is None else
              "file has no INVARIANT BEGIN marker, or no END marker after it" if gb is None else
              f"file {hashlib.sha256(gb.encode()).hexdigest()[:16]} vs template "
              f"{hashlib.sha256(wb.encode()).hexdigest()[:16]}")
    checks = [("INVARIANT block byte-identical to templates/agents/role-lead.md",
               gb is not None and wb is not None and gb == wb, detail),
              ("frontmatter name == role-lead-<role>", bool(name) and name.group(1) == f"role-lead-{a.role}",
               name.group(1) if name else "missing")]
    if a.blank_part_b:  # the operator gave no answers, so the Role scope section must still be the template's
        def scope(text: str) -> str:
            return text[text.find("## Role scope"):].rstrip() if "## Role scope" in text else ""

        checks.append(("Role scope (Part B) left as the template's blanks", bool(scope(want)) and
                       scope(got) == scope(want), f"{scope(got).count('<')} placeholders"))
    return print_checks("invariant", checks, run)


def cmd_seed(a) -> int:
    run = Run.load(a.run)
    target = run.proj / a.file
    if not within(target, run.proj) or target.exists():
        die(f"{a.file}: must be a new path inside the scratch project")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(f"{x}\n" for x in a.line) if a.line else a.text + "\n")
    run.note(f"seeded fixture {a.file} sha256={sha256_file(target)}")
    print(f"seeded {target}")
    return 0


def cmd_mkt_project(a) -> int:
    """Declare the marketplace in the scratch project with the CLI. The slash '/plugin marketplace add' is never
    used: it writes the declaration to user settings. Stops (21) when another 'supervisor-mesh' is registered."""
    run = Run.load(a.run)
    code, out, err = sh(["claude", "plugin", "marketplace", "list", "--json"], cwd=run.ev)
    if code != 0 or not isinstance(jload(out), list):
        die(f"claude plugin marketplace list --json failed (exit {code}): {err.strip()[:200]}", 1)
    known = next((m for m in jload(out) if m.get("name") == MARKETPLACE), None)
    settings_file = claude_config_dir() / USER_SETTINGS  # read in memory only: never copied, no value printed
    decl = ((jload(settings_file.read_text(), {}) if settings_file.is_file() else {})
            .get("extraKnownMarketplaces") or {}).get(MARKETPLACE)
    src = (decl or {}).get("source") or {}
    same_known = bool(known) and known.get("source") == "directory" and same_path(known.get("path"), run.repo)
    same_decl = src.get("source") == "directory" and same_path(src.get("path"), run.repo)
    print(f"registry entry '{MARKETPLACE}': {'none' if not known else ('this checkout' if same_known else 'OTHER')}")
    print(f"user settings declaration: {'none' if not decl else ('this checkout' if same_decl else 'OTHER')}")
    if (known and not same_known) or (decl and not same_decl):
        print(f"STOP (21): a '{MARKETPLACE}' marketplace already points elsewhere; declaring this checkout would "
              "shadow it. Ask the operator. smv changes nothing.")
        return 21
    def claim(d: dict) -> bool:
        if d.get("marketplace") is not None:
            return False
        d["marketplace"] = {"mode": "project-cli", "declared": False, "registry_preexisting": bool(known),
                            "at": now_iso()}
        return True

    first = run.update(claim)
    pre = run.data["marketplace"]["registry_preexisting"]  # recorded once, before the first add; a retry keeps it
    before = config_file_digest(settings_file, None)["sha256"]
    code, _, _ = run.cmd("marketplace-add-project", ["claude", "plugin", "marketplace", "add", str(run.repo),
                                                     "--scope", "project", "--json"], cwd=run.proj, timeout=300)
    settings = run.proj / ".claude/settings.json"
    scratch_decl = (jload(settings.read_text(), {}) if settings.is_file() else {}).get("extraKnownMarketplaces") or {}
    user_same = config_file_digest(settings_file, None)["sha256"] == before
    lc, lo, _ = sh(["claude", "plugin", "marketplace", "list", "--json"], cwd=run.proj)
    entry = next((m for m in (jload(lo, []) if lc == 0 else []) or [] if m.get("name") == MARKETPLACE), None)
    checks = [("claude plugin marketplace add <checkout> --scope project", code == 0, f"exit {code}"),
              ("scratch .claude/settings.json declares the marketplace", MARKETPLACE in scratch_decl, MARKETPLACE),
              (f"registry entry '{MARKETPLACE}' is this checkout (directory source)", bool(entry) and
               entry.get("source") == "directory" and same_path(entry.get("path"), run.repo),
               "this checkout" if entry else "no entry"),
              (f"user {USER_SETTINGS} sha256 unchanged by the add", user_same, "whole-file sha256")]
    ok = all(c[1] for c in checks)
    run.update(lambda d: d["marketplace"].update(declared=ok, at=now_iso()))
    run.note(f"marketplace declared at project scope with the CLI ({'first' if first else 'repeated'} add; registry "
             f"entry before this run's first add: {pre}); the slash /plugin marketplace add is not exercised")
    return print_checks("mkt-project", checks, run)


def cmd_scope_pick(a) -> int:
    """Drive the /plugin install scope menu like a user: find the exact project-scope option, move the highlight
    onto it with arrow keys, re-capture and confirm the highlighted line is that exact text, then press Enter.
    Anything unexpected stops with 12 and nothing is pressed; Enter is never pressed on an unverified line."""
    run = Run.load(a.run)
    agent = run.resolve_agent(a.agent)
    if (run.data["sends"].get(agent) or {}).get("text") != INSTALL_LINE:
        die(f"scope-pick runs only right after: smv send --text '{INSTALL_LINE}'")
    pane = run.pane(agent, wait=30)
    pressed: list[str] = []

    def stop(reason: str, text: str) -> int:
        path = run.save_pane("scope-pick-stop", text, "menu")
        rec = {"at": now_iso(), "result": "STOP", "reason": reason, "keys": list(pressed)}
        run.update(lambda d: d.__setitem__("scope_pick", rec))
        print(f"STOP (12): scope-pick could not verify the menu: {reason}. Nothing more was pressed. Ask the "
              f"operator to pick '{SCOPE_OPTION}' in the pane; after they confirm, resume.\n"
              f"{resume_line(run, agent, 'scope-pick')}\ncapture: {path}")
        mark_stop(run, agent, "scope-pick", 12, path)
        return 12

    def press(key: str) -> None:
        log_key(run, agent, pane, f"KEY {key}")
        run.keys(pane, key)
        pressed.append(key)

    if ((run.data.get("pending_stops") or {}).get(agent) or {}).get("kind") == "scope-menu":
        # smv drives this menu itself: not an operator-side answer
        run.update(lambda d: (d.get("pending_stops") or {}).pop(agent, None))
    text = run.capture(pane)
    run.save_pane("scope-pick-start", text, "menu")
    for move in range(a.max_moves + 1):
        st = screen_state(text)
        if st in ("trust", "permission"):
            return stop_message(run, agent, st, text)
        if st != "scope-menu":
            return stop(f"screen is '{st}', not the scope menu", text)
        targets = [i for i, _, t in menu_options(text) if t == SCOPE_OPTION]
        hl = highlighted(text)
        if len(targets) != 1:
            return stop(f"the exact option text appears {len(targets)} times", text)
        if hl is None:
            return stop("not exactly one highlighted line", text)
        if hl[0] == targets[0]:
            break
        if move == a.max_moves:
            return stop(f"highlight not on the option after {a.max_moves} moves", text)
        key = "Down" if targets[0] > hl[0] else "Up"
        press(key)
        time.sleep(0.6)
        text = run.capture(pane)
        run.save_pane(f"scope-pick-{key}-{move + 1}", text, "menu")
        now = highlighted(text)
        if now is None or now[0] == hl[0] or (now[0] > hl[0]) != (key == "Down"):
            return stop(f"{key} did not move the highlight one way (before line {hl[0]}, after "
                        f"{now[0] if now else 'none'})", text)
    final = run.capture(pane)  # re-capture: the highlighted line itself must be the exact option text
    path = run.save_pane("scope-pick-verified", final, "menu")
    hl = highlighted(final)
    if screen_state(final) != "scope-menu" or hl is None or hl[1] != SCOPE_OPTION:
        return stop(f"re-capture shows highlighted line {hl[1] if hl else None!r}, not the option", final)
    print(f"VERIFIED highlighted line == {SCOPE_OPTION!r}; capture: {path}")
    press("Enter")
    run.note(f"scope menu answered by smv scope-pick (keys {pressed}) on the verified line {SCOPE_OPTION!r} "
             f"({path}); not an operator-side answer")
    rec = {"at": now_iso(), "result": "ENTER", "keys": list(pressed), "verified_capture": str(path)}
    run.update(lambda d: d.__setitem__("scope_pick", rec))
    deadline = time.time() + a.timeout
    while True:
        time.sleep(2)
        text = run.capture(pane)
        st = screen_state(text)
        if st == "ready":
            print(f"scope-pick: PASS (keys {pressed}); capture: {run.save_pane('scope-pick-done', text, 'state')}\n"
                  "next: smv plugin-check")
            return 0
        if st in ("trust", "permission"):
            return stop_message(run, agent, st, text)
        if time.time() >= deadline:
            path = run.save_pane("scope-pick-after-enter", text, "menu")
            print(f"STOP (13): after Enter the plugin view is still open (screen={st}). smv presses nothing more: "
                  f"ask the operator to close it, then resume.\n{resume_line(run, agent, 'scope-pick')}\n"
                  f"capture: {path}")
            mark_stop(run, agent, "scope-pick", 13, path)
            return 13


def cmd_plugin_check(a) -> int:
    run = Run.load(a.run)
    return print_checks("plugin-check", plugin_checks(run), run)


# ---------------------------------------------------------------- FakeSlack (tests/test_slack.py, read-only)

def slack_base(run: Run) -> str:
    port = (run.data["pids"].get("fake-slack") or {}).get("port")
    if not port:
        die("FakeSlack is not running for this run (smv fake-slack start)")
    return f"http://127.0.0.1:{port}"


def ctl(run: Run, path: str, **q) -> dict:
    with urllib.request.urlopen(f"{slack_base(run)}/_ctl/{path}?{urllib.parse.urlencode(q)}", timeout=10) as r:
        return json.load(r)


def serve_fake_slack(run: Run) -> None:
    from http.server import HTTPServer
    sys.path.insert(0, str(run.repo / "tests"))
    import test_slack  # read-only import: FakeSlack and make_handler; never runs the test suite

    fake = test_slack.FakeSlack()
    base = test_slack.make_handler(fake)
    count = {"api": 0}
    requests = (run.ev / "slack/requests.jsonl").open("a", buffering=1)

    def redact(text):
        return text.replace(FAKE_TOKEN, "<fake-token>") if isinstance(text, str) else text

    def summary() -> dict:
        return {"api_requests": count["api"], "auth_headers": len(fake.auth),
                "auth_all_expected": all(h == f"Bearer {FAKE_TOKEN}" for h in fake.auth),
                "posts": [{"text": redact(p.get("text")), "thread_ts": p.get("thread_ts")} for p in fake.posts],
                "posts_with_token": sum(FAKE_TOKEN in json.dumps(p) for p in fake.posts),
                "messages": len(fake.messages)}

    class Handler(base):
        def log_request_row(self, verb: str) -> None:
            """One line per Slack API call: whether a Bearer header came and whether it was the fake token,
            never the header itself; a posted text is kept with the token redacted."""
            auth = self.headers.get("Authorization") or ""
            row = {"at": now_iso(), "method": urllib.parse.urlparse(self.path).path.strip("/"), "http": verb,
                   "bearer_present": auth.startswith("Bearer "), "bearer_is_fake_token": auth == f"Bearer {FAKE_TOKEN}"}
            if row["method"] == "chat.postMessage" and fake.posts:
                row["text"] = redact(fake.posts[-1].get("text"))
            requests.write(json.dumps(row) + "\n")

        def do_GET(self):  # noqa: N802
            if not self.path.startswith("/_ctl/"):
                count["api"] += 1
                super().do_GET()
                self.log_request_row("GET")
                return
            url = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(url.query))
            if url.path == "/_ctl/say":
                body = {"ts": fake.say(q["text"], user=q.get("user", FAKE_USER), channel=FAKE_CHANNEL)}
            elif url.path == "/_ctl/summary":
                body = summary()
            else:
                self.send_error(404)
                return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802
            count["api"] += 1
            super().do_POST()
            self.log_request_row("POST")

    def stop(*_):
        raise KeyboardInterrupt

    server = HTTPServer(("127.0.0.1", 0), Handler)
    (run.ev / "slack/port").write_text(str(server.server_port))
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        (run.ev / "slack/fake-slack-final.json").write_text(json.dumps(summary(), indent=1))
        server.server_close()


def proc_cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        code, out, _ = sh(["ps", "-o", "args=", "-p", str(pid)])
        return out.strip() if code == 0 else ""


def stop_fake_slack(run: Run, dry: bool = False) -> str:
    """SIGTERM to the one pid this run recorded for FakeSlack, after its command line names this run."""
    rec = run.data["pids"].get("fake-slack") or {}
    pid = rec.get("pid")
    if not pid or rec.get("stopped"):
        return "not running"
    cmdline = proc_cmdline(pid)
    if "fake-slack" not in cmdline or str(run.root) not in cmdline:
        if not dry:
            run.update(lambda d: d["pids"]["fake-slack"].__setitem__("stopped", now_iso()))
        return f"pid {pid} is gone or is not this run's FakeSlack; nothing killed"
    if dry:
        return f"would stop pid {pid} (command line names this run)"
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not proc_cmdline(pid):
            break
        time.sleep(0.25)
    run.update(lambda d: d["pids"]["fake-slack"].__setitem__("stopped", now_iso()))
    return f"stopped pid {pid}"


def cmd_fake_slack(a) -> int:
    run = Run.load(a.run)
    if a.action == "serve":
        serve_fake_slack(run)
        return 0
    if a.action == "start":
        rec = run.data["pids"].get("fake-slack") or {}
        if rec.get("pid") and not rec.get("stopped"):
            die("FakeSlack already started for this run")
        env_file = run.proj / ".env"
        if env_file.exists():
            die("scratch .env already exists; refusing to overwrite it")
        port_file = run.ev / "slack/port"
        port_file.unlink(missing_ok=True)
        log = (run.ev / "slack/fake-slack.log").open("a")
        proc = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "fake-slack", "serve",
                                 "--run", str(run.root)], stdout=log, stderr=subprocess.STDOUT, cwd=run.ev,
                                start_new_session=True)
        run.update(lambda d: d["pids"].__setitem__("fake-slack", {"pid": proc.pid, "started": now_iso()}))
        for _ in range(80):
            if port_file.is_file() and port_file.read_text().strip():
                break
            time.sleep(0.25)
        else:
            die(f"FakeSlack did not report a port within 20 s (see {run.ev / 'slack/fake-slack.log'}); its pid "
                f"{proc.pid} is recorded, so smv fake-slack stop or smv cleanup stops it", 1)
        port = int(port_file.read_text())
        run.update(lambda d: d["pids"]["fake-slack"].__setitem__("port", port))
        lines = [f"SLACK_BOT_TOKEN={FAKE_TOKEN}", f"SLACK_CHANNEL_ID={FAKE_CHANNEL}",
                 f"SLACK_ALLOWED_USERS={FAKE_USER}", f"SLACK_API_BASE=http://127.0.0.1:{port}",
                 f"SLACK_STATE_DIR={run.proj / '.slack-state'}", "SLACK_POLL_SECONDS=2",
                 "SLACK_THREAD_SCAN_SECONDS=0"]
        env_file.write_text("\n".join(lines) + "\n")
        run.note(f"wrote scratch .env with FakeSlack values (keys only: {[x.split('=')[0] for x in lines]})")
        print(f"FAKE_SLACK pid={proc.pid} base=http://127.0.0.1:{port} env={env_file} (fake token, local only)")
        return 0
    if a.action == "say":
        print(json.dumps(ctl(run, "say", text=a.text, user=a.user)))
        return 0
    if a.action == "summary":
        body = ctl(run, "summary")
        (run.ev / "slack" / f"{run.next_id('fake-slack-summary')}.json").write_text(json.dumps(body, indent=1))
        print(json.dumps(body, indent=1))
        return 0
    print(stop_fake_slack(run))
    return 0


# ---------------------------------------------------------------- cleanup

def procs_in(path: Path) -> list[tuple[int, str]] | None:
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    found, me = [], {os.getpid(), os.getppid()}
    for d in proc.iterdir():
        if not d.name.isdigit() or int(d.name) in me:
            continue
        try:
            cwd = os.readlink(d / "cwd")
        except OSError:
            continue
        if cwd == str(path) or cwd.startswith(str(path) + "/"):
            found.append((int(d.name), proc_cmdline(int(d.name))))
    return found


def delete_agent(run: Run, uid: str, dry: bool, say) -> str:
    """ok | gone | foreign | failed. The --yes delete runs only after its --dry-run plan exited 0, names this
    agent, and names no agent the run did not record."""
    base = ["projmux", "delete", "agent", f"uid:{uid}", "-p", f"uid:{run.project}"]
    code, out, err = run.cmd(f"delete-agent-plan-{uid}", [*base, "--dry-run"])
    if code != 0 and "matched no agents" in out + err:
        say(f"agent uid:{uid}: already gone")
        return "gone"
    foreign = set(AGENT_RE.findall(out + err)) - set(run.data["created"]["agents"])
    if foreign:
        say(f"REFUSED: the delete plan for uid:{uid} names agents this run did not record {sorted(foreign)}")
        return "foreign"
    if code != 0 or uid not in out + err:
        say(f"FAIL: the delete plan for uid:{uid} exited {code}{'' if uid in out + err else ' without naming it'}; "
            "nothing deleted")
        return "failed"
    if dry:
        say(f"plan for uid:{uid}: see the delete-agent-plan step (projmux --dry-run)")
        return "ok"
    code, _, _ = run.cmd(f"delete-agent-{uid}", [*base, "--yes"])
    if code != 0:
        say(f"FAIL: projmux delete agent uid:{uid} --yes exited {code}")
    return "ok" if code == 0 else "failed"


def registered_projects() -> set[str] | None:
    code, out, _ = sh(["projmux", "get", "projects", "-o", "json"])
    data = jload(out) if code == 0 else None
    return {i["metadata"]["uid"] for i in data.get("items", [])} if isinstance(data, dict) else None


def live_agents() -> set[str] | None:
    code, out, _ = sh(["projmux", "get", "agents", "-A", "-o", "json"])
    data = jload(out) if code == 0 else None
    return {i["metadata"]["uid"] for i in data.get("items", [])} if isinstance(data, dict) else None


def write_manifest(run: Run) -> int:
    files = sorted(p for p in run.ev.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256")
    (run.ev / "MANIFEST.sha256").write_text("".join(f"{sha256_file(p)}  {p.relative_to(run.ev)}\n" for p in files))
    return len(files)


def has_install_row(run: Run) -> bool | None:
    """This run's project-scope install row is listed (None: the listing failed)."""
    code, out, _ = sh(["claude", "plugin", "list", "--json"], cwd=run.proj if run.proj.is_dir() else run.ev)
    rows = jload(out) if code == 0 else None
    if not isinstance(rows, list):
        return None
    return any(p.get("id") == PLUGIN_ID and p.get("scope") == "project" and same_path(p.get("projectPath"), run.proj)
               for p in rows)


def leftovers(run: Run) -> list[str]:
    """What this run created that is still there, read from projmux, Claude and the disk right now."""
    out, proj = [], run.project
    projects, agents = registered_projects(), live_agents()
    if proj and (projects is None or proj in projects):
        out.append(f"project uid:{proj} {'(projmux listing failed)' if projects is None else 'registered'}")
    alive = sorted(set(run.data["created"]["agents"]) & (agents or set()))
    if agents is None or alive:
        out.append(f"agents alive: {alive if agents is not None else '(projmux listing failed)'}")
    row = has_install_row(run)
    if row is None or row:
        out.append("project-scope install row" + (" (claude plugin list failed)" if row is None else ""))
    rec = run.data["pids"].get("fake-slack") or {}
    if rec.get("pid") and not rec.get("stopped"):
        out.append(f"FakeSlack pid {rec['pid']}")
    if run.proj.is_dir():
        out.append(f"scratch project {run.proj} (kept, with its digest key, for the next cleanup)")
    return out


def cmd_cleanup(a) -> int:
    """Reconcile: re-read what is really left (project registered, agents alive, install row, FakeSlack) and tear
    down only what run.json records. Every mutation runs only after its --dry-run plan exited 0 and was checked;
    an ownership refusal stops at once (4). The scratch project is deleted and the run marked cleaned only when
    every step passed; otherwise it stays, with its digest key, and the next cleanup picks up from what is left.
    --dry-run prints the plans and changes nothing outside the evidence dir (it adds step files)."""
    run = Run.load(a.run)
    ok, proj, dry, log = True, run.project, a.dry_run, []

    def say(line: str) -> None:
        print(line)
        log.append(line)

    def finish(rc: int) -> int:
        name = "cleanup-dry-run" if dry else "cleanup"
        if rc != 0 and not dry:
            for item in leftovers(run):
                say(f"LEFT {item}")
        (run.ev / "steps" / f"{run.next_id(name)}.txt").write_text("\n".join(log) + f"\n# exit: {rc}\n")
        if not dry:
            n = write_manifest(run)
            print(f"EVIDENCE={run.ev}  files={n}  manifest={run.ev / 'MANIFEST.sha256'}")
        return rc

    def ownership_stop(what: str) -> int:
        say(f"STOP (4): {what}. smv never deletes or stops what it did not record. Ask the operator; after they "
            "removed or explained it, run smv cleanup again.")
        return finish(4)

    if run.data.get("cleaned") and not run.proj.exists():
        say(f"cleanup already completed with PASS at {run.data['cleaned']}; nothing left to tear down")
        return finish(0)
    projects, agents = registered_projects(), live_agents()
    if projects is None or agents is None:
        say("FAIL: projmux get projects / get agents -A failed: cannot tell what is left; nothing was changed")
        return finish(1)
    registered = bool(proj) and proj in projects
    if registered:
        foreign = run.foreign_agents()
        for i in foreign:
            say(f"FOREIGN uid:{i['metadata']['uid']} name={i['metadata'].get('name')} creator=uid:{creator_of(i)} "
                f"cwd={cwd_of(i)}")
        if foreign:
            return ownership_stop("agents in this run's project that the run did not record (creator rule)")
    recorded = list(run.data["created"]["agents"])
    alive = [u for u in recorded if u in agents]
    if not dry:  # evidence first: final transcript extracts and last screens (dead panes too) survive teardown
        for uid in alive:
            record_transcript(run, uid, "cleanup final")
            capture_any(run, uid, f"final-{uid}", "state")
    uninstall = ["claude", "plugin", "uninstall", PLUGIN_ID, "--scope", "project", "--keep-data"]
    row = has_install_row(run)
    if row is None:
        ok = False
        say("FAIL: claude plugin list --json failed: cannot tell whether the install row is left")
    elif row and not run.proj.is_dir():
        ok = False
        say("FAIL: an install row names the scratch project, but its directory is gone: uninstall needs it as cwd")
    elif row and dry:
        say(f"would run in the scratch project: {' '.join(uninstall)}")
    elif row:
        code = run.cmd("plugin-uninstall-project", uninstall, cwd=run.proj, timeout=180)[0]
        ok &= code == 0 and has_install_row(run) is False
    else:
        say("plugin: no project-scope install row for this scratch project")
    mkt = run.data.get("marketplace") or {}
    remove = ["claude", "plugin", "marketplace", "remove", MARKETPLACE, "--scope", "project", "--json"]
    scratch_settings = run.proj / ".claude/settings.json"
    declared_now = MARKETPLACE in ((jload(scratch_settings.read_text(), {}) if scratch_settings.is_file() else {})
                                   .get("extraKnownMarketplaces") or {})
    if mkt.get("declared") and mkt.get("registry_preexisting"):
        say(f"marketplace: the registry entry '{MARKETPLACE}' predates this run; it is left as it was (the project "
            "declaration goes with the scratch project)")
    elif not declared_now:
        say("marketplace: the scratch project declares no marketplace (nothing to remove)")
    elif dry:
        say(f"would run in the scratch project: {' '.join(remove)}")
    else:  # never without --scope: that removes the marketplace from every scope
        ok &= run.cmd("marketplace-remove-project", remove, cwd=run.proj, timeout=180)[0] == 0
    for uid in reversed(recorded):
        if uid not in alive:
            say(f"agent uid:{uid}: not alive (already gone)")
            continue
        res = delete_agent(run, uid, dry, say)
        if res == "foreign":
            return ownership_stop(f"the delete plan for uid:{uid} names an agent this run did not record")
        if res == "failed":
            say("FAIL: cleanup stops here: no project stop or unregister while a recorded agent is left")
            return finish(1)
    say(f"fake-slack: {stop_fake_slack(run, dry)}")
    if registered:  # ownership again, right before the whole-project operations
        foreign = run.foreign_agents()
        if foreign:
            return ownership_stop(f"agents appeared in the project during cleanup: "
                                  f"{[i['metadata']['uid'] for i in foreign]}")
        stop_project = ["projmux", "stop", "project", f"uid:{proj}", "-o", "receipt"]
        if dry:
            say(f"would run: {' '.join(stop_project)}")
        elif run.cmd("stop-project", stop_project)[0] != 0:
            say("FAIL: projmux stop project exited non-zero: cleanup stops here (no unregister)")
            return finish(1)
    left = procs_in(run.proj)
    for _ in range(5 if not dry else 0):  # give stopped panes a moment to exit; never signal them
        if not left:
            break
        time.sleep(2)
        left = procs_in(run.proj)
    if left is None:
        say("process scan unavailable (no /proc): the operator checks for processes in the scratch project by hand")
    elif dry:
        say(f"info: {len(left)} process(es) have their cwd inside the scratch project now (the run's panes, "
            "before teardown)")
    elif left:
        for pid, cmdline in left:
            say(f"LEFT pid {pid} (cwd inside the scratch project): {cmdline[:160]}")
        run.note(f"cleanup stopped: processes left inside the scratch project {[pid for pid, _ in left]}")
        say("STOP (4): processes are still running inside the scratch project after projmux stop project. smv "
            "kills only the FakeSlack pid it recorded, never these. Ask the operator; then run smv cleanup again.")
        return finish(4)
    if registered:
        code, out, err = run.cmd("unregister-project-plan", ["projmux", "unregister", "project", f"uid:{proj}",
                                                             "--dry-run"])
        other = (set(PROJ_RE.findall(out + err)) - {proj}) | (set(AGENT_RE.findall(out + err)) - set(recorded))
        if other:
            return ownership_stop(f"the unregister plan names resources this run did not create {sorted(other)}")
        if code != 0 or proj not in out + err:
            say(f"FAIL: the unregister plan exited {code}{'' if proj in out + err else ' without naming the project'}"
                "; nothing unregistered")
            return finish(1)
        if not dry and run.cmd("unregister-project", ["projmux", "unregister", "project", f"uid:{proj}",
                                                      "--yes"])[0] != 0:
            say("FAIL: projmux unregister project --yes exited non-zero")
            return finish(1)
    else:
        say(f"project uid:{proj}: not registered (already unregistered)" if proj else "project: none recorded")
    if dry:
        say("dry-run: nothing was changed outside the evidence dir; the plans are in the step files above")
        return finish(0 if ok else 1)
    projects, agents = registered_projects(), live_agents()
    gone_p = projects is not None and proj not in projects
    alive = sorted(set(recorded) & agents) if agents is not None else ["(projmux listing failed)"]
    say(f"{'PASS' if gone_p else 'FAIL'}  project uid:{proj} unregistered")
    say(f"{'PASS' if not alive else 'FAIL'}  recorded agents gone: {alive or 'all'}")
    ok &= gone_p and not alive
    for name, good, detail in isolation_checks(run, "after-cleanup", "cleanup"):
        say(f"{'PASS' if good else 'FAIL'}  {name}: {detail}")
        ok &= good
    if not ok:
        say("cleanup: FAIL (the scratch project and its digest key are kept; fix the cause, then run smv cleanup "
            "again)")
        return finish(1)
    if not a.keep_proj and run.proj.is_dir() and run.proj.parent == run.root:
        shutil.rmtree(run.proj)  # takes the digest key with it
        say(f"removed scratch project {run.proj}")
    run.update(lambda d: d.__setitem__("cleaned", now_iso()))
    say("cleanup: PASS")
    return finish(0)


# ---------------------------------------------------------------- offline self-tests (tests/, stubs only)

TESTS_DIR = Path(__file__).resolve().parents[1] / "tests"


def cmd_selftest_redaction(a) -> int:
    """The marker self-test (tests/redaction_selftest.py): no tool_result body, user config or instruction text,
    tool-output preview, e-mail or config path (wrapped ones too) may reach the evidence dir."""
    sys.path.insert(0, str(TESTS_DIR))
    os.environ["SMV_UNDER_TEST"] = str(Path(__file__).resolve())
    import redaction_selftest
    return redaction_selftest.run(keep=a.keep, verbose=a.verbose)


def cmd_selftest(a) -> int:
    """Every offline test in tests/ (screen parsing on redacted live captures, the behaviour scenarios against
    stub projmux / tmux / claude, the marker self-test). No projmux, tmux or claude is touched."""
    env = {**os.environ, "SMV_UNDER_TEST": str(Path(__file__).resolve()), "PYTHONDONTWRITEBYTECODE": "1"}
    argv = [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(TESTS_DIR), "-t", str(TESTS_DIR),
            *(["-v"] if a.verbose else []), *(x for k in a.k for x in ("-k", k))]
    return subprocess.run(argv, env=env, cwd=TESTS_DIR).returncode


# ---------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    resume = "".join(f"\n      {kind}: {cmd}" for kind, cmd in RESUME.items())
    ap = argparse.ArgumentParser(prog="smv", description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=f"{__doc__}\nresume command per stop (each STOP line prints the same):{resume}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn, help_text: str, agent: bool = False) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(fn=fn)
        if name != "launch":
            p.add_argument("--run", help="<scratch-root>/<run-id> (default: $SMV_RUN)")
        if agent:
            p.add_argument("--agent", help="pm (default) | role:<role> | <agent uid recorded by this run>")
        return p

    p = add("launch", cmd_launch, "create the run, the projmux project and the PM Claude pane")
    p.add_argument("--scratch-root", required=True)
    p.add_argument("--prefix", default="smv")
    p.add_argument("--timeout", type=int, default=300,
                   help="seconds for the whole launch: every step and wait shares this budget")
    p = add("doctor", cmd_doctor, "read-only: is this instance worth driving?")
    p.add_argument("--pre", action="store_true", help="before the plugin is installed")
    p = add("wait", cmd_wait, "wait for the turn to finish, the pane to be ready, or a pattern", agent=True)
    p.add_argument("--ready", action="store_true")
    p.add_argument("--expect")
    p.add_argument("--fail")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--poll", type=float, default=3.0)
    p.add_argument("--settle", type=int, default=0)
    p = add("send", cmd_send, "type one line into the pane and press Enter (refused unless ready)", agent=True)
    p.add_argument("--text", required=True)
    p = add("capture", cmd_capture, "save a capture-pane transcript", agent=True)
    p.add_argument("--label", required=True)
    add("status", cmd_status, "print projmux interaction status and screen state", agent=True)
    p = add("cmd", cmd_cmd, "run one command and keep cmd/stdout/stderr/exit as evidence")
    p.add_argument("--label", required=True)
    p.add_argument("--cwd", choices=["proj", "repo", "evidence"], default="proj")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("argv", nargs=argparse.REMAINDER)
    p = add("note", cmd_note, "append a timestamped note (operator decisions) to evidence/notes.md")
    p.add_argument("--text", required=True)
    p = add("hash", cmd_hash, "sha256 of every scratch-project file except .git/")
    p.add_argument("--label", required=True)
    p.add_argument("--exclude", action="append", default=[], help="top-level dir to leave out (repeatable)")
    p = add("hash-diff", cmd_hash_diff, "compare two hash lists; each --allow change must happen, nothing else may")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--allow", action="append", default=[], metavar="'KIND PATH'",
                   help="ADDED|MISSING|CHANGED and a relative scratch-project path (repeatable); anything else is "
                        "refused with exit 2")
    p = add("tgrep", cmd_tgrep, "search an agent's Claude transcript in place (evidence: redacted extract)",
            agent=True)
    p.add_argument("--pattern", required=True)
    p.add_argument("--since-last-send", action="store_true")
    p.add_argument("--assistant-only", action="store_true", help="only the agent's own replies and tool calls")
    p.add_argument("--tool-inputs", action="store_true", help="only the agent's tool-call inputs, as JSON objects")
    p.add_argument("--absent", action="store_true", help="pass only when there is no hit")
    p.add_argument("--min", type=int, default=1)
    p.add_argument("--wait", type=int, default=0, help="keep polling up to N seconds")
    add("msg-status", cmd_msg_status, "check dispatch receipts since the last send with projmux", agent=True)
    p = add("init-check", cmd_init_check, "assert the init journey from transcript + hashes")
    p.add_argument("mode", choices=["fresh", "rerun"])
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p = add("agents", cmd_agents, "list (and record) the agents in this run's project")
    p.add_argument("--label", default="list")
    p.add_argument("--expect-count", type=int)
    p = add("spawn-check", cmd_spawn_check, "assert a spawned role pane: labels, cwd, ROLE LOADED")
    p.add_argument("--role", required=True)
    p.add_argument("--wait", type=int, default=300)
    p = add("invariant", cmd_invariant, "assert a stamped role file keeps the INVARIANT block byte-identical")
    p.add_argument("--file", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--blank-part-b", action="store_true", help="also require the Role scope section untouched")
    p = add("seed", cmd_seed, "create a fixture file in the scratch project (never inside the checkout)")
    p.add_argument("--file", required=True)
    p.add_argument("--text", default="smv fixture")
    p.add_argument("--line", action="append", default=[], help="one line of the file (repeatable; replaces --text)")
    add("mkt-project", cmd_mkt_project, "declare the marketplace in the scratch project (CLI, --scope project)")
    p = add("scope-pick", cmd_scope_pick, "after /plugin install: highlight the exact project-scope option, verify, "
            "Enter", agent=True)
    p.add_argument("--max-moves", type=int, default=6)
    p.add_argument("--timeout", type=int, default=120, help="seconds to wait for the prompt after Enter")
    add("plugin-check", cmd_plugin_check, "assert the project-scope install from this checkout")
    p = add("fake-slack", cmd_fake_slack, "FakeSlack from tests/test_slack.py: start|say|summary|stop")
    p.add_argument("action", choices=["start", "say", "summary", "stop", "serve"])
    p.add_argument("--text")
    p.add_argument("--user", default=FAKE_USER, help="say: the Slack user id the message comes from")
    p = sub.add_parser("selftest", help="offline: run every test in tests/ (stubs only, no run needed)")
    p.set_defaults(fn=cmd_selftest)
    p.add_argument("--verbose", action="store_true")
    p.add_argument("-k", action="append", default=[], help="only tests whose name matches (repeatable)")
    p = sub.add_parser("selftest-redaction", help="offline: prove evidence holds no tool_result body or user config "
                       "/ instruction text (stubs only, no run needed)")
    p.set_defaults(fn=cmd_selftest_redaction)
    p.add_argument("--keep", action="store_true", help="keep the self-test directory for inspection")
    p.add_argument("--verbose", action="store_true", help="print every step's output")
    p = add("cleanup", cmd_cleanup, "tear down only what this run recorded; evidence stays")
    p.add_argument("--dry-run", action="store_true", help="print the delete/unregister plans; change nothing")
    p.add_argument("--keep-proj", action="store_true")
    a = ap.parse_args(argv)
    BUDGET["deadline"] = None  # only launch sets a budget
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # stdout lines land in order with stderr in one pipe
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
