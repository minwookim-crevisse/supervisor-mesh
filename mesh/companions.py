"""Which D87 companion plugins are present for THIS project. Presence only: the caller gets one boolean per companion
and nothing else (no other plugin names, marketplaces, paths, versions or the registry), the same approach as
`skills/init/companions.py`.

A Claude plugin counts when the registry (`~/.claude/plugins/installed_plugins.json`) lists it at user scope, or at
project/local scope for this project's root. gstack is not a Claude plugin (git clone + setup script): it counts when
its skill directory exists at user or project level, either with a top-level SKILL.md or with any one of its skills
(`gstack/<skill>/SKILL.md`). Any name also counts as a standalone skill directory (`skill_present`):
`<home>/.claude/skills/<name>/SKILL.md` or `<home>/.claude/skills/gstack/<name>/SKILL.md`, a symlinked directory
included. An unreadable registry reports every plugin missing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import layers, runner

PLUGINS = ("mattpocock-skills", "pstack")
D87 = ("gstack", *PLUGINS)


def present(home: Path | None, root: Path) -> dict[str, bool]:
    found = {name for name in D87 if skill_present(home, name)}
    if home is not None:
        found |= _registered(home / ".claude" / "plugins" / "installed_plugins.json", root)
    if any(_gstack(base / ".claude" / "skills" / "gstack") for base in (home, root) if base is not None):
        found.add("gstack")
    return {name: name in found for name in D87}


def skill_present(home: Path | None, name: str) -> bool:
    """A standalone skill directory under home, by name only."""
    if home is None:
        return False
    base = home / ".claude" / "skills"
    return any(_is_file(p) for p in (base / name / "SKILL.md", base / "gstack" / name / "SKILL.md"))


def _gstack(skills: Path) -> bool:
    try:
        return _is_file(skills / "SKILL.md") or any(_is_file(p / "SKILL.md") for p in skills.iterdir())
    except OSError:
        return False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _registered(registry: Path, root: Path) -> set[str]:
    try:
        data = layers.parse_json(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return set()
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return set()
    here = os.path.realpath(root)
    found = set()
    for key, value in plugins.items():
        name = key.split("@", 1)[0] if isinstance(key, str) else None
        if name not in PLUGINS:
            continue
        for entry in value if isinstance(value, list) else [value]:
            if not isinstance(entry, dict):
                continue
            scope = entry.get("scope", "user")
            project = entry.get("projectPath")
            if scope == "user" or (isinstance(project, str) and os.path.realpath(project) == here):
                found.add(name)
    return found


# ---------------------------------------------------------------- D88: the install plan (fixed data) and the run
@dataclass(frozen=True, slots=True)
class Step:
    plugin: str
    argv: tuple[str, ...]  # fixed: no caller-supplied text; always `--scope project` (mesh never touches user scope)
    unless_declared: str | None = None  # skipped when this marketplace is already declared

    @property
    def command(self) -> str:
        return " ".join(self.argv)


PLAN: tuple[Step, ...] = (
    Step("pstack", ("claude", "plugin", "marketplace", "add", "cursor/plugins", "--scope", "project")),
    Step("pstack", ("claude", "plugin", "install", "pstack@cursor-plugins", "--scope", "project")),
    Step(
        "mattpocock-skills",
        ("claude", "plugin", "marketplace", "add", "anthropics/claude-plugins-official", "--scope", "project"),
        unless_declared="claude-plugins-official",
    ),
    Step(
        "mattpocock-skills",
        ("claude", "plugin", "install", "mattpocock-skills@claude-plugins-official", "--scope", "project"),
    ),
)
GSTACK_COMMAND = (
    "git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack "
    "&& cd ~/.claude/skills/gstack && ./setup"
)  # not a Claude plugin, user scope: printed for the operator, never run by mesh
STEP_TIMEOUT_S = 300


def plan_for(have: dict[str, bool]) -> dict:
    steps = [
        {
            "plugin": s.plugin,
            "command": s.command,
            **({"skipped_when_declared": s.unless_declared} if s.unless_declared else {}),
        }
        for s in PLAN
        if not have.get(s.plugin)
    ]
    manual = [] if have.get("gstack") else [{"plugin": "gstack", "command": GSTACK_COMMAND}]
    return {"steps": steps, "manual": manual}


def wanted(have: dict[str, bool], only: str | None) -> list[str]:
    """The missing plugins the install covers, in plan order."""
    return list(dict.fromkeys(s.plugin for s in PLAN if not have.get(s.plugin) and only in (None, s.plugin)))


def declared_marketplaces(env, cwd: Path) -> set[str]:
    """Marketplace NAMES from `claude plugin marketplace list --json`; used for one membership test, never returned.
    A failure reads as 'none declared' (the add step then runs)."""
    r = runner.run(["claude", "plugin", "marketplace", "list", "--json"], timeout=60, cwd=cwd, env=env)
    if not r.ok:
        return set()
    try:
        data = layers.parse_json(r.out)
    except ValueError:
        return set()
    if isinstance(data, dict):
        data = data.get("marketplaces") if isinstance(data.get("marketplaces"), list) else [{"name": k} for k in data]
    items = data if isinstance(data, list) else []
    return {i["name"] for i in items if isinstance(i, dict) and isinstance(i.get("name"), str)}


def installed_version(home: Path | None, root: Path, plugin: str) -> str | None:
    """The version (or commit) the registry records for `plugin` at project/local scope for THIS root, else None."""
    if home is None:
        return None
    try:
        data = layers.parse_json((home / ".claude" / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    here = os.path.realpath(root)
    for key, value in (plugins or {}).items() if isinstance(plugins, dict) else ():
        if not isinstance(key, str) or key.split("@", 1)[0] != plugin:
            continue
        for entry in value if isinstance(value, list) else [value]:
            if isinstance(entry, dict) and isinstance(entry.get("projectPath"), str):
                if os.path.realpath(entry["projectPath"]) == here:
                    found = entry.get("version") or entry.get("gitCommitSha")
                    return str(found) if found else None
    return None


def run_plan(plugins: list[str], env, root: Path, home: Path | None) -> dict:
    """The fixed steps for `plugins`, in order, each through the runner with a timeout. A failed step stops the rest
    of that plugin's steps. Returns each command's exit code and each plugin's installed version afterwards."""
    steps: list[dict] = []
    failed: set[str] = set()
    declared: set[str] | None = None
    for step in (s for s in PLAN if s.plugin in plugins):
        row = {"plugin": step.plugin, "command": step.command}
        if step.plugin in failed:
            steps.append({**row, "exit_code": None, "skipped": "an earlier step failed"})
            continue
        if step.unless_declared:
            declared = declared_marketplaces(env, root) if declared is None else declared
            if step.unless_declared in declared:
                steps.append({**row, "exit_code": None, "skipped": "marketplace already declared"})
                continue
        r = runner.run(list(step.argv), timeout=STEP_TIMEOUT_S, cwd=root, env=env)
        row["exit_code"] = r.code
        if r.spawn_error or r.timed_out:
            row["error"] = "claude could not be started" if r.spawn_error else f"timed out after {STEP_TIMEOUT_S}s"
        if not r.ok:
            failed.add(step.plugin)
        steps.append(row)
    versions = {p: None if p in failed else installed_version(home, root, p) for p in plugins}
    return {"steps": steps, "versions": versions, "all_ok": not failed}
