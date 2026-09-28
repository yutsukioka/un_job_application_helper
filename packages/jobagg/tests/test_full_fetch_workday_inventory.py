from jobagg.adapters.base import AdapterContext
from jobagg.adapters.workday import WorkdayAdapter
from jobagg.models import OrganizationSource


class HTTP:
    def __init__(self, pages): self.pages=pages
    def post_json(self, url, payload):
        data=self.pages[payload['offset']]
        class Response:
            def json(self): return data
        return Response()


def fetch(pages, max_pages):
    source=OrganizationSource(id='test_workday',name='Test',ats_family='workday',
        base_url='https://example.myworkdayjobs.com',
        extra={'jobs_url':'https://example.myworkdayjobs.com/jobs','page_size':2,'max_pages':max_pages})
    adapter=WorkdayAdapter(AdapterContext(source=source,http=HTTP(pages)))
    return adapter,adapter.fetch_jobs()


def row(i): return {'title':f'Job {i}','externalPath':f'/job/{i}','jobReqId':str(i)}


def test_capped_inventory_does_not_pass():
    adapter,jobs=fetch({0:{'total':4,'jobPostings':[row(1),row(2)]}},1)
    assert len(jobs)==2 and adapter.run_diagnostics.pagination_complete is False


def test_premature_empty_does_not_pass():
    adapter,jobs=fetch({0:{'total':4,'jobPostings':[row(1),row(2)]},2:{'total':4,'jobPostings':[]}},3)
    assert len(jobs)==2 and adapter.run_diagnostics.pagination_complete is False


def test_exact_unique_total_passes():
    adapter,jobs=fetch({0:{'total':3,'jobPostings':[row(1),row(2)]},2:{'total':3,'jobPostings':[row(3)]}},3)
    assert len(jobs)==3 and adapter.run_diagnostics.pagination_complete is True


def test_changed_total_requires_reconciliation():
    adapter,jobs=fetch({0:{'total':4,'jobPostings':[row(1),row(2)]},2:{'total':3,'jobPostings':[row(3)]},4:{'total':3,'jobPostings':[]}},3)
    assert adapter.run_diagnostics.pagination_complete is False


def test_noninitial_zero_with_rows_does_not_replace_global_count():
    adapter,jobs=fetch({0:{'total':3,'jobPostings':[row(1),row(2)]},2:{'total':0,'jobPostings':[row(3)]}},3)
    assert len(jobs)==3 and adapter.run_diagnostics.pagination_complete is True
    assert adapter.run_diagnostics.total_reported_by_source==3


def test_noninitial_zero_does_not_hide_missing_jobs():
    adapter,jobs=fetch({0:{'total':4,'jobPostings':[row(1),row(2)]},2:{'total':0,'jobPostings':[row(3)]},4:{'total':0,'jobPostings':[]}},3)
    assert len(jobs)==3 and adapter.run_diagnostics.pagination_complete is False


def test_initial_zero_with_rows_is_inconsistent():
    adapter,jobs=fetch({0:{'total':0,'jobPostings':[row(1)]}},1)
    assert len(jobs)==1 and adapter.run_diagnostics.pagination_complete is False
