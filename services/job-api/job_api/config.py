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
    allow_lan: bool = False
    api_token: str | None = None
    unix_socket: Path | None = None
    scoring_root: Path = Path("strategies")
    scoring_max_bytes: int = 2 * 1024 * 1024


def load_settings() -> ApiSettings:
    root = repo_root()
    private_jobagg = root / "private" / "jobagg"
    output_dir = private_jobagg / "output"
    unix_socket = os.environ.get("JOB_API_UNIX_SOCKET")
    scoring_root = os.environ.get("JOB_API_SCORING_ROOT")
    return ApiSettings(
        repo_root=root,
        db_path=Path(os.environ.get("JOB_API_DB", output_dir / "all_jobs.sqlite3")),
        saved_searches_path=Path(
            os.environ.get("JOB_API_SAVED_SEARCHES", private_jobagg / "saved_searches.json")
        ),
        tracker_path=Path(
            os.environ.get("JOB_API_TRACKER", private_jobagg / "application_tracker.json")
        ),
        allow_lan=os.environ.get("JOB_API_ALLOW_LAN") == "1",
        api_token=os.environ.get("JOB_API_TOKEN") or None,
        unix_socket=Path(unix_socket) if unix_socket else None,
        scoring_root=Path(scoring_root) if scoring_root else root / "strategies",
        scoring_max_bytes=_int_env("JOB_API_SCORING_MAX_BYTES", 2 * 1024 * 1024),
    )


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default
