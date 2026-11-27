"""tmux adapter and the screen classifier. Only `pane_command` uses it; the socket is projmux's (`tmux -L projmux`).

`classify` is a POSITIVE match for an idle Claude Code prompt: the bottom-most input box (a `❯` line between two rules
near the bottom, with no dialog or numbered menu below it) is empty and no turn is running. D141: a box that holds
text on the plain capture is still idle when `suggestion_only` finds, on a second `-e` capture, that the text is only
a faint suggestion. Anything else (a trust or
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


_ESCAPE = re.compile(r"\x1b\[([0-9;:]*)([@-~])|\x1b[^\[]?")
_BODY_SGR = frozenset({"", "0", "2", "22"})  # D141: inside the box body only faint on and the resets
_OFF = {"22": ("faint", "bold"), "23": ("italic",), "24": ("underline",), "25": ("blink",), "27": ("inverse",),
        "28": ("hidden",), "29": ("strike",), "39": ("fg",), "49": ("bg",)}  # fmt: skip
_ON = {"1": "bold", "2": "faint", "3": "italic", "4": "underline", "5": "blink", "7": "inverse", "8": "hidden",
       "9": "strike"}  # fmt: skip


def _sgr(state: set[str], params: str) -> None:
    """Apply one SGR sequence to `state` (the set of active non-default attributes)."""
    parts = params.replace(":", ";").split(";")
    i = 0
    while i < len(parts):
        p = parts[i] or "0"
        if p == "0":
            state.clear()
        elif p in _ON:
            state.add(_ON[p])
        elif p in _OFF:
            state.difference_update(_OFF[p])
        elif p in ("38", "48"):
            state.add("fg" if p == "38" else "bg")
            i += 2 if i + 1 < len(parts) and parts[i + 1] == "5" else 4
        elif p.isdigit() and (30 <= int(p) <= 37 or 90 <= int(p) <= 97):
            state.add("fg")
        elif p.isdigit() and (40 <= int(p) <= 47 or 100 <= int(p) <= 107):
            state.add("bg")
        else:
            state.add(f"sgr{p}")
        i += 1


def _cells(line: str) -> tuple[list[tuple[str, frozenset[str], tuple[str, ...]]], tuple[str, ...]] | None:
    """The line's cells as (character, its attributes, the SGR parameter strings right before it), and the SGR
    parameters after the last cell; None for an escape that is not SGR (a cursor move or a mode change)."""
    state: set[str] = set()
    cells: list[tuple[str, frozenset[str], tuple[str, ...]]] = []
    pending: list[str] = []
    pos = 0
    for m in _ESCAPE.finditer(line):
        for ch in line[pos : m.start()]:
            cells.append((ch, frozenset(state), tuple(pending)))
            pending = []
        if m.group(2) != "m":
            return None
        _sgr(state, m.group(1))
        pending.append(m.group(1))
        pos = m.end()
    for ch in line[pos:]:
        cells.append((ch, frozenset(state), tuple(pending)))
        pending = []
    return cells, tuple(pending)


def suggestion_only(styled: str, box: str) -> bool:
    """D141 (F4): the input box of the STYLED capture (`capture-pane -e`) holds `box` (the plain capture's box text)
    and that body is only a faint suggestion: every non-space cell is faint (SGR 2) and nothing else is styled, and
    no escape but faint-on and the resets appears inside the body. An inverse first cell (a cursor), a colour, bold,
    mixed faint and plain cells, or style carried in from before the prompt mark = not a suggestion (typed text)."""
    lines = styled.rstrip("\n").split("\n")[-60:]
    plain_lines = [_ESCAPE.sub("", x) for x in lines]
    span = _box_span(plain_lines)
    if not box or span is None or prompt_text("\n".join(plain_lines)) != box:
        return False
    body: list[tuple[str, frozenset[str], tuple[str, ...]]] = []
    escapes: list[str] = []
    for n, i in enumerate(range(span[0] + 1, span[1])):
        parsed = _cells(lines[i])
        if parsed is None:
            return False
        cells, trailing = parsed
        if n == 0:  # the body starts after the prompt mark and one space
            mark = next((k for k, c in enumerate(cells) if c[0] in "❯>"), None)
            if mark is None:
                return False
            cells = cells[mark + 1 :]
            if cells and cells[0][0].isspace():
                cells = cells[1:]
        body += [c for c in cells if c[0] != "│"]
        escapes += [e for c in cells for e in c[2]] + list(trailing)
    ink = [attrs for ch, attrs, _ in body if not ch.isspace()]
    return bool(ink) and all(attrs == {"faint"} for attrs in ink) and all(e in _BODY_SGR for e in escapes)


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

    def capture_styled(self, pane_id: str, lines: int = 60) -> str:
        """D141: the same capture with its SGR escapes (`-e`), used only to tell a faint suggestion from typed text."""
        return self._run("capture-pane", "-e", "-p", "-J", "-t", pane_id, "-S", f"-{lines}")

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
