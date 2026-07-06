from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict[str, object]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict[str, object]]:
    workflow = _workflow()
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    steps: list[dict[str, object]] = []
    for job in jobs.values():
        assert isinstance(job, dict)
        job_steps = job.get("steps")
        assert isinstance(job_steps, list)
        steps.extend(step for step in job_steps if isinstance(step, dict))
    return steps


def test_D3_workflow_has_read_only_permissions_and_pinned_actions() -> None:
    workflow = _workflow()

    assert workflow.get("permissions") == {"contents": "read"}

    uses_values = [step["uses"] for step in _steps() if isinstance(step.get("uses"), str)]
    assert uses_values
    for uses in uses_values:
        assert re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}", uses)


def test_D3_workflow_runs_security_gates() -> None:
    run_text = "\n".join(
        str(step.get("run") or "")
        for step in _steps()
    )

    for required in (
        "pip-audit",
        "bandit -q -r",
        "ruff check --select=S",
        "pytest -m security",
    ):
        assert required in run_text


def test_D3_pytest_security_marker_is_registered() -> None:
    pytest_ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")

    assert re.search(r"^\s*security\s*:", pytest_ini, flags=re.MULTILINE)
