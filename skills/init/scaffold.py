#!/usr/bin/env python3
"""supervisor-mesh init — lay the primed 4-pillar harness into a project.

A thin wrapper: the scaffold lives in `mesh/init.py` (shared with the `mesh_init` MCP tool); the printed lines are
unchanged. Idempotent and non-destructive: an existing file is never overwritten.

usage: scaffold.py --target DIR [--stack auto|ts|python|none] [--dry-run]
exit:  0 ok · 3 stack ambiguous (rerun with --stack)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the plugin root, so `mesh` imports

from mesh.init import AGENTS_MARKER, main  # noqa: E402

__all__ = ["AGENTS_MARKER", "main"]

if __name__ == "__main__":
    sys.exit(main())
