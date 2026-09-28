from jobagg.adapters.successfactors_rmk import _detail_description


def test_sequential_public_sections_preserve_short_eligibility_and_recruitment():
    body = "Substantive responsibilities and qualifications. " * 10
    html = f'<span itemprop="description">Grade: P5. Eligibility: external candidates.</span><span itemprop="description">{body}</span><span itemprop="description">Recruitment process: apply before midnight.</span>'
    result = _detail_description(html)
    assert 'Eligibility: external candidates.' in result
    assert body.strip() in result
    assert 'Recruitment process: apply before midnight.' in result


def test_nested_or_repeated_description_is_not_duplicated():
    body = 'Substantive responsibilities and qualifications. ' * 10
    html = f'<div class="jobdescription"><span itemprop="description">{body}</span></div>'
    assert _detail_description(html).count('Substantive responsibilities') == 10


def test_metadata_only_still_does_not_pass_as_full_description():
    assert _detail_description('<span itemprop="description">Grade: P5</span>') is None
