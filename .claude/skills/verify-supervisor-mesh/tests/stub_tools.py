#!/usr/bin/env python3
"""Offline stand-in for projmux, tmux and claude in the smv tests (installed on PATH under those three names).

It answers from the JSON state file $SMV_STUB_STATE, logs every call to $SMV_STUB_LOG, and touches nothing outside
the test directory. A tmux pane is a screen template with a `{box}` slot: `send-keys -l` types into the box, and
`Enter` / arrow keys run the actions the test scripted for that pane.
"""
import json
import os
import sys
from pathlib import Path

STATE = Path(os.environ["SMV_STUB_STATE"])


def load() -> dict:
    return json.loads(STATE.read_text())


def save(st: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    os.replace(tmp, STATE)


def out(obj, code: int = 0):
    print(obj if isinstance(obj, str) else json.dumps(obj, indent=1))
    sys.exit(code)


def failing(st: dict, name: str) -> bool:
    """Consume one scripted failure of `name` (the counter in state.fail)."""
    left = (st.get("fail") or {}).get(name, 0)
    if left:
        st["fail"][name] = left - 1
        save(st)
    return bool(left)


def arg_after(args: list[str], flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


def claude(st: dict, args: list[str]):
    if args == ["--version"]:
        out("2.1.307 (Claude Code)")
    if args == ["plugin", "--help"]:
        out("Usage: claude plugin [command]\nCommands: marketplace install uninstall list validate")
    if args[:2] == ["plugin", "list"]:
        out(st.get("plugin_rows", []))
    if args[:3] == ["plugin", "marketplace", "list"]:
        out(st.get("marketplaces", []))
    cwd = Path.cwd()
    settings = cwd / ".claude/settings.json"
    data = json.loads(settings.read_text()) if settings.is_file() else {}
    if args[:3] == ["plugin", "marketplace", "add"]:
        data.setdefault("extraKnownMarketplaces", {})["supervisor-mesh"] = {
            "source": {"source": "directory", "path": args[3]}}
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps(data, indent=1))
        if not any(m.get("name") == "supervisor-mesh" for m in st.setdefault("marketplaces", [])):
            st["marketplaces"].append({"name": "supervisor-mesh", "source": "directory", "path": args[3]})
        save(st)
        out({"ok": True})
    if args[:3] == ["plugin", "marketplace", "remove"]:
        (data.get("extraKnownMarketplaces") or {}).pop(args[3], None)
        if settings.is_file():
            settings.write_text(json.dumps(data, indent=1))
        st["marketplaces"] = [m for m in st.get("marketplaces", []) if m.get("name") != args[3]]
        save(st)
        out({"ok": True})
    if args[:2] == ["plugin", "uninstall"]:
        if failing(st, "uninstall"):
            out("Error: uninstall failed (scripted)", 1)
        st["plugin_rows"] = [r for r in st.get("plugin_rows", []) if not (
            r.get("id") == args[2] and r.get("scope") == "project" and r.get("projectPath") == str(cwd))]
        save(st)
        out("uninstalled")
    out(f"stub claude: unsupported {args}", 2)


def tmux(st: dict, args: list[str]):
    if args == ["-V"]:
        out("tmux 3.4")
    target = arg_after(args, "-t")
    pane = (st.get("tmux") or {}).get(target)
    if "display-message" in args:
        out(pane.get("dead", "0 0")) if pane else out("can't find pane", 1)
    if pane is None:
        out(f"can't find pane: {target}", 1)
    if "capture-pane" in args:
        screen = pane["screens"][pane["screen"]]
        out(screen.replace("{box}", pane.get("box", "")), 0)
    if "send-keys" in args:
        keys = args[args.index(target) + 1:]
        if keys[:1] == ["-l"]:
            pane["box"] = pane.get("box", "") + " ".join(keys[1:])
        else:
            for key in keys:
                if key == "Enter":
                    queue = pane.setdefault("on_enter", [])
                    apply(pane, queue.pop(0) if queue else "submit")
                else:
                    queue = (pane.get("on_key") or {}).get(key) or []
                    if queue:
                        apply(pane, queue.pop(0))
        save(st)
        out("")
    out(f"stub tmux: unsupported {args}", 2)


def apply(pane: dict, action: str) -> None:
    """submit (clear the box) | ignore | screen:<name> | submit-screen:<name>."""
    if action.startswith("submit"):
        pane["box"] = ""
    if ":" in action:
        pane["screen"] = action.split(":", 1)[1]


def projmux(st: dict, args: list[str]):
    if args == ["version"]:
        out("projmux 0.17.2")
    verb = args[:2]
    ref = args[2][4:] if len(args) > 2 and args[2].startswith("uid:") else None
    live = [a for a in st.get("agents", []) if a["metadata"]["uid"] not in st.get("deleted", [])]
    proj = st.get("project")
    if verb == ["get", "projects"]:
        out({"items": [{"metadata": {"uid": proj}}] if st.get("registered") else []})
    if verb == ["get", "agents"]:
        out({"items": live if st.get("registered") or "-A" in args else []})
    if verb == ["describe", "agent"]:
        item = next((a for a in live if a["metadata"]["uid"] == ref), None)
        out(item) if item else out("Error: agent not found", 1)
    if verb == ["describe", "project"]:
        out({"metadata": {"uid": proj}, "spec": {"root": st.get("root")},
             "status": {"session": {"live": True, "name": "stub"}}})
    if verb == ["describe", "pane"]:
        runtime = (st.get("panes") or {}).get(ref)
        out({"status": {"activation": {"runtimeID": runtime}}}) if runtime else out("Error: pane not found", 1)
    if args[:3] == ["agent", "status", "get"]:
        line = (st.get("status") or {}).get(args[3][4:] if args[3].startswith("uid:") else args[3])
        out(line) if line else out("", 1)
    if args[:3] == ["agent", "message", "status"]:
        out({"ref": args[3], "status": "delivered"})
    if verb == ["delete", "agent"]:
        if "--dry-run" in args:
            if failing(st, "delete-plan"):
                out("Error: registry busy (scripted)", 1)
            out(f"delete agent: would delete 1 agent\nagent/x uid={ref}{st.get('delete_plan_extra', '')}\n"
                "dry-run: nothing was deleted")
        if failing(st, "delete"):
            out("Error: delete failed (scripted)", 1)
        st.setdefault("deleted", []).append(ref)
        if st.get("spawn_after_delete"):
            st["agents"].append(st.pop("spawn_after_delete"))
        save(st)
        out(f"deleted agent {ref}")
    if verb == ["stop", "project"]:
        if failing(st, "stop"):
            out("Error: stop failed (scripted)", 1)
        out({"operation": "stop.project", "target": {"uid": proj}})
    if verb == ["unregister", "project"]:
        if "--dry-run" in args:
            if failing(st, "unregister-plan"):
                out("Error: registry lock (scripted)", 1)
            out(f"unregister project: would delete 1 project\nproject/x uid={proj}\ndry-run: nothing was deleted")
        if failing(st, "unregister"):
            out("Error: unregister failed (scripted)", 1)
        st["registered"] = False
        st["deleted"] = sorted({*st.get("deleted", []), *(a["metadata"]["uid"] for a in st.get("agents", []))})
        save(st)
        out(f"unregistered project {proj} runtime=preserved")
    out(f"stub projmux: unsupported {args}", 2)


def main() -> None:
    tool, args = globals().get("TOOL") or os.path.basename(sys.argv[0]), sys.argv[1:]
    with open(os.environ["SMV_STUB_LOG"], "a") as f:
        f.write(json.dumps({"tool": tool, "args": args, "cwd": os.getcwd()}) + "\n")
    st = load()
    {"claude": claude, "tmux": tmux, "projmux": projmux}[tool](st, args)


if __name__ == "__main__":
    main()
