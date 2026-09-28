#!/usr/bin/env python3
"""Expose canonical UNESCO skills through reversible Codex discovery symlinks.

Default: ~/.agents/skills (user scope). --scope project uses .agents/skills
under this repository. Never replaces an existing different skill or link.
--check is read-only and returns 1 if installation is incomplete or conflicting.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

NAMES = ("apex-generate-unesco-employment-history", "apex-select-domain-of-expertise",
         "apex-curate-publications", "apex-guardrails", "capel-fit")
REPO = Path(__file__).resolve().parents[3]
SOURCES = REPO / "agents/apex/skills"


def inspect(destination: Path, sources: Path = SOURCES) -> list[dict]:
    rows = []
    for name in NAMES:
        source, target = sources / name, destination / name
        state = "missing"
        if not (source / "SKILL.md").is_file():
            state = "source_missing"
        elif target.is_symlink() and target.resolve() == source.resolve():
            state = "installed"
        elif target.exists() or target.is_symlink():
            state = "conflict"
        rows.append({"name": name, "state": state, "source": str(source), "target": str(target)})
    return rows


def install(destination: Path, sources: Path = SOURCES) -> list[dict]:
    rows = inspect(destination, sources)
    if any(r["state"] in {"source_missing", "conflict"} for r in rows):
        raise ValueError("Installation has missing sources or existing conflicting destinations; nothing replaced")
    destination.mkdir(parents=True, exist_ok=True)
    for row in rows:
        if row["state"] == "missing":
            Path(row["target"]).symlink_to(Path(row["source"]).resolve(), target_is_directory=True)
    return inspect(destination, sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=["user", "project"], default="user")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    destination = (Path.home() if args.scope == "user" else REPO) / ".agents/skills"
    try:
        rows = inspect(destination) if args.check else install(destination)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"scope": args.scope, "skills": rows}, indent=2))
    return 0 if all(r["state"] == "installed" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
