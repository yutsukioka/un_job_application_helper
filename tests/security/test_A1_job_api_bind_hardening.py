from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[2]


def _run_startup_check(args: list[str], env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = {
        "PYTHONPATH": f"{ROOT / 'services' / 'job-api'}:{ROOT / 'packages' / 'jobagg'}",
    }
    if env:
        run_env.update(env)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "job_api.app",
            *args,
            "--check-startup",
        ],
        cwd=ROOT,
        env=run_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_A1_job_api_default_startup_check_remains_loopback() -> None:
    result = _run_startup_check([])

    assert result.returncode == 0


def test_A1_job_api_refuses_lan_bind_without_token() -> None:
    result = _run_startup_check(["--host", "0.0.0.0"])

    assert result.returncode != 0
    assert "JOB_API_ALLOW_LAN=1" in result.stderr
    assert "X-Job-Api-Token" in result.stderr


def test_A1_job_api_allows_lan_bind_with_explicit_token_hardening() -> None:
    result = _run_startup_check(
        ["--host", "0.0.0.0"],
        env={"JOB_API_ALLOW_LAN": "1", "JOB_API_TOKEN": "test-token"},
    )

    assert result.returncode == 0
