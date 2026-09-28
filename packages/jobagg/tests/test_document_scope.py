import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from jobagg.http_safe import SSRFProtectionError, SafeHTTPPolicy
from jobagg.models import JobRecord
from jobagg.pipelines.document_tasks import (
    classify_document_link,
    discover_document_inventory,
    discover_documents,
    extract_document,
    propose_aiib_navigation_cleanup,
)


def job(html=""):
    return JobRecord(
        source_id="example",
        org_id="Example",
        ats_family="html",
        external_id="1",
        title="Officer",
        apply_url="https://example.org/jobs/1",
        description=html,
    )


def aiib():
    value = json.loads(
        (Path(__file__).parent / "fixtures/document_tasks/aiib_navigation_detail.json").read_text()
    )
    return JobRecord(**value["job"]), value


def test_real_aiib_global_navigation_is_not_nine_required_documents():
    record, fixture = aiib()
    inventory = discover_document_inventory(record)
    assert len(fixture["prior_document_candidates"]) == 9
    assert inventory["candidates"] == []
    excluded = {
        item["url"]
        for item in inventory["excluded_links"]
        if item["classification"]["reason"] == "site_navigation_link"
    }
    assert {item["url"] for item in fixture["prior_document_candidates"]} <= excluded
    assert inventory["discovery_complete"] is False


def test_job_documents_and_forms_survive_navigation_policy_and_portal_filter():
    record = job("""<header><nav>
        <a href="/climate-plan.pdf">Climate Action Plan</a>
        <a href="/framework">Accountability Framework</a>
        <a href="/jd.pdf">Job Description</a></nav></header>
        <main><a href="/role.docx">Terms of Reference</a>
        <a href="/p11.docx">P11 personal history form</a>
        <a href="/unknown.pdf">Supporting information</a>
        <a href="/download?id=123" download>Attachment</a>
        <a href="/privacy-policy.pdf">Privacy Policy</a>
        <a href="https://linkedin.com/share?u=role.pdf">Share</a>
        <a href="/career?career_ns=job_listing">How to apply</a>
        </main><footer><a href="/report.pdf">Annual report</a></footer>""")
    inventory = discover_document_inventory(record)
    assert {item["url"] for item in inventory["candidates"]} == {
        "https://example.org/jd.pdf",
        "https://example.org/role.docx",
        "https://example.org/p11.docx",
        "https://example.org/unknown.pdf",
        "https://example.org/download?id=123",
    }
    assert all(item["required"] for item in inventory["candidates"])
    assert {item["classification"]["reason"] for item in inventory["excluded_links"]} >= {
        "site_navigation_link",
        "site_policy_link",
        "social_or_sharing_link",
        "application_portal_or_instructions_link",
    }


def test_same_classifier_filters_recursive_html_wrapper_links():
    data = b"""<html><nav><a href="policy.pdf">Bank policy</a></nav>
    <main><a href="download.docx">Terms of Reference</a></main>
    <footer><a href="annual.pdf">Annual report</a></footer></html>"""
    result = extract_document(data, "text/html", "https://example.org/wrapper")
    assert [link["url"] for link in result["document_links"]] == [
        "https://example.org/download.docx"
    ]
    assert len(result["excluded_document_links"]) == 2
    assert result["extraction_status"] == "partial" and not result["fidelity_complete"]


def test_pdf_policy_annotation_is_audited_but_tor_annotation_is_retained():
    from test_document_tasks import pdf_bytes

    policy = extract_document(
        pdf_bytes(link="https://example.org/privacy-policy.pdf"),
        "application/pdf",
        "https://example.org/jd.pdf",
    )
    assert policy["document_links"] == []
    assert policy["excluded_document_links"][0]["classification"]["reason"] == "site_policy_link"
    tor = extract_document(
        pdf_bytes(link="https://files.provider.org/role-tor.pdf"),
        "application/pdf",
        "https://example.org/jd.pdf",
    )
    assert tor["document_links"][0]["url"] == "https://files.provider.org/role-tor.pdf"
    assert not tor["fidelity_complete"]


def test_unknown_main_body_reference_and_explicit_declaration_are_retained():
    record = job('<a href="/framework">Competency framework</a>')
    record.raw = {"required_attachment_urls": ["https://files.example.org/opaque?id=1"]}
    candidates = discover_documents(record)
    assert len(candidates) == 2
    assert all(r["required"] and r["purpose_state"] == "unresolved" for r in candidates)


def test_clickable_literal_url_does_not_gain_a_printed_url_hold():
    record = job("See https://example.org/a.pdf?token=x&section=1")
    record.raw = {
        "detail_html": '<a href="/a.pdf?token=x&section=1">https://example.org/a.pdf?token=x&section=1</a>'
    }
    candidates = discover_documents(record)
    assert len(candidates) == 1
    assert not candidates[0].get("printed_url_dispatch_requires_review")


def test_valid_off_origin_document_does_not_bypass_source_or_network_guard():
    url = "https://files.provider.org/job-specific-tor.pdf"
    assert classify_document_link(url, "Terms of Reference")["decision"] == "candidate"
    policy = SafeHTTPPolicy(allowed_hosts={"example.org"}, resolver=lambda _: ["8.8.8.8"])
    with pytest.raises(SSRFProtectionError, match="allowlist"):
        policy.validate_url(url)
    reviewed = SafeHTTPPolicy(allowed_hosts={"files.provider.org"}, resolver=lambda _: ["8.8.8.8"])
    assert reviewed.validate_url(url) == "files.provider.org"
    private = SafeHTTPPolicy(allowed_hosts={"files.provider.org"}, resolver=lambda _: ["127.0.0.1"])
    with pytest.raises(SSRFProtectionError, match="denied network"):
        private.validate_url(url)
    with pytest.raises(SSRFProtectionError, match="allowlist"):
        reviewed.validate_redirect(url, "http://169.254.169.254/latest/meta-data", redirect_count=1)


def test_cleanup_only_proposes_current_aiib_navigation_and_proven_descendants():
    record, fixture = aiib()
    parent = fixture["prior_document_candidates"][0]
    body_hash = hashlib.sha256(record.description.encode()).hexdigest()
    common = {
        "source_id": record.source_id,
        "job_key": record.identity_key(),
        "parent_description_sha256": body_hash,
    }
    tasks = [
        {"task_id": "root", "payload": {**common, "url": parent["url"], "depth": 0}},
        {
            "task_id": "child",
            "payload": {
                **common,
                "url": "https://www.aiib.org/unrelated.pdf",
                "depth": 1,
                "parent_document_sha256": "a" * 64,
            },
        },
        {
            "task_id": "unknown",
            "payload": {
                **common,
                "url": "https://www.aiib.org/unknown.pdf",
                "depth": 1,
                "parent_document_sha256": "b" * 64,
            },
        },
        {
            "task_id": "stale",
            "payload": {
                **common,
                "url": parent["url"],
                "depth": 0,
                "parent_description_sha256": "old",
            },
        },
    ]
    manifests = [
        {
            **common,
            "url": parent["url"],
            "content_sha256": "a" * 64,
            "document_links": [{"url": "https://www.aiib.org/unrelated.pdf"}],
        }
    ]
    original = json.dumps([tasks, manifests], sort_keys=True)
    proposal = propose_aiib_navigation_cleanup(record, tasks, manifests)
    assert {item["task_id"] for item in proposal["eligible"]} == {"root", "child"}
    assert proposal["retained"] == ["stale", "unknown"]
    assert not proposal["writes_performed"]
    assert json.dumps([tasks, manifests], sort_keys=True) == original
    assert {
        item["task_id"]
        for item in propose_aiib_navigation_cleanup(
            record, tasks, [{**manifests[0], "document_links": []}]
        )["eligible"]
    } == {"root"}
    record.raw["required_attachment_urls"] = [parent["url"]]
    assert propose_aiib_navigation_cleanup(record, tasks, manifests)["eligible"] == []


def test_cleanup_retains_explicit_forms_and_any_content_region_reference():
    record, fixture = aiib()
    parent = fixture["prior_document_candidates"][0]
    common = {
        "source_id": record.source_id,
        "job_key": record.identity_key(),
        "parent_description_sha256": hashlib.sha256(record.description.encode()).hexdigest(),
        "url": parent["url"],
        "depth": 0,
    }
    assert (
        propose_aiib_navigation_cleanup(
            record,
            [{"task_id": "form", "payload": {**common, "label": "P11 application form"}}],
            [],
        )["eligible"]
        == []
    )
    record.raw["detail_html"] += f'<main><a href="{parent["url"]}">Relevant information</a></main>'
    assert (
        propose_aiib_navigation_cleanup(
            record, [{"task_id": "content-reference", "payload": common}], []
        )["eligible"]
        == []
    )


def docx(*, images=False, extra_members=None):
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "word/document.xml": f'''<w:document xmlns:w="{word}" xmlns:r="{rel}"><w:body>
        <w:p><w:r><w:t>Responsibilities</w:t><w:tab/><w:t>All text</w:t><w:br/><w:t>Next line</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table cell A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Table cell B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        <w:p><w:hyperlink r:id="download"><w:r><w:t>Annex Terms of Reference</w:t></w:r></w:hyperlink><w:footnoteReference w:id="1"/>{"<w:r><w:drawing/></w:r>" if images else ""}</w:p>
        <w:sectPr><w:headerReference r:id="header"/></w:sectPr></w:body></w:document>''',
        "word/_rels/document.xml.rels": f'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="download" Type="{rel}/hyperlink" Target="https://files.provider.org/annex.pdf?token=x&amp;section=2" TargetMode="External"/>
        <Relationship Id="header" Type="{rel}/header" Target="header1.xml"/>
        <Relationship Id="notes" Type="{rel}/footnotes" Target="footnotes.xml"/></Relationships>''',
        "word/header1.xml": f'<w:hdr xmlns:w="{word}"><w:p><w:r><w:t>Vacancy 123 header</w:t></w:r></w:p></w:hdr>',
        "word/footnotes.xml": f'<w:footnotes xmlns:w="{word}"><w:footnote w:id="1"><w:p><w:r><w:t>Required qualification note</w:t></w:r></w:p></w:footnote><w:footnote w:id="2"><w:p><w:r><w:t>Orphan note not referenced</w:t></w:r></w:p></w:footnote></w:footnotes>',
    }
    parts.update(extra_members or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_native_docx_extracts_body_tables_linked_header_notes_and_links():
    data = docx()
    result = extract_document(
        data,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "https://example.org/role.docx",
    )
    assert result["extraction_status"] == "extracted"
    for text in [
        "Responsibilities\tAll text\nNext line",
        "Table cell A",
        "Table cell B",
        "Vacancy 123 header",
        "Required qualification note",
    ]:
        assert text in result["extracted_text"]
    assert "Orphan note" not in result["extracted_text"]
    assert [u["part"] for u in result["units"]] == [
        "word/document.xml",
        "word/header1.xml",
        "word/footnotes.xml",
    ]
    assert (
        result["document_links"][0]["url"]
        == "https://files.provider.org/annex.pdf?token=x&section=2"
    )
    assert result["content_sha256"] == hashlib.sha256(data).hexdigest()
    assert result["text_sha256"] == hashlib.sha256(result["extracted_text"].encode()).hexdigest()
    assert not result["fidelity_complete"] and not result["discovery_complete"]
    assert result["detectors"]["external_relationships_executed"] is False


def test_docx_image_cannot_claim_full_native_text():
    result = extract_document(
        docx(images=True), "application/octet-stream", "https://example.org/role.docx"
    )
    assert result["extraction_status"] == "partial"
    assert result["detectors"]["image_or_drawing_count"] == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"../escape.xml": "bad"},
        {"word/large.xml": "x" * 1_000_000},
        {"word/document.xml": '<!DOCTYPE x [<!ENTITY x "expand">]><x>&x;</x>'},
    ],
)
def test_unsafe_docx_archives_fail_closed(extra):
    result = extract_document(
        docx(extra_members=extra), "application/octet-stream", "https://example.org/role.docx"
    )
    assert result["extraction_status"] == "failed"
    assert not result["fidelity_complete"]


def test_malformed_docx_and_html_challenge_are_not_empty_success():
    for content, mime in [
        (b"PK\x03\x04bad", "application/zip"),
        (b"<html>Sign in</html>", "text/html"),
    ]:
        result = extract_document(content, mime, "https://example.org/role.docx")
        assert result["extraction_status"] == "failed"
