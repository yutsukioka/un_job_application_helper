import pytest

from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource, SourceRunDiagnostics
from jobagg.pipelines.sync_source import (
    _prepare_list_breaker_for_run,
    _record_list_breaker_success,
    _with_list_probe_page_cap,
)


@pytest.mark.parametrize(
    "overrides,has_jobs,capped,recovers",
    [
        ({}, True, True, True),
        ({}, True, False, False),
        ({}, False, True, False),
        ({"list_error_count": 1}, True, True, False),
        ({"blocked": True}, True, True, False),
        ({"transient_error": True}, True, True, False),
        ({"scope_validation_status": "failed"}, True, True, False),
        ({"pages_fetched": 0}, True, True, False),
    ],
)
def test_capped_probe_recovery_preserves_lifecycle_safety(
    tmp_path, overrides, has_jobs, capped, recovers
):
    source = OrganizationSource(
        id="probe", name="Probe", ats_family="oracle_hcm",
        base_url="https://example.org", extra={"max_pages": 12},
    )
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    db.set_source_breaker(source_id=source.id, breaker_type="list", state="half_open")
    fields = dict(
        source_id=source.id, pages_fetched=1, pagination_complete=False,
        scope_validation_status="passed", run_classification="inconclusive",
        missing_transition_allowed=False,
    )
    fields.update(overrides)
    diagnostics = SourceRunDiagnostics(**fields)
    probe = _with_list_probe_page_cap(source) if capped else source
    _record_list_breaker_success(db, probe, diagnostics, [object()] if has_jobs else [])

    expected = "closed" if recovers else "half_open"
    assert db.get_source_breaker(source.id, "list")["state"] == expected
    assert _prepare_list_breaker_for_run(db, source)["state"] == expected
    assert diagnostics.pagination_complete is False
    assert diagnostics.missing_transition_allowed is False
    assert diagnostics.run_classification == "inconclusive"
    assert source.extra["max_pages"] == 12
