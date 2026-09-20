"""Document tasks never turn a native extraction into a complete-source claim."""

import hashlib
import io
import zipfile

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    TextStringObject,
)

from jobagg.models import JobRecord
from jobagg.pipelines.document_tasks import (
    canonical_document_url,
    discover_documents,
    extract_document,
)


def pdf_bytes(texts=("First page", "Second page"), *, link=None, widget=False, encrypted=False):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
        )
        if text:
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 250 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
    if texts and (link or widget):
        annot = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget" if widget else "/Link"),
                NameObject("/Rect"): ArrayObject([FloatObject(n) for n in (1, 1, 2, 2)]),
            }
        )
        if link:
            annot[NameObject("/A")] = DictionaryObject(
                {NameObject("/S"): NameObject("/URI"), NameObject("/URI"): TextStringObject(link)}
            )
        writer.pages[0][NameObject("/Annots")] = ArrayObject([writer._add_object(annot)])
    if encrypted:
        writer.encrypt("not-supplied")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def job(**changes):
    values = dict(
        source_id="sample",
        org_id="org",
        ats_family="html",
        title="Officer",
        external_id="123",
        apply_url="https://example.org/jobs/123",
        description="",
    )
    values.update(changes)
    return JobRecord(**values)


def test_all_pdf_pages_and_text_are_hash_bound_without_fidelity_claim():
    data = pdf_bytes()
    result = extract_document(data, "application/pdf", "https://example.org/vacancy.pdf")
    assert result["extraction_status"] == "extracted"
    assert result["content_sha256"] == hashlib.sha256(data).hexdigest()
    assert result["page_count"] == 2
    assert [u["page"] for u in result["units"]] == [1, 2]
    assert result["extracted_text"] == "\n\n".join(
        f"[Page {u['page']}]\n{u['text']}" for u in result["units"]
    )
    assert result["text_sha256"] == hashlib.sha256(result["extracted_text"].encode()).hexdigest()
    assert all(
        u["text_sha256"] == hashlib.sha256(u["text"].encode()).hexdigest() for u in result["units"]
    )
    assert result["detectors"]["completed"] is True
    assert result["detectors"]["independent_extraction"] == "not_performed"
    assert result["fidelity_complete"] is False
    assert result["discovery_complete"] is False


def test_pdf_nested_document_links_keep_parent_identity_and_unresolved_purpose():
    data = pdf_bytes(link="https://example.org/requirements.pdf?section=2&token=abc")
    result = extract_document(data, "application/pdf", "https://example.org/main.pdf")
    child = result["document_links"][0]
    assert child["url"] == "https://example.org/requirements.pdf?section=2&token=abc"
    assert child["parent_content_sha256"] == hashlib.sha256(data).hexdigest()
    assert child["page"] == 1
    assert child["required"] is True and child["purpose_state"] == "unresolved"
    assert "nested_document_purpose_and_retrieval_unresolved" in result["discovery_gaps"]


def test_blank_pdf_page_remains_in_order_and_requires_review():
    result = extract_document(pdf_bytes(("First", "")), "application/pdf", "https://x/a.pdf")
    assert result["extraction_status"] == "partial"
    assert len(result["units"]) == 2
    assert result["detectors"]["empty_pages"] == [2]
    assert result["units"][1]["text"] == ""
    assert not result["fidelity_complete"]


def test_form_widget_never_claims_full_native_extraction():
    result = extract_document(pdf_bytes(widget=True), "application/pdf", "https://x/a.pdf")
    assert result["extraction_status"] == "partial"
    assert result["detectors"]["widget_pages"] == [1]
    assert "form_control_values_and_options_not_extracted" in result["extraction_gaps"]


@pytest.mark.parametrize("data", [pdf_bytes(encrypted=True), pdf_bytes(())])
def test_encrypted_and_zero_page_pdfs_fail_closed(data):
    result = extract_document(data, "application/pdf", "https://x/a.pdf")
    assert result["extraction_status"] == "failed"
    assert not result["fidelity_complete"]
    assert result["content_sha256"] == hashlib.sha256(data).hexdigest()


def test_wrong_pdf_response_is_retained_but_rejected():
    result = extract_document(b"<html>Sign in</html>", "text/html", "https://x/a.pdf")
    assert result["extraction_status"] == "failed"
    assert "expected_pdf_but_response_is_not_pdf" in str(result["extraction_gaps"])


def test_office_archive_is_unsupported_not_empty_success():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("unrelated.txt", "not an OOXML document")
    result = extract_document(archive.getvalue(), "application/zip", "https://x/archive.zip")
    assert result["extraction_status"] == "unsupported"
    assert "Office_or_ZIP_requires_structural_extractor" in result["extraction_gaps"]


def test_html_download_wrapper_remains_partial_and_discovers_exact_child():
    result = extract_document(
        b'<html><a href="a.pdf?x=1&section=2">Download</a></html>', "text/html", "https://x/wrapper"
    )
    assert result["extraction_status"] == "partial"
    assert result["document_links"][0]["url"] == "https://x/a.pdf?x=1&section=2"
    assert not result["fidelity_complete"]


def test_text_decoding_and_empty_text_have_distinct_states():
    exact = extract_document(b"First\nSecond", "text/plain", "https://x/a.txt")
    assert exact["extracted_text"] == "First\nSecond"
    assert exact["fidelity_status"] == "exact_utf8_decoding"
    assert not exact["fidelity_complete"]
    assert extract_document(b"", "text/plain", "https://x/a.txt")["extraction_status"] == "partial"


def test_discovery_preserves_signed_query_and_never_exempts_unknown_purpose():
    found = discover_documents(
        job(
            description=(
                '<a href="/terms.pdf?x=1&section=2&amp;token=abc">General conditions</a>'
                '<a href="/framework">Competency framework</a>'
            )
        )
    )
    assert {r["url"] for r in found} == {
        "https://example.org/terms.pdf?x=1&section=2&token=abc",
        "https://example.org/framework",
    }
    assert all(r["required"] and r["purpose_state"] == "unresolved" for r in found)
    assert all(r["job_key"] == "org:123" and r["source_id"] == "sample" for r in found)
    assert all(not r["freshness_verified"] and not r["discovery_complete"] for r in found)


def test_public_raw_fields_are_discovered_but_history_and_certificates_are_not():
    found = discover_documents(
        job(
            raw={
                "detail_html": '<a href="/current.docx">P11</a>',
                "history": {"detail_html": '<a href="/old.pdf">JD</a>'},
                "attachment_verification": {"description": "https://example.org/cert.pdf"},
                "_private": {"description": "https://example.org/private.pdf"},
            }
        )
    )
    assert [r["url"] for r in found] == ["https://example.org/current.docx"]
    assert found[0]["provenance"][0]["path"] == "raw.detail_html"


def test_printed_url_is_not_silently_repaired_and_requires_dispatch_review():
    found = discover_documents(job(description="See https://example.org/a.pdf?token=x)."))
    assert found[0]["url"] == "https://example.org/a.pdf?token=x)."
    assert found[0]["printed_url_dispatch_requires_review"] is True


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///tmp/a.pdf",
        "https://user:secret@example.org/a.pdf",
        "#document",
    ],
)
def test_nonpublic_or_fragment_urls_rejected(url):
    assert canonical_document_url(url, "https://example.org/main") is None


def test_uuid_suffix_pdf_annotation_is_not_silently_lost():
    url = "https://echa.europa.eu/documents/10162/guide.pdf/01234567-89ab-cdef-0123-456789abcdef"
    result = extract_document(pdf_bytes(link=url), "application/pdf", "https://x/main.pdf")
    assert result["document_links"][0]["url"] == url
    assert result["document_links"][0]["purpose_state"] == "unresolved"
    assert result["fidelity_complete"] is False


def test_current_declared_required_urls_are_queued_with_provenance_but_history_is_not():
    found = discover_documents(
        job(
            raw={
                "required_attachment_urls": ["https://example.org/exact-download?id=123"],
                "_prior": {"required_attachment_urls": ["https://example.org/stale.pdf"]},
                "history": {"required_attachment_urls": ["https://example.org/old.pdf"]},
            }
        )
    )
    assert [r["url"] for r in found] == ["https://example.org/exact-download?id=123"]
    assert found[0]["provenance"][0]["path"] == "raw.required_attachment_urls"
    assert found[0]["required"] and found[0]["purpose_state"] == "unresolved"
    assert not found[0]["discovery_complete"] and not found[0]["freshness_verified"]
