"""Fake `projmux`, `tmux` and `claude` executables for the mesh adapter tests. Never the real tools.

`Fakes(tmp)` writes one small Python script into `<tmp>/bin` under each tool name. The script reads a JSON state file
(agents, panes, screens, interaction kinds, scripted exit codes), answers the argv it understands, and appends every
argv it receives to a JSONL log the test asserts on. tmux keeps an input box per pane: `send-keys -l -- <text>` types
into it, `send-keys C-u` empties it, `send-keys Enter` submits it (the box empties and the text is recorded),
`capture-pane` renders the pane's screen with the box filled in. With `render_after = N` the typed text reaches the box
only on the (N+1)-th capture after it was typed (D125: the slash menu renders first).
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

RULE = "─" * 60

FAKE = r"""#!__PYTHON__
import json, os, sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
state_p = Path(os.environ["MESH_FAKE_STATE"])
st = json.loads(state_p.read_text())
with open(os.environ["MESH_FAKE_LOG"], "a") as log:
    log.write(json.dumps({"tool": tool, "args": args}) + "\n")


def out(value, code=0, err=""):
    if value is not None:
        sys.stdout.write(value if isinstance(value, str) else json.dumps(value))
        sys.stdout.write("\n")
    if err:
        sys.stderr.write(err + "\n")
    sys.exit(code)


def save():
    state_p.write_text(json.dumps(st))


scripted = st.get("exit", {}).get(tool + " " + " ".join(args[:3]))
if scripted is not None:
    out(None, scripted, "scripted failure")

if tool == "projmux":
    if args[:2] == ["get", "agents"]:
        out(st.get("agents_raw") if "agents_raw" in st else {"items": st.get("agents", [])})
    if args[:2] == ["get", "panes"]:
        out({"items": st.get("panes", [])})
    if args[:2] == ["describe", "agent"]:  # D118: the agent record, with status.sessionRef when a test set one
        uid = args[2].removeprefix("uid:")
        found = next((a for a in st.get("agents", []) if a["metadata"]["uid"] == uid), None)
        if found is None:
            out(None, 1, "fake projmux: no such agent")
        ref = st.get("sessions", {}).get(uid)
        status = {**found["status"], **({"sessionRef": {"claude": ref}} if ref else {})}
        out({**found, "status": status})
    if args[:3] == ["agent", "status", "get"]:
        kind = st.get("interaction", {}).get(args[3].removeprefix("uid:"))
        out(f"{kind} observedAt=2026-11-18T00:00:00Z lifecycle=Running source=hook" if kind else "", 0 if kind else 1)
    if args[:3] == ["agent", "message", "send"]:
        peek = os.environ.get("MESH_FAKE_PEEK")  # what a file held at send time (send-after-commit proofs)
        st.setdefault("messages", []).append({"args": args, "peek": Path(peek).read_text() if peek else None})
        plan = st.get("send_plan")  # D93: scripted receipts, one [exit code, reason] per send, in order
        if plan:
            code, reason = plan.pop(0)
        else:
            code, reason = st.get("send_code", 0), "refused"
        save()
        ref = args[args.index("--message-ref") + 1] if "--message-ref" in args else "-"
        out(f"{ref}\tdelivered" if code == 0 else f"{ref}\trefused\t{reason}\tnone", code)
    out(None, 2, "fake projmux: unsupported")

if tool == "tmux":
    if "list-clients" in args:
        out(st.get("tmux_client", "/dev/pts/9"))
    if "display-popup" in args:  # D113: play the operator at the popup (it runs the helper; never logs the code)
        mode = st.get("popup", "none")  # a test that never set it fails fast (refused), never waits
        if mode != "never":
            import re, shlex, subprocess
            popup_env = dict(os.environ)  # D121: `-e K=V` sets the popup's env (the work dir), never the argv
            for i, a in enumerate(args[:-1]):
                if a == "-e":
                    k, _, v = args[i + 1].partition("=")
                    popup_env[k] = v
            helper = subprocess.Popen(
                shlex.split(args[-1]), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=popup_env
            )
            code = None
            while code is None:
                line = helper.stdout.readline()
                if not line:
                    break
                m = re.search(r"code: (\S+)", line)
                code = m.group(1) if m else None
            if code:  # what the operator read off the popup: kept for the test, outside .mesh/ and the log
                with open(state_p.parent / "popup-seen", "a") as fh:
                    fh.write(code + "\n")
            answer = {"right": code or "", "wrong": "WRONGCODE", "none": ""}[mode]
            helper.communicate(answer + "\n", timeout=30)
        out("")
    if "capture-pane" in args:
        pane = args[args.index("-t") + 1]
        p = st["tmux"][pane]
        if p.get("next_screens"):  # a screen that changes between two captures
            p["screen"] = p["next_screens"].pop(0)
            save()
        if p.get("pending") is not None:  # D125: typed text shows only after `render_after` more captures
            if p.get("render_after", 0) <= 0:
                p["box"] = p.get("box", "") + p.pop("pending")
            else:
                p["render_after"] -= 1
            save()
        out(p["screen"].replace("{box}", p.get("box", "")))
    if "send-keys" in args:
        pane = args[args.index("-t") + 1]
        p = st["tmux"][pane]
        if "-l" in args:
            text = args[args.index("--") + 1]
            if p.get("render_after") is not None:
                p["pending"] = text
            elif not p.get("swallow"):
                p["box"] = p.get("box", "") + text
        elif args[-1] == "C-u":
            p["box"] = ""
        elif args[-1] == "Enter":
            p.setdefault("submitted", []).append(p.get("box", ""))
            p["box"] = ""
            if p.get("after_enter"):
                p["screen"] = p["after_enter"]
        save()
        out("")
    out(None, 2, "fake tmux: unsupported")

if tool == "claude":
    if args[:3] == ["plugin", "marketplace", "list"]:
        out(st.get("claude_marketplaces", []))
    if args[:2] == ["plugin", "install"]:  # registers the plugin at project scope for the cwd, like the real CLI
        reg = Path(os.environ["HOME"]) / ".claude" / "plugins" / "installed_plugins.json"
        data = json.loads(reg.read_text()) if reg.is_file() else {"version": 2, "plugins": {}}
        name = args[2].split("@", 1)[0]
        version = st.get("claude_versions", {}).get(name, "1.0.0")
        data["plugins"].setdefault(args[2], []).append(
            {"scope": "project", "projectPath": os.getcwd(), "version": version, "gitCommitSha": "ab" * 20}
        )
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps(data))
        out("installed")
    out(st.get("claude_out", ""), st.get("claude_code", 0))

out(None, 2, "fake: unknown tool")
"""


def ready_screen(*above: str) -> str:
    """An idle Claude Code screen: transcript lines, then the empty input box with a `{box}` slot, then the footer."""
    return "\n".join([" Claude Code", "", *above, "", RULE, "❯ {box}", RULE, "  ? for shortcuts"])


BUSY_SCREEN = ready_screen("✶ Thinking… (esc to interrupt)")
TRUST_SCREEN = "\n".join(
    [
        " Accessing workspace:",
        "",
        " Quick safety check: Is this a project you trust?",
        "",
        " ❯ 1. Yes, I trust this folder",
        "   2. No, exit",
        "",
        " Enter to confirm · Esc to cancel",
    ]
)
PERMISSION_SCREEN = "\n".join(
    [
        " Bash command",
        "   rm -rf build",
        "",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. Yes, and don't ask again",
        "   3. No",
        "",
        " Esc to cancel",
    ]
)


def parse_caller(spec: str | None):
    """A test's caller shorthand, "pm[:<agent uid>]", "lead:<role>[:<agent uid>]" or "unknown", as a `Caller`. For
    in-process injection (`Context.for_project(..., caller=...)`); shipped code reads no such variable (F1)."""
    from mesh.model import ROLE_PATTERN, Caller

    kind, _, rest = (spec or "").partition(":")
    if kind == "pm":
        return Caller.pm(rest or None)
    if kind == "lead":
        role, _, uid = rest.partition(":")
        if re.fullmatch(ROLE_PATTERN, role):
            return Caller.lead(role, uid or None)
    return Caller.unknown()


class Fakes:
    """One temp bin dir with the three fakes, one state file, one argv log."""

    def __init__(self, tmp: Path):
        self.dir = Path(tmp)
        self.bin = self.dir / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.state_p = self.dir / "fake-state.json"
        self.log_p = self.dir / "fake-log.jsonl"
        self.state: dict = {"agents": [], "panes": [], "tmux": {}, "interaction": {}}
        script = FAKE.replace("__PYTHON__", sys.executable)
        for name in ("projmux", "tmux", "claude"):
            path = self.bin / name
            path.write_text(script, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.save()

    def env(self, base: dict | None = None, **extra: str) -> dict:
        env = dict(base if base is not None else os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env["MESH_FAKE_STATE"] = str(self.state_p)
        env["MESH_FAKE_LOG"] = str(self.log_p)
        env.update(extra)
        return env

    def save(self) -> None:
        self.state_p.write_text(json.dumps(self.state), encoding="utf-8")

    def set(self, **changes) -> None:
        """Change scripted state WITHOUT dropping what the fakes recorded since (messages, typed boxes)."""
        self.load().update(changes)
        self.save()

    def load(self) -> dict:
        self.state = json.loads(self.state_p.read_text(encoding="utf-8"))
        return self.state

    def agent(
        self,
        uid: str,
        role: str | None,
        cwd: Path,
        *,
        phase: str = "Running",
        name: str | None = None,
        pane: str | None = None,
        tmux_id: str | None = None,
        screen: str | None = None,
        interaction: str | None = "response_complete",
        claude_pid: int | None = None,
    ) -> dict:
        """A projmux agent with its pane; `tmux_id` + `screen` give the pane a tmux screen. `claude_pid` is the pane's
        claude process (status.activation.claude.process.pid); by default the test process's PARENT, an ancestor of the
        test process (an in-process production caller passes the D108 check) and of every child a test starts (so `mesh
        event record` run as a child passes the D96 parent check); 0 leaves it out."""
        pane_uid = pane or f"pane-{uid}"
        item = {
            "metadata": {"uid": uid, "name": name or f"agent-{uid}", "labels": {"role": role} if role else {}},
            "spec": {"workspace": {"cwd": str(cwd)}},
            "status": {"phase": phase, "paneRef": pane_uid},
        }
        self.state["agents"].append(item)
        activation = {"agentUID": uid}
        pid = os.getppid() if claude_pid is None else claude_pid
        if pid:
            activation["claude"] = {"process": {"pid": pid, "ownerUID": os.getuid(), "start": "0"}}
        if tmux_id:
            activation["runtimeID"] = tmux_id
            self.state["tmux"][tmux_id] = {"screen": screen or ready_screen(), "box": ""}
        self.state["panes"].append({"metadata": {"uid": pane_uid}, "status": {"activation": activation}})
        if interaction:
            self.state["interaction"][uid] = interaction
        self.save()
        return item

    def claude_session(self, uid: str, session_id: str | None, transcript_path: str | os.PathLike | None) -> None:
        """D118: what `projmux describe agent uid:<uid>` reports as the agent's live Claude session (None leaves a
        field out). Kept apart from the agent record so re-registering an agent does not drop it."""
        ref = {k: str(v) for k, v in (("sessionId", session_id), ("transcriptPath", transcript_path)) if v is not None}
        self.set(sessions={**self.load().get("sessions", {}), uid: ref})

    def caller_env(self, spec: str | None, root: Path) -> dict:
        """The environment that makes a CHILD process resolve as `spec` (see `parse_caller`) the production way: a
        live, labelled agent of `root` in the fake projmux and its pane uid in PMX_INTERNAL_ACTIVATION_PANE_UID. An
        agent with that uid already registered is reused. "unknown" (or None) adds nothing."""
        kind, _, rest = (spec or "").partition(":")
        if kind == "pm":
            role, uid = "pm", rest or "pm"
        elif kind == "lead" and rest:
            role, _, uid = rest.partition(":")
            uid = uid or f"{role}-uid"
        else:
            return {}
        have = next((a for a in self.load()["agents"] if a["metadata"]["uid"] == uid), None)
        if have is None:
            self.agent(uid, role, root)
            return {"PMX_INTERNAL_ACTIVATION_PANE_UID": f"pane-{uid}"}
        return {"PMX_INTERNAL_ACTIVATION_PANE_UID": have["status"]["paneRef"]}

    def calls(self, tool: str, *prefix: str) -> list[list[str]]:
        if not self.log_p.is_file():
            return []
        rows = [json.loads(x) for x in self.log_p.read_text(encoding="utf-8").splitlines()]
        return [r["args"] for r in rows if r["tool"] == tool and r["args"][: len(prefix)] == list(prefix)]


def work_order(root, item_id: str = "WO-0001", rel: str | None = None) -> Path:
    """D117 (4) fixture: the work-order file `work_dispatch` needs under the project root (default path)."""
    path = Path(root) / (rel or f"docs/work-orders/{item_id}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {item_id}\n", encoding="utf-8")
    return path


def pin_pm(store, uid: str = "pm-uid") -> None:
    """D109 fixture: the PM pin mesh_init apply would leave in state.json (D118: no session is pinned)."""
    from dataclasses import replace

    if not store.initialized():
        store.create()
    with store.locked() as txn:
        txn.commit(replace(txn.doc, pm_uid=uid))


def prompt_payload(home: Path, root: Path, prompt: str, session: str = "s") -> dict:
    """D110 fixture, temp dirs only: append `prompt` as the newest real user entry of the session's transcript under
    `<home>/.claude/projects/<slug>/<session>.jsonl` and return the UserPromptSubmit payload that names it."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(root))
    path = Path(home) / ".claude" / "projects" / slug / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else 0
    entry = {"type": "user", "uuid": f"u-{n + 1}", "message": {"role": "user", "content": prompt}}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session,
        "prompt": prompt,
        "transcript_path": str(path),
    }
