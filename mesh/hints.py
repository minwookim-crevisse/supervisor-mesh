"""The hint catalogue (D135): the next-step texts mesh prints, in one place. WO-0044 moved today's texts here VERBATIM
(the CLI init's `# `-prefixed lines and `mesh_init`'s `next` entries). The two Part B wordings (`NEXT` for the CLI,
`PART_B` for `mesh_init`) are kept as they were; unifying them changes output (WO-0045).
"""

from __future__ import annotations

# ---------------------------------------------------------------- CLI init (mesh/init.py prints these as they are)
AMBIGUOUS = "STACK AMBIGUOUS — no single package.json / pyproject.toml found. Rerun with --stack ts|python|none."
NEXT = "# next: fill docs/harness/part-b-checklist.md (Part B — never invent these)"
AUTHOR_HINT = (  # D134 (A7): read-only; init never sets a git identity
    '# hint: git user.name is not set for this project; set it before the first commit: git config user.name "<name>"'
)

# ---------------------------------------------------------------- the PM label (mesh_init `next`, caller_not_pm detail)
PM_LABEL_HINT = (
    "label the PM pane (D82): start it with `projmux create claude --project <name> --label role=pm`; "
    "until then every pm tool is refused and the operator-event hook records nothing"
)

# ---------------------------------------------------------------- mesh_init `next`
STACK_AMBIGUOUS = (
    "the stack is ambiguous (no single package.json / pyproject.toml): ask the operator ts | python | none"
)
PART_B = "fill docs/harness/part-b-checklist.md (Part B: ask the operator, never invent values)"
COMPANIONS_MISSING = (
    "missing companions: the operator names `companions_install` (the whole plan) or one plugin name in a "
    "prompt in the PM pane; then call companions_install with that prompt's event id"
)


def show_plan(plan_id: str) -> str:
    return f"show these rows to the operator, then call mesh_init with apply=true and plan_id={plan_id}"
