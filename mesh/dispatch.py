"""The work order `work_dispatch` sends: ONE pure function of the item record and the project root (D83).

`HEADER` is the dispatch skill's standard header, verbatim (skills/dispatch/SKILL.md step 3; a test pins the two
together). The message is the header plus a pointer to the work-order FILE, at most MAX_MESSAGE bytes (pilot: projmux
refuses longer messages). The text is never stored: state.json keeps its sha256 (`text_sha256`) and the
message-ref, and a replay recomposes the text and sends it only when the hash still matches (DEC-0008: no absolute
paths in state.json).
"""

from __future__ import annotations

import hashlib
from pathlib import PurePath

from .model import Item

HEADER = "\n".join(
    (
        "[supervisor-mesh work order]",
        "Operating model: lead + in-process subagent worker + lead-review. Dispatch to a same-role worker, verify"
        " against the canonical source; self-reports are not accepted.",
        "Discipline: think before coding, simplicity first, surgical changes, goal-driven.",
        "Evidence-first DoD: captured command output or exit code; LIVE evidence for render, write, money and auth.",
        "Constraints: coordination docs are read-only (ask the orchestrator), no secrets or .env content, no"
        " worktree-destroying git commands, push is Ship only.",
        "Workers: each worker is your in-process subagent (your provider's subagent tool): brief it, receive its"
        " output directly, verify, send rework to it. Only if this order explicitly asks for a separate worker agent:"
        " label it worker-of=<your role>, make its last step a projmux agent message send with message-ref"
        " <WO>-done, and wait with projmux agent wait uid:<worker> --until idle --timeout <duration>.",
        "Report: PASS only with evidence attached, to the orchestrator.",
    )
)


def _abs(root: PurePath, path: str) -> str:
    p = PurePath(path)
    return str(p if p.is_absolute() else root / p)


MAX_MESSAGE = 4096  # bytes (UTF-8) of one projmux message: projmux refuses a longer one; never split


def work_order_path(item: Item) -> str:
    return item.contract.scope.work_order or f"docs/work-orders/{item.id}.md"


def compose(item: Item, root: PurePath, message_ref: str) -> str:
    """The header, then a pointer: the item id, the message-ref and the ABSOLUTE work-order path. The order itself
    (mission, done criteria, read and write sets, depends) lives in that file and is never inlined (projmux caps a
    message at MAX_MESSAGE bytes)."""
    c = item.contract
    report = _abs(root, c.scope.report) if c.scope.report else f"your role report under {root / 'docs' / 'reports'}"
    body = [
        f"Work item: {item.id} (role {c.role}, type {c.type}, contract v{c.version}); message-ref {message_ref}",
        f"Work order: {_abs(root, work_order_path(item))} (read it first: mission, done criteria, read and write"
        " sets and depends live in that file, not in this message)",
        f"Report: {report}",
        f"Lifecycle (supervisor-mesh MCP): work_transition start on {item.id} (returns the attempt id), submit with it"
        " and your commit, then verify; fail with a class when blocked. A prose report never reaches verified.",
    ]
    return HEADER + "\n\n" + "\n".join(body) + "\n"


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
