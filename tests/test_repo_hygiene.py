from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tracked_files_exclude_private_and_generated_artifacts() -> None:
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    forbidden_prefixes = (
        ".agents/",
        "private/",
        "inputs/",
        "output/",
        "tmp/",
        "logs/",
        "bak/",
    )
    forbidden_suffixes = (
        ".sqlite3",
        ".sqlite",
        ".db",
        ".har",
        ".jsonl",
        "CCOG_9_2015.pdf",
        "ccog_reference_full.md",
    )
    offenders = [
        path
        for path in tracked
        if path.startswith(forbidden_prefixes) or path.endswith(forbidden_suffixes)
    ]
    assert offenders == []
