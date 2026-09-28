"""Provider regressions, including optional immutable September audit evidence."""

import gzip
import hashlib
import json
import sqlite3
import unicodedata
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.oracle_hcm import OracleHCMAdapter
from jobagg.adapters.pageup import PageUpAdapter
from jobagg.db import JobDatabase
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job


def adapter(kind, source_id):
    source = OrganizationSource(id=source_id, name=source_id, ats_family=kind,
                                base_url="https://example.org")
    cls = OracleHCMAdapter if kind == "oracle_hcm" else PageUpAdapter
    return cls(AdapterContext(source=source, http=JobAggHTTPClient()))


def fingerprint(text):
    normalized = " ".join(unicodedata.normalize("NFC", text).split())
    return len(normalized), hashlib.sha256(normalized.encode()).hexdigest()


def test_oracle_inline_initial_does_not_split_word_or_duplicate_teaser():
    raw = {"Id": "123", "Title": "Consultant", "ShortDescriptionStr": "The primary objective",
           "ExternalDescriptionStr": "<p><strong>T</strong>he primary objective</p><p>Public &lt;b&gt;literal&lt;/b&gt;.</p>",
           "ExternalResponsibilitiesStr": "<p>Deliver <b>work</b>.</p>",
           "ExternalQualificationsStr": "<ul><li>Degree</li><li>three (2) years</li></ul>"}
    job = adapter("oracle_hcm", "unfpa_oracle_hcm").parse_jobs({"items": [raw]})[0]
    assert job.description == "The primary objective Public <b>literal</b>. Deliver work. Degree three (2) years"
    assert job.raw["ExternalDescriptionStr"] == raw["ExternalDescriptionStr"]


def test_pageup_inline_description_keeps_source_typo_and_scope():
    markup = """<h2>Role</h2><div id="job-details"><p><b>D</b>escriptio n here :</p>
    <p><em>Job Descriptio</em>n <em>here</em>: <a href="/jd.pdf">JD</a>.</p>
    <p>A</p><p>B</p></div><p><b>Advertised:</b> 1 September 2026</p>"""
    job = adapter("pageup", "unicef_pageup").parse_detail_html(markup, "https://example.org/job/123/role")
    assert job.description == "Descriptio n here : Job Description here: JD. A B"
    assert "Advertised" not in job.description
    assert job.raw["detail_html"] == markup


@pytest.mark.parametrize("kind", ["oracle_hcm", "pageup"])
def test_later_listing_refresh_preserves_repaired_public_inline_text(tmp_path, kind):
    instance = adapter(kind, f"test_{kind}")
    markup = "<p><strong>T</strong>he public <em>role</em>.</p>" * 30
    if kind == "oracle_hcm":
        full = instance.parse_jobs({"items": [{"Id": "123", "Title": "Role",
                "ShortDescriptionStr": "The public role.", "ExternalDescriptionStr": markup}]})[0]
        listing_raw = {"ShortDescriptionStr": "The public role."}
    else:
        full = instance.parse_detail_html('<h2>Role</h2><div id="job-details">' + markup
            + '</div><p><b>Advertised:</b> 1 September 2026</p>', "https://example.org/job/123/role")
        listing_raw = {"listing_html": "<p>The public role.</p>"}
    database = JobDatabase(tmp_path / "test.sqlite3")
    database.initialize()
    database.upsert_job(full)
    database.upsert_job(build_job(instance.source, title="Role", external_id="123",
        apply_url=full.apply_url, description="The public role.", raw=listing_raw))
    assert database.get_job(full.identity_key())["description"] == full.description
    assert "T he" not in full.description and "role ." not in full.description


AUDIT = Path(__file__).resolve().parents[3] / "private/jobagg/complete_fetch_20260913"


def test_actual_unfpa_36745_natural_inline_fingerprint():
    body = AUDIT / "sources/unfpa_oracle_hcm/full-001/http/00023.body.gz"
    if not body.exists():
        pytest.skip("Private original audit capture is not installed")
    data = gzip.decompress(body.read_bytes())
    assert hashlib.sha256(data).hexdigest() == "c9bdb3962d422a8efcb59acfd8262ad6a6e1f84e450cf64fc35ad4c54325a2ec"
    job = adapter("oracle_hcm", "unfpa_oracle_hcm").parse_jobs(json.loads(data), public_detail_observed=True)[0]
    assert job.external_id == "36745"
    assert fingerprint(job.description) == (12190, "4877b6cde5cfda2a1e11d60841d6a17246067198dabed1ff77db3f983f76c020")
    assert "T he" not in job.description


@pytest.mark.parametrize(("rank", "external_id"), [
    (1, "595547"), (23, "595402"), (25, "595477"), (46, "595650"),
    (47, "595336"), (48, "595338"), (52, "595527"),
])
def test_actual_unicef_entire_description_matches_saved_browser(rank, external_id):
    review_path = AUDIT / f"evidence/content_verification/random_manual_reviews/{rank:04d}_unicef_{external_id}.json"
    db = AUDIT / "output/unicef_jobs.sqlite3"
    if not review_path.exists() or not db.exists():
        pytest.skip("Private browser review and staged capture are not installed")
    review = json.loads(review_path.read_text())
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT raw_json FROM jobs WHERE source_id=? AND external_id=?",
                                 ("unicef_pageup", external_id)).fetchone()
    raw = json.loads(row[0])
    job = adapter("pageup", "unicef_pageup").parse_detail_html(raw["detail_html"], raw["_pageup_detail_url"])
    assert job.external_id == external_id
    assert fingerprint(job.description) == (review["browser_normalized_characters"], review["browser_normalized_sha256"])
