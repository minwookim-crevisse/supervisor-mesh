"""`docs/tracker.md` as a generated view of state.json, and the one-time legacy import of a hand-written tracker.

Legacy import (pm-led-onboarding C4 + O5 revision): the first Markdown table with an id column is read; columns map
by header name (English, or Korean equivalents written as escapes), missing columns stay empty. Every row keeps its
raw text; the status maps to the standard vocabulary only when the FIRST word is unambiguous (`FAIL (2 🔴)` ->
fail, `waiting` -> open), anything else is `legacy`. Never `verified`, never deleted, and the source file is not
modified by the import.
Depends keep their raw text; only exact ids and `WO-0001..0003` ranges become dependencies, parenthesised notes and
`∥ <id>` (parallel, not a dependency) are dropped, prose yields nothing.

Render: only the region between BEGIN and END is ever replaced, so hand prose around it survives. The first render
over an unmarked file replaces the tracker table block (or appends the region when there is none) and keeps the
original under `.mesh/tracker-original.md` first. Output is deterministic (no timestamps): a re-render with no state
change writes nothing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import layers
from .model import Doc, InvalidArgs, LegacyRow, MeshError

__all__ = ["BEGIN", "END", "InvalidArgs", "Preview", "Rendered", "parse_legacy", "render_region", "render_file"]

BEGIN = "<!-- mesh:tracker:begin -->"
END = "<!-- mesh:tracker:end -->"
HEADER = "| id | title | role | depends | status | report |\n|---|---|---|---|---|---|"
TRACKER = Path("docs") / "tracker.md"

COLUMNS = {
    "id": ("id", "step", "no", "#", "\ubc88\ud638", "\ub2e8\uacc4", "\uc544\uc774\ub514"),
    "title": ("title", "content", "task", "\uc81c\ubaa9", "\ub0b4\uc6a9", "\uc791\uc5c5"),
    "role": ("role", "roles", "owner", "\uc5ed\ud560", "\ub2f4\ub2f9"),
    "depends": ("depends", "depends on", "deps", "\uc758\uc874", "\uc120\ud589"),
    "status": ("status", "state", "\uc0c1\ud0dc"),
    "report": ("report", "reports", "\ubcf4\uace0\uc11c", "\ub9ac\ud3ec\ud2b8"),
}
STATUS_WORDS = {
    "open": "open", "pending": "open", "waiting": "open", "queued": "open", "todo": "open", "\ub300\uae30": "open",
    "dispatched": "dispatched", "in progress": "dispatched", "in-progress": "dispatched", "wip": "dispatched",
    "\uc9c4\ud589": "dispatched", "\uc9c4\ud589\uc911": "dispatched", "\uc9c4\ud589 \uc911": "dispatched",
    "pass": "pass", "passed": "pass", "fail": "fail", "failed": "fail",
    "closed": "closed", "done": "closed", "complete": "closed", "completed": "closed", "\uc644\ub8cc": "closed",
    "✅": "closed", "✔": "closed", "✔️": "closed", "✓": "closed",
    "n/a": "n/a",
}  # fmt: skip
EMPTY = {"", "—", "–", "-", "none", "n/a"}
_ID = r"WO-\d{4,}"


@dataclass(frozen=True, slots=True)
class Preview:
    rows: tuple[LegacyRow, ...]
    skipped: tuple[dict, ...]  # {"line": n, "reason": ...}: line numbers and reasons, never the line text

    def mapping(self) -> list[dict]:
        return [{"id": r.id, "status_raw": r.status_raw, "status": r.status} for r in self.rows]


@dataclass(frozen=True, slots=True)
class Rendered:
    changed: bool
    first_render: bool  # the file had no markers (or did not exist)
    backup: str | None  # the original's name under .mesh/, when one was kept
    region: str


# ---------------------------------------------------------------- parsing
def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body)]


def _is_rule(line: str) -> bool:
    return bool(re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?", line.strip()))


def _column_map(header: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for i, name in enumerate(header):
        key = name.strip().lower()
        for col, names in COLUMNS.items():
            if key in names and col not in found:
                found[col] = i
    return found


def _table(lines: list[str]) -> tuple[int, int, dict[str, int]] | None:
    """(header line index, end index exclusive, column map) of the first table with an id column."""
    for i in range(len(lines) - 1):
        if lines[i].lstrip().startswith("|") and _is_rule(lines[i + 1]):
            cols = _column_map(_cells(lines[i]))
            if "id" in cols:
                end = i + 2
                while end < len(lines) and lines[end].lstrip().startswith("|"):
                    end += 1
                return i, end, cols
    return None


def map_status(raw: str) -> str:
    text = raw.strip().lower()
    for words in (3, 2, 1):
        head = " ".join(text.split()[:words])
        if head in STATUS_WORDS and (words > 1 or not text[len(head) : len(head) + 1].isalnum()):
            return STATUS_WORDS[head]
    first = re.split(r"[\s(,;:]", text, maxsplit=1)[0]
    return STATUS_WORDS.get(first, "legacy")


def parse_depends(raw: str) -> tuple[str, ...]:
    text = re.sub(r"\([^)]*\)", " ", raw)  # notes in parentheses
    text = re.sub(rf"∥\s*{_ID}", " ", text)  # parallel to, not depends on
    out: list[str] = []
    for m in re.finditer(rf"({_ID})(?:\.\.(?:WO-)?(\d+))?", text):
        start = m.group(1)
        if m.group(2):
            lo, hi, width = int(start[3:]), int(m.group(2)), len(start) - 3
            out += [f"WO-{n:0{width}d}" for n in range(lo, hi + 1)] if 0 <= hi - lo <= 1000 else [start]
        else:
            out.append(start)
    return tuple(dict.fromkeys(out))


def _list(raw: str) -> tuple[str, ...]:
    if raw.strip().lower() in EMPTY:
        return ()
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _reports(raw: str) -> tuple[str, ...]:
    quoted = re.findall(r"`([^`]+)`", raw)
    return tuple(quoted) if quoted else _list(raw)


def parse_legacy(text: str) -> Preview:
    lines = text.splitlines()
    table = _table(lines)
    if table is None:
        return Preview((), ())
    start, end, cols = table
    rows: list[LegacyRow] = []
    skipped: list[dict] = []
    seen: set[str] = set()

    def cell(cells: list[str], col: str) -> str:
        i = cols.get(col)
        return cells[i] if i is not None and i < len(cells) else ""

    for n in range(start + 2, end):
        cells = _cells(lines[n])
        rid = cell(cells, "id")
        if not rid:
            skipped.append({"line": n + 1, "reason": "no id"})
            continue
        if rid in seen:
            skipped.append({"line": n + 1, "reason": f"duplicate id {rid}"})
            continue
        seen.add(rid)
        status_raw, depends_raw = cell(cells, "status"), cell(cells, "depends")
        rows.append(
            LegacyRow(
                rid,
                cell(cells, "title"),
                _list(cell(cells, "role")),
                depends_raw,
                parse_depends(depends_raw),
                status_raw,
                map_status(status_raw) if status_raw else "legacy",
                _reports(cell(cells, "report")),
                len(rows),
            )
        )
    return Preview(tuple(rows), tuple(skipped))


# ---------------------------------------------------------------- rendering
def _esc(text: str) -> str:
    """One table cell: one line, `|` escaped, and no HTML comment opener, so a goal that quotes a mesh marker can never
    become a second marker (markdown shows `&lt;!--` as `<!--`)."""
    return " ".join(text.replace("|", "\\|").replace("<!--", "&lt;!--").split()) or "—"


def _joined(values) -> str:
    return ", ".join(values) if values else "—"


def render_region(doc: Doc) -> str:
    lines = [BEGIN, HEADER]
    for r in sorted(doc.legacy.values(), key=lambda r: r.order):
        status = f"legacy: {r.status_raw}" if r.status == "legacy" else f"{r.status} (legacy: {r.status_raw})"
        reports = _joined([f"`{p}`" for p in r.reports])
        cells = [r.id, r.title, _joined(r.roles), r.depends_raw or "—", status, reports]
        lines.append("| " + " | ".join(_esc(c) for c in cells) + " |")
    for item in sorted(doc.items.values(), key=lambda i: i.id):
        c = item.contract
        report = f"`{c.scope.report}`" if c.scope.report else "—"
        cells = [item.id, c.goal, c.role, _joined(c.depends), item.phase.value, report]
        lines.append("| " + " | ".join(_esc(x) for x in cells) + " |")
    lines.append(END)
    return "\n".join(lines)


def _splice(text: str, region: str) -> tuple[str, bool]:
    """(new text, first render). A file with markers changes only between them."""
    b, e = text.count(BEGIN), text.count(END)
    if b or e:
        if b != 1 or e != 1 or text.index(BEGIN) > text.index(END):
            raise InvalidArgs("docs/tracker.md: the mesh tracker markers are unbalanced; fix them by hand")
        head, rest = text.split(BEGIN, 1)
        return head + region + rest.split(END, 1)[1], False
    lines = text.splitlines(keepends=True)
    table = _table([line.rstrip("\n") for line in lines])
    if table is None:
        return text.rstrip("\n") + ("\n\n" if text.strip() else "") + region + "\n", True
    start, end, _ = table
    return "".join(lines[:start]) + region + "\n" + "".join(lines[end:]), True


def _backup_name(mesh_dir: Path) -> str:
    name, n = "tracker-original.md", 2
    while (mesh_dir / name).exists():
        name, n = f"tracker-original-{n}.md", n + 1
    return name


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class OutsideRoot(MeshError):  # noqa: N818
    code = "outside_root"


def render_file(root: Path, doc: Doc, dry_run: bool = False) -> Rendered:
    """D111: refused (OutsideRoot, nothing written, the dry run included) when docs/tracker.md or the .mesh/ backup
    resolves outside the project root."""
    for rel in (str(TRACKER), ".mesh/tracker-original.md"):
        if not layers.contained(Path(root), rel):
            raise OutsideRoot(f"{rel} resolves outside the project root (a symlink): refused (D111)")
    path = Path(root) / TRACKER
    region = render_region(doc)
    try:
        old = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        old = None
    new, first = _splice(old, region) if old is not None else ("# Tracker\n\n" + region + "\n", True)
    changed = new != old
    backup = None
    if changed and not dry_run:
        if first and old is not None and old.strip():
            mesh_dir = Path(root) / ".mesh"
            backup = _backup_name(mesh_dir)
            _write(mesh_dir / backup, old)
        _write(path, new)
    return Rendered(changed, first, backup, region)
