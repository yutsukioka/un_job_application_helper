#!/usr/bin/env python3
"""Fill only the verified UNESCO EHF template; never rewrite its other parts."""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile

from lxml import etree as ET

SKILL = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL / "assets/unesco-employment-history-2025-02.docx"
SHA256 = "364ed4385a5542fde650a7e7bab5b7e03fae9792ad668e69f3d0b1e21194a30c"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
SLOTS = {"from_date": (2, 0), "to_date": (2, 1), "job_title": (2, 2),
         "employer": (3, 0), "location": (3, 1), "annual_salary": (4, 0),
         "direct_reports": (4, 1), "un_grade": (5, 0)}
JOB_KEYS = set(SLOTS) | {"responsibilities", "achievements"}


def q(name):
    return f"{{{W}}}{name}"


def checked_text(value, path, missing, paragraphs=False):
    if value is None or value == "" or value == []:
        missing.append(path)
        return [] if paragraphs else ""
    values = value if paragraphs and isinstance(value, list) else [value]
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise ValueError(f"{path}: supply nonempty text, text paragraphs, or null")
    for item in values:
        if any(ord(c) < 32 for c in item) or any(0xD800 <= ord(c) <= 0xDFFF for c in item):
            raise ValueError(f"{path}: control characters, tabs, newlines or surrogates are unsupported; use paragraph arrays")
        if re.search(r"\[(?:confirm\b|placeholder\b|user\s+to\s+insert\b|select\s+one\b)", item, re.I):
            raise ValueError(f"{path}: keep unresolved placeholders in the review, not in the form")
    return values if paragraphs else values[0]


def prepare(data):
    if not isinstance(data, dict) or set(data) != {"first_name", "last_name", "jobs"}:
        raise ValueError("Input must have exactly first_name, last_name and jobs")
    missing, names, jobs, warnings = [], {}, [], []
    for key in ("first_name", "last_name"):
        names[key] = checked_text(data[key], key, missing)
        # Conservative UTF-16 count: the native Word limit's Unicode semantics
        # have not been independently tested. Never truncate a legal name.
        if len(names[key].encode("utf-16-le")) // 2 > 20:
            raise ValueError(f"{key}: exceeds the template's 20-unit conservative name check; do not abbreviate without applicant direction")
    if not isinstance(data["jobs"], list) or not data["jobs"]:
        raise ValueError("jobs must be a nonempty list in present/recent-first order")
    parsed = []
    for index, job in enumerate(data["jobs"], 1):
        if not isinstance(job, dict) or set(job) != JOB_KEYS:
            raise ValueError(f"jobs[{index}]: expected exactly {', '.join(sorted(JOB_KEYS))}")
        clean = {k: checked_text(job[k], f"jobs[{index}].{k}", missing,
                                  k in {"responsibilities", "achievements"}) for k in job}
        dates = {}
        for key in ("from_date", "to_date"):
            value = clean[key]
            if not value or (key == "to_date" and value == "Present"):
                dates[key] = None
                continue
            if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
                raise ValueError(f"jobs[{index}].{key}: use confirmed DD/MM/YYYY, Present for an ongoing post, or null")
            try:
                dates[key] = datetime.strptime(value, "%d/%m/%Y").date()
            except ValueError as error:
                raise ValueError(f"jobs[{index}].{key}: invalid calendar date") from error
        if all(dates.values()) and dates["from_date"] > dates["to_date"]:
            raise ValueError(f"jobs[{index}]: start date is after end date")
        parsed.append(dates)
        jobs.append(clean)
    for i in range(1, len(jobs)):
        if jobs[i]["to_date"] == "Present" and jobs[i-1]["to_date"] != "Present":
            warnings.append(f"jobs[{i+1}]: ongoing post follows an ended post; review chronology")
        a, b = parsed[i-1]["to_date"], parsed[i]["to_date"]
        if a and b and a < b:
            warnings.append(f"jobs[{i+1}]: end date is more recent than preceding post; review chronology")
    return names, jobs, missing, warnings


def cell(table, row, col):
    return table.findall("w:tr", NS)[row].findall("w:tc", NS)[col]


def fill_field(tc, value):
    """Patch cached result text, retaining each form field's structure."""
    inside, results = False, []
    for node in tc.iter():
        if node.tag == q("fldChar"):
            kind = node.get(q("fldCharType"))
            if kind == "separate":
                inside = True
            elif kind == "end":
                inside = False
        elif inside and node.tag == q("t"):
            results.append(node)
    if len(tc.findall(".//w:ffData", NS)) != 1 or not results:
        raise ValueError("Unsupported form-field structure")
    for i, node in enumerate(results):
        node.text = value if i == 0 else ""
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def fill_paragraphs(tc, values, skip):
    slots = tc.findall("w:p", NS)[skip:]
    if not slots or any(p.findall(".//w:t", NS) for p in slots):
        raise ValueError("Unsupported narrative paragraph structure")
    pattern = deepcopy(slots[0])
    for i, value in enumerate(values):
        p = slots[i] if i < len(slots) else deepcopy(pattern)
        if i >= len(slots):
            # A new paragraph must not duplicate the source paragraph ID.
            for attr in list(p.attrib):
                if ET.QName(attr).localname in {"paraId", "textId"}:
                    del p.attrib[attr]
            tc.append(p)
        run = ET.SubElement(p, q("r"))
        source_rpr = p.find("w:pPr/w:rPr", NS)
        if source_rpr is not None:
            run.append(deepcopy(source_rpr))
        text = ET.SubElement(run, q("t"))
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text.text = value
    # Blank answer lines are space for the user, not separate form fields.
    # Keep one paragraph when blank, otherwise only the used answer lines.
    for p in slots[max(len(values), 1):]:
        tc.remove(p)


def unique_clone(table, number):
    clone = deepcopy(table)
    bookmarks = {}
    for node in clone.iter():
        for attr in list(node.attrib):
            if ET.QName(attr).localname in {"paraId", "textId"}:
                del node.attrib[attr]
        if node.tag == q("bookmarkStart"):
            old = node.get(q("id"))
            new = str(100000 + number * 100 + len(bookmarks))
            bookmarks[old] = new
            node.set(q("id"), new)
            node.set(q("name"), f"EHF{number}_{node.get(q('name'))}")
        elif node.tag == q("bookmarkEnd"):
            node.set(q("id"), bookmarks[node.get(q("id"))])
    for i, node in enumerate(clone.findall(".//w:ffData/w:name", NS)):
        node.set(q("val"), f"EHF{number}_Field{i+1}")
    return clone


def fill(template, data, output, allow_incomplete=False):
    if template.resolve() == output.resolve():
        raise ValueError("Output must differ from template")
    if output.exists():
        raise ValueError("Output already exists; choose a new path")
    if hashlib.sha256(template.read_bytes()).hexdigest() != SHA256:
        raise ValueError("Template hash differs from verified official template; inspect it separately, do not use this filler")
    names, jobs, missing, warnings = prepare(data)
    if missing and not allow_incomplete:
        raise ValueError("Unresolved fields (use null + --allow-incomplete for a review draft): " + ", ".join(missing))
    if warnings and not allow_incomplete:
        raise ValueError("Chronology needs review: " + "; ".join(warnings))
    parser = ET.XMLParser(resolve_entities=False, no_network=True)
    with ZipFile(template) as source:
        document = ET.fromstring(source.read("word/document.xml"), parser)
        body = document.find("w:body", NS)
        tables = body.findall("w:tbl", NS)
        fill_field(cell(tables[0], 1, 0), names["last_name"])
        fill_field(cell(tables[0], 1, 1), names["first_name"])
        original_jobs = tables[1:]
        extra_job_pattern = deepcopy(original_jobs[-1])
        separators = []
        for table in original_jobs:
            sequence, node = [], table.getnext()
            while node is not None and node.tag not in (q("tbl"), q("sectPr")):
                sequence.append(deepcopy(node))
                node = node.getnext()
            separators.append(sequence)
        tail = list(body).index(original_jobs[0])
        # Replace only the repeatable job area; retain both section properties.
        for node in list(body)[tail:]:
            if node.tag != q("sectPr"):
                body.remove(node)
        for index, job in enumerate(jobs):
            table = original_jobs[index] if index < 5 else unique_clone(extra_job_pattern, index+1)
            # Extra jobs clone a pristine source block, never a populated job.
            heading_text = cell(table, 0, 0).findall(".//w:t", NS)
            for j, node in enumerate(heading_text):
                node.text = f"WORK EXPERIENCE {index+1}:" if j == 0 else ""
            for key, location in SLOTS.items():
                fill_field(cell(table, *location), job[key])
            fill_paragraphs(cell(table, 7, 0), job["responsibilities"], 0)
            fill_paragraphs(cell(table, 8, 0), job["achievements"], 1)
            body.insert(len(body)-1, table)
            # Retain each source block's complete native separator sequence.
            for separator in separators[min(index, len(separators)-1)]:
                node = separator if index < len(original_jobs) else unique_clone(separator, index+1)
                body.insert(len(body)-1, node)
        settings = ET.fromstring(source.read("word/settings.xml"), parser)
        update = settings.find("w:updateFields", NS)
        if update is None:
            update = ET.SubElement(settings, q("updateFields"))
        update.set(q("val"), "true")
        replacements = {"word/document.xml": ET.tostring(document, xml_declaration=True, encoding="UTF-8", standalone=True),
                        "word/settings.xml": ET.tostring(settings, xml_declaration=True, encoding="UTF-8", standalone=True)}
        output.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output, "x") as dest:
            for info in source.infolist():
                dest.writestr(info, replacements.get(info.filename, source.read(info.filename)))
    with ZipFile(template) as a, ZipFile(output) as b:
        changed = [name for name in a.namelist() if a.read(name) != b.read(name)]
        if set(changed) - {"word/document.xml", "word/settings.xml"}:
            raise ValueError("Unexpected package change")
    return {"status": "incomplete_review_draft" if missing or warnings else "filled_pending_content_and_visual_review",
            "templateSha256": SHA256, "jobCount": len(jobs), "unresolvedFields": missing,
            "chronologyWarnings": warnings, "changedPackageParts": changed,
            "nameLimit": {"maxLength": 20, "checkUnit": "utf16_conservative", "normalization": "none"},
            "fieldRefresh": "Word update requested on open; Word refresh not performed", "visualQA": "not_performed_by_this_helper"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--template", type=Path, default=TEMPLATE)
    ap.add_argument("--allow-incomplete", action="store_true")
    args = ap.parse_args()
    try:
        report = fill(args.template, json.loads(args.input.read_text(encoding="utf-8")), args.output, args.allow_incomplete)
    except (ValueError, KeyError, IndexError, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
