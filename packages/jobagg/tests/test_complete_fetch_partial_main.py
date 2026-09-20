import hashlib

from jobagg.db import JobDatabase
from jobagg.detail_quality import detail_quality_status
from jobagg.models import OrganizationSource
from jobagg.normalize import build_job


INTRO = "Public introduction and eligibility. See the required notice for full duties. " * 20


def job(body, raw=None):
    source = OrganizationSource("eu_careers_static", "EU", "static_html", "https://example.org")
    return build_job(
        source,
        external_id="cinea",
        title="Financial Engineering Adviser",
        description=body,
        apply_url="https://example.org/cinea",
        raw=raw or {},
    )


def quality(row):
    return detail_quality_status(title=row["title"], description=row["description"], raw=row["raw"])


def test_same_body_refresh_preserves_bound_incomplete_main_evidence(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    partial = job(INTRO)
    partial.raw["_jobagg_main_text_verification"] = {
        "complete": False,
        "normalized_description_sha256": hashlib.sha256(
            " ".join(partial.description.split()).encode()
        ).hexdigest(),
        "reason": "Required public vacancy PDF unavailable",
    }
    db.upsert_job(partial)
    db.upsert_job(job(INTRO))
    row = db.get_job(partial.identity_key())
    assert quality(row) == "detail_missing"
    assert (
        row["raw"]["_jobagg_main_text_verification"]
        == partial.raw["_jobagg_main_text_verification"]
    )

    # Existing negative evidence remains historical but cannot condemn a new body.
    full = job(INTRO + " Full duties: manage financial operations and budget reporting.")
    db.upsert_job(full)
    row = db.get_job(full.identity_key())
    assert quality(row) == "complete"
    assert row["description"] == full.description


def test_ordinary_complete_body_and_unbound_flags_are_unchanged():
    ordinary = job(INTRO)
    assert (
        detail_quality_status(title=ordinary.title, description=ordinary.description, raw={})
        == "complete"
    )
    assert (
        detail_quality_status(
            title=ordinary.title,
            description=ordinary.description,
            raw={"_jobagg_main_text_verification": {"complete": False}},
        )
        == "complete"
    )
