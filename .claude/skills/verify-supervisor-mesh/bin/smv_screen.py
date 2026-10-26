"""Pure screen parsing for smv: a Claude Code TUI capture in, facts out. No I/O: no projmux, tmux or files.

Tested offline by tests/test_screen.py against redacted captures from a live run (tests/fixtures/).
"""
import re

SCOPE_OPTION = "Install for all collaborators on this repository (project scope)"
DIALOGS = [  # consulted when no active input box is on screen, or when this text shows below the box
    ("trust", re.compile(r"Accessing workspace:|Quick safety check|Yes, I trust this folder")),
    ("scope-menu", re.compile(r"\((user|project|local) scope\)")),
    ("permission", re.compile(r"Do you want to |don't ask again|Yes, allow")),
]
MENU_RE = re.compile(r"^\s*[❯>]\s*\d+\.\s", re.M)
OPTION_RE = re.compile(r"^\s*(?:│\s*)?(?P<cur>[❯›>])?\s*(?:\d+\.\s+)?(?P<text>.*?)\s*│?\s*$")
MENU_HEADER_RE = re.compile(r"^\s*Will install:")  # the plugin details view, right above the option list
MENU_RULE_RE = re.compile(r"^\s*[─━▔▁═]{8,}")
MENU_FOOTER_RE = re.compile(r"Enter to select|Esc to go back")
DIALOG_MARK_RE = re.compile("|".join(rx.pattern for _, rx in DIALOGS)
                            + r"|Enter to (?:confirm|select)|Esc to (?:cancel|go back)")
MODE_RE = re.compile(r"\b(auto mode|accept edits|plan mode|bypass permissions) on\b", re.I)
RULE_RE = re.compile(r"^\s*[─━╭╰]{8,}")
INPUT_RE = re.compile(r"^\s*(?:│\s*)?[❯>](?!\s*\d+\.\s)\s?(.*?)\s*│?\s*$")
BUSY_RE = re.compile(r"esc to interrupt", re.I)
DONE_LINE_RE = re.compile(r"^\s*✻ \S.* for \d")  # the turn-complete line above the box ("... for 1m 31s")
BOX_TAIL = 12  # the active box ends within this many non-empty lines of the bottom (footer, autocomplete list)


def lines_of(text: str) -> list[str]:
    return text.rstrip("\n").split("\n")


def box_span(lines: list[str]) -> tuple[int, int] | None:
    """(top rule, bottom rule) of the active prompt input box: the bottom-most `❯` line between two rules, ending
    within BOX_TAIL non-empty lines of the bottom, with no dialog or numbered menu text below it. A box higher up
    (an older frame in the scrollback) never counts, so a dialog under it is never read as a ready screen."""
    seen = 0
    for i in range(len(lines) - 1, 0, -1):
        if seen > BOX_TAIL:
            return None
        if RULE_RE.match(lines[i]):
            j = next((j for j in range(i - 1, max(i - 8, 0) - 1, -1) if RULE_RE.match(lines[j])), None)
            if j is not None and j < i - 1 and INPUT_RE.match(lines[j + 1]):
                below = "\n".join(lines[i + 1:])
                if MENU_RE.search(below) or any(rx.search(below) for _, rx in DIALOGS):
                    return None
                return j, i
        if lines[i].strip():
            seen += 1
    return None


def input_box(lines: list[str]) -> str | None:
    """Text of the active input box (wrapped lines joined), or None when no active box is on screen."""
    span = box_span(lines)
    if span is None:
        return None
    body = lines[span[0] + 1:span[1]]
    first = INPUT_RE.match(body[0]).group(1)
    return " ".join([first, *(b.strip(" │") for b in body[1:])]).strip()


def box_is_empty(box: str | None) -> bool:
    return not box or box.startswith('Try "')  # an untouched prompt may show a 'Try "..."' placeholder


def box_holds(box: str | None, text: str) -> bool:
    """The box holds exactly the typed text (whitespace ignored: the box wraps long lines)."""
    return box is not None and "".join(box.split()) == "".join(text.split())


def screen_state(text: str) -> str:
    """ready | busy | trust | permission | scope-menu | dialog | unknown, from the last 60 lines."""
    lines = lines_of(text)[-60:]
    if box_span(lines) is None:
        tail = "\n".join(lines)
        for name, rx in DIALOGS:
            if rx.search(tail):
                return name
        return "dialog" if MENU_RE.search(tail) else "unknown"
    return "busy" if BUSY_RE.search("\n".join(lines[-15:])) else "ready"


def menu_block(text: str) -> list[tuple[int, str]]:
    """(line index, line) of the scope menu's option block only: the lines after the plugin details header
    ('Will install:', else the nearest rule above the options) and before the footer ('Enter to select ...').
    The input box and the scrollback above the plugin view are never part of it; no footer, no block."""
    lines = lines_of(text)
    footer = next((i for i in range(len(lines) - 1, -1, -1) if MENU_FOOTER_RE.search(lines[i])), None)
    if footer is None:
        return []
    head = next((i for i in range(footer - 1, -1, -1)
                 if MENU_HEADER_RE.match(lines[i]) or MENU_RULE_RE.match(lines[i])), None)
    return [] if head is None else [(i, lines[i]) for i in range(head + 1, footer)]


def menu_options(text: str) -> list[tuple[int, bool, str]]:
    """(line index, highlighted, option text) inside the menu block; cursor glyph, number and border stripped."""
    rows = []
    for i, line in menu_block(text):
        m = OPTION_RE.match(line)
        if m and m.group("text"):
            rows.append((i, bool(m.group("cur")), m.group("text")))
    return rows


def highlighted(text: str) -> tuple[int, str] | None:
    """The one highlighted menu line, or None when there is none or more than one."""
    hits = [(i, t) for i, cur, t in menu_options(text) if cur]
    return hits[0] if len(hits) == 1 else None


def permission_mode(text: str) -> str:
    """The pane's permission mode from the footer under the input box ('auto mode', 'accept edits', ...)."""
    m = MODE_RE.search("\n".join(lines_of(text)[-6:]))
    return m.group(1).lower() if m else "default"


def dialog_span(lines: list[str]) -> list[int]:
    """Line indexes of the bottom dialog: from the nearest rule above its first marker line (trust text, option
    or footer) through the last non-empty line after its last marker. Empty when no marker is on screen."""
    start = max(0, len(lines) - 40)
    marks = [i for i in range(start, len(lines)) if DIALOG_MARK_RE.search(lines[i]) or MENU_RE.match(lines[i])]
    if not marks:
        return []
    first, last = marks[0], marks[-1]
    top = next((j for j in range(first, max(first - 12, -1), -1) if RULE_RE.match(lines[j])), max(first - 2, 0))
    end = max((i for i in range(last, min(last + 4, len(lines))) if lines[i].strip()), default=last)
    return list(range(top, end + 1))


def keep_lines(text: str, kind: str = "auto", pattern: re.Pattern | None = None) -> list[tuple[int, str]]:
    """The only lines a capture keeps as evidence (Y10): what a check needs, never the transcript area.
    Every kind keeps the active box with its footer and the busy / turn-complete line above it. 'menu' adds the
    scope menu (header through footer), 'dialog' the bottom dialog, 'match' the lines `pattern` matches, 'tail'
    the last 8 non-empty lines (a dead pane's exit message). 'auto' adds the menu or the dialog when no box shows."""
    lines = lines_of(text)
    keep: set[int] = set()
    span = box_span(lines)
    if span:
        top, bot = span
        keep |= set(range(top, bot + 1))
        keep |= {i for i in range(bot + 1, min(len(lines), bot + 4)) if lines[i].strip()}
        keep |= {i for i in range(max(0, top - 4), top) if BUSY_RE.search(lines[i]) or DONE_LINE_RE.match(lines[i])}
    block = menu_block(text)
    if block and (kind == "menu" or (kind == "auto" and not span)):
        keep |= set(range(max(0, block[0][0] - 1), min(len(lines), block[-1][0] + 2)))
    elif kind == "dialog" or (kind == "auto" and not span):
        keep |= set(dialog_span(lines))
    if kind == "match" and pattern is not None:
        keep |= {i for i, line in enumerate(lines) if pattern.search(line)}
    if kind == "tail":
        keep |= set([i for i, line in enumerate(lines) if line.strip()][-8:])
    return [(i, lines[i]) for i in sorted(keep)]


def drop_map(lines: list[str], patterns: dict) -> dict[int, str]:
    """{line index: rule} of every line a drop rule matches, alone or wrapped: each line is matched on its own and
    the stripped lines are also matched joined end to end, so a token or address the TUI broke across lines drops
    every line it touches (Y9)."""
    drops = {i: name for i, line in enumerate(lines)
             for name in [next((n for n, rx in patterns.items() if rx.search(line)), None)] if name}
    spans, pos, parts = [], 0, []
    for i, line in enumerate(lines):
        s = line.strip()
        spans.append((pos, pos + len(s), i))
        parts.append(s)
        pos += len(s)
    joined = "".join(parts)
    for name, rx in patterns.items():
        for m in rx.finditer(joined):
            touched = [i for a, b, i in spans if a < m.end() and b > m.start()]
            if len(touched) > 1:
                for i in touched:
                    drops.setdefault(i, f"{name} (wrapped)")
    return drops
