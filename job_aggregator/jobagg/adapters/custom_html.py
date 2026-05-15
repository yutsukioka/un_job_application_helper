"""Generic adapter for simple legacy HTML careers pages."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord
from jobagg.normalize import build_job

_ANCHOR_RE = re.compile(
    r"<a[^>]+href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


@register_adapter
class CustomHTMLAdapter(JobAdapter):
    family = "custom_html"

    def fetch_jobs(self) -> list[JobRecord]:
        return self.parse_jobs_from_html(self.fetch_text(self.source.base_url))

    def parse_jobs_from_html(self, html_text: str) -> list[JobRecord]:
        selector_hint = str(self.source.extra.get("job_link_selector_hint") or "").lower()
        exclude_hints = self.source.extra.get("exclude_link_selector_hint") or []
        if isinstance(exclude_hints, str):
            exclude_hints = [exclude_hints]
        exclude_hints = [str(item).lower() for item in exclude_hints]
        jobs = []
        seen_hrefs: set[str] = set()
        for match in _ANCHOR_RE.finditer(html_text):
            href = match.group("href")
            absolute_href = urljoin(self.source.base_url, href)
            if absolute_href.rstrip("/") == self.source.base_url.rstrip("/"):
                continue
            if selector_hint and selector_hint not in href.lower():
                continue
            if exclude_hints and any(hint in href.lower() for hint in exclude_hints):
                continue
            if absolute_href in seen_hrefs:
                continue
            seen_hrefs.add(absolute_href)
            title = _TAG_RE.sub("", match.group("title")).strip()
            if not title:
                continue
            jobs.append(
                build_job(
                    self.source,
                    title=title,
                    external_id=absolute_href.rstrip("/").split("/")[-1],
                    apply_url=absolute_href,
                    raw={"href": absolute_href, "title": title},
                )
            )
        return jobs
