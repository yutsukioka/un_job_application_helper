"""Taleo list observations cannot overwrite structured detail date semantics."""
import copy

import pytest

from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job


BODY = "Responsibilities and qualifications for the public job. " * 20
URL = "https://example.org/jobdetail.ftl?job=123&tzname=Europe%2FRome"


def record(raw, *, closes="2026-12-31T22:59:00Z", posted="2026-08-27T02:00:00Z", body=BODY):
    source = OrganizationSource("example", "Example", "taleo", "https://example.org")
    return build_job(source, title="Public job", external_id="123", apply_url=URL,
                     description=body, posted_at=posted, closes_at=closes, raw=copy.deepcopy(raw))


def detailed_raw(kind="known_instant"):
    return {
        "detail_url": URL, "_taleo_detail_url": URL, "_taleo_record_kind": "detail",
        "_taleo_flat": {"_taleo_parser": "requisitionDescriptionInterface.fillList",
                        "Job Number": "123", "Requisition Title": "Public job",
                        "Job Posting": "27/Aug/2026, 4:00:00 AM", "Closing Date": "31/Dec/2026, 11:59:00 PM"},
        "_taleo_deadline_resolution": {"kind": kind, "url": URL,
                                       "public_value": "31/Dec/2026, 11:59:00 PM", "tzname": "Europe/Rome"},
    }


def listing_raw():
    return {"_taleo_record_kind": "listing", "_taleo_detail_url": URL,
            "_taleo_flat": {"Job Number": "123", "Requisition Title": "Listing title",
                            "Job Posting": "2026-08-27", "Closing Date": "2026-06-30"}}


@pytest.fixture
def db(tmp_path):
    result = JobDatabase(tmp_path / "jobs.sqlite3")
    result.initialize()
    return result


def test_listing_preserves_full_flat_dates_and_markers_and_records_its_values_separately(db):
    original_raw = detailed_raw()
    original_raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    original = record(original_raw)
    original.closes_at_local = "31/Dec/2026, 11:59:00 PM"
    original.closes_tz = "Europe/Rome"
    db.upsert_job(original)
    listing = record(listing_raw(), closes="2026-06-30", posted="2026-08-27", body="Short listing")
    db.upsert_job(listing)
    stored = db.get_job(original.identity_key())
    assert stored["posted_at"] == "2026-08-27T02:00:00+00:00"
    assert stored["closes_at"] == "2026-12-31T22:59:00+00:00"
    assert stored["closes_tz"] == "Europe/Rome"
    assert stored["closes_at_local"] == "31/Dec/2026, 11:59:00 PM"
    assert stored["description"] == BODY.strip()
    assert stored["raw"]["_taleo_flat"] == original_raw["_taleo_flat"]
    assert stored["raw"]["_taleo_record_kind"] == "detail"
    assert stored["raw"]["_taleo_deadline_resolution"] == original_raw["_taleo_deadline_resolution"]
    observed = stored["raw"]["_jobagg_listing_date_observation"]
    assert observed["raw_taleo_flat"] == listing_raw()["_taleo_flat"]
    assert observed["closes_at"] == "2026-06-30T00:00:00+00:00"
    assert stored["raw"]["attachment_verification"]["complete"] is False
    assert stored["raw"]["attachment_verification"]["discovery_complete"] is False


@pytest.mark.parametrize("kind,local,tz", [
    ("open_ended", None, None),
    ("unknown_timezone", "31/Dec/2026, 11:59:00 PM", None),
    ("unparsed", "not parseable", "Europe/Rome"),
])
def test_explicit_unresolved_or_open_deadline_clears_previous_instant_and_stays_clear(db, kind, local, tz):
    first = record(detailed_raw())
    first.closes_at_local = "old wall clock"
    first.closes_tz = "Europe/Rome"
    db.upsert_job(first)
    changed_raw = detailed_raw(kind)
    changed_raw["_taleo_flat"].pop("Closing Date")
    changed_raw["_taleo_deadline_resolution"].update(public_value=local, tzname=tz)
    changed = record(changed_raw, closes=None)
    changed.closes_at_local = local
    changed.closes_tz = tz
    db.upsert_job(changed)
    for _ in range(2):
        db.upsert_job(record(listing_raw(), closes="2026-06-30", posted="2026-08-27", body="Listing"))
    stored = db.get_job(first.identity_key())
    assert stored["closes_at"] is None
    assert stored["closes_at_local"] == local
    assert stored["closes_tz"] == tz
    assert "Closing Date" not in stored["raw"]["_taleo_flat"]
    assert stored["raw"]["_taleo_deadline_resolution"]["kind"] == kind


def test_fresh_known_detail_can_replace_open_ended_state_and_raw_flat(db):
    first_raw = detailed_raw("open_ended")
    first_raw["_taleo_flat"]["obsolete"] = "must disappear"
    db.upsert_job(record(first_raw, closes=None))
    fresh_raw = detailed_raw()
    fresh_raw["_taleo_flat"]["Closing Date"] = "15/Jan/2027, 11:59:00 PM"
    fresh_raw["_taleo_deadline_resolution"]["public_value"] = fresh_raw["_taleo_flat"]["Closing Date"]
    fresh = record(fresh_raw, closes="2027-01-15T22:59:00Z")
    fresh.closes_at_local = fresh_raw["_taleo_flat"]["Closing Date"]
    fresh.closes_tz = "Europe/Rome"
    db.upsert_job(fresh)
    stored = db.get_job(fresh.identity_key())
    assert stored["closes_at"] == "2027-01-15T22:59:00+00:00"
    assert stored["raw"]["_taleo_flat"] == fresh_raw["_taleo_flat"]
    assert stored["raw"]["_taleo_deadline_resolution"]["kind"] == "known_instant"


@pytest.mark.parametrize("mutation,recognized", [
    ({}, True),
    ({"detail_url": None}, False),
    ({"_taleo_flat": {"Closing Date": "date", "long_text": BODY}}, False),
    ({"_taleo_flat": {"_taleo_parser": "requisitionDescriptionInterface.fillList",
                      "Job Number": "different", "Requisition Title": "Public job"}}, False),
    ({"_taleo_record_kind": "listing"}, False),
])
def test_legacy_detail_requires_exact_structured_parser_identity(db, mutation, recognized):
    raw = detailed_raw()
    raw.pop("_taleo_record_kind")
    raw.pop("_taleo_deadline_resolution")
    raw.update(mutation)
    db.upsert_job(record(raw))
    db.upsert_job(record(listing_raw(), closes="2026-06-30", posted="2026-08-27", body="Listing"))
    stored = db.get_job("example:123")
    expected = "2026-12-31T22:59:00+00:00" if recognized else "2026-06-30T00:00:00+00:00"
    assert stored["closes_at"] == expected


def test_plain_null_detail_without_explicit_resolution_keeps_legacy_fallback(db):
    db.upsert_job(record(detailed_raw()))
    raw = detailed_raw()
    raw.pop("_taleo_deadline_resolution")
    db.upsert_job(record(raw, closes=None))
    assert db.get_job("example:123")["closes_at"] == "2026-12-31T22:59:00+00:00"


def test_taleo_resolution_change_invalidates_certificate_with_unchanged_body(db):
    raw = detailed_raw()
    raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    db.upsert_job(record(raw))
    unresolved = detailed_raw("unknown_timezone")
    unresolved["_taleo_deadline_resolution"]["tzname"] = None
    changed = record(unresolved, closes=None)
    changed.closes_at_local = "31/Dec/2026, 11:59:00 PM"
    db.upsert_job(changed)
    stored = db.get_job("example:123")
    assert stored["description"] == BODY.strip()
    assert stored["closes_at"] is None
    assert stored["raw"]["attachment_verification"]["complete"] is False
    assert stored["raw"]["attachment_verification"]["discovery_complete"] is False


def test_identical_taleo_listing_after_document_rebind_keeps_certificate(db):
    db.upsert_job(record(detailed_raw()))
    db.upsert_job(record(listing_raw(), closes="2026-06-30", posted="2026-08-27", body="Listing"))
    current = db.get_job("example:123")
    verified = {"complete": True, "discovery_complete": True}
    rebound = record({**current["raw"], "attachment_verification": verified})
    db.upsert_job(rebound)
    db.upsert_job(record(listing_raw(), closes="2026-06-30", posted="2026-08-27", body="Listing"))
    stored = db.get_job("example:123")
    assert stored["raw"]["attachment_verification"] == verified
    assert stored["raw"]["_taleo_flat"] == detailed_raw()["_taleo_flat"]


@pytest.mark.parametrize("before_kind,after_kind", [
    ("open_ended", "known_instant"),
    ("known_instant", "open_ended"),
    ("known_instant", "unknown_timezone"),
    ("open_ended", "unparsed"),
])
def test_fresh_resolution_drops_obsolete_mutually_exclusive_raw_markers(db, before_kind, after_kind):
    marker_by_kind = {
        "known_instant": "_taleo_deadline_timezone_evidence",
        "open_ended": "_taleo_deadline_open_ended",
    }
    before = detailed_raw(before_kind)
    before[marker_by_kind[before_kind]] = dict(before["_taleo_deadline_resolution"])
    db.upsert_job(record(before, closes=None if before_kind == "open_ended" else "2026-12-31T22:59:00Z"))
    fresh = detailed_raw(after_kind)
    if after_kind in marker_by_kind:
        fresh[marker_by_kind[after_kind]] = dict(fresh["_taleo_deadline_resolution"])
    db.upsert_job(record(fresh, closes="2026-12-31T22:59:00Z" if after_kind == "known_instant" else None))
    for _ in range(2):
        db.upsert_job(record(listing_raw(), body="Listing"))
    stored = db.get_job("example:123")["raw"]
    assert stored["_taleo_deadline_resolution"] == fresh["_taleo_deadline_resolution"]
    for marker in marker_by_kind.values():
        assert stored.get(marker) == fresh.get(marker)
        assert (marker in stored) == (marker in fresh)


def test_ordinary_listing_updates_separate_observation_while_retaining_detail_dates(db):
    db.upsert_job(record(detailed_raw()))
    db.upsert_job(record(listing_raw(), closes="2026-06-30", body="Listing"))
    changed_listing = listing_raw()
    changed_listing["_taleo_flat"]["Closing Date"] = "2026-07-31"
    db.upsert_job(record(changed_listing, closes="2026-07-31", body="Listing"))
    stored = db.get_job("example:123")
    assert stored["closes_at"] == "2026-12-31T22:59:00+00:00"
    assert stored["raw"]["_jobagg_listing_date_observation"]["closes_at"] == "2026-07-31T00:00:00+00:00"
    assert stored["raw"]["_jobagg_listing_date_observation"]["raw_taleo_flat"] == changed_listing["_taleo_flat"]
