from jobagg.adapters.base import AdapterContext
from jobagg.adapters.inspira import InspiraAdapter
from jobagg.models import OrganizationSource


class HTTP:
    def __init__(self, final_total=3):
        self.calls = []
        self.final_total = final_total

    def post_json(self, url, payload, headers=None):
        self.calls.append(payload)
        page = payload["pagination"]["page"]
        size = payload["pagination"]["itemPerPage"]
        ids = [1, 2, 3] if size == 3 else ([1, 2] if page == 0 else [2])
        total = self.final_total if size == 3 else 3
        data = {
            "data": {
                "count": total,
                "list": [{"jobId": i, "postingTitle": f"Job {i}"} for i in ids],
            }
        }

        class Response:
            def json(self):
                return data

        return Response()


def adapter(http, **extra):
    source = OrganizationSource(
        id="un_inspira",
        name="UN",
        ats_family="inspira",
        base_url="https://careers.un.org",
        extra={"page_size": 2, "max_pages": 3, "filter_config": {"jc": ["PD"]}, **extra},
    )
    return InspiraAdapter(AdapterContext(source=source, http=http))


def test_duplicate_offset_inventory_is_reconciled_using_observed_total():
    http = HTTP()
    instance = adapter(http)
    assert {job.external_id for job in instance.fetch_jobs()} == {"1", "2", "3"}
    assert http.calls[-1]["pagination"]["itemPerPage"] == 3
    assert all(call["filterConfig"] == {"jc": ["PD"]} for call in http.calls)
    assert instance.run_diagnostics.pagination_complete is True


def test_changed_total_does_not_pass():
    instance = adapter(HTTP(final_total=4))
    instance.fetch_jobs()
    assert instance.run_diagnostics.pagination_complete is False


def test_single_request_bound_does_not_hide_missing_ids():
    http = HTTP()
    instance = adapter(http, single_page_reconciliation_max_rows=2)
    instance.fetch_jobs()
    assert len(http.calls) == 2
    assert instance.run_diagnostics.pagination_complete is False
