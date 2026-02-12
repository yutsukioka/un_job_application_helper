#!/usr/bin/env python3
"""
Validate every skill directory under .agents/skills/.

Checks performed:
  1. Each skill directory contains a SKILL.md with valid YAML frontmatter
     that includes non-empty 'name' and 'description' fields.
  2. Each skill directory contains agents/openai.yaml with a valid YAML
     structure including interface.display_name, interface.short_description,
     and interface.default_prompt.
  3. The 'name' field in SKILL.md matches the directory name.

Usage (from repo root):
    python3 .agents/scripts/validate_skills.py
"""

import re
import sys
import pathlib

try:
    import yaml
except ImportError:
    print("PyYAML not installed. Install with: pip install pyyaml")
    sys.exit(2)

root = pathlib.Path(__file__).resolve().parent.parent / "skills"
if not root.is_dir():
    print(f"Skills directory not found: {root}")
    sys.exit(2)

bad: list[tuple[str, str]] = []

fm_re = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def validate_skill_md(skill_dir: pathlib.Path) -> None:
    """Validate SKILL.md frontmatter in a skill directory."""
    name = skill_dir.name
    f = skill_dir / "SKILL.md"
    if not f.exists():
        bad.append((name, "missing SKILL.md"))
        return

    text = f.read_text(encoding="utf-8")
    m = fm_re.match(text)
    if not m:
        bad.append((name, "missing YAML frontmatter in SKILL.md"))
        return

    try:
        meta = yaml.safe_load(m.group(1))
    except Exception as e:
        bad.append((name, f"SKILL.md YAML parse error: {e}"))
        return

    if not isinstance(meta, dict):
        bad.append((name, "SKILL.md frontmatter is not a YAML mapping"))
        return

    for k in ("name", "description"):
        if k not in meta or not meta[k]:
            bad.append((name, f"SKILL.md missing/empty '{k}'"))

    if meta.get("name") and meta["name"] != name:
        bad.append(
            (name, f"SKILL.md 'name' is '{meta['name']}' but directory is '{name}'")
        )


def validate_openai_yaml(skill_dir: pathlib.Path) -> None:
    """Validate agents/openai.yaml in a skill directory."""
    name = skill_dir.name
    f = skill_dir / "agents" / "openai.yaml"
    if not f.exists():
        bad.append((name, "missing agents/openai.yaml"))
        return

    text = f.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except Exception as e:
        bad.append((name, f"openai.yaml YAML parse error: {e}"))
        return

    if not isinstance(data, dict):
        bad.append((name, "openai.yaml is not a YAML mapping"))
        return

    iface = data.get("interface")
    if not isinstance(iface, dict):
        bad.append((name, "openai.yaml missing 'interface' mapping"))
        return

    for k in ("display_name", "short_description", "default_prompt"):
        if k not in iface or not iface[k]:
            bad.append((name, f"openai.yaml missing/empty 'interface.{k}'"))


for d in sorted(root.iterdir()):
    if not d.is_dir() or d.name.startswith("."):
        continue
    validate_skill_md(d)
    validate_openai_yaml(d)

if bad:
    print("Skill validation FAILED:")
    for name, err in bad:
        print(f"  - {name}: {err}")
    sys.exit(1)

print(
    f"Skill validation OK: all {sum(1 for d in root.iterdir() if d.is_dir() and not d.name.startswith('.'))} "
    f"skills pass (SKILL.md frontmatter + agents/openai.yaml structure verified)."
)
