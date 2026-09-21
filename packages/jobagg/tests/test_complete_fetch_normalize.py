"""Preserve public job prose while excluding source script/style bodies."""
import pytest

from jobagg.models import OrganizationSource
from jobagg.normalize import build_job, clean_text


@pytest.mark.parametrize('markup', [
    '<STYLE type="text/css">.headtable { font-family: Arial; }</STYLE>',
    '<script>window.open("/session");\nconst hidden = "Not job prose";</script>',
    '&lt;style&gt;.header { display: none; }&lt;/style&gt;',
])
def test_clean_text_keeps_prose_on_both_sides_of_hidden_markup(markup):
    raw = '<h2>Education</h2><p>Advanced degree required.</p>' + markup + '<h2>Languages</h2><p>English required.</p>'
    assert clean_text(raw) == 'Education Advanced degree required. Languages English required.'


def test_build_job_keeps_source_markup_in_raw_but_excludes_css_from_description():
    source = OrganizationSource(id='un_inspira', name='UN', ats_family='inspira', base_url='https://careers.un.org')
    raw = '<div>Responsibilities: Oversight and reporting.</div><style>.headtable { color: black; }</style><div>Education: Advanced degree.</div>'
    job = build_job(source, external_id='123', title='Officer', apply_url='/job/123',
                    description=raw, raw={'jobId':'123','jobDescription':raw})
    assert job.description == 'Responsibilities: Oversight and reporting. Education: Advanced degree.'
    assert job.raw['jobDescription'] == raw


def test_plain_prose_mentioning_script_and_style_is_retained():
    text = 'Write a Python script and apply the editorial style guide. Five years of experience.'
    assert clean_text(text) == text


def test_escaped_angle_brackets_do_not_erase_public_responsibilities_or_requirements():
    raw = '<p>&lt;Anchored in the 2030 Agenda, support development.</p><p>&lt; a) Enrollment in a bachelor’s programme is required</p><p>&lt;Knowledge of Adobe Premiere Pro.</p>'
    expected = '<Anchored in the 2030 Agenda, support development. < a) Enrollment in a bachelor’s programme is required <Knowledge of Adobe Premiere Pro.'
    assert clean_text(raw) == expected
    assert clean_text(expected) == expected


def test_real_html_tags_still_removed_with_attributes_and_comments():
    assert clean_text('<section data-name="role"><!-- hidden --><h2>Education</h2><p>A degree.</p></section>') == 'Education A degree.'
