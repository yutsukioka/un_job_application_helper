"""Source synchronization pipeline."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from jobagg.adapters.base import AdapterContext, get_adapter_class
from jobagg.db import JobDatabase
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource, SyncResult
from jobagg.robots import RobotsChecker, RobotsPolicy


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
    source_host = urlsplit(bounded_source.base_url).netloc
    http = JobAggHTTPClient(
        user_agent=policy.user_agent,
        timeout_seconds=policy.request_timeout_seconds,
        min_delay_seconds=policy.min_delay_for(source_host),
    )
    adapter = adapter_cls(AdapterContext(source=bounded_source, http=http, robots=RobotsChecker(policy)))
    try:
        jobs = adapter.fetch_jobs()
    except Exception as exc:  # adapters should preserve source-level sync isolation
        result.errors.append(str(exc))
        return _finish_result(db, result)

    seen_job_keys = {job.identity_key() for job in jobs}
    jobs = _cap_jobs_for_policy(jobs, policy, result)

    result.fetched = len(jobs)
    if _should_skip_missing_for_zero_fetch(source, db, jobs, close_missing):
        result.errors.append(_zero_fetch_active_jobs_error(source))
        return _finish_result(db, result)

    counts = db.upsert_jobs(jobs)
    result.inserted = counts["inserted"]
    result.updated = counts["updated"]
    result.unchanged = counts["unchanged"]
    if close_missing:
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
    source_host = urlsplit(bounded_source.base_url).netloc
    http = JobAggHTTPClient(
        user_agent=policy.user_agent,
        timeout_seconds=policy.request_timeout_seconds,
        min_delay_seconds=policy.min_delay_for(source_host),
    )
    listing_source = replace(bounded_source, extra={**bounded_source.extra, "fetch_details": False})
    adapter = adapter_cls(AdapterContext(source=listing_source, http=http, robots=RobotsChecker(policy)))
    try:
        listing_jobs = adapter.fetch_jobs()
    except Exception as exc:
        result.errors.append(str(exc))
        return _finish_result(db, result)

    seen_job_keys = {job.identity_key() for job in listing_jobs}
    listing_jobs = _cap_jobs_for_policy(listing_jobs, policy, result)

    result.fetched = len(listing_jobs)
    if _should_skip_missing_for_zero_fetch(source, db, listing_jobs, close_missing):
        result.errors.append(_zero_fetch_active_jobs_error(source))
        return _finish_result(db, result)

    cutoff = datetime.now(tz=UTC) + timedelta(days=deadline_refresh_days)
    jobs = []
    for job in listing_jobs:
        detail_job = None
        if refresh_all_details or _needs_detail_refresh(db, job.identity_key(), cutoff, listing_job=job):
            fetch_detail = getattr(adapter, "fetch_detail_for_listing_item", None)
            if callable(fetch_detail):
                try:
                    detail_job = fetch_detail(job.raw)
                except Exception as exc:
                    result.errors.append(f"{_job_detail_label(job)} detail refresh failed: {exc}")
        jobs.append(detail_job or job)

    result.fetched = len(jobs)
    counts = db.upsert_jobs(jobs)
    result.inserted = counts["inserted"]
    result.updated = counts["updated"]
    result.unchanged = counts["unchanged"]
    if close_missing:
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
) -> bool:
    return close_missing and not jobs and db.has_active_jobs(source.id)


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


def _job_detail_label(job) -> str:
    return str(job.external_id or job.identity_key())


def _finish_result(db: JobDatabase, result: SyncResult) -> SyncResult:
    db.add_source_run(result)
    return result


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
