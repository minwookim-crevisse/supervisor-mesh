#!/usr/bin/env python3
"""supervisor-mesh init — report which recommended companions are installed.

Presence only: prints one line per recommended companion and nothing else. It
never prints other plugin names, marketplaces, paths, versions, scopes or the
registry itself, so the user's plugin registry stays out of the session.

A companion counts when a registered plugin ships it as a skill, or when it is a
standalone skill directory (gstack installs its skills this way, not as a plugin):
<home>/.claude/skills/<name>/SKILL.md or <home>/.claude/skills/gstack/<name>/SKILL.md
(a symlinked directory counts).

usage: companions.py [--registry PATH] [--home DIR]
exit:  0 always (a missing or unreadable registry reports every companion missing)
"""
import argparse
import json
import sys
from pathlib import Path

COMPANIONS = ["ready", "office-hours", "plan-eng-review", "karpathy-guidelines"]
REGISTRY = Path(".claude") / "plugins" / "installed_plugins.json"


def entries(value: object) -> list[dict]:
    items = value if isinstance(value, list) else [value]
    return [e for e in items if isinstance(e, dict)]


def installed(registry: Path) -> set[str]:
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return set()
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return set()
    found = set()
    for key, value in plugins.items():
        if not isinstance(key, str):
            continue
        found.add(key.split("@", 1)[0])
        for entry in entries(value):
            root = entry.get("installPath")
            if not isinstance(root, str):
                continue
            for name in COMPANIONS:
                try:
                    if (Path(root) / "skills" / name / "SKILL.md").is_file():
                        found.add(name)
                except OSError:
                    continue
    return found & set(COMPANIONS)


def skill_dirs(home: Path) -> set[str]:
    """The companions that exist as standalone skills under home (presence by name only)."""
    base = home / ".claude" / "skills"
    found = set()
    for name in COMPANIONS:
        for path in (base / name / "SKILL.md", base / "gstack" / name / "SKILL.md"):
            try:
                if path.is_file():
                    found.add(name)
            except OSError:
                continue
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", default=None, help="default: <home>/.claude/plugins/installed_plugins.json")
    ap.add_argument("--home", default="~")
    args = ap.parse_args(argv)
    try:
        home = Path(args.home).expanduser()
        have = installed(Path(args.registry).expanduser() if args.registry else home / REGISTRY)
        have |= skill_dirs(home)
    except Exception:  # why: never leak a traceback (it would carry registry paths)
        have = set()
    for name in COMPANIONS:
        print(f"companion {name}: {'installed' if name in have else 'missing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
