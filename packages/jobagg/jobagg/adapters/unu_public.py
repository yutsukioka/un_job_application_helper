"""UNU's rendered vacancy header and explicitly labelled public date fields."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jobagg.models import JobRecord
from jobagg.normalize import clean_text

MARKER = "_unu_public_field_resolution"


@dataclass
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def raw_text(self):
        return "".join(c.raw_text() if isinstance(c, _Node) else c for c in self.children)

    def text(self):
        return " ".join(self.raw_text().split())

    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.nodes()


class _DOM(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _one(nodes, name, required=True):
    if len(nodes) > 1 or (required and not nodes):
        raise ValueError("UNU public field missing or ambiguous: " + name)
    return nodes[0] if nodes else None


def _section(blocks, label):
    matches = [i for i,n in enumerate(blocks) if n.text().rstrip(":").casefold() == label.casefold()
               or n.text().casefold().startswith(label.casefold()+":")]
    if len(matches) > 1:
        raise ValueError("UNU ambiguous public section: " + label)
    if not matches:
        return []
    index = matches[0]
    text = blocks[index].text()
    if text.casefold().startswith(label.casefold()+":") and text[len(label)+1:].strip():
        return [text[len(label)+1:].strip()]
    paragraphs = []
    for node in blocks[index+1:]:
        text = node.text()
        if node.tag in {"h2", "h3", "h4"} or re.match(
                r"(?:Application Deadline|Duration of contract|Remuneration|Expected start date|How to Apply|Assessment)(?::|$)",text,re.I):
            break
        if node.tag == "p" and text:
            paragraphs.append(text)
    return paragraphs


def _deadline(paragraphs):
    text = " ".join(paragraphs)
    result = {"public_paragraphs":paragraphs, "kind":"missing_or_unparsed_public_deadline",
              "utc_resolved":False, "closes_at":None, "closes_at_local":None, "closes_tz":None}
    if text.casefold() in {"open.", "open"}:
        result["kind"] = "explicitly_open"
        return result
    match = re.match(r"(\d{1,2} [A-Za-z]+ \d{4}|[A-Za-z]+ \d{1,2}, \d{4})(?=\s|\(|$)",text)
    if not match:
        return result
    try:
        calendar = datetime.strptime(match[1], "%B %d, %Y" if "," in match[1] else "%d %B %Y")
    except ValueError:
        return result
    result.update(kind="public_calendar_date_only", closes_at_local=calendar.date().isoformat(), public_date=match[1])
    remaining = text[match.end():].strip()
    # These are complete observed publisher formulations, not job-prose date searches.
    clock = re.fullmatch(r"Submissions must be received by (\d{2}:\d{2}) (MYT \(Malaysia time zone\)|JST) on the deadline; late entries will not be accepted\.",remaining)
    if clock:
        clock_text, label = clock[1], clock[2]
        zone_name = "Asia/Kuala_Lumpur" if label.startswith("MYT") else "Asia/Tokyo"
        zone = ZoneInfo(zone_name)
        basis = "Explicit Malaysia or Japan public civil-time label"
    elif (clock := re.fullmatch(r"\((\d{2}:\d{2}) JST\)",remaining)):
        clock_text, label, zone_name, zone = clock[1], "JST", "Asia/Tokyo", ZoneInfo("Asia/Tokyo")
        basis = "Explicit Japan public civil-time label"
    elif (clock := re.fullmatch(r"Submissions must be received by (\d{2})\.(\d{2}), UTC \+(\d{1,2}) on the deadline; late entries will not be accepted\.",remaining)):
        clock_text, label = clock[1]+":"+clock[2], "UTC +"+clock[3]
        zone_name = "UTC+"+clock[3].zfill(2)+":00"
        if int(clock[3]) >= 24:
            return result
        zone = timezone(timedelta(hours=int(clock[3])))
        basis = "Explicit numeric public UTC offset"
    elif (clock := re.fullmatch(r"\((\d{1,2}):(\d{2})(am|pm), Macau time zone GMT \+(\d{1,2})\)",remaining,re.I)):
        if not 1 <= int(clock[1]) <= 12 or int(clock[4]) >= 24:
            return result
        clock_text = str(int(clock[1])%12 + (12 if clock[3].lower()=="pm" else 0)).zfill(2)+":"+clock[2]
        label = "Macau time zone GMT +"+clock[4]
        zone_name = "UTC+"+clock[4].zfill(2)+":00"
        zone = timezone(timedelta(hours=int(clock[4])))
        basis = "Explicit numeric public GMT offset"
    else:
        if remaining:
            result.update(kind="unparsed_public_clock_or_timezone", public_unresolved_suffix=remaining)
        return result
    try:
        local = calendar.replace(hour=int(clock_text[:2]), minute=int(clock_text[3:]))
    except ValueError:
        return result
    result.update(kind="explicit_public_clock_and_timezone", utc_resolved=True,
                  closes_at=local.replace(tzinfo=zone).astimezone(UTC).isoformat(),
                  closes_at_local=local.isoformat(timespec="minutes"), closes_tz=zone_name,
                  public_timezone=label, timezone_mapping_basis=basis)
    return result


def public_fields(html_text: str, page_url: str) -> dict[str, Any]:
    url = urlsplit(page_url)
    if (url.scheme != "https" or url.netloc != "careers.unu.edu"
            or not re.fullmatch(r"/o/[a-z0-9-]+",url.path) or url.query or url.fragment):
        raise ValueError("UNU metadata requires the canonical public vacancy URL")
    nodes = list(_DOM(html_text).root.nodes())
    canonical = _one([n for n in nodes if n.tag=="link" and n.attrs.get("rel")=="canonical"],"canonical")
    if canonical.attrs.get("href") != page_url:
        raise ValueError("UNU canonical URL differs")
    title_node = _one([n for n in nodes if n.tag=="h1"],"public title")
    title = title_node.text()
    header_parent = _one([n for n in nodes if any(c is title_node for c in n.children)],"title container")
    header_lists = [c for c in header_parent.children if isinstance(c,_Node) and c.tag=="ul"]
    header_items = [n.text() for u in header_lists for n in u.children if isinstance(n,_Node) and n.tag=="li"]
    scripts = [n for n in nodes if n.tag=="script" and n.attrs.get("type")=="application/ld+json"]
    payloads = [json.loads("".join(n.children)) for n in scripts]
    posting = _one([p for p in payloads if isinstance(p,dict) and p.get("@type")=="JobPosting"],"JobPosting")
    if clean_text(posting.get("title")) != title:
        raise ValueError("UNU public header and publisher title differ")
    panel = _one([n for n in nodes if n.attrs.get("role")=="tabpanel" and "hidden" not in n.attrs],"visible detail panel")
    department = _one([n for n in nodes if n.attrs.get("data-cy")=="department-name"],"department",False)
    locations = [n.text() for n in nodes if n.attrs.get("data-testid")=="styled-location-list-item"]
    if len(locations) != len(set(locations)):
        raise ValueError("UNU duplicated location nodes")
    blocks = [n for n in panel.nodes() if n.tag in {"h2","h3","h4","p"}]
    deadline = _deadline(_section(blocks,"Application Deadline"))
    contract = _section(blocks,"Duration of contract") or _section(blocks,"Duration")
    contract_text = " ".join(contract)
    if contract_text.startswith("This is a full-time, fixed-term appointment."):
        employment = "full-time, fixed-term appointment"
    elif "This is Personnel Service Agreement (PSA) contract with UNU" in contract_text:
        employment = "Personnel Service Agreement (PSA)"
    elif contract_text.startswith("The successful candidate will be retained under an Academic Affiliation Agreement and/or Consultant Contract (CTC)."):
        employment = "Academic Affiliation Agreement and/or Consultant Contract (CTC)"
    elif title == "Consultant (CTC5)":
        employment = "Consultant"
    elif title.startswith("REMOTE Internship:") and "duration of the internship" in contract_text:
        employment = "Internship"
    else:
        employment = None
    return {"record_kind":"detail","provider":"unu_rendered_public_fields","source_url":page_url,
            "external_id":url.path.rsplit("/",1)[1],"public_title":title,
            "html_sha256":hashlib.sha256(html_text.encode()).hexdigest(),
            "public_department":department.text() if department else None,"public_locations":locations,
            "public_header_items":header_items,
            "public_contract_type":employment,"contract_section_paragraphs":contract,
            "deadline":deadline,"posting_time_resolved":False,"public_posting_time_unknown":True,
            "publisher_date_posted_claim":posting.get("datePosted"),"publisher_valid_through_claim":posting.get("validThrough"),
            "publisher_employment_type_claim":posting.get("employmentType"),
            "publisher_hiring_organization_claim":posting.get("hiringOrganization"),
            "publisher_location_claim":posting.get("jobLocation"),"publisher_identifier":posting.get("identifier")}


def apply_public_fields(job: JobRecord, html_text: str, page_url: str) -> JobRecord:
    if job.source_id != "unu_recruitee":
        return job
    resolution = public_fields(html_text,page_url)
    if job.source_url != page_url or job.apply_url != page_url:
        raise ValueError("UNU record URL differs from its exact public notice")
    # This adapter's list keys are canonical slugs, while JSON-LD supplies a
    # separate numeric publisher identifier. Preserve both without key churn.
    if str(job.external_id) not in {resolution["external_id"],str((resolution["publisher_identifier"] or {}).get("value"))}:
        raise ValueError("UNU detail identity differs from its public slug/identifier")
    job.external_id = resolution["external_id"]
    job.title, job.department = resolution["public_title"], resolution["public_department"]
    job.location = "; ".join(resolution["public_locations"]) or None
    job.employment_type = resolution["public_contract_type"]
    job.posted_at = None
    deadline = resolution["deadline"]
    job.closes_at = datetime.fromisoformat(deadline["closes_at"]) if deadline["utc_resolved"] else None
    job.closes_at_local,job.closes_tz = deadline["closes_at_local"],deadline["closes_tz"]
    job.raw.update(detail_html=html_text,parser="recruitee_public_detail_tab",**{MARKER:resolution})
    return job
