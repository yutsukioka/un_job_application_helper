"""Source synchronization pipeline."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from jobagg.adapters.base import AdapterContext, get_adapter_class
from jobagg.db import JobDatabase
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource, SourceRunDiagnostics, SyncResult
from jobagg.robots import RobotsChecker, RobotsPolicy

_VERIFIED_EMPTY_REASONS = {"verified_total_zero", "verified_structural_empty", "verified_text_empty"}
_MISSING_ALLOWED_HEALTH = {"ok", "ok_empty"}


def register_builtin_adapters() -> None:
    from jobagg.adapters import avature  # noqa: F401
    from jobagg.adapters import csod  # noqa: F401
    from jobagg.adapters import custom_html  # noqa: F401
    from jobagg.adapters import icddrb  # noqa: F401
    from jobagg.adapters import icims  # noqa: F401
    from jobagg.adapters import imo  # noqa: F401
    from jobagg.adapters import inspira  # noqa: F401
    from jobagg.adapters import oracle_hcm  # noqa: F401
    from jobagg.adapters import pageup  # noqa: F401
    from jobagg.adapters import peoplesoft  # noqa: F401
    from jobagg.adapters import playwright_discovery  # noqa: F401
    from jobagg.adapters import smartrecruiters  # noqa: F401
    from jobagg.adapters import static_html  # noqa: F401
    from jobagg.adapters import successfactors_rmk  # noqa: F401
    from jobagg.adapters import taleo  # noqa: F401
    from jobagg.adapters import unv  # noqa: F401
    from jobagg.adapters import usajobs  # noqa: F401
    from jobagg.adapters import workable  # noqa: F401
    from jobagg.adapters import workday  # noqa: F401


def load_sources(path: str | Path) -> list[OrganizationSource]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load organizations.yaml") from exc

    data = yaml.safe_load(Path(path).read_text()) or {}
    return [OrganizationSource.from_mapping(item) for item in data.get("sources", [])]


def sync_source(
    source: OrganizationSource,
    *,
    db: JobDatabase,
    policy: RobotsPolicy,
    close_missing: bool = True,
    missing_run_threshold: int = 3,
) -> SyncResult:
    register_builtin_adapters()
    result = SyncResult(source_id=source.id)
    adapter_cls = get_adapter_class(source.adapter or source.ats_family)
    bounded_source = _source_with_policy_page_cap(source, policy)
    http = _http_client_for_source(bounded_source, policy)
    adapter = adapter_cls(AdapterContext(source=bounded_source, http=http, robots=RobotsChecker(policy)))
    try:
        jobs = adapter.fetch_jobs()
    except Exception as exc:  # adapters should preserve source-level sync isolation
        result.errors.append(str(exc))
        result.diagnostics = _diagnostics_from_adapter(adapter, bounded_source)
        _mark_list_failure(result.diagnostics)
        return _finish_result(db, result)

    seen_job_keys = {job.identity_key() for job in jobs}
    jobs = _cap_jobs_for_policy(jobs, policy, result)

    result.fetched = len(jobs)
    result.diagnostics = _diagnostics_from_adapter(adapter, bounded_source)
    _finalize_list_diagnostics(result, bounded_source, jobs)
    if _should_skip_missing_for_zero_fetch(bounded_source, db, jobs, close_missing, result.diagnostics):
        result.errors.append(_zero_fetch_active_jobs_error(source))
        _mark_list_failure(result.diagnostics, empty_reason="unverified_zero")
        return _finish_result(db, result)
    if _should_report_unverified_zero(bounded_source, jobs, result.diagnostics):
        result.errors.append(_unverified_zero_error(source))
        _mark_list_failure(result.diagnostics, empty_reason="unverified_zero")
        return _finish_result(db, result)

    counts = db.upsert_jobs(jobs)
    result.inserted = counts["inserted"]
    result.updated = counts["updated"]
    result.unchanged = counts["unchanged"]
    result.diagnostics.missing_transition_allowed = _missing_transition_allowed(
        close_missing=close_missing,
        jobs=jobs,
        diagnostics=result.diagnostics,
    )
    if result.diagnostics.missing_transition_allowed:
        missing_counts = db.mark_missing(
            source.id,
            seen_job_keys,
            missing_run_threshold=missing_run_threshold,
        )
        result.missing = missing_counts["missing"]
        result.closed = missing_counts["closed"]
    return _finish_result(db, result)


def sync_source_with_selective_details(
    source: OrganizationSource,
    *,
    db: JobDatabase,
    policy: RobotsPolicy,
    deadline_refresh_days: int = 14,
    refresh_all_details: bool = False,
    close_missing: bool = True,
    missing_run_threshold: int = 3,
) -> SyncResult:
    """Sync listings, fetching detail pages only when deadline data is needed.

    This is useful for Workday sources where listings identify open jobs but
    detail pages contain the closing date. Existing detail fields are preserved
    by JobDatabase when a listing-only row is upserted.
    """

    register_builtin_adapters()
    result = SyncResult(source_id=source.id)
    adapter_cls = get_adapter_class(source.adapter or source.ats_family)
    bounded_source = _source_with_policy_page_cap(source, policy)
    http = _http_client_for_source(bounded_source, policy)
    listing_source = replace(bounded_source, extra={**bounded_source.extra, "fetch_details": False})
    adapter = adapter_cls(AdapterContext(source=listing_source, http=http, robots=RobotsChecker(policy)))
    try:
        listing_jobs = adapter.fetch_jobs()
    except Exception as exc:
        result.errors.append(str(exc))
        result.diagnostics = _diagnostics_from_adapter(adapter, bounded_source)
        _mark_list_failure(result.diagnostics)
        return _finish_result(db, result)

    seen_job_keys = {job.identity_key() for job in listing_jobs}
    listing_jobs = _cap_jobs_for_policy(listing_jobs, policy, result)

    result.fetched = len(listing_jobs)
    result.diagnostics = _diagnostics_from_adapter(adapter, bounded_source)
    _finalize_list_diagnostics(result, bounded_source, listing_jobs)
    if _should_skip_missing_for_zero_fetch(
        bounded_source,
        db,
        listing_jobs,
        close_missing,
        result.diagnostics,
    ):
        result.errors.append(_zero_fetch_active_jobs_error(source))
        _mark_list_failure(result.diagnostics, empty_reason="unverified_zero")
        return _finish_result(db, result)
    if _should_report_unverified_zero(bounded_source, listing_jobs, result.diagnostics):
        result.errors.append(_unverified_zero_error(source))
        _mark_list_failure(result.diagnostics, empty_reason="unverified_zero")
        return _finish_result(db, result)

    cutoff = datetime.now(tz=UTC) + timedelta(days=deadline_refresh_days)
    jobs = []
    detail_attempts = 0
    detail_failures = 0
    detail_skipped = 0
    detail_errors: list[str] = []
    detail_aborted = False
    max_detail_pages = _optional_int(bounded_source.extra.get("max_detail_pages_per_run"))
    if max_detail_pages is None:
        max_detail_pages = _optional_int(bounded_source.extra.get("max_detail_fetches_per_run"))
    # Threshold guard: a systemic detail-fetch failure (auth wall, schema
    # change, blocked CDN) can otherwise look like a long quiet stream of
    # per-job warnings. Once we have a meaningful sample (>=5 attempts) and
    # at least half are failing, stop calling the detail endpoint and
    # surface a single high-signal error instead.
    DETAIL_MIN_SAMPLE = 5
    DETAIL_FAILURE_RATIO = 0.5
    for job in listing_jobs:
        detail_job = None
        if (
            not detail_aborted
            and (refresh_all_details or _needs_detail_refresh(db, job.identity_key(), cutoff, listing_job=job))
        ):
            fetch_detail = getattr(adapter, "fetch_detail_for_listing_item", None)
            if callable(fetch_detail):
                if max_detail_pages is not None and detail_attempts >= max_detail_pages:
                    detail_skipped += 1
                    jobs.append(job)
                    continue
                detail_attempts += 1
                try:
                    detail_job = fetch_detail(job.raw)
                except Exception as exc:
                    detail_failures += 1
                    detail_errors.append(f"{_job_detail_label(job)} detail refresh failed: {exc}")
                    if (
                        detail_attempts >= DETAIL_MIN_SAMPLE
                        and detail_failures / detail_attempts >= DETAIL_FAILURE_RATIO
                    ):
                        detail_aborted = True
                        result.errors.append(
                            f"detail refresh aborted for {source.id}: "
                            f"{detail_failures}/{detail_attempts} attempts failed "
                            f">= {int(DETAIL_FAILURE_RATIO * 100)}% threshold"
                        )
        jobs.append(detail_job or job)

    result.fetched = len(jobs)
    _apply_detail_diagnostics(result, detail_attempts, detail_failures, detail_skipped, detail_errors)
    counts = db.upsert_jobs(jobs)
    result.inserted = counts["inserted"]
    result.updated = counts["updated"]
    result.unchanged = counts["unchanged"]
    result.diagnostics.missing_transition_allowed = _missing_transition_allowed(
        close_missing=close_missing,
        jobs=jobs,
        diagnostics=result.diagnostics,
    )
    if result.diagnostics.missing_transition_allowed:
        missing_counts = db.mark_missing(
            source.id,
            seen_job_keys,
            missing_run_threshold=missing_run_threshold,
        )
        result.missing = missing_counts["missing"]
        result.closed = missing_counts["closed"]
    return _finish_result(db, result)


def _needs_detail_refresh(
    db: JobDatabase,
    job_key: str,
    cutoff: datetime,
    *,
    listing_job=None,
) -> bool:
    current = db.get_job(job_key)
    if current is None:
        return getattr(listing_job, "closes_at", None) is None
    closes_at = current.get("closes_at")
    if not closes_at:
        return True
    try:
        parsed = datetime.fromisoformat(closes_at)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= cutoff


def _should_skip_missing_for_zero_fetch(
    source: OrganizationSource,
    db: JobDatabase,
    jobs: list,
    close_missing: bool,
    diagnostics: SourceRunDiagnostics | None,
) -> bool:
    return (
        close_missing
        and not jobs
        and db.has_active_jobs(source.id)
        and not _verified_zero_fetch(diagnostics)
    )


def _should_report_unverified_zero(
    source: OrganizationSource,
    jobs: list,
    diagnostics: SourceRunDiagnostics | None,
) -> bool:
    return (
        not jobs
        and not _verified_zero_fetch(diagnostics)
        and _empty_policy_requires_verification(source)
    )


def _http_client_for_source(
    source: OrganizationSource,
    policy: RobotsPolicy,
) -> JobAggHTTPClient:
    source_host = urlsplit(source.base_url).netloc
    timeout_seconds = (
        _optional_int(source.extra.get("request_timeout_seconds"))
        or _optional_int(source.extra.get("timeout_seconds"))
        or policy.request_timeout_seconds
    )
    max_retries = _optional_int(source.extra.get("max_retries"))
    backoff_base_seconds = _optional_float(source.extra.get("backoff_base_seconds"))
    return JobAggHTTPClient(
        user_agent=policy.user_agent,
        timeout_seconds=timeout_seconds,
        min_delay_seconds=policy.min_delay_for(source_host),
        max_retries=max_retries if max_retries is not None else 3,
        backoff_base_seconds=backoff_base_seconds if backoff_base_seconds is not None else 1.0,
    )


def _source_with_policy_page_cap(
    source: OrganizationSource,
    policy: RobotsPolicy,
) -> OrganizationSource:
    extra = dict(source.extra)
    for key in ("max_pages", "fallback_max_pages"):
        configured = _optional_int(extra.get(key))
        extra[key] = policy.max_pages_per_source if configured is None else min(
            configured,
            policy.max_pages_per_source,
        )
    return replace(source, extra=extra)


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "on"}


def _cap_jobs_for_policy(
    jobs: list,
    policy: RobotsPolicy,
    result: SyncResult,
) -> list:
    if len(jobs) <= policy.max_jobs_per_source:
        return jobs
    result.errors.append(
        f"Adapter returned {len(jobs)} jobs, above max_jobs_per_source={policy.max_jobs_per_source}"
    )
    return jobs[: policy.max_jobs_per_source]


def _zero_fetch_active_jobs_error(source: OrganizationSource) -> str:
    return f"{source.id}: zero jobs fetched for source with active jobs; skipping missing/closed marking"


def _unverified_zero_error(source: OrganizationSource) -> str:
    return f"{source.id}: zero jobs fetched without verified empty evidence"


def _job_detail_label(job) -> str:
    return str(job.external_id or job.identity_key())


def _finish_result(db: JobDatabase, result: SyncResult) -> SyncResult:
    if result.diagnostics is None:
        result.diagnostics = SourceRunDiagnostics(source_id=result.source_id)
    db.add_source_run(result)
    return result


def _diagnostics_from_adapter(adapter, source: OrganizationSource) -> SourceRunDiagnostics:
    diagnostics = getattr(adapter, "run_diagnostics", None)
    if not isinstance(diagnostics, SourceRunDiagnostics):
        diagnostics = SourceRunDiagnostics(source_id=source.id)
    diagnostics.source_id = source.id
    diagnostics.platform_host = diagnostics.platform_host or urlsplit(source.base_url).netloc
    diagnostics.site_number = diagnostics.site_number or _optional_text(source.extra.get("site_number"))
    diagnostics.expected_site_name = diagnostics.expected_site_name or _optional_text(
        source.extra.get("expected_site_name")
    )
    diagnostics.endpoint_family = diagnostics.endpoint_family or source.ats_family
    diagnostics.fetch_method = diagnostics.fetch_method or str(source.adapter or source.ats_family)
    if diagnostics.scope_validation_status is None:
        diagnostics.scope_validation_status = "not_applicable"
    return diagnostics


def _finalize_list_diagnostics(
    result: SyncResult,
    source: OrganizationSource,
    jobs: list,
) -> None:
    diagnostics = result.diagnostics
    if diagnostics is None:
        return
    if diagnostics.pages_fetched is None and not result.errors:
        diagnostics.pages_fetched = 1
    if diagnostics.pagination_complete is None and not result.errors:
        diagnostics.pagination_complete = True
    if diagnostics.scope_validation_status is None:
        diagnostics.scope_validation_status = "not_applicable"
    if not jobs:
        if _verified_zero_fetch(diagnostics):
            diagnostics.health_status = diagnostics.health_status or "ok_empty"
        elif diagnostics.health_status is None:
            diagnostics.health_status = "issue" if _empty_policy_requires_verification(source) else "warning"
            diagnostics.empty_reason = diagnostics.empty_reason or "unverified_zero"
    elif diagnostics.pagination_complete is False:
        diagnostics.health_status = "issue"
        if not any("pagination incomplete" in error for error in result.errors):
            result.errors.append(f"{source.id}: pagination incomplete")
    elif diagnostics.health_status is None:
        diagnostics.health_status = "ok"


def _mark_list_failure(
    diagnostics: SourceRunDiagnostics | None,
    *,
    empty_reason: str | None = None,
) -> None:
    if diagnostics is None:
        return
    diagnostics.list_error_count += 1
    diagnostics.health_status = "issue"
    diagnostics.pagination_complete = False
    diagnostics.missing_transition_allowed = False
    if empty_reason:
        diagnostics.empty_reason = empty_reason


def _apply_detail_diagnostics(
    result: SyncResult,
    detail_attempts: int,
    detail_failures: int,
    detail_skipped: int,
    detail_errors: list[str],
) -> None:
    diagnostics = result.diagnostics
    if diagnostics is None:
        return
    diagnostics.detail_attempted = detail_attempts
    diagnostics.detail_failed = detail_failures
    diagnostics.detail_succeeded = max(0, detail_attempts - detail_failures)
    diagnostics.detail_skipped = detail_skipped
    if detail_attempts == 0 or detail_failures == 0:
        return
    ratio = detail_failures / detail_attempts
    summary = (
        f"detail refresh failed for {detail_failures}/{detail_attempts} jobs"
        f" ({ratio:.0%})"
    )
    if detail_errors:
        summary = f"{summary}: {'; '.join(detail_errors[:5])}"
    if ratio < 0.05:
        diagnostics.health_status = diagnostics.health_status or "ok"
        return
    if ratio <= 0.25:
        diagnostics.health_status = "degraded"
        result.errors.append(summary)
        return
    diagnostics.health_status = "issue"
    result.errors.append(summary)


def _missing_transition_allowed(
    *,
    close_missing: bool,
    jobs: list,
    diagnostics: SourceRunDiagnostics | None,
) -> bool:
    if not close_missing or diagnostics is None:
        return False
    if diagnostics.health_status not in _MISSING_ALLOWED_HEALTH:
        return False
    if diagnostics.pagination_complete is False:
        return False
    if diagnostics.scope_validation_status not in {None, "passed", "not_applicable"}:
        return False
    if not jobs and not _verified_zero_fetch(diagnostics):
        return False
    return True


def _verified_zero_fetch(diagnostics: SourceRunDiagnostics | None) -> bool:
    if diagnostics is None:
        return False
    return diagnostics.empty_reason in _VERIFIED_EMPTY_REASONS


def _empty_policy_requires_verification(source: OrganizationSource) -> bool:
    policy = source.extra.get("empty_policy") if source.extra else None
    if isinstance(policy, dict):
        mode = str(policy.get("mode") or "")
    else:
        mode = str(policy or "")
    return mode in {"verified_empty_required", "verified_structural_empty"}


def _optional_text(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def sync_all(
    *,
    config_path: str | Path,
    db: JobDatabase,
    policy: RobotsPolicy,
    include_disabled: bool = False,
) -> list[SyncResult]:
    sources = load_sources(config_path)
    results = []
    for source in sources:
        if not source.enabled and not include_disabled:
            continue
        results.append(sync_source(source, db=db, policy=policy))
    return results
