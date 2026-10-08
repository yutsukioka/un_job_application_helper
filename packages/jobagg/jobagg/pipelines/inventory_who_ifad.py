"""Strict captured inventories for WHO's English board and IFAD's empty search.

No requests are made. Provider totals, native IDs and configured scope must agree;
IFAD's 100-result ceiling and inconsistent WHO totals never establish absence.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
import gzip
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit
import zlib

from jobagg.pipelines.inventory_api_contracts import jobs_by_id, require, url_signature

WHO_API = "https://careers.who.int/careersection/rest/jobboard/searchjobs?lang=en&portal=101430233"
IFAD_ROUTE = "/psc/IFHRPRDE/CAREERS/JOBS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"
IFAD_LIST = "https://job.ifad.org" + IFAD_ROUTE + "?Page=HRS_APP_SCHJOB_FL&Action=U"
MAX_BODY = 4 * 1024 * 1024
MAX_TOTAL_BODY = 16 * 1024 * 1024


def _count(value, label):
    require(type(value) is int and value >= 0, "Invalid " + label)
    return value


def _signature(url):
    parts = urlsplit(str(url))
    query = parse_qsl(parts.query, keep_blank_values=True)
    require(len({k for k, _ in query}) == len(query), "Duplicate census URL parameter")
    return url_signature(url)


def _metadata(path):
    path = Path(path)
    require(path.stat().st_size <= 256 * 1024, "Census metadata exceeds read bound")
    meta = json.loads(path.read_bytes(), object_pairs_hook=_object)
    require(isinstance(meta, dict), "Census metadata is not an object")
    return meta


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate census JSON member")
        result[key] = value
    return result


def _body(source, path, meta, endpoint, method, result, request=None):
    binding, phase = meta.get("source_binding"), meta.get("phase")
    require(
        isinstance(binding, dict)
        and binding.get("source_id") == source.id
        and binding.get("ats_family") == source.ats_family,
        "Census capture has missing/wrong source binding",
    )
    require(
        isinstance(phase, dict)
        and phase.get("kind") == "listing"
        and phase.get("job_id") is None
        and meta.get("external_id") is None,
        "Census capture is not a source listing",
    )
    require(
        meta.get("method") == method
        and meta.get("status_code") == 200
        and meta.get("state") == "response_captured"
        and meta.get("body_captured") is True,
        "Census requires a successful captured response",
    )
    require(
        _signature(meta.get("url")) == _signature(endpoint) == _signature(meta.get("response_url")),
        "Census endpoint/filter/locale differs",
    )
    for name, url_name in (("request_url_sha256", "url"), ("response_url_sha256", "response_url")):
        require(
            meta.get(name) == hashlib.sha256(meta[url_name].encode()).hexdigest(),
            "Census URL hash differs",
        )
    expected = (
        hashlib.sha256(json.dumps(request, separators=(",", ":")).encode()).hexdigest()
        if request is not None
        else None
    )
    require(meta.get("request_body_sha256") == expected, "Census request body/filter/page differs")
    stamps = [
        datetime.fromisoformat(str(meta.get(k, "")).replace("Z", "+00:00"))
        for k in ("started_at", "finished_at")
    ]
    require(
        all(t.tzinfo is not None for t in stamps) and stamps[0] <= stamps[1],
        "Census capture timestamps are invalid",
    )
    if result["capture_paths"]:
        require(
            stamps[0] >= datetime.fromisoformat(result["finished_at"]),
            "Census captures are out of order or overlap",
        )
    artifact = Path(meta["artifact"])
    require(artifact.stat().st_size <= MAX_BODY, "Compressed census exceeds read bound")
    with gzip.open(artifact, "rb") as stream:
        body = stream.read(MAX_BODY + 1)
    result["body_bytes_verified"] += len(body)
    require(
        len(body) <= MAX_BODY and result["body_bytes_verified"] <= MAX_TOTAL_BODY,
        "Census body exceeds read bound",
    )
    require(
        meta.get("body_bytes") == len(body)
        and meta.get("body_sha256") == hashlib.sha256(body).hexdigest(),
        "Census response size/hash differs",
    )
    result["capture_paths"].append(
        {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    )
    result.setdefault("started_at", stamps[0].isoformat())
    result["finished_at"] = stamps[1].isoformat()
    return body.decode("utf-8")


def _who(source, jobs, captures, result):
    require(
        source.ats_family == "taleo"
        and _signature(source.base_url)
        == _signature("https://careers.who.int/careersection/ex/jobsearch.ftl"),
        "Unsupported WHO board",
    )
    endpoint = source.extra.get("search_api_url")
    require(_signature(endpoint) == _signature(WHO_API), "Unsupported WHO portal/locale")
    template = source.extra.get("search_payload")
    require(
        isinstance(template, dict) and template.get("pageNo") == 1,
        "WHO census requires configured page-one payload",
    )
    fields = template.get("fieldData", {})
    selection = template.get("filterSelectionParam")
    require(isinstance(selection, dict), "WHO filter declaration missing")
    filters = selection.get("searchFilterSelections")
    require(
        isinstance(fields, dict)
        and fields.get("valid") is True
        and fields.get("fields") == {"KEYWORD": "", "LOCATION": ""}
        and isinstance(filters, list)
        and all(isinstance(f, dict) and f.get("selectedValues") == [] for f in filters),
        "WHO census requires the unfiltered board",
    )
    advanced = template.get("advancedSearchFiltersSelectionParam", {"searchFilterSelections": []})
    require(
        isinstance(advanced, dict)
        and isinstance(advanced.get("searchFilterSelections"), list)
        and all(
            isinstance(f, dict) and f.get("selectedValues") == []
            for f in advanced["searchFilterSelections"]
        ),
        "WHO advanced search filters are selected or ambiguous",
    )
    pages, total, size, found, native_ids = 0, None, None, {}, set()
    for path, meta in captures:
        if urlsplit(str(meta.get("url", ""))).path != urlsplit(WHO_API).path:
            continue  # The public HTML warmup is not a REST census page.
        require(total is None or pages * size < total, "WHO page follows terminal census")
        request = deepcopy(template)
        request["pageNo"] = pages + 1
        payload = json.loads(
            _body(source, path, meta, endpoint, "POST", result, request), object_pairs_hook=_object
        )
        require(
            isinstance(payload, dict) and payload.get("careerSectionUnAvailable") is False,
            "WHO career section is unavailable or ambiguous",
        )
        paging = payload.get("pagingData", {})
        require(isinstance(paging, dict), "WHO paging data missing")
        current_total = _count(paging.get("totalCount"), "WHO total")
        current_size = _count(paging.get("pageSize"), "WHO page size")
        require(
            current_size > 0 and _count(paging.get("currentPageNo"), "WHO page") == pages + 1,
            "WHO page skipped, duplicated or resized",
        )
        if total is not None:
            require((total, size) == (current_total, current_size), "WHO total/page size changed")
        total, size = current_total, current_size
        result["reported_total"] = total
        rows = payload.get("requisitionList")
        require(isinstance(rows, list), "WHO requisition list missing")
        require(
            len(rows) == min(size, max(0, total - pages * size)),
            "WHO returned row count disagrees with advertised page/total",
        )
        facets = payload.get("facetResults")
        require(isinstance(facets, list), "WHO posting-language facet missing")
        locales = [f for f in facets if isinstance(f, dict) and f.get("id") == "JOB_LOCALE"]
        require(
            len(locales) == 1 and isinstance(locales[0].get("facetValueResults"), list),
            "WHO posting-language facet ambiguous",
        )
        values = locales[0]["facetValueResults"]
        require(
            (total == 0 and values == [])
            or (
                len(values) == 1
                and isinstance(values[0], dict)
                and values[0].get("id") == "en"
                and values[0].get("quantity") == str(total)
            ),
            "WHO posting languages/count differ from captured English scope",
        )
        for row in rows:
            require(isinstance(row, dict), "Malformed WHO row")
            identity, native = row.get("contestNo"), row.get("jobId")
            require(
                isinstance(identity, str)
                and re.fullmatch(r"\d{7}", identity)
                and isinstance(native, str)
                and native.isdigit(),
                "WHO native identity missing",
            )
            require(identity not in found and native not in native_ids, "Duplicate WHO vacancy")
            require(
                isinstance(row.get("column"), list)
                and len(row["column"]) >= 2
                and row["column"][1] == identity,
                "WHO displayed/native identity disagreement",
            )
            found[identity] = row
            native_ids.add(native)
        pages += 1
    require(
        pages > 0
        and pages <= source.extra.get("max_pages", 25)
        and pages * size >= total
        and len(found) == total,
        "WHO census has no complete terminal page",
    )
    actual = jobs_by_id(source, jobs)
    require(set(actual) == set(found), "WHO parsed IDs differ from captured census")
    for identity, job in actual.items():
        require(
            job.raw.get("contestNo") == identity
            and job.raw.get("jobId") == found[identity]["jobId"],
            "WHO parsed native identity differs",
        )
    result.update(complete=True, page_count=pages, verified_zero=not found)


@dataclass
class _Node:
    tag: str
    attrs: dict
    parent: object = None
    children: list = field(default_factory=list)
    closed: bool = False

    def text(self):
        if self.tag in {"script", "style", "template"}:
            return ""
        return " ".join(x.text() if isinstance(x, _Node) else x for x in self.children)


class _Board(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, closed=True)
        self.stack = [self.root]
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        require(len(self.nodes) < 20000 and len(self.stack) < 128, "IFAD DOM exceeds read bound")
        valued = {
            "id",
            "name",
            "value",
            "action",
            "type",
            "style",
            "class",
            "aria-hidden",
            "title",
            "aria-label",
        }
        require(
            all(key not in valued or isinstance(value, str) for key, value in attrs),
            "IFAD DOM attribute is missing its required value",
        )
        for key in (
            "id",
            "name",
            "value",
            "action",
            "type",
            "style",
            "class",
            "hidden",
            "aria-hidden",
        ):
            require(sum(k == key for k, _ in attrs) <= 1, "Ambiguous IFAD DOM attribute")
        node = _Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        self.nodes.append(node)
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
            self.stack.append(node)
        else:
            node.closed = True

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack[index].closed = True
                del self.stack[index:]
                break

    def handle_data(self, text):
        self.stack[-1].children.append(text)

    def one(self, identity):
        found = [n for n in self.nodes if n.attrs.get("id") == identity]
        require(
            len(found) == 1 and found[0].closed and _visible(found[0]),
            "IFAD required DOM identity missing/duplicated/hidden: " + identity,
        )
        return found[0]


def _within(node, parent):
    while node is not None:
        if node is parent:
            return True
        node = node.parent
    return False


def _visible(node):
    while node is not None:
        attrs = node.attrs
        style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        if (
            node.tag in {"script", "style", "template"}
            or "hidden" in attrs
            or attrs.get("aria-hidden") == "true"
            or "display:none" in style
            or "visibility:hidden" in style
            or set(attrs.get("class", "").split()) & {"psc_hidden", "psc_force-hidden"}
        ):
            return False
        node = node.parent
    return True


def _text(node):
    return " ".join(node.text().split())


def _ifad(source, jobs, captures, result):
    require(
        source.ats_family == "peoplesoft"
        and _signature(source.base_url) == _signature("https://job.ifad.org" + IFAD_ROUTE),
        "Unsupported IFAD board",
    )
    endpoint = source.extra.get("listing_url")
    require(
        _signature(endpoint) == _signature(IFAD_LIST)
        and source.extra.get("public_job_deeplinks") is True,
        "Unsupported IFAD empty-search scope",
    )
    selected = [
        (p, m)
        for p, m in captures
        if m.get("status_code") == 200 and urlsplit(str(m.get("url", ""))).path == IFAD_ROUTE
    ]
    require(len(selected) == 1, "IFAD single-page census has missing/extra responses")
    path, meta = selected[0]
    html = _body(source, path, meta, endpoint, "GET", result)
    board = _Board()
    try:
        board.feed(html)
    except AssertionError as exc:
        # HTMLParser rejects unknown marked declarations with AssertionError.
        # Malformed provider markup cannot certify absence or fail the task.
        raise ValueError("IFAD census contains malformed declared markup") from exc
    form = board.one("HRS_CG_SEARCH_FL")
    require(
        form.tag == "form"
        and form.attrs.get("name") == "win0"
        and _signature(form.attrs.get("action")) == _signature("https://job.ifad.org" + IFAD_ROUTE),
        "IFAD guest form route differs",
    )
    search = board.one("HRS_SCH_WRK_HRS_SCH_TEXT100")
    require(
        _within(search, form) and search.tag == "input" and search.attrs.get("value") == "",
        "IFAD keyword filter is nonempty or missing",
    )
    for node in board.nodes:
        attrs = node.attrs
        if _within(node, form) and attrs.get("id", "").startswith("PTS_SELECT$"):
            require(
                "checked" not in attrs
                and ("$chk$" not in attrs["id"] or attrs.get("value", "") in ("", "N")),
                "IFAD facet filter is selected",
            )
    count = board.one("win0divHRS_SCH_WRK_FLU_HRS_SES_CNTS_MSG")
    grid = board.one("win0divHRS_AGNT_RSLT_I$0")
    rowcount = board.one("win0divHRS_AGNT_RSLT_Irowcnt$0")
    require(
        all(_within(n, form) for n in (count, grid)) and _within(rowcount, grid),
        "IFAD counts/grid are outside the public form",
    )
    match = re.fullmatch(r"(\d+) jobs? found\.", _text(count))
    rows_match = re.fullmatch(r"(\d+) rows?", _text(rowcount))
    require(match and rows_match, "IFAD explicit result counts missing")
    total = int(match[1])
    result["reported_total"] = total
    require(total < 100, "IFAD empty-search 100-result ceiling prevents completeness")
    require(int(rows_match[1]) == total, "IFAD result and grid counts differ")
    rows = [
        n
        for n in board.nodes
        if n.tag == "li" and _within(n, grid) and "ps_grid-row" in n.attrs.get("class", "").split()
    ]
    require(
        len(rows) == total and all(n.closed and _visible(n) for n in rows),
        "IFAD grid rows truncated or hidden",
    )
    found = {}
    for index, row in enumerate(rows):
        require(
            row.attrs.get("id") == f"HRS_AGNT_RSLT_I$0_row_{index}",
            "IFAD row index is duplicated or skipped",
        )
        identity = board.one(f"HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID${index}")
        title = board.one(f"SCH_JOB_TITLE${index}")
        require(
            _within(identity, row) and _within(title, row),
            "IFAD row/native identity binding differs",
        )
        key = _text(identity)
        require(
            key.isdigit() and key not in found and _text(title),
            "IFAD native identity/title missing or duplicated",
        )
        found[key] = _text(title)
    for node in board.nodes:
        if _within(node, grid) and _visible(node) and node.tag in {"a", "button"}:
            label = " ".join(
                (node.attrs.get("title", ""), node.attrs.get("aria-label", ""), _text(node))
            )
            require(
                not re.search(r"\b(next|last|view all|show more)\b", label, re.I)
                or "disabled" in node.attrs
                or "psc_disabled" in node.attrs.get("class", "").split(),
                "IFAD result grid still has enabled pagination",
            )
    actual = jobs_by_id(source, jobs)
    require(set(actual) == set(found), "IFAD parsed IDs differ from native result grid")
    for identity, job in actual.items():
        require(
            job.raw.get("job_id") == identity and job.title == found[identity],
            "IFAD parsed ID/title differs from native row",
        )
    result.update(complete=True, page_count=1, verified_zero=not found, result_ceiling=100)


def verify_who_ifad_listing(source, jobs, capture_paths):
    """Return incomplete on insufficient/contradictory evidence, never guess totals."""
    result = {
        "complete": False,
        "method": "who_rest_en_v1" if source.id == "who_taleo" else "ifad_public_grid_v1",
        "observed_count": len(jobs),
        "scope": "Configured public unfiltered board; main text is separate",
        "reasons": [],
        "capture_paths": [],
        "body_bytes_verified": 0,
    }
    try:
        require(source.id in {"who_taleo", "ifad_peoplesoft"}, "Unsupported inventory source")
        paths = list(capture_paths)
        require(0 < len(paths) <= 64, "Census capture count exceeds read bound or is empty")
        captures = [(p, _metadata(p)) for p in paths]
        result["scope_signature"] = hashlib.sha256(
            json.dumps(
                {
                    "source_id": source.id,
                    "base_url": source.base_url,
                    "search_api_url": source.extra.get("search_api_url"),
                    "search_payload": source.extra.get("search_payload"),
                    "listing_url": source.extra.get("listing_url"),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        (_who if source.id == "who_taleo" else _ifad)(source, jobs, captures, result)
    except (ValueError, KeyError, TypeError, OSError, OverflowError, EOFError, zlib.error) as exc:
        result["complete"] = False
        result["reasons"].append(str(exc))
    return result
