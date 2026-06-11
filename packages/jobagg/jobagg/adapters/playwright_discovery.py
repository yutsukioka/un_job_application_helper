"""Optional Playwright-assisted endpoint discovery for JavaScript-heavy portals."""

from __future__ import annotations

from dataclasses import dataclass, field

from jobagg.adapters.base import JobAdapter, register_adapter
from jobagg.models import JobRecord


@dataclass(slots=True)
class DiscoveryResult:
    page_url: str
    candidate_api_urls: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@register_adapter
class PlaywrightDiscoveryAdapter(JobAdapter):
    family = "playwright_discovery"

    def fetch_jobs(self) -> list[JobRecord]:
        raise RuntimeError(
            "playwright_discovery is an endpoint discovery helper, not a production sync adapter"
        )

    def discover(self, url: str | None = None) -> DiscoveryResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Install the discovery extra with `pip install -e .[discovery]` to use Playwright"
            ) from exc

        target_url = url or self.source.base_url
        candidates: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()

            def capture(response) -> None:
                response_url = response.url
                lower = response_url.lower()
                if any(token in lower for token in ("job", "career", "requisition", "posting")):
                    candidates.append(response_url)

            page.on("response", capture)
            page.goto(target_url, wait_until="networkidle")
            browser.close()
        return DiscoveryResult(page_url=target_url, candidate_api_urls=sorted(set(candidates)))

