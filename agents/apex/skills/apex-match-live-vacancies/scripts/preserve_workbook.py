#!/usr/bin/env python3
"""Inspect a shortlist, then overlay Artifact-authored cells into its native ZIP.

This helper never installs over the source. It authors no cell content: values,
formulas and their calculation caches must come from update_shortlist.mjs.
The ZIP overlay is the preservation fallback for native features that an import /
export round trip can rewrite. It preserves untouched parts byte-for-byte.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import posixpath
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zipfile import ZipFile, ZIP_DEFLATED
from zoneinfo import ZoneInfo

from lxml import etree as E

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"s": S}
T = lambda name: "{" + S + "}" + name
EAT = ZoneInfo("Africa/Nairobi")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def xml(data):
    return E.fromstring(data)


def encode(tree):
    return E.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)


def relpath(path):
    return posixpath.join(posixpath.dirname(path), "_rels", posixpath.basename(path) + ".rels")


def resolve(path, target):
    return target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(path), target))


def colnum(col):
    n = 0
    for ch in col:
        n = 26 * n + ord(ch) - 64
    return n


def splitaddr(addr):
    m = re.fullmatch(r"\$?([A-Z]+)\$?(\d+)", addr)
    if not m:
        raise ValueError(f"Unsupported cell address: {addr}")
    return m.group(1), int(m.group(2))


def canonical_url(url):
    if not url:
        return ""
    p = urlsplit(str(url).strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))


def key(org, job_id):
    return (re.sub(r"\s+", " ", str(org)).strip().upper(), str(job_id).strip())


class Package:
    def __init__(self, data):
        self.zip = ZipFile(io.BytesIO(data))
        self.parts = {name: self.zip.read(name) for name in self.zip.namelist()}
        self.strings = []
        if "xl/sharedStrings.xml" in self.parts:
            self.strings = xml(self.parts["xl/sharedStrings.xml"]).findall(T("si"))
        self.sheets = {}
        workbook = xml(self.parts["xl/workbook.xml"])
        relationships = xml(self.parts["xl/_rels/workbook.xml.rels"])
        targets = {n.get("Id"): resolve("xl/workbook.xml", n.get("Target")) for n in relationships}
        for item in workbook.find(T("sheets")):
            path = targets[item.get("{" + R + "}id")]
            root = xml(self.parts[path])
            cells = {n.get("r"): n for n in root.findall(".//s:sheetData/s:row/s:c", NS)}
            links = {}
            rp = relpath(path)
            rr = xml(self.parts[rp]) if rp in self.parts else E.Element("{" + P + "}Relationships", nsmap={None: P})
            targets2 = {n.get("Id"): n.get("Target") for n in rr}
            for h in root.findall("s:hyperlinks/s:hyperlink", NS):
                links[h.get("ref")] = targets2.get(h.get("{" + R + "}id"), h.get("location", ""))
            self.sheets[item.get("name")] = {"path": path, "root": root, "cells": cells, "links": links, "rels": rr}

    def value(self, sheet, address):
        c = self.sheets[sheet]["cells"].get(address)
        if c is None:
            return None
        if c.find(T("f")) is not None:
            return "=" + (c.find(T("f")).text or "")
        typ = c.get("t")
        if typ == "inlineStr":
            return "".join(c.find(T("is")).itertext())
        v = c.find(T("v"))
        if v is None:
            return None
        if typ == "s":
            return "".join(self.strings[int(v.text)].itertext())
        return v.text


def normalized_date(value, precision=None):
    if value in (None, ""):
        return None, "unknown"
    text = str(value)
    is_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text))
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo:
        dt = dt.astimezone(EAT).replace(tzinfo=None)
    chosen = precision or ("date" if is_date else "time")
    if chosen not in ("date", "time"):
        raise ValueError("date_precision must be date or time for a known deadline")
    if is_date and chosen == "time":
        raise ValueError("A date-only deadline cannot have time precision")
    if chosen == "date":
        if not is_date and any((dt.hour, dt.minute, dt.second, dt.microsecond)):
            raise ValueError("Refusing to discard known deadline time")
        return dt.date().isoformat(), chosen
    return dt.isoformat(timespec="seconds"), chosen


def prepare(args):
    source = Path(args.source).resolve()
    run = Path(args.run_dir).resolve()
    run.mkdir(parents=True, exist_ok=True)
    data = source.read_bytes()
    digest = sha(data)
    curated_bytes = Path(args.curated).read_bytes()
    curated = json.loads(curated_bytes)
    if isinstance(curated, dict):
        curated = curated["records"]
    if not isinstance(curated, list):
        raise ValueError("Curated input must be an array or {records: [...]}.")
    datetime.strptime(args.as_of, "%Y-%m-%d")
    manifest_path = run / "manifest.json"
    if manifest_path.exists():
        prior = json.loads(manifest_path.read_text())
        if prior["source_sha256"] != digest or prior["curated_sha256"] != sha(curated_bytes) or prior["as_of"] != args.as_of:
            raise ValueError("Run directory belongs to a different source/input/date; use a fresh run directory.")
        print(json.dumps({"manifest": str(manifest_path), "reused": True}))
        return
    book = Package(data)
    # Resolve worksheet names by distinctive headers, not numeric sheetN.xml paths.
    roles = {}
    for name in book.sheets:
        if book.value(name, "B9") == "Organization" and book.value(name, "E9") == "Status":
            roles["plan"] = name
        if book.value(name, "O6") == "Vacancy ID" and book.value(name, "Q6") == "Time until close":
            role = "roster" if "roster" in str(book.value(name, "A2")).lower() else "vacancy"
            if role in roles:
                raise ValueError(f"Ambiguous worksheet role: {role}")
            roles[role] = name
    if set(roles) != {"plan", "vacancy", "roster"}:
        raise ValueError("Expected existing plan, vacancy and roster layouts with countdown columns.")
    sheets, existing, urlindex, idindex = {}, [], {}, {}
    for role, name in roles.items():
        start = 10 if role == "plan" else 7
        rows = sorted({splitaddr(a)[1] for a in book.sheets[name]["cells"]
                       if splitaddr(a)[0] == "C" and splitaddr(a)[1] >= start and book.value(name, a)})
        if not rows:
            raise ValueError(f"No template data row found in {name}")
        sheets[name] = {"role": role, "path": book.sheets[name]["path"], "first": start,
                        "last": max(rows), "count": len(rows), "new_last": max(rows), "template": max(rows)}
        if role == "plan":
            continue
        for row in rows:
            item = {"sheet": name, "row": row, "kind": role, "org": book.value(name, f"B{row}"),
                    "id": book.value(name, f"O{row}"), "title": book.value(name, f"C{row}")}
            item["urls"] = [canonical_url(book.sheets[name]["links"].get(f"{c}{row}", "")) for c in ("J", "P")]
            k = key(item["org"], item["id"])
            if k in idindex:
                raise ValueError(f"Existing duplicate organization/ID: {k}")
            idindex[k] = item
            existing.append(item)
            for u in set(filter(None, item["urls"])):
                urlindex.setdefault(u, []).append(item)
    plan_name = roles["plan"]
    plan_rows = list(range(sheets[plan_name]["first"], sheets[plan_name]["last"] + 1))
    for item in existing:
        by_url = [r for r in plan_rows if canonical_url(book.sheets[plan_name]["links"].get(f"C{r}", "")) in item["urls"]
                  and canonical_url(book.sheets[plan_name]["links"].get(f"C{r}", ""))]
        by_title = [r for r in plan_rows if key(book.value(plan_name, f"B{r}"), "")[0] == key(item["org"], "")[0]
                    and book.value(plan_name, f"C{r}") == item["title"]]
        matches = by_url or by_title
        item["plan_row"] = matches[0] if len(matches) == 1 else None
    pending, skipped, batchkeys, batchurls = [], [], set(), set()
    required = ("org", "id", "title", "duty", "grade", "contract", "fit", "why", "gap", "requirements", "prep", "deadline", "evidence", "apply_url", "notice_url")
    for incoming in curated:
        r = deepcopy(incoming)
        for f in ("org", "id"):
            if not str(r.get(f, "")).strip():
                raise ValueError(f"Missing stable identity field: {f}")
        r["id"] = str(r["id"])
        k = key(r["org"], r["id"])
        urls = {canonical_url(r.get(f, "")) for f in ("apply_url", "notice_url")} - {""}
        if k in batchkeys or batchurls.intersection(urls):
            raise ValueError(f"Duplicate curated identity or URL: {k}")
        batchkeys.add(k)
        batchurls.update(urls)
        candidates = ([idindex[k]] if k in idindex else []) + [i for u in urls for i in urlindex.get(u, [])]
        candidates = {(i["sheet"], i["row"]): i for i in candidates}
        if len(candidates) > 1:
            raise ValueError(f"Conflicting ID/URL matches: {k}")
        match = next(iter(candidates.values()), None)
        if match and key(match["org"], match["id"]) != k:
            raise ValueError(f"Supplied organization/ID conflicts with URL identity: {k}")
        action = r.get("action", "append")
        if action not in ("append", "review", "refresh"):
            raise ValueError(f"Unsupported action: {action}")
        if match and action == "append":
            skipped.append({"org": r["org"], "id": r["id"], "reason": "already present", "sheet": match["sheet"], "row": match["row"]})
            continue
        if not match and action != "append":
            raise ValueError(f"Existing-record {action} has no identity match: {k}")
        if action == "append":
            for f in required:
                if f not in r or (f not in ("gap",) and not str(r[f]).strip()):
                    raise ValueError(f"New record {k}: missing {f}")
            if "date" not in r:
                raise ValueError(f"New record {k}: supply date, or explicit null when unknown")
        kind = r.get("kind", match["kind"] if match else "vacancy")
        if kind not in ("vacancy", "roster") or (match and kind != match["kind"]):
            raise ValueError("Unsupported kind or attempted cross-sheet move")
        if r.get("fit") and re.search(r"conditional|eligibility|review", r["fit"], re.I):
            if not str(r.get("gap", "")).strip():
                raise ValueError("Conditional/eligibility review needs a visible, specific gap")
            r["priority"] = "P2"
        elif "fit" in r:
            r["priority"] = r.get("priority") or {"Strong match": "P0", "Good match": "P1"}.get(r["fit"], "P2")
        if r.get("priority") not in (None, "P0", "P1", "P2"):
            raise ValueError("Priority must be P0, P1, or P2")
        if "date" in r:
            detail = r.get("deadline_detail") or {}
            if (detail.get("utc_instant") and detail.get("state") == "future_instant"
                    and isinstance(r["date"], str) and len(r["date"]) == 10):
                raise ValueError(f"{k}: date-only input discards the reviewed deadline timestamp; pass the source instant or resolve the precision conflict")
            r["date_local"], r["date_precision"] = normalized_date(r["date"], r.get("date_precision"))
        r.update({"action": action, "kind": kind, "sheet": roles[kind]})
        if match:
            if match["plan_row"] is None:
                raise ValueError(f"Ambiguous or absent Project Plan identity for {k}")
            r.update({"row": match["row"], "plan_row": match["plan_row"]})
        pending.append(r)
    # Order only appended records; existing rows and their user-entered state never move.
    appended = sorted([r for r in pending if r["action"] == "append"], key=lambda r: (r.get("date_local") or "9999", r["org"], r["id"]))
    for r in appended:
        sheets[r["sheet"]]["new_last"] += 1
        sheets[plan_name]["new_last"] += 1
        r["row"] = sheets[r["sheet"]]["new_last"]
        r["plan_row"] = sheets[plan_name]["new_last"]
    records = [r for r in pending if r["action"] != "append"] + appended
    manifest = {"source": str(source), "source_sha256": digest, "curated": str(Path(args.curated).resolve()),
                "curated_sha256": sha(curated_bytes), "as_of": args.as_of, "roles": roles,
                "sheets": sheets, "records": records, "skipped": skipped,
                "existing_plan_rows": [{"row": r, "status": book.value(plan_name, f"E{r}")} for r in plan_rows],
                "owner": book.value(plan_name, "C6") or book.value(plan_name, "H5") or ""}
    (run / "before.xlsx").write_bytes(data)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({"manifest": str(manifest_path), "appends": len(appended), "reviews": len(records)-len(appended), "skipped": len(skipped)}))


def update_sqref(sqref, first, last, newlast, allowed=None):
    def transform(token):
        m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", token)
        if not m:
            return token
        a, low, b, high = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        if allowed and (a, b) not in allowed:
            return token
        if first <= low <= last and high <= last:
            return f"{a}{low}:{b}{newlast}"
        return token
    return " ".join(transform(x) for x in sqref.split())


def overlay(args):
    run = Path(args.run_dir).resolve()
    manifest = json.loads((run / "manifest.json").read_text())
    original = (run / "before.xlsx").read_bytes()
    if sha(original) != manifest["source_sha256"] or sha(Path(manifest["source"]).read_bytes()) != manifest["source_sha256"]:
        raise ValueError("Source changed during preparation; re-read in a new run directory")
    if sha(Path(manifest["curated"]).read_bytes()) != manifest["curated_sha256"]:
        raise ValueError("Curated input changed after inspection")
    base = Package(original)
    authored = Package((run / "authored.xlsx").read_bytes())
    changes = json.loads((run / "author_manifest.json").read_text())
    parts = dict(base.parts)
    changed_parts = set()
    styles = xml(parts["xl/styles.xml"])
    xfs = styles.find(T("cellXfs"))
    numfmts = styles.find(T("numFmts"))
    if numfmts is None:
        numfmts = E.Element(T("numFmts"), count="0")
        styles.insert(0, numfmts)
    format_ids = {x.get("formatCode"): int(x.get("numFmtId")) for x in numfmts}
    date_styles = {}

    def cell_style(template, fmt=None):
        index = int(template.get("s", "0")) if template is not None else 0
        if not fmt:
            return index
        if (index, fmt) in date_styles:
            return date_styles[index, fmt]
        if fmt not in format_ids:
            fmtid = max([163] + [int(n.get("numFmtId")) for n in numfmts]) + 1
            E.SubElement(numfmts, T("numFmt"), numFmtId=str(fmtid), formatCode=fmt)
            numfmts.set("count", str(len(numfmts)))
            format_ids[fmt] = fmtid
        xf = deepcopy(xfs[index])
        xf.set("numFmtId", str(format_ids[fmt]))
        xf.set("applyNumberFormat", "1")
        for i, candidate in enumerate(xfs):
            if E.tostring(candidate) == E.tostring(xf):
                date_styles[index, fmt] = i
                return i
        xfs.append(xf)
        xfs.set("count", str(len(xfs)))
        date_styles[index, fmt] = len(xfs) - 1
        return len(xfs) - 1

    def author_cell(name, address, style, fmt=None):
        c = authored.sheets[name]["cells"].get(address)
        if c is None:
            c = E.Element(T("c"), r=address)
        else:
            c = deepcopy(c)
        c.set("s", str(cell_style(style, fmt)))
        if c.get("t") == "s":
            v = c.find(T("v"))
            si = deepcopy(authored.strings[int(v.text)])
            si.tag = T("is")
            c.remove(v)
            c.set("t", "inlineStr")
            c.append(si)
        return c

    def replace_cell(root, address, new):
        rownum = str(splitaddr(address)[1])
        data = root.find(T("sheetData"))
        row = data.find(f's:row[@r="{rownum}"]', NS)
        if row is None:
            raise ValueError(f"Missing source row {rownum}")
        old = row.find(f's:c[@r="{address}"]', NS)
        if old is not None:
            row.replace(old, new)
        else:
            row.append(new)
            row[:] = sorted(row, key=lambda c: colnum(splitaddr(c.get("r"))[0]))

    for name, config in changes["sheets"].items():
        meta = manifest["sheets"][name]
        orig = base.sheets[name]
        root = deepcopy(orig["root"])
        data = root.find(T("sheetData"))
        for address in config["updated"]:
            prior = orig["cells"].get(address)
            replace_cell(root, address, author_cell(name, address, prior, config.get("formats", {}).get(address)))
        for rownum in config["appended"]:
            new = E.Element(T("row"), r=str(rownum))
            template = orig["root"].find(f'.//s:row[@r="{meta["template"]}"]', NS)
            for attr, value in template.attrib.items():
                if attr not in ("r", "spans", "hidden"):
                    new.set(attr, value)
            authorrow = authored.sheets[name]["root"].find(f'.//s:row[@r="{rownum}"]', NS)
            if authorrow is None:
                raise ValueError(f"Missing authored row {name}:{rownum}")
            if authorrow.get("ht"):
                new.set("ht", authorrow.get("ht"))
                new.set("customHeight", "1")
            endcol = 30 if meta["role"] == "plan" else 17
            for c in authorrow:
                col = splitaddr(c.get("r"))[0]
                if colnum(col) > endcol:
                    continue
                style = orig["cells"].get(f'{col}{meta["template"]}')
                new.append(author_cell(name, c.get("r"), style, config.get("formats", {}).get(c.get("r"))))
            data.append(new)
        if config["appended"]:
            dim = root.find(T("dimension"))
            oldmax = splitaddr(dim.get("ref").split(":")[-1])[1]
            lastcol = splitaddr(dim.get("ref").split(":")[-1])[0]
            dim.set("ref", f'A1:{lastcol}{max(oldmax, meta["new_last"])}')
            if meta["role"] == "plan":
                for cf in root.findall(T("conditionalFormatting")):
                    cf.set("sqref", update_sqref(cf.get("sqref"), meta["first"], meta["last"], meta["new_last"]))
                dvs = root.find(T("dataValidations"))
                if dvs is not None:
                    for dv in list(dvs):
                        # Extend only the tail of owner/status/priority validation.
                        ranges = dv.get("sqref").split()
                        dv.set("sqref", " ".join(re.sub(rf"([DEF]){meta['last']}$", rf"\g<1>{meta['new_last']}", x) for x in ranges))
                    source_dv = next((d for d in dvs if d.get("sqref", "").startswith("B")), None)
                    if source_dv is not None:
                        newdv = deepcopy(source_dv)
                        newdv.set("sqref", f'B{meta["last"]+1}:B{meta["new_last"]}')
                        for a in list(newdv.attrib):
                            if a.endswith("}uid"):
                                del newdv.attrib[a]
                        orgs = sorted({r["org"] for r in manifest["records"] if r["action"] == "append"})
                        values = ",".join(orgs)
                        # Excel inline validation lists cannot exceed 255 characters.
                        if len(values) <= 253 and not any('"' in o or ',' in o for o in orgs):
                            newdv.find(T("formula1")).text = '"' + values + '"'
                            dvs.append(newdv)
                    dvs.set("count", str(len(dvs)))
            else:
                for cf in root.findall(T("conditionalFormatting")):
                    cf.set("sqref", update_sqref(cf.get("sqref"), meta["first"], meta["last"], meta["new_last"], {("G", "G")}))
                # Resolve native tables through relationships; retain columns/styles.
                relation_targets = {r.get("Id"): resolve(orig["path"], r.get("Target")) for r in orig["rels"]}
                for tp in root.findall("s:tableParts/s:tablePart", NS):
                    tablepath = relation_targets[tp.get("{" + R + "}id")]
                    table = xml(parts[tablepath])
                    start, end = table.get("ref").split(":")
                    if splitaddr(end)[1] != meta["last"]:
                        raise ValueError("Table extent does not match existing detail rows")
                    ref = f'{start}:{splitaddr(end)[0]}{meta["new_last"]}'
                    table.set("ref", ref)
                    if table.find(T("autoFilter")) is not None:
                        table.find(T("autoFilter")).set("ref", ref)
                    parts[tablepath] = encode(table)
                    changed_parts.add(tablepath)
        if config.get("links"):
            relationships = deepcopy(orig["rels"])
            hyperlinks = root.find(T("hyperlinks"))
            if hyperlinks is None:
                raise ValueError("Reference workbook has no hyperlinks container")
            existing_ids = {r.get("Id") for r in relationships}
            for address, url in config["links"].items():
                old = hyperlinks.find(f's:hyperlink[@ref="{address}"]', NS)
                if old is not None:
                    hyperlinks.remove(old)
                stem = "rIdMatch" + sha((name + address + url).encode())[:16]
                rid = stem
                i = 1
                while rid in existing_ids:
                    rid = stem + str(i)
                    i += 1
                existing_ids.add(rid)
                E.SubElement(relationships, "{" + P + "}Relationship", Id=rid, Type=R + "/hyperlink", Target=url, TargetMode="External")
                h = E.SubElement(hyperlinks, T("hyperlink"), ref=address)
                h.set("{" + R + "}id", rid)
            rp = relpath(orig["path"])
            parts[rp] = encode(relationships)
            changed_parts.add(rp)
        parts[orig["path"]] = encode(root)
        changed_parts.add(orig["path"])
    if date_styles:
        parts["xl/styles.xml"] = encode(styles)
        changed_parts.add("xl/styles.xml")
    workbook = xml(parts["xl/workbook.xml"])
    calc = workbook.find(T("calcPr"))
    if calc is None:
        calc = E.SubElement(workbook, T("calcPr"))
    calc.set("fullCalcOnLoad", "1")
    calc.set("forceFullCalc", "1")
    parts["xl/workbook.xml"] = encode(workbook)
    changed_parts.add("xl/workbook.xml")
    dest = run / "verified.xlsx"
    if dest.resolve() == Path(manifest["source"]).resolve():
        raise ValueError("Refusing to overwrite original")
    with ZipFile(dest, "w", ZIP_DEFLATED) as out:
        for info in base.zip.infolist():
            out.writestr(info, parts[info.filename])
        for path in set(parts) - set(base.parts):
            out.writestr(path, parts[path])
    result = Package(dest.read_bytes())
    preserved = 0
    for name, orig in base.sheets.items():
        after = result.sheets[name]
        allow = set(changes["sheets"].get(name, {}).get("updated", []))
        for address, c in orig["cells"].items():
            if address not in allow:
                if E.tostring(c) != E.tostring(after["cells"].get(address)):
                    raise AssertionError(f"Unrequested cell mutation {name}!{address}")
                preserved += 1
        changed_links = changes["sheets"].get(name, {}).get("links", {})
        for address, url in orig["links"].items():
            if address not in changed_links and after["links"].get(address) != url:
                raise AssertionError(f"Existing hyperlink changed: {name}!{address}")
        for tag in ("sheetViews", "mergeCells", "cols", "sheetProtection", "legacyDrawing", "drawing", "pageSetup", "pageMargins"):
            oldnode, newnode = orig["root"].find(T(tag)), after["root"].find(T(tag))
            if (E.tostring(oldnode) if oldnode is not None else None) != (E.tostring(newnode) if newnode is not None else None):
                raise AssertionError(f"Native {tag} changed on {name}")
    for path in set(parts) - changed_parts:
        if path in base.parts and parts[path] != base.parts[path]:
            raise AssertionError(f"Unrequested package part mutation: {path}")
    if list(base.sheets) != list(result.sheets):
        raise AssertionError("Sheet names/order changed")
    for name, config in changes["sheets"].items():
        for addr, url in config.get("links", {}).items():
            if result.sheets[name]["links"].get(addr) != url:
                raise AssertionError(f"New hyperlink mismatch: {name}!{addr}")
        for addr in config.get("formulas", []):
            c = result.sheets[name]["cells"].get(addr)
            if c is None or c.find(T("f")) is None:
                raise AssertionError(f"Lost formula {name}!{addr}")
            if c.get("t") == "e":
                raise AssertionError(f"Formula error {name}!{addr}: {c.find(T('v')).text}")
    for r in manifest["records"]:
        if r["action"] == "append":
            if result.value(manifest["roles"]["plan"], f'E{r["plan_row"]}') not in (None, ""):
                raise AssertionError("New application status must remain blank")
            if result.value(r["sheet"], f'O{r["row"]}') != r["id"]:
                raise AssertionError("New vacancy identity mismatch")
            if result.value(manifest["roles"]["plan"], f'J{r["plan_row"]}') != "='" + r["sheet"].replace("'", "''") + f"'!Q{r['row']}":
                raise AssertionError("New countdown must directly reference the ID-resolved detail row")
        if r["action"] == "append" or (r["action"] == "refresh" and "date_local" in r):
            # Independent date serial check also catches timezone shifts / midnight truncation.
            if r["date_local"] is not None:
                expected = (datetime.fromisoformat(r["date_local"]) - datetime(1899, 12, 30)).total_seconds() / 86400
                for name, address in ((r["sheet"], f'A{r["row"]}'), (manifest["roles"]["plan"], f'H{r["plan_row"]}')):
                    actual = result.value(name, address)
                    if actual is None or abs(float(actual) - expected) > 1 / 86400:
                        raise AssertionError(f"Deadline precision changed: {name}!{address}")
    for item in manifest["existing_plan_rows"]:
        if result.value(manifest["roles"]["plan"], f'E{item["row"]}') != item["status"]:
            raise AssertionError("Existing application status changed")
    if manifest["records"]:
        summary = result.sheets[manifest["roles"]["plan"]]["cells"]["K6"]
        expected = len(manifest["existing_plan_rows"]) + sum(r["action"] == "append" for r in manifest["records"])
        if summary.find(T("v")) is None or float(summary.find(T("v")).text) != expected:
            raise AssertionError("Cached tracked-option count does not reconcile to preserved plus appended rows")
    if sha(Path(manifest["source"]).read_bytes()) != manifest["source_sha256"]:
        raise ValueError("Source changed during overlay; do not install draft")
    checks = {"source_sha256": manifest["source_sha256"], "verified_sha256": sha(dest.read_bytes()),
              "existing_cells_preserved": preserved, "changed_parts": sorted(changed_parts),
              "unchanged_parts": len(parts) - len(changed_parts),
              "new_vacancies": sum(r["action"] == "append" and r["kind"] == "vacancy" for r in manifest["records"]),
              "new_rosters": sum(r["action"] == "append" and r["kind"] == "roster" for r in manifest["records"]),
              "reviewed_existing": sum(r["action"] != "append" for r in manifest["records"]),
              "source_not_overwritten": True, "visual_qa": "pending; run --verify and inspect PNGs",
              "native_engine_recalculation": "not performed; formulas/caches checked and full recalculation requested on open"}
    (run / "preservation_checks.json").write_text(json.dumps(checks, indent=2))
    print(json.dumps(checks))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "overlay"))
    parser.add_argument("--source")
    parser.add_argument("--curated")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--as-of")
    args = parser.parse_args()
    if args.mode == "prepare":
        if not all((args.source, args.curated, args.as_of)):
            parser.error("prepare requires --source, --curated and --as-of")
        prepare(args)
    else:
        overlay(args)


if __name__ == "__main__":
    main()
