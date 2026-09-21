"""A bounded Chromium renderer for deterministic job adapters, with no LLM.

Every page request is fulfilled by the existing DurableCapture transport. The
browser never receives an unrestricted network route or a user's normal profile.
Enable per source only after reviewing URL patterns and a ready selector.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

from jobagg.atomic_files import atomic_write_text
from jobagg.http import HttpResponse
from jobagg.pipelines.http_checkpoint import HostIneligible, safe_error, safe_url


class BrowserContractError(RuntimeError):
    pass


COMPOSED_HTML = """() => {
  function copy(node) {
    if (node.nodeType === Node.ELEMENT_NODE && node.localName === 'slot') {
      const fragment = document.createDocumentFragment();
      const assigned = node.assignedNodes({flatten:true});
      for (const child of (assigned.length ? assigned : node.childNodes)) fragment.append(copy(child));
      return fragment;
    }
    const clone = node.cloneNode(false);
    const children = node.shadowRoot ? node.shadowRoot.childNodes : node.childNodes;
    if (node.shadowRoot) clone.setAttribute('data-jobagg-open-shadow-root', 'serialized');
    for (const child of children) clone.appendChild(copy(child));
    return clone;
  }
  return '<!DOCTYPE html>\\n' + copy(document.documentElement).outerHTML;
}"""

COMPOSED_TEXT = """root => {
  function text(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const range = document.createRange(); range.selectNodeContents(node);
      if (!range.getClientRects().length) return '';
      const parent = node.parentElement || node.parentNode.host;
      return /^pre/.test(getComputedStyle(parent).whiteSpace)
        ? node.data : node.data.replace(/\\s+/g, ' ');
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return '';
    if (['SCRIPT','STYLE','NOSCRIPT','TEMPLATE'].includes(node.tagName)) return '';
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden') return '';
    if (node.tagName === 'BR') return '\\n';
    let children = node.shadowRoot ? node.shadowRoot.childNodes : node.childNodes;
    if (node.localName === 'slot') {
      const assigned = node.assignedNodes({flatten:true});
      if (assigned.length) children = assigned;
    }
    const result = Array.from(children).map(text).join('');
    return /^(block|flex|grid|table|list-item)/.test(style.display) ? '\\n'+result+'\\n' : result;
  }
  return text(root).replace(/[ \\t]*\\n[ \\t]*/g,'\\n').replace(/\\n{3,}/g,'\\n\\n').trim();
}"""


class GuardedBrowser:
    def __init__(self, client, capture, contract, *, headed=False):
        self.client, self.capture = client, capture
        self.contract = dict(contract)
        patterns = self.contract.get("url_patterns")
        if (
            not isinstance(patterns, list)
            or not patterns
            or not all(
                isinstance(p, str) and p.startswith("^https://") and p.endswith("$")
                for p in patterns
            )
        ):
            raise BrowserContractError("Browser URLs require explicit anchored HTTPS patterns")
        self.patterns = [re.compile(p) for p in patterns]
        if not self.contract.get("ready_selector"):
            raise BrowserContractError("A reviewed browser ready_selector is required")
        self.timeout = float(self.contract.get("timeout_seconds", 120))
        if not 1 <= self.timeout <= 300:
            raise BrowserContractError("Browser timeout must be within 1–300 seconds")
        self.headed = headed
        self.failed = None
        self.last_receipt = None
        self.omitted_resources = []
        self.asset_cache = {}
        self.redirect_documents = {}

    def matches(self, url):
        return any(p.fullmatch(url) for p in self.patterns)

    def request(self, url, **kwargs):
        if kwargs.get("method", "GET") == "GET" and self.matches(url):
            return self.render(url)
        return self.capture.request(url, **kwargs)

    def route(self, route):
        """No continue_()/route.fetch(): all traffic passes the shared HTTP guard."""
        request = route.request
        if self.failed is not None:
            route.abort()
            return
        if request.resource_type in {"image", "font", "media"}:
            route.abort()
            return
        if request.resource_type == "stylesheet" and self.contract.get("load_stylesheets") is False:
            self.omitted_resources.append(
                {
                    "url": safe_url(request.url),
                    "type": "stylesheet",
                    "reason": "contract_omits_stylesheets",
                }
            )
            route.abort()
            return
        try:
            if urlsplit(request.url).scheme != "https":
                raise BrowserContractError("Browser resource requires HTTPS")
            if request.method not in {"GET", "HEAD"}:
                # Explicit exact read-only API endpoints; never submit arbitrary
                # application forms, analytics, or unknown page-generated POSTs.
                if request.method != "POST" or request.url not in self.contract.get(
                    "read_only_post_urls", []
                ):
                    self.omitted_resources.append(
                        {
                            "url": safe_url(request.url),
                            "type": request.resource_type,
                            "method": request.method,
                            "reason": "unreviewed_request_method",
                        }
                    )
                    route.abort()
                    return
            if self.client.safe_policy is None or not self.client.safe_policy.allowed_hosts:
                raise BrowserContractError("Browser requires a nonempty source host allowlist")
            if (
                urlsplit(request.url).hostname or ""
            ).lower() not in self.client.safe_policy.allowed_hosts and request.resource_type in {
                "script",
                "stylesheet",
            }:
                # Analytics/fonts are not an excuse to expand source authority.
                # If a necessary app script is omitted, the ready contract must
                # fail; preserve omissions even when an SSR page is usable.
                self.omitted_resources.append(
                    {
                        "url": safe_url(request.url),
                        "type": request.resource_type,
                        "reason": "unreviewed_asset_host",
                    }
                )
                route.abort()
                return
            self.client.safe_policy.validate_url(request.url)
            public_headers = {"accept", "accept-language", "content-type", "user-agent"}
            headers = {
                k: v for k, v in request.all_headers().items() if k.lower() in public_headers
            }
            cacheable = request.method == "GET" and request.resource_type in {
                "script",
                "stylesheet",
            }
            response = self.redirect_documents.pop(request.url, None)
            if response is None and cacheable:
                response = self.asset_cache.get(request.url)
            if response is None:
                response = self.capture.request(
                    request.url,
                    method=request.method,
                    headers=headers,
                    body=request.post_data_buffer,
                )
                if cacheable and response.status_code == 200:
                    self.asset_cache[request.url] = response
            if response.url != request.url:
                # A fulfilled final response at the old URL would resolve relative
                # links incorrectly. Require the contract to use the canonical URL.
                raise BrowserContractError("Browser redirect requires a canonical URL contract")
            headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
            }
            route.fulfill(
                status=response.status_code,
                headers=headers,
                body=response.content or response.text.encode(),
            )
        except Exception as exc:
            if self.failed is None:
                self.failed = exc
            route.abort()

    def render(self, url):
        if not self.matches(url):
            raise BrowserContractError("URL outside reviewed browser contract")
        if self.client.safe_policy is None:
            raise BrowserContractError("Missing browser network policy")
        self.client.safe_policy.validate_url(url)
        bundled = Path(__file__).resolve().parents[3] / "private/jobagg-runtime/browsers"
        if bundled.is_dir():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled))
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserContractError("Install jobagg[browser] and Playwright Chromium") from exc
        self.failed = None
        self.omitted_resources = []
        remaining = self.timeout
        if self.capture.deadline_at:
            remaining = min(remaining, self.capture.deadline_at - time.time())
        if remaining <= 0:
            raise HostIneligible("Browser task deadline exhausted", category="budget")
        original_deadline = self.capture.deadline_at
        self.capture.deadline_at = time.time() + remaining

        def remaining_ms():
            self.capture._check_deadline()
            return max(1, (self.capture.deadline_at - time.time()) * 1000)

        target = self.capture.target / ("browser-" + uuid4().hex)
        target.mkdir()
        try:
            navigation_url = url
            if self.contract.get("inventory") == "osce_full_search_v1":
                from jobagg.adapters.osce_inventory import SEARCH_URL, SESSION
                if url != SEARCH_URL:
                    raise BrowserContractError("OSCE inventory must begin at official search route")
                # Resolve through the guarded Python client before browser
                # navigation. Never synthesize a redirect that Chromium might
                # follow without invoking its route handler again.
                response = self.capture.request(url, method="GET")
                if not SESSION.fullmatch(response.url):
                    raise BrowserContractError("OSCE search did not return a generated session")
                navigation_url = response.url
                self.redirect_documents[navigation_url] = response
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=not self.headed,
                    timeout=remaining_ms(),
                    args=[
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    ],
                )
                try:
                    context = browser.new_context(service_workers="block", accept_downloads=False)
                    context.route("**/*", self.route)
                    context.route_web_socket("**/*", lambda ws: ws.close())
                    page = context.new_page()
                    page.set_default_timeout(remaining * 1000)
                    page.on("dialog", lambda dialog: dialog.dismiss())
                    try:
                        page.goto(navigation_url, wait_until="domcontentloaded", timeout=remaining_ms())
                        page.locator(self.contract["ready_selector"]).first.wait_for(
                            state="visible", timeout=remaining_ms()
                        )
                        for selector in self.contract.get("expand_selectors", []):
                            controls = page.locator(selector)
                            if controls.count() > 30:
                                raise BrowserContractError(
                                    "Expand control count exceeds reviewed bound"
                                )
                            for control in controls.all():
                                if (
                                    control.is_visible()
                                    and control.get_attribute("aria-expanded") == "false"
                                ):
                                    control.click(timeout=remaining_ms())
                        selected = page.locator(self.contract.get("content_selector", "body"))
                        if selected.count() != 1:
                            raise BrowserContractError(
                                "Content selector must identify one complete job region"
                            )
                        remaining_ms()
                        public_text = selected.evaluate(COMPOSED_TEXT)
                        if not public_text.strip():
                            raise BrowserContractError("Rendered public content is empty")
                        inventory_pages = None
                        if self.contract.get("inventory") == "osce_full_search_v1":
                            from jobagg.adapters.osce_inventory import collect_pages
                            inventory_pages = collect_pages(
                                page, remaining_ms,
                                save_page=lambda value: atomic_write_text(
                                    target / "inventory-pages" / (str(value["number"]) + ".json"),
                                    json.dumps(value, sort_keys=True),
                                ),
                            )
                        light_html = page.content()
                        html = page.evaluate(COMPOSED_HTML)
                        links = selected.locator("a[href]").evaluate_all(
                            "nodes => nodes.map(a => ({url:a.href, label:a.innerText}))"
                        )
                        if self.failed:
                            raise self.failed
                        page.screenshot(
                            path=str(target / "page.png"), full_page=True, timeout=remaining_ms()
                        )
                    except Exception:
                        if self.failed:
                            raise self.failed
                        raise
                finally:
                    browser.close()
            if inventory_pages is not None:
                html = '<script type="application/json" id="jobagg-osce-inventory">' + json.dumps(inventory_pages).replace('<', '\\u003c') + '</script>'
            atomic_write_text(target / "rendered.html", html)
            atomic_write_text(target / "light_dom.html", light_html)
            atomic_write_text(target / "public_text.txt", public_text)
            receipt = {
                "url": safe_url(url),
                "engine": "chromium",
                "llm_calls": 0,
                "contract": self.contract,
                "html_path": str(target / "rendered.html"),
                "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
                "dom_format": "composed HTML including open shadow roots and assigned slots",
                "text_path": str(target / "public_text.txt"),
                "text_sha256": hashlib.sha256(public_text.encode()).hexdigest(),
                "links": [
                    {**link, "url": safe_url(link["url"])}
                    for link in links
                    if link["url"].startswith(("http://", "https://"))
                ],
                "complete": False,
                "omitted_resources": self.omitted_resources,
                "asset_cache_scope": "this one bounded worker task; listings and data responses are never reused",
                "limits": [
                    "Ready selector is a site-specific contract, not a census proof",
                    "Images, fonts and media omitted; image text requires separate extraction",
                    "No human session or CAPTCHA automation",
                    "Closed shadow roots and cross-origin iframe content require separate contracts",
                    "No unreviewed form submissions",
                ],
            }
            self.last_receipt = target / "receipt.json"
            atomic_write_text(self.last_receipt, json.dumps(receipt, indent=2) + "\n")
            return HttpResponse(
                url, 200, {"Content-Type": "text/html; charset=utf-8"}, html, html.encode()
            )
        except Exception as exc:
            atomic_write_text(
                target / "error.json",
                json.dumps(
                    {
                        "error": safe_error(exc),
                        "url": safe_url(url),
                        "omitted_resources": self.omitted_resources,
                        "complete": False,
                    },
                    indent=2,
                )
                + "\n",
            )
            raise
        finally:
            self.capture.deadline_at = original_deadline


def install_browser_transport(client, capture, source):
    contract = source.extra.get("browser_render")
    if contract:
        renderer_type = GuardedBrowser
        if contract.get("transport") == "chromium_cdp_native_v1":
            if source.id != "osce_custom_html" or contract.get("inventory") != "osce_full_search_v1":
                raise BrowserContractError("Native transport is reviewed for OSCE only")
            from jobagg.osce_native_browser import OSCENativeBrowser
            renderer_type = OSCENativeBrowser
        elif contract.get("transport"):
            raise BrowserContractError("Unknown browser transport")
        renderer = renderer_type(
            client, capture, contract, headed=os.environ.get("JOBAGG_BROWSER_HEADED") == "1"
        )
        client._request = renderer.request
        capture.browser_renderer = renderer
        return renderer
    return None


def bounded_inspection(argv):
    """Keep a hung browser/JS engine outside the CLI supervisor's process."""
    process = subprocess.Popen(
        [sys.executable, "-m", "jobagg.browser_fetch", *argv, "--_child"], start_new_session=True
    )
    try:
        return process.wait(timeout=210)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        print(
            json.dumps(
                {
                    "status": "browser_watchdog_timeout",
                    "complete": False,
                    "reason": "Interrupted reservation remains counted; inspect saved evidence before retry",
                }
            )
        )
        return 75


def main(argv=None):
    """Inspect one enabled source's listing in a real bounded browser window.

    Inspection shares source holds, request pacing, and durable attempts. It does
    not invent a listing parser or bypass the normal worker/publication pipeline.
    """
    from jobagg.remediation_worker import Worker, shared_owner

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dispatcher-config", type=Path, required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--ready-selector", default="body")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.execute and not a._child:
        return bounded_inspection(list(sys.argv[1:] if argv is None else argv))
    config = json.loads(a.dispatcher_config.read_text())
    args = config["worker_argv"]

    def value(flag):
        return args[args.index(flag) + 1]

    worker = Worker(
        registry=value("--registry"),
        robots=value("--robots"),
        workspace=value("--workspace"),
        shared_lock=value("--shared-lock"),
        max_tasks=1,
        max_seconds=180,
        max_requests_per_task=25,
    )
    # Validate the same external volume before opening any file on that volume.
    guard = config.get("storage_guard")
    if guard:
        if (
            not os.path.ismount(guard["mount_root"])
            or hashlib.sha256(Path(guard["sentinel_path"]).read_bytes()).hexdigest()
            != guard["sentinel_sha256"]
        ):
            raise BrowserContractError("Configured external volume is unavailable")
    source = worker.by_id.get(a.source)
    if source is None:
        raise BrowserContractError("Source missing or disabled")
    url = str(
        source.extra.get("listing_url") or source.extra.get("all_jobs_url") or source.base_url
    )
    contract = {
        "url_patterns": ["^" + re.escape(url) + "$"],
        "ready_selector": a.ready_selector,
        "timeout_seconds": 150,
    }
    if not a.execute:
        print(
            json.dumps(
                {
                    "source": source.id,
                    "url": url,
                    "contract": contract,
                    "network_requests": 0,
                    "database_writes": 0,
                },
                indent=2,
            )
        )
        return 0
    with shared_owner(worker.shared_lock):
        worker.preview()
        if worker.shared_policy.source_hold(source.id):
            raise BrowserContractError("Source policy hold requires review")
        with worker.db.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM source_circuit_breakers WHERE source_id=? AND state IN('open','half_open')",
                (source.id,),
            ).fetchone():
                raise BrowserContractError("Source circuit requires review")
        attempt = "browser-inspect-" + uuid4().hex
        target = worker.workspace / "browser_checks" / attempt
        target.mkdir(parents=True)
        worker.shared_policy.reserve(
            attempt, source.id, "listing", time.time(), worker.workspace, attempt
        )
        outcome = "failed"
        try:
            _, client, capture = worker.context(
                replace(source, extra={**source.extra, "browser_render": None}),
                target,
                {"kind": "listing", "external_id": ""},
                time.time() + 180,
            )
            renderer = GuardedBrowser(client, capture, contract, headed=a.headed)
            renderer.render(url)
            outcome = "captured_inspection"
            print(
                json.dumps(
                    {
                        "status": outcome,
                        "receipt": str(renderer.last_receipt),
                        "live_database_updated": False,
                        "llm_calls": 0,
                    },
                    indent=2,
                )
            )
        except Exception as exc:
            atomic_write_text(target / "error.json", json.dumps({"error": safe_error(exc)}) + "\n")
            raise
        finally:
            worker.shared_policy.finish(attempt, outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
