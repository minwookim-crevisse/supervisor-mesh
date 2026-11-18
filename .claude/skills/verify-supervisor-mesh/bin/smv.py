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
from smv_screen import (  # noqa: E402
    SCOPE_OPTION,
    box_holds,
    box_is_empty,
    drop_map,
    highlighted,
    input_box,
    keep_lines,
    lines_of,
    mcp_rows,
    menu_options,
    permission_mode,
    screen_state,
)

PLUGIN_NAME = "supervisor-mesh"  # the plugin's own name (marketplace.json plugins[0].name): never renamed
# The user-level marketplace of this name (GitHub source) must end every run exactly as it started: a project-scope
# `marketplace add` of the same name rewrites that shared registry entry by name. So a run never adds, removes or
# declares this name; it exports the commit under test and installs it from a scratch-named marketplace.
GUARD_MARKETPLACE = "supervisor-mesh"
SCRATCH_PREFIX = "smv-"
SCRATCH_NAME_RE = re.compile(r"smv-[a-z0-9][a-z0-9-]{0,62}")
MKT_MANIFEST = ".claude-plugin/marketplace.json"
CREATOR = "projmux.io/creator-agent"
# user config files: compared by whole-file sha256 + keyed per-key digests; never copied, values never stored
USER_SETTINGS = "settings.json"
USER_REGISTRY = ("plugins/known_marketplaces.json", "plugins/installed_plugins.json")
SLACK_DEFAULT_STATE = Path.home() / ".local/state/supervisor-mesh-slack"  # the bridge's default SLACK_STATE_DIR
ALLOW_KINDS = ("ADDED", "MISSING", "CHANGED")
SHIPPED = (".claude-plugin", "skills", "agents", "templates", "docs/core", "mesh", ".mcp.json")  # dirs or files
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


def scratch_name(run_id: str) -> str:
    """The run's own marketplace name: lower-case letters, digits and '-', prefixed 'smv-', never the guard name."""
    body = re.sub(r"[^a-z0-9-]+", "-", run_id.lower()).strip("-")
    name = (body if body.startswith(SCRATCH_PREFIX) else SCRATCH_PREFIX + body)[:64].rstrip("-")
    if not SCRATCH_NAME_RE.fullmatch(name) or name == GUARD_MARKETPLACE:
        die(f"cannot derive a scratch marketplace name from run id {run_id!r}")
    return name


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

    # the scratch marketplace (never GUARD_MARKETPLACE): its name, the plugin id under it, the export it serves
    @property
    def mkt_name(self) -> str:
        return (self.data.get("marketplace") or {}).get("name") or scratch_name(self.data["run_id"])

    @property
    def plugin_id(self) -> str:
        return f"{PLUGIN_NAME}@{self.mkt_name}"

    @property
    def install_line(self) -> str:
        return f"/plugin install {self.plugin_id}"

    @property
    def mkt_src(self) -> Path:
        return self.root / "mkt-src"

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
                      f"'{run.install_line}', resume with scope-pick (it presses Enter only on a verified highlight). "
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
    mrows = listing(["claude", "plugin", "marketplace", "list", "--json"], run.ev)
    mkts = sorted([str(m.get("name")), kd(key, m)] for m in mrows)
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
            "guard_source": source_of(next((m for m in mrows if m.get("name") == GUARD_MARKETPLACE), None)),
            "installed_scopes": installed_scopes(cfg / USER_REGISTRY[1], key),
            "files": {rel: config_file_digest(cfg / rel, key) for rel in (USER_SETTINGS, *USER_REGISTRY)},
            "slack_default_state": slack, "lead_tree": lead_tree(run.repo)}


def installed_scopes(path: Path, key: bytes | None) -> dict[str, list[list[str]]]:
    """installed_plugins.json read in memory: per plugin id, each install entry's scope and a keyed digest of the
    entry. Ids and scope names only (the same fields `claude plugin list` rows already keep), never a value."""
    data = jload(path.read_text(errors="replace")) if path.is_file() else None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return {pid: sorted([str(x.get("scope")), kd(key, x)] for x in rows if isinstance(x, dict))
            for pid, rows in (plugins or {}).items() if isinstance(rows, list)}


def source_of(row: dict | None) -> dict | None:
    """A marketplace list row's SOURCE only: its type in clear, and the locator (repo, path or url) as a sha256, so
    two snapshots compare equal or not without the evidence holding the value. None: no such entry."""
    if not isinstance(row, dict):
        return None
    loc = {k: row.get(k) for k in ("source", "repo", "path", "url")}
    return {"type": str(row.get("source")),
            "locator_sha256": hashlib.sha256(json.dumps(loc, sort_keys=True).encode()).hexdigest()}


def guard_checks(base: dict | None, now: dict | None, label: str) -> list[tuple[str, bool, str]]:
    """The user-level GUARD_MARKETPLACE entry: same source (type + locator digest) as at the baseline."""
    show = lambda g: "absent" if g is None else f"{g['type']} locator {g['locator_sha256'][:12]}"  # noqa: E731
    return [(f"user marketplace '{GUARD_MARKETPLACE}' source unchanged ({label})", base == now,
             f"{show(base)} -> {show(now)}")]


def write_snapshot(run: Run, label: str) -> dict:
    snap = user_state(run)
    (run.ev / "snapshots" / f"{label}.json").write_text(json.dumps(snap, indent=1))
    return snap


def compare(base: dict, now: dict, phase: str, owned: tuple[str, str], declared: bool = False
            ) -> tuple[list[str], list[str]]:
    """(fails, notes). No flag excuses a fail: user settings, other plugin rows, the marketplace registry rows
    (the guard entry's source above all), the default Slack state dir and the lead tree must equal the baseline.
    `owned` is (scratch marketplace name, plugin id): phase 'doctor' tolerates this run's own install row and its
    scratch registry entry (`declared`: mkt-project declared it, so its name must be in the registry); phase
    'cleanup' requires both gone."""
    fails, notes = [], []
    mkt, pid = owned
    run_owned = {USER_REGISTRY[0]: (mkt, None), USER_REGISTRY[1]: ("plugins", pid)}
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
            own = run_owned[rel]
            mine = [key_path(r) for r in rows if r[1] == own[0] and own[1] in (None, r[2])]
            other = [key_path(r) for r in rows if key_path(r) not in mine]
            notes.append(f"user {rel} changed (sha256 {str(b['sha256'])[:12]} -> {str(n['sha256'])[:12]}, info): "
                         f"run-owned key paths {mine or '-'}; other key paths {other or '-'}")
            if rel == USER_REGISTRY[1]:  # installed_plugins.json: every changed key path must name @<scratch>
                foreign = [key_path(r) for r in rows if not (r[1] == "plugins" and str(r[2]).endswith(f"@{mkt}"))]
                if not keyed:
                    fails.append(f"user {rel} changed and its key paths cannot be named (no digest key)")
                elif foreign:
                    fails.append(f"user {rel}: changed key paths that do not name '@{mkt}': {foreign}")
    user_b, user_n = ({(pid, d) for pid, rows in (x.get("installed_scopes") or {}).items() for sc, d in rows
                       if sc == "user"} for x in (base, now))
    if user_b != user_n:  # a user-scope install entry appeared, disappeared or changed: never this run's doing
        fails.append(f"user {USER_REGISTRY[1]}: user-scope entries changed for "
                     f"{sorted({pid for pid, _ in user_b ^ user_n})}")
    # known_marketplaces.json: whole-file equality is no oracle (auto-refresh rewrites lastUpdated). Compared: the
    # set of marketplace names (== baseline, plus exactly the scratch name while it is declared) and the guard
    # entry's source (below). Key names only.
    nb, nn = (set(x["files"][USER_REGISTRY[0]]["keys"]) for x in (base, now))
    want = nb | {mkt} if declared and phase == "doctor" else nb
    if nn != want:
        fails.append(f"user {USER_REGISTRY[0]} marketplace names: +{sorted(nn - want)} -{sorted(want - nn)} "
                     f"(want the baseline{' plus ' + mkt if want != nb else ''})")
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
        if phase == "doctor" and name == mkt and name != GUARD_MARKETPLACE and name not in bm:
            notes.append(f"marketplace registry entry '{name}' added by this run's project-scope declaration")
            continue
        state = "MISSING" if name not in nm else ("ADDED" if name not in bm else "CHANGED")
        fails.append(f"marketplace registry row {state}: {name}")
    if base.get("guard_source", "unset") != now.get("guard_source", "unset"):
        fails.append(f"user marketplace '{GUARD_MARKETPLACE}' source changed: "
                     f"{guard_checks(base.get('guard_source'), now.get('guard_source'), phase)[0][2]}")
    reg_b, reg_n = (s["files"][USER_REGISTRY[0]]["sub"].get(GUARD_MARKETPLACE) or {} for s in (base, now))
    if keyed and reg_b.get("source") != reg_n.get("source"):
        fails.append(f"user registry key path {GUARD_MARKETPLACE}.source changed ({USER_REGISTRY[0]})")
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
        found = [base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []
        for p in found:
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
    """The project-scope install of the run's scratch-named plugin, compared with the commit export it was installed
    from (`mkt-project`), never with the working tree."""
    pid, mkt = run.plugin_id, run.data.get("marketplace") or {}
    code, out, err = sh(["claude", "plugin", "list", "--json"], cwd=run.proj)
    rows = jload(out, []) if code == 0 else []
    plugin_rows_step(run, "plugin-list", rows if isinstance(rows, list) else [], code)
    entries = [p for p in (rows if isinstance(rows, list) else []) if p.get("id") == pid]
    mine = [p for p in entries if p.get("scope") == "project" and same_path(p.get("projectPath"), run.proj)]
    local = [p for p in entries if p.get("scope") == "local" and same_path(p.get("projectPath"), run.proj)]
    src = run.mkt_src
    exported = mkt.get("mode") == "scratch-export" and mkt.get("export_ok") and src.is_dir()
    checks = [("scratch marketplace export recorded and verified (mkt-project)", bool(exported),
               f"{mkt.get('name')} at commit {str(mkt.get('commit'))[:12]}" if exported else "run smv mkt-project"),
              ("plugin list --json", code == 0, err.strip()[:120] or "ok"),
              (f"project-scope install of {pid} for this scratch project", len(mine) == 1, f"{len(mine)} entr(y/ies)"),
              ("no local-scope install for this scratch project", not local, f"{len(local)} entr(y/ies)")]
    if exported:
        now = tree_digest(src)
        checks.append(("export unchanged since mkt-project (shipped dirs)", digest_of(now) == mkt.get("shipped_sha256"),
                       f"{len(now)} files"))
    if len(mine) == 1 and exported:
        p = mine[0]
        want = json.loads((src / ".claude-plugin/plugin.json").read_text())["version"]
        checks.append(("installed version == export plugin.json", p.get("version") == want,
                       f"installed {p.get('version')} vs export {want}"))
        checks.append(("enabled", bool(p.get("enabled")), str(p.get("enabled"))))
        if p.get("installPath") and Path(p["installPath"]).is_dir():
            installed, export = tree_digest(Path(p["installPath"])), tree_digest(src)
            drift = sorted(k for k in set(installed) | set(export) if installed.get(k) != export.get(k))
            checks.append(("installed copy == commit export (shipped dirs)", not drift,
                           f"{len(drift)} differing: {drift[:8]}" if drift else f"{len(export)} files identical"))
        else:
            checks.append(("installed copy == commit export (shipped dirs)", False, "no installPath on disk"))
    settings = run.proj / ".claude/settings.json"
    enabled = ((jload(settings.read_text(), {}) if settings.is_file() else {}).get("enabledPlugins") or {})
    checks.append(("scratch .claude/settings.json enables the plugin", enabled.get(pid) is True,
                   f"{pid}: {enabled.get(pid)}"))
    return checks


def digest_of(tree: dict[str, str]) -> str:
    return hashlib.sha256("".join(f"{h}  {k}\n" for k, h in sorted(tree.items())).encode()).hexdigest()


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
    """The scaffold.py runs inside one Bash command: mode (apply, dry-run, or mixed when one command runs it more
    than once), and whether every run's --target names the scratch project (., the project dir variable, or its
    path)."""
    segs = [x for x in re.split(r"&&|\|\||;|\n|\|", command) if SCAFFOLD_RE.search(x)]
    if not segs:
        return None

    def target_ok(seg: str) -> bool:
        m = TARGET_RE.search(seg)
        target = next((g for g in m.groups() if g is not None), None) if m else None
        return target in PROJECT_TARGETS or (bool(target) and proj is not None and os.path.isabs(target)
                                             and same_path(target, proj))

    mode = "mixed" if len(segs) > 1 else ("dry-run" if re.search(r"(?<!\S)--dry-run\b", segs[0]) else "apply")
    return {"mode": mode, "target_ok": all(target_ok(x) for x in segs)}


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
    code = scratch_identity(run)
    if code != 0:
        return launch_failed(run, "git config (scratch identity)", code, "", "", budget(30))
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
    if a.pm_role:
        run.update(lambda d: d.__setitem__("pm_role", True))
    code, out, err, cap = step("create-claude", pm_create_argv(uid, run.proj, run_id, a.pm_role), 240)
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


SCRATCH_GIT_IDENTITY = {"user.name": "smv", "user.email": "smv-qa"}  # no '@': typed text never opens autocomplete


def scratch_identity(run: Run) -> int:
    """A repo-LOCAL git identity in the scratch repo only (never --global, never the lead tree), so a commit in the
    scratch project works on a host without a global identity. Recorded in run.json; each config call is a step."""
    for k, v in SCRATCH_GIT_IDENTITY.items():
        code = run.cmd(f"git-config-{k}", ["git", "-C", str(run.proj), "config", "--local", k, v], timeout=30)[0]
        if code != 0:
            return code
    run.update(lambda d: d.__setitem__("git_identity", {**SCRATCH_GIT_IDENTITY, "scope": "local", "repo": "proj"}))
    return 0


def pm_create_argv(project: str, proj: Path, run_id: str, pm_role: bool) -> list[str]:
    """The PM pane. --pm-role adds the projmux label role=pm: the mesh MCP tools refuse every pm verb to a PM pane
    without it (D82)."""
    return ["projmux", "create", "claude", "--host", "tmux", "--project", f"uid:{project}", "--cwd", str(proj),
            "--name", "pm", "--label", f"smv-run={run_id}", *(["--label", "role=pm"] if pm_role else []), "-o", "uid"]


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
    base, now = json.loads(base_p.read_text()), write_snapshot(run, label)
    rec = run.data.get("marketplace") or {}
    fails, notes = compare(base, now, phase, (run.mkt_name, run.plugin_id),
                           bool(rec.get("mode") == "scratch-export" and rec.get("declared")))
    out = guard_checks(base.get("guard_source"), now.get("guard_source"), f"{phase} vs baseline")
    if rec.get("mode") == "scratch-export":  # the source mkt-project recorded right before its add
        out += guard_checks(rec.get("guard"), now.get("guard_source"), f"{phase} vs mkt-project")
    out += [("user config, plugin rows, registry, Slack default state, lead tree == baseline", not fails,
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
    if a.allow_file:  # a named allowance list kept in the skill (features/*.allow): one 'KIND PATH' per line
        src = Path(a.allow_file)
        if not src.is_file() or not within(src, SKILL_DIR):
            die(f"--allow-file must be a file inside the skill dir ({SKILL_DIR.name}/)", 2)
        a.allow = [*a.allow, *(x.strip() for x in src.read_text().splitlines() if x.strip()
                               and not x.lstrip().startswith("#"))]
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
    or the agent's echo of a summary is info only, never proof. Rerun only: a mixed call (preview and apply in one
    command) on the scratch project also counts when every summary in its own result reads created=0 merged=0
    (at least one) and no file changed; fresh never takes a mixed call."""
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

    def own_sums(uid: str) -> list[tuple[int, int, int]]:
        return [tuple(int(x) for x in m.groups()) for r in results.get(uid, []) if not r["error"]
                for m in SUMMARY_RE.finditer(r["text"])]

    def summary(uid: str) -> tuple[int, int, int] | None:
        sums = own_sums(uid)
        return sums[-1] if sums else None

    applies = [i for i, c in calls.items() if c["mode"] == "apply" and c["target_ok"]]
    applied = [(i, summary(i)) for i in applies if summary(i)]
    previews = [i for i, c in calls.items() if c["mode"] == "dry-run"]
    others = [i for i, c in calls.items() if c["mode"] == "mixed" or not c["target_ok"]]
    echoes = [r["text"] for r in rows if r["kind"] == "assistant summary"]
    added, removed, changed = hash_diff(read_hashes(run, a.before), read_hashes(run, a.after))
    unchanged = not (added or removed or changed)
    mixed_proof, mixed_notes = [], []
    for i in (i for i, c in calls.items() if c["mode"] == "mixed" and c["target_ok"] and a.mode == "rerun"):
        sums = own_sums(i)
        why = ("its own result has no summary" if not sums else
               "a summary has created>0 or merged>0" if not all(c == 0 and m == 0 for c, m, _ in sums) else
               "files changed" if not unchanged else None)
        if why:
            mixed_notes.append(f"mixed call {i} not counted: {why}")
        else:
            mixed_proof.append(i)
            mixed_notes.append(f"mixed call {i}: every summary created=0 merged=0 ({len(sums)}) and no file changed")
    proof = ", ".join([*(f"{i}: created={c} merged={m} skipped={k}" for i, (c, m, k) in applied), *mixed_notes])
    checks = [("scaffold.py runs since the last send (info)", True,
               f"apply on the scratch project {len(applies)} (with a result summary {len(applied)}), dry-run "
               f"{len(previews)}, other target or several runs in one command {len(others)}; echoed summary lines "
               f"{len(echoes)} (info only, never proof)"),
              ("an apply run (no --dry-run, --target = scratch project) with its own result summary",
               bool(applied or mixed_proof), proof or "none"),
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
        checks += [("every apply: created=0 merged=0", bool(applied or mixed_proof) and all(
                        c == 0 and mg == 0 for _, (c, mg, _) in applied),
                    str([x for _, x in applied] + [f"mixed {i}: {own_sums(i)}" for i in mixed_proof])),
                   ("every file's sha256 unchanged", not (added or removed or changed),
                    f"added={added} removed={removed} changed={changed}")]
    return print_checks(f"init-check {a.mode}", checks, run)


def scaffold_paths(run: Run, transcript: Path) -> str:
    """Which scaffold.py the PM's tool calls ran since the last send: the commit export the scratch marketplace
    serves, the installed copy, this checkout, or other. With a directory marketplace the skill may run the
    source's file; plugin-check proves byte identity only."""
    code, out, _ = sh(["claude", "plugin", "list", "--json"], cwd=run.proj)
    rows = jload(out, []) if code == 0 else []
    install = next((r.get("installPath") for r in (rows if isinstance(rows, list) else [])
                    if r.get("id") == run.plugin_id and r.get("scope") == "project"
                    and same_path(r.get("projectPath"), run.proj)), None)
    seen: dict[str, int] = {}
    for _, inp in transcript_texts(transcript, since_mark(run, True), tool_inputs=True):
        for raw in SCAFFOLD_RE.findall(inp):
            p = Path(os.path.expandvars(raw.replace("${CLAUDE_PLUGIN_ROOT}", "$CLAUDE_PLUGIN_ROOT")))
            where = ("commit export" if p.is_absolute() and within(p, run.mkt_src) else
                     "checkout" if p.is_absolute() and within(p, run.repo) else
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


def json_diff(a, b, path: str = "") -> list[str]:
    """Key paths where two JSON values differ (`plugins[0].name`); never a value."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [x for k in sorted(set(a) | set(b), key=str)
                for x in (json_diff(a.get(k), b.get(k), f"{path}.{k}" if path else str(k)) if k in a and k in b
                          else [f"{path}.{k}" if path else str(k)])]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [x for i, (u, v) in enumerate(zip(a, b)) for x in json_diff(u, v, f"{path}[{i}]")]
    return [] if a == b and type(a) is type(b) else [path or "(root)"]


def git_blob_id(data: bytes, algo: str) -> str:
    return hashlib.new(algo, b"blob %d\0" % len(data) + data).hexdigest()


def commit_tree(repo: Path, sha: str) -> tuple[str, dict[str, tuple[str, str, str]]]:
    """(object format, {path: (mode, type, object id)}) of every entry of the commit, read with git ls-tree -z."""
    code, fmt, err = sh(["git", "-C", str(repo), "rev-parse", "--show-object-format"])
    algo = fmt.strip() if code == 0 and fmt.strip() in ("sha1", "sha256") else "sha1"
    code, out, err = sh(["git", "-C", str(repo), "ls-tree", "-r", "-z", "--full-tree", sha])
    if code != 0:
        die(f"git ls-tree {sha[:12]} failed (exit {code}): {err.strip()[:160]}", 1)
    tree = {}
    for rec in filter(None, out.split("\0")):
        meta, path = rec.split("\t", 1)
        mode, kind, oid = meta.split()
        tree[path] = (mode, kind, oid)
    return algo, tree


def export_checks(run: Run, sha: str, dest: Path, name: str) -> list[tuple[str, bool, str]]:
    """The export equals the commit, except that the marketplace manifest's `name` is the scratch name: every other
    file's git blob id equals the commit's, no file is missing or extra, and the manifest's JSON differs at the key
    path `name` only. Evidence: export/tree.txt (object id, verdict, path) and export/manifest-keypaths.txt."""
    algo, tree = commit_tree(run.repo, sha)
    on_disk = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file() or p.is_symlink()}
    rows, missing, differ, subs = [], [], [], []
    for path, (mode, kind, oid) in sorted(tree.items()):
        f = dest / path
        if kind == "commit":
            subs.append(path)
            rows.append(f"{oid}  submodule  {path}")
            continue
        if path == MKT_MANIFEST:
            rows.append(f"{oid}  manifest   {path}")
            continue
        data = (os.readlink(f).encode() if f.is_symlink() else None) if mode == "120000" else (
            f.read_bytes() if f.is_file() and not f.is_symlink() else None)
        verdict = "missing" if data is None else ("same" if git_blob_id(data, algo) == oid else "DIFFERS")
        (missing if data is None else differ if verdict == "DIFFERS" else []).append(path)
        rows.append(f"{oid}  {verdict:<9}  {path}")
    extra = sorted(on_disk - {p for p, (_, k, _) in tree.items() if k != "commit"})
    want = jload(sh(["git", "-C", str(run.repo), "cat-file", "blob", tree.get(MKT_MANIFEST, ("", "", "-"))[2]])[1])
    got = jload((dest / MKT_MANIFEST).read_text()) if (dest / MKT_MANIFEST).is_file() else None
    diff = json_diff(want, got) if isinstance(want, dict) and isinstance(got, dict) else ["(manifest unreadable)"]
    plugins = [x.get("name") for x in (want or {}).get("plugins", []) if isinstance(x, dict)] if want else []
    edir = run.ev / "export"
    edir.mkdir(exist_ok=True)
    (edir / "tree.txt").write_text(f"# commit {sha}  object format {algo}  entries {len(tree)}\n"
                                   + "".join(f"{r}\n" for r in rows) + "".join(f"-  EXTRA  {x}\n" for x in extra))
    (edir / "manifest-keypaths.txt").write_text(f"# {MKT_MANIFEST}: key paths where the export differs from the "
                                                f"commit (no values)\n" + "".join(f"{x}\n" for x in diff))
    files = len(tree) - len(subs) - 1
    return [("export file set == commit tree", not missing and not extra,
             f"{files} files + manifest; missing {missing[:5]}; extra {extra[:5]}"),
            ("every exported file except the manifest == its commit blob", not differ,
             f"{files - len(differ) - len(missing)} same; differing {differ[:5]}"),
            (f"{MKT_MANIFEST} differs from the commit at key path 'name' only", diff == ["name"], str(diff[:6])),
            (f"{MKT_MANIFEST} name is the scratch name", (got or {}).get("name") == name, name),
            (f"the commit's marketplace lists the plugin '{PLUGIN_NAME}'", PLUGIN_NAME in plugins, str(plugins)),
            ("no submodule in the commit (an archive leaves it empty)", not subs, str(subs[:5]))]


def make_export(run: Run, sha: str, dest: Path, name: str) -> bool:
    """git archive <commit> | tar -x into <run>/mkt-src (two captured steps through a tar file next to it, removed
    after), then the one edit: the manifest's `name`. A previous attempt's export of the same commit is reused."""
    if not dest.is_dir():
        tar = run.root / "mkt-src.tar"
        code = run.cmd("export-archive", ["git", "-C", str(run.repo), "archive", "--format=tar", "-o", str(tar),
                                          sha], timeout=120)[0]
        if code == 0:
            dest.mkdir()
            code = run.cmd("export-extract", ["tar", "-x", "-f", str(tar), "-C", str(dest)], timeout=120)[0]
        tar.unlink(missing_ok=True)
        if code != 0:
            return False
    manifest = dest / MKT_MANIFEST
    data = jload(manifest.read_text()) if manifest.is_file() else None
    if not isinstance(data, dict):
        return False
    if data.get("name") != name:
        data["name"] = name
        manifest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


def cmd_mkt_project(a) -> int:
    """Declare a SCRATCH-NAMED marketplace in the scratch project with the CLI: an export of the commit under test
    whose manifest name is the run's own (smv-...), so the user-level GUARD_MARKETPLACE registry entry is never
    rewritten. The slash '/plugin marketplace add' is never used (it writes user settings). Stops (21) when the
    scratch name is already registered or declared at user scope by anything but this run."""
    run = Run.load(a.run)
    name = run.mkt_name
    if name == GUARD_MARKETPLACE or not SCRATCH_NAME_RE.fullmatch(name):
        die(f"refused: '{name}' is not a scratch marketplace name", 2)
    if a.commit.startswith("-"):
        die("--commit must be a commit-ish, not an option", 2)
    code, out, err = sh(["claude", "plugin", "marketplace", "list", "--json"], cwd=run.ev)
    if code != 0 or not isinstance(jload(out), list):
        die(f"claude plugin marketplace list --json failed (exit {code}): {err.strip()[:200]}", 1)
    rows = [m for m in jload(out) if isinstance(m, dict)]
    guard = source_of(next((m for m in rows if m.get("name") == GUARD_MARKETPLACE), None))
    known = next((m for m in rows if m.get("name") == name), None)
    settings_file = claude_config_dir() / USER_SETTINGS  # read in memory only: never copied, no value printed
    decl = ((jload(settings_file.read_text(), {}) if settings_file.is_file() else {})
            .get("extraKnownMarketplaces") or {}).get(name)
    rec = run.data.get("marketplace") or {}
    ours = rec.get("mode") == "scratch-export" and rec.get("name") == name
    mine = bool(known) and ours and known.get("source") == "directory" and same_path(known.get("path"), run.mkt_src)
    print(f"user marketplace '{GUARD_MARKETPLACE}': "
          f"{guard['type'] + ' source (left untouched)' if guard else 'none'}")
    print(f"scratch marketplace '{name}': registry {'none' if not known else ('this run' if mine else 'OTHER')}, "
          f"user settings {'none' if not decl else 'DECLARED'}")
    if (known and not mine) or decl:
        print(f"STOP (21): the scratch marketplace name '{name}' is already registered or declared at user scope "
              "by something other than this run. Ask the operator. smv changes nothing.")
        return 21
    code, out, _ = run.cmd("commit-under-test", ["git", "-C", str(run.repo), "rev-parse", "--verify", "--quiet",
                                                 "--end-of-options", f"{a.commit}^{{commit}}"])
    sha = out.strip()
    if code != 0 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        die(f"--commit {a.commit!r} does not name a commit in the checkout", 2)
    if ours and rec.get("commit") != sha:
        die(f"this run already exported commit {str(rec.get('commit'))[:12]}; one commit per run (launch a new one)",
            2)

    def claim(d: dict) -> bool:
        if (d.get("marketplace") or {}).get("mode") == "scratch-export":
            return False
        d["marketplace"] = {"mode": "scratch-export", "name": name, "plugin_id": f"{PLUGIN_NAME}@{name}",
                            "commit": sha, "src": str(run.mkt_src), "guard": guard, "registry_preexisting": False,
                            "export_ok": False, "declared": False, "at": now_iso()}
        return True

    first = run.update(claim)
    exported = make_export(run, sha, run.mkt_src, name)
    echecks = export_checks(run, sha, run.mkt_src, name) if exported else [
        ("git archive | tar -x of the commit, manifest renamed", False, "see the export-* step files")]
    export_ok = all(c[1] for c in echecks)
    shipped = digest_of(tree_digest(run.mkt_src)) if export_ok else None
    run.update(lambda d: d["marketplace"].update(export_ok=export_ok, shipped_sha256=shipped))
    if not export_ok:
        return print_checks("mkt-project", echecks, run)
    before = config_file_digest(settings_file, None)["sha256"]
    code, _, _ = run.cmd("marketplace-add-project", ["claude", "plugin", "marketplace", "add", str(run.mkt_src),
                                                     "--scope", "project", "--json"], cwd=run.proj, timeout=300)
    settings = run.proj / ".claude/settings.json"
    scratch_decl = (jload(settings.read_text(), {}) if settings.is_file() else {}).get("extraKnownMarketplaces") or {}
    user_same = config_file_digest(settings_file, None)["sha256"] == before
    lc, lo, _ = sh(["claude", "plugin", "marketplace", "list", "--json"], cwd=run.proj)
    after = [m for m in (jload(lo, []) if lc == 0 else []) or [] if isinstance(m, dict)]
    entry = next((m for m in after if m.get("name") == name), None)
    guard_now = source_of(next((m for m in after if m.get("name") == GUARD_MARKETPLACE), None))
    checks = echecks + [
        ("claude plugin marketplace add <commit export> --scope project", code == 0, f"exit {code}"),
        ("scratch .claude/settings.json declares the scratch marketplace", name in scratch_decl, name),
        ("scratch .claude/settings.json does not declare the guard name", GUARD_MARKETPLACE not in scratch_decl,
         GUARD_MARKETPLACE),
        (f"registry entry '{name}' is the commit export (directory source)", bool(entry) and
         entry.get("source") == "directory" and same_path(entry.get("path"), run.mkt_src),
         "the export" if entry else "no entry"),
        *guard_checks(guard, guard_now, "before vs after the add"),
        (f"user {USER_SETTINGS} sha256 unchanged by the add", user_same, "whole-file sha256")]
    ok = all(c[1] for c in checks)
    run.update(lambda d: d["marketplace"].update(declared=ok, at=now_iso()))
    run.note(f"scratch marketplace '{name}' (commit {sha[:12]} exported, manifest name the only change) declared at "
             f"project scope with the CLI ({'first' if first else 'repeated'} add); '{GUARD_MARKETPLACE}' never "
             "added, removed or declared; the slash /plugin marketplace add is not exercised")
    rc = print_checks("mkt-project", checks, run)
    print(f"PLUGIN_ID={run.plugin_id}\nnext: \"$SMV\" send --text \"{run.install_line}\"")
    return rc


def cmd_scope_pick(a) -> int:
    """Drive the /plugin install scope menu like a user: find the exact project-scope option, move the highlight
    onto it with arrow keys, re-capture and confirm the highlighted line is that exact text, then press Enter.
    Anything unexpected stops with 12 and nothing is pressed; Enter is never pressed on an unverified line."""
    run = Run.load(a.run)
    agent = run.resolve_agent(a.agent)
    if (run.data["sends"].get(agent) or {}).get("text") != run.install_line:
        die(f"scope-pick runs only right after: smv send --text '{run.install_line}'")
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
    return any(p.get("id") == run.plugin_id and p.get("scope") == "project"
               and same_path(p.get("projectPath"), run.proj) for p in rows)


def scratch_registered(run: Run) -> bool | None:
    """The run's scratch marketplace name is in the registry (None: the listing failed)."""
    code, out, _ = sh(["claude", "plugin", "marketplace", "list", "--json"], cwd=run.ev)
    rows = jload(out) if code == 0 else None
    if not isinstance(rows, list):
        return None
    return any(isinstance(m, dict) and m.get("name") == run.mkt_name for m in rows)


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
    if (run.data.get("marketplace") or {}).get("mode") == "scratch-export":
        reg = scratch_registered(run)
        if reg is None or reg:
            out.append(f"scratch marketplace registry entry '{run.mkt_name}'"
                       + (" (claude plugin marketplace list failed)" if reg is None else ""))
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
    uninstall = ["claude", "plugin", "uninstall", run.plugin_id, "--scope", "project", "--keep-data"]
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
    name = mkt.get("name")
    scratch_settings = run.proj / ".claude/settings.json"
    declared = ((jload(scratch_settings.read_text(), {}) if scratch_settings.is_file() else {})
                .get("extraKnownMarketplaces") or {})
    if mkt.get("mode") != "scratch-export":
        say("marketplace: this run declared no scratch marketplace (nothing to remove)")
    elif name == GUARD_MARKETPLACE or not SCRATCH_NAME_RE.fullmatch(str(name)):
        ok = False  # never: the guard name is never added, so it is never removed either
        say(f"FAIL: the recorded marketplace name {name!r} is not a scratch name; nothing removed")
    else:  # the scratch name is always created by this run (mkt-project stops on an existing one): remove it,
        # at project scope only and by its recorded name (never without --scope: that removes it from every scope)
        remove = ["claude", "plugin", "marketplace", "remove", name, "--scope", "project", "--json"]
        if GUARD_MARKETPLACE in declared:
            ok = False
            say(f"FAIL: the scratch settings declare '{GUARD_MARKETPLACE}': smv never declares it; nothing removed")
        elif name not in declared:
            say(f"marketplace: the scratch project no longer declares '{name}' (nothing to remove at project scope)")
        elif dry:
            say(f"would run in the scratch project: {' '.join(remove)}")
        else:
            ok &= run.cmd("marketplace-remove-project", remove, cwd=run.proj, timeout=180)[0] == 0
        if not dry:
            reg = scratch_registered(run)
            say(f"{'PASS' if reg is False else 'FAIL'}  scratch marketplace '{name}' gone from the registry: "
                f"{'gone' if reg is False else 'listing failed' if reg is None else 'still listed'}")
            ok &= reg is False
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
    if not a.keep_proj and run.mkt_src.is_dir() and run.mkt_src.parent == run.root:
        shutil.rmtree(run.mkt_src)  # the commit export: reproducible from the commit; export/tree.txt is evidence
        say(f"removed commit export {run.mkt_src}")
    run.update(lambda d: d.__setitem__("cleaned", now_iso()))
    say("cleanup: PASS")
    return finish(0)


# ---------------------------------------------------------------- the MCP journey (features/mcp.md)

PANE_VAR = "PMX_INTERNAL_ACTIVATION_PANE_UID"  # the pane identity mesh/identity.py reads; presence/equality only
MCP_NAME_RE = re.compile(r"^mcp__(?P<prefix>.+)__(?P<tool>[a-z][a-z0-9_]*)$")
TOOL_DEF_RE = re.compile(r'@tool\(\s*"([a-z][a-z0-9_]*)"')
# tool-call evidence keeps these result fields only (never an item's goal, criteria, notes, messages or paths)
RESULT_KEYS = ("ok", "error.code", "error.reason", "row", "item_id", "from", "to", "noop", "applied", "plan_id",
               "stack", "exit_code", "summary.create", "summary.merge", "summary.skip", "identity.kind",
               "identity.role", "initialized", "launcher", "dispatch.sent", "dispatch.replayed",
               "dispatch.message_ref", "dispatch.attempts", "written", "changed", "proposal.id", "receipt.id",
               "receipt.outcome", "receipt.lead_notified")
INPUT_KEYS = ("item_id", "verb", "apply", "plan_id", "stack", "metrics", "role", "phase", "dry_run", "propose", "only",
              "event_id", "command", "args.attempt_id", "args.commit", "args.cls")
MESH_FILES = ("state.json", "config.toml", ".gitignore", "events.jsonl", "refusals.jsonl", "bin/mesh")
SHAPE_ARGV = (("projmux", "get", "agents"), ("projmux", "get", "panes"), ("projmux", "get", "projects"),
              ("projmux", "describe", "agent"), ("projmux", "describe", "pane"),
              ("claude", "plugin", "marketplace", "list"), ("claude", "plugin", "list"))


# json-shape presets: the read-only command and the key paths mesh/projmux.py (identity, live members, pane_command)
# and the marketplace listing are read through. Re-read them against mesh/ at the commit under test (WO-0030).
SHAPE_PRESETS = {
    "agents": (["projmux", "get", "agents", "-A", "-o", "json"],
               ["items[].metadata.uid", "items[].metadata.name", "items[].metadata.labels.role",
                "items[].spec.workspace.cwd", "items[].status.phase", "items[].status.paneRef"]),
    "agents-kind": (["projmux", "get", "agents", "-A", "-o", "json"], ["items[].status.interaction.kind"]),
    "panes": (["projmux", "get", "panes", "-A", "-o", "json"],
              ["items[].metadata.uid", "items[].status.activation.agentUID", "items[].status.activation.runtimeID",
               "items[].status.activation.claude.process.pid"]),
    "marketplaces": (["claude", "plugin", "marketplace", "list", "--json"], ["[].name", "[].source"]),
}


def dig(obj, path: str):
    for part in path.split("."):
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def scalar(v) -> str:
    if isinstance(v, list):
        return ",".join(scalar(x) for x in v if not isinstance(x, (dict, list)))[:120]
    return "-" if v is None else str(v)[:80]


def mesh_extract(run: Run) -> dict:
    """The selected fields of the scratch project's `.mesh/` state: file sha256/size, the document's counters, per
    item its phase, versions, role, history as verb:actor:from->to, attempts (commits shortened, evidence runs as
    kind/status/exit) and pending sends; event counts by type and operator events by id/seq only; refusals by
    subject/code. Never an item's goal or criteria, a history note, an argv, a refusal detail or any prompt text."""
    mesh = run.proj / ".mesh"
    files = {rel: ({"sha256": sha256_file(mesh / rel), "size": (mesh / rel).stat().st_size,
                    "executable": os.access(mesh / rel, os.X_OK)} if (mesh / rel).is_file() else None)
             for rel in MESH_FILES}
    doc = jload((mesh / "state.json").read_text()) if (mesh / "state.json").is_file() else None
    out = {"taken": now_iso(), "files": files, "doc": None, "items": {}, "events": {}, "operator_events": [],
           "refusals": []}
    if isinstance(doc, dict):
        out["doc"] = {k: doc.get(k) for k in ("schema_version", "seq", "next_id", "last_operator_seq")}
        out["doc"].update({f"{k}_count": len(doc.get(k) or {}) for k in ("items", "legacy", "event_uses", "proposals")})
        for iid, it in sorted((doc.get("items") or {}).items()):
            c = it.get("contract") or {}
            out["items"][iid] = {
                "phase": it.get("phase"), "state": it.get("state"), "blocked_reason": it.get("blocked_reason"),
                "version": it.get("version"), "contract_version": c.get("version"), "role": c.get("role"),
                "type": c.get("type"), "depends": c.get("depends"),
                "history": [f"{h.get('verb') or h.get('row')}:{h.get('actor')}:{h.get('from_phase')}->"
                            f"{h.get('to_phase')}" for h in it.get("history") or []],
                "attempts": [{"id": x.get("id"), "base_commit": str(x.get("base_commit"))[:12],
                              "commit": str(x.get("commit"))[:12], "contract_version": x.get("contract_version"),
                              "config_hash": str(x.get("config_hash"))[:12],
                              "evidence": None if not x.get("evidence") else {
                                  "kind": x["evidence"].get("kind"), "commit": str(x["evidence"].get("commit"))[:12],
                                  "runs": [f"{r.get('kind')}:{r.get('status')}:{r.get('exit_code')}"
                                           for r in x["evidence"].get("runs") or []]}}
                             for x in it.get("attempts") or []],
                "pending_actions": [{"kind": x.get("kind"), "done": x.get("done"),
                                     "message_ref": (x.get("payload") or {}).get("message_ref"),
                                     "attempts": (x.get("payload") or {}).get("attempts")}
                                    for x in it.get("pending_actions") or []],
                "failures": [f"{f.get('cls')}:{f.get('reason')}:{f.get('actor')}" for f in it.get("failures") or []]}
    for rel, key in (("events.jsonl", "events"), ("refusals.jsonl", "refusals")):
        path = mesh / rel
        for line in path.read_text(errors="replace").splitlines() if path.is_file() else []:
            r = jload(line)
            if not isinstance(r, dict):
                continue
            if key == "refusals":
                out["refusals"].append(f"{r.get('subject')}:{r.get('code')}:{r.get('caller_kind')}:{r.get('item_id')}")
                continue
            kind = str(r.get("type"))
            out["events"][kind] = out["events"].get(kind, 0) + 1
            if kind == "operator":
                out["operator_events"].append({"id": r.get("id"), "seq": r.get("seq"),
                                               "from_pm": r.get("agent_uid") == run.data.get("pm")})
    return out


def cmd_state_check(a) -> int:
    run = Run.load(a.run)
    ext = mesh_extract(run)
    sdir = run.ev / "state"
    sdir.mkdir(exist_ok=True)
    (sdir / f"{slug(a.label)}.json").write_text(json.dumps(ext, indent=1))
    st = ext["files"]["state.json"]
    checks = [(".mesh/state.json present and a mesh document", bool(st) and ext["doc"] is not None,
               f"sha256 {st['sha256'][:16]} size {st['size']}" if st else "missing")]
    item = ext["items"].get(a.item) if a.item else None
    if a.item:
        checks.append((f"item {a.item} recorded", item is not None, f"{len(ext['items'])} item(s)"))
    if item and a.expect_phase:
        checks.append((f"item {a.item} phase == {a.expect_phase}", item["phase"] == a.expect_phase, item["phase"]))
    if item and a.expect_verbs:
        want, got = [v for v in a.expect_verbs.split(",") if v], [h.split(":")[0] for h in item["history"]]
        it = iter(got)
        checks.append((f"item {a.item} history holds {want} in order", all(v in it for v in want), str(got)))
    if item and a.expect_evidence_pass:
        att = (item["attempts"] or [None])[-1]
        ev = (att or {}).get("evidence") or {}
        runs = ev.get("runs") or []
        checks.append((f"item {a.item} last attempt: evidence at its commit, every run passed", bool(runs)
                       and all(":pass:" in r.lower() for r in runs) and ev.get("commit") == (att or {}).get("commit"),
                       f"commit {(att or {}).get('commit')} evidence {ev.get('commit')} runs {runs}"))
    if a.baseline:
        base = jload((sdir / f"{slug(a.baseline)}.json").read_text()) if (sdir / f"{slug(a.baseline)}.json").is_file() \
            else None
        if base is None:
            die(f"no state-check {a.baseline} (run: smv state-check --label {a.baseline})")
        delta = len(ext["operator_events"]) - len(base["operator_events"])
        typed = sum(1 for line in ((run.ev / "sends.log").read_text().splitlines()
                                   if (run.ev / "sends.log").is_file() else [])
                    if line.split("\t")[1:2] == [f"uid:{run.data['pm']}"] and "\tTYPE " in line
                    and (parse_ts(line.split("\t")[0]) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
                    >= (parse_ts(base["taken"]) or dt.datetime.max.replace(tzinfo=dt.timezone.utc)))
        hook_err = ext["events"].get("hook_error", 0) - base["events"].get("hook_error", 0)
        checks.append(("operator events since the baseline (info: lines smv typed into the PM pane meanwhile)", True,
                       f"+{delta} operator, +{hook_err} hook_error, typed {typed}"))
        if a.expect_operator_delta is not None:
            checks.append((f"operator events since {a.baseline} == {a.expect_operator_delta}",
                           delta == a.expect_operator_delta, f"+{delta}"))
    if a.expect_operator_from_pm:
        checks.append(("every operator event was recorded from the PM pane", bool(ext["operator_events"]) and all(
            e["from_pm"] for e in ext["operator_events"]), f"{len(ext['operator_events'])} event(s)"))
    print(f"state-check {a.label}: items {json.dumps({k: v['phase'] for k, v in ext['items'].items()})} events "
          f"{ext['events']} refusals {len(ext['refusals'])}; extract: state/{slug(a.label)}.json")
    return print_checks(f"state-check {a.label}", checks, run)


def cmd_mesh_config(a) -> int:
    """The operator's hand edit of .mesh/config.toml (the D96 deny rules keep the PM's file tools off .mesh/): one
    `[[verify.commands]]` entry, `test -f <file>`, appended once. The file must exist (mesh_init wrote it)."""
    run = Run.load(a.run)
    why = allow_refusal(run, f"ADDED {a.verify_file}")
    if why:
        die(f"--verify-file refused: {why}", 2)
    cfg = run.proj / ".mesh/config.toml"
    if not cfg.is_file():
        die("no .mesh/config.toml in the scratch project: apply mesh_init first", 2)
    text = cfg.read_text()
    if re.search(r"^\s*\[\[verify\.commands\]\]", text, re.M):
        die(".mesh/config.toml already has a [[verify.commands]] entry; smv adds exactly one", 2)
    before = sha256_file(cfg)
    rel = Path(a.verify_file).as_posix()
    block = ("\n# added by smv mesh-config: the operator's hand edit (the PM's file tools are denied on .mesh/)\n"
             f"[[verify.commands]]\nargv = [\"test\", \"-f\", {json.dumps(rel)}]\nkind = \"test\"\n"
             f"timeout_s = {a.timeout_s}\n")
    cfg.write_text(text + ("" if text.endswith("\n") else "\n") + block)
    parsed = "tomllib not available"
    try:
        import tomllib
        cmds = (tomllib.loads(cfg.read_text()).get("verify") or {}).get("commands") or []
        parsed = f"{len(cmds)} verify command(s), argv {cmds[0].get('argv') if cmds else None}"
        ok = len(cmds) == 1 and cmds[0].get("argv") == ["test", "-f", rel]
    except ImportError:
        ok = True
    except ValueError as exc:
        parsed, ok = f"does not parse: {exc}", False
    run.note(f"mesh-config: .mesh/config.toml {before[:12]} -> {sha256_file(cfg)[:12]} (+ verify: test -f {rel})")
    return print_checks("mesh-config", [(".mesh/config.toml gains one verify command and parses", ok, parsed)], run)


def tool_names(src: Path) -> list[str]:
    """The tool names the export's mesh/tools.py registers (@tool("name", ...)), read as text: never imported."""
    p = src / "mesh/tools.py"
    return TOOL_DEF_RE.findall(p.read_text()) if p.is_file() else []


def flat_pick(obj, keys: tuple[str, ...]) -> dict:
    return {k: scalar(v) for k in keys if (v := dig(obj, k)) is not None}


def mcp_calls(path: Path, since: dt.datetime | None) -> list[dict]:
    """Every supervisor-mesh MCP tool call in a transcript with its result, correlated by tool_use_id: the prefix
    and tool name, the whitelisted inputs, and the whitelisted fields of the result's JSON (plus the last attempt's
    id and commit and its evidence runs, from the returned item). No other part of a result is kept."""
    calls, by_id = [], {}
    for raw in path.read_text(errors="replace").splitlines():
        rec = jload(raw)
        if not isinstance(rec, dict) or rec.get("type") not in ("user", "assistant"):
            continue
        ts = rec.get("timestamp") or "-"
        if since and ((t := parse_ts(rec.get("timestamp"))) is None or t < since):
            continue
        content = (rec.get("message") or {}).get("content")
        for c in (x for x in (content if isinstance(content, list) else []) if isinstance(x, dict)):
            if rec["type"] == "assistant" and c.get("type") == "tool_use":
                m = MCP_NAME_RE.match(str(c.get("name")))
                if m and "supervisor-mesh" in m.group("prefix"):
                    call = {"ts": ts, "id": c.get("id"), "prefix": f"mcp__{m.group('prefix')}__",
                            "tool": m.group("tool"), "input": flat_pick(c.get("input") or {}, INPUT_KEYS),
                            "result": None}
                    calls.append(call)
                    by_id[c.get("id")] = call
            elif rec["type"] == "user" and c.get("type") == "tool_result" and c.get("tool_use_id") in by_id:
                call = by_id[c["tool_use_id"]]
                data = next((d for d in (jload(t) for t in texts(c.get("content"))) if isinstance(d, dict)), None)
                res = flat_pick(data or {}, RESULT_KEYS)
                atts = dig(data or {}, "item.attempts") or []
                if atts and isinstance(atts[-1], dict):
                    res["attempt_id"] = scalar(atts[-1].get("id"))
                    res["attempt_commit"] = str(atts[-1].get("commit"))[:12]
                    runs = (atts[-1].get("evidence") or {}).get("runs") or []
                    if runs:
                        res["evidence"] = ",".join(f"{r.get('kind')}:{r.get('status')}:{r.get('exit_code')}"
                                                   for r in runs if isinstance(r, dict))
                call["result"] = {"is_error": bool(c.get("is_error")) or (data or {}).get("ok") is False,
                                  "parsed": data is not None, "fields": res}
    return calls


def field_ok(value: str | None, want: str) -> bool:
    """`want` '' or '*': present and non-empty; otherwise equal (as the extract shows it)."""
    if want in ("", "*"):
        return value not in (None, "", "-", "None")
    return value == want


def mesh_init_checks(all_calls: list[dict], recent: list[dict], mode: str) -> list[tuple[str, bool, str]]:
    """F9: a mesh_init call is proof only with the right fields. dry-run: applied=False, a non-empty plan_id,
    exit_code 0. apply: applied=True, exit_code 0, and both the call's plan_id argument and the result's plan_id equal
    the plan_id of the last good dry run before it (anywhere in the transcript)."""
    def ok(c, applied):
        f = (c["result"] or {}).get("fields", {})
        return (c["result"] is not None and not c["result"]["is_error"] and f.get("applied") == applied
                and f.get("exit_code") == "0" and field_ok(f.get("plan_id"), "*"))
    mine = [c for c in recent if c["tool"] == "mesh_init"]
    if mode == "dry-run":
        good = [c for c in mine if ok(c, "False")]
        return [("mesh_init dry run: applied=False, non-empty plan_id, exit_code 0", bool(good),
                 str([(c["result"] or {}).get("fields") for c in mine]))]
    out = []
    for c in (x for x in mine if ok(x, "True")):
        before = [d for d in all_calls if d["tool"] == "mesh_init" and d["ts"] <= c["ts"] and ok(d, "False")]
        plan = before[-1]["result"]["fields"]["plan_id"] if before else None
        if plan and c["input"].get("plan_id") == plan == c["result"]["fields"].get("plan_id"):
            out.append(c)
    return [("mesh_init apply: applied=True, exit_code 0, plan_id == the dry run's (argument and result)", bool(out),
             str([((c["result"] or {}).get("fields"), c["input"].get("plan_id")) for c in mine]))]


def call_line(c: dict) -> str:
    r = c["result"]
    outcome = "no result" if r is None else ("error" if r["is_error"] else "ok") + ("" if r["parsed"] else " (raw)")
    fields = " ".join(f"{k}={v}" for k, v in (r or {}).get("fields", {}).items())
    args = " ".join(f"{k}={v}" for k, v in c["input"].items())
    return f"{c['ts']} {c['tool']} [{c['id']}] args{{{args}}} -> {outcome} {fields}".rstrip()


def cmd_mcp_calls(a) -> int:
    run = Run.load(a.run)
    agent = run.resolve_agent(a.agent)
    path = transcript_source(run, agent)
    if not path:
        print(f"mcp-calls uid:{agent}: no transcript found")
        return 1
    calls = mcp_calls(path, since_mark(run, a.since_last_send))
    lines = [call_line(c) for c in calls]
    prefixes = sorted({c["prefix"] for c in calls})
    checks = [("supervisor-mesh MCP tool calls in the transcript", bool(calls), f"{len(calls)} call(s)"),
              ("one tool-name prefix for every call (as Claude shows it)", len(prefixes) == 1, str(prefixes))]
    if a.prefix:
        checks.append((f"prefix == {a.prefix}", prefixes == [a.prefix], str(prefixes)))
    for spec in a.expect:
        tool, _, want = spec.partition("=")
        want = want or "ok"
        got = [c for c in calls if c["tool"] == tool and c["result"] is not None
               and (want == "any" or c["result"]["is_error"] == (want == "error"))]
        checks.append((f"{tool}: at least one call with a {want} result (correlated by tool_use_id)", bool(got),
                       f"{len(got)} of {sum(c['tool'] == tool for c in calls)}"))
    for spec in a.expect_field:
        tool, _, kv = spec.partition(":")
        key, _, want = kv.partition("=")
        got = [c for c in calls if c["tool"] == tool and c["result"] is not None and field_ok(
            c["result"]["fields"].get(key), want)]
        checks.append((f"{tool}: a result with {key}={want or '*'}", bool(got),
                       f"seen {[c['result']['fields'].get(key) for c in calls if c['tool'] == tool and c['result']]}"))
    if a.mesh_init:
        checks += mesh_init_checks(mcp_calls(path, None) if a.since_last_send else calls, calls, a.mesh_init)
    if a.all_tools:
        names = tool_names(run.mkt_src if run.mkt_src.is_dir() else run.repo)
        seen = {c["tool"] for c in calls if c["result"] is not None}
        missing = [t for t in names if t not in seen]
        checks.append((f"every tool the export registers answered at least once ({len(names)})", bool(names)
                       and not missing, f"missing {missing}" if missing else ", ".join(names)))
    kept, dropped = record_transcript(run, agent, "mcp-calls", lines)
    text = "".join(f"{x}\n" for x in kept) + f"# lines dropped by redaction: {dropped}\n"
    (run.ev / "steps" / f"{run.next_id('mcp-calls-extract')}.txt").write_text(text)
    print(text, end="")
    return print_checks("mcp-calls", checks, run)


def cmd_mcp_check(a) -> int:
    """`/mcp` lists the supervisor-mesh server as connected: read the current screen, press nothing."""
    run = Run.load(a.run)
    agent = run.resolve_agent(a.agent)
    text = run.capture(run.pane(agent, wait=10))
    rows = mcp_rows(text)
    path = run.save_pane("mcp-dialog", text, "match", re.compile(r"supervisor-mesh|MCP|[Ss]erver", re.I))
    checks = [("the screen is the /mcp dialog (no input box)", screen_state(text) != "ready", screen_state(text)),
              ("a supervisor-mesh line shows connected", any(s == "connected" for _, _, s in rows),
               f"{[s for _, _, s in rows]}"),
              ("no supervisor-mesh line shows a failure", not any(s == "failed" for _, _, s in rows),
               f"capture {path.name}")]
    return print_checks("mcp-check", checks, run)


def proc_children(proc: Path) -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for d in proc.iterdir() if proc.is_dir() else []:
        if d.name.isdigit():
            try:
                ppid = int((d / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            kids.setdefault(ppid, []).append(int(d.name))
    return kids


def proc_argv(proc: Path, pid: int) -> list[str]:
    try:
        return [x for x in (proc / str(pid) / "cmdline").read_bytes().decode(errors="replace").split("\0") if x]
    except OSError:
        return []


def proc_env_digest(proc: Path, pid: int, var: str, key: bytes) -> str | None:
    """A keyed digest of one variable of a process's environment, or None when it is absent. The environment is
    read in memory only and never written or printed."""
    try:
        raw = (proc / str(pid) / "environ").read_bytes()
    except OSError:
        return None
    for item in raw.split(b"\0"):
        name, sep, value = item.partition(b"=")
        if sep and name.decode(errors="replace") == var:
            return hmac.new(key, value, "sha256").hexdigest()
    return None


def cmd_mcp_env(a) -> int:
    """Live check (a): the plugin-declared MCP server process under this pane received the pane identity variable,
    equal to its claude process's and to the pane uid projmux reports. Presence and equality only, through keyed
    digests in memory; no environment value is ever written. Also where the server's script runs from."""
    run = Run.load(a.run)
    agent = run.resolve_agent(a.agent)
    proc = Path(a.proc_root)
    tmux_id = run.pane(agent, wait=10)
    code, out, _ = sh(["tmux", *run.socket_args(), "display-message", "-p", "-t", tmux_id, "#{pane_pid}"])
    pane_pid = int(out.strip()) if code == 0 and out.strip().isdigit() else None
    key = os.urandom(32)
    kids, servers, todo = proc_children(proc), [], [pane_pid] if pane_pid else []
    parent: dict[int, int] = {}
    while todo:
        pid = todo.pop()
        for k in kids.get(pid, []):
            parent[k] = pid
            todo.append(k)
            argv = proc_argv(proc, k)
            if any(x.endswith("mesh/mcp_server.py") for x in argv):
                servers.append(k)

    def claude_of(pid: int) -> int | None:
        while pid in parent:
            pid = parent[pid]
            if any(Path(x).name == "claude" or x.endswith("/claude") for x in proc_argv(proc, pid)[:2]):
                return pid
        return None

    pane_uid = (run.data.get("panes", {}).get(agent) or {}).get("pane_uid") or ""
    want = hmac.new(key, pane_uid.encode(), "sha256").hexdigest()
    rows = []
    install = next((r.get("installPath") for r in listing(["claude", "plugin", "list", "--json"], run.proj)
                    if r.get("id") == run.plugin_id and same_path(r.get("projectPath"), run.proj)), None)
    for pid in servers:
        script = next(x for x in proc_argv(proc, pid) if x.endswith("mesh/mcp_server.py"))
        where = ("installed copy" if install and within(Path(script), Path(install)) else
                 "commit export" if within(Path(script), run.mkt_src) else
                 "checkout" if within(Path(script), run.repo) else "other")
        srv, cl = proc_env_digest(proc, pid, PANE_VAR, key), proc_env_digest(proc, claude_of(pid) or -1, PANE_VAR, key)
        try:
            cwd_ok = same_path(os.readlink(proc / str(pid) / "cwd"), run.proj)
        except OSError:
            cwd_ok = False
        row = {"present": srv is not None, "equal_to_claude": srv is not None and srv == cl,
               "equal_to_pane_uid": srv == want, "cwd_is_project": cwd_ok, "script": where}
        rows.append(row)
    checks = [("pane pid resolved", pane_pid is not None, "tmux #{pane_pid}"),
              ("a mesh/mcp_server.py process under this pane", bool(servers), f"{len(servers)} process(es)")]
    for n, row in enumerate(rows):
        checks += [(f"server {n}: {PANE_VAR} present", row["present"], "presence only"),
                   (f"server {n}: equal to its claude process's", row["equal_to_claude"], "keyed digest, in memory"),
                   (f"server {n}: equal to the pane uid projmux reports", row["equal_to_pane_uid"], "keyed digest"),
                   (f"server {n}: cwd is the scratch project", row["cwd_is_project"], "realpath"),
                   (f"server {n}: script location (info)", True, row["script"])]
    run.update(lambda d: d.setdefault("mcp_env", {}).__setitem__(agent, rows))
    return print_checks("mcp-env", checks, run)


def key_paths(obj, path: str = "") -> dict[str, set[str]]:
    """{key path: value types} with list items merged under `[]`; never a value."""
    out: dict[str, set[str]] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            for p, t in key_paths(v, f"{path}.{k}" if path else str(k)).items():
                out.setdefault(p, set()).update(t)
        out.setdefault(path or "(root)", set()).add("object")
    elif isinstance(obj, list):
        for v in obj:
            for p, t in key_paths(v, f"{path}[]").items():
                out.setdefault(p, set()).update(t)
        out.setdefault(path or "(root)", set()).add("array")
    else:
        out[path or "(root)"] = {type(obj).__name__}
    return out


def cmd_json_shape(a) -> int:
    """Live checks (b) and (d): run one listed read-only JSON command and keep its key paths and value types only,
    then require each --expect path. Values never reach the evidence."""
    run = Run.load(a.run)
    argv = [x for x in a.argv if x != "--"]
    if a.preset:
        if argv:
            die("json-shape: --preset takes no command", 2)
        argv, preset = SHAPE_PRESETS[a.preset]
        a.expect = [*preset, *a.expect]
        a.label = a.label or a.preset
    if not a.label:
        die("json-shape: --label or --preset", 2)
    if not any(tuple(argv[:len(p)]) == p for p in SHAPE_ARGV) or not {"json", "--json"} & set(argv):
        die(f"json-shape runs only {[' '.join(p) for p in SHAPE_ARGV]} with a JSON output flag", 2)
    if any(x.startswith("~") or "$" in x for x in argv):
        die("json-shape: no '~' or '$' in arguments", 2)
    code, out, err = sh(argv, cwd=run.proj)
    data = jload(out)
    paths = key_paths(data) if data is not None else {}
    lines = [f"{p}\t{'|'.join(sorted(t))}" for p, t in sorted(paths.items())]
    missing = [p for p in a.expect if p not in paths]
    (run.ev / "steps" / f"{run.next_id('shape-' + a.label)}.txt").write_text(
        f"$ {' '.join(argv)}   (key paths and value types only)\n# exit: {code}\n" + "".join(f"{x}\n" for x in lines))
    checks = [(f"{' '.join(argv[:4])} exited 0 with JSON", code == 0 and data is not None, f"exit {code}"),
              ("every expected key path present", not missing, f"missing {missing}" if missing
               else f"{len(a.expect)} expected, {len(paths)} seen")]
    return print_checks(f"json-shape {a.label}", checks, run)


def cmd_msg_probe(a) -> int:
    """Live check (c): send one probe text N times with the SAME --message-ref from one recorded agent to another,
    then count how many user records of the target's transcript carry the probe's marker (deliveries). projmux
    deduplicating on the ref means one delivery. The text names no tool and asks for nothing."""
    run = Run.load(a.run)
    target, source = run.resolve_agent(a.to), run.resolve_agent(a.source)
    if target == source:
        die("msg-probe: --to and --source must be two different recorded agents", 2)
    marker = f"SMV-PROBE-{run.data['step']:03d}-{os.urandom(4).hex()}"
    ref = f"smv-probe-{marker.lower()}"
    text = f"smv delivery probe {marker}. No action needed: reply with the single word ack."
    first = dt.datetime.now(dt.timezone.utc)
    receipts = []
    for n in range(a.times):
        code, out, err = run.cmd(f"probe-send-{n + 1}", ["projmux", "agent", "message", "send", f"uid:{target}",
                                                         "--source", f"uid:{source}", "--message-ref", ref, "--",
                                                         text])
        line = next((x for x in (out + err).splitlines() if x.startswith(ref)), "")
        receipts.append(f"send {n + 1}: exit {code} receipt {'/'.join(line.split(chr(9))[1:3]) or '-'}")
    def deliveries() -> int:
        path = transcript_source(run, target)
        return sum(1 for raw in (path.read_text(errors="replace").splitlines() if path else [])
                   if isinstance(r := jload(raw), dict) and r.get("type") == "user"
                   and (parse_ts(r.get("timestamp")) or first) >= first and marker in json.dumps(r.get("message")))

    deadline = time.time() + a.wait
    while deliveries() < a.expect_deliveries and time.time() < deadline:
        time.sleep(min(3, a.settle or 3))
    time.sleep(a.settle)  # room for a duplicate delivery to arrive before the count is final
    count = deliveries()
    record_transcript(run, target, "msg-probe", [f"probe {marker}: {count} delivery record(s)"])
    checks = [*((r, True, "info") for r in receipts),
              (f"deliveries of one message-ref == {a.expect_deliveries}", count == a.expect_deliveries,
               f"{count} user record(s) carry the marker")]
    run.update(lambda d: d.setdefault("probes", []).append({"marker": marker, "ref": ref, "to": target,
                                                            "from": source, "sends": a.times, "deliveries": count,
                                                            "at": now_iso()}))
    return print_checks("msg-probe", checks, run)


# ---------------------------------------------------------------- offline self-tests (tests/, stubs only)

SKILL_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = SKILL_DIR / "tests"


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
    p.add_argument("--pm-role", action="store_true", help="label the PM pane role=pm (needed by the mesh MCP tools)")
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
    p.add_argument("--allow-file", help="a 'KIND PATH' per line list inside the skill dir (features/*.allow)")
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
    p = add("mkt-project", cmd_mkt_project, "export a commit as a scratch-named marketplace and declare it in the "
            "scratch project (CLI, --scope project)")
    p.add_argument("--commit", default="HEAD", help="the commit under test (default HEAD); one per run")
    p = add("scope-pick", cmd_scope_pick, "after /plugin install: highlight the exact project-scope option, verify, "
            "Enter", agent=True)
    p.add_argument("--max-moves", type=int, default=6)
    p.add_argument("--timeout", type=int, default=120, help="seconds to wait for the prompt after Enter")
    add("plugin-check", cmd_plugin_check, "assert the project-scope install from this checkout")
    p = add("state-check", cmd_state_check, "extract .mesh/state.json (hashed, selected fields) and check it")
    p.add_argument("--label", required=True)
    p.add_argument("--item", help="an item id to check")
    p.add_argument("--expect-phase")
    p.add_argument("--expect-verbs", help="comma list the item's history must hold in order (e.g. new,ready,start)")
    p.add_argument("--expect-evidence-pass", action="store_true", help="the last attempt's runs all passed")
    p.add_argument("--baseline", help="an earlier state-check label: report the operator-event delta")
    p.add_argument("--expect-operator-delta", type=int)
    p.add_argument("--expect-operator-from-pm", action="store_true")
    p = add("mesh-config", cmd_mesh_config, "the operator's hand edit: add one verify command to .mesh/config.toml")
    p.add_argument("--verify-file", required=True, help="relative path the verify command tests with test -f")
    p.add_argument("--timeout-s", type=int, default=60)
    p = add("mcp-check", cmd_mcp_check, "read the /mcp dialog on screen: supervisor-mesh connected (no key pressed)",
            agent=True)
    p = add("mcp-calls", cmd_mcp_calls, "supervisor-mesh MCP tool calls and results from the transcript (by id)",
            agent=True)
    p.add_argument("--since-last-send", action="store_true")
    p.add_argument("--expect", action="append", default=[], metavar="TOOL[=ok|error|any]")
    p.add_argument("--all-tools", action="store_true", help="every tool the export registers answered once")
    p.add_argument("--prefix", help="the tool-name prefix every call must carry")
    p.add_argument("--expect-field", action="append", default=[], metavar="TOOL:KEY=VALUE",
                   help="a call of TOOL whose result field KEY equals VALUE ('*' or empty: non-empty)")
    p.add_argument("--mesh-init", choices=["dry-run", "apply"], help="the mesh_init field checks (F9)")
    p = add("mcp-env", cmd_mcp_env, "the MCP server under the pane got the pane identity env (presence/equality)",
            agent=True)
    p.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    p = add("json-shape", cmd_json_shape, "key paths and types of one read-only JSON command (never values)")
    p.add_argument("--label")
    p.add_argument("--preset", choices=sorted(SHAPE_PRESETS), help="a listed command with its expected key paths")
    p.add_argument("--expect", action="append", default=[], metavar="KEY.PATH")
    p.add_argument("argv", nargs=argparse.REMAINDER)
    p = add("msg-probe", cmd_msg_probe, "send one probe N times with one --message-ref; count deliveries")
    p.add_argument("--to", required=True, help="pm | role:<role> | <recorded uid>")
    p.add_argument("--source", required=True, help="pm | role:<role> | <recorded uid>")
    p.add_argument("--times", type=int, default=2)
    p.add_argument("--expect-deliveries", type=int, default=1)
    p.add_argument("--wait", type=int, default=120)
    p.add_argument("--settle", type=int, default=20)
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
