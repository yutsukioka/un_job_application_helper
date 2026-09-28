"""Render the public EDA vacancy API used by its official vacancy page."""
from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from jobagg.models import JobRecord, OrganizationSource
from jobagg.normalize import build_job, clean_text


class _BoardIdentity(HTMLParser):
    """Read the explicit EU board title and outbound notice links."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.titles: list[str] = []
        self.links: list[str] = []
        self.title_parts: list[str] | None = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "h2" and "job-title" in attrs.get("class", "").split():
            self.title_parts = []

    def handle_data(self, data):
        if self.title_parts is not None:
            self.title_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "h2" and self.title_parts is not None:
            self.titles.append("".join(self.title_parts))
            self.title_parts = None


def _short_board_reference(payload, external_id, page_url, summary_html, summary_url, title):
    """Bind a shortened board ID through its own exact official notice link."""
    reference = str(payload.get("Reference") or "")
    match = re.fullmatch(r"EDA/\d{4}/([1-9]\d*[a-z])", reference, re.I)
    if (not match or match[1].casefold() != external_id.casefold()
            or (str(payload.get("BudgetCode")) + str(payload.get("AlphaPart"))).casefold()
            != external_id.casefold()):
        return None
    summary = urlsplit(summary_url)
    if (summary.scheme != "https" or summary.hostname != "eu-careers.europa.eu"
            or summary.username or summary.password or summary.port not in (None, 443)
            or summary.query or summary.fragment
            or not re.fullmatch(r"/[a-z]{2}/job-opportunities/[^/]+/" + re.escape(external_id),
                                summary.path.rstrip("/"))):
        return None
    board = _BoardIdentity(summary_html)
    def compact(value):
        return re.sub(r"[^a-z0-9]", "", value.casefold())
    if (len(board.titles) != 1 or compact(board.titles[0]) != compact(title)
            or page_url not in board.links):
        return None
    return {"board_external_id": external_id, "public_reference": reference,
            "summary_url": summary_url, "summary_title": clean_text(board.titles[0]),
            "observed_notice_url": page_url}


def public_notice_endpoint(page_url: str) -> str:
    url = urlsplit(page_url)
    match = re.fullmatch(r"/vacanciesnotice/(\d+)", url.path.rstrip("/"))
    if (url.scheme != "https" or url.hostname != "vacancies.eda.europa.eu" or not match
            or url.query or url.fragment or url.username or url.password or url.port not in (None, 443)):
        raise ValueError("EDA notice URL must identify one official public version")
    return f"https://vacancies.eda.europa.eu/api/public/Vacancies/GetVacancyAndRelatedNotices/{match[1]}"


def render_public_notice(source: OrganizationSource, payload: dict[str, Any], *,
                         page_url: str, external_id: str, expected_title: str,
                         summary_html: str, summary_url: str) -> JobRecord:
    endpoint = public_notice_endpoint(page_url)
    version = int(endpoint.rsplit("/", 1)[1])
    def compact(value):
        return re.sub(r"[^a-z0-9]", "", str(value).casefold())
    short_reference = None
    reference_matches = compact(payload.get("Reference")) == compact(external_id)
    if not reference_matches:
        short_reference = _short_board_reference(
            payload, external_id, page_url, summary_html, summary_url, expected_title)
    if (type(payload.get("IdVacancy")) is not int or payload["IdVacancy"] <= 0
            or type(payload.get("IDVacanciesVersion")) is not int
            or payload.get("IDVacanciesVersion") != version
            or not (reference_matches or short_reference)):
        raise ValueError("EDA public API version/reference differs from listing")
    title = payload.get("FrendlyTitleFrontend") or payload.get("Post")
    if not isinstance(title, str) or compact(title) != compact(expected_title):
        raise ValueError("EDA public title differs from listing")
    notices = payload.get("Notices")
    if not isinstance(notices, list) or not notices:
        raise ValueError("EDA public notice has no body sections")
    if any(not isinstance(n, dict) or n.get("IdVacancy") != payload.get("IdVacancy")
           or type(n.get("Order")) is not int or not n.get("Label")
           or not isinstance(n.get("Description"), str) or not clean_text(n["Description"])
           for n in notices):
        raise ValueError("EDA public sections are malformed or belong to another vacancy")
    notices = sorted(notices, key=lambda n: n["Order"])
    if [n["Order"] for n in notices] != list(range(1, len(notices) + 1)):
        raise ValueError("EDA public section order is duplicated or incomplete")
    labels = " ".join(str(n["Label"]) for n in notices).casefold()
    if not all(label in labels for label in ("duties", "eligibility criteria", "selection criteria", "application procedure")):
        raise ValueError("EDA public notice lacks required vacancy sections")
    fields = [
        ("Contract type", payload.get("ContractStatusText")),
        ("Directorate", payload.get("Directorate")), ("Group", payload.get("Group")),
        ("Grade", payload.get("Grade")), ("Level of Security Clearance", payload.get("SecurityClearance")),
        ("Management of staff", payload.get("StaffManagment")), ("Location", payload.get("Location")),
    ]
    start = payload.get("StartingDateText") if payload.get("StartingDateTextShow") else payload.get("StartingDate")
    deadline = payload.get("PublicationDateEndShow")
    def public_date(value):
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00", value):
            date = value[:10].split("-")
            return "/".join(reversed(date))
        return value
    fields.extend([("Indicative starting date", public_date(start)),
                   ("Closing date for applications", public_date(deadline))])
    html = "<h1>" + escape(title) + "</h1>" + "".join(
        f"<p><strong>{escape(label)}:</strong> {escape(str(value))}</p>"
        for label, value in fields if value is not None
    ) + "".join(f'<h2>{n["Order"]}. {escape(n["Label"])}</h2>{n["Description"]}' for n in notices)
    # Calendar values and conflicting public wall-clock prose are retained.
    # They do not justify inheriting an unrelated board UTC deadline.
    resolution = {
        "record_kind": "detail", "provider": "eda_public_notice_api",
        "public_fields": dict(fields), "official_grade": payload.get("Grade"),
        "utc_resolved": False, "posting_time_resolved": False,
        "public_deadline_date": deadline, "public_posting_date": payload.get("PublicationDateStart"),
        "deadline_reason": "Public date and deadline prose require a coherent timezone and cutoff interpretation",
        "public_version": version, "reference": payload.get("Reference"),
    }
    if short_reference:
        resolution["board_reference_binding"] = short_reference
    return build_job(source, title=title, external_id=external_id, apply_url=page_url,
                     location=payload.get("Location"), department=payload.get("Directorate"),
                     employment_type=payload.get("ContractStatusText"), description=html,
                     raw={"parser": "eu_official_detail", "eda_public_notice": payload,
                          "_eu_official_field_resolution": resolution,
                          "summary_html": summary_html, "detail_html": html,
                          "detail_url": summary_url, "detail_fetch_url": endpoint,
                          "official_vacancy_url": page_url, "official_wrapper_url": page_url,
                          "official_notice_text": clean_text(html),
                          "required_attachment_urls": [f"https://vacancies.eda.europa.eu/api/public/Vacancies/ExportPdf/{version}/false"],
                          "identity_verification": "official_public_version_reference_and_title"})
