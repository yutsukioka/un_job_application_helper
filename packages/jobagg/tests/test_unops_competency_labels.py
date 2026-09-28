from pathlib import Path
import re

import pytest

from jobagg.adapters.avature import AvatureAdapter, _main_content, _unops_competency_content
from jobagg.adapters.base import AdapterContext
from jobagg.models import OrganizationSource
from jobagg.utils import clean_html

FIXTURE=Path(__file__).parent/'fixtures/unops/4362_20260913.html'
URL='https://careers.unops.org/careersmarketplace/JobDetail/Associate-Civil-Engineer-Quantity-Surveyor-Cost-Estimator/4362'
SOURCE=OrganizationSource('unops_avature','UNOPS','avature','https://careers.unops.org')
LABELS=['Respect','Collaboration','Partnerships','Excellence','Adaptability','Decision-making','Communication']


def test_actual_seven_competencies_keep_labels_at_their_public_positions():
    body=FIXTURE.read_text()
    job=AvatureAdapter(AdapterContext(SOURCE,object())).parse_detail_html(body,URL)
    assert job.raw['detail_html']==body
    assert job.raw['_avature_competency_text_resolution']['public_labels_in_order']==LABELS
    rendered,labels=_unops_competency_content(_main_content(body))
    assert labels==LABELS
    original_tags=re.findall(r'<img\b[^>]*class="competency__icon"[^>]*>',_main_content(body))
    assert len(original_tags)==7
    expected=_main_content(body)
    for tag,label in zip(original_tags,LABELS):
        expected=expected.replace(tag,'<span>'+label+'</span>',1)
    assert rendered==expected
    assert job.description==clean_html(expected)
    assert 'Respect Treats all individuals with respect' in ' '.join(job.description.split())
    assert 'Decision-making Evaluates data and courses of action' in ' '.join(job.description.split())
    assert 'Facebook Logo' not in job.description and 'UNOPS Logo' not in job.description


def test_scope_excludes_chrome_and_images_in_other_sections():
    body=('<img class="competency__icon" alt="Outside">'
          '<details class="article--details"><summary>Requirements</summary><img class="competency__icon" alt="Other"></details>'
          '<details class="article--details"><summary><span>Competencies</span></summary>'
          '<img alt="Respect" class="compact competency__icon"/><p>Act fairly.</p><img alt="Logo" class="brand"></details>')
    rendered,labels=_unops_competency_content(body)
    assert labels==['Respect']
    assert '<span>Respect</span><p>Act fairly.' in rendered
    assert 'alt="Outside"' in rendered and 'alt="Other"' in rendered and 'alt="Logo"' in rendered


def test_missing_competency_alt_fails_instead_of_silent_text_loss():
    with pytest.raises(ValueError,match='alternative label'):
        _unops_competency_content('<details class="article--details"><summary>Competencies</summary><img class="competency__icon"></details>')


def test_other_avature_source_keeps_existing_text_behavior():
    body=FIXTURE.read_text()
    source=OrganizationSource('other','Other','avature','https://example.org')
    job=AvatureAdapter(AdapterContext(source,object())).parse_detail_html(body,URL)
    assert job.description==clean_html(_main_content(body))
    assert '_avature_competency_text_resolution' not in job.raw
