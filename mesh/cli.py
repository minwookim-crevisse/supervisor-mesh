#!/usr/bin/env python3
"""supervisor-mesh CLI: the second front door over the SAME tool functions as the MCP server.

    mesh <tool> [--<arg> VALUE ...] [--json '{...}']   one subcommand per entry of `tools.REGISTRY` (derived, never
                                                       listed by hand); prints the tool's JSON result on stdout;
                                                       exit 0 ok, 1 refused or failed, 2 usage error
    mesh event record --source terminal                the section-9a UserPromptSubmit hook (via .mesh/bin/mesh)

Options are derived from each tool's args dataclass: a boolean is a flag, a string (or enum) is taken as typed, and
anything structured (lists, objects) is JSON. `--json` supplies the whole argument object; explicit options win.

`event record` reads the hook's JSON from stdin and records the prompt as an operator event ONLY when the payload is a
UserPromptSubmit event with a session id, the prompt is not a projmux coordination envelope (a peer message), and the
caller is the PM (projmux label role=pm, this project's cwd: the same identity rule as the MCP server, D82) and the
PM pane's claude process (projmux `status.activation.claude.process.pid`) is an ancestor of this process (D96: a lead
that sets the PM's pane uid in its environment is not run by the PM's claude; fail closed, `hook_error`
`parent_not_pm_claude`). Anything else records nothing. It prints nothing (a UserPromptSubmit hook's stdout would join
the prompt) and exits 0 on every error, leaving a `hook_error` line in .mesh/events.jsonl when .mesh/ exists.

The project root is `CLAUDE_PROJECT_DIR` when set, else the working directory.
"""

import sys

if sys.version_info < (3, 11):  # before any mesh import (StrEnum, tomllib)
    sys.stderr.write("supervisor-mesh: python3 >= 3.11 is required\n")
    sys.exit(3)

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import types  # noqa: E402
import typing  # noqa: E402
from dataclasses import MISSING, fields  # noqa: E402
from enum import Enum  # noqa: E402
from pathlib import Path  # noqa: E402

if __name__ == "__main__":
    sys.path[0] = str(Path(__file__).resolve().parents[1])  # the plugin root, not mesh/

from mesh import identity, layers, tools  # noqa: E402
from mesh.model import CallerKind  # noqa: E402
from mesh.projmux import Projmux  # noqa: E402
from mesh.store import Store  # noqa: E402


def _root(env) -> Path:
    return Path(env.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def _plain(tp) -> str:
    """'bool', 'str' (taken as typed) or 'json' for a field type."""
    args = [a for a in typing.get_args(tp) if a is not type(None)]
    if typing.get_origin(tp) in (typing.Union, types.UnionType) and len(args) == 1:
        tp = args[0]
    if tp is bool:
        return "bool"
    if tp is str or typing.get_origin(tp) is typing.Literal or (isinstance(tp, type) and issubclass(tp, Enum)):
        return "str"
    return "json"


def _json_arg(text: str):
    try:
        return json.loads(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not JSON: {text[:40]!r}") from None


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mesh", description="supervisor-mesh CLI (the MCP tools, plus the event hook)")
    sub = ap.add_subparsers(dest="command", required=True, metavar="<command>")
    for t in tools.REGISTRY.values():
        sp = sub.add_parser(t.name, help=t.description.split(". ")[0], description=t.description)
        sp.add_argument("--json", type=_json_arg, default=None, help="the whole argument object as JSON")
        hints = typing.get_type_hints(t.args)
        for f in fields(t.args):
            kind = _plain(hints[f.name])
            flag = "--" + f.name.replace("_", "-")
            required = f.default is MISSING and f.default_factory is MISSING
            note = f.metadata.get("doc", "") + (" (required)" if required else "")
            if kind == "bool":
                sp.add_argument(flag, dest=f.name, action="store_true", default=None, help=note)
            else:
                sp.add_argument(
                    flag,
                    dest=f.name,
                    type=_json_arg if kind == "json" else str,
                    default=None,
                    help=(note + " (JSON)" if kind == "json" else note),
                )
    ev = sub.add_parser("event", help="operator events (the section-9a hook)")
    ev_sub = ev.add_subparsers(dest="event_command", required=True, metavar="<event command>")
    rec = ev_sub.add_parser("record", help="record the hook's prompt as an operator event (PM pane only)")
    rec.add_argument("--source", choices=["terminal"], default="terminal")
    return ap


def call_tool(name: str, ns: argparse.Namespace, env) -> int:
    args = dict(ns.json or {}) if isinstance(ns.json, dict) or ns.json is None else None
    if args is None:
        layers.log("--json must be an object")
        return 2
    for f in fields(tools.REGISTRY[name].args):
        value = getattr(ns, f.name, None)
        if value is not None:
            args[f.name] = value
    ctx = tools.Context.for_project(_root(env), env)
    result = tools.call(ctx, name, args)
    sys.stdout.write(json.dumps(result.data, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0 if result.ok else 1


COORDINATION_KIND = "projmux-coordination"
_ENVELOPE_START = re.compile(r'\{\s*"kind"\s*:\s*"projmux-coordination"')


def is_coordination_envelope(prompt: str) -> bool:
    """A projmux peer message delivered into the pane as a prompt: a JSON object of kind `projmux-coordination`, or
    text that starts with that envelope's opening. It is coordination data from another agent, never the operator."""
    text = prompt.strip()
    if _ENVELOPE_START.match(text):
        return True
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("kind") == COORDINATION_KIND


def operator_prompt(payload) -> str | None:
    """The prompt of a genuine UserPromptSubmit hook payload, else None (ignored: nothing is recorded). A payload of
    another hook event, one without a session id, or a coordination envelope is not an operator prompt. A payload
    that is not an object, or has no string prompt, is a hook error (ValueError)."""
    if not isinstance(payload, dict):
        raise ValueError("the hook input is not an object")
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return None
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        return None
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        raise ValueError("no prompt in the hook input")
    return None if is_coordination_envelope(prompt) else prompt


def record_event(env, stdin) -> int:
    root = _root(env)
    store = Store(root / ".mesh")
    if not store.initialized():
        return 0  # not a mesh project (or not initialised yet): nothing to record into
    at = tools.utc_now()
    try:
        prompt = operator_prompt(json.loads(stdin.read() or "null"))
        if prompt is None:
            return 0
        who = identity.resolve(env, root, Projmux(env))
        if who.caller.kind is not CallerKind.PM:
            return 0  # a lead's or an unlabelled pane: never an operator event
        if not identity.descends_from(os.getpid(), who.claude_pid):  # D96: run by the PM pane's own claude
            store.append_hook_error(at, "parent_not_pm_claude")
            return 0
        store.record_operator_event(prompt, at, who.caller.agent_uid)
    except Exception as exc:  # the hook never blocks the prompt
        try:
            store.append_hook_error(at, type(exc).__name__)
        except OSError:
            pass
    return 0


def main(argv=None, env=None) -> int:
    env = dict(os.environ if env is None else env)
    ns = parser().parse_args(argv)
    if ns.command == "event":
        layers.quiet_stdout()  # a UserPromptSubmit hook's stdout would be added to the prompt
        return record_event(env, sys.stdin)
    return call_tool(ns.command, ns, env)


if __name__ == "__main__":
    sys.exit(main())
