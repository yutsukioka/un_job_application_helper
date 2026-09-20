import pytest
from jobagg.adapters.icddrb import _description_html
from jobagg.utils import clean_html

def test_nested_sections_preserve_tail_and_exclude_footer():
    text = '<div class="icddrb-invites"><p>Introduction</p><div>Contract <div>nested terms</div></div><p>Salary &amp; benefits</p><p>Apply by Friday</p></div><footer>Not job text</footer>'
    result = clean_html(_description_html(text))
    for phrase in ('Introduction', 'Contract', 'nested terms', 'Salary & benefits', 'Apply by Friday'):
        assert phrase in result
    assert 'Not job text' not in result

@pytest.mark.parametrize('text', ['<div>No job</div>', '<div class="icddrb-invites">Unclosed', '<div class="icddrb-invites"></div>', '<div class="icddrb-invites">One</div><div class="icddrb-invites">Two</div>'])
def test_missing_or_ambiguous_container_is_not_accepted(text):
    with pytest.raises(ValueError):
        _description_html(text)


def test_category_transport_failure_propagates_without_landing_fallback():
    from jobagg.adapters.base import AdapterContext
    from jobagg.adapters.icddrb import ICDDRBAdapter
    from jobagg.models import OrganizationSource
    from jobagg.pipelines.http_checkpoint import HostIneligible
    class HTTP:
        def get(self,url):
            return type('Response',(),{'text':'<a href="/vacancy-preview/1">Existing vacancy</a>'})()
        def post_form(self,*args,**kwargs): raise HostIneligible('budget',category='budget')
    adapter=ICDDRBAdapter(AdapterContext(OrganizationSource('icddrb','icddrb','icddrb_custom_html','https://career.icddrb.org/'),HTTP()))
    with pytest.raises(HostIneligible): adapter.fetch_jobs()
