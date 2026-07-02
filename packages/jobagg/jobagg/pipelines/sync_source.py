"""Source synchronization pipeline."""

from __future__ import annotations

import random
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from jobagg.adapters.base import AdapterContext, get_adapter_class
from jobagg.db import JobDatabase
from jobagg.detail_quality import (
    DETAIL_QUALITY_COMPLETE,
    detail_quality_requeue_reason,
    detail_quality_status,
)
from jobagg.hashing import ensure_job_hash
from jobagg.http import JobAggHTTPClient
from jobagg.models import ChangeEvent, OrganizationSource, SourceRunDiagnostics, SyncResult
from jobagg.observability.logging import get_logger
from jobagg.robots import RobotsChecker, RobotsPolicy

_VERIFIED_EMPTY_REASONS = {"verified_total_zero", "verified_structural_empty", "verified_text_empty"}
_MISSING_ALLOWED_HEALTH = {"ok", "ok_empty"}
_DETAIL_ADAPTER_FAILURE_MIN_ATTEMPTS = 3
_DETAIL_ADAPTER_FAILURE_RATIO = 0.80
_DETAIL_BREAKER_PROBE_COOLDOWN_SECONDS = 24 * 60 * 60
_DETAIL_BREAKER_HALF_OPEN_LIMIT = 3
_TRANSIENT_DETAIL_BREAKER_COOLDOWN_SECONDS = 15 * 60
GLOBAL_FETCH_POLICY_DEFAULTS = {
    "list_fetch_interval_minutes": 180,
    "list_fetch_jitter_minutes": 30,
    "failed_source_cooldown_minutes": 360,
    "repeated_failed_source_cooldown_minutes": 720,
}
UNDP_ORACLE_DETAIL_POLICY_DEFAULTS = {
    "list_fetch_interval_minutes": 180,
    "oracle_detail_concurrency": 1,
    "max_detail_pages_per_run": 20,
    "oracle_detail_min_delay_seconds": 8,
    "oracle_detail_jitter_seconds": 3,
    "oracle_detail_batch_size": 10,
    "oracle_detail_batch_pause_seconds": 120,
    "oracle_detail_failure_cooldown_seconds": 900,
    "oracle_detail_stop_after_transient_failures": 3,
    "detail_timeout_seconds": 120,
}
UNSTABLE_DETAIL_POLICY_DEFAULTS = {
    "detail_concurrency": 1,
    "detail_min_delay_seconds": 30,
    "detail_jitter_seconds": 10,
    "detail_batch_size": 5,
    "detail_batch_pause_seconds": 300,
    "max_detail_pages_per_run": 10,
    "stop_after_transient_failures": 2,
    "host_cooldown_seconds": 1800,
    "detail_breaker_probe_cooldown_seconds": 86400,
    "detail_permanent_failure_quarantine_days": 0,
    "detail_none_is_transient": False,
}
UNSTABLE_DETAIL_SOURCE_IDS = {
    "unicef_pageup",
    "ctbto_successfactors_legacy",
    "icc_successfactors_legacy",
}
_TRANSIENT_RUN_MARKERS = (
    "http 429",
    "too many requests",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "service unavailable",
    "gateway timeout",
    "timed out",
    "timeout",
    "remote end closed",
    "remotedisconnected",
    "connection reset",
    "connection aborted",
    "connection refused",
    "temporarily unavailable",
)
_BLOCKED_RUN_MARKERS = ("http 401", "http 403", "forbidden", "blocked", "captcha")

LOGGER = get_logger(__name__)


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


def fetch_schedule_policy(source: OrganizationSource) -> dict[str, object]:
    """Return the effective list/detail pacing policy for a source.

    This is intentionally data-only so the CLI can render a dry-run schedule
    without touching network adapters.
    """

    policy: dict[str, object] = dict(GLOBAL_FETCH_POLICY_DEFAULTS)
    if source.id == "undp_oracle_hcm":
        policy.update(UNDP_ORACLE_DETAIL_POLICY_DEFAULTS)
    if source.id in UNSTABLE_DETAIL_SOURCE_IDS or _optional_bool(
        source.extra.get("unstable_detail_source")
    ):
        policy.update(UNSTABLE_DETAIL_POLICY_DEFAULTS)
    for key in set(policy) | set(UNDP_ORACLE_DETAIL_POLICY_DEFAULTS) | set(UNSTABLE_DETAIL_POLICY_DEFAULTS):
        if key in source.extra:
            policy[key] = source.extra[key]
    return policy


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
    list_breaker = _prepare_list_breaker_for_run(db, source)
    if list_breaker["skip"]:
        result.errors.append(f"{source.id}: list fetch skipped by circuit breaker")
        result.diagnostics = _diagnostics_for_skipped_list(source, list_breaker)
        return _finish_result(db, result)
    if list_breaker["state"] == "half_open":
        source = _with_list_probe_page_cap(source)
    bounded_source = _source_with_policy_page_cap(source, policy)
    http = _http_client_for_source(bounded_source, policy)
    adapter = adapter_cls(AdapterContext(source=bounded_source, http=http, robots=RobotsChecker(policy)))
    list_started_at = time.monotonic()
    try:
        jobs = adapter.fetch_jobs()
    except Exception as exc:  # adapters should preserve source-level sync isolation
        result.errors.append(str(exc))
        result.diagnostics = _diagnostics_from_adapter(adapter, bounded_source)
        _mark_list_failure(result.diagnostics, error=exc)
        _record_list_breaker_failure(db, bounded_source, result.diagnostics, error=exc)
        return _finish_result(db, result)

    result.diagnostics = _diagnostics_from_adapter(adapter, bounded_source)
    jobs = _exclude_configured_jobs(bounded_source, jobs)
    seen_job_keys = {job.identity_key() for job in jobs}
    jobs = _cap_jobs_for_policy(jobs, policy, result)

    result.fetched = len(jobs)
    _finalize_list_diagnostics(result, bounded_source, jobs)
    _record_list_breaker_success(db, bounded_source, result.diagnostics, jobs)
    _log_list_progress(bounded_source, result, list_started_at)
    if _should_skip_missing_for_zero_fetch(bounded_source, db, jobs, close_missing, result.diagnostics):
        result.errors.append(_zero_fetch_active_jobs_error(source))
        _mark_list_failure(
            result.diagnostics,
            empty_reason="unverified_zero",
            run_classification="inconclusive",
        )
        _record_list_breaker_failure(
            db,
            bounded_source,
            result.diagnostics,
            reason="zero_fetched_without_verified_empty",
        )
        return _finish_result(db, result)
    if _should_report_unverified_zero(bounded_source, jobs, result.diagnostics):
        result.errors.append(_unverified_zero_error(source))
        _mark_list_failure(
            result.diagnostics,
            empty_reason="unverified_zero",
            run_classification="inconclusive",
        )
        _record_list_breaker_failure(
            db,
            bounded_source,
            result.diagnostics,
            reason="zero_fetched_without_verified_empty",
        )
        return _finish_result(db, result)

    counts = db.upsert_jobs(jobs)
    result.inserted = counts["inserted"]
    result.updated = counts["updated"]
    result.unchanged = counts["unchanged"]
    transition_allowed = _missing_transition_allowed(
        close_missing=close_missing,
        jobs=jobs,
        diagnostics=result.diagnostics,
    )
    result.diagnostics.missing_transition_allowed = transition_allowed
    excluded_closed = _close_configured_excluded_jobs(db, bounded_source) if transition_allowed else 0
    if transition_allowed:
        missing_counts = db.mark_missing(
            source.id,
            seen_job_keys,
            missing_run_threshold=missing_run_threshold,
        )
        result.missing = missing_counts["missing"]
        result.closed = missing_counts["closed"] + excluded_closed
    else:
        result.closed = excluded_closed
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
    list_breaker = _prepare_list_breaker_for_run(db, source)
    if list_breaker["skip"]:
        result.errors.append(f"{source.id}: list fetch skipped by circuit breaker")
        result.diagnostics = _diagnostics_for_skipped_list(source, list_breaker)
        return _finish_result(db, result)
    if list_breaker["state"] == "half_open":
        source = _with_list_probe_page_cap(source)
    bounded_source = _source_with_policy_page_cap(source, policy)
    http = _http_client_for_source(bounded_source, policy)
    listing_source = replace(bounded_source, extra={**bounded_source.extra, "fetch_details": False})
    adapter = adapter_cls(AdapterContext(source=listing_source, http=http, robots=RobotsChecker(policy)))
    list_started_at = time.monotonic()
    try:
        listing_jobs = adapter.fetch_jobs()
    except Exception as exc:
        result.errors.append(str(exc))
        result.diagnostics = _diagnostics_from_adapter(adapter, bounded_source)
        _mark_list_failure(result.diagnostics, error=exc)
        _record_list_breaker_failure(db, bounded_source, result.diagnostics, error=exc)
        return _finish_result(db, result)

    result.diagnostics = _diagnostics_from_adapter(adapter, bounded_source)
    listing_jobs = _exclude_configured_jobs(bounded_source, listing_jobs)
    seen_job_keys = {job.identity_key() for job in listing_jobs}
    listing_jobs = _cap_jobs_for_policy(listing_jobs, policy, result)

    result.fetched = len(listing_jobs)
    _finalize_list_diagnostics(result, bounded_source, listing_jobs)
    _record_list_breaker_success(db, bounded_source, result.diagnostics, listing_jobs)
    _log_list_progress(bounded_source, result, list_started_at)
    if _should_skip_missing_for_zero_fetch(
        bounded_source,
        db,
        listing_jobs,
        close_missing,
        result.diagnostics,
    ):
        result.errors.append(_zero_fetch_active_jobs_error(source))
        _mark_list_failure(
            result.diagnostics,
            empty_reason="unverified_zero",
            run_classification="inconclusive",
        )
        _record_list_breaker_failure(
            db,
            bounded_source,
            result.diagnostics,
            reason="zero_fetched_without_verified_empty",
        )
        return _finish_result(db, result)
    if _should_report_unverified_zero(bounded_source, listing_jobs, result.diagnostics):
        result.errors.append(_unverified_zero_error(source))
        _mark_list_failure(
            result.diagnostics,
            empty_reason="unverified_zero",
            run_classification="inconclusive",
        )
        _record_list_breaker_failure(
            db,
            bounded_source,
            result.diagnostics,
            reason="zero_fetched_without_verified_empty",
        )
        return _finish_result(db, result)

    list_missing_transition_allowed = _missing_transition_allowed(
        close_missing=close_missing,
        jobs=listing_jobs,
        diagnostics=result.diagnostics,
    )
    cutoff = datetime.now(tz=UTC) + timedelta(days=deadline_refresh_days)
    jobs = []
    detail_attempts = 0
    detail_failures = 0
    detail_skipped = 0
    detail_errors: list[str] = []
    detail_aborted = False
    transient_detail_failures = 0
    detail_breaker_opened = False
    listing_payload_detail_completions = 0
    failed_detail_items: list[tuple[str, str, str]] = []
    max_detail_pages = _optional_int(bounded_source.extra.get("max_detail_pages_per_run"))
    if max_detail_pages is None:
        max_detail_pages = _optional_int(bounded_source.extra.get("max_detail_fetches_per_run"))
    if max_detail_pages is None:
        max_detail_pages = _optional_int(bounded_source.extra.get("detail_queue_max_per_run"))
    detail_pacer = _DetailFetchPacer.from_source(bounded_source)
    transient_failure_limit = _optional_int(
        bounded_source.extra.get("oracle_detail_stop_after_transient_failures")
    )
    if transient_failure_limit is None:
        transient_failure_limit = _optional_int(
            bounded_source.extra.get("detail_stop_after_transient_failures")
        )
    if transient_failure_limit is None:
        transient_failure_limit = _optional_int(bounded_source.extra.get("stop_after_transient_failures"))
    failure_cooldown = _optional_int(
        bounded_source.extra.get("oracle_detail_failure_cooldown_seconds")
    )
    if failure_cooldown is None:
        failure_cooldown = _optional_int(bounded_source.extra.get("detail_failure_cooldown_seconds"))
    if failure_cooldown is None:
        failure_cooldown = _optional_int(bounded_source.extra.get("host_cooldown_seconds"))
    detail_none_is_transient = _optional_bool(
        bounded_source.extra.get("detail_none_is_transient")
    )
    detail_breaker = _prepare_detail_breaker_for_run(db, bounded_source)
    if result.diagnostics is not None:
        result.diagnostics.detail_breaker_state = str(detail_breaker.get("state") or "closed")
    if detail_breaker["state"] == "open":
        max_detail_pages = 0
    elif detail_breaker["state"] == "half_open":
        probe_limit = int(detail_breaker.get("max_attempts") or _DETAIL_BREAKER_HALF_OPEN_LIMIT)
        max_detail_pages = probe_limit if max_detail_pages is None else min(max_detail_pages, probe_limit)
    listing_jobs = _prioritize_detail_refresh_jobs(
        db,
        listing_jobs,
        cutoff,
        refresh_all_details=refresh_all_details,
    )
    fetch_detail = getattr(adapter, "fetch_detail_for_listing_item", None)
    host_cooldown_until: datetime | None = None
    for job in listing_jobs:
        job_key = job.identity_key()
        listing_hash = _listing_hash(job)
        queue_reason = _detail_queue_reason(
            db,
            bounded_source,
            job,
            cutoff,
            listing_hash=listing_hash,
            refresh_all_details=refresh_all_details,
        )
        if queue_reason is None:
            jobs.append(job)
            continue

        if _listing_payload_satisfies_detail(bounded_source, job):
            db.record_detail_backlog_attempt(
                job_key=job_key,
                source_id=source.id,
                status="complete",
                listing_hash=listing_hash,
            )
            listing_payload_detail_completions += 1
            jobs.append(job)
            continue

        backlog = db.queue_detail_backlog_item(
            job_key=job_key,
            source_id=source.id,
            listing_hash=listing_hash,
            reason=queue_reason,
        )
        if not callable(fetch_detail):
            db.record_detail_backlog_attempt(
                job_key=job_key,
                source_id=source.id,
                status="skipped",
                listing_hash=listing_hash,
                error="adapter has no detail fetch method",
            )
            detail_skipped += 1
            jobs.append(job)
            continue

        cooldown_until = _parse_backlog_time(backlog.get("cooldown_until"))
        if detail_breaker["state"] == "open":
            detail_skipped += 1
            db.update_detail_backlog_status(
                job_key=job_key,
                source_id=source.id,
                status="blocked_by_circuit_breaker",
                listing_hash=listing_hash,
                reason="blocked_by_circuit_breaker",
                error="detail breaker open",
                cooldown_until=detail_breaker.get("cooldown_until"),
            )
            jobs.append(job)
            continue

        if detail_aborted or _cooldown_active(cooldown_until):
            detail_skipped += 1
            if detail_breaker_opened:
                db.update_detail_backlog_status(
                    job_key=job_key,
                    source_id=source.id,
                    status="blocked_by_circuit_breaker",
                    listing_hash=listing_hash,
                    reason="blocked_by_circuit_breaker",
                    error="detail breaker opened during run",
                    cooldown_until=host_cooldown_until or cooldown_until,
                )
            else:
                db.defer_detail_backlog_item(
                    job_key=job_key,
                    source_id=source.id,
                    listing_hash=listing_hash,
                    reason="host_cooldown" if detail_aborted else "cooldown",
                    cooldown_until=host_cooldown_until or cooldown_until,
                )
            jobs.append(job)
            continue

        if max_detail_pages is not None and detail_attempts >= max_detail_pages:
            detail_skipped += 1
            db.defer_detail_backlog_item(
                job_key=job_key,
                source_id=source.id,
                listing_hash=listing_hash,
                reason="detail_run_budget_exhausted",
            )
            jobs.append(job)
            continue

        detail_pacer.before_attempt(detail_attempts)
        detail_attempts += 1
        attempt_started_at = time.monotonic()
        try:
            detail_job = fetch_detail(job.raw)
            elapsed = time.monotonic() - attempt_started_at
            if detail_job is None:
                detail_failures += 1
                error_text = f"{_job_detail_label(job)} detail refresh returned no detail"
                detail_errors.append(error_text)
                is_transient_none = detail_none_is_transient
                attempt_cooldown = (
                    datetime.now(tz=UTC) + timedelta(seconds=failure_cooldown)
                    if is_transient_none and failure_cooldown
                    else None
                )
                detail_status = "transient_failed" if is_transient_none else "permanent_failed"
                db.record_detail_backlog_attempt(
                    job_key=job_key,
                    source_id=source.id,
                    status=detail_status,
                    listing_hash=listing_hash,
                    error=error_text,
                    cooldown_until=attempt_cooldown,
                )
                failed_detail_items.append((job_key, listing_hash, error_text))
                LOGGER.info(
                    "%s detail %s/%s job=%s status=%s elapsed=%.2fs next_wait=%.1fs",
                    source.id,
                    detail_attempts,
                    max_detail_pages or "?",
                    _job_detail_label(job),
                    detail_status,
                    elapsed,
                    detail_pacer.min_delay_seconds if is_transient_none else 0,
                )
                if is_transient_none:
                    transient_detail_failures += 1
                    if (
                        transient_failure_limit is not None
                        and transient_failure_limit > 0
                        and transient_detail_failures >= transient_failure_limit
                    ):
                        detail_aborted = True
                        host_cooldown_until = (
                            datetime.now(tz=UTC) + timedelta(seconds=failure_cooldown)
                            if failure_cooldown
                            else None
                        )
                        cooldown_text = (
                            f"; host cooldown recommended for {failure_cooldown}s"
                            if failure_cooldown
                            else ""
                        )
                        result.errors.append(
                            f"detail refresh stopped for {source.id}: "
                            f"{transient_detail_failures} transient failures reached "
                            f"limit {transient_failure_limit}{cooldown_text}"
                        )
                        db.set_source_breaker(
                            source_id=source.id,
                            breaker_type="transient_detail",
                            state="open",
                            failure_count=transient_detail_failures,
                            success_count=0,
                            cooldown_until=host_cooldown_until,
                            reason="no_detail_response",
                        )
                        LOGGER.info(
                            "%s detail host cooldown reason=no_detail_response failures=%s cooldown_until=%s",
                            source.id,
                            transient_detail_failures,
                            host_cooldown_until.isoformat() if host_cooldown_until else "",
                        )
                elif _should_open_detail_adapter_breaker(detail_attempts, detail_failures):
                    detail_breaker_opened = True
                    detail_aborted = True
                    host_cooldown_until = _open_detail_adapter_breaker(
                        db,
                        bounded_source,
                        failed_detail_items,
                        reason=error_text,
                    )
                    result.errors.append(
                        f"detail breaker opened for {source.id}: "
                        f"{detail_failures}/{detail_attempts} detail attempts failed"
                    )
                jobs.append(job)
                continue
            quality_status = detail_quality_status(
                title=detail_job.title,
                description=detail_job.description,
                raw=detail_job.raw,
            )
            if quality_status != DETAIL_QUALITY_COMPLETE:
                detail_failures += 1
                queue_reason = detail_quality_requeue_reason(quality_status) or "detail_quality_incomplete"
                error_text = (
                    f"{_job_detail_label(job)} detail content quality is "
                    f"{quality_status}; leaving detail queued"
                )
                detail_errors.append(error_text)
                db.update_detail_backlog_status(
                    job_key=job_key,
                    source_id=source.id,
                    status="pending",
                    listing_hash=listing_hash,
                    reason=queue_reason,
                    error=error_text,
                )
                failed_detail_items.append((job_key, listing_hash, error_text))
                LOGGER.info(
                    "%s detail %s/%s job=%s status=%s elapsed=%.2fs next_wait=%.1fs",
                    source.id,
                    detail_attempts,
                    max_detail_pages or "?",
                    _job_detail_label(job),
                    queue_reason,
                    elapsed,
                    0,
                )
                if _should_open_detail_adapter_breaker(detail_attempts, detail_failures):
                    detail_breaker_opened = True
                    detail_aborted = True
                    host_cooldown_until = _open_detail_adapter_breaker(
                        db,
                        bounded_source,
                        failed_detail_items,
                        reason=error_text,
                    )
                    result.errors.append(
                        f"detail breaker opened for {source.id}: "
                        f"{detail_failures}/{detail_attempts} detail attempts failed"
                    )
                jobs.append(job)
                continue
            db.record_detail_backlog_attempt(
                job_key=job_key,
                source_id=source.id,
                status="complete",
                listing_hash=listing_hash,
            )
            LOGGER.info(
                "%s detail %s/%s job=%s status=ok elapsed=%.2fs next_wait=%.1fs",
                source.id,
                detail_attempts,
                max_detail_pages or "?",
                _job_detail_label(job),
                elapsed,
                detail_pacer.min_delay_seconds,
            )
            jobs.append(detail_job)
            continue
        except Exception as exc:
            elapsed = time.monotonic() - attempt_started_at
            detail_failures += 1
            error_text = f"{_job_detail_label(job)} detail refresh failed: {exc}"
            detail_errors.append(error_text)
            is_transient = _is_transient_detail_error(exc)
            attempt_cooldown = (
                datetime.now(tz=UTC) + timedelta(seconds=failure_cooldown)
                if is_transient and failure_cooldown
                else None
            )
            db.record_detail_backlog_attempt(
                job_key=job_key,
                source_id=source.id,
                status="transient_failed" if is_transient else "permanent_failed",
                listing_hash=listing_hash,
                error=error_text,
                cooldown_until=attempt_cooldown,
            )
            failed_detail_items.append((job_key, listing_hash, error_text))
            LOGGER.info(
                "%s detail %s/%s job=%s status=%s elapsed=%.2fs next_wait=%.1fs",
                source.id,
                detail_attempts,
                max_detail_pages or "?",
                _job_detail_label(job),
                "transient_failed" if is_transient else "permanent_failed",
                elapsed,
                detail_pacer.min_delay_seconds,
            )
            if is_transient:
                transient_detail_failures += 1
            if (
                transient_failure_limit is not None
                and transient_failure_limit > 0
                and transient_detail_failures >= transient_failure_limit
            ):
                detail_aborted = True
                host_cooldown_until = (
                    datetime.now(tz=UTC) + timedelta(seconds=failure_cooldown)
                    if failure_cooldown
                    else None
                )
                cooldown_text = (
                    f"; host cooldown recommended for {failure_cooldown}s"
                    if failure_cooldown
                    else ""
                )
                result.errors.append(
                    f"detail refresh stopped for {source.id}: "
                    f"{transient_detail_failures} transient failures reached "
                    f"limit {transient_failure_limit}{cooldown_text}"
                )
                db.set_source_breaker(
                    source_id=source.id,
                    breaker_type="transient_detail",
                    state="open",
                    failure_count=transient_detail_failures,
                    success_count=0,
                    cooldown_until=host_cooldown_until,
                    reason=_detail_error_reason(exc),
                )
                LOGGER.info(
                    "%s detail host cooldown reason=%s failures=%s cooldown_until=%s",
                    source.id,
                    _detail_error_reason(exc),
                    transient_detail_failures,
                    host_cooldown_until.isoformat() if host_cooldown_until else "",
                )
            if (not is_transient) and _should_open_detail_adapter_breaker(detail_attempts, detail_failures):
                detail_breaker_opened = True
                detail_aborted = True
                host_cooldown_until = _open_detail_adapter_breaker(
                    db,
                    bounded_source,
                    failed_detail_items,
                    reason=error_text,
                )
                result.errors.append(
                    f"detail breaker opened for {source.id}: "
                    f"{detail_failures}/{detail_attempts} detail attempts failed"
                )
            jobs.append(job)
            continue

    result.fetched = len(jobs)
    _apply_detail_diagnostics(
        result,
        detail_attempts,
        detail_failures,
        detail_skipped,
        detail_errors,
        detail_breaker_opened=detail_breaker_opened,
    )
    _record_detail_breaker_after_run(
        db,
        bounded_source,
        detail_breaker,
        detail_attempts=detail_attempts,
        detail_failures=detail_failures,
        opened=detail_breaker_opened,
    )
    if (
        listing_payload_detail_completions > 0
        and detail_attempts == 0
        and not detail_breaker_opened
    ):
        db.set_source_breaker(
            source_id=source.id,
            breaker_type="detail",
            state="closed",
            failure_count=0,
            success_count=1,
            reason="listing payload satisfies detail",
        )
    latest_detail_breaker_type = str(detail_breaker.get("breaker_type") or "detail")
    latest_detail_breaker = db.get_source_breaker(source.id, latest_detail_breaker_type)
    if latest_detail_breaker is None and latest_detail_breaker_type != "detail":
        latest_detail_breaker = db.get_source_breaker(source.id, "detail")
    if result.diagnostics is not None and latest_detail_breaker is not None:
        result.diagnostics.detail_breaker_state = str(latest_detail_breaker.get("state") or "closed")
    counts = db.upsert_jobs(jobs)
    result.inserted = counts["inserted"]
    result.updated = counts["updated"]
    result.unchanged = counts["unchanged"]
    result.diagnostics.missing_transition_allowed = list_missing_transition_allowed
    excluded_closed = (
        _close_configured_excluded_jobs(db, bounded_source)
        if list_missing_transition_allowed
        else 0
    )
    if list_missing_transition_allowed:
        missing_counts = db.mark_missing(
            source.id,
            seen_job_keys,
            missing_run_threshold=missing_run_threshold,
        )
        result.missing = missing_counts["missing"]
        result.closed = missing_counts["closed"] + excluded_closed
    else:
        result.closed = excluded_closed
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
        return True
    if not current.get("description"):
        return True
    if listing_job is not None and _missing_detail_metadata(current, listing_job):
        return True
    if listing_job is not None and _classification_detail_required(db, current, listing_job):
        return True
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


def _detail_queue_reason(
    db: JobDatabase,
    source: OrganizationSource,
    listing_job,
    cutoff: datetime,
    *,
    listing_hash: str,
    refresh_all_details: bool,
) -> str | None:
    job_key = listing_job.identity_key()
    backlog = db.get_detail_backlog(job_key)
    backlog_status = str(backlog.get("detail_status") or "") if backlog else ""
    backlog_hash = str(backlog.get("listing_hash_at_detail_fetch") or "") if backlog else ""
    current = db.get_job(job_key)
    if backlog_status in {"adapter_failed", "blocked_by_circuit_breaker"} and (
        _listing_payload_satisfies_detail(source, listing_job)
    ):
        return "listing_payload_detail_complete"
    if backlog_status == "permanent_failed" and backlog_hash == listing_hash:
        quarantine_days = _optional_int(source.extra.get("detail_permanent_failure_quarantine_days"))
        if quarantine_days is None or quarantine_days <= 0:
            return None
        last_attempt = _parse_backlog_time(backlog.get("last_attempt_at"))
        if last_attempt and last_attempt > datetime.now(tz=UTC) - timedelta(days=quarantine_days):
            return None
        return "previous_permanent_failed_quarantine_expired"
    if backlog_status == "skipped" and backlog_hash == listing_hash:
        return None
    if current is not None:
        quality_status = detail_quality_status(
            title=current.get("title"),
            description=current.get("description"),
            raw=current.get("raw") if isinstance(current.get("raw"), dict) else None,
            detail_status=backlog_status or None,
        )
        quality_reason = detail_quality_requeue_reason(quality_status)
        if quality_reason is not None:
            return quality_reason
    if backlog_status == "complete" and backlog_hash == listing_hash and not _detail_record_stale(
        backlog,
        source,
    ):
        return None
    if backlog_status == "pending":
        return "previous_pending"
    if backlog_status == "transient_failed":
        return "previous_transient_failed"
    if backlog_hash and backlog_hash != listing_hash:
        return "listing_hash_changed"

    if current is None:
        return "new"
    if not current.get("description"):
        return "required_detail_missing"
    if _missing_detail_metadata(current, listing_job):
        return "required_detail_missing"
    if _classification_detail_required(db, current, listing_job):
        return "required_detail_missing"
    if _detail_record_stale(backlog, source):
        return "stale"
    if refresh_all_details and backlog_status != "complete":
        return "refresh_requested"
    if _closing_needs_detail_refresh(current.get("closes_at"), cutoff):
        return "stale"
    return None


def _listing_payload_satisfies_detail(source: OrganizationSource, job) -> bool:
    if not _optional_bool(source.extra.get("listing_payload_is_detail_complete")):
        return False
    return (
        detail_quality_status(
            title=getattr(job, "title", None),
            description=getattr(job, "description", None),
            raw=getattr(job, "raw", None),
        )
        == DETAIL_QUALITY_COMPLETE
    )


def _exclude_configured_jobs(source: OrganizationSource, jobs: list) -> list:
    excluded_ids = _configured_excluded_external_ids(source)
    excluded_titles = _configured_excluded_titles(source)
    if not excluded_ids and not excluded_titles:
        return jobs
    return [
        job
        for job in jobs
        if str(getattr(job, "external_id", "") or "") not in excluded_ids
        and str(getattr(job, "title", "") or "").strip().casefold() not in excluded_titles
    ]


def _configured_excluded_external_ids(source: OrganizationSource) -> set[str]:
    return {
        str(value)
        for value in (
            source.extra.get("exclude_external_ids")
            or source.extra.get("excluded_external_ids")
            or []
        )
        if value not in (None, "")
    }


def _configured_excluded_titles(source: OrganizationSource) -> set[str]:
    return {
        str(value).strip().casefold()
        for value in (
            source.extra.get("exclude_titles")
            or source.extra.get("excluded_titles")
            or []
        )
        if str(value).strip()
    }


def _close_configured_excluded_jobs(db: JobDatabase, source: OrganizationSource) -> int:
    excluded_ids = {
        str(value)
        for value in (
            source.extra.get("exclude_external_ids")
            or source.extra.get("excluded_external_ids")
            or []
        )
        if value not in (None, "")
    }
    excluded_titles = {
        str(value).strip().casefold()
        for value in (
            source.extra.get("exclude_titles")
            or source.extra.get("excluded_titles")
            or []
        )
        if str(value).strip()
    }
    if not excluded_ids and not excluded_titles:
        return 0
    clauses = ["source_id = ?", "status IN ('open', 'missing')"]
    params: list[object] = [source.id]
    if excluded_ids:
        placeholders = ", ".join("?" for _ in excluded_ids)
        clauses.append(f"external_id IN ({placeholders})")
        params.extend(sorted(excluded_ids))
    if excluded_titles:
        placeholders = ", ".join("?" for _ in excluded_titles)
        clauses.append(f"LOWER(TRIM(title)) IN ({placeholders})")
        params.extend(sorted(excluded_titles))
    where = " AND (" + " OR ".join(clauses[2:]) + ")" if len(clauses) > 2 else ""
    query = (
        "SELECT job_key, normalized_hash FROM jobs "
        "WHERE source_id = ? AND status IN ('open', 'missing')" + where
    )
    with db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
        if not rows:
            return 0
        job_keys = [row["job_key"] for row in rows]
        placeholders = ", ".join("?" for _ in job_keys)
        conn.execute(
            f"""
            UPDATE jobs
            SET status = 'closed',
                missing_run_count = 0
            WHERE job_key IN ({placeholders})
            """,
            tuple(job_keys),
        )
        for row in rows:
            db.add_change_event(
                ChangeEvent(
                    source_id=source.id,
                    job_key=row["job_key"],
                    change_type="closed",
                    old_hash=row["normalized_hash"],
                    new_hash=None,
                ),
                conn=conn,
            )
        return len(rows)


def _detail_record_stale(backlog: dict | None, source: OrganizationSource) -> bool:
    stale_after_days = _optional_int(source.extra.get("detail_stale_after_days"))
    if stale_after_days is None or stale_after_days <= 0:
        return False
    if not backlog:
        return True
    last_success = _parse_backlog_time(backlog.get("last_success_at"))
    if last_success is None:
        return True
    return last_success <= datetime.now(tz=UTC) - timedelta(days=stale_after_days)


def _listing_hash(job) -> str:
    ensure_job_hash(job)
    return str(job.normalized_hash or "")


def _parse_backlog_time(value: object | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _cooldown_active(value: datetime | None) -> bool:
    return value is not None and value > datetime.now(tz=UTC)


def _prepare_list_breaker_for_run(db: JobDatabase, source: OrganizationSource) -> dict[str, object]:
    breaker = db.get_source_breaker(source.id, "list") or {}
    state = str(breaker.get("state") or "closed")
    cooldown_until = _parse_backlog_time(breaker.get("cooldown_until"))
    if state == "open" and _cooldown_active(cooldown_until):
        return {"state": "open", "skip": True, "cooldown_until": cooldown_until}
    if state == "open":
        db.set_source_breaker(
            source_id=source.id,
            breaker_type="list",
            state="half_open",
            failure_count=int(breaker.get("failure_count") or 0),
            success_count=0,
            reason="list breaker probe",
        )
        return {"state": "half_open", "skip": False, "cooldown_until": None}
    return {"state": state if state in {"closed", "half_open"} else "closed", "skip": False}


def _diagnostics_for_skipped_list(
    source: OrganizationSource,
    breaker: dict[str, object],
) -> SourceRunDiagnostics:
    diagnostics = SourceRunDiagnostics(source_id=source.id)
    diagnostics.platform_host = urlsplit(source.base_url).netloc
    diagnostics.endpoint_family = source.ats_family
    diagnostics.fetch_method = str(source.adapter or source.ats_family)
    diagnostics.health_status = "issue"
    diagnostics.run_classification = "inconclusive"
    diagnostics.publishability_classification = "source_inconclusive"
    diagnostics.pagination_complete = False
    diagnostics.list_error_count = 1
    diagnostics.list_breaker_state = str(breaker.get("state") or "open")
    diagnostics.scope_validation_status = "not_applicable"
    diagnostics.missing_transition_allowed = False
    return diagnostics


def _with_list_probe_page_cap(source: OrganizationSource) -> OrganizationSource:
    extra = dict(source.extra)
    extra["max_pages"] = 1
    extra["fallback_max_pages"] = 1
    return replace(source, extra=extra)


def _record_list_breaker_failure(
    db: JobDatabase,
    source: OrganizationSource,
    diagnostics: SourceRunDiagnostics | None,
    *,
    error: Exception | None = None,
    reason: str | None = None,
) -> None:
    if diagnostics is None:
        return
    breaker = db.get_source_breaker(source.id, "list") or {}
    failure_count = int(breaker.get("failure_count") or 0) + 1
    failure_reason = reason or _classify_run_failure(error)
    open_immediately = (
        failure_reason == "zero_fetched_without_verified_empty"
        and diagnostics.pagination_complete is not True
    )
    threshold = 1 if source.id == "iom_oracle_hcm" else 2
    should_open = open_immediately or failure_count >= threshold
    cooldown_minutes = _list_failure_cooldown_minutes(source, repeated=failure_count > threshold)
    state = "open" if should_open else "closed"
    cooldown_until = (
        datetime.now(tz=UTC) + timedelta(minutes=cooldown_minutes)
        if should_open
        else None
    )
    diagnostics.list_breaker_state = state
    db.set_source_breaker(
        source_id=source.id,
        breaker_type="list",
        state=state,
        failure_count=failure_count,
        success_count=0,
        cooldown_until=cooldown_until,
        reason=failure_reason,
    )


def _record_list_breaker_success(
    db: JobDatabase,
    source: OrganizationSource,
    diagnostics: SourceRunDiagnostics | None,
    jobs: list,
) -> None:
    if diagnostics is None:
        return
    if (
        diagnostics.pagination_complete is True
        and diagnostics.scope_validation_status in {None, "passed", "not_applicable"}
        and (jobs or _verified_zero_fetch(diagnostics))
        and diagnostics.run_classification in {None, "ok", "ok_empty"}
    ):
        diagnostics.list_breaker_state = "closed"
        db.set_source_breaker(
            source_id=source.id,
            breaker_type="list",
            state="closed",
            failure_count=0,
            success_count=1,
            reason="healthy list run",
        )


def _list_failure_cooldown_minutes(source: OrganizationSource, *, repeated: bool) -> int:
    repeated_value = _optional_int(source.extra.get("repeated_failed_source_cooldown_minutes"))
    failed_value = _optional_int(source.extra.get("failed_source_cooldown_minutes"))
    if source.id == "iom_oracle_hcm":
        return repeated_value or (12 * 60 if repeated else 6 * 60)
    if repeated:
        return repeated_value or int(GLOBAL_FETCH_POLICY_DEFAULTS["repeated_failed_source_cooldown_minutes"])
    return failed_value or int(GLOBAL_FETCH_POLICY_DEFAULTS["failed_source_cooldown_minutes"])


def _prepare_detail_breaker_for_run(db: JobDatabase, source: OrganizationSource) -> dict[str, object]:
    for breaker_type in ("detail", "transient_detail"):
        breaker = db.get_source_breaker(source.id, breaker_type) or {}
        state = str(breaker.get("state") or "closed")
        if state in {"open", "half_open"}:
            return _prepare_detail_breaker_state(db, source, breaker_type, breaker)
    return {"state": "closed", "cooldown_until": None, "max_attempts": None, "breaker_type": "detail"}


def _prepare_detail_breaker_state(
    db: JobDatabase,
    source: OrganizationSource,
    breaker_type: str,
    breaker: dict[str, object],
) -> dict[str, object]:
    state = str(breaker.get("state") or "closed")
    cooldown_until = _parse_backlog_time(breaker.get("cooldown_until"))
    if state == "open" and _cooldown_active(cooldown_until):
        return {
            "state": "open",
            "cooldown_until": cooldown_until,
            "max_attempts": 0,
            "breaker_type": breaker_type,
        }
    if state == "open":
        db.set_source_breaker(
            source_id=source.id,
            breaker_type=breaker_type,
            state="half_open",
            failure_count=int(breaker.get("failure_count") or 0),
            success_count=int(breaker.get("success_count") or 0),
            reason=f"{breaker_type} breaker probe",
        )
        return {"state": "half_open", "cooldown_until": None, "max_attempts": 1, "breaker_type": breaker_type}
    if state == "half_open":
        return {
            "state": "half_open",
            "cooldown_until": cooldown_until,
            "max_attempts": _DETAIL_BREAKER_HALF_OPEN_LIMIT,
            "breaker_type": breaker_type,
        }
    return {"state": "closed", "cooldown_until": None, "max_attempts": None, "breaker_type": breaker_type}


def _should_open_detail_adapter_breaker(detail_attempts: int, detail_failures: int) -> bool:
    return (
        detail_attempts >= _DETAIL_ADAPTER_FAILURE_MIN_ATTEMPTS
        and detail_attempts > 0
        and detail_failures / detail_attempts >= _DETAIL_ADAPTER_FAILURE_RATIO
    )


def _open_detail_adapter_breaker(
    db: JobDatabase,
    source: OrganizationSource,
    failed_items: list[tuple[str, str, str]],
    *,
    reason: str,
) -> datetime:
    cooldown_until = datetime.now(tz=UTC) + timedelta(seconds=_detail_breaker_cooldown_seconds(source))
    db.set_source_breaker(
        source_id=source.id,
        breaker_type="detail",
        state="open",
        failure_count=len(failed_items),
        success_count=0,
        cooldown_until=cooldown_until,
        reason="adapter_failed",
    )
    for job_key, listing_hash, error_text in failed_items:
        db.update_detail_backlog_status(
            job_key=job_key,
            source_id=source.id,
            status="blocked_by_circuit_breaker",
            listing_hash=listing_hash,
            reason="blocked_by_circuit_breaker",
            error=f"adapter_failed: {error_text or reason}",
            cooldown_until=cooldown_until,
        )
    return cooldown_until


def _record_detail_breaker_after_run(
    db: JobDatabase,
    source: OrganizationSource,
    breaker: dict[str, object],
    *,
    detail_attempts: int,
    detail_failures: int,
    opened: bool,
) -> None:
    state = str(breaker.get("state") or "closed")
    if opened:
        return
    breaker_type = str(breaker.get("breaker_type") or "detail")
    if detail_attempts <= 0:
        return
    if state == "closed":
        db.set_source_breaker(
            source_id=source.id,
            breaker_type=breaker_type,
            state="closed",
            failure_count=0 if detail_failures == 0 else detail_failures,
            success_count=1 if detail_failures == 0 else 0,
            reason="detail run complete",
        )
        return
    ratio = detail_failures / detail_attempts
    if detail_failures == 0:
        previous = db.get_source_breaker(source.id, breaker_type) or {}
        success_count = int(previous.get("success_count") or 0) + 1
        should_close = state == "half_open" and success_count >= 2
        db.set_source_breaker(
            source_id=source.id,
            breaker_type=breaker_type,
            state="closed" if should_close else "half_open",
            failure_count=0,
            success_count=success_count,
            reason="successful detail probe",
        )
        return
    if ratio < 0.20:
        db.set_source_breaker(
            source_id=source.id,
            breaker_type=breaker_type,
            state="closed",
            failure_count=0,
            success_count=1,
            reason="detail failure ratio below threshold",
        )
        return
    cooldown_until = datetime.now(tz=UTC) + timedelta(seconds=_detail_breaker_cooldown_seconds(source))
    db.set_source_breaker(
        source_id=source.id,
        breaker_type=breaker_type,
        state="open",
        failure_count=detail_failures,
        success_count=0,
        cooldown_until=cooldown_until,
        reason="detail probe failed",
    )


def _detail_breaker_cooldown_seconds(source: OrganizationSource) -> int:
    configured = _optional_int(source.extra.get("detail_breaker_probe_cooldown_seconds"))
    return configured or _DETAIL_BREAKER_PROBE_COOLDOWN_SECONDS


def _prioritize_detail_refresh_jobs(
    db: JobDatabase,
    jobs: list,
    cutoff: datetime,
    *,
    refresh_all_details: bool,
) -> list:
    decorated = [
        (_detail_refresh_priority(db, job, cutoff, refresh_all_details=refresh_all_details), index, job)
        for index, job in enumerate(jobs)
    ]
    return [job for _, _, job in sorted(decorated, key=lambda item: (item[0], item[1]))]


def _detail_refresh_priority(
    db: JobDatabase,
    job,
    cutoff: datetime,
    *,
    refresh_all_details: bool,
) -> int:
    current = db.get_job(job.identity_key())
    if current is None:
        return 0
    if _missing_detail_metadata(current, job):
        return 1
    if _classification_detail_required(db, current, job):
        return 2
    quality_status = detail_quality_status(
        title=current.get("title"),
        description=current.get("description"),
        raw=current.get("raw") if isinstance(current.get("raw"), dict) else None,
        detail_status=None,
    )
    if detail_quality_requeue_reason(quality_status) is not None:
        return 3
    if _short_description(current.get("description")):
        return 4
    if refresh_all_details:
        return 5
    if _closing_needs_detail_refresh(current.get("closes_at"), cutoff):
        return 6
    return 9


def _classification_detail_required(db: JobDatabase, current: dict, listing_job) -> bool:
    source_id = getattr(listing_job, "source_id", "") or str(current.get("source_id") or "")
    if source_id not in {
        "undp_oracle_hcm",
        "unfpa_oracle_hcm",
        "unwomen_oracle_hcm",
        "icao_oracle_hcm",
        "wmo_oracle_hcm",
        "iom_oracle_hcm",
        "unicef_pageup",
        "unesco_successfactors",
        "aiib_successfactors_legacy",
    }:
        return False
    if not _classification_lacks_standardization(db, str(current.get("job_key") or "")):
        return False
    raw = current.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    if source_id == "unicef_pageup":
        return not raw.get("detail_html")
    if source_id == "unesco_successfactors":
        return raw.get("parser") != "successfactors_detail"
    if source_id == "aiib_successfactors_legacy":
        return raw.get("parser") != "aiib_official_detail"
    return True


def _classification_lacks_standardization(db: JobDatabase, job_key: str) -> bool:
    if not job_key:
        return True
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT grade_mapping_raw_grade_code, standard_seniority_tier
            FROM vacancy_classifications
            WHERE vacancy_id = ?
            """,
            (job_key,),
        ).fetchone()
    if row is None:
        return True
    return not row["grade_mapping_raw_grade_code"] or not row["standard_seniority_tier"]


def _short_description(value: object | None) -> bool:
    return len(str(value or "").strip()) < 500


def _closing_needs_detail_refresh(value: str | None, cutoff: datetime) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= cutoff


def _missing_detail_metadata(current: dict, listing_job) -> bool:
    raw = current.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    source_id = getattr(listing_job, "source_id", "") or str(current.get("source_id") or "")
    if source_id in {"adb_taleo", "fao_taleo"}:
        flat = raw.get("_taleo_flat") if isinstance(raw.get("_taleo_flat"), dict) else {}
        expected_keys = {
            "JOB_LEVEL",
            "Position Level",
            "Grade Level",
            "TYPE_OF_REQUISITION",
            "Type of Requisition",
        }
        return not any(flat.get(key) not in (None, "") for key in expected_keys)
    if source_id in {
        "undp_oracle_hcm",
        "unfpa_oracle_hcm",
        "unwomen_oracle_hcm",
        "icao_oracle_hcm",
        "wmo_oracle_hcm",
        "iom_oracle_hcm",
    }:
        flex_fields = raw.get("requisitionFlexFields")
        return not (isinstance(flex_fields, list) and flex_fields)
    if source_id == "unicef_pageup":
        return not raw.get("detail_html")
    if source_id == "unesco_successfactors":
        return raw.get("parser") != "successfactors_detail"
    if source_id == "aiib_successfactors_legacy":
        return raw.get("parser") != "aiib_official_detail"
    return False


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
    tls_verify = _optional_bool(source.extra.get("tls_verify"))
    default_headers = {}
    cookie_header = str(source.extra.get("cookie_header") or "").strip()
    if cookie_header:
        default_headers["Cookie"] = cookie_header
    return JobAggHTTPClient(
        user_agent=policy.user_agent,
        timeout_seconds=timeout_seconds,
        min_delay_seconds=policy.min_delay_for(source_host),
        max_retries=max_retries if max_retries is not None else 3,
        backoff_base_seconds=backoff_base_seconds if backoff_base_seconds is not None else 1.0,
        tls_verify=True if tls_verify is None else tls_verify,
        default_headers=default_headers,
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


class _DetailFetchPacer:
    def __init__(
        self,
        *,
        min_delay_seconds: float = 0.0,
        jitter_seconds: float = 0.0,
        batch_size: int | None = None,
        batch_pause_seconds: float = 0.0,
    ) -> None:
        self.min_delay_seconds = max(0.0, min_delay_seconds)
        self.jitter_seconds = max(0.0, jitter_seconds)
        self.batch_size = batch_size if batch_size and batch_size > 0 else None
        self.batch_pause_seconds = max(0.0, batch_pause_seconds)
        self._last_attempt_at: float | None = None

    @classmethod
    def from_source(cls, source: OrganizationSource) -> "_DetailFetchPacer":
        extra = source.extra
        min_delay = _optional_float(extra.get("oracle_detail_min_delay_seconds"))
        if min_delay is None:
            min_delay = _optional_float(extra.get("detail_min_delay_seconds")) or 0.0
        jitter = _optional_float(extra.get("oracle_detail_jitter_seconds"))
        if jitter is None:
            jitter = _optional_float(extra.get("detail_jitter_seconds")) or 0.0
        batch_size = _optional_int(extra.get("oracle_detail_batch_size"))
        if batch_size is None:
            batch_size = _optional_int(extra.get("detail_batch_size"))
        batch_pause = _optional_float(extra.get("oracle_detail_batch_pause_seconds"))
        if batch_pause is None:
            batch_pause = _optional_float(extra.get("detail_batch_pause_seconds")) or 0.0
        return cls(
            min_delay_seconds=min_delay,
            jitter_seconds=jitter,
            batch_size=batch_size,
            batch_pause_seconds=batch_pause,
        )

    def before_attempt(self, attempts_completed: int) -> None:
        if (
            self.batch_size
            and self.batch_pause_seconds > 0
            and attempts_completed > 0
            and attempts_completed % self.batch_size == 0
        ):
            time.sleep(_with_jitter(self.batch_pause_seconds, self.jitter_seconds))
            self._last_attempt_at = None
        if self.min_delay_seconds <= 0 or self._last_attempt_at is None:
            self._last_attempt_at = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_attempt_at
        remaining = self.min_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(_with_jitter(remaining, self.jitter_seconds))
        self._last_attempt_at = time.monotonic()


def _with_jitter(delay: float, jitter_seconds: float) -> float:
    if delay <= 0:
        return 0.0
    if jitter_seconds <= 0:
        return delay
    return delay + random.uniform(0, jitter_seconds)


def _is_transient_detail_error(exc: Exception) -> bool:
    text = repr(exc).casefold()
    if "http 401" in text or "http 403" in text or "forbidden" in text:
        return False
    return any(
        pattern in text
        for pattern in (
            "http 429",
            "too many requests",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
            "service unavailable",
            "gateway timeout",
            "timed out",
            "timeout",
            "remote end closed connection",
            "remotedisconnected",
            "connection reset",
            "connection aborted",
            "connection refused",
            "temporarily unavailable",
        )
    )


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
    diagnostics = result.diagnostics
    if diagnostics is not None:
        diagnostics.list_error_count += 1
        diagnostics.pagination_complete = False
        diagnostics.health_status = "issue"
        diagnostics.run_classification = "inconclusive"
        diagnostics.missing_transition_allowed = False
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
    if result.diagnostics.run_classification is None:
        result.diagnostics.run_classification = _classification_from_health(result.diagnostics)
    if result.diagnostics.publishability_classification is None:
        result.diagnostics.publishability_classification = _publishability_from_result(result)
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
            diagnostics.run_classification = diagnostics.run_classification or "ok_empty"
        elif diagnostics.health_status is None:
            diagnostics.health_status = "issue" if _empty_policy_requires_verification(source) else "warning"
            diagnostics.empty_reason = diagnostics.empty_reason or "unverified_zero"
            diagnostics.run_classification = "inconclusive"
    elif diagnostics.pagination_complete is False:
        diagnostics.health_status = "issue"
        diagnostics.run_classification = "inconclusive"
        if not any("pagination incomplete" in error for error in result.errors):
            result.errors.append(f"{source.id}: pagination incomplete")
    elif diagnostics.health_status is None:
        diagnostics.health_status = "ok"
        diagnostics.run_classification = diagnostics.run_classification or "ok"
    elif diagnostics.run_classification is None:
        diagnostics.run_classification = _classification_from_health(diagnostics)


def _mark_list_failure(
    diagnostics: SourceRunDiagnostics | None,
    *,
    empty_reason: str | None = None,
    error: Exception | None = None,
    run_classification: str | None = None,
) -> None:
    if diagnostics is None:
        return
    diagnostics.list_error_count += 1
    diagnostics.health_status = "issue"
    diagnostics.pagination_complete = False
    diagnostics.missing_transition_allowed = False
    classification = run_classification or _classify_run_failure(error)
    diagnostics.run_classification = classification
    diagnostics.blocked = classification == "blocked"
    diagnostics.transient_error = classification == "transient_error"
    if empty_reason:
        diagnostics.empty_reason = empty_reason


def _apply_detail_diagnostics(
    result: SyncResult,
    detail_attempts: int,
    detail_failures: int,
    detail_skipped: int,
    detail_errors: list[str],
    *,
    detail_breaker_opened: bool = False,
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
    if detail_breaker_opened:
        diagnostics.health_status = "issue"
        diagnostics.run_classification = "source_adapter_broken"
        diagnostics.publishability_classification = "publishable_list_only"
        result.errors.append(summary)
        return
    if ratio < 0.05:
        diagnostics.health_status = diagnostics.health_status or "ok"
        diagnostics.run_classification = diagnostics.run_classification or "ok"
        return
    if ratio < 0.30:
        diagnostics.health_status = "degraded"
        diagnostics.run_classification = "detail_degraded"
        diagnostics.publishability_classification = "publishable_detail_degraded"
        result.errors.append(summary)
        return
    diagnostics.health_status = "issue"
    diagnostics.run_classification = "detail_degraded"
    diagnostics.publishability_classification = "publishable_detail_degraded"
    result.errors.append(summary)


def _missing_transition_allowed(
    *,
    close_missing: bool,
    jobs: list,
    diagnostics: SourceRunDiagnostics | None,
) -> bool:
    if not close_missing or diagnostics is None:
        return False
    if diagnostics.blocked or diagnostics.transient_error:
        return False
    if diagnostics.run_classification in {
        "blocked",
        "source_blocked",
        "transient_error",
        "inconclusive",
        "source_inconclusive",
        "parser_error",
        "source_parser_error",
        "source_adapter_broken",
    }:
        return False
    if diagnostics.health_status not in _MISSING_ALLOWED_HEALTH:
        return False
    if diagnostics.pagination_complete is not True:
        return False
    if diagnostics.scope_validation_status not in {None, "passed", "not_applicable"}:
        return False
    if not jobs and not _verified_zero_fetch(diagnostics):
        return False
    return True


def _classification_from_health(diagnostics: SourceRunDiagnostics) -> str:
    if diagnostics.blocked:
        return "blocked"
    if diagnostics.transient_error:
        return "transient_error"
    if diagnostics.health_status == "ok_empty":
        return "ok_empty"
    if diagnostics.health_status == "ok":
        return "ok"
    if diagnostics.health_status in {"degraded", "warning"}:
        return "detail_degraded" if diagnostics.detail_failed else "inconclusive"
    return "parser_error" if diagnostics.list_error_count else "inconclusive"


def _publishability_from_result(result: SyncResult) -> str:
    diagnostics = result.diagnostics
    if diagnostics is None:
        return "source_inconclusive" if result.errors else "ok"
    classification = diagnostics.run_classification or _classification_from_health(diagnostics)
    if diagnostics.publishability_classification:
        return diagnostics.publishability_classification
    if classification in {"ok", "ok_empty"} and not result.errors:
        return classification
    if classification in {"detail_degraded", "publishable_detail_degraded"}:
        return "publishable_detail_degraded"
    if classification in {"source_adapter_broken", "adapter_failed"}:
        return "publishable_list_only"
    if classification in {"blocked", "source_blocked"}:
        return "source_blocked"
    if classification in {"parser_error", "source_parser_error"}:
        return "source_parser_error"
    if classification in {"transient_error", "inconclusive", "source_inconclusive"}:
        return "source_inconclusive"
    if result.errors:
        return "source_inconclusive"
    return "ok_empty" if result.fetched == 0 and _verified_zero_fetch(diagnostics) else "ok"


def _classify_run_failure(error: Exception | None) -> str:
    text = repr(error).casefold() if error is not None else ""
    if any(marker in text for marker in _BLOCKED_RUN_MARKERS):
        return "blocked"
    if any(marker in text for marker in _TRANSIENT_RUN_MARKERS):
        return "transient_error"
    if any(marker in text for marker in ("json", "parse", "expecting value", "invalid")):
        return "parser_error"
    return "parser_error"


def _detail_error_reason(exc: Exception) -> str:
    text = repr(exc).casefold()
    for marker in _TRANSIENT_RUN_MARKERS:
        if marker in text:
            return marker.replace(" ", "_")
    return "transient"


def _log_list_progress(
    source: OrganizationSource,
    result: SyncResult,
    started_at: float,
) -> None:
    diagnostics = result.diagnostics
    elapsed = time.monotonic() - started_at
    LOGGER.info(
        "%s list fetched=%s total=%s pages=%s pagination_complete=%s classification=%s elapsed=%.2fs",
        source.id,
        result.fetched,
        diagnostics.total_reported_by_source if diagnostics else None,
        diagnostics.pages_fetched if diagnostics else None,
        diagnostics.pagination_complete if diagnostics else None,
        diagnostics.run_classification if diagnostics else None,
        elapsed,
    )


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
