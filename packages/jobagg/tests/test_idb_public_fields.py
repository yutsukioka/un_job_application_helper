"""IDB's actual labelled notices must retain public metadata and uncertainty."""

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.idb_public import _deadline, apply_public_fields, public_fields
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter, _detail_description
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource

FIXTURES = Path(__file__).parent / "fixtures/successfactors/idb_public_20260913"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())["records"]


def detail(identity="3452", *, html_text=None):
    proof = next(row for row in MANIFEST if row["external_id"] == identity)
    body = gzip.decompress((FIXTURES / proof["fixture"]).read_bytes())
    assert hashlib.sha256(body).hexdigest() == proof["body_sha256"]
    text = html_text if html_text is not None else body.decode()

    class HTTP:
        def get(self, url):
            assert url == proof["source_url"]
            return HttpResponse(url, 200, {}, text)

    adapter = SuccessFactorsRMKAdapter(AdapterContext(OrganizationSource(
        "idb_successfactors", "IDB", "successfactors_rmk", "https://jobs.iadb.org"), HTTP()))
    job = adapter.fetch_detail_for_listing_item({"detail_url": proof["source_url"], "title": proof["title"]})
    return job, text


@pytest.mark.parametrize("identity", [row["external_id"] for row in MANIFEST])
def test_actual_notice_keeps_full_body_and_exact_identity(identity):
    job, text = detail(identity)
    resolution = job.raw["_idb_public_field_resolution"]
    assert job.description == _detail_description(text)
    assert job.raw["detail_html"] == text
    assert job.title == resolution["public_title"]
    assert job.source_url == job.apply_url == resolution["source_url"]
    assert job.external_id == resolution["external_id"]
    assert job.location == resolution["header_labels"]["City"]
    assert job.raw["company"] == resolution["header_labels"]["Company"]
    assert job.department is None and job.posted_at is None
    assert job.closes_at is None and job.closes_tz is None
    assert job.closes_at_local
    assert hashlib.sha256(job.description.encode()).hexdigest() == resolution["retained_description_sha256"]


def test_intern_header_and_labelled_contract_are_not_company_or_duration():
    job, _ = detail()
    assert job.location == "Washington DC"
    assert job.raw["company"] == "IDB Invest"
    assert job.employment_type == "Intern Consultant"
    assert job.closes_at_local == "2026-09-25T23:59"
    proof = job.raw["_idb_public_field_resolution"]
    assert proof["deadline"]["public_label"] == "9/25/2026 11:59 PM EST"
    assert proof["deadline"]["public_timezone_label"] == "EST"
    assert "EST_fixed_standard" in proof["deadline"]["unknown_reason"]


@pytest.mark.parametrize(("identity", "expected"), [
    ("3526", "Consultant(e) national(e) à temps plein (CNS)."),
    ("3524", "Consultor Externo de Produtos e Serviços (PEC)"),
    ("3580", "Consultor de Productos y Servicios Externos (PEC), suma alzada."),
    ("3544", "Consultor/a Local Remoto a tiempo completo"),
    ("3516", "International staff contract"),
    ("3227", "nternational consultant Full-Time"),
    ("3447", "Intern Consultant"),
    ("3444", "Intern Consultant"),
])
def test_public_contract_language_bullets_and_source_typo_are_preserved(identity, expected):
    job, _ = detail(identity)
    assert job.employment_type == expected
    assert job.raw["_idb_public_field_resolution"]["contract_section_lines"]


@pytest.mark.parametrize("identity", ["3070", "3487", "3539"])
def test_absent_or_conditional_contract_is_not_inferred_from_title(identity):
    job, _ = detail(identity)
    assert job.employment_type is None
    proof = job.raw["_idb_public_field_resolution"]
    if identity == "3539":
        assert proof["contract_type_unknown_reason"] == "conditional_or_unrecognized_public_terms"
        assert "nacional o internacional" in " ".join(proof["contract_section_lines"])
    else:
        assert proof["contract_type_unknown_reason"] == "no_explicit_contract_section"


@pytest.mark.parametrize(("label", "local"), [
    ("September 16th, 2026", "2026-09-16"), ("Sep 16, 2026", "2026-09-16"),
    ("16 September, 2026", "2026-09-16"), ("16 September 2026", "2026-09-16"),
    ("9/15/2026", "2026-09-15"), ("September 11, 2026.", "2026-09-11"),
    ("2/29/2028 11:59 PM EST", "2028-02-29T23:59"),
    ("2/29/2026 11:59 PM EST", None), ("deadline extended", None),
])
def test_calendar_and_unknown_deadline_labels_do_not_invent_instants(label, local):
    result = _deadline(label)
    assert result["public_label"] == label and result["closes_at_local"] == local
    assert result["closes_at"] is None and result["closes_tz"] is None
    assert result["utc_resolved"] is False


@pytest.mark.parametrize("defect", ["source_url", "apply_url", "external_id", "title"])
def test_metadata_cannot_bind_to_a_different_public_notice(defect):
    job, text = detail()
    setattr(job, defect, "https://example.org/elsewhere" if defect.endswith("url") else "wrong")
    with pytest.raises(ValueError, match="exact captured notice identity"):
        apply_public_fields(job, text)


def test_unlabelled_generic_html_and_malformed_contract_structure_do_not_certify():
    assert public_fields("<p>Company: IDB. Posting End Date: next Friday.</p>") is None
    _, text = detail()
    text = text.replace('<b>Type of contract and duration</b>', '<b>Type of contract and duration</b></H2><H2>Unrelated extra heading')
    with pytest.raises(ValueError, match="contract section structure"):
        public_fields(text)
