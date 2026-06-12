"""Operational confidence report for collected job bundles."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


VERIFIED_EMPTY_REASONS = {"verified_total_zero", "verified_structural_empty", "verified_text_empty"}
ISSUE_HEALTH_STATUSES = {"issue"}
WARN_HEALTH_STATUSES = {"warning", "degraded"}


@dataclass(slots=True)
class OpsSourceCheck:
    source_id: str
    db_path: Path
    observed_at: str | None = None
    fetched: int | None = None
    open_jobs: int = 0
    missing_jobs: int = 0
    closed_jobs: int = 0
    error_count: int = 0
    health_status: str | None = None
    run_classification: str | None = None
    blocked: bool = False
    transient_error: bool = False
    scope_validation_status: str | None = None
    pagination_complete: bool | None = None
    total_reported_by_source: int | None = None
    detail_attempted: int = 0
    detail_failed: int = 0
    detail_skipped: int = 0
    missing_transition_allowed: bool | None = None
    empty_reason: str | None = None
    severity: str = "PASS"
    findings: list[str] = field(default_factory=list)

    @property
    def active_jobs(self) -> int:
        return self.open_jobs + self.missing_jobs


@dataclass(slots=True)
class OpsCheckReport:
    output_dir: Path | None
    db_paths: list[Path]
    checks: list[OpsSourceCheck]
    generated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def fail_count(self) -> int:
        return sum(1 for check in self.checks if check.severity == "FAIL")

    @property
    def warn_count(self) -> int:
        return sum(1 for check in self.checks if check.severity == "WARN")

    @property
    def pass_count(self) -> int:
        return sum(1 for check in self.checks if check.severity == "PASS")


def collect_ops_check(
    *,
    db_path: str | Path,
    output_dir: str | Path | None = None,
    all_bundles: bool = False,
) -> OpsCheckReport:
    """Collect read-only operational checks for one DB or all bundle DBs."""

    resolved_output_dir = Path(output_dir) if output_dir else None
    paths = _db_paths(db_path=Path(db_path), output_dir=resolved_output_dir, all_bundles=all_bundles)
    checks = [_check_database(path) for path in paths]
    return OpsCheckReport(output_dir=resolved_output_dir, db_paths=paths, checks=checks)


def ops_check_to_markdown(report: OpsCheckReport) -> str:
    lines = [
        "# Job Aggregator Operational Check",
        "",
        f"Generated at: `{report.generated_at.isoformat()}`",
        f"Output directory: `{report.output_dir or 'n/a'}`",
        f"Databases inspected: `{len(report.db_paths)}`",
        "",
        "## Summary",
        "",
        f"- `PASS`: {report.pass_count}",
        f"- `WARN`: {report.warn_count}",
        f"- `FAIL`: {report.fail_count}",
        "",
        "## Source Status",
        "",
    ]
    columns = [
        ("severity", "Status"),
        ("source_id", "Source"),
        ("fetched", "Fetched"),
        ("open_jobs", "Open"),
        ("missing_jobs", "Missing"),
        ("closed_jobs", "Closed"),
        ("error_count", "Errors"),
        ("health_status", "Health"),
        ("run_classification", "Class"),
        ("scope_validation_status", "Scope"),
        ("pagination", "Pagination"),
        ("details", "Details"),
        ("missing_transition", "Missing Transition"),
        ("observed_at", "Observed"),
    ]
    lines.append("| " + " | ".join(label for _, label in columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for check in sorted(report.checks, key=lambda item: (item.severity, item.source_id)):
        lines.append("| " + " | ".join(_markdown_value(check, key) for key, _ in columns) + " |")

    lines.extend(["", "## Findings", ""])
    finding_lines = []
    for check in sorted(report.checks, key=lambda item: (item.severity, item.source_id)):
        for finding in check.findings:
            finding_lines.append(f"- `{check.severity}` `{check.source_id}`: {finding}")
    if finding_lines:
        lines.extend(finding_lines)
    else:
        lines.append("No operational findings.")
    lines.append("")
    return "\n".join(lines)


def _db_paths(*, db_path: Path, output_dir: Path | None, all_bundles: bool) -> list[Path]:
    if not all_bundles:
        return [db_path]
    candidates: list[Path] = []
    if output_dir and output_dir.exists():
        candidates = sorted(
            path
            for path in output_dir.glob("*_jobs.sqlite3")
            if path.name != "all_jobs.sqlite3"
        )
    if candidates:
        return candidates
    if db_path.exists():
        return [db_path]
    if output_dir and (output_dir / "all_jobs.sqlite3").exists():
        return [output_dir / "all_jobs.sqlite3"]
    return [db_path]


def _check_database(path: Path) -> OpsSourceCheck:
    source_id = _source_id_from_path(path)
    check = OpsSourceCheck(source_id=source_id, db_path=path)
    if not path.exists():
        check.severity = "FAIL"
        check.findings.append(f"database not found: {path}")
        return check

    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if not _table_exists(conn, "jobs"):
                check.severity = "FAIL"
                check.findings.append("jobs table is missing")
                return check
            _apply_job_counts(conn, check)
            if _table_exists(conn, "source_runs"):
                _apply_latest_run(conn, check)
            else:
                check.findings.append("source_runs table is missing")
            if _table_exists(conn, "source_run_diagnostics"):
                _apply_latest_diagnostics(conn, check)
            else:
                check.findings.append("source_run_diagnostics table is missing; run a fresh sync with current code")
    except sqlite3.Error as exc:
        check.severity = "FAIL"
        check.findings.append(f"sqlite read failed: {exc}")
        return check

    _grade_check(check)
    return check


def _apply_job_counts(conn: sqlite3.Connection, check: OpsSourceCheck) -> None:
    rows = conn.execute("SELECT source_id, status, COUNT(*) AS count FROM jobs GROUP BY source_id, status").fetchall()
    if len({row["source_id"] for row in rows}) == 1:
        check.source_id = str(rows[0]["source_id"])
    for row in rows:
        count = int(row["count"] or 0)
        if row["status"] == "open":
            check.open_jobs += count
        elif row["status"] == "missing":
            check.missing_jobs += count
        elif row["status"] == "closed":
            check.closed_jobs += count


def _apply_latest_run(conn: sqlite3.Connection, check: OpsSourceCheck) -> None:
    row = conn.execute(
        """
        SELECT *
        FROM source_runs
        ORDER BY observed_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        check.findings.append("source_runs has no rows")
        return
    check.source_id = str(row["source_id"])
    check.observed_at = row["observed_at"]
    check.fetched = int(row["fetched"] or 0)
    errors = _parse_json(row["errors_json"], [])
    check.error_count = len(errors) if isinstance(errors, list) else 1
    if check.error_count:
        check.findings.append(f"latest run recorded {check.error_count} error(s)")


def _apply_latest_diagnostics(conn: sqlite3.Connection, check: OpsSourceCheck) -> None:
    row = conn.execute(
        """
        SELECT *
        FROM source_run_diagnostics
        ORDER BY observed_at DESC, source_run_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        check.findings.append("source_run_diagnostics has no rows")
        return
    check.health_status = row["health_status"]
    columns = set(row.keys())
    if "run_classification" in columns:
        check.run_classification = row["run_classification"]
    if "blocked" in columns:
        check.blocked = bool(row["blocked"])
    if "transient_error" in columns:
        check.transient_error = bool(row["transient_error"])
    check.scope_validation_status = row["scope_validation_status"]
    check.pagination_complete = _optional_bool(row["pagination_complete"])
    check.total_reported_by_source = _optional_int(row["total_reported_by_source"])
    check.detail_attempted = int(row["detail_attempted"] or 0)
    check.detail_failed = int(row["detail_failed"] or 0)
    check.detail_skipped = int(row["detail_skipped"] or 0)
    check.missing_transition_allowed = _optional_bool(row["missing_transition_allowed"])
    check.empty_reason = row["empty_reason"]


def _grade_check(check: OpsSourceCheck) -> None:
    if check.health_status in ISSUE_HEALTH_STATUSES:
        check.findings.append(f"health_status is {check.health_status}")
    elif check.health_status in WARN_HEALTH_STATUSES:
        check.findings.append(f"health_status is {check.health_status}")
    if check.run_classification in {"blocked", "transient_error", "inconclusive", "parser_error"}:
        check.findings.append(f"run_classification is {check.run_classification}")
    elif check.run_classification == "detail_degraded":
        check.findings.append("run_classification is detail_degraded")
    if check.blocked:
        check.findings.append("source appears blocked")
    if check.transient_error:
        check.findings.append("source hit a transient list error")

    if check.scope_validation_status and check.scope_validation_status not in {"passed", "not_applicable"}:
        check.findings.append(f"scope validation is {check.scope_validation_status}")
    if check.pagination_complete is False:
        check.findings.append("pagination is incomplete")

    if check.fetched == 0:
        verified_empty = check.empty_reason in VERIFIED_EMPTY_REASONS
        if check.active_jobs and not verified_empty:
            check.findings.append("zero fetched with active jobs and no verified-empty evidence")
        elif not verified_empty:
            check.findings.append("zero fetched without verified-empty diagnostics")

    if check.detail_attempted:
        failure_ratio = check.detail_failed / check.detail_attempted
        if failure_ratio > 0.25:
            check.findings.append(f"detail failure ratio is {failure_ratio:.0%}")
        elif failure_ratio >= 0.05:
            check.findings.append(f"detail failure ratio is {failure_ratio:.0%}")

    fail_signals = (
        check.error_count > 0
        or check.health_status in ISSUE_HEALTH_STATUSES
        or check.run_classification in {"blocked", "transient_error", "parser_error"}
        or check.blocked
        or check.transient_error
        or check.pagination_complete is False
        or (check.fetched == 0 and check.active_jobs and check.empty_reason not in VERIFIED_EMPTY_REASONS)
        or (
            check.scope_validation_status is not None
            and check.scope_validation_status not in {"passed", "not_applicable"}
        )
        or (check.detail_attempted > 0 and check.detail_failed / check.detail_attempted > 0.25)
    )
    warn_signals = (
        check.health_status in WARN_HEALTH_STATUSES
        or check.run_classification in {"inconclusive", "detail_degraded"}
        or any("source_run_diagnostics table is missing" in finding for finding in check.findings)
        or any("source_runs" in finding for finding in check.findings)
        or (check.fetched == 0 and check.empty_reason not in VERIFIED_EMPTY_REASONS)
        or (check.detail_attempted > 0 and check.detail_failed / check.detail_attempted >= 0.05)
    )
    if fail_signals:
        check.severity = "FAIL"
    elif warn_signals:
        check.severity = "WARN"
    else:
        check.severity = "PASS"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _parse_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _source_id_from_path(path: Path) -> str:
    name = path.name
    if name.endswith("_jobs.sqlite3"):
        return name[: -len("_jobs.sqlite3")]
    if name.endswith(".sqlite3"):
        return name[: -len(".sqlite3")]
    return name


def _markdown_value(check: OpsSourceCheck, key: str) -> str:
    if key == "pagination":
        if check.pagination_complete is None:
            return "n/a"
        total = f" / {check.total_reported_by_source}" if check.total_reported_by_source is not None else ""
        return _escape(f"{'yes' if check.pagination_complete else 'no'}{total}")
    if key == "details":
        if not (check.detail_attempted or check.detail_failed or check.detail_skipped):
            return "n/a"
        return _escape(f"{check.detail_failed}/{check.detail_attempted} failed, {check.detail_skipped} skipped")
    if key == "missing_transition":
        if check.missing_transition_allowed is None:
            return "n/a"
        return "yes" if check.missing_transition_allowed else "no"
    value = getattr(check, key)
    if value is None:
        return "n/a"
    return _escape(str(value))


def _escape(value: str) -> str:
    return " ".join(value.replace("|", "\\|").split())
