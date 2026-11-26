"""`mesh_init`: the 4-pillar scaffold (moved here from `skills/init/scaffold.py`, now a thin wrapper) plus mesh's own
bootstrap.

THE SCAFFOLD SEAM (kept contract: tests/test_scaffold*.py and the live verifier match the printed lines):
    scaffold(target, stack, dry_run) -> ScaffoldResult   writes only when dry_run is False
    render_text(result) -> str                           the one formatter of the printed lines
    main(argv) -> int                                    the CLI of scaffold.py; prints each row as it is added, exactly
                                                         as before the move (a crash mid-run prints the same prefix)
The merge_* / write_new bodies are the pre-move code: idempotent, an existing file is never overwritten.

Idempotent and non-destructive: an existing file is never overwritten. Missing files are created; a few known files
are merged additively (AGENTS.md block, .gitignore lines, package.json scripts, pyproject tables); everything else that
exists is skipped and reported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping

from . import defaults, git, layers, tracker
from .model import empty_doc
from .store import Store

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
AGENTS_MARKER = "<!-- supervisor-mesh:harness -->"
GITIGNORE_LINES = [".env", ".env.*", "!.env.example", ".auth/", "*.sqlite", "*.sqlite3", "*:Zone.Identifier"]
USAGE = "supervisor-mesh init — lay the primed 4-pillar harness into a project."
AMBIGUOUS = "STACK AMBIGUOUS — no single package.json / pyproject.toml found. Rerun with --stack ts|python|none."
NEXT = "# next: fill docs/harness/part-b-checklist.md (Part B — never invent these)"
AUTHOR_HINT = (  # D134 (A7): read-only; init never sets a git identity
    '# hint: git user.name is not set for this project; set it before the first commit: git config user.name "<name>"'
)

COMMON = {
    "docs/decisions/0001-decision-log.md": "docs/decisions/0001-decision-log.md",
    "docs/conventions/glossary.md": "docs/conventions/glossary.md",
    "docs/conventions/constraints.md": "constraints/README.md",
    "docs/harness/part-b-checklist.md": "part-b-checklist.md",
    "tests/README.md": "tests/README.md",
    "scripts/audit.sh": "tests/audit.sh",
    "scripts/verify.sh": "verify/verify.sh",  # D131: the one verify command of .mesh/config.toml
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


OUTSIDE_NOTE = "resolves outside the project root (a symlink): refused, nothing is written (D111)"


def refused(target: Path, rel: str, log: "Log") -> bool:
    """True (and a REFUSE row) when a write to `rel` would land outside the project root."""
    if layers.contained(target, rel):
        return False
    log.add("REFUSE", rel, OUTSIDE_NOTE)
    return True


@dataclass(frozen=True, slots=True)
class Row:
    action: str  # CREATE | MERGE | SKIP | REFUSE (D111: the target resolves outside the project root)
    path: str  # relative to the target, never absolute
    note: str = ""

    def line(self) -> str:
        return f"{self.action:<7} {self.path}{'  (' + self.note + ')' if self.note else ''}"


@dataclass(frozen=True, slots=True)
class ScaffoldResult:
    target: Path
    stack: str | None  # None: ambiguous
    dry_run: bool
    rows: tuple[Row, ...]
    exit_code: int  # 0 ok, 3 stack ambiguous
    hints: tuple[str, ...] = ()  # printed after NEXT; not part of the rows (nor of mesh_init's plan_id)

    def counts(self) -> dict[str, int]:
        return {k: sum(1 for r in self.rows if r.action == k) for k in ("CREATE", "MERGE", "SKIP")}


class Log:
    def __init__(self, dry: bool, echo: Callable[[str], None] | None = None):
        self.dry, self.rows, self.echo = dry, [], echo

    def add(self, action: str, path: str, note: str = "") -> None:
        row = Row(action, path, note)
        self.rows.append(row)
        if self.echo:
            self.echo(row.line())


def detect_stack(target: Path) -> str | None:
    ts, py = (target / "package.json").exists(), (target / "pyproject.toml").exists()
    if ts and not py:
        return "ts"
    if py and not ts:
        return "python"
    return None


def write_new(target: Path, rel: str, content: str, log: Log, mode: int | None = None) -> None:
    dest = target / rel
    if refused(target, rel, log):
        return
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
    if refused(target, "AGENTS.md", log):
        return
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
    if refused(target, ".gitignore", log):
        return
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
    if refused(target, "package.json", log):
        return
    if not dest.exists():
        log.add("SKIP", "package.json", "absent — create it with your package manager, then rerun init")
        return
    raw = dest.read_text(encoding="utf-8")
    pkg = layers.parse_json(raw)
    wanted = layers.parse_json((TEMPLATES / "constraints/typescript/package-scripts.json").read_text(encoding="utf-8"))
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
    if refused(target, "pyproject.toml", log):
        return
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


def _header(target: Path, stack: str, dry_run: bool) -> str:
    return f"# supervisor-mesh init → {target}  (stack: {stack}{', dry-run' if dry_run else ''})"


def _summary(counts: dict[str, int]) -> str:
    return f"# summary: created={counts['CREATE']} merged={counts['MERGE']} skipped={counts['SKIP']}"


def author_hints(target: Path, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """D134: the hint when git answers that `user.name` is unset for the target (a git that cannot run: no hint)."""
    return (AUTHOR_HINT,) if git.user_name(target, dict(os.environ if env is None else env)) == "" else ()


def scaffold(
    target: Path,
    stack: str,
    dry_run: bool,
    echo: Callable[[str], None] | None = None,
    env: Mapping[str, str] | None = None,
) -> ScaffoldResult:
    """The scaffold steps as a value. `echo` (main only) prints each line the moment it is known. `env` is the
    environment git reads its config with (default: this process's)."""
    target = Path(target).resolve()
    chosen = detect_stack(target) if stack == "auto" else stack
    if chosen is None:
        if echo:
            echo(AMBIGUOUS)
        return ScaffoldResult(target, None, dry_run, (), 3)
    log = Log(dry_run, echo)
    if echo:
        echo(_header(target, chosen, dry_run))
    merge_agents(target, log)
    for rel, src in {**COMMON, **STACK_FILES[chosen]}.items():
        content = (TEMPLATES / src).read_text(encoding="utf-8")
        write_new(target, rel, content, log, 0o755 if rel.endswith((".sh", ".py")) else None)
    merge_gitignore(target, log)
    if chosen == "ts":
        merge_package_scripts(target, log)
    if chosen == "python":
        merge_pyproject(target, log)
    result = ScaffoldResult(target, chosen, dry_run, tuple(log.rows), 0, author_hints(target, env))
    if echo:
        echo(_summary(result.counts()))
        echo(NEXT)
        for hint in result.hints:
            echo(hint)
    return result


def render_text(result: ScaffoldResult) -> str:
    if result.stack is None:
        return AMBIGUOUS + "\n"
    lines = [_header(result.target, result.stack, result.dry_run), *(r.line() for r in result.rows)]
    lines += [_summary(result.counts()), NEXT, *result.hints]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=USAGE)
    ap.add_argument("--target", required=True)
    ap.add_argument("--stack", choices=["auto", "ts", "python", "none"], default="auto")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    return scaffold(Path(a.target), a.stack, a.dry_run, echo=print).exit_code


if __name__ == "__main__":
    sys.exit(main())


# ================================================================ mesh's own bootstrap (the `mesh_init` tool)
# After the scaffold rows, in the same CREATE / MERGE / SKIP vocabulary, every path relative to the project root:
#   .mesh/state.json      the empty document (+ the one-time legacy import of docs/tracker.md)
#   .mesh/config.toml     operator_channel = "terminal" (section 9a) and commented examples
#   .mesh/.gitignore      the logs, the lock, runs/, bin/ and temp files never enter the repo
#   .mesh/bin/mesh        the launcher the hook calls: the ONE place that knows where the plugin lives (gitignored;
#                         refreshed when the plugin moves, so state.json and the hook hold no absolute path)
#   .claude/settings.json the section-9a UserPromptSubmit hook and the D96 `permissions.deny` rules, an additive merge
#                         (an existing key is never replaced; our hook entry is recognised by its command and refreshed
#                         when its command is not the current one; a deny rule by its exact text; a re-run adds nothing;
#                         the pre-D107 `.mesh/**` wildcards mesh wrote are replaced by the narrowed rules)
# Dry run and apply are separate calls: the dry run returns `plan_id` (a hash of the rows and the import mapping);
# apply recomputes the plan and refuses when it differs, so the PM applies exactly what the operator saw.

# The launcher is gitignored, so a fresh clone has none: the hook then does nothing, silently, and exits 0 (a hook that
# fails on every prompt would be noise in every pane). `mesh_status` reports the launcher's health instead.
HOOK_COMMAND = (
    'test -x "$CLAUDE_PROJECT_DIR"/.mesh/bin/mesh || exit 0; '
    '"$CLAUDE_PROJECT_DIR"/.mesh/bin/mesh event record --source terminal; exit 0'
)
HOOK_MARK = ".mesh/bin/mesh event record"
PM_LABEL_HINT = (
    "label the PM pane (D82): start it with `projmux create claude --project <name> --label role=pm`; "
    "until then every pm tool is refused and the operator-event hook records nothing"
)
MESH_GITIGNORE = "state.lock\n*.tmp\nevents.jsonl\nrefusals.jsonl\nruns/\nbin/\n"


class PlanChanged(Exception):  # noqa: N818
    """apply: the recomputed plan differs from the dry run the operator saw."""


class OutsideRoot(Exception):  # noqa: N818
    """apply: a planned write resolves outside the project root (D111); nothing was written."""


@dataclass(frozen=True, slots=True)
class MeshInitResult:
    applied: bool
    stack: str | None
    exit_code: int  # 0 ok, 3 stack ambiguous
    rows: tuple[Row, ...]
    legacy: dict  # {"status": ..., "rows": n, "mapping": [...], "skipped": [...]}
    plan_id: str
    hints: tuple[str, ...] = ()  # the scaffold's (D134), outside the plan_id

    def summary(self) -> dict[str, int]:
        return {k.lower(): sum(1 for r in self.rows if r.action == k) for k in ("CREATE", "MERGE", "SKIP")}


def _launcher(plugin_root: Path) -> str:
    cli = shlex.quote(str(Path(plugin_root) / "mesh" / "cli.py"))
    return (
        "#!/bin/sh\n# supervisor-mesh launcher, generated by mesh_init (refreshed on every init; not committed)\n"
        f'exec python3 {cli} "$@"\n'
    )


def launcher_health(root: Path, plugin_root: Path) -> str:
    """ok | missing | not_executable | stale (it points at another plugin location: re-run mesh_init). No path."""
    dest = Path(root) / ".mesh" / "bin" / "mesh"
    try:
        have = dest.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "not_executable"
    if not os.access(dest, os.X_OK):
        return "not_executable"
    return "ok" if have == _launcher(plugin_root) else "stale"


def write_launcher(root: Path, plugin_root: Path, log: Log) -> None:
    rel = ".mesh/bin/mesh"
    dest, want = root / rel, _launcher(plugin_root)
    if refused(root, rel, log):
        return
    try:
        have = dest.read_text(encoding="utf-8")
    except FileNotFoundError:
        have = None
    if have == want:
        log.add("SKIP", rel, "current")
        return
    if not log.dry:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(want, encoding="utf-8")
        dest.chmod(0o700)
    log.add("CREATE" if have is None else "MERGE", rel, "" if have is None else "launcher refreshed")


# D96 host rules, narrowed by D107: the PM model cannot run the hook command itself or write mesh's own record files
# with its file tools; .mesh/config.toml stays editable (D100 pins its hash at `ready`). Recognised by exact text (the
# marker of an additive merge): an existing rule, or any other key, is never replaced.
DENY_RULES = (
    "Bash(*mesh* event record*)",
    "Bash(*tmux*send-keys*)",  # D113, advisory: the models do not type into panes; mesh's server process does
    "Bash(*tmux*paste-buffer*)",
    "Bash(*agent-hook*ingest*)",  # D119, advisory: a forged `projmux internal agent-hook ingest` moves the D118 anchor
    "Write(.mesh/state.json)",
    "Edit(.mesh/state.json)",
    "Write(.mesh/events.jsonl)",
    "Edit(.mesh/events.jsonl)",
    "Write(.mesh/refusals.jsonl)",
    "Edit(.mesh/refusals.jsonl)",
)
# The pre-D107 wildcards mesh itself wrote: a re-run drops exactly these (every other user rule is kept, in order).
LEGACY_DENY_RULES = ("Write(.mesh/**)", "Edit(.mesh/**)")


def _hook_entries(prompt: list):
    """Every hook dict of ours (its command names the launcher's `event record`) inside the UserPromptSubmit list."""
    for e in prompt:
        if isinstance(e, dict) and isinstance(e.get("hooks"), list):
            for h in e["hooks"]:
                if isinstance(h, dict) and HOOK_MARK in str(h.get("command", "")):
                    yield h


def merge_hook(root: Path, log: Log) -> None:
    """The section-9a hook and the D96 deny rules in the target's .claude/settings.json, additively: missing pieces are
    added, our own hook entry is refreshed when its command is not the current one, nothing else is touched."""
    rel = ".claude/settings.json"
    dest = root / rel
    if refused(root, rel, log):
        return
    entry = {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 10}]}
    if not dest.exists():
        if not log.dry:
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = {"hooks": {"UserPromptSubmit": [entry]}, "permissions": {"deny": list(DENY_RULES)}}
            dest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        log.add("CREATE", rel, "UserPromptSubmit operator-event hook + deny rules")
        return
    raw = dest.read_text(encoding="utf-8")
    try:
        data = layers.parse_json(raw)
    except ValueError:
        data = None
    hooks = data.get("hooks", {}) if isinstance(data, dict) else None
    prompt = hooks.get("UserPromptSubmit", []) if isinstance(hooks, dict) else None
    perms = data.get("permissions", {}) if isinstance(data, dict) else None
    deny = perms.get("deny", []) if isinstance(perms, dict) else None
    if not isinstance(prompt, list) or not isinstance(deny, list):
        log.add("SKIP", rel, "unexpected shape — add the UserPromptSubmit hook by hand: " + HOOK_COMMAND)
        return
    notes = []
    ours = list(_hook_entries(prompt))
    if not ours:
        prompt = [*prompt, entry]
        notes.append("+UserPromptSubmit operator-event hook")
    elif any(h.get("command") != HOOK_COMMAND for h in ours):
        for h in ours:
            h["command"] = HOOK_COMMAND
        notes.append("operator-event hook refreshed")
    legacy = [r for r in deny if r in LEGACY_DENY_RULES]
    if legacy:
        deny = [r for r in deny if r not in LEGACY_DENY_RULES]
        notes.append(f"-{len(legacy)} legacy .mesh/** deny rules (D107)")
    missing = [r for r in DENY_RULES if r not in deny]
    if missing:
        notes.append(f"+{len(missing)} deny rules")
    if not notes:
        log.add("SKIP", rel, "operator-event hook and deny rules present")
        return
    if not log.dry:
        data.setdefault("hooks", hooks)["UserPromptSubmit"] = prompt
        data.setdefault("permissions", perms)["deny"] = [*deny, *missing]
        indent = len(m.group(1)) if (m := re.search(r'\n( +)"', raw)) else 2
        dest.write_text(json.dumps(data, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
    log.add("MERGE", rel, "; ".join(notes))


def _legacy_preview(root: Path):
    path = root / tracker.TRACKER
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return None
    if tracker.BEGIN in text or tracker.END in text:
        return None  # already a generated view: never imported again
    preview = tracker.parse_legacy(text)
    return preview if preview.rows else None


def ensure_state(root: Path, log: Log) -> dict:
    """state.json, and the legacy import ONCE: only into a document with no items and no legacy rows yet."""
    store, rel = Store(root / ".mesh"), ".mesh/state.json"
    if refused(root, rel, log):
        return {"status": "none", "rows": 0, "mapping": [], "skipped": []}
    preview = _legacy_preview(root)
    legacy = {r.id: r for r in preview.rows} if preview else {}
    info = {
        "status": "none",
        "rows": len(legacy),
        "mapping": preview.mapping() if preview else [],
        "skipped": list(preview.skipped) if preview else [],
    }
    if not store.initialized():
        if legacy:
            info["status"] = "will import" if log.dry else "imported"
        if not log.dry:
            store.create(replace(empty_doc(), legacy=legacy))
        log.add("CREATE", rel, f"+{len(legacy)} legacy rows from docs/tracker.md" if legacy else "empty")
        return info
    doc = store.read().doc
    if doc.legacy or doc.items or not legacy:
        info["status"] = "already imported" if doc.legacy else info["status"]
        info.update(rows=len(doc.legacy) if doc.legacy else info["rows"])
        if doc.legacy or doc.items:
            info.update(mapping=[], skipped=[])
        log.add("SKIP", rel, "exists")
        return info
    info["status"] = "will import" if log.dry else "imported"
    if not log.dry:
        with store.locked() as txn:
            if not txn.doc.legacy and not txn.doc.items:
                txn.commit(replace(txn.doc, legacy=legacy))
    log.add("MERGE", rel, f"+{len(legacy)} legacy rows from docs/tracker.md")
    return info


def _plan_id(stack: str | None, rows: tuple[Row, ...], legacy: dict) -> str:
    body = {"stack": stack, "rows": [[r.action, r.path, r.note] for r in rows], "legacy": legacy["mapping"]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _bootstrap(
    root: Path, plugin_root: Path, stack: str, dry_run: bool, env: Mapping[str, str] | None = None
) -> MeshInitResult:
    sc = scaffold(root, stack, dry_run, env=env)
    if sc.stack is None:
        return MeshInitResult(not dry_run, None, 3, (), {"status": "none", "rows": 0, "mapping": [], "skipped": []}, "")
    log = Log(dry_run)
    if not dry_run:
        (root / ".mesh").mkdir(parents=True, exist_ok=True)
    legacy = ensure_state(root, log)
    write_new(root, ".mesh/config.toml", defaults.config_toml(sc.stack), log)
    write_new(root, ".mesh/.gitignore", MESH_GITIGNORE, log)
    write_launcher(root, plugin_root, log)
    merge_hook(root, log)
    rows = sc.rows + tuple(log.rows)
    return MeshInitResult(not dry_run, sc.stack, 0, rows, legacy, _plan_id(sc.stack, rows, legacy), sc.hints)


def mesh_init(
    root: Path,
    plugin_root: Path,
    stack: str,
    apply: bool,
    plan_id: str | None,
    env: Mapping[str, str] | None = None,
) -> MeshInitResult:
    """The dry run, or the apply of exactly the plan the dry run returned (PlanChanged otherwise)."""
    root = Path(root).resolve()
    preview = _bootstrap(root, plugin_root, stack, dry_run=True, env=env)
    if not apply or preview.exit_code:
        return preview
    if plan_id != preview.plan_id:
        raise PlanChanged("the project changed since the dry run; run mesh_init without apply again and re-show it")
    outside = [r.path for r in preview.rows if r.action == "REFUSE"]
    if outside:
        raise OutsideRoot(f"refused: {', '.join(outside)} resolve outside the project root (D111); nothing was written")
    return _bootstrap(root, plugin_root, stack, dry_run=False, env=env)
