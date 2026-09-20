"""Public notice URLs must remain dereferenceable after normalization."""

import pytest

from jobagg.normalize import canonical_url


@pytest.mark.parametrize("query", [
    "557339",
    "449096",
    "token=a%2Fb%2bc%20d&token=a+b&empty=&bare",
    "sig=a%2fb%3d%3d&encoded=%26%3D&x=1&&x=2",
])
def test_opaque_query_components_preserve_exact_spelling_order_and_bare_form(query):
    url = "https://erecruitment.eulisa.europa.eu/assets/offers/notice.pdf?" + query
    assert canonical_url(url) == url


def test_tracking_keys_removed_without_rewriting_remaining_public_query():
    assert canonical_url(
        "/notice.pdf?557339&utm_source=board&sig=a%2fb%20c&%75TM_medium=x&bare#download",
        "https://AGENCY.EXAMPLE/jobs/",
    ) == "https://agency.example/notice.pdf?557339&sig=a%2fb%20c&bare"
