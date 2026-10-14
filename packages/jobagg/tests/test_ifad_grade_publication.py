"""Provider blank-grade revision cannot silently bypass the shorter-text guard."""

from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.peoplesoft import PeopleSoftAdapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource
from test_live_publication import setup as publication_setup, run, write, sha
from test_peoplesoft_details import detail


@pytest.fixture
def setup(tmp_path):
    return publication_setup.__wrapped__(tmp_path)


def test_ifad_blank_grade_is_provider_revision_and_both_destinations_retain_prior_text(setup):
    source = OrganizationSource(
        "ifad_peoplesoft", "IFAD", "peoplesoft", "https://job.ifad.org/search"
    )
    adapter = PeopleSoftAdapter(AdapterContext(source, None))
    old_html = (
        detail("37851")
        .replace("Final requirements.", "Qualifications and experience. " * 50)
        .replace(
            "</span>", "</span><span class='ps_box-value' id='IFA_HRS_SCH_WRK_DESCR'>P-1</span>", 1
        )
    )
    new_html = old_html.replace(
        "id='IFA_HRS_SCH_WRK_DESCR'>P-1", "id='IFA_HRS_SCH_WRK_DESCR'>&nbsp;"
    )
    old, new = [
        adapter.parse_detail_html(html, item={"job_id": "37851"}, detail_url=source.base_url)
        for html in (old_html, new_html)
    ]
    assert old.raw["grade"] == "P-1" and not new.raw["grade"]
    assert old.raw["detail_sections"] == new.raw["detail_sections"]
    assert "Grade: P-1" in old.description and "Grade:" not in new.description
    old.first_seen_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    setup["registry"].write_text(
        "sources:\n- id: ifad_peoplesoft\n  name: IFAD\n  ats_family: peoplesoft\n  base_url: https://job.ifad.org/search\n  enabled: true\n  extra:\n    output_slug: ifad\n    fetch_attachments: false\n"
    )
    for path in (setup["output"] / "ifad_jobs.sqlite3", setup["output"] / "all_jobs.sqlite3"):
        db = JobDatabase(path)
        db.initialize()
        db.upsert_job(old)
    capture = setup["root"] / "ifad" / "http" / "1.json"
    blob = capture.with_suffix(".gz")
    blob.parent.mkdir(parents=True)
    blob.write_bytes(gzip.compress(new_html.encode()))
    write(
        capture,
        {
            "phase": {"kind": "detail", "job_id": new.external_id},
            "status_code": 200,
            "body_captured": True,
            "artifact": str(blob),
            "body_bytes": len(new_html.encode()),
            "body_sha256": hashlib.sha256(new_html.encode()).hexdigest(),
        },
    )
    digest = hashlib.sha256(new.description.encode()).hexdigest()
    observed = datetime.now(timezone.utc)
    proof = {
        "source_id": source.id,
        "external_id": new.external_id,
        "observed_at": observed.isoformat(),
        "parsed_source_text_sha256": digest,
        "captures": [{"path": str(capture), "sha256": sha(capture)}],
        "completeness_certified": False,
    }
    artifact = capture.parent / "detail.json"
    write(artifact, {"job": asdict(new), "proof": proof})
    setup["worker"].upsert_job(new)
    receipt = json.dumps({"detail_path": str(artifact), "detail_sha256": sha(artifact)})
    with setup["worker"].connect() as conn:
        conn.execute(
            "INSERT INTO remediation_observations VALUES(?,?,?,?,?,?)",
            (
                new.identity_key(),
                source.id,
                observed.timestamp(),
                digest,
                digest,
                json.dumps(proof),
            ),
        )
        conn.execute(
            "INSERT INTO remediation_tasks VALUES(?,?,?,?,?,?,?)",
            ("detail-37851", source.id, "detail", "37851", "done", receipt, "{}"),
        )
        conn.execute(
            "INSERT INTO remediation_attempts VALUES(?,?,?,?,?,?,?,?)",
            (
                "accepted-37851",
                "detail-37851",
                source.id,
                "detail",
                observed.timestamp(),
                observed.timestamp(),
                "done",
                receipt,
            ),
        )
    result = run(setup, execute=True)
    assert result["status"] == "no_changes"
    assert (
        result["rejected"][0]["reason"]
        == "shorter_than_retained_live_text_requires_full_source_contract"
    )
    for path in (setup["output"] / "ifad_jobs.sqlite3", setup["output"] / "all_jobs.sqlite3"):
        actual = JobDatabase(path).get_job(old.identity_key())
        assert actual["description"] == old.description
        assert actual["first_seen_at"] == old.first_seen_at.isoformat()
        assert actual["raw"]["grade"] == "P-1"
