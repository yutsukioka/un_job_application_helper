"""OSCE's public full-search walk in one guarded browser session.

A session number is discovered from the redirect, never configured. Every page
is retained for independent reconciliation; latest-jobs is not a full board.
"""

import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from jobagg.normalize import build_job

SEARCH_URL = "https://vacancies.osce.org/jobs/search/"
SESSION = re.compile(r"https://vacancies\.osce\.org/jobs/search/\d+(?:/page[1-9]\d*)?/?(?:\?[^#]*)?$")


def pagination_state(body):
    """Read provider page counters, including its integral decimal page count."""
    def counter(name):
        values = re.findall(
            r'<div\b[^>]*\bid=["\']' + name + r'["\'][^>]*>\s*(\d+)(?:\.0+)?\s*</div>',
            body,
            re.I,
        )
        if len(values) != 1:
            raise ValueError("OSCE pagination counter missing or ambiguous")
        return int(values[0])

    return counter("jPaginateCurrPage"), counter("jPaginateNumPages")


class Cards(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.active = None
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "job_link" in attrs.get("class", "").split():
            url = attrs.get("href", "")
            match = re.fullmatch(r"https://vacancies\.osce\.org/jobs/[^/?#]+-(\d+)", url)
            if not match:
                raise ValueError("OSCE card has an unrecognized public identity")
            self.active = [match[1], url, []]

    def handle_data(self, text):
        if self.active is not None:
            self.active[2].append(text)

    def handle_endtag(self, tag):
        if tag == "a" and self.active is not None:
            key, url, text = self.active
            self.rows.append((key, url, " ".join("".join(text).split())))
            self.active = None


def reconcile(pages):
    if not pages:
        raise ValueError("OSCE full-search pages missing")
    rows, seen, total, scope, session = [], set(), None, None, None
    page_count = None
    for number, page in enumerate(pages, 1):
        if page["number"] != number or not SESSION.fullmatch(page["url"]):
            raise ValueError("OSCE missing page or generated search session")
        path = urlsplit(page["url"]).path.rstrip("/")
        match = re.fullmatch(r"/jobs/search/(\d+)(?:/page([1-9]\d*))?", path)
        if not match or (match[2] is not None and int(match[2]) != number):
            raise ValueError("OSCE URL page index differs from captured page")
        this_session = match[1]
        if session is not None and session != this_session:
            raise ValueError("OSCE search session changed")
        session = this_session
        current, count = pagination_state(page["html"])
        if (current != number or (count < current and (current, count) != (1, 0))
                or (page_count is not None and count != page_count)):
            raise ValueError("OSCE pagination counters changed or differ from captured page")
        page_count = count
        if captured_scope(page["html"]) != page["scope"]:
            raise ValueError("OSCE saved scope differs from captured controls")
        if (
            page["scope"].get("new_jobs") is not False
            or page["scope"].get("unfiltered") is not True
        ):
            raise ValueError("OSCE full-board filter scope unverified")
        if scope is not None and scope != page["scope"]:
            raise ValueError("OSCE filters changed during pagination")
        scope = page["scope"]
        matches = re.findall(
            r"<(?:strong|span\b[^>]*class=[\"\'][^\"\']*total_results[^\"\']*[\"\'])[^>]*>\s*(\d+)\s*</(?:strong|span)>\s*results",
            page["html"],
            re.I,
        )
        if len(matches) != 1:
            raise ValueError("OSCE advertised total missing or ambiguous")
        reported = int(matches[0])
        if total is not None and total != reported:
            raise ValueError("OSCE advertised total changed")
        total = reported
        if page_count == 0 and total != 0:
            raise ValueError("OSCE nonempty board advertises zero pages")
        cards = Cards(page["html"]).rows
        ids = [row[0] for row in cards]
        if len(ids) != len(set(ids)) or seen.intersection(ids) or (not cards and total):
            raise ValueError("OSCE repeated or empty page")
        if any(not row[2] for row in cards):
            raise ValueError("OSCE empty card title")
        seen.update(ids)
        rows.extend(cards)
    if len(seen) != total:
        raise ValueError("OSCE ID union differs from advertised total")
    if len(pages) != max(1, page_count):
        raise ValueError("OSCE captured page count differs from advertised pages")
    return rows, total


def collect_pages(page, remaining_ms, *, max_pages=50, save_page=None):
    """Interact with visible page-number controls; all network stays guarded."""
    page.wait_for_url(SESSION, timeout=remaining_ms())
    pages, seen = [], set()
    for number in range(1, max_pages + 1):
        page.locator(".number_of_results").wait_for(state="visible", timeout=remaining_ms())
        body = page.content()
        scope = captured_scope(body)
        if page.locator('input[aria-label="New Jobs"]').is_checked():
            raise ValueError("OSCE New Jobs filter selected")
        if int(page.locator("#jPaginateCurrPage").inner_text()) != number:
            raise ValueError("OSCE displayed page index differs")
        cards = Cards(body).rows
        ids = {row[0] for row in cards}
        if seen.intersection(ids):
            raise ValueError("OSCE repeated page after navigation")
        seen.update(ids)
        pages.append({"number": number, "url": page.url, "scope": scope, "html": body})
        if save_page is not None:
            save_page(pages[-1])
        totals = re.findall(
            r"<(?:strong|span\b[^>]*class=[\"\'][^\"\']*total_results[^\"\']*[\"\'])[^>]*>\s*(\d+)\s*</(?:strong|span)>\s*results",
            body,
            re.I,
        )
        if len(totals) != 1:
            raise ValueError("OSCE total missing")
        if len(seen) >= int(totals[0]):
            reconcile(pages)
            return pages
        controls = page.locator("#jPaginationHldr a, #jPaginationHldr button").filter(
            has_text=re.compile(r"^\s*" + str(number + 1) + r"\s*$")
        )
        controls.first.wait_for(state="visible", timeout=remaining_ms())
        visible = [control for control in controls.all() if control.is_visible()]
        if len(visible) != 1:
            raise ValueError("OSCE next numbered page control missing or ambiguous")
        previous = sorted(ids)
        visible[0].click(timeout=remaining_ms())
        page.wait_for_function(
            """expected => {
          const ids = [...document.querySelectorAll('a.job_link')].map(e => e.href.match(/-(\\d+)$/)?.[1]).filter(Boolean).sort();
          const current = document.querySelector('#jPaginateCurrPage');
          return Number(current?.textContent) === expected.number && ids.length > 0
            && JSON.stringify(ids) !== JSON.stringify(expected.ids);
        }""",
            arg={"ids": previous, "number": number + 1},
            timeout=remaining_ms(),
        )
    raise ValueError("OSCE maximum page bound reached")


def parse_bundle(source, body):
    match = re.fullmatch(
        r'<script type="application/json" id="jobagg-osce-inventory">(.*)</script>', body, re.S
    )
    if not match:
        raise ValueError("OSCE full-search requires guarded browser page evidence")
    pages = json.loads(match[1])
    rows, total = reconcile(pages)
    jobs = [
        build_job(
            source,
            title=title,
            external_id=key,
            source_url=url,
            apply_url=url,
            raw={"href": url, "title": title, "external_id": key},
        )
        for key, url, title in rows
    ]
    return jobs, total, len(pages)


class SearchScope(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.scopes = []
        self.new_jobs = []
        self.checked = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "jResultsContent" in attrs.get("class", "").split():
            self.scopes.append(attrs)
        if tag == "input" and attrs.get("type") == "checkbox":
            if "checked" in attrs:
                self.checked.append(attrs)
            if attrs.get("aria-label") == "New Jobs":
                self.new_jobs.append("checked" in attrs)


def captured_scope(text):
    parsed = SearchScope(text)
    if len(parsed.scopes) != 1 or len(parsed.new_jobs) != 1:
        raise ValueError("OSCE captured scope controls missing/ambiguous")
    attrs = parsed.scopes[0]
    unfiltered = (
        attrs.get("data-keyword-string") == "All jobs"
        and attrs.get("data-location-string") == "All locations"
        and attrs.get("data-keywords") == ""
        and attrs.get("data-location-ids") == ""
        and not parsed.checked
        and all(not value for key, value in attrs.items() if key.startswith("data-geo"))
    )
    return {"new_jobs": parsed.new_jobs[0], "unfiltered": unfiltered}
