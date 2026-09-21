from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.smartrecruiters import SmartRecruitersAdapter, variant_manifest_hash
from jobagg.models import OrganizationSource
from jobagg.pipelines.sync_source import _detail_queue_reason, _listing_hash


def row(id, language="en", ref="REF1"):
    return {
        "id": id,
        "refNumber": ref,
        "name": "Research role",
        "language": {"code": language, "label": language},
        "ref": f"https://example.org/{id}",
    }


def detail(item, text):
    return dict(
        item,
        jobAd={
            "sections": {
                "description": {"text": f"<p>{text}</p>"},
                "qualification": {"text": "Required qualifications."},
            }
        },
    )


def adapter(pages=3):
    return SmartRecruitersAdapter(
        AdapterContext(
            source=OrganizationSource(
                id="oecd_smartrecruiters",
                name="OECD",
                ats_family="smartrecruiters",
                base_url="https://example.org",
                extra={"company": "OECD", "page_size": 2, "max_pages": pages},
            ),
            http=SimpleNamespace(),
        )
    )


def test_public_posting_inventory_preserves_language_variants_and_proves_posting_total():
    a = adapter()
    first = row("1")
    second = row("2", "fr")
    third = row("3", ref="REF2")
    a.fetch_json = lambda u: (
        {"totalFound": 3, "content": [third]}
        if "offset=2" in u
        else {"totalFound": 3, "content": [first, second]}
    )
    jobs = a.fetch_jobs()
    assert len(jobs) == 2
    assert jobs[0].raw["_smartrecruiters_listing_variants"] == [first, second]
    assert a.run_diagnostics.total_reported_by_source == 3
    assert a.run_diagnostics.pagination_complete is True
    assert a.run_diagnostics.pages_fetched == 2


def test_every_variant_full_text_and_raw_fields_survive_canonical_merge():
    a = adapter()
    en = row("1")
    fr = row("2", "fr")
    listing = dict(en, _smartrecruiters_listing_variants=[en, fr])
    en_detail = detail(en, "English substantive responsibilities.")
    fr_detail = detail(
        fr,
        'French substantive responsibilities. <a href="https://example.org/tor.pdf">Terms of Reference</a>',
    )
    a.fetch_json = lambda u: {
        "https://example.org/1": en_detail,
        "https://example.org/2": fr_detail,
    }[u]
    job = a.fetch_detail_for_listing_item(listing)
    assert job.external_id == "REF1"
    assert "English substantive" in job.description and "French substantive" in job.description
    assert job.raw["_smartrecruiters_public_variants"] == [en_detail, fr_detail]
    assert "tor.pdf" in str(job.raw["_smartrecruiters_public_variants"])
    assert job.raw["_smartrecruiters_variant_manifest_sha256"] == variant_manifest_hash([en, fr])


@pytest.mark.parametrize("bad", ["posting_id", "canonical_id", "empty"])
def test_missing_or_wrong_variant_fails_whole_canonical_detail(bad):
    a = adapter()
    en = row("1")
    fr = row("2", "fr")
    listing = dict(en, _smartrecruiters_listing_variants=[en, fr])
    broken = detail(fr, "French responsibilities")
    if bad == "posting_id":
        broken["id"] = "other"
    elif bad == "canonical_id":
        broken["refNumber"] = "OTHER"
    else:
        broken["jobAd"] = {}
    a.fetch_json = lambda u: detail(en, "English responsibilities") if u.endswith("/1") else broken
    with pytest.raises(ValueError):
        a.fetch_detail_for_listing_item(listing)


def test_repeated_public_page_cannot_certify_inventory():
    a = adapter()
    a.fetch_json = lambda u: {"totalFound": 3, "content": [row("1"), row("2", "fr")]}
    assert len(a.fetch_jobs()) == 1
    assert a.run_diagnostics.pagination_complete is False


def test_cache_missing_language_variant_is_selectively_queued():
    a = adapter()
    en = row("1")
    fr = row("2", "fr")
    listing = a.parse_jobs({"content": [en]})[0]
    listing.raw = dict(en, _smartrecruiters_listing_variants=[en, fr])
    current = {
        "title": "Research role",
        "description": "Substantive work responsibilities. " * 30,
        "raw": detail(en, "English responsibilities"),
    }
    backlog = {"detail_status": "complete", "listing_hash_at_detail_fetch": _listing_hash(listing)}
    db = SimpleNamespace(get_detail_backlog=lambda _: backlog, get_job=lambda _: current)
    assert (
        _detail_queue_reason(
            db,
            a.source,
            listing,
            datetime.now(timezone.utc),
            listing_hash=_listing_hash(listing),
            refresh_all_details=False,
        )
        == "public_language_variants_missing_or_changed"
    )
