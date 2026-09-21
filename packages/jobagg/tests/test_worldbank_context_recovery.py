"""Exercise the shipped source contract, not an API-only approximation."""

from dataclasses import replace
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.csod import CSODAdapter
from jobagg.http import HttpResponse
from jobagg.pipelines.sync_source import load_sources
from test_csod_inventory_recovery import page


def configured_source():
    source = next(s for s in load_sources(
        Path(__file__).parents[1] / "config/organizations.yaml"
    ) if s.id == "worldbank_csod")
    return replace(source, extra={**source.extra, "fetch_details": False, "page_size": 2})


class PublicHTTP:
    def __init__(self, source, *, token="public-anonymous-fixture-token", error=None):
        self.source, self.token, self.error = source, token, error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        assert url == self.source.extra["context_url"]
        context = {"token": self.token, "endpoints": {"cloud": "https://us.api.csod.com"}}
        return HttpResponse(url, 200, {}, '<script>window.csod.context = '
                            + json.dumps(context) + ';</script>')

    def post_json(self, url, payload, *, headers):
        self.calls.append(("POST", payload["pageNumber"]))
        assert headers["Authorization"] == "Bearer " + self.token
        assert url == self.source.extra["api_url"]
        if self.error:
            raise self.error
        value = page(5, *{1: [1, 2], 2: [3, 4], 3: [5]}[payload["pageNumber"]])
        return HttpResponse(url, 200, {}, json.dumps(value))


def test_shipped_contract_discovers_context_and_authenticates_every_page():
    source = configured_source()
    assert source.extra["requires_bearer_token"] is True
    assert source.extra["requires_runtime_context"] is True
    assert source.extra["requires_user_credentials"] is False
    http = PublicHTTP(source)
    adapter = CSODAdapter(AdapterContext(source, http))
    assert [job.external_id for job in adapter.fetch_jobs()] == ["1", "2", "3", "4", "5"]
    assert http.calls == [("GET", source.extra["context_url"]), ("POST", 1), ("POST", 2), ("POST", 3)]
    assert adapter.run_diagnostics.pagination_complete


@pytest.mark.parametrize("flags", [
    {"requires_bearer_token": True, "requires_runtime_context": False},
    {"requires_bearer_token": False, "requires_runtime_context": True},
])
def test_missing_required_context_stops_before_api_request(flags):
    source = configured_source()
    source.extra.update(flags)
    http = PublicHTTP(source, token="")
    with pytest.raises(RuntimeError, match="anonymous token"):
        CSODAdapter(AdapterContext(source, http)).fetch_jobs()
    assert http.calls == [("GET", source.extra["context_url"])]


@pytest.mark.parametrize("status", [401, 403])
def test_denial_cannot_trigger_token_refresh_or_endpoint_fallback(status):
    source = configured_source()
    error = HTTPError(source.extra["api_url"], status, "Denied", {}, None)
    http = PublicHTTP(source, error=error)
    with pytest.raises(HTTPError) as caught:
        CSODAdapter(AdapterContext(source, http)).fetch_jobs()
    assert caught.value is error
    assert http.calls == [("GET", source.extra["context_url"]), ("POST", 1)]


def test_osce_shipped_contract_omits_stylesheets_only_for_listing():
    source = next(s for s in load_sources(
        Path(__file__).parents[1] / "config/organizations.yaml"
    ) if s.id == "osce_custom_html")
    assert source.extra["browser_render"]["load_stylesheets"] is False
    assert source.extra["fetch_details"] is True
    assert source.extra["fetch_attachments"] is False
