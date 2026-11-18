"""Reads the `Table ==` constant of tests/formal/WorkItemState.tla WITHOUT Java or TLC, so the conformance test runs in
`make check`. A restricted evaluator, not eval(): it understands only what the Table block uses (strings, the names
None/Lead/OtherLead/NonTerminal/Created, Blocked(...), set literals, \\cup and \\). Anything else raises ValueError:
the model changed shape, update this file."""

import re
from pathlib import Path

_TOKEN = re.compile(
    r'\s*(?:(?P<str>"[^"]*")|(?P<cup>\\cup)|(?P<diff>\\)|(?P<id>[A-Za-z_][A-Za-z_0-9]*)|(?P<p>[(){},]))'
)
_REASONS = ("awaiting_user", "environment", "limit", "children")
_BLOCKED = {f"blocked({r})" for r in _REASONS}
_NON_TERMINAL = {"draft", "ready", "running", "verifying", "verified", "rework"} | _BLOCKED
_NAMES = {
    "None": "(none)",
    "Lead": "lead (own role)",
    "OtherLead": "lead (other role)",
    "NonTerminal": frozenset(_NON_TERMINAL),
    "Created": frozenset(_NON_TERMINAL | {"closed", "cancelled"}),
}


def _tokens(text):
    pos = 0
    while text[pos:].strip():
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"unparsed Table text near: {text[pos : pos + 30]!r}")
        pos = m.end()
        yield m.lastgroup, m.group(m.lastgroup)


class _Parser:
    def __init__(self, tokens):
        self.toks, self.i = list(tokens), 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, want=None):
        kind, value = self.peek()
        if want is not None and value != want:
            raise ValueError(f"expected {want!r}, got {value!r}")
        self.i += 1
        return kind, value

    def expr(self):
        left = self.term()
        while self.peek()[0] in ("cup", "diff"):
            op, _ = self.take()
            right = self.term()
            left = (left | right) if op == "cup" else (left - right)
        return left

    def term(self):
        kind, value = self.take()
        if kind == "str":
            return value[1:-1]
        if kind == "id" and value == "Blocked":
            self.take("(")
            arg = self.expr()
            self.take(")")
            return f"blocked({arg})"
        if kind == "id":
            if value not in _NAMES:
                raise ValueError(f"unknown name {value!r}")
            return _NAMES[value]
        if value == "{":
            out = set()
            if self.peek()[1] != "}":
                out.add(self.expr())
                while self.peek()[1] == ",":
                    self.take(",")
                    out.add(self.expr())
            self.take("}")
            return frozenset(out)
        raise ValueError(f"unexpected token {value!r}")


def parse_table(tla_text: str) -> frozenset:
    """-> the model's (verb, caller, from, to) triples; `unchanged` is expanded to the from-state (the .tla's RowTo)."""
    m = re.search(r"^Table ==\n(?P<body>.*?)\n\n\\\* RowTo", tla_text, re.S | re.M)
    if not m:
        raise ValueError("Table block not found: the model layout changed")
    body = re.sub(r"\\\*[^\n]*", "", m.group("body")).strip()
    body = body[body.index("{") + 1 : body.rindex("}")]
    p = _Parser(_tokens(body))
    triples = set()
    while p.peek()[0] is not None:
        p.take("Row")
        p.take("(")
        verb = p.expr()
        p.take(",")
        caller = p.expr()
        p.take(",")
        src = p.expr()
        p.take(",")
        dst = p.expr()
        p.take(")")
        if p.peek()[1] == ",":
            p.take(",")
        for s in src:
            for d in dst:
                triples.add((verb, caller, s, s if d == "unchanged" else d))
    return frozenset(triples)


def load_table(path: Path) -> frozenset:
    return parse_table(path.read_text(encoding="utf-8"))


def load_probe_names(path: Path) -> frozenset:
    """The `NoRow_<name>` row-coverage probes of the model: one per row outcome."""
    text = path.read_text(encoding="utf-8")
    return frozenset(re.findall(r"^NoRow_(\w+)\s*==", text, re.M))
