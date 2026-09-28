"""PeopleSoft Careers listing adapter."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_bool, clean_html


@register_adapter
class PeopleSoftAdapter(JobAdapter):
    family = "peoplesoft"

    def fetch_jobs(self) -> list[JobRecord]:
        listing_url = str(self.source.extra.get("listing_url") or self.source.base_url)
        html_text = self.fetch_text(listing_url)
        self._listing_html = html_text
        jobs = self.parse_listing_html(html_text, listing_url=listing_url)
        total_match = re.search(r"\b(\d+)\s+jobs?\s+found\b", clean_html(html_text) or "", re.I)
        total = int(total_match.group(1)) if total_match else None
        self.run_diagnostics.pages_fetched = 1
        self.run_diagnostics.total_reported_by_source = total
        # IFAD's empty-query search explicitly limits the result set to 100.
        # Never certify that cap as a complete source census.
        self.run_diagnostics.pagination_complete = total == len(jobs) and total < 100 if total is not None else False
        if total == 0 and not jobs:
            self.run_diagnostics.empty_reason = "verified_total_zero"
            self.run_diagnostics.zero_fetched_evidence = {"total_reported_by_source": 0}
        if as_bool(self.source.extra.get("fetch_details"), default=False):
            detailed = []
            for job in jobs:
                detail = self.fetch_detail_for_listing_item(job.raw)
                if detail is None:
                    raise RuntimeError(f"{self.source.id}: detail not available for {job.external_id}")
                detailed.append(detail)
            return detailed
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, str]) -> JobRecord | None:
        if self.source.id == "ifad_peoplesoft" or item.get("parser") == "peoplesoft_listing":
            return self._fetch_guest_detail(item)
        detail_url = item.get("detail_url") or item.get("source_url") or item.get("apply_url")
        if not detail_url:
            return None
        detail_url = str(detail_url)
        self.ensure_allowed(detail_url)
        html_text = self.fetch_text(detail_url)
        return self.parse_detail_html(html_text, item=item, detail_url=detail_url)

    def _fetch_guest_detail(self, item: dict[str, str]) -> JobRecord | None:
        job_id = str(item.get("job_id") or item.get("external_id") or "")
        if not job_id:
            raise ValueError("PeopleSoft detail requires a listing job ID")
        listing_url = str(self.source.extra.get("listing_url") or self.source.base_url)
        html_text = getattr(self, "_listing_html", None) or self.fetch_text(listing_url)
        self._listing_html = None  # A PeopleSoft ICStateNum is consumed by navigation.
        rows = _search_rows(html_text)
        selected = next((row for row in rows
                         if _span_value(row, "HRS_JOB_OPENING_ID") == job_id), None)
        if selected is None:
            return None
        # Re-resolve the row by job ID on each fresh listing; row positions change.
        action = re.search(r"\bid=[\"'](?P<action>HRS_VIEW_DETAILSPB\$\d+)[\"']", selected)
        if action is None:
            raise ValueError(f"PeopleSoft listing has no public detail action for {job_id}")
        form = _GuestForm()
        form.feed(html_text)
        if not form.action or not form.fields.get("ICSID"):
            raise ValueError("PeopleSoft public guest form/session missing")
        post_url = urljoin(listing_url, form.action)
        form.fields.update(ICAction=action.group("action"), ICAJAX="1")
        response = self.post_form_text(post_url, form.fields)
        listing_title = _span_value(selected, "SCH_JOB_TITLE")
        unique_title = sum(_span_value(row, "SCH_JOB_TITLE") == listing_title for row in rows) == 1
        item = {**item, **_listing_metadata(selected)}
        # Keep the public entry URL, not a fabricated GET that silently shows Search Jobs.
        return self.parse_detail_html(response, item=item, detail_url=listing_url,
                                      guest_identity=(job_id, listing_title) if unique_title else None)

    def parse_detail_html(self, html_text: str, *, item: dict[str, str], detail_url: str,
                          guest_identity: tuple[str, str] | None = None) -> JobRecord | None:
        content_html = _detail_page_html(html_text)
        blocks = _ValueBlocks()
        blocks.feed(content_html)
        values = {key: clean_html(value) for key, value in blocks.values.items()}
        job_id = str(item.get("job_id") or item.get("external_id") or "")
        actual_id = values.get("HRS_SCH_WRK2_HRS_JOB_OPENING_ID")
        title = values.get("HRS_SCH_WRK2_POSTING_TITLE")
        # IFAD's standing-roster detail pages omit the ID field. Only the fresh,
        # ID-selected guest action plus an exact unique title can establish that
        # response identity; a supplied item title alone is never sufficient.
        guest_verified = not actual_id and guest_identity == (job_id, title)
        if (actual_id != job_id and not guest_verified) or not title:
            raise ValueError(f"PeopleSoft detail identity mismatch: expected {job_id}, observed {actual_id}")
        sections = []
        for key, body in blocks.values.items():
            if not re.fullmatch(r"HRS_SCH_PSTDSC_DESCRLONG\$\d+", key):
                continue
            index = key.rsplit("$", 1)[1]
            sections.append({"heading": values.get(f"HRS_SCH_WRK_DESCR100${index}lbl"),
                             "text": clean_html(body), "html": body})
        if not sections or not all(section["text"] for section in sections):
            raise ValueError(f"PeopleSoft detail has no complete job-description sections: {job_id}")
        metadata = [("Job Title", title), ("Job ID", job_id)]
        metadata.extend((label, values.get(key)) for key, label in _METADATA_FIELDS.items())
        for key, value in values.items():
            if key in _METADATA_FIELDS or key in {"HRS_SCH_WRK2_POSTING_TITLE", "HRS_SCH_WRK2_HRS_JOB_OPENING_ID"}:
                continue
            label = blocks.labels.get(key)
            if label and value and not key.startswith("HRS_SCH_PSTDSC_DESCRLONG$"):
                metadata.append((label, value))
        description = "\n".join(f"{label}: {value}" for label, value in metadata if value)
        description += "\n\n" + "\n\n".join(
            "\n".join(str(value) for value in (section["heading"], section["text"]) if value)
            for section in sections
        )
        return build_job(
            self.source,
            title=title,
            external_id=job_id,
            location=values.get("HRS_SCH_WRK_HRS_DESCRLONG") or item.get("listing_location"),
            department=values.get("IFA_HRS_SCH_WRK_HRS_DEPT_DESCR") or item.get("listing_department"),
            posted_at=values.get("IFA_HRS_SCH_WRK_OPEN_DT") or item.get("listing_posted_at"),
            closes_at=values.get("IFA_HRS_SCH_WRK_HRS_JO_PST_CLS_DT") or item.get("listing_closes_at"),
            employment_type=values.get("HRS_SCH_WRK_HRS_REG_TEMP"),
            apply_url=detail_url,
            source_url=detail_url,
            description=description,
            raw={**item, "detail_url": detail_url, "parser": "peoplesoft_detail",
                 "detail_fetch_method": "public_guest_form_post", "verified_job_id": job_id,
                 "response_job_id": actual_id,
                 "identity_verification": "guest_row_id_and_exact_unique_title" if guest_verified else "explicit_detail_job_id",
                 "detail_sections": sections, "detail_fields": values,
                 "grade": values.get("IFA_HRS_SCH_WRK_DESCR"), "detail_html": content_html},
        )

    def parse_listing_html(self, html_text: str, *, listing_url: str | None = None) -> list[JobRecord]:
        listing_url = listing_url or str(self.source.extra.get("listing_url") or self.source.base_url)
        jobs = []
        for row_html in _search_rows(html_text):
            title = _span_value(row_html, "SCH_JOB_TITLE")
            job_id = _span_value(row_html, "HRS_JOB_OPENING_ID")
            if not title or not job_id:
                continue
            # This PeopleSoft installation exposes details through a guest form
            # action. Appending job_id to the search URL does not select a job.
            detail_url = listing_url
            jobs.append(
                build_job(
                    self.source,
                    title=title,
                    external_id=job_id,
                    location=_span_value(row_html, "LOCATION"),
                    department=_span_value(row_html, "HRS_DEPT_DESCR"),
                    posted_at=_span_value(row_html, "SCH_OPENED"),
                    closes_at=_span_value(row_html, "HRS_JO_PST_CLS_DT"),
                    apply_url=detail_url,
                    source_url=detail_url,
                    raw={
                        "job_id": job_id,
                        "title": title,
                        "detail_url": detail_url,
                        "listing_url": listing_url,
                        **_listing_metadata(row_html),
                        "close_date_text": _span_value(row_html, "HRS_CLS_DT_DESCR"),
                        "parser": "peoplesoft_listing",
                    },
                )
            )
        return _dedupe(jobs)


_METADATA_FIELDS = {
    "HRS_SCH_WRK_HRS_DESCRLONG": "Location",
    "IFA_HRS_SCH_WRK_OPEN_DT": "Posted Date",
    "IFA_HRS_SCH_WRK_HRS_JO_PST_CLS_DT": "Close Date",
    "IFA_HRS_SCH_WRK_HRS_DEPT_DESCR": "Department",
    "HRS_SCH_WRK_HRS_FULL_PART_TIME": "Full/Part Time",
    "HRS_SCH_WRK_HRS_REG_TEMP": "Regular/Temporary",
    "IFA_HRS_SCH_WRK_IFA_VR_ASSIGN_DUR": "Assignment Duration",
    "IFA_HRS_SCH_WRK_DESCR": "Grade",
}


def _listing_metadata(row_html):
    return {"listing_location": _span_value(row_html, "LOCATION"),
            "listing_department": _span_value(row_html, "HRS_DEPT_DESCR"),
            "listing_posted_at": _span_value(row_html, "SCH_OPENED"),
            "listing_closes_at": _span_value(row_html, "HRS_JO_PST_CLS_DT")}


class _GuestForm(HTMLParser):
    """Read only the public guest form fields; never persist its session token."""
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}
        self.active = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.active = attrs.get("name") == "win0"
            if self.active:
                self.action = attrs.get("action")
        if self.active and tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False


class _ValueBlocks(HTMLParser):
    """Capture complete nested value spans, including unquoted PeopleSoft IDs."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.values = {}
        self.labels = {}
        self.active = []

    def handle_starttag(self, tag, attrs):
        for capture in self.active:
            capture["parts"].append(self.get_starttag_text())
            if tag == capture["tag"]:
                capture["depth"] += 1
        attrs = dict(attrs)
        key = attrs.get("id", "")
        label = tag == "div" and key.startswith("win0div") and key.endswith("lbl")
        if label or (tag in {"span", "div"} and ("ps_box-value" in attrs.get("class", "").split()
                or re.fullmatch(r"HRS_SCH_WRK_DESCR100\$\d+lbl", key))):
            self.active.append({"key": key, "tag": tag, "depth": 1, "parts": [], "label": label})

    def handle_endtag(self, tag):
        for capture in list(self.active):
            if tag == capture["tag"]:
                capture["depth"] -= 1
            if capture["depth"] == 0:
                body = "".join(capture["parts"])
                if capture["label"]:
                    self.labels[capture["key"][7:-3]] = clean_html(body)
                else:
                    self.values[capture["key"]] = body
                self.active.remove(capture)
            else:
                capture["parts"].append(f"</{tag}>")

    def handle_data(self, data):
        for capture in self.active:
            capture["parts"].append(data)

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")


def _detail_page_html(response_text: str) -> str:
    if response_text.lstrip().startswith("<?xml") or response_text.lstrip().startswith("<PAGE"):
        page = ET.fromstring(response_text)
        if page.tag != "PAGE" or page.get("id") != "HRS_APP_JBPST_FL":
            raise ValueError("PeopleSoft returned a search/session/error page instead of a job detail")
        fields = [node.text or "" for node in page.findall("FIELD")
                  if node.get("id") == "win0divPAGECONTAINER"]
        if not fields:
            raise ValueError("PeopleSoft detail response lacks its page container")
        return "\n".join(fields)
    if "HRS_SCH_WRK2_HRS_JOB_OPENING_ID" not in response_text:
        raise ValueError("PeopleSoft returned a search/session/error page instead of a job detail")
    return response_text


def _search_rows(html_text: str) -> list[str]:
    return re.findall(
        r"<li\b(?=[^>]*id=[\"']HRS_AGNT_RSLT_I\$0_row_\d+[\"'])[^>]*>(?P<body>.*?)</li>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )


def _span_value(row_html: str, field_id_contains: str) -> str | None:
    value_pattern = re.compile(
        rf"<span\b"
        rf"(?=[^>]*class=[\"'][^\"']*\bps_box-value\b[^\"']*[\"'])"
        rf"(?=[^>]*id=[\"'][^\"']*{re.escape(field_id_contains)}[^\"']*[\"'])"
        rf"[^>]*>(?P<value>.*?)</span>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    label_pattern = re.compile(
        rf"<span\b"
        rf"(?=[^>]*id=[\"'][^\"']*{re.escape(field_id_contains)}[^\"']*[\"'])"
        rf"[^>]*>(?P<value>.*?)</span>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    matches = [clean_html(match.group("value")) for match in value_pattern.finditer(row_html)]
    if not matches:
        matches = [
            clean_html(match.group("value"))
            for match in label_pattern.finditer(row_html)
            if "lbl" not in match.group(0).casefold()
        ]
    return next((value for value in matches if value), None)


def _dedupe(jobs: list[JobRecord]) -> list[JobRecord]:
    seen = set()
    deduped = []
    for job in jobs:
        key = job.identity_key()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(job)
    return deduped
