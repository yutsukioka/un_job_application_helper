from jobagg.adapters.base import AdapterContext
from jobagg.adapters.taleo import TaleoAdapter
from jobagg.models import OrganizationSource


class HTTP:
    def __init__(self,pages):self.pages=pages
    def post_json(self,url,payload,headers=None):
        data=self.pages[payload['pageNo']]
        class Response:
            def json(self):return data
        return Response()


def page(ids,total):
    return {'pagingData':{'pageSize':2,'totalCount':total},'requisitionList':[
        {'contestNo':str(i),'jobId':str(i),'column':[f'Job {i}']} for i in ids]}


def fetch(pages,max_pages=3):
    source=OrganizationSource(id='test_taleo',name='Test',ats_family='taleo',base_url='https://example.taleo.net',
        extra={'search_api_url':'https://example.taleo.net/search','max_pages':max_pages,'warmup_search_page':False})
    adapter=TaleoAdapter(AdapterContext(source=source,http=HTTP(pages)))
    jobs=adapter.fetch_jobs()
    return adapter,jobs


def test_total_gap_is_not_certified():
    adapter,jobs=fetch({1:page([1,2],4),2:page([3],4)})
    assert len(jobs)==3 and adapter.run_diagnostics.pagination_complete is False
    assert adapter.run_diagnostics.total_reported_by_source==4


def test_exact_unique_inventory_passes():
    adapter,jobs=fetch({1:page([1,2],3),2:page([3],3)})
    assert len(jobs)==3 and adapter.run_diagnostics.pagination_complete is True


def test_page_cap_does_not_pass():
    adapter,jobs=fetch({1:page([1,2],4)},1)
    assert adapter.run_diagnostics.pagination_complete is False


def test_repeated_rows_do_not_pass():
    adapter,jobs=fetch({1:page([1,2],4),2:page([1,2],4)})
    assert adapter.run_diagnostics.pagination_complete is False
