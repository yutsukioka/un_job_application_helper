"""World Bank's full public JobPosting notice, including selection criteria."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from html.parser import HTMLParser
import json
import re
from typing import Any
import unicodedata

from jobagg.models import JobRecord, OrganizationSource
from jobagg.normalize import build_job, clean_text


class _PublicText(HTMLParser):
    """Preserve adjacent inline runs and escaped literal prose as displayed."""
    blocks = {"p", "div", "section", "article", "table", "tr", "td", "th", "li", "ul", "ol", "br", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0
        self.headings = []
        self.active_headings = []
        self.feed(body)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1
        if not self.ignored:
            if tag in self.blocks:
                self.parts.append(" ")
            if tag in {"b", "strong", "h1", "h2", "h3", "h4", "h5", "h6"}:
                self.active_headings.append((tag, []))

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.ignored = max(0, self.ignored - 1)
        if not self.ignored:
            if self.active_headings and self.active_headings[-1][0] == tag:
                _, parts = self.active_headings.pop()
                self.headings.append(" ".join("".join(parts).split()))
            if tag in self.blocks:
                self.parts.append(" ")

    def handle_data(self, value):
        if not self.ignored:
            self.parts.append(value)
            for _, parts in self.active_headings:
                parts.append(value)

    def text(self):
        return unicodedata.normalize("NFC", " ".join("".join(self.parts).split()))


def public_notice_sections(description_html: str, title: str) -> tuple[str, str]:
    rendered = _PublicText(description_html)
    text = rendered.text()
    if re.search(r"\bDescription\b", text) and re.search(r"\bSelection Criteria\b", text):
        return text, "description_and_selection_criteria"
    # The captured Young Professionals notice uses these explicit public
    # headings for duties and requirements instead of the ordinary layout.
    headings = [h.replace("\u2019", "'") for h in rendered.headings]
    duties_headings = [h for h in headings if h in {"What You'll Deliver", "What You Will Deliver"}]
    if (title == "WBG Young Professional" and len(duties_headings) == 1 and headings.count("What You'll Bring") == 1):
        normalized_text = text.replace("\u2019", "'")
        duties_heading = duties_headings[0]
        duties = normalized_text.find(duties_heading)
        requirements = normalized_text.find("What You'll Bring", duties + 1)
        if duties >= 0 and requirements > duties + len(duties_heading) and normalized_text[requirements + len("What You'll Bring"):].strip():
            return text, "young_professionals_deliver_and_bring"
    if title == "WBG Young Professional" and not duties_headings and headings.count("What You'll Bring") == 1:
        before_requirements = headings[:headings.index("What You'll Bring")]
        normalized_text = text.replace("\u2019", "'")
        # Two complete public YPP notices omit a separate duties heading. Their
        # exact introduction/rotations/requirements/footer layout is sufficient
        # to preserve everything published; never manufacture a duties section.
        if (before_requirements == [title, "Are you ready for a career with a lasting impact?"]
                and headings.count("WBG Culture Attributes:") == 1
                and all(marker in normalized_text for marker in (
                    "The World Bank Group's Young Professionals Program (YPP)",
                    "initial two-year term contract", "three eight-month rotations",
                    "World Bank Group Core Competencies", "comprehensive benefits",
                ))):
            return text, "young_professionals_requirements_only"
    raise ValueError("World Bank full public notice lacks a recognized duties/requirements layout")


class _PublicPosting(HTMLParser):
    def __init__(self, html_text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.active = False
        self.parts: list[str] = []
        self.postings: list[dict[str, Any]] = []
        self.feed(html_text)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self.active, self.parts = True, []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            value = json.loads("".join(self.parts))
            if isinstance(value, dict) and value.get("@type") == "JobPosting":
                self.postings.append(value)
            self.active = False


class _Tables(HTMLParser):
    def __init__(self, html_text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.table = None
        self.row = None
        self.cell = None
        self.depth = 0
        self.feed(html_text)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.table = []
        if self.depth != 1:
            return
        if tag == "tr":
            self.row = []
        if tag in {"td", "th"}:
            self.cell = []
        if tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data):
        if self.depth == 1 and self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if self.depth == 1:
            if tag in {"td", "th"} and self.cell is not None:
                if self.row is not None:
                    self.row.append(clean_text("".join(self.cell)) or "")
                self.cell = None
            if tag == "tr" and self.row is not None:
                self.table.append(self.row)
                self.row = None
            if tag == "table" and self.table is not None:
                self.tables.append(self.table)
                self.table = None
        if tag == "table":
            self.depth = max(0, self.depth - 1)


def public_metadata(description_html: str, *, public_title: str | None = None) -> dict[str, str]:
    candidates = []
    for table in _Tables(description_html).tables:
        fields = {}
        for row in table:
            if len(row) != 2:
                continue
            label, value = row[0].rstrip(": "), row[1]
            if label in fields and fields[label] != value:
                raise ValueError("Conflicting World Bank public table field")
            fields[label] = value
        if "Job #" in fields:
            candidates.append(fields)
    if len(candidates) != 1:
        raise ValueError("World Bank public metadata table missing or ambiguous")
    fields = candidates[0]
    required = {"Job #", "Organization", "Sector", "Grade", "Recruitment Type", "Location", "Closing Date"}
    if (public_title and public_title.startswith("WBG Pioneer - ")
            and "WBG Pioneers, the World Bank Group’s Internship Program" in _PublicText(description_html).text()):
        # This observed internship template omits Sector, Recruitment Type and
        # Term Duration, and publishes Hiring Manager instead. Preserve absence.
        required = {"Job #", "Organization", "Grade", "Location", "Hiring Manager", "Required Language(s)", "Preferred Language(s)", "Closing Date"}
    if not required <= fields.keys():
        raise ValueError("World Bank public metadata labels are incomplete")
    return fields


def _deadline(value: str | None) -> dict[str, Any]:
    result = {"public_value": value, "kind": "unknown", "utc_resolved": False,
              "closes_at": None, "closes_at_local": None, "closes_tz": None}
    match = re.fullmatch(r"(\d{1,2}/\d{1,2}/\d{4}) \(MM/DD/YYYY\) at (\d{1,2}):(\d{2})(am|pm) UTC", value or "", re.I)
    if not match:
        candidate = re.fullmatch(r"(\d{1,2}/\d{1,2}/\d{4}) \((\d{1,2}):(\d{2})(am|pm) UTC\)", value or "", re.I)
        if candidate:
            month, day, _ = (int(part) for part in candidate[1].split("/"))
            # The observed internship label omits the explicit date mask.
            # 9/30 is unambiguous; do not guess a future 9/10 equivalent.
            if 1 <= month <= 12 and (day > 12 or day == month):
                match = candidate
    if not match:
        return result
    try:
        if not 1 <= int(match[2]) <= 12:
            return result
        instant = datetime.strptime(match[1] + " " + match[2] + ":" + match[3] + match[4].upper(), "%m/%d/%Y %I:%M%p").replace(tzinfo=UTC)
    except ValueError:
        return result
    result.update(kind="explicit_public_utc_clock", utc_resolved=True, closes_at=instant.isoformat(),
                  closes_at_local=instant.replace(tzinfo=None).isoformat(timespec="minutes"), closes_tz="UTC")
    return result


def render_public_notice(source: OrganizationSource, posting: dict[str, Any], *, page_url: str,
                         external_id: str, expected_title: str, listing_raw: dict[str, Any] | None = None,
                         public_page_sha256: str | None = None) -> JobRecord:
    if source.id != "worldbank_csod" or not str(external_id).isdigit():
        raise ValueError("World Bank public notice source or identity is invalid")
    exact_url = f"https://worldbankgroup.csod.com/ux/ats/careersite/1/home/requisition/{external_id}?c=worldbankgroup"
    if page_url != exact_url or not isinstance(posting, dict) or posting.get("@type") != "JobPosting":
        raise ValueError("World Bank public notice URL or schema differs from current job")
    title = clean_text(posting.get("Title"))
    description_html = posting.get("Description")
    if not title or title != clean_text(expected_title) or not isinstance(description_html, str):
        raise ValueError("World Bank public title/body differs from current listing")
    public = public_metadata(description_html, public_title=title)
    if public["Job #"] != "req" + str(external_id):
        raise ValueError("World Bank public Job # does not match the requested identity")
    text, section_layout = public_notice_sections(description_html, title)
    deadline = _deadline(public.get("Closing Date"))
    posted = None
    raw_posted = posting.get("DatePosted")
    if isinstance(raw_posted, str):
        try:
            parsed = datetime.fromisoformat(raw_posted.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                posted = parsed.astimezone(UTC)
        except ValueError:
            pass
    resolution = {
        "record_kind": "detail", "provider": "worldbank_public_jobposting",
        "external_id": str(external_id), "source_url": page_url, "public_title": title,
        "public_fields": public, "description_html_sha256": hashlib.sha256(description_html.encode()).hexdigest(),
        "public_metadata_labels_absent": [label for label in ("Sector", "Recruitment Type", "Term Duration") if label not in public],
        "description_sha256": hashlib.sha256(text.encode()).hexdigest(), "deadline": deadline,
        "public_section_layout": section_layout,
        "duties_section_present": (False if section_layout == "young_professionals_requirements_only" else
                                   True if section_layout == "young_professionals_deliver_and_bring" else None),
        "publisher_date_posted": raw_posted, "publisher_valid_through": posting.get("ValidThrough"),
        "posting_time_resolved": posted is not None,
        "posted_at": posted.isoformat() if posted else None,
        "posting_precision": "explicit_offset" if posted else "unqualified_publisher_calendar_timestamp",
        "utc_resolved": deadline["utc_resolved"],
        "sector_recruitment_type_and_term_duration_are_not_department_or_contract_type": True,
    }
    raw = {
        "_worldbank_record_kind": "detail", "worldbank_public_jobposting": posting,
        "_worldbank_public_field_resolution": resolution, "detail_html": description_html,
        "detail_url": page_url, "public_page_sha256": public_page_sha256,
        "grade": public.get("Grade") or None, "company": public.get("Organization") or None,
        "sector": public.get("Sector") or None, "recruitment_type": public.get("Recruitment Type") or None,
        "term_duration": public.get("Term Duration") or None,
        "required_languages": public.get("Required Language(s)"), "preferred_languages": public.get("Preferred Language(s)"),
    }
    if listing_raw is not None:
        raw["worldbank_listing_metadata"] = listing_raw
    job = build_job(source, title=title, external_id=external_id, apply_url=page_url, source_url=page_url,
                    location=public.get("Location"), department=None, employment_type=None,
                    posted_at=posted, closes_at=deadline["closes_at"], description=description_html, raw=raw)
    job.closes_at_local, job.closes_tz = deadline["closes_at_local"], deadline["closes_tz"]
    # build_job's general HTML normalizer inserts spaces around inline tags.
    # This is already parsed public text, so retain the actual DOM text joins.
    job.description = text
    return job


def render_public_page(source: OrganizationSource, html_text: str, *, page_url: str,
                       external_id: str, expected_title: str, listing_raw: dict[str, Any] | None = None) -> JobRecord:
    postings = _PublicPosting(html_text).postings
    if len(postings) != 1:
        raise ValueError("World Bank page must contain exactly one public JobPosting")
    return render_public_notice(source, postings[0], page_url=page_url, external_id=external_id,
                                expected_title=expected_title, listing_raw=listing_raw,
                                public_page_sha256=hashlib.sha256(html_text.encode()).hexdigest())
