#!/usr/bin/env python3
"""STRUCTURE drift check (supervisor-mesh GC, methodology §14).

Fails on files that break directory conventions: temp/backup names, env junk,
and undisposed one-shot scripts.

why: temp/backup files and env junk in the tree = drift; one-shot scripts must
be disposed after use (a stale delete script re-runs against new real data);
temp belongs in the session scratchpad, not the repo.

usage: check_structure.py [--root .] [--exclude DIR ...] [--pattern GLOB ...]
"""
import argparse
import fnmatch
import sys
from pathlib import Path

FORBIDDEN = [
    "temp_*", "tmp_*", "*_new.*", "*_old.*", "*_backup.*", "*_fix.*", "*.bak", "*.orig",
    "*.sqlite", "*.sqlite3", "*:Zone.Identifier", ".DS_Store",
    "oneshot_*", "one_shot_*",  # one-shot scripts: dispose right after they run
]
DEFAULT_EXCLUDES = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}


def scan(root: Path, excludes: list[str], patterns: list[str]) -> list[str]:
    bad = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if any(part in DEFAULT_EXCLUDES for part in rel.parts):
            continue
        posix = rel.as_posix()
        if any(posix == ex.rstrip("/") or posix.startswith(ex.rstrip("/") + "/") for ex in excludes):
            continue
        if any(fnmatch.fnmatch(p.name, pat) for pat in patterns):
            bad.append(posix)
    return sorted(bad)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--pattern", action="append", default=[], help="extra forbidden glob (Part B)")
    a = ap.parse_args(argv)
    bad = scan(Path(a.root).resolve(), a.exclude, FORBIDDEN + a.pattern)
    if bad:
        print("STRUCTURE DRIFT — files violating directory conventions:")
        for p in bad:
            print(f"  - {p}")
        return 1
    print("no structure drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
