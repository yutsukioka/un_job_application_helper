"""Only fully bound date headers may explain a shorter Taleo public body."""

from copy import deepcopy
from urllib.parse import urlencode

import pytest

from jobagg.pipelines.public_text_regression import taleo_date_metadata_only_change

_CASES = {
    "fao_taleo": (
        "jobs.fao.org", "Job Posting", "Closure Date", "Europe/Rome", "GMT+02:00",
        "11/Sep/2026", "11/Sep/2026", None,
        "25/Sep/2026, 11:59:00 PM", "25/Sep/2026, 9:59:00 PM",
        "2026-09-25T21:59:00+00:00", "2026-09-25T21:59:00+00:00",
    ),
    "wipo_taleo": (
        "wipo.taleo.net",
        "Publication Date",
        "Application Deadline",
        "Europe/Zurich",
        "GMT+02:00",
        "24-Aug-2026",
        "24-Aug-2026",
        None,
        "15-Sep-2026, 11:59:00 PM",
        "15-Sep-2026, 9:59:00 PM",
        "2026-09-15T21:59:00+00:00",
        "2026-09-15T21:59:00+00:00",
    ),
    "who_taleo": (
        "careers.who.int",
        "Job Posting",
        "Closing Date",
        "Europe/Zurich",
        "GMT+02:00",
        "Sep 13, 2026, 9:17:35 AM",
        "Sep 13, 2026, 7:17:35 AM",
        "2026-09-13T07:17:35+00:00",
        "Sep 20, 2026, 11:59:00 PM",
        "Sep 20, 2026, 9:59:00 PM",
        "2026-09-20T21:59:00+00:00",
        "2026-09-20T21:59:00+00:00",
    ),
    "iaea_taleo": (
        "iaea.taleo.net",
        "Job Posting",
        "Closing Date",
        "Europe/Vienna",
        "GMT+02:00",
        "2026-09-11, 8:31:39 AM",
        "2026-09-11, 6:31:39 AM",
        "2026-09-11T06:31:39+00:00",
        "2026-09-25, 11:59:00 PM",
        "2026-09-25, 9:59:00 PM",
        "2026-09-25T21:59:00+00:00",
        "2026-09-25T21:59:00+00:00",
    ),
    "adb_taleo": (
        "adb.taleo.net",
        "Job Posting",
        "Closing Date (Period for Applying) - Internal",
        "Asia/Manila",
        "GMT+08:00",
        "17-Aug-2026, 11:27:53 AM",
        "17-Aug-2026, 3:27:53 AM",
        "2026-08-17T03:27:53+00:00",
        "16-Sep-2026, 11:59:00 PM",
        "09-Oct-2026, 3:59:00 PM",
        "2026-09-16T15:59:00+00:00",
        "2026-10-09T15:59:00+00:00",
    ),
}


def rows(source="wipo_taleo"):
    (
        host,
        posted_label,
        closing_label,
        old_zone,
        old_offset,
        old_posted,
        new_posted,
        posted_instant,
        old_close,
        new_close,
        old_close_instant,
        new_close_instant,
    ) = _CASES[source]
    result = []
    for zone, offset, posted, closing, closing_instant in (
        (old_zone, old_offset, old_posted, old_close, old_close_instant),
        ("Etc/UTC", "GMT+00:00", new_posted, new_close, new_close_instant),
    ):
        url = (
            "https://"
            + host
            + "/careersection/ex/jobdetail.ftl?"
            + urlencode({"job": "vacancy-001", "tz": offset, "tzname": zone})
        )
        posting = {
            "kind": "known_instant" if posted_instant else "public_calendar_date_only",
            "public_value": posted,
            "tzname": zone,
            "url": url,
        }
        close = {"kind": "known_instant", "public_value": closing, "tzname": zone, "url": url}
        raw = {
            "_taleo_posting_time_resolution": posting,
            "_taleo_deadline_resolution": close,
            "_taleo_deadline_timezone_evidence": deepcopy(close),
            "_taleo_flat": {
                posted_label: posted,
                closing_label: closing,
                "_taleo_public_bindings": {
                    "kind": "paired_public_dom_bindings",
                    "visible_fields": [
                        {
                            "target": "reqPostingDate",
                            "semantic": "reqlistitem.postingdate",
                            "public_label": posted_label,
                            "public_text": posted,
                        },
                        {
                            "target": "reqUnpostingDate",
                            "semantic": "reqlistitem.unpostingdate",
                            "public_label": closing_label,
                            "public_text": closing,
                        },
                        {
                            "target": "description",
                            "semantic": "reqlistitem.description",
                            "public_label": "Responsibilities",
                            "public_text": "Manage 20 projects. Keep all qualifications and duties.",
                        },
                    ],
                },
            },
        }
        result.append(
            {
                "source_id": source,
                "external_id": "vacancy-001",
                "ats_family": "taleo",
                "title": "Programme officer",
                "location": "Geneva",
                "department": "Programmes",
                "employment_type": "Fixed term",
                "source_url": url,
                "apply_url": url,
                "posted_at": posted_instant,
                "closes_at": closing_instant,
                "closes_at_local": closing,
                "closes_tz": zone,
                "raw": raw,
                "description": "Programme officer\n\n"
                + posted_label
                + "\n"
                + posted
                + "\n\n"
                + closing_label
                + "\n"
                + closing
                + "\n\nResponsibilities\nManage 20 projects. Keep all qualifications and duties.",
            }
        )
    return result


@pytest.mark.parametrize("source", list(_CASES))
def test_actual_source_date_formats_allow_only_complete_typed_metadata_change(source):
    old, new = rows(source)
    result = taleo_date_metadata_only_change(old, new)
    assert result["accepted"] is True
    assert result["method"] == "taleo_bound_date_headers_v1"
    assert result["completeness_certified"] is False
    assert len(result["non_date_text_sha256"]) == 64
    if source == "adb_taleo":
        closing = next(item for item in result["date_changes"] if item["field"] == "closes_at")
        assert closing["before"]["normalized_value"] == "2026-09-16T15:59:00+00:00"
        assert closing["incoming"]["normalized_value"] == "2026-10-09T15:59:00+00:00"


def test_unchanged_whitespace_nfc_is_allowed_but_no_other_prose_change():
    old, new = rows()
    new["description"] = new["description"].replace("\n\n", "\n\n\n")
    assert taleo_date_metadata_only_change(old, new)["accepted"]
    new["description"] = new["description"].replace("20 projects", "2 projects")
    assert not taleo_date_metadata_only_change(old, new)["accepted"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_prose",
        "missing_heading",
        "changed_number",
        "missing_nondate_binding",
        "changed_nondatetime_scalar",
        "missing_timezone_proof",
        "wrong_normalized_instant",
        "wrong_timezone",
        "wrong_url_job",
        "wrong_apply_url",
        "unknown_header",
        "duplicate_header",
        "missing_bound_header",
        "public_value_mismatch",
        "unsupported_source",
        "wrong_source_identity",
        "missing_dom_binding",
        "unbound_date_in_prose",
        "calendar_date_fabricated_instant",
    ],
)
def test_rejects_information_loss_or_unbound_metadata(mutation):
    old, new = rows()
    binding = new["raw"]["_taleo_flat"]["_taleo_public_bindings"]
    if mutation == "missing_prose":
        new["description"] = new["description"].replace(" Keep all qualifications and duties.", "")
    elif mutation == "missing_heading":
        new["description"] = new["description"].replace("Responsibilities\n", "")
    elif mutation == "changed_number":
        new["description"] = new["description"].replace("20 projects", "2 projects")
    elif mutation == "missing_nondate_binding":
        binding["visible_fields"].pop()
    elif mutation == "changed_nondatetime_scalar":
        new["location"] = None
    elif mutation == "missing_timezone_proof":
        del new["raw"]["_taleo_deadline_timezone_evidence"]
    elif mutation == "wrong_normalized_instant":
        new["closes_at"] = "2026-09-15T19:59:00+00:00"
    elif mutation == "wrong_timezone":
        new["closes_tz"] = "Europe/Zurich"
    elif mutation == "wrong_url_job":
        for key in ("source_url", "apply_url"):
            new[key] = new[key].replace("vacancy-001", "another-vacancy")
        for key in (
            "_taleo_posting_time_resolution",
            "_taleo_deadline_resolution",
            "_taleo_deadline_timezone_evidence",
        ):
            new["raw"][key]["url"] = new["source_url"]
    elif mutation == "wrong_apply_url":
        new["apply_url"] = "https://wrong.example/job"
    elif mutation == "unknown_header":
        binding["visible_fields"][1]["public_label"] = "Other Date"
    elif mutation == "duplicate_header":
        new["description"] += "\n\nApplication Deadline\n" + new["closes_at_local"]
    elif mutation == "missing_bound_header":
        new["description"] = new["description"].replace("Application Deadline\n", "")
    elif mutation == "public_value_mismatch":
        binding["visible_fields"][1]["public_text"] = "16-Sep-2026, 9:59:00 PM"
    elif mutation == "unsupported_source":
        old["source_id"] = new["source_id"] = "undp_oracle_hcm"
    elif mutation == "wrong_source_identity":
        new["external_id"] = "different-vacancy"
    elif mutation == "missing_dom_binding":
        del new["raw"]["_taleo_flat"]["_taleo_public_bindings"]
    elif mutation == "unbound_date_in_prose":
        old["description"] += "\nAttend by 15-Sep-2026, 11:59:00 PM."
        new["description"] += "\nAttend by 15-Sep-2026, 9:59:00 PM."
    elif mutation == "calendar_date_fabricated_instant":
        new["posted_at"] = "2026-08-24T00:00:00+00:00"
    assert taleo_date_metadata_only_change(old, new)["accepted"] is False


def test_fao_historical_english_url_qualification_is_narrow():
    old, new = rows('fao_taleo')
    old['source_url'] += '&lang=en'
    old['apply_url'] += '&lang=en'
    assert taleo_date_metadata_only_change(old, new)['accepted']
    wrong = deepcopy(old)
    wrong['source_url'] = wrong['source_url'].replace('lang=en', 'lang=fr')
    assert not taleo_date_metadata_only_change(wrong, new)['accepted']
    wrong = deepcopy(new)
    wrong['description'] = wrong['description'].replace('Responsibilities', 'Removed section')
    assert not taleo_date_metadata_only_change(old, wrong)['accepted']
