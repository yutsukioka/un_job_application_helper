"""Configurable parser for public static/custom HTML vacancy pages."""

from __future__ import annotations

import html
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from io import BytesIO
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit
from zoneinfo import ZoneInfo

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord, OrganizationSource
from jobagg.normalize import build_job, canonical_url, clean_text, parse_datetime
from jobagg.utils import as_bool as _as_bool
from jobagg.utils import as_int as _as_int
from jobagg.utils import clean_html as _clean_html


@register_adapter
class StaticHTMLAdapter(JobAdapter):
    family = "static_html"

    def fetch_jobs(self) -> list[JobRecord]:
        listing_url = str(self.source.extra.get("listing_url") or self.source.base_url)
        parser_name = str(self.source.extra.get("parser") or self.source.ats_family)
        html_text = self.fetch_text(listing_url)
        blocked_reason = _blocked_page_reason(html_text)
        if blocked_reason:
            self.run_diagnostics.health_status = "issue"
            self.run_diagnostics.empty_reason = "blocked"
            raise RuntimeError(f"{self.source.id}: listing page blocked by {blocked_reason}")
        if parser_name == "eu_careers_open_vacancies":
            return self._fetch_eu_listing(html_text, listing_url)
        if self.source.id == "itcilo_custom_html":
            from jobagg.adapters.itcilo_public import fetch_itcilo_listing

            return fetch_itcilo_listing(self, html_text, listing_url)
        if self.source.id == "osce_custom_html":
            return self._fetch_osce_listing(html_text, listing_url)
        if parser_name == "unssc_drupal":
            jobs = parse_unssc_jobs(self.source, html_text, listing_url)
            if not jobs and _unssc_verified_empty(html_text):
                self.run_diagnostics.pages_fetched = 1
                self.run_diagnostics.pagination_complete = True
                self.run_diagnostics.total_reported_by_source = 0
                self.run_diagnostics.health_status = "ok_empty"
                self.run_diagnostics.empty_reason = "verified_structural_empty"
                self.run_diagnostics.zero_fetched_evidence = {
                    "employment_view": "view-employment",
                    "matched_text": "There are no vacancies at present, please visit this page regularly for updates.",
                }
            return jobs
        if parser_name in {"markdown_public_links", "jina_markdown_public_links"}:
            return self._parse_markdown_links(html_text, listing_url)
        if parser_name in {"generic", "public_links"}:
            return self._parse_generic_links(html_text, listing_url)
        return self._parse_generic_links(html_text, listing_url)

    def _fetch_osce_listing(self, html_text: str, listing_url: str) -> list[JobRecord]:
        if urlsplit(listing_url).path.rstrip("/") == "/jobs/search":
            from jobagg.adapters.osce_inventory import parse_bundle
            jobs, total, pages = parse_bundle(self.source, html_text)
            self.run_diagnostics.total_reported_by_source = total
            self.run_diagnostics.pages_fetched = pages
            self.run_diagnostics.pagination_complete = True
            self.run_diagnostics.scope_validation_status = "verified"
            self.run_diagnostics.health_status = "ok" if jobs else "ok_empty"
            return jobs
        """Preserve the initial public rows without certifying the newest-page subset.

        The advertised scroll continuation currently returns HTTP 403. It must
        be restored through a valid public session before pagination can pass.
        """
        jobs = self._parse_generic_links(html_text, listing_url)
        match = re.search(r'<strong[^>]*>\s*(\d+)\s*</strong>\s*results', html_text, re.I)
        total = int(match.group(1)) if match else None
        self.run_diagnostics.total_reported_by_source = total
        self.run_diagnostics.pages_fetched = 1
        self.run_diagnostics.pagination_complete = False
        self.run_diagnostics.list_error_count = 1
        self.run_diagnostics.health_status = "issue"
        self.run_diagnostics.empty_reason = (
            f"OSCE latest-jobs scope unverified: observed {len(jobs)} of "
            f"{total if total is not None else 'unknown'} advertised latest results; "
            "public scroll continuation requires accessible session and full-job search scope reconciliation"
        )
        return jobs

    def _fetch_eu_listing(self, html_text: str, listing_url: str) -> list[JobRecord]:
        jobs, seen_ids, seen_pages = [], set(), set()
        pending = [(listing_url, html_text)]
        limit = _as_int(self.source.extra.get("max_pages"), default=20)
        issues = []
        self.run_diagnostics.pagination_complete = False
        while pending and len(seen_pages) < limit:
            page_url, cached_html = pending.pop(0)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)
            body = cached_html if cached_html is not None else self.fetch_text(page_url)
            if _blocked_page_reason(body):
                raise ValueError("EU Careers listing page is blocked")
            page_jobs = parse_eu_careers_jobs(self.source, body, page_url)
            if not page_jobs:
                raise ValueError("EU Careers vacancy table missing or unexpectedly empty")
            requested_page = int(dict(parse_qsl(urlsplit(page_url).query)).get("page", "0"))
            observed = re.search(r'aria-current=["\']true["\']\s*>Page (\d+)', body)
            if observed and int(observed.group(1)) != requested_page + 1:
                issues.append(f"requested_page_{requested_page + 1}_returned_{observed.group(1)}")
            ids = {job.external_id for job in page_jobs}
            if seen_ids & ids:
                issues.append(f"repeated_ids_on_page_{requested_page + 1}")
            jobs.extend(job for job in page_jobs if job.external_id not in seen_ids)
            seen_ids.update(ids)
            for target in _eu_page_links(body, page_url):
                if target not in seen_pages and target not in {url for url, _ in pending}:
                    pending.append((target, None))
            if 'ecl-pagination' not in body:
                issues.append("pagination_evidence_missing")
        self.run_diagnostics.pages_fetched = len(seen_pages)
        if pending:
            issues.append(f"page_cap_{limit}_reached")
        self.run_diagnostics.pagination_complete = not issues
        if issues:
            self.run_diagnostics.list_error_count = len(issues)
            self.run_diagnostics.health_status = "issue"
            self.run_diagnostics.empty_reason = "pagination_incomplete: " + "; ".join(issues)
        return jobs

    def fetch_detail_for_listing_item(self, item: dict[str, Any]) -> JobRecord | None:
        public_url = (
            item.get("href")
            or item.get("document_url")
            or item.get("source_url")
            or item.get("url")
            or item.get("apply_url")
        )
        if not public_url:
            return None
        public_url = str(public_url)
        if self.source.id == "ipu_static_html":
            from jobagg.adapters.ipu_detail import fetch_ipu_bilingual_detail

            return fetch_ipu_bilingual_detail(self, item, public_url)
        detail_fetch_url = str(item.get("detail_fetch_url") or public_url)
        if str(item.get("parser") or self.source.extra.get("parser")) == "eu_careers_open_vacancies":
            return self._fetch_eu_detail(item, public_url)
        self.ensure_allowed(detail_fetch_url)
        response = self.context.http.get(detail_fetch_url)
        detail_text = _detail_text_from_response(response, detail_fetch_url)
        parser_name = str(item.get("parser") or self.source.extra.get("parser") or self.source.ats_family)
        if parser_name in {"markdown_public_links", "jina_markdown_public_links"}:
            detail_job = parse_markdown_detail_page(self.source, detail_text, public_url)
        else:
            detail_job = parse_detail_page(self.source, detail_text, public_url)
        # Plain descriptions discard hrefs. Keep the observed HTML available
        # for deterministic document discovery, including the early return
        # when a listing has no separate external_id. Extracted PDF/Markdown
        # text must not be mislabelled as an HTML response.
        content = getattr(response, "content", b"") or b""
        content = bytes(content) if isinstance(content, (bytes, bytearray)) else b""
        headers = getattr(response, "headers", {}) or {}
        media = str(headers.get("Content-Type") or headers.get("content-type") or "").lower()
        if (parser_name not in {"markdown_public_links", "jina_markdown_public_links"}
                and not _response_looks_like_pdf(detail_fetch_url, response, detail_text, content)
                and ("html" in media or re.search(r"<(?:html|body|main|article|div|p|script|a)\b", detail_text, re.I))):
            detail_job.raw["detail_html"] = detail_text
        listing_external_id = item.get("external_id") or item.get("code")
        if listing_external_id in (None, ""):
            return detail_job
        closes_at = detail_job.closes_at or _listing_datetime(self.source, item, "closes_at")
        posted_at = detail_job.posted_at or _listing_datetime(self.source, item, "posted_at")
        osce_resolution = detail_job.raw.get("_osce_public_field_resolution")
        if self.source.id == "osce_custom_html" and isinstance(osce_resolution, dict):
            if osce_resolution.get("utc_resolved") is False:
                closes_at = None
            if osce_resolution.get("posting_time_resolved") is False:
                posted_at = None
        cern_resolution = detail_job.raw.get("_cern_public_field_resolution")
        if self.source.id == "cern_custom_html" and isinstance(cern_resolution, dict):
            # The public field resolver owns explicit unknowns as well as clocks.
            closes_at, posted_at = detail_job.closes_at, detail_job.posted_at
        unu_resolution = detail_job.raw.get("_unu_public_field_resolution")
        if self.source.id == "unu_recruitee" and isinstance(unu_resolution, dict):
            closes_at, posted_at = detail_job.closes_at, detail_job.posted_at
        listing_title = clean_text(item.get("title"))
        return replace(
            detail_job,
            title=listing_title if detail_job.title == "Untitled role" and listing_title else detail_job.title,
            external_id=str(listing_external_id),
            location=detail_job.location if cern_resolution or unu_resolution else detail_job.location or clean_text(item.get("location")),
            employment_type=detail_job.employment_type if cern_resolution or unu_resolution else detail_job.employment_type or clean_text(item.get("employment_type")),
            posted_at=posted_at,
            closes_at=closes_at,
            apply_url=public_url,
            source_url=public_url,
            raw={
                **detail_job.raw,
                "listing_raw": item,
                "detail_url": public_url,
                "detail_fetch_url": detail_fetch_url if detail_fetch_url != public_url else None,
            },
        )

    def _eu_get_official(self, url: str, form_payload: dict | None = None):
        host = urlsplit(url).hostname or ""
        cooldown = (self.source.extra.get("official_host_cooldowns") or {}).get(host)
        if cooldown:
            expiry = datetime.fromisoformat(str(cooldown))
            if expiry.tzinfo is None:
                raise ValueError("EU official host cooldown requires an explicit timezone")
            if expiry > datetime.now(timezone.utc):
                raise RuntimeError(f"EU official host {host} cooldown until {cooldown}")
        failed = getattr(self, "_eu_failed_hosts", {})
        if host in failed:
            cause = getattr(self, "_eu_failed_host_errors", {}).get(host)
            raise RuntimeError(f"EU official host {host} stopped for this pass: {failed[host]}") from cause
        self.ensure_allowed(url)
        try:
            response = self.context.http.get(url) if form_payload is None else self.context.http.post_form(url, form_payload)
            body = str(response.text or "")
            if (_blocked_page_reason(body) or re.search(
                    r"<title[^>]*>\s*(?:just a moment|access denied|403 forbidden|verify you are human)",
                    body, re.I)):
                raise RuntimeError(f"EU official host {host} returned an access challenge")
            return response
        except Exception as exc:
            self._eu_failed_hosts = {**failed, host: f"{type(exc).__name__}: {str(exc)[:180]}"}
            self._eu_failed_host_errors = {**getattr(self, "_eu_failed_host_errors", {}), host: exc}
            raise

    def _fetch_eu_detail(self, item: dict[str, Any], summary_url: str) -> JobRecord:
        summary_html = self.fetch_text(summary_url)
        summary = parse_detail_page(self.source, summary_html, summary_url)
        metadata = _eu_summary_metadata(summary_html)
        external_id = str(item.get("external_id") or _external_id_from_url(summary_url))
        title = str(item.get("title") or summary.title)
        official_url = _eu_vacancy_url(summary_html, summary_url)
        summary_official_url = official_url
        route_overrides = self.source.extra.get("official_notice_url_overrides", {})
        if official_url in route_overrides:
            target = route_overrides[official_url]
            if not isinstance(target, str) or target not in self.source.extra.get("official_vacancy_urls", []):
                raise ValueError("EU observed notice override must be an explicitly registered official URL")
            official_url = target
        response = self._eu_get_official(official_url)
        official_html = str(response.text or "")
        official_url = str(response.url or official_url)
        path = urlsplit(official_url).path.rstrip("/").casefold()
        primary_hosts = {"www.euipo.europa.eu", "euipo.europa.eu", "www.euda.europa.eu", "euda.europa.eu"}
        if ({urlsplit(official_url).hostname, urlsplit(summary_official_url).hostname} & primary_hosts):
            from jobagg.adapters.eu_primary_refresh import refresh_reviewed_notice, supports_reference
            if supports_reference(external_id):
                return refresh_reviewed_notice(
                    self, item, identity=external_id, summary_url=summary_url, summary_html=summary_html,
                    wrapper_url=official_url, wrapper_html=official_html,
                )
        if urlsplit(official_url).hostname == "vacancies.eda.europa.eu":
            from jobagg.adapters.eda_public import public_notice_endpoint, render_public_notice
            endpoint = public_notice_endpoint(official_url)
            notice_response = self._eu_get_official(endpoint)
            if str(notice_response.url or endpoint) != endpoint:
                raise ValueError("EDA public notice API redirected away from its version")
            payload = json.loads(notice_response.text)
            if not isinstance(payload, dict):
                raise ValueError("EDA public notice API must return one vacancy")
            return render_public_notice(
                self.source, payload, page_url=official_url, external_id=external_id,
                expected_title=title, summary_html=summary_html, summary_url=summary_url,
            )
        euaa_form_target = None
        directory_notice = None
        if urlsplit(official_url).hostname in {"careers.euaa.europa.eu", "www.euaa.europa.eu"}:
            if urlsplit(official_url).hostname != "careers.euaa.europa.eu":
                portals = {urljoin(official_url, a["href"]) for a in _TokenParser.parse(official_html).anchors
                           if urlsplit(urljoin(official_url, a["href"])).hostname == "careers.euaa.europa.eu"}
                if len(portals) != 1:
                    raise ValueError("EUAA official directory must identify one eRecruitment portal")
                official_url = portals.pop()
                response = self._eu_get_official(official_url)
                official_html = str(response.text or "")
            form = _EUAAVacancyForm()
            form.feed(official_html)
            payload = form.notice_payload(external_id)
            form_url = urljoin(official_url, form.action)
            if urlsplit(form_url).hostname != "careers.euaa.europa.eu":
                raise ValueError("EUAA notice form left the official portal")
            euaa_form_target = payload["__EVENTTARGET"]
            response = self._eu_get_official(form_url, form_payload=payload)
            official_url = str(response.url or form_url)
            official_html = str(response.text or "")
            if not _response_looks_like_pdf(official_url, response, official_html, response.content):
                raise ValueError("EUAA EN notice form did not return its vacancy PDF")
        elif urlsplit(official_url).hostname == "www.sesarju.eu" and path == "/careers":
            directory_notice = _sesar_notice_section(official_html, official_url, external_id)
            official_url = directory_notice["notice_url"]
            response = self._eu_get_official(official_url)
            official_html = str(response.text or "")
        elif path in {"", "/careers", "/careers/vacancies"} or path.endswith("/vacancies"):
            candidates = [urljoin(official_url, a["href"]) for a in _TokenParser.parse(official_html).anchors
                          if _eu_identity_matches(a["text"], title, external_id)]
            candidates = list(dict.fromkeys(candidates))
            if len(candidates) != 1:
                raise ValueError(f"EU official directory has no unique vacancy-specific link for {external_id}")
            official_url = candidates[0]
            response = self._eu_get_official(official_url)
            official_html = str(response.text or "")
        original_language_notice = None
        if urlsplit(official_url).hostname == "curia.europa.eu" and "/fr/" in urlsplit(official_url).path:
            language_parser = _EUAnchors()
            language_parser.feed(official_html)
            link_base = urljoin(official_url, language_parser.base_href or official_url)
            english = {urljoin(link_base, a["href"]) for a in _TokenParser.parse(official_html).anchors
                       if a["text"] == "English / EN"}
            document = re.search(r"/jcms/([^/]+)/", official_url)
            if len(english) != 1 or document is None:
                raise ValueError("CURIA French notice lacks one linked English document version")
            english_url = english.pop()
            if (urlsplit(english_url).hostname != "curia.europa.eu"
                    or f"/jcms/{document.group(1)}/en/" not in urlsplit(english_url).path):
                raise ValueError("CURIA language link changes document identity")
            original_region = re.search(r"<main\b[^>]*>(.*?)</main>", official_html, re.S | re.I)
            original_content = original_region.group(1) if original_region else official_html
            original_language_notice = {
                "language": "fr", "url": official_url, "document_id": document.group(1),
                "html": _eu_html_with_base(original_content, official_html, official_url),
                "text": _clean_html(re.sub(r"<!--.*?-->", " ", original_content, flags=re.S)) or "",
                "linked_english_url": english_url,
            }
            response = self._eu_get_official(english_url)
            official_url = str(response.url or english_url)
            if f"/jcms/{document.group(1)}/en/" not in urlsplit(official_url).path:
                raise ValueError("CURIA English response changed document identity")
            official_html = str(response.text or "")
        wrapper_url = official_url
        if urlsplit(wrapper_url).hostname == "eur-lex.europa.eu":
            from jobagg.adapters.eurlex_public import render_public_notice

            job = render_public_notice(
                self.source, official_html, page_url=official_url,
                external_id=external_id, summary_url=summary_url, expected_title=title,
                summary_metadata={"listing": item, "summary": metadata},
            )
            job.raw["summary_html"] = _eu_html_with_base(summary_html, summary_html, summary_url)
            job.raw["summary_official_vacancy_url"] = summary_official_url
            job.raw["observed_official_url_override"] = route_overrides.get(summary_official_url)
            return job
        enisa_wrapper = _enisa_public_wrapper(official_html) if urlsplit(wrapper_url).hostname == "www.enisa.europa.eu" else None
        europol_node = _europol_public_vacancy(official_html, official_url, external_id, title)
        offer = _eu_lisa_offer(official_html, official_url, external_id)
        notice_html = None
        if offer is not None:
            notice_html = str(offer.get("content") or "")
            pdfs = list(dict.fromkeys(urljoin(official_url, anchor["href"])
                       for anchor in _TokenParser.parse(notice_html).anchors
                       if urlsplit(anchor["href"]).path.casefold().endswith(".pdf")))
            if len(pdfs) != 1:
                raise ValueError("eu-LISA current offer requires exactly one identifiable vacancy PDF")
            official_url = pdfs[0]
            response = self._eu_get_official(official_url)
            official_html = str(response.text or "")
        host = urlsplit(wrapper_url).hostname
        if host == "euosha.gestmax.eu":
            anchors = _TokenParser.parse(official_html).anchors
            pdfs = [urljoin(wrapper_url, a["href"]) for a in anchors
                    if a["text"].casefold() == "en" and urlsplit(a["href"]).path.casefold().endswith(".pdf")]
            if len(set(pdfs)) != 1 or "original version therefore prevails" not in (_clean_html(official_html) or "").casefold():
                raise ValueError("EU-OSHA authoritative English vacancy PDF not uniquely established")
            official_url = pdfs[0]
            notice_html = f'<a href="{html.escape(official_url, quote=True)}">Authoritative English vacancy notice</a>'
            response = self._eu_get_official(official_url)
            official_html = str(response.text or "")
        elif host == "www.etf.europa.eu":
            form = _ETFViewPDFForm()
            form.feed(official_html)
            if not form.action or not form.english or form.fields.get("form_id") != "etf_ui_attachment_dropdown_select_form1":
                raise ValueError("ETF public English View PDF form missing")
            form_url = urljoin(wrapper_url, form.action)
            if urlsplit(form_url).hostname != host:
                raise ValueError("ETF View PDF form left official host")
            payload = {**form.fields, "locale": form.english, "op": "View PDF"}
            response = self._eu_get_official(form_url, form_payload=payload)
            official_url = str(response.url or form_url)
            official_html = str(response.text or "")
        elif host in {"eurojust.tal.net", "www.cor.europa.eu", "www.enisa.europa.eu"}:
            anchors = _TokenParser.parse(official_html).anchors
            if host == "eurojust.tal.net":
                documents = [urljoin(wrapper_url, a["href"]) for a in anchors
                             if a["text"].startswith("Vacancy Notice") and ".pdf" in a["text"].casefold()]
            elif host == "www.enisa.europa.eu":
                reference = re.sub(r"[^a-z0-9]", "", external_id.casefold())
                documents = [urljoin(wrapper_url, a["href"]) for a in anchors
                             if a["text"].strip() == "Download"
                             and urlsplit(urljoin(wrapper_url, a["href"])).hostname == host
                             and re.sub(r"[^a-z0-9]", "", urlsplit(a["href"]).path.rsplit("/", 1)[-1].casefold()) == "vn" + reference + "pdf"]
            else:
                documents = [urljoin(wrapper_url, a["href"]) for a in anchors
                             if a["text"].startswith("Download ") and urlsplit(a["href"]).path.casefold().endswith(".pdf")]
            if len(set(documents)) != 1:
                raise ValueError("EU official page requires one identifiable vacancy notice document")
            official_url = documents[0]
            notice_html = f'<a href="{html.escape(official_url, quote=True)}">Official vacancy notice PDF</a>'
            response = self._eu_get_official(official_url)
            official_url = str(response.url or official_url)
            official_html = str(response.text or "")
        is_pdf = _response_looks_like_pdf(official_url, response, official_html, response.content)
        if is_pdf:
            content_text = _extract_pdf_text(response.content)
            if not content_text:
                raise ValueError("EU official vacancy PDF full text extraction failed")
            content_html = notice_html
        else:
            if _blocked_page_reason(official_html):
                raise ValueError("EU official vacancy page blocked")
            # Preserve the complete main/article element; do not apply the old
            # first-closing-div snippets, which can truncate nested notices.
            region = (re.search(r"<(main)\b[^>]*>(.*?)</\1>", official_html, re.S | re.I)
                      or re.search(r"<(article)\b[^>]*>(.*?)</\1>", official_html, re.S | re.I))
            content_html = europol_node["body"] if europol_node else (region.group(2) if region else official_html)
            content_text = _clean_html(re.sub(r"<!--.*?-->", " ", content_html, flags=re.S)) or ""
        if not _eu_identity_matches(content_text, title, external_id):
            raise ValueError(f"EU official vacancy identity missing for {external_id}")
        if not _eu_full_notice_signals(content_text):
            raise ValueError(f"EU official link lacks full duties and eligibility text for {external_id}; inspect required attachment")
        job = build_job(
            self.source, title=title, external_id=external_id,
            apply_url=official_url, source_url=summary_url,
            location=item.get("location") or metadata.get("location") or summary.location,
            department=item.get("institution") or metadata.get("institution") or summary.department,
            employment_type=item.get("employment_type") or metadata.get("grade") or summary.employment_type,
            posted_at=_listing_datetime(self.source, item, "posted_at") or summary.posted_at,
            closes_at=_listing_datetime(self.source, item, "closes_at") or metadata.get("closes_at") or summary.closes_at,
            description=(summary.description or "") + "\n\nOfficial vacancy notice\n" + content_text
                        + ("\n\nOriginal French vacancy notice\n" + original_language_notice["text"]
                           if original_language_notice and original_language_notice["text"] != content_text else ""),
            raw={**item, "parser": "eu_official_detail",
                 "summary_html": _eu_html_with_base(summary_html, summary_html, summary_url),
                 "detail_html": _eu_html_with_base(
                     content_html, official_html if not is_pdf else "", wrapper_url),
                 "official_vacancy_url": official_url,
                 "summary_official_vacancy_url": summary_official_url,
                 "observed_official_url_override": route_overrides.get(summary_official_url),
                 "official_wrapper_url": wrapper_url,
                 "enisa_public_wrapper": enisa_wrapper,
                 "official_offer_reference": offer.get("reference") if offer else None,
                 "official_notice_form_target": euaa_form_target,
                 "official_directory_notice": directory_notice,
                 "europol_public_vacancy": europol_node,
                 "original_language_notice": original_language_notice,
                 "summary_metadata": metadata,
                 "detail_url": summary_url, "detail_fetch_url": official_url,
                 "identity_verification": "official_link_and_title_or_reference",
                 "official_notice_text": content_text,
                 "required_attachment_urls": [official_url] if is_pdf else []},
        )
        job.closes_at_local = metadata.get("deadline_local")
        job.closes_tz = metadata.get("closes_tz")
        if directory_notice and urlsplit(directory_notice["directory_url"]).hostname == "www.sesarju.eu":
            # The official notice distinguishes the TA contract from its grade.
            # A summary grade is not an employment type.
            contract = re.search(
                r"\b(?:Administrator|Assistant)\s*[-–—]\s*(TA\s*2\s*\(\s*[a-z]\s*\))"
                r"\s*[-–—]\s*((?:AD|AST)\s*\d{1,2})\b", content_text, re.I)
            job.employment_type = re.sub(r"\s+", " ", contract[1]).strip() if contract else None
            job.raw["_eu_official_field_resolution"] = {
                "record_kind": "detail", "provider": "sesar_official_vacancy_pdf",
                "employment_type": job.employment_type,
                "official_grade": re.sub(r"\s+", "", contract[2]).upper() if contract else None,
                "public_contract_phrase": contract[0] if contract else None,
                "contract_type_resolved": bool(contract),
                "summary_values_retained_in": "summary_metadata",
            }
        if enisa_wrapper:
            official_title = enisa_wrapper["title"]
            compact = re.sub(r"[^a-z0-9]", "", content_text.casefold())
            if (re.sub(r"[^a-z0-9]", "", external_id.casefold()) not in compact
                    or re.sub(r"[^a-z0-9]", "", official_title.casefold()) not in compact):
                raise ValueError("ENISA wrapper title/reference differs from official PDF")
            public = enisa_wrapper["fields"]
            job.title = official_title
            job.employment_type = public.get("type of contract")
            job.department = public.get("area")
            job.location = public.get("place of employment")
            job.description = (summary.description or "") + "\n\nOfficial vacancy page\n" + enisa_wrapper["text"] + "\n\nOfficial vacancy notice\n" + content_text
            deadline = public.get("deadline for applications", "")
            job.closes_at, job.closes_at_local, job.closes_tz = None, None, None
            match = re.fullmatch(r"(\d{2}/\d{2}/\d{4}) at (\d{2}:\d{2}:\d{2}) Greek time", deadline, re.I)
            if match:
                local = datetime.strptime(match[1] + " " + match[2], "%d/%m/%Y %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Athens"))
                job.closes_at = local.astimezone(timezone.utc)
                job.closes_at_local, job.closes_tz = local.isoformat(), "Europe/Athens"
            job.raw["_eu_official_field_resolution"] = {
                "record_kind": "detail", "provider": "enisa_official_wrapper_and_pdf",
                "official_grade": public.get("function group and grade"),
                "public_fields": public, "public_deadline": deadline,
                "utc_resolved": job.closes_at is not None,
                "summary_values_retained_in": "summary_metadata",
            }
        if europol_node:
            job.title = europol_node["title"]
            job.department = europol_node.get("department") or job.department
            job.employment_type = europol_node.get("contractType") or job.employment_type
            job.posted_at = datetime.fromtimestamp(europol_node["published"], timezone.utc)
            job.closes_at = datetime.fromtimestamp(europol_node["deadline"], timezone.utc)
            job.closes_at_local = None
            job.closes_tz = None
            if "amsterdam time zone" in content_text.casefold():
                job.closes_tz = "Europe/Amsterdam"
                job.closes_at_local = job.closes_at.astimezone(ZoneInfo(job.closes_tz)).isoformat()
            job.raw["_eu_official_field_resolution"] = {
                "record_kind": "detail", "provider": "europol_public_vacancy",
                "published_epoch": europol_node["published"], "deadline_epoch": europol_node["deadline"],
                "utc_resolved": True, "public_timezone": job.closes_tz,
                "summary_values_retained_in": "summary_metadata",
            }
        if is_pdf:
            from jobagg.adapters.eu_primary_metadata import apply_public_fields, supports_reference
            if supports_reference(external_id):
                # Contract, unit and local-clock claims come from the primary
                # notice. The board's grade/agency labels remain raw evidence.
                job = apply_public_fields(job, content_text)
        return job

    def _parse_generic_links(self, html_text: str, listing_url: str) -> list[JobRecord]:
        json_ld_jobs = parse_json_ld_jobs(self.source, html_text, listing_url)
        if json_ld_jobs:
            return json_ld_jobs

        detail_jobs = []
        fetch_details = _as_bool(self.source.extra.get("fetch_details"), default=True)
        max_detail_jobs = _as_int(self.source.extra.get("max_detail_jobs"), default=100)
        seen: set[str] = set()
        links = _job_links_from_html(self.source, html_text, listing_url)
        if not links:
            structural_evidence = _verified_structural_empty_evidence(self.source, html_text)
            if structural_evidence.get("verified"):
                self.run_diagnostics.health_status = "ok_empty"
                self.run_diagnostics.empty_reason = "verified_structural_empty"
                self.run_diagnostics.zero_fetched_evidence = structural_evidence
                self.run_diagnostics.pagination_complete = True
                return []
            if _has_structural_empty_policy(self.source):
                self.run_diagnostics.health_status = "issue"
                self.run_diagnostics.empty_reason = "parser_no_match"
                self.run_diagnostics.zero_fetched_evidence = structural_evidence
                raise RuntimeError(
                    f"{self.source.id}: no job links found and structural empty markers were not verified"
                )
            empty_reason = _empty_board_reason(html_text)
            if empty_reason:
                self.run_diagnostics.health_status = "ok_empty"
                self.run_diagnostics.empty_reason = "verified_text_empty"
                self.run_diagnostics.zero_fetched_evidence = {"matched_text": empty_reason}
                self.run_diagnostics.pagination_complete = True
                return []
            raise RuntimeError(
                f"{self.source.id}: no job links found; selectors may be stale or page blocked"
            )
        for link in links:
            key = link["href"]
            if key in seen:
                continue
            seen.add(key)
            if fetch_details and len(detail_jobs) < max_detail_jobs:
                try:
                    detail_html = self.fetch_text(link["href"])
                    detail_job = parse_detail_page(self.source, detail_html, link["href"])
                    detail_jobs.append(detail_job)
                    continue
                except Exception:
                    pass
            detail_jobs.append(
                build_job(
                    self.source,
                    title=link["title"],
                    external_id=_external_id_from_url(link["href"]),
                    apply_url=link["href"],
                    source_url=link["href"],
                    raw={
                        "href": link["href"],
                        "external_id": _external_id_from_url(link["href"]),
                        "title": link["title"],
                        "parser": "public_links",
                    },
                )
            )
        return _dedupe(detail_jobs)

    def _parse_markdown_links(self, markdown_text: str, listing_url: str) -> list[JobRecord]:
        links = _job_links_from_markdown(self.source, markdown_text)
        if not links:
            structural_evidence = _verified_structural_empty_evidence(self.source, markdown_text)
            if structural_evidence.get("verified"):
                self.run_diagnostics.health_status = "ok_empty"
                self.run_diagnostics.empty_reason = "verified_structural_empty"
                self.run_diagnostics.zero_fetched_evidence = structural_evidence
                self.run_diagnostics.pagination_complete = True
                return []
            raise RuntimeError(f"{self.source.id}: no markdown job links found")
        jobs = []
        for link in links:
            jobs.append(
                build_job(
                    self.source,
                    title=link["title"],
                    external_id=_external_id_from_url(link["href"]),
                    location=link.get("location"),
                    employment_type=link.get("employment_type"),
                    closes_at=link.get("closes_at"),
                    apply_url=link["href"],
                    source_url=link["href"],
                    description=link.get("summary"),
                    raw={
                        "href": link["href"],
                        "detail_fetch_url": link.get("detail_fetch_url"),
                        "external_id": _external_id_from_url(link["href"]),
                        "title": link["title"],
                        "closes_at": link.get("closes_at"),
                        "location": link.get("location"),
                        "employment_type": link.get("employment_type"),
                        "parser": "markdown_public_links",
                        "listing_url": listing_url,
                    },
                )
            )
        return _dedupe(jobs)


def _detail_text_from_response(response: Any, detail_fetch_url: str) -> str:
    text = str(getattr(response, "text", "") or "")
    content = getattr(response, "content", b"")
    if isinstance(content, bytearray):
        content = bytes(content)
    if not isinstance(content, bytes):
        content = b""
    if _response_looks_like_pdf(detail_fetch_url, response, text, content):
        extracted = _extract_pdf_text(content)
        if extracted:
            return extracted
    return text


def _response_looks_like_pdf(url: str, response: Any, text: str, content: bytes) -> bool:
    headers = getattr(response, "headers", {}) or {}
    content_type = ""
    if hasattr(headers, "get"):
        content_type = str(headers.get("Content-Type") or headers.get("content-type") or "")
    path = urlsplit(url).path.casefold()
    return (
        "application/pdf" in content_type.casefold()
        or path.endswith(".pdf")
        or content.lstrip().startswith(b"%PDF")
        or text.lstrip().startswith("%PDF")
    )


def _extract_pdf_text(content: bytes) -> str | None:
    if not content:
        return None
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(BytesIO(content))
        chunks = [page.extract_text() or "" for page in reader.pages]
    except Exception:
        return None
    return clean_text("\n".join(chunks))


def _unssc_verified_empty(html_text: str) -> bool:
    """Accept the explicit empty employment view, never a footer or partial row."""
    main = re.search(r"<main\b[^>]*>(.*?)</main>", html_text, re.I | re.S)
    if not main:
        return False
    body = main.group(1)
    if "views-field-field-vacancy-code" in body or re.search(r"<tr\b", body, re.I):
        return False
    view = re.search(
        r'<div\b[^>]*class=["\'][^"\']*\bview-employment\b[^"\']*["\'][^>]*>'
        r'\s*<div\b[^>]*class=["\']view-empty["\'][^>]*>(.*?)</div>',
        body, re.I | re.S,
    )
    marker = "There are no vacancies at present, please visit this page regularly for updates."
    return bool(view and clean_text(_clean_html(view.group(1))) == marker)


def parse_unssc_jobs(
    source: OrganizationSource,
    html_text: str,
    listing_url: str,
) -> list[JobRecord]:
    jobs = []
    for row in _table_rows(html_text):
        code = _cell_text(row, "views-field-field-vacancy-code-1")
        title_cell = _cell_html(row, "views-field-title")
        title, document_url = _first_anchor(title_cell, listing_url)
        if not code or not title:
            continue
        apply_cell = _cell_html(row, "views-field-nid")
        _, apply_url = _first_anchor(apply_cell, listing_url)
        posted_at = _time_datetime(_cell_html(row, "views-field-field-issue-date"))
        closes_at = _time_datetime(_cell_html(row, "views-field-field-application-deadline"))
        jobs.append(
            build_job(
                source,
                title=title,
                external_id=code,
                posted_at=posted_at,
                closes_at=closes_at,
                apply_url=apply_url or document_url or listing_url,
                source_url=document_url or listing_url,
                raw={
                    "code": code,
                    "external_id": code,
                    "title": title,
                    "document_url": document_url,
                    "apply_url": apply_url,
                    "parser": "unssc_drupal",
                },
            )
        )
    return jobs


def parse_eu_careers_jobs(
    source: OrganizationSource,
    html_text: str,
    listing_url: str,
) -> list[JobRecord]:
    jobs = []
    for row in _table_rows(html_text):
        title_cell = _cell_html(row, "views-field-title")
        title, detail_url = _first_anchor(title_cell, listing_url)
        if not title or not detail_url:
            continue
        if "/job-opportunities/" not in urlsplit(detail_url).path:
            continue
        grade = _cell_text(row, "views-field-field-epso-grade")
        domain = _cell_text(row, "views-field-field-epso-domain")
        institution = _cell_text(row, "views-field-field-epso-institution")
        location = _cell_text(row, "views-field-field-epso-location")
        posted_at = _time_datetime(_cell_html(row, "views-field-created"))
        closes_at = _time_datetime(_cell_html(row, "views-field-field-epso-deadline"))
        jobs.append(
            build_job(
                source,
                title=title,
                external_id=_external_id_from_url(detail_url),
                location=location,
                department="; ".join(part for part in (institution, domain) if part) or None,
                employment_type=grade,
                posted_at=posted_at,
                closes_at=closes_at,
                apply_url=detail_url,
                source_url=detail_url,
                raw={
                    "grade": grade,
                    "domain": domain,
                    "institution": institution,
                    "location": location,
                    "parser": "eu_careers_open_vacancies",
                    "href": detail_url,
                    "external_id": _external_id_from_url(detail_url),
                    "title": title,
                    "posted_at": posted_at,
                    "closes_at": closes_at,
                    "employment_type": grade,
                },
            )
        )
    return _dedupe(jobs)


def parse_json_ld_jobs(
    source: OrganizationSource,
    html_text: str,
    page_url: str,
) -> list[JobRecord]:
    jobs = []
    for payload in _json_ld_payloads(html_text):
        for item in _find_job_postings(payload):
            external_id = _identifier_value(item.get("identifier")) or _external_id_from_url(page_url)
            apply_url = item.get("url") or page_url
            jobs.append(
                build_job(
                    source,
                    title=item.get("title") or _title_from_html(html_text),
                    external_id=external_id,
                    location=_json_ld_location(item.get("jobLocation")),
                    department=_organization_name(item.get("hiringOrganization")),
                    employment_type=item.get("employmentType"),
                    posted_at=item.get("datePosted"),
                    closes_at=item.get("validThrough") or _deadline_from_text(item.get("description")),
                    apply_url=str(apply_url),
                    source_url=page_url,
                    description=item.get("description"),
                    raw={**item, "parser": "json_ld"},
                )
            )
    return _dedupe(jobs)


def parse_detail_page(
    source: OrganizationSource,
    html_text: str,
    page_url: str,
) -> JobRecord:
    if source.id == "opcw_talentsoft_candidatespace":
        from jobagg.vacancy_outcomes import VacancyUnavailable, unavailable_template
        identity = re.search(r"_(\d+)\.aspx$", urlsplit(page_url).path)
        if identity and unavailable_template(source.id, identity[1], page_url, page_url, html_text):
            raise VacancyUnavailable("OPCW explicitly reports this vacancy no longer exists")
    json_ld_jobs = parse_json_ld_jobs(source, html_text, page_url)
    if json_ld_jobs:
        if source.id == "cern_custom_html":
            from jobagg.adapters.cern_public import apply_public_fields
            return apply_public_fields(json_ld_jobs[0], html_text, page_url)
        if source.id == "unu_recruitee":
            # JSON-LD joins description and requirements without the visible
            # Qualifications heading. Retain the complete public detail tab;
            # hidden application-form panels are outside vacancy text.
            panel = _VisibleRecruiteePanel(html_text)
            if panel.count != 1 or not panel.html:
                raise ValueError("UNU visible public job-detail tab is missing or ambiguous")
            job = json_ld_jobs[0]
            job.description = clean_text(panel.html)
            job.raw.update(detail_html=html_text, parser="recruitee_public_detail_tab")
            from jobagg.adapters.unu_public import apply_public_fields
            return apply_public_fields(job, html_text, page_url)
        return json_ld_jobs[0]

    content_html = _mainish_html(html_text)
    parsed = _TokenParser.parse(html_text)
    content_parsed = _TokenParser.parse(content_html)
    tokens = [token["text"] for token in content_parsed.tokens if token["type"] == "text"]
    title = (
        parsed.meta.get("og:title")
        or parsed.meta.get("twitter:title")
        or _title_from_html(html_text)
        or parsed.title
    )
    if source.id == "osce_custom_html":
        heading = re.search(r"<h1\b[^>]*>(.*?)</h1>", html_text, re.I | re.S)
        if not heading:
            raise ValueError("OSCE public vacancy title heading missing")
        title = clean_text(heading.group(1))
    elif source.id == "itcilo_custom_html":
        title = _itcilo_vacancy_title(html_text)
    elif source.id == "opcw_talentsoft_candidatespace":
        match = re.search(r"<h1\b[^>]*>(.*?)</h1>", html_text, re.I | re.S)
        if not match:
            raise ValueError("OPCW public vacancy title heading missing")
        title = clean_text(match.group(1))
    else:
        title = _strip_site_suffix(title)
    closes_at = _field_after(tokens, ("closing date", "deadline", "deadline for application", "application deadline"))
    location = _field_after(tokens, ("location", "duty station", "job location"))
    grade = _normalize_grade_field(_field_after(tokens, ("grade range", "grade", "post level")))
    contract_type = _field_after(tokens, ("contract type", "contract", "type"))
    posted_at = _field_after(tokens, ("posted date", "date posted", "publication date", "posted on"))
    description = _clean_html(content_html)
    job = build_job(
        source,
        title=title,
        external_id=_external_id_from_url(page_url),
        location=location,
        employment_type=" / ".join(part for part in (grade, contract_type) if part) or None,
        posted_at=posted_at,
        closes_at=closes_at or _deadline_from_text(description),
        apply_url=page_url,
        source_url=page_url,
        description=description,
        raw={
            "grade": grade,
            "contract_type": contract_type,
            "parser": "static_detail",
            "href": page_url,
            **({"_itcilo_title_resolution": {"selector": "h1.titlevacancy", "public_title": title}}
               if source.id == "itcilo_custom_html" else {}),
        },
    )

    if source.id == "cern_custom_html":
        from jobagg.adapters.cern_public import apply_public_fields
        return apply_public_fields(job, html_text, page_url)

    if source.id == "osce_custom_html":
        issue_date = _field_after(tokens, ("issue date",))
        job.employment_type = contract_type
        job.posted_at, job.closes_at = None, None
        job.closes_at_local, job.closes_tz = None, None
        job.raw.update(detail_html=html_text, _osce_public_field_resolution={
            "record_kind": "detail", "public_title": title,
            "public_contract_type": contract_type, "public_grade": grade,
            "public_issue_date": issue_date, "public_closing_date": closes_at,
            "posting_time_resolved": False, "utc_resolved": False,
            "reason": "Public calendar dates do not establish an exact UTC instant",
        })
    if source.id == "opcw_talentsoft_candidatespace":
        # The public vacancy specifies a day plus a site-wide local closing
        # time. A date-only parse must never silently become midnight UTC.
        job.raw["detail_html"] = html_text
        closing_day = str(closes_at or "").strip()
        job.closes_at = None
        job.closes_at_local = closing_day or None
        job.closes_tz = None
        notice = re.search(
            r"all vacancies will close at (\d{1,2}:\d{2}) The Netherlands local time",
            description, re.I,
        )
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", closing_day) and notice:
            local = datetime.strptime(closing_day + " " + notice.group(1), "%d/%m/%Y %H:%M")
            local = local.replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
            job.closes_at = local.astimezone(timezone.utc)
            job.closes_at_local = local.replace(tzinfo=None).isoformat()
            job.closes_tz = "Europe/Amsterdam"
            job.raw["_opcw_deadline_evidence"] = {
                "closing_date_label": closing_day,
                "local_time_notice": notice.group(0),
                "timezone": "Europe/Amsterdam",
                "precision": "explicit_public_local_time",
            }
        else:
            job.raw["_opcw_deadline_evidence"] = {
                "closing_date_label": closing_day,
                "precision": "calendar_date_time_or_timezone_unverified",
            }
    return job


class _VisibleRecruiteePanel(HTMLParser):
    """Retain only the non-hidden public detail tab, with balanced HTML depth."""

    _void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, body: str):
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.target_depth = None
        self.count = 0
        self.parts: list[str] = []
        self.feed(body)
        self.html = "".join(self.parts)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag not in self._void:
            self.depth += 1
        if attributes.get("role") == "tabpanel" and "hidden" not in attributes:
            self.target_depth = self.depth
            self.count += 1
        if self.target_depth is not None:
            self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if self.target_depth is not None:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.target_depth is not None:
            self.parts.append(f"</{tag}>")
            if self.depth == self.target_depth:
                self.target_depth = None
        if tag not in self._void:
            self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.target_depth is not None:
            self.parts.append(data)

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")


def parse_markdown_detail_page(
    source: OrganizationSource,
    markdown_text: str,
    page_url: str,
) -> JobRecord:
    title = _markdown_title(markdown_text) or _external_id_from_url(page_url) or page_url
    content = _markdown_content(markdown_text)
    description = _markdown_to_text(content)
    employment_type = _grade_from_title(title)
    return build_job(
        source,
        title=title,
        external_id=_external_id_from_url(page_url),
        employment_type=employment_type,
        closes_at=_deadline_from_text(description),
        apply_url=page_url,
        source_url=page_url,
        description=description,
        raw={
            "grade": employment_type,
            "parser": "markdown_detail",
            "href": page_url,
        },
    )


def _job_links_from_markdown(
    source: OrganizationSource,
    markdown_text: str,
) -> list[dict[str, str]]:
    content = _markdown_content(markdown_text)
    lines = [line.strip() for line in content.splitlines()]
    heading_pattern = re.compile(r"^#{1,4}\s+\[(?P<title>[^\]]+)\]\((?P<href>[^)]+)\)")
    jobs: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        match = heading_pattern.match(line)
        if not match:
            continue
        title = clean_text(match.group("title"))
        href = html.unescape(match.group("href"))
        if not title or not _markdown_href_allowed(source, href):
            continue
        block_end = len(lines)
        for next_index in range(index + 1, len(lines)):
            if heading_pattern.match(lines[next_index]):
                block_end = next_index
                break
            if lines[next_index].casefold() == "ipu job application":
                block_end = next_index
                break
        block = [line for line in lines[index + 1 : block_end] if line]
        category = _markdown_previous_category(lines, index)
        closes_at = _markdown_deadline(block)
        location = _markdown_location(block)
        summary = _markdown_summary(block)
        employment_type = _grade_from_title(title) or category
        jobs.append(
            {
                "title": title,
                "href": href,
                "detail_fetch_url": _reader_detail_url(source, href),
                "closes_at": closes_at or "",
                "location": location or "",
                "employment_type": employment_type or "",
                "summary": summary or "",
            }
        )
    return jobs


def _job_links_from_html(
    source: OrganizationSource,
    html_text: str,
    listing_url: str,
) -> list[dict[str, str]]:
    hint = str(source.extra.get("job_link_selector_hint") or "").lower()
    include_hints = source.extra.get("job_link_hints") or []
    if isinstance(include_hints, str):
        include_hints = [include_hints]
    if hint:
        include_hints = [hint, *include_hints]
    include_hints = [str(item).lower() for item in include_hints]

    exclude_hints = source.extra.get("exclude_link_selector_hint") or []
    if isinstance(exclude_hints, str):
        exclude_hints = [exclude_hints]
    exclude_hints = [str(item).lower() for item in exclude_hints]

    links = []
    for anchor in _TokenParser.parse(html_text).anchors:
        href = urljoin(listing_url, anchor["href"])
        title = _clean(anchor["text"])
        if not title or _is_generic_link_text(title):
            continue
        lowered = href.lower()
        if include_hints and not any(item in lowered for item in include_hints):
            continue
        if exclude_hints and any(item in lowered for item in exclude_hints):
            continue
        if canonical_url(href) == canonical_url(listing_url):
            continue
        links.append({"href": href, "title": title})
    return links


def _blocked_page_reason(html_text: str) -> str | None:
    lowered = html_text.casefold()
    if "attention required! | cloudflare" in lowered or "sorry, you have been blocked" in lowered:
        return "Cloudflare"
    return None


def _empty_board_reason(html_text: str) -> str | None:
    lowered = _clean(html_text).casefold()
    empty_markers = (
        "there are no vacancies available",
        "there are no vacancies",
        "there are no internships available",
        "there are no internships",
        "no current vacancies",
        "no vacancies available",
        "no vacancies at this time",
        "no jobs available",
        "no open positions",
    )
    for marker in empty_markers:
        if marker in lowered:
            return marker
    return None


def _has_structural_empty_policy(source: OrganizationSource) -> bool:
    policy = source.extra.get("empty_policy")
    return isinstance(policy, dict) and policy.get("mode") == "verified_structural_empty"


def _verified_structural_empty_evidence(
    source: OrganizationSource,
    html_text: str,
) -> dict[str, Any]:
    policy = source.extra.get("empty_policy")
    if not isinstance(policy, dict) or policy.get("mode") != "verified_structural_empty":
        return {}
    text = _clean(html_text)
    lowered = text.casefold()
    required = [str(marker) for marker in policy.get("required_page_markers") or []]
    missing_markers = [marker for marker in required if marker.casefold() not in lowered]
    expected_counts = policy.get("required_text_counts") or {}
    observed_counts = {
        str(marker): lowered.count(str(marker).casefold()) for marker in expected_counts
    }
    required_counts_match = all(
        observed_counts[str(marker)] == int(count)
        for marker, count in expected_counts.items()
    )
    section_start = str(policy.get("section_start_text") or "")
    section_start_found = not section_start or section_start.casefold() in lowered
    section_end_any = [str(marker) for marker in policy.get("section_end_any_text") or []]
    section_end_found = not section_end_any or any(marker.casefold() in lowered for marker in section_end_any)
    link_patterns = [str(pattern).casefold() for pattern in policy.get("job_link_patterns") or []]
    ignored_text = {str(value).casefold() for value in policy.get("ignore_link_text") or []}
    job_nodes = []
    for anchor in _TokenParser.parse(html_text).anchors:
        title = _clean(anchor["text"])
        href = str(anchor["href"])
        if title.casefold() in ignored_text:
            continue
        if link_patterns and not any(pattern in href.casefold() for pattern in link_patterns):
            continue
        if title:
            job_nodes.append({"title": title, "href": href})
    verified = not missing_markers and required_counts_match and section_start_found and section_end_found and not job_nodes
    return {
        "verified": verified,
        "required_markers_found": not missing_markers,
        "missing_markers": missing_markers,
        "required_text_counts_match": required_counts_match,
        "observed_text_counts": observed_counts,
        "section_start_found": section_start_found,
        "section_end_found": section_end_found,
        "job_nodes_found": len(job_nodes),
        "job_nodes_sample": job_nodes[:5],
    }


def _table_rows(html_text: str) -> list[str]:
    return re.findall(r"<tr\b[^>]*>(.*?)</tr>", html_text, flags=re.IGNORECASE | re.DOTALL)


def _cell_html(row_html: str, class_name: str) -> str:
    pattern = re.compile(
        rf"<td\b(?=[^>]*\bclass=[\"'][^\"']*\b{re.escape(class_name)}\b)[^>]*>(?P<body>.*?)</td>",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(row_html)
    return match.group("body") if match else ""


def _cell_text(row_html: str, class_name: str) -> str | None:
    return clean_text(_cell_html(row_html, class_name))


def _first_anchor(html_text: str, base_url: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title>.*?)</a>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, None
    return clean_text(match.group("title")), urljoin(base_url, html.unescape(match.group("href")))


def _time_datetime(html_text: str) -> str | None:
    match = re.search(r"<time\b[^>]*datetime=[\"'](?P<value>[^\"']+)[\"']", html_text, re.I)
    if match:
        return match.group("value")
    return clean_text(html_text)


def _json_ld_payloads(html_text: str) -> list[Any]:
    payloads = []
    for body in re.findall(
        r"<script\b(?=[^>]*type=[\"']application/ld\+json[\"'])[^>]*>(.*?)</script>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            payloads.append(json.loads(html.unescape(body.strip())))
        except json.JSONDecodeError:
            continue
    return payloads


def _find_job_postings(payload: Any) -> list[dict[str, Any]]:
    found = []
    if isinstance(payload, dict):
        item_type = payload.get("@type")
        if item_type == "JobPosting" or (isinstance(item_type, list) and "JobPosting" in item_type):
            found.append(payload)
        for value in payload.values():
            found.extend(_find_job_postings(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_find_job_postings(item))
    return found


def _identifier_value(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("value", "name", "@id"):
            if value.get(key):
                return str(value[key])
    if value:
        return str(value)
    return None


def _json_ld_location(value: object) -> str | None:
    if isinstance(value, list):
        return "; ".join(filter(None, (_json_ld_location(item) for item in value))) or None
    if not isinstance(value, dict):
        return clean_text(value)
    address = value.get("address")
    if isinstance(address, dict):
        parts = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        return ", ".join(str(part) for part in parts if part)
    return clean_text(value.get("name"))


def _organization_name(value: object) -> str | None:
    if isinstance(value, dict):
        return clean_text(value.get("name"))
    return clean_text(value)


def _markdown_content(markdown_text: str) -> str:
    marker = "Markdown Content:"
    if marker in markdown_text:
        return markdown_text.split(marker, 1)[1].strip()
    return markdown_text.strip()


def _markdown_title(markdown_text: str) -> str | None:
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Title:"):
            return clean_text(stripped.split(":", 1)[1])
    for line in _markdown_content(markdown_text).splitlines():
        match = re.match(r"^#\s+(?P<title>.+)$", line.strip())
        if match:
            return clean_text(match.group("title"))
    return None


def _markdown_href_allowed(source: OrganizationSource, href: str) -> bool:
    hints = source.extra.get("job_link_hints") or source.extra.get("job_link_selector_hint") or []
    if isinstance(hints, str):
        hints = [hints]
    hints = [str(hint).casefold() for hint in hints]
    return not hints or any(hint in href.casefold() for hint in hints)


def _markdown_previous_category(lines: list[str], index: int) -> str | None:
    ignored = {"", "vacancies list", "deadline:"}
    for previous in reversed(lines[:index]):
        text = clean_text(previous)
        if not text or text.casefold() in ignored:
            continue
        if text.startswith("[") or text.startswith("*") or text.startswith("#"):
            continue
        if len(text) <= 60:
            return text
        return None
    return None


def _markdown_deadline(block: list[str]) -> str | None:
    for index, line in enumerate(block):
        if line.rstrip(":").casefold() != "deadline":
            continue
        for next_line in block[index + 1 : index + 5]:
            value = clean_text(next_line)
            if value:
                return value
    return None


def _markdown_location(block: list[str]) -> str | None:
    for index, line in enumerate(block):
        if line.casefold().startswith("[read more]"):
            candidates = [clean_text(value) for value in block[max(0, index - 4) : index]]
            candidates = [
                value
                for value in candidates
                if value and len(value) <= 80 and not value.endswith(".") and value.casefold() != "deadline:"
            ]
            if len(candidates) >= 2:
                return ", ".join(candidates[-2:])
            if candidates:
                return candidates[-1]
    return None


def _markdown_summary(block: list[str]) -> str | None:
    summary_lines = []
    skipping_deadline = False
    for line in block:
        lowered = line.casefold()
        if lowered == "deadline:":
            skipping_deadline = True
            continue
        if skipping_deadline:
            skipping_deadline = False
            continue
        if lowered.startswith("[read more]"):
            break
        if len(line) <= 80 and not line.endswith("."):
            continue
        summary_lines.append(line)
    return _markdown_to_text("\n".join(summary_lines)) or None


def _markdown_to_text(value: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", value)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", " ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[*-]\s+", " ", text, flags=re.MULTILINE)
    text = text.replace("**", " ").replace("__", " ").replace("`", " ")
    return clean_text(text) or ""


def _grade_from_title(value: object | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"\((?P<grade>(?:[PD]-?\d|G-?\d|NO[A-D]?))\)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group("grade").replace("-", "").upper()


def _reader_detail_url(source: OrganizationSource, href: str) -> str | None:
    template = source.extra.get("detail_fetch_url_template") or source.extra.get("reader_proxy_url_template")
    if not template:
        return None
    return str(template).format(url=href)


def _listing_datetime(
    source: OrganizationSource,
    item: dict[str, Any],
    key: str,
) -> Any:
    value = item.get(key)
    if not value:
        return None
    return parse_datetime(value, date_locale=source.extra.get("date_locale"))


def _field_after(tokens: list[str], labels: tuple[str, ...]) -> str | None:
    label_set = {label.casefold() for label in labels}
    all_labels = label_set | {
        "closing date",
        "deadline",
        "deadline for application",
        "application deadline",
        "location",
        "duty station",
        "job location",
        "grade",
        "grade range",
        "post level",
        "contract type",
        "contract",
        "type",
        "posted date",
        "date posted",
        "publication date",
        "posted on",
    }
    for index, token in enumerate(tokens):
        text = _clean(token)
        lowered = text.rstrip(":").casefold()
        for label in label_set:
            prefix = f"{label}:"
            if lowered == label:
                for next_token in tokens[index + 1 : index + 8]:
                    candidate = _clean(next_token)
                    if candidate and candidate.rstrip(":").casefold() not in all_labels:
                        return candidate
            if text.casefold().startswith(prefix):
                value = text[len(prefix) :].strip()
                if value:
                    return value
    return None


def _normalize_grade_field(value: str | None) -> str | None:
    text = _clean(value)
    if not text:
        return None
    match = re.fullmatch(r"(?:grade\s*)?(?P<level>[2-8])", text, flags=re.IGNORECASE)
    if match:
        return f"Grade {match.group('level')}"
    return text


def _deadline_from_text(value: object | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    for pattern in (
        r"(?:Application\s+Deadline|Application\s+deadline|Deadline|Closing\s+Date)\s*:?\s*(?P<value>\d{1,2}\s+[A-Za-z]+\s+\d{4})",
        r"(?:Application\s+Deadline|Application\s+deadline|Deadline|Closing\s+Date)\s*:?\s*(?P<value>\d{1,2}/\d{1,2}/\d{4})",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group("value")
    return None


class _ITCILOVacancyHeading(HTMLParser):
    def __init__(self, html_text: str):
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self.parts: list[str] | None = None
        self.feed(html_text)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "h1" and "titlevacancy" in (dict(attrs).get("class") or "").split():
            if self.parts is not None:
                raise ValueError("ITCILO vacancy title heading is malformed")
            self.parts = []

    def handle_data(self, data):
        if self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "h1" and self.parts is not None:
            self.headings.append(clean_text(" ".join(self.parts)) or "")
            self.parts = None


def _itcilo_vacancy_title(html_text: str) -> str:
    parser = _ITCILOVacancyHeading(html_text)
    if parser.parts is not None or len(parser.headings) != 1 or not parser.headings[0]:
        raise ValueError("ITCILO public vacancy title heading missing or ambiguous")
    return parser.headings[0]


def _title_from_html(html_text: str) -> str | None:
    for pattern in (
        r"<h1\b[^>]*>(?P<title>.*?)</h1>",
        r"<title\b[^>]*>(?P<title>.*?)</title>",
    ):
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(match.group("title"))
    return None


def _strip_site_suffix(value: object | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    return re.split(r"\s+[|-]\s+", text, maxsplit=1)[0].strip()


def _mainish_html(html_text: str) -> str:
    detail_patterns = (
        r"<div\b(?=[^>]*id=[\"']contenu-ficheoffre[\"'])[^>]*>(?P<body>.*?)</div>\s*</div>",
        r"<div\b(?=[^>]*id=[\"']detail_offre[\"'])[^>]*>(?P<body>.*?)</div>\s*</div>",
        r"<div\b(?=[^>]*class=[\"'][^\"']*\bjob_description\b)[^>]*>(?P<body>.*?)</div>\s*</div>",
        r"<div\b(?=[^>]*id=[\"']description_box[\"'])[^>]*>(?P<body>.*?)</div>\s*</div>",
        r"<div\b(?=[^>]*id=[\"']job_details_content[\"'])[^>]*>(?P<body>.*?)</div>\s*</div>",
    )
    for pattern in detail_patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group("body")
    for tag in ("main", "article"):
        match = re.search(
            rf"<{tag}\b[^>]*>(?P<body>.*?)</{tag}>",
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return match.group("body")
    return html_text


def _external_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    for key in ("job", "job_id", "id", "vacancy", "vacancy_code"):
        if query.get(key):
            return query[key]
    match = re.search(r"(?:_|/)(?P<id>\d{3,})(?:\.[a-z]+)?$", parts.path)
    if match:
        return match.group("id")
    path = parts.path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else None


def _is_generic_link_text(value: str) -> bool:
    return value.casefold() in {
        "apply",
        "apply now",
        "apply for job",
        "apply for vacancy",
        "back",
        "careers",
        "employment",
        "home",
        "job openings",
        "jobs",
        "read more",
        "view all",
        "view job",
        "vacancies",
    }


def _dedupe(jobs: list[JobRecord]) -> list[JobRecord]:
    deduped = []
    seen = set()
    for job in jobs:
        key = job.identity_key()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(job)
    return deduped


def _clean(value: object | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(value))).strip()


class _TokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[dict[str, str]] = []
        self.anchors: list[dict[str, str]] = []
        self.meta: dict[str, str] = {}
        self.title: str | None = None
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self._capture_title = False
        self._title_parts: list[str] = []
        self._ignored_tag: str | None = None

    @classmethod
    def parse(cls, html_text: str) -> "_TokenParser":
        parser = cls()
        parser.feed(html_text)
        parser.close()
        return parser

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style"}:
            self._ignored_tag = tag
            return
        if tag == "meta":
            key = attrs_dict.get("property") or attrs_dict.get("name")
            content = attrs_dict.get("content")
            if key and content:
                self.meta[key.lower()] = content
        elif tag == "title":
            self._capture_title = True
            self._title_parts = []
        elif tag == "a" and attrs_dict.get("href") and self._anchor_href is None:
            self._anchor_href = attrs_dict["href"]
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignored_tag == tag:
            self._ignored_tag = None
            return
        if tag == "title" and self._capture_title:
            self.title = _clean(" ".join(self._title_parts))
            self._capture_title = False
        if tag == "a" and self._anchor_href is not None:
            text = _clean(" ".join(self._anchor_text))
            if text:
                item = {"type": "a", "text": text, "href": self._anchor_href}
                self.tokens.append(item)
                self.anchors.append(item)
            self._anchor_href = None
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._ignored_tag:
            return
        text = _clean(data)
        if not text:
            return
        if self._capture_title:
            self._title_parts.append(text)
        if self._anchor_href is not None:
            self._anchor_text.append(text)
        else:
            self.tokens.append({"type": "text", "text": text, "href": ""})


class _EUAnchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []
        self.base_href = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.anchors.append(dict(attrs))
        elif tag == "base" and self.base_href is None:
            self.base_href = dict(attrs).get("href")


class _EUAAVacancyForm(HTMLParser):
    """Select only an EN notice control from the exact public vacancy panel."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.action = ""
        self.fields = {}
        self.panels = []
        self.depth = 0
        self.panel = None
        self.anchor = None
        self.in_form = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.in_form = attrs.get("id") == "aspnetForm" and attrs.get("method", "").casefold() == "post"
            if self.in_form:
                self.action = attrs.get("action", "")
        if not self.in_form:
            return
        if tag == "input" and attrs.get("type") == "hidden" and attrs.get("name", "").startswith("__"):
            self.fields[attrs["name"]] = attrs.get("value", "")
        if tag == "div":
            self.depth += 1
            if "vacancy-list" in attrs.get("class", "").split():
                self.panel = {"depth": self.depth, "text": [], "links": []}
        if tag == "a" and self.panel is not None:
            self.anchor = {"href": attrs.get("href", ""), "text": []}

    def handle_data(self, data):
        if self.panel is not None:
            self.panel["text"].append(data)
        if self.anchor is not None:
            self.anchor["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None and self.panel is not None:
            self.panel["links"].append(self.anchor)
            self.anchor = None
        if tag == "div":
            if self.panel is not None and self.panel["depth"] == self.depth:
                self.panels.append(self.panel)
                self.panel = None
            self.depth = max(0, self.depth-1)
        if tag == "form":
            self.in_form = False

    def notice_payload(self, external_id):
        def normalize(text):
            return re.sub(r"[^a-z0-9]", "", text.casefold())
        matches = [p for p in self.panels if normalize(external_id) in normalize(" ".join(p["text"]))]
        if len(matches) != 1 or not self.fields.get("__VIEWSTATE") or not self.fields.get("__EVENTVALIDATION"):
            raise ValueError("EUAA exact vacancy panel or fresh public form state missing")
        targets = []
        for link in matches[0]["links"]:
            if clean_text(" ".join(link["text"])) != "EN":
                continue
            match = re.fullmatch(r"javascript:__doPostBack\('([^']+)',''\)", link["href"])
            if match and re.fullmatch(r"ctl00\$cphMain\$RPVacancyCategories\$ctl\d+\$RPVacancies\$ctl\d+\$rptVacancyNoticeTranslations\$ctl\d+\$ctl\d+", match.group(1)):
                targets.append(match.group(1))
        if len(targets) != 1:
            raise ValueError("EUAA exact vacancy must have one EN notice download control")
        return {**self.fields, "__EVENTTARGET": targets[0], "__EVENTARGUMENT": ""}


def _eu_html_with_base(fragment: str | None, document_html: str, page_url: str) -> str | None:
    """Retain each page's URL context when saving a vacancy HTML fragment."""
    if fragment is None:
        return None
    parser = _EUAnchors()
    parser.feed(document_html)
    base_url = urljoin(page_url, parser.base_href or page_url)
    return f'<base href="{html.escape(base_url, quote=True)}">' + fragment


def _eu_next_page(html_text: str, page_url: str) -> str | None:
    parser = _EUAnchors()
    parser.feed(html_text)
    links = [a.get("href") for a in parser.anchors
             if a.get("aria-label", "").casefold() == "go to next page"]
    if not links:
        return None
    if len(set(links)) != 1:
        raise ValueError("EU Careers ambiguous next-page links")
    target = urljoin(page_url, links[0])
    current, following = urlsplit(page_url), urlsplit(target)
    if (following.scheme, following.netloc, following.path) != (current.scheme, current.netloc, current.path):
        raise ValueError("EU Careers pagination left the configured listing scope")
    old_query, new_query = dict(parse_qsl(current.query)), dict(parse_qsl(following.query))
    if int(new_query.pop("page", "-1")) != int(old_query.pop("page", "0")) + 1 or new_query != old_query:
        raise ValueError("EU Careers pagination query did not advance within scope")
    return target


def _eu_page_links(html_text: str, page_url: str) -> list[str]:
    parser = _EUAnchors()
    parser.feed(html_text)
    current = urlsplit(page_url)
    original = dict(parse_qsl(current.query))
    original.pop("page", None)
    found = set()
    for anchor in parser.anchors:
        label = anchor.get("aria-label", "").casefold()
        if not re.fullmatch(r"go to (?:(?:next|previous) page|page \d+)", label):
            continue
        target = urljoin(page_url, anchor.get("href", ""))
        parsed = urlsplit(target)
        query = dict(parse_qsl(parsed.query))
        page = query.pop("page", "")
        if ((parsed.scheme, parsed.netloc, parsed.path) != (current.scheme, current.netloc, current.path)
                or query != original or not page.isdigit()):
            raise ValueError("EU Careers pagination left the configured listing scope")
        # The bare configured URL already represents page zero.
        if int(page) == 0:
            continue
        found.add(target)
    return sorted(found, key=lambda url: int(dict(parse_qsl(urlsplit(url).query))["page"]))


def _eu_vacancy_url(html_text: str, page_url: str) -> str:
    match = re.search(r'field--name-field-epso-link[^>]*>.*?<a\b[^>]*href=["\']([^"\']+)',
                      html_text, flags=re.S | re.I)
    if not match:
        raise ValueError("EU Careers summary has no official Link to vacancy")
    return urljoin(page_url, html.unescape(match.group(1)))


def _eu_summary_metadata(html_text: str) -> dict[str, str]:
    labels = {"domain(s)": "domain", "reference number": "reference", "deadline": "deadline_local",
              "location(s):": "location", "grade:": "grade", "institution/agency": "institution",
              "type of contract": "contract_type", "link to vacancy": "official_link"}
    result: dict[str, list[str]] = {}
    field = None
    for token in _TokenParser.parse(_mainish_html(html_text)).tokens:
        text = token["text"]
        label = labels.get(text.casefold())
        if label:
            field = label
            result.setdefault(field, [])
        elif field:
            if field == "official_link":
                break
            result[field].append(text)
    metadata = {key: "; ".join(values) for key, values in result.items() if values}
    deadline = metadata.get("deadline_local", "")
    if re.fullmatch(r"\d{2}/\d{2}/\d{4} - \d{2}:\d{2} \(Brussels time\)", deadline):
        local = datetime.strptime(deadline, "%d/%m/%Y - %H:%M (Brussels time)")
        metadata["closes_at"] = local.replace(tzinfo=ZoneInfo("Europe/Brussels")).isoformat()
        metadata["closes_tz"] = "Europe/Brussels"
    return metadata


class _ENISAWrapperSections(HTMLParser):
    """Capture the observed public title/body/application fields by DOM scope."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.active = None
        self.parts = []
        self.sections = {}

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        key = ("title" if tag == "h1" else "body" if "field--name-body" in classes
               else "how_to_apply" if "field--name-field-how-to-apply-content" in classes else None)
        if key and self.active is None:
            if key in self.sections:
                raise ValueError("ENISA public wrapper has duplicate scoped fields")
            self.active, self.parts = (key, self.depth), []
        if self.active:
            self.parts.append(self.get_starttag_text())
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            return
        self.depth -= 1
        if self.active:
            self.parts.append(f"</{tag}>")
            if self.depth == self.active[1]:
                self.sections[self.active[0]] = "".join(self.parts)
                self.active = None

    def handle_data(self, data):
        if self.active:
            self.parts.append(html.escape(data))


def _enisa_public_wrapper(html_text: str) -> dict[str, Any]:
    main = re.findall(r"<main\b[^>]*>(.*?)</main>", html_text, re.S | re.I)
    if len(main) != 1:
        raise ValueError("ENISA wrapper lacks one public main region")
    parser = _ENISAWrapperSections()
    parser.feed(main[0])
    sections = parser.sections
    if not all(sections.get(key) for key in ("title", "body", "how_to_apply")):
        raise ValueError("ENISA public wrapper lacks title/body/application sections")
    fields = {}
    for label, value in re.findall(r"<strong\b[^>]*>(.*?)</strong>(.*?)(?=<br\b|</p>|<strong\b)", sections["body"], re.S | re.I):
        key = (_clean_html(label) or "").rstrip(": ").casefold()
        if key in fields:
            raise ValueError("ENISA public wrapper has duplicate metadata labels")
        fields[key] = _clean_html(value)
    return {"title": _clean_html(sections["title"]), "fields": fields,
            "sections_html": sections,
            "text": "\n\n".join(_clean_html(sections[key]) or "" for key in ("title", "body", "how_to_apply"))}


def _sesar_notice_section(html_text: str, page_url: str, external_id: str) -> dict[str, str]:
    """Bind generic notice links to an exact public heading reference.

    SESAR can publish external and inter-agency versions with the same title.
    Match the entire reference, then require one PDF explicitly labelled as
    the vacancy notice. Keep that heading's other application-document links.
    """
    if urlsplit(page_url).hostname != "www.sesarju.eu":
        raise ValueError("SESAR directory must remain on its official host")
    sections = []
    for match in re.finditer(r"<h3\b[^>]*>(.*?)</h3>(.*?)(?=<h[1-3]\b|\Z)", html_text, re.S | re.I):
        heading = _clean_html(match.group(1)) or ""
        reference = re.search(r"\(\s*REF\.\s*([^()]+)\)", heading, re.I)
        if reference and reference.group(1).strip().casefold() == external_id.casefold():
            sections.append((heading, match.group(0)))
    if len(sections) != 1:
        raise ValueError("SESAR directory needs one exact vacancy-reference section")
    heading, section = sections[0]
    candidates = {
        urljoin(page_url, anchor["href"])
        for anchor in _TokenParser.parse(section).anchors
        if anchor["text"].strip().casefold() == "vacancy notice"
        and urlsplit(anchor["href"]).path.casefold().endswith(".pdf")
    }
    if len(candidates) != 1:
        raise ValueError("SESAR reference section needs one labelled vacancy PDF")
    notice_url = candidates.pop()
    if urlsplit(notice_url).hostname != urlsplit(page_url).hostname:
        raise ValueError("SESAR vacancy PDF left the official directory host")
    return {"directory_url": page_url, "reference": external_id,
            "heading": heading, "section_html": section, "notice_url": notice_url}


def _eu_identity_matches(text: str, title: str, external_id: str) -> bool:
    def normalize(value):
        return re.sub(r"[^a-z0-9]", "", value.casefold())
    haystack = normalize(text)
    code = normalize(external_id)
    name = normalize(title)
    bilingual = re.fullmatch(r"(.+?)\s*\((.+)\)", title)
    separate_titles = [normalize(part) for part in bilingual.groups()] if bilingual else []
    return ((len(code) >= 7 and code in haystack) or (len(name) >= 12 and name in haystack)
            or (len(separate_titles) == 2 and all(len(part) >= 12 and part in haystack for part in separate_titles)))


def _eu_full_notice_signals(text: str) -> bool:
    body = text.casefold()
    duties = any(word in body for word in ("responsibilit", "duties", "tasks", "job description", "missions", "what will your contribution be", "quelle sera votre contribution", "plusieurs tâches juridiques", "tâches à accomplir"))
    eligibility = any(word in body for word in ("eligibility", "qualifications", "requirements", "selection criteria", "qualifications", "profil", "conditions de base", "conditions souhaitées", "les fonctions à exercer exigent", "les fonctions à exercer requièrent"))
    return len(text) >= 1200 and duties and eligibility


def _europol_public_vacancy(html_text: str, page_url: str, external_id: str, title: str) -> dict | None:
    """Read the exact public vacancy payload without evaluating JavaScript."""
    if urlsplit(page_url).hostname != "www.europol.europa.eu":
        return None
    assignments = list(re.finditer(r"\bwindow\.SERVER_DATA\s*=\s*", html_text))
    if len(assignments) != 1:
        raise ValueError("Europol needs one public server-data assignment")
    try:
        payload, _ = json.JSONDecoder().raw_decode(html_text[assignments[0].end():])
        node = payload["NodeLoader"]["node"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Europol public vacancy data is malformed") from exc
    if not isinstance(node, dict):
        raise ValueError("Europol public vacancy node missing")
    def normalize(value):
        return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())
    # The board abbreviates titles before department/grade suffixes. Exact
    # node ID, alias and full reference remain mandatory identity checks.
    public_title = re.sub(r"[^a-z0-9]+", " ", str(node.get("title") or "").casefold()).strip()
    summary_title = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
    title_matches = len(summary_title) >= 12 and (
        public_title == summary_title or public_title.startswith(summary_title + " ")
    )
    path = urlsplit(page_url).path.rstrip("/")
    if (node.get("type") != "vacancy" or not re.fullmatch(r"/work-with-us/careers/open-vacancies/vacancy/\d+", path)
            or str(node.get("id")) != path.rsplit("/", 1)[-1]
            or node.get("alias") != path
            or normalize(node.get("referenceNumber")) != normalize(external_id)
            or not title_matches):
        raise ValueError("Europol public vacancy identity mismatch")
    if not isinstance(node.get("body"), str) or not _eu_full_notice_signals(_clean_html(node["body"]) or ""):
        raise ValueError("Europol public vacancy lacks full duties and eligibility text")
    for field in ("published", "deadline"):
        if type(node.get(field)) is not int or not 946684800 <= node[field] <= 4102444800:
            raise ValueError("Europol public vacancy timestamp invalid")
    return node


def _eu_lisa_offer(html_text: str, page_url: str, external_id: str) -> dict | None:
    if urlsplit(page_url).hostname != "erecruitment.eulisa.europa.eu":
        return None
    match = re.search(r'<script\b[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html_text, re.S | re.I)
    if not match:
        raise ValueError("eu-LISA public offer data missing")
    offer = json.loads(match.group(1)).get("props", {}).get("pageProps", {}).get("offer")
    if not isinstance(offer, dict):
        raise ValueError("eu-LISA current offer missing")
    reference = re.sub(r"[^a-z0-9]", "", str(offer.get("reference") or "").casefold())
    expected = re.sub(r"[^a-z0-9]", "", external_id.casefold())
    if not reference or reference != expected or str(offer.get("uri") or "").rstrip("/") != page_url.rstrip("/"):
        raise ValueError("eu-LISA current offer reference or URL identity mismatch")
    return offer


class _ETFViewPDFForm(HTMLParser):
    """Only the public vacancy-document form, never an application form."""
    def __init__(self):
        super().__init__()
        self.active = False
        self.action = None
        self.fields = {}
        self.english = None
        self.option = None
        self.option_text = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.active = attrs.get("id") == "etf-ui-attachment-dropdown-select-form1"
            if self.active:
                self.action = attrs.get("action")
        if self.active and tag == "input" and attrs.get("type") == "hidden" and attrs.get("name") in {"form_build_id", "form_id"}:
            self.fields[attrs["name"]] = attrs.get("value", "")
        if self.active and tag == "option":
            self.option, self.option_text = attrs.get("value"), ""

    def handle_data(self, data):
        if self.active and self.option is not None:
            self.option_text += data

    def handle_endtag(self, tag):
        if tag == "option" and self.active:
            if self.option_text.strip().casefold() == "english":
                self.english = self.option
            self.option = None
        if tag == "form":
            self.active = False
