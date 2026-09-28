"""Inline formatting must not introduce letters, punctuation or word gaps."""

import pytest

from jobagg.html_text import render_html_text
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job


@pytest.mark.parametrize(("markup", "expected"), [
    ("<p><strong>T</strong>he <em>role</em>.</p>", "The role."),
    ("<em>Job Descriptio</em>n <em>here</em>:", "Job Description here:"),
    ("<b>D</b>escriptio n here :", "Descriptio n here :"),
    ("Read <a href='/terms'>the terms</a>, then apply.", "Read the terms, then apply."),
    ("Read <a href='/terms'>the terms</a> , then apply .", "Read the terms , then apply ."),
    ("<b>one</b><i>two</i>", "onetwo"),
    ("<b>one</b> <i>two</i>", "one two"),
    ("<p>one</p><p>two</p>", "one two"),
    ("<h2>Requirements</h2><ul><li>Degree</li><li>Experience</li></ul>",
     "Requirements Degree Experience"),
    ("<div>A<br>B<hr>C</div><table><tr><td>D</td><td>E</td></tr></table>", "A B C D E"),
    ("co<wbr>operate and H<sub>2</sub>O", "cooperate and H2O"),
    ("<span>A<!-- editorial comment -->B</span>", "AB"),
    ("<p>Public<script>not public</script><style>CSS</style> text</p>", "Public text"),
    ("A<span hidden><b>secret</b></span>B<template>template</template>C", "ABC"),
    ("A<span style='display: none !important;'>secret</span>B", "AB"),
    ("<p>Visible <span aria-hidden='true'>visual label</span></p>", "Visible visual label"),
    ("<p>File<img alt='Download File' src='/icon.gif'>: <a href='/jd.pdf'>JD</a></p>", "File: JD"),
    ("<p>&lt;b&gt;literal&lt;/b&gt; &amp;amp; A&nbsp;B</p>", "<b>literal</b> &amp; A B"),
    ("<p>three (2) years; Cafe\u0301; &lt;Anchored in the 2030 Agenda&gt;</p>",
     "three (2) years; Cafe\u0301; <Anchored in the 2030 Agenda>"),
    (None, None),
    ("<p> </p>", None),
])
def test_html_fragment_preserves_inline_and_real_source_boundaries(markup, expected):
    assert render_html_text(markup) == expected


def test_build_job_does_not_reinterpret_already_rendered_literal_text():
    source = OrganizationSource(id="source", name="Source", ats_family="pageup", base_url="https://example.org")
    text = render_html_text("<p>&lt;b&gt;literal&lt;/b&gt; &amp;amp;</p>")
    job = build_job(source, title="Role", apply_url="/job/1", description=text,
                    description_is_plain_text=True)
    assert job.description == "<b>literal</b> &amp;"
    # Other adapters retain the existing default contract.
    assert build_job(source, title="Role", apply_url="/job/1",
                     description="<p>Full <b>body</b>.</p>").description == "Full body ."
