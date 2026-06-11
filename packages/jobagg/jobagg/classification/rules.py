"""Rule file loading helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


RULES_DIR = Path(__file__).with_name("rules")


@lru_cache(maxsize=None)
def load_rule_file(name: str) -> dict[str, Any]:
    path = RULES_DIR / name
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def rules_path(name: str) -> Path:
    return RULES_DIR / name
