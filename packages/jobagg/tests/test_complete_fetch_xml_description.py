from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.successfactors_rmk import SuccessFactorsLegacyAdapter
from jobagg.db import JobDatabase
from jobagg.models import OrganizationSource


FIXTURE = Path(__file__).parent / "fixtures/successfactors_xml_descriptions.xml"


def parse(text):
    source = OrganizationSource(
        "icc_successfactors_legacy", "ICC", "successfactors_rmk", "https://public.example"
    )
    adapter = SuccessFactorsLegacyAdapter(AdapterContext(source=source, http=SimpleNamespace()))
    return adapter.parse_jobs_from_xml_feed(ET.fromstring(text))


def test_xml_cdata_and_nested_markup_preserve_links_without_polluting_main_text():
    first, second = parse(FIXTURE.read_text())
    assert "<strong>programme delivery</strong>" in first.raw["detail_html"]
    assert "tor.pdf?language=en&amp;version=2" in first.raw["detail_html"]
    assert first.description == first.raw["jobdescription"]
    assert "programme delivery" in first.description and "Terms of Reference" in first.description
    assert "<a" not in first.description and "<strong" not in first.description
    assert "<em>operations</em>" in second.raw["detail_html"]
    assert "https://public.example/vacancy/24539/annex.pdf" in second.raw["detail_html"]
    assert "Manage operations" in second.description and "<em>" not in second.description


def test_href_only_xml_change_invalidates_attachment_discovery(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    first = parse(FIXTURE.read_text())[0]
    first.raw["attachment_verification"] = {"complete": True, "discovery_complete": True}
    db.upsert_job(first)
    changed = parse(FIXTURE.read_text().replace("version=2", "version=3"))[0]
    assert changed.description == first.description
    db.upsert_job(changed)
    row = db.get_job(f"{first.org_id}:{first.external_id}")
    assert "version=3" in row["raw"]["detail_html"]
    assert row["raw"]["attachment_verification"]["complete"] is False
    assert row["raw"]["attachment_verification"]["discovery_complete"] is False
    assert row["description"] == first.description
