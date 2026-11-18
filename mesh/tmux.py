"""tmux adapter and the screen classifier. Only `pane_command` uses it; the socket is projmux's (`tmux -L projmux`).

`classify` is a POSITIVE match for an idle Claude Code prompt: the bottom-most input box (a `❯` line between two rules
near the bottom, with no dialog or numbered menu below it) is empty and no turn is running. Anything else (a trust or
permission dialog, a running turn, a half-typed box, an unrecognised screen) is not IDLE, and pane_command refuses:
it never answers a prompt. The patterns are a deliberate small copy of the repo verifier's `smv_screen.py`, which is
repo-own and not importable from a shipped module.
"""

from __future__ import annotations

import re
import shlex
from enum import StrEnum
from typing import Mapping

from . import runner

TIMEOUT_S = 10.0
SOCKET = ("-L", "projmux")

DIALOGS = (
    ("trust", re.compile(r"Accessing workspace:|Quick safety check|Yes, I trust this folder")),
    ("permission", re.compile(r"Do you want to |don't ask again|Yes, allow")),
)
MENU_RE = re.compile(r"^\s*[❯>]\s*\d+\.\s", re.M)
RULE_RE = re.compile(r"^\s*[─━╭╰]{8,}")
INPUT_RE = re.compile(r"^\s*(?:│\s*)?[❯>](?!\s*\d+\.\s)\s?(.*?)\s*│?\s*$")
BUSY_RE = re.compile(r"esc to interrupt", re.I)
BOX_TAIL = 12  # the active box ends within this many non-empty lines of the bottom


class ScreenKind(StrEnum):
    IDLE = "idle"
    DIRTY = "dirty"  # the box already holds text: typing would append to it
    BUSY = "busy"
    TRUST_PROMPT = "trust prompt"
    PERMISSION_PROMPT = "permission prompt"
    UNKNOWN = "unknown"


def _box_span(lines: list[str]) -> tuple[int, int] | None:
    seen = 0
    for i in range(len(lines) - 1, 0, -1):
        if seen > BOX_TAIL:
            return None
        if RULE_RE.match(lines[i]):
            j = next((j for j in range(i - 1, max(i - 8, 0) - 1, -1) if RULE_RE.match(lines[j])), None)
            if j is not None and j < i - 1 and INPUT_RE.match(lines[j + 1]):
                below = "\n".join(lines[i + 1 :])
                if MENU_RE.search(below) or any(rx.search(below) for _, rx in DIALOGS):
                    return None
                return j, i
        if lines[i].strip():
            seen += 1
    return None


def prompt_text(screen: str) -> str | None:
    """The text of the active input box (wrapped lines joined), or None when no active box is on screen."""
    lines = screen.rstrip("\n").split("\n")[-60:]
    span = _box_span(lines)
    if span is None:
        return None
    body = lines[span[0] + 1 : span[1]]
    first = INPUT_RE.match(body[0]).group(1)
    return " ".join([first, *(b.strip(" │") for b in body[1:])]).strip()


def classify(screen: str) -> ScreenKind:
    lines = screen.rstrip("\n").split("\n")[-60:]
    box = prompt_text(screen)
    if box is None:
        tail = "\n".join(lines)
        if DIALOGS[0][1].search(tail):
            return ScreenKind.TRUST_PROMPT
        if DIALOGS[1][1].search(tail):
            return ScreenKind.PERMISSION_PROMPT
        return ScreenKind.UNKNOWN
    if BUSY_RE.search("\n".join(lines[-15:])):
        return ScreenKind.BUSY
    if box and not box.startswith('Try "'):  # an untouched prompt may show a 'Try "..."' placeholder
        return ScreenKind.DIRTY
    return ScreenKind.IDLE


def holds(screen: str, text: str) -> bool:
    """The prompt box holds exactly the typed text (whitespace ignored: the box wraps long lines)."""
    box = prompt_text(screen)
    return box is not None and "".join(box.split()) == "".join(text.split())


class TmuxFailed(Exception):  # noqa: N818
    pass


PANE_ID = re.compile(r"%[0-9]+")


def valid_pane_id(pane_id: object) -> bool:
    """D116: a tmux pane target from projmux must be exactly `%<digits>` (no session, option or separator syntax)."""
    return isinstance(pane_id, str) and PANE_ID.fullmatch(pane_id) is not None


class Tmux:
    def __init__(self, env: Mapping[str, str] | None = None, exe: str = "tmux"):
        self.env = dict(env) if env is not None else None
        self.exe = exe

    def _run(self, *args: str) -> str:
        r = runner.run([self.exe, *SOCKET, *args], timeout=TIMEOUT_S, env=self.env)
        if not r.ok:
            raise TmuxFailed(f"tmux {args[0]} failed (exit {r.code})")
        return r.out

    def capture(self, pane_id: str, lines: int = 60) -> str:
        return self._run("capture-pane", "-p", "-J", "-t", pane_id, "-S", f"-{lines}")

    def type_literal(self, pane_id: str, text: str) -> None:
        """`-l`: the text is typed as literal keys (no key names), and `--` ends the options."""
        self._run("send-keys", "-t", pane_id, "-l", "--", text)

    def enter(self, pane_id: str) -> None:
        self._run("send-keys", "-t", pane_id, "Enter")

    def clear_line(self, pane_id: str) -> None:
        """C-u: erase the input line (an aborted keypress leaves nothing behind in the box)."""
        self._run("send-keys", "-t", pane_id, "C-u")

    def popup(self, pane_id: str, argv: list[str], env: Mapping[str, str], timeout_s: float) -> bool:
        """D113/D121: run `argv` in a popup on the client showing `pane_id` (popup content is not pane content:
        capture-pane cannot read it), with `env` set in the popup (`-e`: the work dir path, not a secret). Neither
        carries a secret: the code travels only through the work dir's FIFO. True: started."""
        clients = self._run("list-clients", "-t", pane_id, "-F", "#{client_name}").split()
        if not clients:
            return False
        sets = [a for k, v in sorted(env.items()) for a in ("-e", f"{k}={v}")]
        r = runner.run(
            [self.exe, *SOCKET, "display-popup", "-c", clients[0], "-t", pane_id, "-E", "-w", "72", "-h", "9",
             *sets, shlex.join(argv)],
            timeout=timeout_s + 5,
            env=self.env,
        )  # fmt: skip
        return r.ok
