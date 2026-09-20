"""Pure attachment discovery and extraction; no network, storage or freshness claims.

Callers must bind the JobRecord to a current accepted source capture, apply URL/
robots/rate policies, and persist original bytes before invoking the extractor.
Unknown link purpose is a review requirement, never a background exemption.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import posixpath
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET
import zipfile

from pypdf import PdfReader

from jobagg.models import JobRecord

VERSION = "document-tasks-2"
_EXTENSIONS = {".pdf", ".doc", ".docx", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".txt", ".zip"}
_PURPOSE = re.compile(
    r"job\s*description|terms?\s+of\s+reference|\btor\b|vacancy\s+notice|\bp\s?11\b|"
    r"personal\s+history|competenc|framework|application\s+(?:form|guide)|how\s+to\s+apply|"
    r"retainer|contract\s+(?:types?|terms?|conditions?)|selection\s+(?:criteria|procedure)",
    re.I,
)
_PUBLIC_FIELDS = {
    "description",
    "detail_html",
    "description_html",
    "jobdescription",
    "job_description",
    "externaldescription",
    "externaldescriptionstr",
    "externalqualificationsstr",
    "externalresponsibilitiesstr",
    "taskdescription",
    "livingconditions",
    "requirements",
    "qualifications",
    "responsibilities",
    "maindutiesandresponsibilities",
    "education",
    "additionalinformation",
    "competencies",
    "jobad",
    "sections",
    "jobpostinginfo",
}
_EXCLUDED = {"attachments", "attachment_verification", "history", "previous", "archive"}
_JOB_DOCUMENT = re.compile(
    r"job\s*description|terms?\s+of\s+reference|\btor\b|vacancy\s+notice|"
    r"\bjd\b|\bp\s?11\b|personal\s+history|application\s+form",
    re.I,
)
_SOCIAL_HOSTS = {
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
    "youtu.be",
    "whatsapp.com",
    "wa.me",
}


def classify_document_link(
    url: str, label: str, *, region: str = "content", download: bool = False
) -> dict[str, Any]:
    """Classify relevance only; this never grants transport/host authorization.

    Unknown documents and explicitly referenced job forms remain obligations.
    Clear page navigation and site policies are excluded with a recorded reason.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    label = " ".join(label.split())
    path = unquote(parts.path).lower()
    explicit = bool(_JOB_DOCUMENT.search(label))
    reason = None
    if host in _SOCIAL_HOSTS or any(host.endswith("." + known) for known in _SOCIAL_HOSTS):
        reason = "social_or_sharing_link"
    elif not explicit and (
        re.search(
            r"\b(?:privacy policy|cookie policy|cookie settings|terms of use|website disclaimer)\b",
            label,
            re.I,
        )
        or re.search(
            r"(?:^|/)(?:privacy[-_]policy|cookie[-_]policy|terms[-_]of[-_]use)(?:[./_-]|$)", path
        )
    ):
        reason = "site_policy_link"
    elif not explicit and region in {"navigation", "page_header", "page_footer"}:
        reason = "site_navigation_link"
    elif not explicit and (
        re.fullmatch(
            r"(?:apply(?:\s+(?:now|here|online|for this (?:job|position)))?|how to apply|sign in|log in|register|create (?:an? )?account)",
            label,
            re.I,
        )
        and not _document_like(url, "")
    ):
        reason = "application_portal_or_instructions_link"
    candidate = reason is None and (_document_like(url, label) or download)
    return {
        "version": VERSION,
        "decision": "candidate" if candidate else "excluded",
        "reason": reason
        or (
            "explicit_job_document"
            if explicit
            else "document_purpose_unresolved"
            if candidate
            else "ordinary_web_link"
        ),
        "region": region,
        "purpose_state": "job_specific_or_application_form"
        if explicit and candidate
        else "unresolved",
        "transport_authorization": "separate_source_allowlist_and_SSRF_validation_required",
        "completeness_certified": False,
    }


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_document_url(value: str, base_url: str) -> str | None:
    """Keep signed query bytes/order; avoid the permissive '&section' entity trap."""
    if not value.strip() or value.lstrip().startswith("#"):
        return None
    value = re.sub(
        r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);",
        lambda match: html.unescape(match[0]),
        value.strip(),
    )
    try:
        p = urlsplit(urljoin(base_url, value))
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return None
        return urlunsplit((p.scheme.lower(), p.netloc, p.path, p.query, ""))
    except ValueError:
        return None


def _document_like(url: str, label: str) -> bool:
    path = unquote(urlsplit(url).path).lower()
    return (
        any(path.endswith(ext) for ext in _EXTENSIONS)
        or bool(
            re.search(
                r"\.pdf/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/?$", path
            )
        )
        or bool(re.search(r"/(?:download|exportpdf)(?:/|$)|transferrichtextfile\.ashx$", path))
        or (urlsplit(url).hostname == "drive.google.com" and "/file/d/" in path)
        or bool(_PURPOSE.search(label))
    )


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.active: list[dict[str, str]] = []
        self.text: list[str] = []
        self.hidden = 0
        self.elements: list[tuple[str, str]] = []
        self.fragments: list[tuple[str, str]] = []

    def feed(self, data: str) -> None:
        # HTMLParser otherwise decodes valid entity prefixes in signed queries,
        # for example '&section=2' becomes a section sign followed by 'ion=2'.
        protected = re.sub(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]+;)", "&amp;", data)
        super().feed(protected)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        region = self.elements[-1][1] if self.elements else "content"
        role = (attributes.get("role") or "").lower()
        if tag == "nav" or role == "navigation":
            region = "navigation"
        elif tag == "header" or role == "banner":
            region = "page_header"
        elif tag == "footer" or role == "contentinfo":
            region = "page_footer"
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.elements.append((tag, region))
        if tag in {"script", "style"}:
            self.hidden += 1
        if self.hidden:
            return
        if tag == "a" and attributes.get("href"):
            node = {
                "url": attributes["href"] or "",
                "label": attributes.get("title") or "",
                "region": region,
                "download": "download" in attributes,
            }
            self.links.append(node)
            self.active.append(node)
        if tag in {"object", "embed", "iframe"}:
            target = attributes.get("data") or attributes.get("src")
            if target:
                self.links.append(
                    {"url": target, "label": attributes.get("title") or "", "region": region}
                )
        if tag == "img" and self.active and attributes.get("alt"):
            self.active[-1]["label"] += " " + (attributes["alt"] or "")
        if tag in {"p", "div", "li", "br", "h1", "h2", "h3", "tr"}:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "a" and self.active:
            self.active.pop()
        for index in range(len(self.elements) - 1, -1, -1):
            if self.elements[index][0] == tag:
                del self.elements[index:]
                break

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.text.append(data)
            self.fragments.append((data, self.elements[-1][1] if self.elements else "content"))
            if self.active:
                self.active[-1]["label"] += data


def _links_from_text(text: str, base: str, *, excluded: list | None = None) -> list[dict[str, Any]]:
    parser = _Links()
    parser.feed(text)
    found = list(parser.links)
    # This is discovery only. Printed line-wrapped/ambiguous URLs require review.
    linked = {canonical_document_url(item["url"], base) for item in found}
    for fragment, region in parser.fragments:
        for match in re.finditer(r"https?://[^\s<>\"']+", fragment):
            # A literal printed URL stays unmodified. An exact clickable target
            # already supplies dispatch evidence and must not become a false hold.
            if canonical_document_url(match[0], base) not in linked:
                found.append(
                    {"url": match[0], "label": "", "kind": "printed_url", "region": region}
                )
    result = []
    for item in found:
        url = canonical_document_url(item["url"], base)
        label = " ".join(item["label"].split())
        if url:
            decision = classify_document_link(
                url,
                label,
                region=item.get("region", "content"),
                download=bool(item.get("download")),
            )
            record = {
                "url": url,
                "label": label,
                "kind": item.get("kind", "html_link"),
                "classification": decision,
            }
            if decision["decision"] == "candidate":
                result.append(record)
            elif excluded is not None:
                excluded.append(record)
    return result


def discover_document_inventory(job: JobRecord) -> dict[str, Any]:
    """Return candidates from supported public fields; absence is not certification.

    Raw histories, attachment certificates and internal/private fields are never
    rediscovered as current evidence. Provider-specific encoded public bindings
    still need their adapter's independent validation before full discovery.
    """
    base = job.source_url or job.apply_url
    fields: list[tuple[str, str]] = [("description", job.description or "")]

    def walk(value: Any, path: str, public: bool = False) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                if (
                    lowered.startswith("_")
                    or lowered in _EXCLUDED
                    or lowered.startswith("internal")
                ):
                    continue
                walk(child, path + "." + str(key), public or lowered in _PUBLIC_FIELDS)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]", public)
        elif public and isinstance(value, str):
            fields.append((path, value))

    walk(job.raw, "raw")
    records: dict[str, dict[str, Any]] = {}
    exclusions = []
    for path, text in fields:
        excluded = []
        found_links = _links_from_text(text, base, excluded=excluded)
        exclusions.extend(
            {**item, "path": path, "field_sha256": _sha(text.encode())} for item in excluded
        )
        for found in found_links:
            url = found["url"]
            record = records.setdefault(
                url,
                {
                    "attachment_id": _sha((job.identity_key() + "\n" + url).encode()),
                    "job_key": job.identity_key(),
                    "source_id": job.source_id,
                    "external_id": job.external_id,
                    "url": url,
                    "label": found["label"],
                    "purpose_state": "unresolved",
                    "required": True,
                    "required_for_complete_text": True,
                    "provenance": [],
                    "discovery_complete": False,
                    "freshness_verified": False,
                    "classification": found["classification"],
                    "discovery_gaps": [
                        "source_capture_binding_required",
                        "purpose_review_required",
                        "provider_specific_public_field_coverage_unverified",
                    ],
                },
            )
            record["provenance"].append(
                {
                    "path": path,
                    "field_sha256": _sha(text.encode()),
                    "base_url": base,
                    "kind": found["kind"],
                    "source": "supplied_job_record_public_field",
                    "classification": found["classification"],
                }
            )
            if found["kind"] == "printed_url":
                record["discovery_gaps"].append("printed_url_continuation_requires_review")
                record["printed_url_dispatch_requires_review"] = True
    declared = job.raw.get("required_attachment_urls", [])
    if isinstance(declared, list):
        declaration_hash = _sha(json.dumps(declared, ensure_ascii=False, sort_keys=True).encode())
        for value in declared:
            if not isinstance(value, str):
                continue
            url = canonical_document_url(value, base)
            if not url:
                continue
            record = records.setdefault(
                url,
                {
                    "attachment_id": _sha((job.identity_key() + "\n" + url).encode()),
                    "job_key": job.identity_key(),
                    "source_id": job.source_id,
                    "external_id": job.external_id,
                    "url": url,
                    "label": "",
                    "purpose_state": "unresolved",
                    "required": True,
                    "required_for_complete_text": True,
                    "provenance": [],
                    "discovery_complete": False,
                    "freshness_verified": False,
                    "discovery_gaps": [
                        "source_capture_binding_required",
                        "purpose_review_required",
                        "provider_specific_public_field_coverage_unverified",
                    ],
                },
            )
            record["provenance"].append(
                {
                    "path": "raw.required_attachment_urls",
                    "field_sha256": declaration_hash,
                    "base_url": base,
                    "kind": "declared_required_url",
                    "source": "supplied_job_record_current_top_level_declaration",
                }
            )
    for record in records.values():
        if any(p["kind"] in {"html_link", "declared_required_url"} for p in record["provenance"]):
            record.pop("printed_url_dispatch_requires_review", None)
            record["discovery_gaps"] = [
                gap
                for gap in record["discovery_gaps"]
                if gap != "printed_url_continuation_requires_review"
            ]
    return {
        "version": VERSION,
        "candidates": list(records.values()),
        "excluded_links": exclusions,
        "discovery_complete": False,
        "completeness_certified": False,
        "remaining_scope": "Provider field coverage, current capture binding, incorporation and unknown links still require source contracts.",
    }


def discover_documents(job: JobRecord) -> list[dict[str, Any]]:
    """Compatibility interface; audit callers should retain the full inventory."""
    return discover_document_inventory(job)["candidates"]


def propose_aiib_navigation_cleanup(
    job: JobRecord, tasks: list[dict], manifests: list[dict]
) -> dict[str, Any]:
    """Read-only proposal for obligations reached solely through AIIB navigation.

    Input tasks are {task_id, payload}; manifests are captured document manifests.
    The caller must verify current source captures before applying any proposal.
    Nothing here updates the queue, deletes evidence, or certifies completeness.
    """
    inventory = discover_document_inventory(job)
    body_hash = _sha((job.description or "").encode())
    output = {
        "version": VERSION,
        "job_key": job.identity_key(),
        "source_id": job.source_id,
        "parent_description_sha256": body_hash,
        "eligible": [],
        "retained": [],
        "writes_performed": False,
        "completeness_certified": False,
        "application_requires_verified_current_capture_and_owner": True,
    }
    if job.source_id != "aiib_successfactors_legacy":
        output["reason"] = "Cleanup proposal restricted to reviewed AIIB navigation case"
        return output
    current = {item["url"] for item in inventory["candidates"]}
    # A content-region reference is not provably just site navigation, even if
    # its link label is too vague for automatic attachment discovery.
    current.update(
        item["url"]
        for item in inventory["excluded_links"]
        if item["classification"]["region"] == "content"
    )
    roots = {
        item["url"]
        for item in inventory["excluded_links"]
        if item["classification"]["reason"] == "site_navigation_link"
        and (urlsplit(item["url"]).hostname or "").lower() == "www.aiib.org"
    } - current
    excluded = set()
    remaining = {str(row["task_id"]): row for row in tasks}
    # Each pass establishes another proven ancestry level. Worker policy caps
    # retrieval depth at three; one additional blocked child level is auditable.
    for _ in range(5):
        changed = False
        for task_id, row in list(remaining.items()):
            payload = row.get("payload", {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    continue
            if (
                not isinstance(payload, dict)
                or payload.get("job_key") != job.identity_key()
                or payload.get("source_id") != job.source_id
                or payload.get("parent_description_sha256") != body_hash
                or payload.get("url") in current
                or _JOB_DOCUMENT.search(str(payload.get("label") or ""))
            ):
                continue
            url, depth = payload.get("url"), payload.get("depth")
            if not isinstance(url, str) or type(depth) is not int or not 0 <= depth <= 4:
                continue
            proof = None
            if depth == 0 and url in roots:
                proof = {"reason": "current_source_global_navigation", "root_url": url}
            elif depth > 0:
                parent_hash = payload.get("parent_document_sha256")
                parents = [
                    m
                    for m in manifests
                    if m.get("content_sha256") == parent_hash
                    and m.get("job_key") == job.identity_key()
                    and m.get("source_id") == job.source_id
                    and m.get("parent_description_sha256") == body_hash
                ]
                linked = any(
                    any(
                        isinstance(link, dict)
                        and link.get("url") == url
                        and link.get("parent_content_sha256", parent_hash) == parent_hash
                        for link in m.get("document_links", [])
                    )
                    for m in parents
                )
                if (
                    parent_hash
                    and parents
                    and linked
                    and all(m.get("url") in excluded for m in parents)
                ):
                    proof = {
                        "reason": "descendant_of_excluded_navigation_only",
                        "parent_document_sha256": parent_hash,
                        "parent_urls": sorted({m["url"] for m in parents}),
                    }
            if proof:
                output["eligible"].append(
                    {
                        "task_id": task_id,
                        "url": url,
                        "disposition": "not_job_specific_attachment",
                        **proof,
                    }
                )
                excluded.add(url)
                del remaining[task_id]
                changed = True
        if not changed:
            break
    output["retained"] = sorted(remaining)
    output["excluded_root_urls"] = sorted(roots)
    return output


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _docx_extract(data: bytes, url: str, result: dict[str, Any]) -> None:
    """Bounded native OOXML extraction; never executes macros or external links."""
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        members = package.infolist()
        names = [member.filename for member in members]
        if (
            len(members) > 2000
            or len(set(names)) != len(names)
            or sum(m.file_size for m in members) > 128 * 1024 * 1024
        ):
            raise ValueError("DOCX_archive_structure_or_size_budget_exceeded")
        for member in members:
            name = member.filename
            if (
                name.startswith(("/", "\\"))
                or "\\" in name
                or ".." in name.split("/")
                or member.flag_bits & 1
                or member.file_size > 32 * 1024 * 1024
                or member.file_size > max(member.compress_size, 1) * 200
            ):
                raise ValueError("DOCX_unsafe_or_oversized_archive_member")
        if "word/document.xml" not in names or "[Content_Types].xml" not in names:
            result["extraction_gaps"].append("Office_or_ZIP_requires_structural_extractor")
            return

        def xml(part):
            content = package.read(part)
            if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                raise ValueError("DOCX_XML_entity_or_DTD_not_allowed")
            return ET.fromstring(content)

        content_types = xml("[Content_Types].xml")
        if not any(
            node.get("PartName") == "/word/document.xml"
            and node.get("ContentType")
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
            for node in content_types
        ):
            raise ValueError("DOCX_main_document_type_unrecognized_or_macro_enabled")

        def relationships(part):
            folder, name = posixpath.split(part)
            rel = posixpath.join(folder, "_rels", name + ".rels")
            if rel not in names:
                return {}
            rows = list(xml(rel))
            ids = [node.get("Id") for node in rows]
            if len(ids) != len(set(ids)):
                raise ValueError("DOCX_duplicate_relationship_identity")
            return {node.get("Id"): node for node in rows if node.tag == _REL + "Relationship"}

        main = xml("word/document.xml")
        if main.tag != _W + "document" or main.find(_W + "body") is None:
            raise ValueError("DOCX_main_document_body_missing")
        main_rels = relationships("word/document.xml")

        def internal_part(relation, folder="word"):
            target = relation.get("Target", "")
            part = posixpath.normpath(
                target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
            )
            if (
                relation.get("TargetMode") == "External"
                or not part.startswith("word/")
                or part not in names
            ):
                raise ValueError("DOCX_internal_part_target_invalid")
            return part

        selected = [("word/document.xml", main, None)]
        part_seen = {"word/document.xml"}
        for node in main.iter():
            if node.tag in {_W + "headerReference", _W + "footerReference"}:
                relation = main_rels.get(node.get(_R + "id"))
                if relation is None or relation.get("TargetMode") == "External":
                    raise ValueError("DOCX_header_or_footer_relationship_missing")
                part = internal_part(relation)
                if part not in part_seen:
                    selected.append((part, xml(part), None))
                    part_seen.add(part)
        for kind in ("footnote", "endnote"):
            ids = {n.get(_W + "id") for n in main.iter(_W + kind + "Reference")}
            if ids:
                relations = [
                    r for r in main_rels.values() if r.get("Type", "").endswith("/" + kind + "s")
                ]
                if len(relations) != 1:
                    raise ValueError("DOCX_referenced_note_part_missing")
                part = internal_part(relations[0])
                selected.append((part, xml(part), ids))
        images = controls = unsupported = 0
        excluded = []
        for part, tree, note_ids in selected:
            rels = relationships(part)
            roots = (
                [tree] if note_ids is None else [n for n in tree if n.get(_W + "id") in note_ids]
            )
            if note_ids is not None and {n.get(_W + "id") for n in roots} != note_ids:
                raise ValueError("DOCX_referenced_note_text_missing")
            paragraphs = []
            for root in roots:
                parents = {child: node for node in root.iter() for child in node}
                for paragraph in root.iter(_W + "p"):
                    ancestor, nested = parents.get(paragraph), False
                    while ancestor is not None:
                        if ancestor.tag == _W + "p":
                            nested = True
                            break
                        ancestor = parents.get(ancestor)
                    if nested:
                        continue
                    chunks = []
                    for node in paragraph.iter():
                        if node.tag in {_W + "t", _W + "delText"}:
                            chunks.append(node.text or "")
                        elif node.tag == _W + "tab":
                            chunks.append("\t")
                        elif node.tag in {_W + "br", _W + "cr"}:
                            chunks.append("\n")
                    paragraphs.append("".join(chunks))
                for node in root.iter(_W + "hyperlink"):
                    rel = rels.get(node.get(_R + "id"))
                    if rel is None or rel.get("TargetMode") != "External":
                        continue
                    target = canonical_document_url(rel.get("Target", ""), url)
                    label = "".join(n.text or "" for n in node.iter(_W + "t"))
                    if target:
                        decision = classify_document_link(target, label)
                        record = {
                            "url": target,
                            "label": label,
                            "part": part,
                            "kind": "docx_hyperlink",
                            "classification": decision,
                            "parent_content_sha256": result["content_sha256"],
                            "purpose_state": "unresolved",
                            "required": True,
                        }
                        (
                            result["document_links"]
                            if decision["decision"] == "candidate"
                            else excluded
                        ).append(record)
                images += sum(n.tag in {_W + "drawing", _W + "pict"} for n in root.iter())
                controls += sum(n.tag in {_W + "sdt", _W + "ffData"} for n in root.iter())
                unsupported += sum(
                    n.tag
                    in {
                        _W + "altChunk",
                        _W + "object",
                        _W + "ins",
                        _W + "del",
                        _W + "commentRangeStart",
                    }
                    for n in root.iter()
                )
            text = "\n".join(paragraphs)
            result["units"].append(
                {
                    "part": part,
                    "method": "ooxml_native",
                    "paragraphs": paragraphs,
                    "text": text,
                    "text_sha256": _sha(text.encode()),
                }
            )
        result["extracted_text"] = "\n\n".join(
            f"[Part {u['part']}]\n{u['text']}" for u in result["units"]
        )
        result["excluded_document_links"] = excluded
        result["extraction_status"] = "extracted"
        result["detectors"] = {
            "version": VERSION,
            "completed": True,
            "parts": [p for p, _, _ in selected],
            "image_or_drawing_count": images,
            "form_control_count": controls,
            "unsupported_or_revision_nodes": unsupported,
            "external_relationships_executed": False,
        }
        if images or controls or unsupported or not any(u["text"].strip() for u in result["units"]):
            result["extraction_status"] = "partial"
            result["extraction_gaps"].append(
                "DOCX_images_controls_revisions_embedded_content_or_empty_body_require_review"
            )
        result["fidelity_gaps"].append(
            "DOCX_rendered_layout_pagination_fields_and_independent_text_comparison_unverified"
        )
        result["discovery_gaps"].append("printed_URLs_and_named_unlinked_documents_unverified")


def _image_count(resources: Any, seen: set[str] | None = None) -> int:
    seen = set() if seen is None else seen
    resources = resources.get_object() if hasattr(resources, "get_object") else resources
    count = 0
    objects = (resources or {}).get("/XObject", {})
    objects = objects.get_object() if hasattr(objects, "get_object") else objects
    for reference in objects.values():
        key = repr(reference)
        if key in seen:
            continue
        if len(seen) >= 10000:
            raise ValueError("PDF resource graph exceeds inspection budget")
        seen.add(key)
        obj = reference.get_object()
        if obj.get("/Subtype") == "/Image":
            count += 1
        elif obj.get("/Subtype") == "/Form":
            count += _image_count(obj.get("/Resources"), seen)
    return count


def extract_document(data: bytes, media_type: str, url: str) -> dict[str, Any]:
    """Extract every native PDF page; fidelity remains unverified without proof.

    No subprocesses, filesystem writes, OCR, credentials or network operations.
    Caller stores the original bytes even for unsupported/failed extraction.
    """
    result: dict[str, Any] = {
        "extractor_version": VERSION,
        "content_sha256": _sha(data),
        "byte_count": len(data),
        "media_type": media_type,
        "url": url,
        "extracted_text": "",
        "units": [],
        "page_count": None,
        "extraction_status": "unsupported",
        "fidelity_status": "unverified",
        "fidelity_complete": False,
        "discovery_complete": False,
        "document_links": [],
        "excluded_document_links": [],
        "extraction_gaps": [],
        "fidelity_gaps": [],
        "discovery_gaps": [],
        "detectors": {"version": VERSION, "completed": False},
    }
    mime = media_type.split(";", 1)[0].strip().lower()
    try:
        if data.lstrip().startswith(b"%PDF-"):
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("encrypted_pdf_requires_separate_access_review")
            result["page_count"] = len(reader.pages)
            images, widgets, blank, errors = [], [], [], []
            for number, page in enumerate(reader.pages, 1):
                unit: dict[str, Any] = {"page": number, "text": "", "method": "pypdf_native"}
                try:
                    unit["text"] = page.extract_text() or ""
                    if not unit["text"].strip():
                        blank.append(number)
                    if _image_count(page.get("/Resources")):
                        images.append(number)
                    for annotation in page.get("/Annots", []):
                        annotation = annotation.get_object()
                        if annotation.get("/Subtype") == "/Widget":
                            widgets.append(number)
                        action = annotation.get("/A")
                        action = action.get_object() if hasattr(action, "get_object") else action
                        target = canonical_document_url(str((action or {}).get("/URI") or ""), url)
                        label = str(annotation.get("/Contents") or "")
                        if target:
                            decision = classify_document_link(target, label)
                            bucket = (
                                "document_links"
                                if decision["decision"] == "candidate"
                                else "excluded_document_links"
                            )
                            result[bucket].append(
                                {
                                    "url": target,
                                    "label": label,
                                    "page": number,
                                    "purpose_state": "unresolved",
                                    "required": True,
                                    "parent_content_sha256": result["content_sha256"],
                                    "classification": decision,
                                }
                            )
                except Exception as exc:
                    unit["error"] = type(exc).__name__ + ": " + str(exc)
                    errors.append(number)
                unit["text_sha256"] = _sha(unit["text"].encode())
                result["units"].append(unit)
            result["extracted_text"] = "\n\n".join(
                f"[Page {unit['page']}]\n{unit['text']}" for unit in result["units"]
            )
            fields = reader.get_fields() or {}
            result["detectors"] = {
                "version": VERSION,
                "completed": not errors,
                "page_count": len(reader.pages),
                "empty_pages": blank,
                "error_pages": errors,
                "image_pages": images,
                "widget_pages": sorted(set(widgets)),
                "form_field_count": len(fields),
                "independent_extraction": "not_performed",
                "vector_or_table_layout_review": "not_performed",
            }
            result["extraction_status"] = "partial" if errors or blank else "extracted"
            if not reader.pages:
                result["extraction_status"] = "failed"
                result["extraction_gaps"].append("pdf_has_no_pages")
            if blank:
                result["extraction_gaps"].append("empty_pages_may_require_OCR_or_blank_page_review")
            if errors:
                result["extraction_gaps"].append("one_or_more_page_extraction_or_detector_errors")
            result["fidelity_gaps"].append(
                "independent_text_and_reading_order_verification_required"
            )
            if images:
                result["extraction_status"] = "partial"
                result["extraction_gaps"].append("image_content_not_extracted")
                result["fidelity_gaps"].append(
                    "embedded_images_require_text_or_decoration_disposition"
                )
            if widgets or fields:
                result["extraction_status"] = "partial"
                result["extraction_gaps"].append("form_control_values_and_options_not_extracted")
                result["fidelity_gaps"].append("form_controls_and_choice_states_require_review")
            result["discovery_gaps"].append("printed_URLs_and_named_unlinked_documents_unverified")
        elif mime == "application/pdf" or urlsplit(url).path.lower().endswith(".pdf"):
            raise ValueError("expected_pdf_but_response_is_not_pdf")
        elif data.startswith(b"PK\x03\x04"):
            _docx_extract(data, url, result)
        elif (
            mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or urlsplit(url).path.lower().endswith(".docx")
        ):
            raise ValueError("expected_DOCX_but_response_is_not_DOCX")
        elif mime in {"text/plain", "text/csv"}:
            text = data.decode("utf-8-sig")
            result.update(
                extracted_text=text,
                units=[{"part": "document", "text": text}],
                extraction_status="extracted",
                fidelity_status="exact_utf8_decoding",
            )
            if not text.strip():
                result["extraction_status"] = "partial"
                result["extraction_gaps"].append("empty_text_document_requires_review")
            result["fidelity_gaps"].append("document_purpose_and_structure_not_verified")
        elif mime == "text/html" or data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            text = data.decode("utf-8")
            parser = _Links()
            parser.feed(text)
            visible = "".join(parser.text).strip()
            result.update(
                extracted_text=visible,
                units=[{"part": "html", "text": visible}],
                extraction_status="partial",
                document_links=_links_from_text(
                    text, url, excluded=result["excluded_document_links"]
                ),
            )
            result["extraction_gaps"].append("HTML_wrapper_is_not_verified_full_document")
            result["discovery_gaps"].append("dynamic_download_controls_or_rendered_text_unverified")
        else:
            result["extraction_gaps"].append("unsupported_format_requires_dedicated_extractor")
    except Exception as exc:
        result["extraction_status"] = "failed"
        result["extraction_gaps"].append(type(exc).__name__ + ": " + str(exc))
    result["text_sha256"] = _sha(result["extracted_text"].encode())
    if result["document_links"]:
        result["discovery_gaps"].append("nested_document_purpose_and_retrieval_unresolved")
    return result
