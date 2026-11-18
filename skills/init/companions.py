#!/usr/bin/env python3
"""supervisor-mesh init — report which recommended companions are installed.

Presence only: prints one line per recommended companion and nothing else. It
never prints other plugin names, marketplaces, paths, versions, scopes or the
registry itself, so the user's plugin registry stays out of the session.

usage: companions.py [--registry PATH]
exit:  0 always (a missing or unreadable registry reports every companion missing)
"""
import argparse
import json
import sys
from pathlib import Path

COMPANIONS = ["ready", "office-hours", "plan-eng-review", "karpathy-guidelines"]
DEFAULT_REGISTRY = "~/.claude/plugins/installed_plugins.json"


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    args = ap.parse_args(argv)
    try:
        have = installed(Path(args.registry).expanduser())
    except Exception:  # why: never leak a traceback (it would carry registry paths)
        have = set()
    for name in COMPANIONS:
        print(f"companion {name}: {'installed' if name in have else 'missing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
