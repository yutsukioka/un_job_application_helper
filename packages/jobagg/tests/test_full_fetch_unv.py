import pytest
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.unv import UNVAdapter
from jobagg.models import OrganizationSource


class HTTP:
    def __init__(self, payload):
        self.payload = payload

    def get(self, *args, **kwargs):
        return self

    def post_json(self, *args, **kwargs):
        return self

    def json(self):
        return self.payload


def adapter(payload):
    source = OrganizationSource(
        "unv_test",
        "UNV",
        "unv",
        "https://example.org",
        extra={"api_url": "https://example.org/search", "max_pages": 1},
    )
    return UNVAdapter(AdapterContext(source=source, http=HTTP(payload)))


def test_all_public_narrative_sections_preserved():
    item = {
        "id": 12,
        "name": "Officer",
        "taskDescription": "Task description",
        "competency": "Ethical judgment",
        "additionalEligibilityCriteria": "Age and residence eligibility",
        "accessibilityComment": "Accessible assignment",
    }
    job = adapter(None).parse_jobs({"value": item})[0]
    for key in (
        "taskDescription",
        "competency",
        "additionalEligibilityCriteria",
        "accessibilityComment",
    ):
        assert item[key] in job.description


def test_wrong_detail_identity_rejected():
    with pytest.raises(ValueError, match="identity"):
        adapter({"value": {"id": 2, "name": "Wrong"}}).fetch_detail_for_listing_item({"id": 1})


def test_capped_listing_cannot_be_complete():
    value = {"value": {"total": 2, "result": [{"id": 1, "name": "One"}]}}
    a = adapter(value)
    assert len(a.fetch_jobs()) == 1
    assert a.run_diagnostics.pagination_complete is False


def test_verified_empty_is_explicit():
    a = adapter({"value": {"total": 0, "result": []}})
    assert a.fetch_jobs() == []
    assert a.run_diagnostics.pagination_complete is True
    assert a.run_diagnostics.zero_fetched_evidence["total_reported_by_source"] == 0
