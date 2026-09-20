import json

from jobagg.adapters.static_html import parse_detail_page
from jobagg.models import OrganizationSource


def test_opcw_role_separator_preserved_in_public_title():
    source = OrganizationSource(id='opcw_talentsoft_candidatespace', name='OPCW', ats_family='static_html', base_url='https://jobs.opcw.org')
    job = parse_detail_page(source, '<h1>Internship - Office of Internal Oversight</h1><main>Full vacancy description</main>', 'https://jobs.opcw.org/job/job-internship_584.aspx')
    assert job.title == 'Internship - Office of Internal Oversight'


def test_unu_visible_qualifications_heading_retained_without_hidden_form():
    source = OrganizationSource(id='unu_recruitee', name='UNU', ats_family='static_html', base_url='https://careers.unu.edu')
    data = json.dumps({'@type': 'JobPosting', 'title': 'Programme Officer', 'description': '<p>Public description</p><p>Public requirements</p>'})
    page = f'''<link rel="canonical" href="https://careers.unu.edu/o/programme-officer"><div><h1>Programme Officer</h1></div><script type="application/ld+json">{data}</script>
    <div role="tabpanel"><h2>Job description</h2><p>Public description</p><h2>Qualifications</h2><p>Public requirements</p><br /></div>
    <div role="tabpanel" hidden><h2>Private application form</h2><input name="candidate-name"></div>'''
    job = parse_detail_page(source, page, 'https://careers.unu.edu/o/programme-officer')
    assert 'Qualifications Public requirements' in job.description
    assert 'Private application form' not in job.description
    assert job.raw['detail_html'] == page
