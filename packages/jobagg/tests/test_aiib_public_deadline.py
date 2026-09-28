from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.successfactors_rmk import (
    SuccessFactorsRMKAdapter,
    _aiib_detail_fields,
    _aiib_public_deadline,
)
from jobagg.models import OrganizationSource


RULE = "All opportunities close at 11:59 p.m. (GMT+8) on the dates listed."


def test_explicit_clock_overrides_date_only_card():
    parsed = _aiib_public_deadline(f"<p>{RULE}</p>", {"Closing Date *": "Sep 08, 2026"})
    assert parsed["closes_at"] == "2026-09-08T15:59:00+00:00"
    assert parsed["closes_at_local"] == "Sep 08, 2026 11:59 p.m."
    assert parsed["closes_tz"] == "GMT+8"
    assert parsed["public_clock_rule"] == RULE


def test_midnight_and_explicit_negative_offset():
    rule = RULE.replace("11:59 p.m.", "12:00 a.m.").replace("GMT+8", "GMT-4")
    assert _aiib_public_deadline(rule, {"Closing Date": "Sep 08, 2026"})["closes_at"] == "2026-09-08T04:00:00+00:00"


@pytest.mark.parametrize("rule", [
    RULE + RULE.replace("11:59", "10:59"),
    RULE.replace("GMT+8", "local time"),
    RULE.replace("11:59", "25:99"),
])
def test_conflicting_or_ambiguous_clocks_fail(rule):
    with pytest.raises(ValueError):
        _aiib_public_deadline(rule, {"Closing Date *": "Sep 08, 2026"})


def test_no_explicit_rule_does_not_invent_clock():
    assert _aiib_public_deadline("Date only notice", {"Closing Date *": "Sep 08, 2026"}) is None
    assert _aiib_public_deadline(f"<script>{RULE}</script>", {"Closing Date *": "Sep 08, 2026"}) is None


def test_conflicting_date_cards_fail():
    with pytest.raises(ValueError):
        _aiib_public_deadline(RULE, {"Closing Date *": "Sep 08, 2026", "Closing Date": "Sep 09, 2026"})
    html = "".join(f'<div class="item"><div class="col-title">Closing Date *</div><div class="col-con">Sep {day}, 2026</div></div>' for day in ["08", "09"])
    with pytest.raises(ValueError):
        _aiib_detail_fields(html)


def test_detail_record_preserves_full_text_and_explicit_zone():
    source = OrganizationSource(id="aiib_test", name="AIIB Test", ats_family="successfactors_rmk", base_url="https://www.aiib.org")
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source, None))
    html = '<div class="item"><div class="col-title">Closing Date *</div><div class="col-con">Sep 08, 2026</div></div>' + f"<p>{RULE}</p>"
    body = "Public duties, qualifications and application instructions remain unchanged."
    with patch.object(adapter, "fetch_text", return_value=html), patch.object(adapter, "ensure_allowed"), patch("jobagg.adapters.successfactors_rmk._aiib_detail_description", return_value=body):
        job = adapter._fetch_aiib_detail_for_listing_item({"external_id": "25285", "title": "Senior Officer", "detail_url": "https://www.aiib.org/vacancy.html"})
    assert job.closes_at == datetime(2026, 9, 8, 15, 59, tzinfo=timezone.utc)
    assert job.description == body
    assert job.closes_at_local == "Sep 08, 2026 11:59 p.m."
    assert job.closes_tz == "GMT+8"
    assert job.raw["_aiib_deadline_resolution"]["public_clock_rule"] == RULE
    assert job.raw["detail_html"] == html


def test_new_date_only_detail_drops_old_cached_clock_proof():
    source = OrganizationSource(id='aiib_test', name='AIIB', ats_family='successfactors_rmk', base_url='https://www.aiib.org')
    adapter = SuccessFactorsRMKAdapter(AdapterContext(source, None))
    html = '<div class="item"><div class="col-title">Closing Date *</div><div class="col-con">Sep 30, 2026</div></div>'
    old = {'closes_at': '2026-09-08T15:59:00+00:00', 'public_clock_rule': RULE}
    with patch.object(adapter, 'fetch_text', return_value=html), patch.object(adapter, 'ensure_allowed'), patch('jobagg.adapters.successfactors_rmk._aiib_detail_description', return_value='Public duties and requirements remain unchanged.'):
        job = adapter._fetch_aiib_detail_for_listing_item({'external_id': '25285', 'title': 'Senior Officer', 'detail_url': 'https://www.aiib.org/vacancy.html', '_aiib_deadline_resolution': old})
    assert job.closes_at.isoformat() == '2026-09-30T00:00:00+00:00'
    assert '_aiib_deadline_resolution' not in job.raw
    assert job.raw['detail_html'] == html
