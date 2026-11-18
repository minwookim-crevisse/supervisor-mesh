"""The work order `work_dispatch` sends: ONE pure function of the item record and the project root (D83).

`HEADER` is the dispatch skill's standard header, verbatim (skills/dispatch/SKILL.md step 3; a test pins the two
together). The text is never stored: state.json keeps its sha256 (`text_sha256`) and the message-ref, and a replay
recomposes the text and sends it only when the hash still matches (DEC-0008: no absolute paths in state.json).
"""

from __future__ import annotations

import hashlib
from pathlib import PurePath

from .model import Item

HEADER = "\n".join(
    (
        "[supervisor-mesh work order]",
        "Operating model: lead + worker + lead-review. Dispatch to a same-role worker, verify against the canonical"
        " source; self-reports are not accepted.",
        "Discipline: think before coding, simplicity first, surgical changes, goal-driven.",
        "Evidence-first DoD: captured command output or exit code; LIVE evidence for render, write, money and auth.",
        "Constraints: coordination docs are read-only (ask the orchestrator), no secrets or .env content, no"
        " worktree-destroying git commands, push is Ship only.",
        "Workers: spawn each worker with the label worker-of=<your role> and an explicit last step: it messages you"
        " with projmux agent message send, message-ref <WO>-done; then wait with projmux agent wait uid:<worker>"
        " --until idle --timeout <duration> instead of polling its transcript.",
        "Report: PASS only with evidence attached, to the orchestrator.",
    )
)


def _abs(root: PurePath, path: str) -> str:
    p = PurePath(path)
    return str(p if p.is_absolute() else root / p)


def _bullets(lines) -> list[str]:
    return [f"- {line}" for line in lines] or ["- (none)"]


MAX_ORDER = 100_000  # bytes of one composed order: it travels as ONE argv string (Linux: 128 KiB per argument)


def work_order_path(item: Item) -> str:
    return item.contract.scope.work_order or f"docs/work-orders/{item.id}.md"


def compose(item: Item, root: PurePath) -> str:
    c = item.contract
    report = _abs(root, c.scope.report) if c.scope.report else f"your role report under {root / 'docs' / 'reports'}"
    body = [
        f"Work item: {item.id} (role {c.role}, type {c.type}, contract v{c.version})",
        f"Work order: {_abs(root, work_order_path(item))} (read it first)",
        f"Mission: {c.goal}",
        "Done criteria (captured evidence for each):",
        *_bullets(c.done_criteria),
        "Read set:",
        *_bullets(_abs(root, p) for p in c.scope.read_set),
        "Write set:",
        *_bullets(c.scope.write_set),
        f"Depends on: {', '.join(c.depends) if c.depends else 'none'}",
        f"Report: {report}",
        f"Lifecycle (supervisor-mesh MCP): work_transition verb start on {item.id} (it returns the attempt id), then "
        "submit with that attempt id and your commit, then verify (the configured commands run at that commit); "
        "fail with a class when blocked. A prose report never reaches verified.",
    ]
    return HEADER + "\n\n" + "\n".join(body) + "\n"


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
