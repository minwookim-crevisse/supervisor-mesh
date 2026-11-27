"""D141 F4 screen fixtures: the input box of an idle Claude Code lead pane that shows a faint (SGR 2) suggestion.

Provenance of `SUGGESTION_STYLED` (2026-11-26): a REAL read-only `tmux -L projmux capture-pane -e -p` of an idle Claude
Code lead pane (another project's backend lead) showing a dim suggestion. Kept: the input box (rule, prompt line, rule)
and the footer line under it; trimmed: everything above the box and the task lines under the footer. The escape bytes,
the U+00A0 after the prompt mark, the rule width and the footer are as captured. Changed: ONLY the words of the
suggestion itself (model-written prompt text from that project), replaced by neutral words of the same SGR form. No
home path was on these lines.

`SUGGESTION_PLAIN` is the same region with the SGR sequences removed (what the plain `capture-pane -p` shows). The
other fixtures are built from the captured line by changing only the body's styling.
"""

import re

_SGR = re.compile(r"\x1b\[[0-9;:]*m")

RULE_STYLED = "\x1b[38;5;244m" + "─" * 135
FOOTER_STYLED = (
    "\x1b[39m  \x1b[38;5;220m⏵⏵ auto mode on\x1b[38;5;246m (shift+tab to cycle) · ← 11 agents · ↓ to manage\x1b[39m"
)
WORDS = "run the test suite again"  # replaces the captured suggestion words (see the module docstring)


def box(line: str) -> str:
    """The captured region with `line` as the prompt line."""
    return "\n".join([RULE_STYLED, line, RULE_STYLED, FOOTER_STYLED, ""])


def plain(styled: str) -> str:
    return _SGR.sub("", styled)


SUGGESTION_STYLED = box(f"\x1b[39m❯\xa0\x1b[2m{WORDS}\x1b[0m")  # as captured: faint on, the words, reset
SUGGESTION_PLAIN = plain(SUGGESTION_STYLED)

TYPED_STYLED = box(f"\x1b[39m❯\xa0{WORDS}")  # typed text: no styling on the body
INVERSE_FIRST_STYLED = box(f"\x1b[39m❯\xa0\x1b[7m{WORDS[0]}\x1b[27m{WORDS[1:]}")  # a cursor cell: typed text
MIXED_STYLED = box("\x1b[39m❯\xa0\x1b[2mrun the\x1b[0m test suite again")  # faint then plain: typed after it
OTHER_ESCAPE_STYLED = box(f"\x1b[39m❯\xa0\x1b[2;38;5;246m{WORDS}\x1b[0m")  # faint plus a colour
FAINT_BOLD_STYLED = box(f"\x1b[39m❯\xa0\x1b[2m\x1b[1m{WORDS}\x1b[0m")  # faint plus bold
CARRIED_STYLE_STYLED = box(f"\x1b[7m❯\xa0\x1b[2m{WORDS}\x1b[0m")  # inverse carried into the body from the prefix
