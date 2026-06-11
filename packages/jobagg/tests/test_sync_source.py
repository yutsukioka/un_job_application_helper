from datetime import UTC, datetime

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job
from jobagg.pipelines.sync_source import (
    _needs_detail_refresh,
    sync_source,
    sync_source_with_selective_details,
)
from jobagg.robots import RobotsPolicy


@register_adapter
class ZeroFetchTestAdapter(JobAdapter):
    family = "zero_fetch_test"

    def fetch_jobs(self):
        if self.source.extra.get("empty", False):
            return []
        return [
            build_job(
                self.source,
                title="Fetched Role",
                external_id="FETCHED",
                location="Geneva",
                closes_at="2026-05-30",
                apply_url="https://example.org/jobs/FETCHED",
            )
        ]


@register_adapter
class SelectiveDetailFailureTestAdapter(JobAdapter):
    family = "selective_detail_failure_test"

    def fetch_jobs(self):
        return [
            build_job(
                self.source,
                title="Listing Role 1",
                external_id="A1",
                location="Geneva",
                apply_url="https://example.org/jobs/A1",
                raw={"id": "A1"},
            ),
            build_job(
                self.source,
                title="Listing Role 2",
                external_id="A2",
                location="Rome",
                apply_url="https://example.org/jobs/A2",
                raw={"id": "A2"},
            ),
        ]

    def fetch_detail_for_listing_item(self, item):
        if item["id"] == "A1":
            raise RuntimeError("detail timeout")
        return build_job(
            self.source,
            title="Detailed Role 2",
            external_id="A2",
            location="Rome",
            closes_at="2026-06-15",
            description="Detailed responsibilities.",
            apply_url="https://example.org/jobs/A2",
            raw={"id": "A2", "detail": True},
        )


@register_adapter
class SelectiveDetailNoneTestAdapter(JobAdapter):
    family = "selective_detail_none_test"

    def fetch_jobs(self):
        return [
            build_job(
                self.source,
                title="Listing Role",
                external_id="A1",
                location="Geneva",
                apply_url="https://example.org/jobs/A1",
                raw={"id": "A1"},
            )
        ]

    def fetch_detail_for_listing_item(self, item):
        return None


@register_adapter
class SelectiveDetailBudgetTestAdapter(JobAdapter):
    family = "selective_detail_budget_test"

    def fetch_jobs(self):
        return [
            build_job(
                self.source,
                title=f"Listing Role {index}",
                external_id=f"A{index}",
                apply_url=f"https://example.org/jobs/A{index}",
                raw={"id": f"A{index}"},
            )
            for index in range(1, 4)
        ]

    def fetch_detail_for_listing_item(self, item):
        return build_job(
            self.source,
            title=f"Detailed Role {item['id']}",
            external_id=item["id"],
            closes_at="2026-06-15",
            apply_url=f"https://example.org/jobs/{item['id']}",
            raw={"id": item["id"], "detail": True},
        )


@register_adapter
class SelectiveDetailTransientStopTestAdapter(JobAdapter):
    family = "selective_detail_transient_stop_test"

    def fetch_jobs(self):
        return [
            build_job(
                self.source,
                title=f"Listing Role {index}",
                external_id=f"A{index}",
                apply_url=f"https://example.org/jobs/A{index}",
                raw={"id": f"A{index}"},
            )
            for index in range(1, 6)
        ]

    def fetch_detail_for_listing_item(self, item):
        raise RuntimeError("Remote end closed connection without response")


@register_adapter
class SelectiveDetailPriorityTestAdapter(JobAdapter):
    family = "selective_detail_priority_test"

    def fetch_jobs(self):
        return [
            build_job(
                self.source,
                title=f"Listing Role {index}",
                external_id=f"A{index}",
                closes_at="2026-12-31",
                apply_url=f"https://example.org/jobs/A{index}",
                raw={"id": f"A{index}", "listing_html": "<li>listing</li>"},
            )
            for index in range(1, 4)
        ]

    def fetch_detail_for_listing_item(self, item):
        return build_job(
            self.source,
            title=f"Detailed Role {item['id']}",
            external_id=item["id"],
            closes_at="2026-12-31",
            description=f"Detailed responsibilities for {item['id']}.",
            apply_url=f"https://example.org/jobs/{item['id']}",
            raw={"id": item["id"], "detail_html": "<article>detail</article>"},
        )


@register_adapter
class CapTestAdapter(JobAdapter):
    family = "cap_test"

    def fetch_jobs(self):
        return [
            build_job(
                self.source,
                title="Fetched Role 1",
                external_id="A1",
                location="Geneva",
                apply_url="https://example.org/jobs/A1",
            ),
            build_job(
                self.source,
                title="Fetched Role 2",
                external_id="A2",
                location="Rome",
                apply_url="https://example.org/jobs/A2",
            ),
        ]


@register_adapter
class PageCapTestAdapter(JobAdapter):
    family = "page_cap_test"

    def fetch_jobs(self):
        max_pages = self.source.extra.get("max_pages")
        fallback_max_pages = self.source.extra.get("fallback_max_pages")
        return [
            build_job(
                self.source,
                title="Page Cap Role",
                external_id="PAGE",
                apply_url="https://example.org/jobs/PAGE",
                raw={"max_pages": max_pages, "fallback_max_pages": fallback_max_pages},
            )
        ]


@register_adapter
class HTTPConfigTestAdapter(JobAdapter):
    family = "http_config_test"

    def fetch_jobs(self):
        return [
            build_job(
                self.source,
                title="HTTP Config Role",
                external_id="HTTP",
                apply_url="https://example.org/jobs/HTTP",
                raw={
                    "timeout_seconds": self.context.http.timeout_seconds,
                    "max_retries": self.context.http.max_retries,
                    "backoff_base_seconds": self.context.http.backoff_base_seconds,
                },
            )
        ]


@register_adapter
class VerifiedEmptyTestAdapter(JobAdapter):
    family = "verified_empty_test"

    def fetch_jobs(self):
        self.run_diagnostics.health_status = "ok_empty"
        self.run_diagnostics.empty_reason = "verified_total_zero"
        self.run_diagnostics.zero_fetched_evidence = {"total_reported_by_source": 0}
        self.run_diagnostics.pagination_complete = True
        self.run_diagnostics.scope_validation_status = "passed"
        return []


def test_new_listing_with_closing_date_still_needs_detail_refresh(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source("adb_taleo", "taleo")
    listing_job = build_job(
        source,
        title="Senior Education Specialist",
        external_id="260596",
        closes_at="2026-06-30",
        apply_url="https://adb.taleo.net/careersection/1/jobdetail.ftl?job=260596",
    )

    assert _needs_detail_refresh(
        db,
        listing_job.identity_key(),
        datetime(2026, 6, 15, tzinfo=UTC),
        listing_job=listing_job,
    )


def test_zero_fetch_first_run_is_allowed_and_recorded(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source("empty_first_run", "zero_fetch_test", empty=True)

    result = sync_source(source, db=db, policy=_policy())

    assert result.fetched == 0
    assert result.errors == []
    assert list(db.iter_jobs(source_id=source.id)) == []
    runs = list(db.iter_source_runs(source.id))
    diagnostics = list(db.iter_source_run_diagnostics(source.id))
    assert len(runs) == 1
    assert runs[0]["fetched"] == 0
    assert runs[0]["errors"] == []
    assert diagnostics[0]["health_status"] == "warning"
    assert diagnostics[0]["empty_reason"] == "unverified_zero"
    assert diagnostics[0]["missing_transition_allowed"] is False


def test_zero_fetch_with_active_jobs_skips_missing_marking(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source("active_zero_fetch", "zero_fetch_test", empty=True)
    existing = build_job(
        source,
        title="Existing Role",
        external_id="OLD",
        closes_at="2026-12-30",
        apply_url="https://example.org/jobs/OLD",
    )
    db.upsert_job(existing)

    result = sync_source(source, db=db, policy=_policy(), missing_run_threshold=1)

    assert result.fetched == 0
    assert result.missing == 0
    assert result.closed == 0
    assert "zero jobs fetched for source with active jobs" in result.errors[0]
    stored = db.get_job(existing.identity_key())
    assert stored["status"] == "open"
    assert stored["missing_run_count"] == 0
    runs = list(db.iter_source_runs(source.id))
    assert len(runs) == 1
    assert runs[0]["errors"] == result.errors


def test_zero_fetch_allow_empty_source_without_evidence_does_not_mark_missing(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source(
        "legit_empty_source",
        "zero_fetch_test",
        empty=True,
        allow_empty_source=True,
    )
    existing = build_job(
        source,
        title="Existing Role",
        external_id="OLD",
        closes_at="2026-12-30",
        apply_url="https://example.org/jobs/OLD",
    )
    db.upsert_job(existing)

    result = sync_source(source, db=db, policy=_policy(), missing_run_threshold=1)

    assert result.fetched == 0
    assert result.missing == 0
    assert "zero jobs fetched for source with active jobs" in result.errors[0]
    assert db.get_job(existing.identity_key())["status"] == "open"


def test_verified_empty_zero_fetch_allows_missing_transition(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source("verified_empty_source", "verified_empty_test")
    existing = build_job(
        source,
        title="Existing Role",
        external_id="OLD",
        closes_at="2026-12-30",
        apply_url="https://example.org/jobs/OLD",
    )
    db.upsert_job(existing)

    result = sync_source(source, db=db, policy=_policy(), missing_run_threshold=1)

    assert result.fetched == 0
    assert result.errors == []
    assert result.missing == 1
    assert db.get_job(existing.identity_key())["status"] == "missing"
    diagnostics = list(db.iter_source_run_diagnostics(source.id))
    assert diagnostics[0]["health_status"] == "ok_empty"
    assert diagnostics[0]["empty_reason"] == "verified_total_zero"
    assert diagnostics[0]["missing_transition_allowed"] is True


def test_non_empty_fetch_still_marks_missing(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source("non_empty_marks_missing", "zero_fetch_test", empty=False)
    existing = build_job(
        source,
        title="Existing Role",
        external_id="OLD",
        closes_at="2026-12-30",
        apply_url="https://example.org/jobs/OLD",
    )
    db.upsert_job(existing)

    result = sync_source(source, db=db, policy=_policy(), missing_run_threshold=1)

    assert result.fetched == 1
    assert result.inserted == 1
    assert result.missing == 1
    assert result.errors == []
    assert db.get_job(existing.identity_key())["status"] == "missing"


def test_selective_detail_failure_keeps_listing_job_and_records_error(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source("selective_detail", "selective_detail_failure_test")

    result = sync_source_with_selective_details(
        source,
        db=db,
        policy=_policy(),
        refresh_all_details=True,
    )

    assert result.fetched == 2
    assert result.inserted == 2
    assert len(result.errors) == 1
    assert "detail refresh failed for 1/2 jobs" in result.errors[0]
    assert "A1 detail refresh failed: detail timeout" in result.errors[0]
    listing_only = db.get_job("selective_detail:A1")
    detailed = db.get_job("selective_detail:A2")
    assert listing_only["title"] == "Listing Role 1"
    assert detailed["title"] == "Detailed Role 2"
    assert detailed["description"] == "Detailed responsibilities."
    diagnostics = list(db.iter_source_run_diagnostics(source.id))
    assert diagnostics[0]["detail_attempted"] == 2
    assert diagnostics[0]["detail_succeeded"] == 1
    assert diagnostics[0]["detail_failed"] == 1
    assert diagnostics[0]["health_status"] == "issue"


def test_selective_detail_none_counts_as_failure(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source("selective_detail_none", "selective_detail_none_test")

    result = sync_source_with_selective_details(
        source,
        db=db,
        policy=_policy(),
        refresh_all_details=True,
    )

    assert result.fetched == 1
    assert result.inserted == 1
    assert "detail refresh returned no detail" in result.errors[0]
    diagnostics = list(db.iter_source_run_diagnostics(source.id))
    assert diagnostics[0]["detail_attempted"] == 1
    assert diagnostics[0]["detail_succeeded"] == 0
    assert diagnostics[0]["detail_failed"] == 1


def test_selective_detail_budget_skips_remaining_jobs_without_error(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source(
        "selective_detail_budget",
        "selective_detail_budget_test",
        max_detail_pages_per_run=1,
    )

    result = sync_source_with_selective_details(
        source,
        db=db,
        policy=_policy(),
        refresh_all_details=True,
    )

    assert result.fetched == 3
    assert result.errors == []
    assert db.get_job("selective_detail_budget:A1")["title"] == "Detailed Role A1"
    assert db.get_job("selective_detail_budget:A2")["title"] == "Listing Role 2"
    diagnostics = list(db.iter_source_run_diagnostics(source.id))
    assert diagnostics[0]["detail_attempted"] == 1
    assert diagnostics[0]["detail_succeeded"] == 1
    assert diagnostics[0]["detail_failed"] == 0
    assert diagnostics[0]["detail_skipped"] == 2


def test_selective_detail_budget_zero_disables_detail_fetch(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source(
        "selective_detail_budget_zero",
        "selective_detail_budget_test",
        max_detail_pages_per_run=0,
    )

    result = sync_source_with_selective_details(
        source,
        db=db,
        policy=_policy(),
        refresh_all_details=True,
    )

    assert result.fetched == 3
    assert result.errors == []
    assert db.get_job("selective_detail_budget_zero:A1")["title"] == "Listing Role 1"
    diagnostics = list(db.iter_source_run_diagnostics(source.id))
    assert diagnostics[0]["detail_attempted"] == 0
    assert diagnostics[0]["detail_succeeded"] == 0
    assert diagnostics[0]["detail_failed"] == 0
    assert diagnostics[0]["detail_skipped"] == 3


def test_selective_detail_pacing_uses_delay_and_batch_pause(tmp_path, monkeypatch):
    sleep_calls = []
    monkeypatch.setattr("jobagg.pipelines.sync_source.time.monotonic", lambda: 0.0)
    monkeypatch.setattr("jobagg.pipelines.sync_source.time.sleep", sleep_calls.append)

    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source(
        "selective_detail_paced",
        "selective_detail_budget_test",
        oracle_detail_min_delay_seconds=2,
        oracle_detail_batch_size=2,
        oracle_detail_batch_pause_seconds=5,
    )

    result = sync_source_with_selective_details(
        source,
        db=db,
        policy=_policy(),
        refresh_all_details=True,
    )

    assert result.errors == []
    assert sleep_calls == [2, 5]
    diagnostics = list(db.iter_source_run_diagnostics(source.id))
    assert diagnostics[0]["detail_attempted"] == 3
    assert diagnostics[0]["detail_failed"] == 0


def test_selective_detail_stops_after_transient_failure_limit(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source(
        "selective_detail_transient_stop",
        "selective_detail_transient_stop_test",
        oracle_detail_stop_after_transient_failures=3,
        oracle_detail_failure_cooldown_seconds=900,
    )

    result = sync_source_with_selective_details(
        source,
        db=db,
        policy=_policy(),
        refresh_all_details=True,
    )

    assert result.fetched == 5
    assert any("3 transient failures reached limit 3" in error for error in result.errors)
    assert any("host cooldown recommended for 900s" in error for error in result.errors)
    diagnostics = list(db.iter_source_run_diagnostics(source.id))
    assert diagnostics[0]["detail_attempted"] == 3
    assert diagnostics[0]["detail_failed"] == 3
    assert diagnostics[0]["detail_skipped"] == 2
    assert diagnostics[0]["health_status"] == "issue"


def test_selective_detail_budget_prioritizes_null_classification_rows(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source(
        "unicef_pageup",
        "selective_detail_priority_test",
        max_detail_pages_per_run=1,
    )
    for external_id in ("A1", "A2", "A3"):
        db.upsert_job(
            build_job(
                source,
                title=f"Existing Role {external_id}",
                external_id=external_id,
                closes_at="2026-12-31",
                description="Existing teaser text that is not enough for classification.",
                apply_url=f"https://example.org/jobs/{external_id}",
                raw={
                    "id": external_id,
                    "detail_html": "<article>detail</article>"
                    if external_id in {"A1", "A3"}
                    else None,
                    "listing_html": "<li>listing</li>",
                },
            )
        )
    with db.connect() as conn:
        for job_key in ("unicef_pageup:A1", "unicef_pageup:A3"):
            conn.execute(
                """
                INSERT INTO vacancy_classifications (
                    vacancy_id, grade_mapping_raw_grade_code, standard_seniority_tier,
                    needs_review, classification_version, classified_at
                )
                VALUES (?, 'Consultant', 'T0_NONSTAFF_UNGRADED', 0, 'test', '2026-01-01T00:00:00+00:00')
                """,
                (job_key,),
            )

    result = sync_source_with_selective_details(source, db=db, policy=_policy())

    assert result.errors == []
    assert db.get_job("unicef_pageup:A1")["title"] == "Listing Role 1"
    assert db.get_job("unicef_pageup:A2")["title"] == "Detailed Role A2"
    assert db.get_job("unicef_pageup:A3")["title"] == "Listing Role 3"
    diagnostics = list(db.iter_source_run_diagnostics(source.id))
    assert diagnostics[0]["detail_attempted"] == 1
    assert diagnostics[0]["detail_skipped"] == 0


def test_capped_sync_blocks_missing_transition(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source("cap_source", "cap_test")
    existing = build_job(
        source,
        title="Fetched Role 2",
        external_id="A2",
        location="Rome",
        apply_url="https://example.org/jobs/A2",
    )
    db.upsert_job(existing)

    result = sync_source(
        source,
        db=db,
        policy=_policy(max_jobs_per_source=1),
        missing_run_threshold=1,
    )

    assert result.fetched == 1
    assert "above max_jobs_per_source=1" in result.errors[0]
    stored = db.get_job("cap_source:A2")
    assert stored["status"] == "open"
    assert stored["missing_run_count"] == 0
    diagnostics = list(db.iter_source_run_diagnostics(source.id))
    assert diagnostics[0]["health_status"] == "issue"
    assert diagnostics[0]["pagination_complete"] is False
    assert diagnostics[0]["missing_transition_allowed"] is False


def test_policy_max_pages_is_applied_to_adapter_source_extra(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source(
        "page_cap_source",
        "page_cap_test",
        max_pages=10,
        fallback_max_pages=8,
    )

    sync_source(source, db=db, policy=_policy(max_pages_per_source=2))

    stored = db.get_job("page_cap_source:PAGE")
    assert stored["raw"]["max_pages"] == 2
    assert stored["raw"]["fallback_max_pages"] == 2


def test_source_http_retry_and_timeout_overrides_reach_adapter(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source(
        "http_config_source",
        "http_config_test",
        request_timeout_seconds=60,
        max_retries=4,
        backoff_base_seconds=2,
    )

    sync_source(source, db=db, policy=_policy(request_timeout_seconds=15))

    stored = db.get_job("http_config_source:HTTP")
    assert stored["raw"] == {
        "timeout_seconds": 60,
        "max_retries": 4,
        "backoff_base_seconds": 2,
    }


def _source(source_id, family, **extra):
    return OrganizationSource(
        id=source_id,
        name=source_id,
        ats_family=family,
        base_url="https://example.org",
        extra=extra,
    )


def _policy(**overrides):
    values = {"honor_robots_txt": False, "min_delay_seconds": 0}
    values.update(overrides)
    return RobotsPolicy(**values)
