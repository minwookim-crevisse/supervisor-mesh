#!/usr/bin/env python3
"""DOC drift check (supervisor-mesh GC, methodology §14).

1. Every backtick-quoted file path named in the instruction doc must exist.
2. No wording the glossary marks as deprecated may reappear in source.

why: the instruction doc must point at the real tree; a renamed/moved path
silently rots the harness, and deprecated wordings creep back unless checked.

usage: check_docs_drift.py [--doc AGENTS.md] [--glossary docs/conventions/glossary.md]
                           [--root .] [--exclude DIR ...]
"""
import argparse
import re
import sys
from pathlib import Path

PATH_RE = re.compile(r"`([\w./-]+\.\w+)`")
DEPRECATED_RE = re.compile(r"\(deprecated:\s*([^)]+)\)")
DEFAULT_EXCLUDES = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt"}


def is_excluded(path: Path, root: Path, excludes: set[str]) -> bool:
    rel = path.relative_to(root)
    if any(part in DEFAULT_EXCLUDES for part in rel.parts):
        return True
    posix = rel.as_posix()
    return any(posix == ex.rstrip("/") or posix.startswith(ex.rstrip("/") + "/") for ex in excludes)


def missing_paths(doc: Path, root: Path) -> list[str]:
    text = doc.read_text(encoding="utf-8")
    names = {m for m in PATH_RE.findall(text) if "<" not in m and "*" not in m and "://" not in m}
    return sorted(n for n in names if not (root / n).exists())


def deprecated_hits(glossary: Path, root: Path, excludes: set[str]) -> list[str]:
    if not glossary.exists():
        return []
    words = []
    for group in DEPRECATED_RE.findall(glossary.read_text(encoding="utf-8")):
        words += [w.strip() for w in group.split(",") if w.strip() and "<" not in w]
    hits = []
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix not in SOURCE_SUFFIXES or is_excluded(f, root, excludes):
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{f.relative_to(root)}: '{w}'" for w in words if w in text]
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--doc", default="AGENTS.md")
    ap.add_argument("--glossary", default="docs/conventions/glossary.md")
    ap.add_argument("--root", default=".")
    ap.add_argument("--exclude", action="append", default=[])
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    doc = root / a.doc
    if not doc.exists():
        print(f"DOC DRIFT — instruction doc not found: {a.doc}")
        return 1
    missing = missing_paths(doc, root)
    hits = deprecated_hits(root / a.glossary, root, set(a.exclude))
    if missing:
        print(f"DOC DRIFT — {a.doc} names files that do not exist:")
        for f in missing:
            print(f"  - {f}")
    if hits:
        print("DOC DRIFT — deprecated wordings reappeared in source:")
        for h in hits:
            print(f"  - {h}")
    if missing or hits:
        return 1
    print("no doc drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
