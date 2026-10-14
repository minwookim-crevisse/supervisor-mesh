#!/usr/bin/env python3
"""supervisor-mesh init — lay the primed 4-pillar harness into a project.

Idempotent and non-destructive: an existing file is never overwritten. Missing
files are created; a few known files are merged additively (AGENTS.md block,
.gitignore lines, package.json scripts, pyproject tables); everything else that
exists is skipped and reported.

usage: scaffold.py --target DIR [--stack auto|ts|python|none] [--dry-run]
exit:  0 ok · 3 stack ambiguous (rerun with --stack)
"""
import argparse
import json
import re
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
AGENTS_MARKER = "<!-- supervisor-mesh:harness -->"
GITIGNORE_LINES = [".env", ".env.*", "!.env.example", ".auth/", "*.sqlite", "*.sqlite3", "*:Zone.Identifier"]

COMMON = {
    "docs/decisions/0001-decision-log.md": "docs/decisions/0001-decision-log.md",
    "docs/conventions/glossary.md": "docs/conventions/glossary.md",
    "docs/conventions/constraints.md": "constraints/README.md",
    "docs/harness/part-b-checklist.md": "part-b-checklist.md",
    "tests/README.md": "tests/README.md",
    "scripts/audit.sh": "tests/audit.sh",
    "scripts/gc/check_docs_drift.py": "gc/check_docs_drift.py",
    "scripts/gc/check_structure.py": "gc/check_structure.py",
    "scripts/gc/README.md": "gc/README.md",
    ".github/workflows/housekeeping.yml": "gc/housekeeping.yml",
}
STACK_FILES = {
    "ts": {
        ".dependency-cruiser.cjs": "constraints/typescript/.dependency-cruiser.cjs",
        ".eslintrc.cjs": "constraints/typescript/.eslintrc.cjs",
    },
    "python": {".importlinter": "constraints/python/.importlinter"},
    "none": {},
}


class Log:
    def __init__(self, dry: bool):
        self.dry, self.rows = dry, []

    def add(self, action: str, path: str, note: str = "") -> None:
        self.rows.append((action, path, note))
        print(f"{action:<7} {path}{'  (' + note + ')' if note else ''}")


def detect_stack(target: Path) -> str | None:
    ts, py = (target / "package.json").exists(), (target / "pyproject.toml").exists()
    if ts and not py:
        return "ts"
    if py and not ts:
        return "python"
    return None


def write_new(target: Path, rel: str, content: str, log: Log, mode: int | None = None) -> None:
    dest = target / rel
    if dest.exists():
        log.add("SKIP", rel, "exists")
        return
    if not log.dry:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        if mode:
            dest.chmod(mode)
    log.add("CREATE", rel)


def merge_agents(target: Path, log: Log) -> None:
    tpl = (TEMPLATES / "AGENTS.md").read_text(encoding="utf-8")
    dest = target / "AGENTS.md"
    if not dest.exists():
        write_new(target, "AGENTS.md", tpl.replace("\n", f"\n{AGENTS_MARKER}\n", 1), log)
        return
    text = dest.read_text(encoding="utf-8")
    if AGENTS_MARKER in text:
        log.add("SKIP", "AGENTS.md", "harness block present")
        return
    body = tpl.split("\n", 1)[1]  # drop the template title; keep the project's own
    if not log.dry:
        block = f"\n\n{AGENTS_MARKER}\n# Harness (supervisor-mesh)\n{body}"
        dest.write_text(text.rstrip("\n") + block, encoding="utf-8")
    log.add("MERGE", "AGENTS.md", "appended harness block")


def merge_gitignore(target: Path, log: Log) -> None:
    dest = target / ".gitignore"
    have = dest.read_text(encoding="utf-8").splitlines() if dest.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in have]
    if not missing:
        log.add("SKIP", ".gitignore", "secret/junk lines present")
        return
    if not log.dry:
        prefix = "\n".join(have).rstrip("\n")
        block = "# supervisor-mesh: secrets and env junk never enter the repo\n" + "\n".join(missing)
        dest.write_text((prefix + "\n\n" if prefix else "") + block + "\n", encoding="utf-8")
    log.add("MERGE" if have else "CREATE", ".gitignore", f"+{len(missing)} lines")


def merge_package_scripts(target: Path, log: Log) -> None:
    dest = target / "package.json"
    if not dest.exists():
        log.add("SKIP", "package.json", "absent — create it with your package manager, then rerun init")
        return
    raw = dest.read_text(encoding="utf-8")
    pkg = json.loads(raw)
    wanted = json.loads((TEMPLATES / "constraints/typescript/package-scripts.json").read_text(encoding="utf-8"))
    scripts = pkg.setdefault("scripts", {})
    added = [k for k in wanted if k not in scripts]
    if not added:
        log.add("SKIP", "package.json", "scripts present")
        return
    for k in added:
        scripts[k] = wanted[k]
    indent = len(m.group(1)) if (m := re.search(r'\n( +)"', raw)) else 2
    if not log.dry:
        dest.write_text(json.dumps(pkg, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
    log.add("MERGE", "package.json", "+scripts " + ",".join(added))


PYPROJECT_TABLES = {"[tool.ruff]": "pyproject.ruff.toml", "[tool.mypy]": "pyproject.mypy.toml"}


def merge_pyproject(target: Path, log: Log) -> None:
    """Append each primed table that is absent; never touch a table the project already has."""
    dest = target / "pyproject.toml"
    existed = dest.exists()
    text = dest.read_text(encoding="utf-8") if existed else ""
    added, kept = [], []
    for table, src in PYPROJECT_TABLES.items():
        name = re.escape(table[1:-1])
        if re.search(rf"^\[{name}[.\]]", text, re.M):  # table header, incl. sub-tables like [tool.ruff.lint]
            kept.append(table)
            continue
        frag = (TEMPLATES / "constraints/python" / src).read_text(encoding="utf-8")
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + frag
        added.append(table)
    if not added:
        log.add("SKIP", "pyproject.toml", "ruff + mypy tables present — compare with templates/constraints/python")
        return
    if not log.dry:
        dest.write_text(text, encoding="utf-8")
    note = "+" + " ".join(added) + (f"; kept existing {' '.join(kept)}" if kept else "")
    log.add("MERGE" if existed else "CREATE", "pyproject.toml", note)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True)
    ap.add_argument("--stack", choices=["auto", "ts", "python", "none"], default="auto")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    target = Path(a.target).resolve()
    stack = detect_stack(target) if a.stack == "auto" else a.stack
    if stack is None:
        print("STACK AMBIGUOUS — no single package.json / pyproject.toml found. Rerun with --stack ts|python|none.")
        return 3
    log = Log(a.dry_run)
    print(f"# supervisor-mesh init → {target}  (stack: {stack}{', dry-run' if a.dry_run else ''})")
    merge_agents(target, log)
    for rel, src in {**COMMON, **STACK_FILES[stack]}.items():
        content = (TEMPLATES / src).read_text(encoding="utf-8")
        write_new(target, rel, content, log, 0o755 if rel.endswith((".sh", ".py")) else None)
    merge_gitignore(target, log)
    if stack == "ts":
        merge_package_scripts(target, log)
    if stack == "python":
        merge_pyproject(target, log)
    counts = {k: sum(1 for r in log.rows if r[0] == k) for k in ("CREATE", "MERGE", "SKIP")}
    print(f"# summary: created={counts['CREATE']} merged={counts['MERGE']} skipped={counts['SKIP']}")
    print("# next: fill docs/harness/part-b-checklist.md (Part B — never invent these)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
