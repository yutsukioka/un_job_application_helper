"""Runtime configuration for the local job API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class ApiSettings:
    repo_root: Path
    db_path: Path
    saved_searches_path: Path
    tracker_path: Path
    worker_db_path: Path | None = None


def load_settings() -> ApiSettings:
    root = repo_root()
    private_jobagg = root / "private" / "jobagg"
    output_dir = private_jobagg / "output"
    return ApiSettings(
        repo_root=root,
        db_path=Path(os.environ.get("JOB_API_DB", output_dir / "all_jobs.sqlite3")),
        saved_searches_path=Path(
            os.environ.get("JOB_API_SAVED_SEARCHES", private_jobagg / "saved_searches.json")
        ),
        tracker_path=Path(
            os.environ.get("JOB_API_TRACKER", private_jobagg / "application_tracker.json")
        ),
        worker_db_path=Path(os.environ["JOB_API_WORKER_DB"]) if os.environ.get("JOB_API_WORKER_DB") else None,
    )
