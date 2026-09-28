"""Taleo description fragments must preserve prose and drop CSS/script bodies."""
from types import SimpleNamespace
from urllib.parse import quote

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.taleo import TaleoAdapter
from jobagg.models import OrganizationSource


def adapter():
    source=OrganizationSource(id='who_taleo',name='WHO',ats_family='taleo',base_url='https://careers.who.int')
    return TaleoAdapter(AdapterContext(source=source,http=SimpleNamespace()))


def test_taleo_fragment_removes_styles_and_scripts_preserving_list_structure():
    source='<h2>Education</h2><STYLE>a {color: red;}</STYLE><p>Advanced degree.</p><script>window.hidden="Not a requirement";</script><ul><li>English required.</li></ul>'
    text=adapter()._clean_detail_html_fragment(source)
    assert text=='Education\n\nAdvanced degree.\n\n- English required.'


def test_taleo_serialized_detail_removes_encoded_css_without_losing_sections():
    values=['','true','','false','Submission for the position: Officer - (Job Number: 42)','false','','false','true','Officer','42',
            quote('<p>Duties: Deliver the programme.</p><style>.head {display:none;}</style><p>Qualifications: Degree and experience.</p>')]
    body="<script>api.fillList('requisitionDescriptionInterface', 'descRequisition', "+repr(values)+');</script>'
    job=adapter().parse_detail_html(body,'https://careers.who.int/jobdetail.ftl?job=42')
    assert job.external_id=='42'
    assert job.description=='Duties: Deliver the programme.\n\nQualifications: Degree and experience.'


def test_taleo_plain_prose_referencing_styles_and_scripts_is_retained():
    assert adapter()._clean_detail_html_fragment('<p>Write scripts and maintain style guides.</p>')=='Write scripts and maintain style guides.'
