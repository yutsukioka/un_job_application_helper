"""OSCE native Chromium network transport with per-hop durable admission.

CDP pauses both requests and responses, including redirect hops. Only the
admitted request is continued; its response is persisted before page scripts
can consume it. A fresh, task-local anonymous profile is used. No cookie export,
user profile, stealth flags, CAPTCHA handling, or unguarded HTTP fallback.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.parse import urlsplit
from uuid import uuid4

from jobagg.adapters.osce_inventory import Cards, SESSION, captured_scope, reconcile
from jobagg.atomic_files import atomic_write_text
from jobagg.browser_fetch import BrowserContractError, COMPOSED_HTML, COMPOSED_TEXT, GuardedBrowser
from jobagg.http import HttpResponse, ResponseTooLargeError
from jobagg.html_text import render_html_text
from jobagg.osce_fragments import DATA_ROUTE, csrf_token, validate_csrf_header, page_url, request_url, result_html, session_parts, site_name, validate_request
from jobagg.adapters.osce_inventory import pagination_state
from jobagg.pipelines.http_checkpoint import safe_error, safe_url

TRANSPORT = "chromium_cdp_native_v1"
# OSCE requires no child frames, workers, forms, popups or plugins for reading.
CSP = "sandbox allow-scripts allow-same-origin; worker-src 'none'; child-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'"


class OSCENativeBrowser(GuardedBrowser):
    def request(self, url, **kwargs):
        if kwargs.get("method", "GET") != "GET" or not self.matches(url):
            raise BrowserContractError("Native OSCE entry outside reviewed GET contract")
        return self.render(url)

    def render(self, url):
        if not self.matches(url):
            raise BrowserContractError("Native OSCE URL outside reviewed contract")
        if not self.client.tls_verify or not self.client.safe_policy:
            raise BrowserContractError("Native browser requires verified TLS and host policy")
        self.client.safe_policy.validate_url(url)
        target = self.capture.target / ("browser-" + uuid4().hex)
        target.mkdir()
        self.failed = None
        self.omitted_resources = []
        self.data_only = self.contract.get("data_route") == DATA_ROUTE
        self.entry_origin = (urlsplit(url).scheme, urlsplit(url).netloc)
        self.expected_fragment = None
        self.responses = {}
        self.cookie_diagnostics = {}
        self.csrf_diagnostics = {}
        self._csrf_token = None
        original_deadline = self.capture.deadline_at
        self.capture.deadline_at = min(original_deadline or float("inf"), time.time() + self.timeout)
        try:
            html, text, links, final_url = asyncio.run(self._run(url, target))
            atomic_write_text(target / "rendered.html", html)
            atomic_write_text(target / "public_text.txt", text)
            receipt = {
                "url": safe_url(url), "final_url": safe_url(final_url),
                "engine": "chromium", "transport": TRANSPORT, "llm_calls": 0,
                "contract": self.contract, "html_path": str(target / "rendered.html"),
                "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
                "text_path": str(target / "public_text.txt"),
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "links": links, "complete": False, "omitted_resources": self.omitted_resources,
                "session_scope": "one task; fresh anonymous browser context; never persisted",
            }
            self.last_receipt = target / "receipt.json"
            atomic_write_text(self.last_receipt, json.dumps(receipt, indent=2) + "\n")
            return HttpResponse(url, 200, {"Content-Type": "text/html; charset=utf-8"}, html, html.encode())
        except Exception as exc:
            atomic_write_text(target / "error.json", json.dumps({
                "url": safe_url(url), "error": safe_error(exc), "transport": TRANSPORT,
                "complete": False, "omitted_resources": self.omitted_resources,
            }, indent=2) + "\n")
            raise
        finally:
            self.capture.deadline_at = original_deadline
            self._csrf_token = None

    def remaining_ms(self):
        self.capture._check_deadline()
        if self.failed:
            raise self.failed
        return max(1, (self.capture.deadline_at - time.time()) * 1000)

    async def _run(self, url, target):
        from playwright.async_api import async_playwright
        bundled = Path(__file__).resolve().parents[3] / "private/jobagg-runtime/browsers"
        if bundled.is_dir():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled))
        self.pending, self.chains, self.tasks = {}, {}, set()
        self.serial = asyncio.Lock()
        self.loop = asyncio.get_running_loop()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=not self.headed, timeout=self.remaining_ms(), args=[
                "--disable-background-networking", "--disable-component-update",
                "--disable-quic", "--block-new-web-contents",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ])
            try:
                context = await browser.new_context(service_workers="block", accept_downloads=False,
                                                    java_script_enabled=not self.data_only)
                page = await context.new_page()
                self.cdp = await context.new_cdp_session(page)
                await self.cdp.send("Network.enable")
                await self.cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
                await self.cdp.send("Network.setBlockedURLs", {"urls": ["ws://*", "wss://*"]})
                self.frame_id = (await self.cdp.send("Page.getFrameTree"))["frameTree"]["frame"]["id"]
                self.cdp.on("Network.requestWillBeSentExtraInfo", self._safe_cookie_info)
                self.cdp.on("Fetch.requestPaused", self._schedule)
                await self.cdp.send("Fetch.enable", {"patterns": [
                    {"urlPattern": "*", "requestStage": "Request"},
                    {"urlPattern": "*", "requestStage": "Response"},
                ]})
                async with asyncio.timeout(self.remaining_ms() / 1000):
                    await page.goto(url, wait_until="domcontentloaded", timeout=self.remaining_ms())
                    await page.wait_for_load_state("networkidle", timeout=self.remaining_ms())
                    listing = self.capture.phase.get("kind") == "listing"
                    selector = self.contract["ready_selector"] if listing else self.contract["detail_ready_selector"]
                    # The public HTML includes an anti-clickjacking style that
                    # hides the body until UI JavaScript removes it. Data-mode
                    # readiness depends on captured markup, not UI visibility.
                    await page.locator(selector).first.wait_for(
                        state="attached" if self.data_only else "visible", timeout=self.remaining_ms())
                    pages = (await self._collect_fragments(page, target) if self.data_only
                             else await self._collect(page, target)) if listing else None
                    text = (render_html_text("\n".join(p["html"] for p in pages)
                                             if pages is not None else self.responses[page.url][0].text)
                            if self.data_only else await page.locator("body").evaluate(COMPOSED_TEXT))
                    if not text or not text.strip():
                        raise BrowserContractError("Native public content is empty")
                    html = (self.responses[page.url][0].text if self.data_only
                            else await page.evaluate(COMPOSED_HTML))
                    atomic_write_text(target / "light_dom.html", await page.content())
                    if pages is not None:
                        html = '<script type="application/json" id="jobagg-osce-inventory">' + json.dumps(pages).replace('<', '\\u003c') + '</script>'
                    links = await page.locator("a[href]").evaluate_all("nodes => nodes.map(a => ({url:a.href,label:a.innerText}))")
                    links = [{**x, "url": safe_url(x["url"])} for x in links if x["url"].startswith("https://")]
                    await page.screenshot(path=str(target / "page.png"), full_page=True, timeout=self.remaining_ms())
                    self.remaining_ms()
                    return html, text, links, page.url
            except Exception:
                if self.failed:
                    raise self.failed
                raise
            finally:
                # Wake dispatch threads before waiting for them. Never leave a
                # thread holding a host lock after the browser task exits.
                self.failed = self.failed or BrowserContractError("Native browser task finished")
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(self.failed)
                await browser.close()
                if self.tasks:
                    await asyncio.gather(*list(self.tasks), return_exceptions=True)

    def _schedule(self, event):
        task = asyncio.create_task(self._paused(event))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _abort(self, request_id):
        try:
            await self.cdp.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
        except Exception:
            pass  # The browser may already have cancelled this paused request.

    async def _paused(self, event):
        rid = event["requestId"]
        # Response events must run while the request task owns serial admission.
        if "responseStatusCode" in event or "responseErrorReason" in event:
            future = self.pending.get(rid)
            if future is not None and not future.done():
                future.set_result(event)
            else:
                await self._abort(rid)
            return
        async with self.serial:
            if self.failed:
                await self._abort(rid)
                return
            try:
                request = event["request"]
                url, method = request["url"], request["method"]
                resource = event.get("resourceType")
                if self.data_only:
                    if resource == "Document":
                        if (urlsplit(url).scheme, urlsplit(url).netloc) != self.entry_origin:
                            raise BrowserContractError("OSCE data document changed origin")
                        if self.capture.phase.get("kind") == "listing":
                            path = urlsplit(url).path
                            if path != "/jobs/search/" and not re.fullmatch(r"/jobs/search/\d+/?", path):
                                raise BrowserContractError("Unexpected OSCE search navigation")
                    elif resource in {"XHR", "Fetch"} and self.expected_fragment is not None:
                        validate_request(url, method, request.get("postData"), **self.expected_fragment)
                        validate_csrf_header(request.get("headers", {}), self._csrf_token)
                    else:
                        self.omitted_resources.append({"url": safe_url(url), "type": resource,
                                                       "reason": "data_route_omits_ui_resources"})
                        await self._abort(rid)
                        return
                omitted = resource in {"Image", "Font", "Media", "WebSocket", "Ping"} or (
                    resource == "Stylesheet" and self.contract.get("load_stylesheets") is False)
                if omitted:
                    self.omitted_resources.append({"url": safe_url(url), "type": resource, "reason": "reviewed_resource_omission"})
                    await self._abort(rid)
                    return
                if event.get("frameId") != self.frame_id:
                    raise BrowserContractError("OSCE child-frame/worker traffic is outside contract")
                if urlsplit(url).scheme != "https":
                    raise BrowserContractError("Native request requires HTTPS")
                if method not in {"GET", "HEAD"} and not (
                    method == "POST" and (url in self.contract.get("read_only_post_urls", [])
                    or (self.data_only and self.expected_fragment is not None and resource in {"XHR", "Fetch"}))):
                    raise BrowserContractError("Native request method outside read-only contract")
                self.client.safe_policy.validate_url(url)
                previous = event.get("redirectedRequestId")
                depth = 0
                if previous:
                    if previous not in self.chains:
                        raise BrowserContractError("Unbound native redirect")
                    origin, depth = self.chains[previous]
                    depth += 1
                    self.client.safe_policy.validate_redirect(origin, url, redirect_count=depth)
                self.chains[rid] = (url, depth)
                future = self.loop.create_future()
                self.pending[rid] = future
                response_event = {}

                async def dispatch(timeout):
                    await self.cdp.send("Fetch.continueRequest", {"requestId": rid})
                    reply = await asyncio.wait_for(asyncio.shield(future), timeout)
                    response_event.update(reply)
                    if reply.get("responseErrorReason"):
                        reason = reply["responseErrorReason"]
                        if "CERT" in reason.upper() or "SSL" in reason.upper():
                            raise ssl.SSLError("Native TLS validation failed: " + reason)
                        raise ConnectionError("Native network failure: " + reason)
                    status = reply["responseStatusCode"]
                    headers = {h["name"].lower(): h["value"] for h in reply.get("responseHeaders", [])}
                    body = b""
                    if status not in {204, 304} and not 300 <= status < 400 and method != "HEAD":
                        if int(headers.get("content-length", "0")) > self.client.max_response_bytes:
                            raise ResponseTooLargeError("Native response exceeds byte cap")
                        stream = (await self.cdp.send("Fetch.takeResponseBodyAsStream", {"requestId": rid}))["stream"]
                        try:
                            while True:
                                chunk = await self.cdp.send("IO.read", {"handle": stream, "size": 65536})
                                body += base64.b64decode(chunk["data"]) if chunk.get("base64Encoded") else chunk["data"].encode()
                                if len(body) > self.client.max_response_bytes:
                                    raise ResponseTooLargeError("Native response exceeds byte cap")
                                if chunk.get("eof"):
                                    break
                        finally:
                            await self.cdp.send("IO.close", {"handle": stream})
                    self.client.last_request_diagnostics = {"transport": TRANSPORT, "headers_received": True,
                                                            "status_code": status, "wire_bytes_read": len(body),
                                                            "request_body_bytes": len(request.get("postData", "").encode()),
                                                            "resource_type": resource,
                                                            "csrf_observation": self.csrf_diagnostics.pop(event.get("networkId"), {"available": False}),
                                                            # Redirects can reuse a Network id without another
                                                            # extra-info event. Never reuse the previous hop's data.
                                                            "cookie_observation": self.cookie_diagnostics.pop(event.get("networkId"), {"available": False})}
                    return HttpResponse(url, status, headers, body.decode("utf-8", errors="replace"), body)

                def native_dispatch(request_url, **kwargs):
                    assert request_url == url
                    self.client.last_request_diagnostics = {"transport": TRANSPORT, "headers_received": False}
                    timeout = kwargs.get("timeout_seconds", self.client.timeout_seconds)
                    job = asyncio.run_coroutine_threadsafe(asyncio.wait_for(dispatch(timeout), timeout), self.loop)
                    try:
                        return job.result(timeout=timeout + 2)
                    finally:
                        if not job.done():
                            job.cancel()

                response = await asyncio.to_thread(self.capture.request, url, method=method,
                    body=request.get("postData", "").encode() or None, _native_dispatch=native_dispatch)
                self.responses[url] = (response, str(self.capture.target / "http" / f"{self.capture.count:05d}.json"))
                # Preserve duplicate Set-Cookie headers in memory only; never
                # export the anonymous browser session to artifacts.
                headers = [h for h in response_event.get("responseHeaders", [])
                           if h["name"].lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
                if resource == "Document":
                    headers.append({"name": "Content-Security-Policy", "value": CSP})
                await self.cdp.send("Fetch.fulfillRequest", {"requestId": rid,
                    "responseCode": response.status_code, "responseHeaders": headers,
                    "body": base64.b64encode(response.content).decode()})
            except Exception as exc:
                self.failed = self.failed or exc
                await self._abort(rid)
            finally:
                self.pending.pop(rid, None)

    def _safe_cookie_info(self, event):
        # Use actual extra-info events, never persist Cookie/Set-Cookie values.
        from http.cookies import SimpleCookie
        cookie = next((v for k, v in event.get("headers", {}).items() if k.lower() == "cookie"), "")
        parsed = SimpleCookie()
        parsed.load(cookie)
        self.cookie_diagnostics[event["requestId"]] = {
            "available": True, "sent_cookie_names": sorted(parsed),
            "associated_cookies": [{"name": item["cookie"]["name"],
                "blocked_reasons": item.get("blockedReasons", []),
                "secure": item["cookie"].get("secure"), "http_only": item["cookie"].get("httpOnly"),
                "same_site": item["cookie"].get("sameSite")}
                for item in event.get("associatedCookies", [])],
        }
        supplied = next((v for k, v in event.get("headers", {}).items() if k.lower() == "tss-token"), None)
        self.csrf_diagnostics[event["requestId"]] = {
            "available": True, "present": supplied is not None,
            "matches_document": bool(self._csrf_token and supplied == self._csrf_token),
        }

    async def _collect_fragments(self, page, target):
        await page.wait_for_url(SESSION, timeout=self.remaining_ms())
        session, index = session_parts(page.url)
        if index not in (None, 1):
            raise ValueError("OSCE initial page index differs")
        response, evidence = self.responses[page.url]
        html = response.text
        site = site_name(html)
        self._csrf_token = csrf_token(html)
        _, count = pagination_state(html)
        if not 0 <= count <= 50:
            raise ValueError("OSCE advertised page count exceeds bound")
        origin = urlsplit(page.url).scheme + "://" + urlsplit(page.url).netloc
        pages = []
        actual_url = page.url
        for number in range(1, max(1, count) + 1):
            scope = captured_scope(html)
            if scope != {"new_jobs": False, "unfiltered": True}:
                raise ValueError("OSCE data route requires unfiltered scope")
            current, total_pages = pagination_state(html)
            if current != number or total_pages != count:
                raise ValueError("OSCE pagination counters changed")
            value = {"number": number, "url": page_url(session, number, origin),
                     "request_url": actual_url, "capture_path": evidence, "scope": scope, "html": html}
            pages.append(value)
            atomic_write_text(target / "inventory-pages" / f"{number}.json", json.dumps(value, sort_keys=True))
            if number == max(1, count):
                break
            next_page = number + 1
            self.expected_fragment = {"session": session, "site": site, "page": next_page, "origin": origin}
            actual_url = request_url(session, site, next_page, int(time.time() * 1000) % 1000, origin)
            try:
                text = await page.evaluate("""async args => {
                    const response = await fetch(args.url, {method:'POST', credentials:'same-origin',
                        referrer:args.referrer, headers:{'X-Requested-With':'XMLHttpRequest',
                        'Accept':'application/json, text/javascript, */*; q=0.01', 'tss-token':args.token}});
                    if (response.status !== 200) throw Error('OSCE fragment status ' + response.status);
                    return await response.text();
                }""", {"url": actual_url, "referrer": page_url(session, number, origin), "token": self._csrf_token})
                self.remaining_ms()
                response, evidence = self.responses[actual_url]
                if text != response.text:
                    raise ValueError("OSCE browser data differs from captured response")
                html = result_html(text, next_page)
            finally:
                self.expected_fragment = None
        reconcile(pages)
        return pages

    async def _collect(self, page, target):
        await page.wait_for_url(SESSION, timeout=self.remaining_ms())
        pages, seen = [], set()
        for number in range(1, 51):
            body = await page.content()
            scope = captured_scope(body)
            if await page.locator('input[aria-label="New Jobs"]').is_checked():
                raise ValueError("OSCE New Jobs selected")
            if int(await page.locator("#jPaginateCurrPage").inner_text()) != number:
                raise ValueError("OSCE displayed page index differs")
            ids = {row[0] for row in Cards(body).rows}
            if seen.intersection(ids):
                raise ValueError("OSCE repeated page")
            seen.update(ids)
            value = {"number": number, "url": page.url, "scope": scope, "html": body}
            pages.append(value)
            atomic_write_text(target / "inventory-pages" / f"{number}.json", json.dumps(value, sort_keys=True))
            total_text = await page.locator(".number_of_results").inner_text()
            total = re.fullmatch(r"\s*(\d+)\s+results\s*", total_text, re.I)
            if not total:
                raise ValueError("OSCE advertised total missing")
            if len(seen) >= int(total[1]):
                reconcile(pages)
                return pages
            controls = page.locator("#jPaginationHldr a, #jPaginationHldr button").filter(
                has_text=re.compile(r"^\s*" + str(number + 1) + r"\s*$"))
            visible = [control for control in await controls.all() if await control.is_visible()]
            if len(visible) != 1:
                raise ValueError("OSCE next numbered page missing/ambiguous")
            await visible[0].click(timeout=self.remaining_ms())
            await page.wait_for_function("""expected => {
                const ids = [...document.querySelectorAll('a.job_link')].map(e=>e.href.match(/-(\\d+)$/)?.[1]).filter(Boolean).sort();
                return Number(document.querySelector('#jPaginateCurrPage')?.textContent) === expected.number
                    && ids.length > 0 && JSON.stringify(ids) !== JSON.stringify(expected.ids);
            }""", arg={"ids": sorted(ids), "number": number + 1}, timeout=self.remaining_ms())
        raise ValueError("OSCE maximum page bound reached")
