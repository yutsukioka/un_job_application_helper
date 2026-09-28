#!/usr/bin/env python3
"""Apply an explicit, source-locked DOCX edit manifest without rebuilding styles.

This helper does not choose content, resolve factual conflicts, rename originals,
or certify page layout. Use the bundled document runtime and render the result.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as ET

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
      "w14": "http://schemas.microsoft.com/office/word/2010/wordml"}
W = "{" + NS["w"] + "}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(element):
    return ET.tostring(element, method="c14n") if element is not None else b""


def text_nodes(paragraph):
    return paragraph.findall(".//w:t", NS)


def paragraph_text(paragraph):
    return "".join(n.text or "" for n in text_nodes(paragraph))


def formatting(paragraph):
    return (canonical(paragraph.find("w:pPr", NS)),
            [canonical(r.find("w:rPr", NS)) for r in paragraph.findall(".//w:r", NS)])


def resolve(root, xpath):
    found = root.xpath(xpath, namespaces=NS)
    require(len(found) == 1 and isinstance(found[0], ET._Element),
            f"XPath must match one element: {xpath}")
    require(found[0].tag == W + "p", "Only Word paragraphs are supported")
    return found[0]


def require_plain_paragraph(paragraph, *, group_or_clone=False):
    # Cached field results are ordinary direct w:r/w:t children. Checking only
    # the parents of touched text therefore cannot establish safe plain text.
    unsafe = ["drawing", "object", "pict", "fldChar", "instrText", "fldSimple",
              "sdt", "ins", "del", "moveFrom", "moveTo", "pPrChange", "rPrChange",
              "footnoteReference", "endnoteReference", "txbxContent", "altChunk",
              "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
              "customXmlInsRangeStart", "customXmlInsRangeEnd", "customXmlDelRangeStart",
              "customXmlDelRangeEnd", "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
              "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd"]
    if group_or_clone:
        # All original group slots must be safe, including slots removed when
        # the group shrinks; range ends are anchors just as range starts are.
        unsafe += ["bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd",
                   "commentReference", "hyperlink", "sectPr", "br",
                   "permStart", "permEnd"]
    query = " | ".join(".//w:" + name for name in unsafe)
    enclosing = ("ancestor::w:sdt | ancestor::w:ins | ancestor::w:del | "
                 "ancestor::w:moveFrom | ancestor::w:moveTo | ancestor::w:drawing | "
                 "ancestor::w:object | ancestor::w:pict | ancestor::w:txbxContent | "
                 "ancestor::w:fldSimple")
    require(not paragraph.xpath(query + " | " + enclosing, namespaces=NS),
            "Cannot edit or clone a paragraph with fields, revisions, controls, or unsafe rich content")


def replace_text(paragraph, expected, replacement):
    require_plain_paragraph(paragraph)
    require(isinstance(expected, str) and expected, "Expected text must be nonempty")
    require(isinstance(replacement, str) and not any(c in replacement for c in "\r\n\t"),
            "Replacement must be plain text within one paragraph")
    full = paragraph_text(paragraph)
    require(full.count(expected) == 1, "Expected text is absent or ambiguous")
    start = full.index(expected)
    end = start + len(expected)
    nodes, pos, touched = text_nodes(paragraph), 0, []
    for node in nodes:
        value = node.text or ""
        if pos < end and pos + len(value) > start:
            touched.append((node, max(0, start - pos), min(len(value), end - pos)))
        pos += len(value)
    require(touched, "No text nodes in replacement range")
    run_properties = []
    for node, _, _ in touched:
        run = node.getparent()
        require(run.tag == W + "r" and run.getparent() is paragraph,
                "Replacement crosses a hyperlink, field, nested control, or text box; map narrower slots")
        run_properties.append(canonical(run.find("w:rPr", NS)))
    require(len(set(run_properties)) == 1,
            "Replacement crosses differently formatted runs; use narrower replacements")
    old_format = formatting(paragraph)
    remaining = replacement
    for index, (node, lo, hi) in enumerate(touched):
        value = node.text or ""
        segment = remaining if index == len(touched) - 1 else remaining[:hi - lo]
        remaining = remaining[len(segment):]
        node.text = value[:lo] + segment + value[hi:]
        if node.text and (node.text[0].isspace() or node.text[-1].isspace()):
            node.set(XML_SPACE, "preserve")
    require(paragraph_text(paragraph) == full[:start] + replacement + full[end:],
            "Text replacement failed verification")
    require(formatting(paragraph) == old_format, "Run or paragraph formatting changed")


def fresh_clone(paragraph, seed):
    require_plain_paragraph(paragraph, group_or_clone=True)
    result = copy.deepcopy(paragraph)
    for key in ["paraId", "textId"]:
        attr = "{" + NS["w14"] + "}" + key
        if attr in result.attrib:
            result.set(attr, hashlib.sha256((seed + key).encode()).hexdigest()[:8].upper())
    return result


def assert_native_dash(paragraph, numbering):
    np = paragraph.find("w:pPr/w:numPr", NS)
    require(np is not None and numbering is not None, "Bullet must have native Word numbering")
    nid = np.find("w:numId", NS)
    require(nid is not None, "Bullet has no numbering ID")
    il = np.find("w:ilvl", NS)
    level = il.get(W + "val") if il is not None else "0"
    n = numbering.xpath("./w:num[@w:numId=$n]", namespaces=NS, n=nid.get(W + "val"))
    require(len(n) == 1, "Unresolved numbering ID")
    aid = n[0].find("w:abstractNumId", NS)
    require(aid is not None, "No abstract numbering ID")
    levels = n[0].xpath("./w:lvlOverride[@w:ilvl=$l]/w:lvl", namespaces=NS, l=level)
    if not levels:
        levels = numbering.xpath("./w:abstractNum[@w:abstractNumId=$a]/w:lvl[@w:ilvl=$l]",
                                 namespaces=NS, a=aid.get(W + "val"), l=level)
    require(len(levels) == 1, "Unresolved list level")
    fmt, marker = levels[0].find("w:numFmt", NS), levels[0].find("w:lvlText", NS)
    require(fmt is not None and fmt.get(W + "val") == "bullet" and
            marker is not None and marker.get(W + "val") == "-", "List is not a native dash bullet")


def adapt(source, manifest, output):
    original_bytes = source.read_bytes()
    require(hashlib.sha256(original_bytes).hexdigest() == manifest.get("source_sha256"),
            "Source hash changed; inspect the latest source before editing")
    require(source.resolve() != output.resolve() and not output.exists(),
            "Output must be a new file; original and existing output cannot be overwritten")
    parser = ET.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
    with ZipFile(source) as zin:
        parts = {info.filename: zin.read(info.filename) for info in zin.infolist()}
        require(len(parts) == len(zin.infolist()), "Duplicate ZIP members are unsupported")
        roots, plans, modified, changed_nodes = {}, [], set(), set()
        numbering = ET.fromstring(parts["word/numbering.xml"], parser) if "word/numbering.xml" in parts else None
        for op_index, op in enumerate(manifest.get("operations", [])):
            part = op["part"]
            require(part in parts and part.startswith("word/") and part.endswith(".xml"), "Invalid editable part")
            if part not in roots:
                roots[part] = ET.fromstring(parts[part], parser)
            root, kind = roots[part], op["type"]
            if kind == "replace_text":
                nodes = [resolve(root, op["xpath"])]
            elif kind == "replace_paragraph_group":
                nodes = [resolve(root, x) for x in op["xpaths"]]
                require(nodes and len(nodes) == len(op["expected"]), "Group expectation length mismatch")
                require(len(set(nodes)) == len(nodes), "Repeated group paragraph")
                require(all(a.getnext() is b for a, b in zip(nodes, nodes[1:])), "Group is not contiguous")
                require(op["kind"] in ["bullet", "text"] and op["replacements"], "Invalid paragraph group")
                require([paragraph_text(p) for p in nodes] == op["expected"], "Paragraph group text changed")
                for p in nodes:
                    require_plain_paragraph(p, group_or_clone=True)
                if op["kind"] == "bullet":
                    for p in nodes:
                        assert_native_dash(p, numbering)
                    require(all(not t.startswith(("- ", "• ", "* ")) for t in op["replacements"]),
                            "Do not type bullet markers in replacement text")
            elif kind == "insert_blank_before":
                nodes = [resolve(root, op["anchor_xpath"]), resolve(root, op["template_xpath"])]
                require(op["reason"] in ["organization_boundary", "pagination"], "Unsupported spacer purpose")
                require(isinstance(op["count"], int) and 1 <= op["count"] <= 20, "Invalid spacer count")
                require(not paragraph_text(nodes[1]).strip() and nodes[1].find("w:pPr/w:numPr", NS) is None,
                        "Spacer template must be empty and unnumbered")
            else:
                raise ValueError(f"Unsupported operation: {kind}")
            if kind != "insert_blank_before":
                require(not changed_nodes.intersection(nodes), "Overlapping edit operations")
                changed_nodes.update(nodes)
            plans.append((op_index, op, nodes))
        require(plans, "Manifest has no operations")
        protected = [(p, canonical(p)) for r in roots.values() for p in r.findall(".//w:p", NS)
                     if p not in changed_nodes]
        for op_index, op, nodes in plans:
            kind = op["type"]
            if kind == "replace_text":
                replace_text(nodes[0], op["expected"], op["replacement"])
            elif kind == "replace_paragraph_group":
                originals = [copy.deepcopy(p) for p in nodes]
                previous = nodes[-1]
                for index, value in enumerate(op["replacements"]):
                    if index < len(nodes):
                        node, old = nodes[index], op["expected"][index]
                    else:
                        node = fresh_clone(originals[-1], f"{op_index}:{index}:{value}")
                        old = paragraph_text(node)
                        previous.addnext(node)
                        previous = node
                    replace_text(node, old, value)
                for node in nodes[len(op["replacements"]):]:
                    node.getparent().remove(node)
            else:
                for i in range(op["count"]):
                    nodes[0].addprevious(fresh_clone(nodes[1], f"blank:{op_index}:{i}"))
            modified.add(op["part"])
        for p, before in protected:
            require(canonical(p) == before, "Protected paragraph changed")
        patched = dict(parts)
        for part in modified:
            patched[part] = ET.tostring(roots[part], encoding="UTF-8", xml_declaration=True,
                                       standalone=roots[part].getroottree().docinfo.standalone)
        require(source.read_bytes() == original_bytes, "Source changed during editing")
        output.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with ZipFile(output, "x") as zout:
                created = True
                zout.comment = zin.comment
                for info in zin.infolist():
                    zout.writestr(info, patched[info.filename])
            with ZipFile(output) as check:
                require(set(check.namelist()) == set(parts), "Package members changed")
                require(all(check.read(name) == value for name, value in parts.items() if name not in modified),
                        "Protected package part changed")
        except Exception:
            if created:
                output.unlink(missing_ok=True)
            raise
    return {"source_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "changed_parts": sorted(modified), "operations": len(plans),
            "protected_paragraphs_verified": len(protected), "status": "PASS",
            "layout_review_required": True}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()
    if args.report:
        require(args.report.resolve() not in {args.input.resolve(), args.output.resolve(), args.manifest.resolve()},
                "Report must differ from input, manifest and output")
        require(not args.report.exists() and not args.report.is_symlink(), "Report must be a new file")
    result = adapt(args.input, json.loads(args.manifest.read_text()), args.output)
    if args.report:
        with args.report.open("x", encoding="utf-8") as report:
            report.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
