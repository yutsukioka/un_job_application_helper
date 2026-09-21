"""ITCILO public vacancy tables with section-specific empty-board evidence."""
from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

from jobagg.normalize import build_job, clean_text


class _Board(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.section = None
        self.sections = {}
        self.heading = None
        self.skip = 0
        self.table_depth = 0
        self.row = None
        self.cell = None
        self.link = None
        self.rows = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        if tag == "h2":
            self.heading = []
        if tag == "table":
            self.table_depth += 1
        if tag == "tr" and self.table_depth and self.section:
            self.row = {"section": self.section, "cells": []}
        if tag == "td" and self.row is not None:
            self.cell = {"text": [], "links": []}
        if tag == "a" and self.cell is not None and attrs.get("href"):
            self.link = {"href": attrs["href"], "text": []}

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.skip = max(0, self.skip - 1)
        if tag == "h2" and self.heading is not None:
            heading = (clean_text(" ".join(self.heading)) or "").casefold()
            self.section = {"vacancy available": "vacancies", "internship available": "internships"}.get(heading)
            if self.section:
                if self.section in self.sections:
                    raise ValueError("ITCILO repeats a vacancy category heading")
                self.sections[self.section] = []
            self.heading = None
        if tag == "a" and self.link is not None and self.cell is not None:
            self.cell["links"].append({"href": self.link["href"], "text": clean_text(" ".join(self.link["text"]))})
            self.link = None
        if tag == "td" and self.cell is not None and self.row is not None:
            self.cell["text"] = clean_text(" ".join(self.cell["text"])) or ""
            self.row["cells"].append(self.cell)
            self.cell = None
        if tag == "tr" and self.row is not None:
            if self.row["cells"]:
                self.rows.append(self.row)
            self.row = None
        if tag == "table":
            self.table_depth = max(0, self.table_depth - 1)

    def handle_data(self, text):
        if self.skip:
            return
        if self.heading is not None:
            self.heading.append(text)
        elif self.section:
            self.sections[self.section].append(text)
        if self.cell is not None:
            self.cell["text"].append(text)
        if self.link is not None:
            self.link["text"].append(text)


def parse_itcilo_board(source, html_text, listing_url):
    """Return public rows plus completeness evidence; never equate categories."""
    from jobagg.adapters.icddrb_inventory import no_continuation
    no_continuation(html_text)
    board = _Board(html_text)
    for tag in re.findall(r"<(?:a|button)\b[^>]*>.*?</(?:a|button)>", html_text, re.I | re.S):
        label = (clean_text(tag) or "").casefold()
        if (label in {"next", "load more", "show more"}
                or re.search(r"(?:[?&]page=|rel=[\"']next[\"'])", tag, re.I)):
            raise ValueError("ITCILO board exposes unhandled pagination/continuation")
    if set(board.sections) != {"vacancies", "internships"}:
        raise ValueError("ITCILO public vacancy and internship category headings are required")
    jobs = []
    counts = {section: 0 for section in board.sections}
    for row in board.rows:
        cells = row["cells"]
        if not cells:
            continue
        candidates = []
        for link in cells[0]["links"]:
            target = urljoin(listing_url, link["href"])
            match = re.fullmatch(r"/view_vacancy/(\d+)/?", urlsplit(target).path)
            if match and urlsplit(target).netloc == urlsplit(listing_url).netloc and link["text"]:
                candidates.append((match[1], target, link["text"]))
        if len(candidates) != 1 or len(cells) < 5:
            raise ValueError("ITCILO vacancy table contains an unidentified or incomplete row")
        identity, target, title = candidates[0]
        if identity in {job.external_id for job in jobs}:
            raise ValueError("ITCILO vacancy table repeats an ID")
        number, grade, closing, family = (cell["text"] for cell in cells[1:5])
        job = build_job(source, external_id=identity, title=title, apply_url=target,
                        source_url=target, department=family,
                        raw={"href": target, "external_id": identity, "title": title,
                             "parser": "itcilo_public_table", "listing_url": listing_url,
                             "vacancy_number": number, "grade": grade,
                             "listing_closes_at_local": closing,
                             "closing_precision": "calendar_date_timezone_unverified",
                             "job_family": family, "listing_section": row["section"],
                             "listing_cells": [cell["text"] for cell in cells],
                             "listing_html": html_text})
        # A published calendar date does not establish a midnight UTC instant.
        job.closes_at_local = closing or None
        jobs.append(job)
        counts[row["section"]] += 1
    empty = {}
    for section, fragments in board.sections.items():
        text = (clean_text(" ".join(fragments)) or "").casefold()
        noun = "vacancies" if section == "vacancies" else "internships"
        empty[section] = bool(re.search(rf"\bthere are no {noun}(?: available)?(?: at the moment)?\.", text))
        if counts[section] == 0 and not empty[section]:
            raise ValueError(f"ITCILO {section} section has no rows and no explicit empty statement")
    return jobs, {"category_counts": counts, "explicit_empty_categories": empty,
                  "category_headings_verified": True, "verified_empty": not jobs,
                  "source_contract": "Both public vacancy categories inspected; complete table rows or category-specific empty statement"}


def fetch_itcilo_listing(adapter, html_text, listing_url):
    jobs, evidence = parse_itcilo_board(adapter.source, html_text, listing_url)
    diagnostics = adapter.run_diagnostics
    diagnostics.pages_fetched = 1
    diagnostics.pagination_complete = True
    # There is no separately advertised numeric total on this board.
    diagnostics.total_reported_by_source = None
    if not jobs:
        diagnostics.health_status = "ok_empty"
        diagnostics.empty_reason = "verified_structural_empty"
        diagnostics.zero_fetched_evidence = evidence
    return jobs
