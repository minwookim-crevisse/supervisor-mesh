"""Default `.mesh/config.toml` content per stack, as DATA (D92): `mesh_init` writes `config_toml(stack)` once.

TS_FAIL_IF_OUTPUT: output lines that turn a passing TypeScript check into a failure (the pilot's INV-3 passed while
dependency-cruiser cruised 0 modules; vitest exits 0 when it finds no test files under some configurations).
"""

from __future__ import annotations

import json

TS_FAIL_IF_OUTPUT = ("0 modules, 0 dependencies cruised", "No test files found")
FAIL_IF_OUTPUT = {"ts": TS_FAIL_IF_OUTPUT}

_HEAD = """# supervisor-mesh project config (written once by mesh_init; edit freely). Read with tomllib.
operator_channel = "terminal"

# [limits]
# retry = 2
# environment_unblock = 3

# [dispatch]
# retry = 3        # resends of a transiently refused order, same message-ref (D93)
# backoff_s = 1.0  # doubled after each attempt

# [status]
# stall_minutes = 15  # mesh_status: no lead activity on a running/verifying item for this long is a stall (D102)
"""

_VERIFY = """
# Verification, argv only (never a shell string), in a disposable worktree at the submitted commit. `prepare` runs
# first (same timeout/env rules; a failure is class environment, D91). `fail_if_output`: literal substrings (D115,
# never regexes) that FAIL a command that exited 0 when a line of its output contains one (D92); at [verify] level
# they apply to every command.
[verify]
{fail}# prepare = ["npm", "ci", "--prefer-offline"]
# prepare_timeout_s = 600

# [[verify.commands]]
# argv = ["make", "check"]
# kind = "test"
# timeout_s = 600
# fail_if_output = ["No tests ran"]
"""


def config_toml(stack: str | None) -> str:
    patterns = FAIL_IF_OUTPUT.get(stack or "", ())
    fail = f"fail_if_output = [{', '.join(json.dumps(p) for p in patterns)}]\n" if patterns else ""
    return _HEAD + _VERIFY.format(fail=fail)
