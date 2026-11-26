#!/usr/bin/env python3
"""supervisor-mesh stdio MCP server: newline-delimited JSON-RPC 2.0 (initialize, notifications/initialized, ping,
tools/list, tools/call). Launched by `.mcp.json` as `python3 ${CLAUDE_PLUGIN_ROOT}/mesh/mcp_server.py`, so `sys.path[0]`
is `mesh/` itself: the top of this file replaces it with the plugin root (otherwise `import mesh.x` fails, and a
module in `mesh/` could shadow a stdlib one).

stdout carries JSON-RPC ONLY, enforced structurally and not by discipline: `claim_stdout` duplicates fd 1 for the
writer, then points fd 1 and `sys.stdout` at stderr, so a stray print(), a library warning or a child that
inherited fd 1 lands on stderr. Logs are one stderr line per problem, never arguments, env values or paths.

Protocol surface, deliberately small: the client's protocol version is echoed when we know it, else the newest; unknown
method -> -32601; malformed request or params -> -32602 / -32600; unparsable line -> -32700 (id null); notifications get
no reply. A refused or failed tool call is NOT a protocol error: it is a `tools/call` result with `isError: true`,
so the model sees it and can adapt. Single-threaded: requests are served in order.
"""

import sys

if sys.version_info < (3, 11):  # before any mesh import (StrEnum, tomllib)
    sys.stderr.write("supervisor-mesh: python3 >= 3.11 is required\n")
    sys.exit(3)

import json  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, BinaryIO, TextIO  # noqa: E402

if __name__ == "__main__":
    sys.path[0] = str(Path(__file__).resolve().parents[1])  # the plugin root, not mesh/

from mesh import layers, tools  # noqa: E402

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603


def plugin_version() -> str:
    try:
        manifest = layers.parse_json(
            (Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json").read_text("utf-8")
        )
        return str(manifest.get("version", "0"))
    except (OSError, ValueError):
        return "0"


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tools_list(ctx: tools.Context) -> dict:
    cfg = tools.config_or_defaults(ctx)  # D132: the work_new type enum is the project's types
    listed = []
    for t in tools.REGISTRY.values():
        entry = {"name": t.name, "description": t.description, "inputSchema": t.input_schema(cfg)}
        if t.read_only:
            entry["annotations"] = {"readOnlyHint": True}
        listed.append(entry)
    return {"tools": listed}


def _tools_call(ctx: tools.Context, params: Any) -> dict:
    """Raises ValueError for malformed params (-32602); everything else is a result."""
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise ValueError("tools/call needs params.name")
    arguments = params.get("arguments", {})
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("params.arguments must be an object")
    try:
        result = tools.call(ctx, params["name"], arguments)
    except tools.UnknownTool:
        raise ValueError(f"unknown tool {params['name']!r}") from None
    return {
        "content": [{"type": "text", "text": json.dumps(result.data, ensure_ascii=False, separators=(",", ":"))}],
        "structuredContent": result.data,
        "isError": not result.ok,
    }


def handle(msg: Any, ctx: tools.Context) -> dict | None:
    """One decoded line -> one response, or None for a notification. Pure over (msg, ctx); the loop owns the I/O."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "not a JSON-RPC 2.0 request")
    method, params = msg["method"], msg.get("params")
    is_notification = "id" not in msg
    request_id = msg.get("id")
    try:
        if method == "initialize":
            asked = params.get("protocolVersion") if isinstance(params, dict) else None
            result = {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "supervisor-mesh", "version": plugin_version()},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = _tools_list(ctx)
        elif method == "tools/call":
            result = _tools_call(ctx, params)
        elif is_notification:
            return None  # initialized, cancelled, anything else: no reply
        else:
            return _error(request_id, METHOD_NOT_FOUND, f"unknown method {method!r}")
    except ValueError as exc:
        return None if is_notification else _error(request_id, INVALID_PARAMS, str(exc))
    except Exception as exc:  # a bug: one stderr line, the error TYPE and a fixed id (a message can carry a path)
        layers.log(f"internal error {type(exc).__name__} [E-RPC]")
        return None if is_notification else _error(request_id, INTERNAL_ERROR, "internal error")
    return None if is_notification else {"jsonrpc": "2.0", "id": request_id, "result": result}


claim_stdout = layers.claim_stdout  # the only writer of the protocol stream (the stdout layer)


def _recover(ctx: tools.Context) -> None:
    try:
        tools.recover(layers.once_per_request(ctx))
    except Exception as exc:  # one line, the error type only: recovery is retried on the next start
        layers.log(f"recovery failed ({type(exc).__name__})")


def serve(reader: BinaryIO, writer: TextIO, ctx: tools.Context) -> None:
    """Section-8 recovery runs ONCE, after the handshake: right after the `initialize` reply is written, or before the
    first `tools/call` of a client that skipped it. Never before the client has spoken (it would delay the handshake
    and act for a session that may never use the server)."""
    recovered = False
    for raw in layers.bounded_lines(reader):
        line = raw.strip() if raw is not None else None
        if line == b"":
            continue
        try:
            if line is None:
                raise ValueError("line too long")
            msg = layers.parse_json(line)
        except ValueError:
            msg, reply = None, _error(None, PARSE_ERROR, "parse error")
        else:
            method = msg.get("method") if isinstance(msg, dict) else None
            if method == "tools/call" and not recovered:
                recovered = True
                _recover(ctx)
            reply = handle(msg, ctx)
        if reply is not None:
            writer.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n")
            writer.flush()
        if not recovered and isinstance(msg, dict) and msg.get("method") == "initialize":
            recovered = True
            _recover(ctx)


def _terminate(signum, frame) -> None:
    """SIGTERM unwinds like an exception, so `finally` blocks run (a verify worktree is removed, not leaked)."""
    raise SystemExit(128 + signum)


def main() -> int:
    signal.signal(signal.SIGTERM, _terminate)
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
    writer = claim_stdout()
    serve(sys.stdin.buffer, writer, tools.Context.for_project(root, os.environ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
