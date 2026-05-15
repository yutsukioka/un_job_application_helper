from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job
from jobagg.pipelines.sync_source import sync_source, sync_source_with_selective_details
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


def test_zero_fetch_first_run_is_allowed_and_recorded(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source("empty_first_run", "zero_fetch_test", empty=True)

    result = sync_source(source, db=db, policy=_policy())

    assert result.fetched == 0
    assert result.errors == []
    assert list(db.iter_jobs(source_id=source.id)) == []
    runs = list(db.iter_source_runs(source.id))
    assert len(runs) == 1
    assert runs[0]["fetched"] == 0
    assert runs[0]["errors"] == []


def test_zero_fetch_with_active_jobs_skips_missing_marking(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source("active_zero_fetch", "zero_fetch_test", empty=True)
    existing = build_job(
        source,
        title="Existing Role",
        external_id="OLD",
        closes_at="2026-05-30",
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


def test_non_empty_fetch_still_marks_missing(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    source = _source("non_empty_marks_missing", "zero_fetch_test", empty=False)
    existing = build_job(
        source,
        title="Existing Role",
        external_id="OLD",
        closes_at="2026-05-30",
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
    assert "A1 detail refresh failed: detail timeout" in result.errors[0]
    listing_only = db.get_job("selective_detail:A1")
    detailed = db.get_job("selective_detail:A2")
    assert listing_only["title"] == "Listing Role 1"
    assert detailed["title"] == "Detailed Role 2"
    assert detailed["description"] == "Detailed responsibilities."


def test_capped_sync_uses_full_seen_set_for_missing_marking(tmp_path):
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
    assert db.get_job("cap_source:A2")["status"] == "open"


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
