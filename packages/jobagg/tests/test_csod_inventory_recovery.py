import json
import pytest
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.csod import CSODAdapter
from jobagg.models import OrganizationSource
from jobagg.pipelines.http_checkpoint import HostIneligible


class HTTP:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def post_json(self, url, payload, **kwargs):
        self.calls.append(payload["pageNumber"])
        value = self.pages[payload["pageNumber"]]
        if isinstance(value, Exception):
            raise value
        return type("Response", (), {"json": lambda _: value})()


def page(total, *ids):
    return {
        "data": {
            "totalCount": total,
            "requisitions": [
                {"requisitionId": i, "title": f"Role {i}", "url": f"https://example.org/{i}"}
                for i in ids
            ],
        }
    }


def adapter(http, tmp_path, **extra):
    source = OrganizationSource(
        "test_csod",
        "Test",
        "csod",
        "https://example.org",
        extra={"api_url": "https://example.org/api", "page_size": 2, "max_pages": 5, **extra},
    )
    result = CSODAdapter(AdapterContext(source, http))
    result.listing_checkpoint_path = tmp_path / "checkpoint.json"
    return result


@pytest.mark.parametrize(
    "error",
    [
        HostIneligible("deadline", category="budget"),
        HostIneligible("held"),
        TimeoutError("deadline"),
        PermissionError("robots"),
    ],
)
def test_control_exceptions_are_never_wrapped_or_retried(tmp_path, error):
    http = HTTP({1: error})
    with pytest.raises(type(error)) as caught:
        adapter(http, tmp_path).fetch_jobs()
    assert caught.value is error and http.calls == [1]


def test_interruption_checkpoints_exact_scope_and_resumes_revalidated_prefix(tmp_path):
    http = HTTP({1: page(3, 1, 2), 2: HostIneligible("deadline", category="budget")})
    with pytest.raises(HostIneligible):
        adapter(http, tmp_path).fetch_jobs()
    state = json.loads((tmp_path / "checkpoint.json").read_text())
    assert state["captured_ids"] == ["1", "2"] and state["next_page"] == 2 and not state["complete"]
    http = HTTP({1: page(3, 1, 2), 2: page(3, 3)})
    a = adapter(http, tmp_path)
    assert [j.external_id for j in a.fetch_jobs()] == ["1", "2", "3"]
    assert http.calls == [1, 2] and a.run_diagnostics.pagination_complete


@pytest.mark.parametrize("changes", [{"search_payload": {"searchText": "changed"}}, {}])
def test_changed_scope_or_generation_drops_old_ids(tmp_path, changes):
    with pytest.raises(HostIneligible):
        adapter(
            HTTP({1: page(3, 1, 2), 2: HostIneligible("end", category="budget")}), tmp_path
        ).fetch_jobs()
    a = adapter(HTTP({1: page(1, 9)}), tmp_path, **changes)
    assert [j.external_id for j in a.fetch_jobs()] == ["9"]


@pytest.mark.parametrize(
    "pages",
    [{1: page(3, 1, 2), 2: page(3, 2)}, {1: page(3, 1)}, {1: page(3, 1, 2), 2: page(4, 3, 4)}],
)
def test_partial_duplicate_or_changing_total_never_returns_inventory(tmp_path, pages):
    with pytest.raises((RuntimeError, HostIneligible)):
        adapter(HTTP(pages), tmp_path).fetch_jobs()


def test_zero_is_explicit_and_page_limit_is_incomplete(tmp_path):
    a = adapter(HTTP({1: page(0)}), tmp_path)
    assert a.fetch_jobs() == [] and a.run_diagnostics.total_reported_by_source == 0
    with pytest.raises(RuntimeError):
        adapter(HTTP({1: page(3, 1, 2)}), tmp_path, max_pages=1).fetch_jobs()
