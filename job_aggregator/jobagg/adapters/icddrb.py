"""icddr,b custom career portal adapter."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import clean_html


@register_adapter
class ICDDRBAdapter(JobAdapter):
    family = "icddrb_custom_html"

    def fetch_jobs(self) -> list[JobRecord]:
        listing_html = self.fetch_text(self.source.base_url)
        token = _csrf_token(listing_html)
        jobs: list[JobRecord] = []
        seen_urls: set[str] = set()
        employee_type_ids = self.source.extra.get("employee_type_ids") or [3, 2]
        for employee_type_id in employee_type_ids:
            response_html = self._fetch_employee_type_listing(employee_type_id, token)
            for link in _vacancy_links(response_html or listing_html, self.source.base_url):
                if link["href"] in seen_urls:
                    continue
                seen_urls.add(link["href"])
                jobs.append(self._job_from_link(link))
        if not jobs:
            for link in _vacancy_links(listing_html, self.source.base_url):
                if link["href"] in seen_urls:
                    continue
                seen_urls.add(link["href"])
                jobs.append(self._job_from_link(link))
        return jobs

    def _fetch_employee_type_listing(self, employee_type_id: object, token: str | None) -> str:
        url = str(
            self.source.extra.get("opportunities_url")
            or urljoin(self.source.base_url, "/get-current-opportunities")
        )
        payload: dict[str, Any] = {"employee_type_id": employee_type_id}
        if token:
            payload["_token"] = token
        try:
            return self.post_form_text(
                url,
                payload,
                headers={"X-CSRF-TOKEN": token} if token else None,
            )
        except Exception:
            return ""

    def _job_from_link(self, link: dict[str, str]) -> JobRecord:
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=True)
        if fetch_details:
            try:
                return self.parse_detail_page(self.fetch_text(link["href"]), link["href"])
            except Exception:
                pass
        return build_job(
            self.source,
            title=link["title"],
            external_id=_external_id(link["href"]),
            apply_url=link["href"],
            source_url=link["href"],
            raw={"href": link["href"], "title": link["title"], "parser": "icddrb_listing"},
        )

    def parse_detail_page(self, html_text: str, page_url: str) -> JobRecord:
        title = _first_match(
            html_text,
            r"<h1\b(?=[^>]*class=[\"'][^\"']*\bjob-title\b)[^>]*>(?P<value>.*?)</h1>",
        ) or _first_match(html_text, r"<title\b[^>]*>(?P<value>.*?)</title>")
        apply_url = _first_match(
            html_text,
            r"<a\b(?=[^>]*\bhref=[\"'](?P<value>[^\"']*apply-for-job/[^\"']+)[\"'])",
            raw=True,
        )
        posted_at = _first_match(
            html_text,
            r"<span\b(?=[^>]*class=[\"'][^\"']*\bposted-date\b)[^>]*>(?P<value>.*?)</span>",
        )
        closes_at = _first_match(
            html_text,
            r"<span\b(?=[^>]*class=[\"'][^\"']*\bdeadline-date\b)[^>]*>(?P<value>.*?)</span>",
        )
        body = _first_match(
            html_text,
            r"<div\b(?=[^>]*class=[\"'][^\"']*\bicddrb-invites\b)[^>]*>(?P<value>.*?)</div>",
            raw=True,
        )
        location = _field_from_description(body, "Location")
        contract_type = _field_from_description(body, "Contract Type and Duration")
        return build_job(
            self.source,
            title=title,
            external_id=_external_id(page_url),
            location=location,
            employment_type=contract_type,
            posted_at=_strip_label(posted_at, "Posted on"),
            closes_at=_strip_label(closes_at, "Application deadline"),
            apply_url=urljoin(page_url, apply_url) if apply_url else page_url,
            source_url=page_url,
            description=clean_html(body),
            raw={
                "posted_at": posted_at,
                "closes_at": closes_at,
                "location": location,
                "contract_type": contract_type,
                "parser": "icddrb_detail",
            },
        )


def _csrf_token(html_text: str) -> str | None:
    match = re.search(
        r"<meta\b(?=[^>]*name=[\"']csrf-token[\"'])(?=[^>]*content=[\"'](?P<token>[^\"']+)[\"'])",
        html_text,
        flags=re.IGNORECASE,
    )
    return match.group("token") if match else None


def _vacancy_links(html_text: str, base_url: str) -> list[dict[str, str]]:
    links = []
    for match in re.finditer(
        r"<a\b[^>]*href=(?:[\"'](?P<quoted>[^\"']*vacancy-preview/(?P<quoted_id>\d+)[^\"']*)[\"']|"
        r"(?P<bare>[^\s>]*vacancy-preview/(?P<bare_id>\d+)[^\s>]*))[^>]*>(?P<title>.*?)</a>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = match.group("quoted") or match.group("bare")
        vacancy_id = match.group("quoted_id") or match.group("bare_id")
        title = clean_html(match.group("title")) or f"icddr,b vacancy {vacancy_id}"
        links.append({"href": urljoin(base_url, href), "title": title})
    return links


def _first_match(html_text: str | None, pattern: str, *, raw: bool = False) -> str | None:
    if not html_text:
        return None
    match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    value = match.group("value")
    return value if raw else clean_html(value)


def _strip_label(value: str | None, label: str) -> str | None:
    if not value:
        return None
    return re.sub(rf"^{re.escape(label)}\s*:\s*", "", value, flags=re.IGNORECASE).strip()


def _field_from_description(html_text: str | None, label: str) -> str | None:
    if not html_text:
        return None
    strong_match = re.search(
        rf"<strong>\s*{re.escape(label)}\s*:?\s*</strong>\s*(?P<value>.*?)(?:<br\b|<strong\b|</p>)",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if strong_match:
        value = clean_html(strong_match.group("value"))
        if value:
            return value.strip(" :")
        tail = html_text[strong_match.end() :]
        list_item = re.search(r"<li\b[^>]*>(?P<value>.*?)</li>", tail, flags=re.IGNORECASE | re.DOTALL)
        if list_item:
            value = clean_html(list_item.group("value"))
            if value:
                return value.strip(" :")
    text = clean_html(html_text)
    if not text:
        return None
    match = re.search(
        rf"{re.escape(label)}\s*:?\s*(?P<value>.*?)"
        r"(?:\s+(?:Position Description|Required Qualifications|Salary\s*&\s*benefits|"
        r"Notes of Attention|Interested candidates|icddr,b invites)|"
        r"\s+[A-Z][A-Za-z &/]+:|$)",
        text,
        flags=re.IGNORECASE,
    )
    return match.group("value").strip() if match else None


def _external_id(url: str) -> str | None:
    match = re.search(r"/vacancy-preview/(\d+)", url)
    return match.group(1) if match else None
