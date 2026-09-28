"""Public PageUp opening/closing date roles must survive template changes."""
import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.pageup import PageUpAdapter
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource


@pytest.fixture
def adapter():
    source = OrganizationSource(
        id="unicef_pageup", name="UNICEF", ats_family="pageup",
        base_url="https://jobs.unicef.org/en-us/listing/",
    )
    return PageUpAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))


def dates(opened, closed):
    return (
        f'<b>Advertised:</b> <span class="open-date"><time datetime="{opened}">'
        'Advertised date</time></span> Local timezone<br>'
        f'<b>Deadline:</b> <span class="close-date"><time datetime="{closed}">'
        'Deadline date</time></span> Local timezone'
    )


def parse(adapter, date_html, mode="detail"):
    if mode == "detail":
        return adapter.parse_detail_html(
            '<h2>Test vacancy</h2><p><b>Job no:</b>'
            '<span class="job-externalJobNo">592947</span></p>'
            '<div id="job-details"><p>Full substantive public description.</p></div>'
            f'<p>{date_html}</p>',
            "https://jobs.unicef.org/en-us/job/592947/test-vacancy",
        )
    return adapter.parse_listing_html(
        '<div class="list-view--item"><a class="job-link" '
        'href="/en-us/job/592947/test-vacancy">Test vacancy</a>'
        f'<div class="row--teaser"><p>{date_html}</p></div></div>'
    )[0]


@pytest.mark.parametrize("mode", ["listing", "detail"])
@pytest.mark.parametrize("opened,closed", [
    # Exact datetime values from retained public pages for592947 and589208.
    ("2026-09-03T09:00:00Z", "2026-09-17T23:55:00Z"),
    ("2026-01-01T08:00:00Z", "2026-12-31T22:55:00Z"),
])
def test_public_opening_never_becomes_deadline(adapter, mode, opened, closed):
    job = parse(adapter, dates(opened, closed), mode)
    assert job.posted_at.isoformat() == opened.replace("Z", "+00:00")
    assert job.closes_at.isoformat() == closed.replace("Z", "+00:00")
    assert job.posted_at < job.closes_at


@pytest.mark.parametrize("mode", ["listing", "detail"])
def test_reversed_date_order_and_unrelated_time_do_not_change_roles(adapter, mode):
    advertised, deadline = dates("2026-09-03T09:00:00Z", "2026-09-17T23:55:00Z").split("<br>")
    job = parse(adapter, '<time datetime="2000-01-01T00:00:00Z">Unrelated</time>'
                f'{deadline}<br>{advertised}', mode)
    assert job.posted_at.isoformat() == "2026-09-03T09:00:00+00:00"
    assert job.closes_at.isoformat() == "2026-09-17T23:55:00+00:00"


def test_advertised_only_does_not_invent_deadline(adapter):
    job = parse(adapter, '<b>Advertised:</b><span class="open-date">'
                '<time datetime="2026-09-03T09:00:00Z">3 Sep</time></span>')
    assert job.posted_at.isoformat() == "2026-09-03T09:00:00+00:00"
    assert job.closes_at is None


@pytest.mark.parametrize("fragment", [
    '<time datetime="2026-09-17T23:55:00Z">17 Sep</time>',
    '<span class="not-close-date"><time datetime="2026-09-17T23:55:00Z">17 Sep</time></span>',
    '<b>Deadline:</b><span class="open-date"><time datetime="2026-09-03T09:00:00Z">3 Sep</time></span>',
    '<b>Deadline:</b><span CLASS="open-date"><time datetime="2026-09-03T09:00:00Z">3 Sep</time></span>',
    '<b>Deadline:</b><time datetime="2026-09-17T23:55:00Z">17 Sep</time>'
    '<time datetime="2026-10-17T23:55:00Z">17 Oct</time>',
    '<b>Deadline:</b><time datetime="2026-09-17T23:55:00Z">17 Sep</time>'
    '<time>17 October 2026</time>',
    '<span class="close-date"><time datetime="2026-09-17T23:55:00Z">17 Sep</time></span>'
    '<span class="close-date"><time datetime="2026-10-17T23:55:00Z">17 Oct</time></span>',
    '<b>Deadline:</b>2026-09-17<br><b>Deadline:</b>2026-10-17',
    '<b>Deadline:</b>2026-10-17<br><span class="close-date">2026-09-17</span>',
])
def test_absent_unlabeled_or_ambiguous_deadline_is_none(adapter, fragment):
    assert parse(adapter, fragment).closes_at is None


@pytest.mark.parametrize("fragment", [
    '<b>Advertised:</b>2026-09-03<br><b>Advertised:</b>2026-09-04',
    '<span class="open-date">2026-09-03</span><span class="open-date">2026-09-04</span>',
    '<b>Advertised:</b><span class="close-date">2026-09-17</span>',
])
def test_ambiguous_advertised_is_none(adapter, fragment):
    assert parse(adapter, fragment).posted_at is None


def test_explicit_labels_support_single_quotes_and_bounded_plain_dates(adapter):
    job = parse(adapter, "<strong>Advertised:</strong>2026-09-03<br/>"
                "<strong>Deadline:</strong><time datetime='2026-09-17T23:55:00Z'>17 Sep</time>")
    assert job.posted_at.isoformat() == "2026-09-03T00:00:00+00:00"
    assert job.closes_at.isoformat() == "2026-09-17T23:55:00+00:00"


def test_exact_class_token_supports_additional_classes_and_single_quotes(adapter):
    job = parse(adapter, "<span class='metadata open-date'><time datetime='2026-09-03T09:00:00Z'>"
                "3 Sep</time></span><span class='close-date metadata'>"
                "<time datetime='2026-09-17T23:55:00Z'>17 Sep</time></span>")
    assert job.posted_at.isoformat() == "2026-09-03T09:00:00+00:00"
    assert job.closes_at.isoformat() == "2026-09-17T23:55:00+00:00"
