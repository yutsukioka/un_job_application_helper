"""An obsolete feed's empty placeholder must not be a successful public census."""
import importlib
from pathlib import Path

import pytest

from jobagg.db import JobDatabase
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource
from jobagg.robots import RobotsPolicy

sync_module = importlib.import_module("jobagg.pipelines.sync_source")
RSS = Path(__file__).parent / "fixtures/idb/rss_placeholder_20260913.xml"
RSS_URL = "https://jobs.iadb.org/services/rss/category/?catid=9638000"


@pytest.mark.parametrize("sync_name", ["sync_source", "sync_source_with_selective_details"])
@pytest.mark.parametrize("public_widget", [True, False])
def test_empty_rss_scope_failure_is_reported_and_persisted(tmp_path, monkeypatch, sync_name, public_widget):
    requests = []

    class CapturedHTTP:
        def get(self, url):
            assert url == RSS_URL
            requests.append(url)
            body = RSS.read_bytes()
            return HttpResponse(url, 200, {"Content-Type": "application/rss+xml"}, body.decode(), body)

    monkeypatch.setattr(sync_module, "_http_client_for_source", lambda *_: CapturedHTTP())
    extra = {"rss_url": RSS_URL}
    if public_widget:
        extra["public_widget_root_url"] = "https://jobs.iadb.org/"
    source = OrganizationSource("idb_successfactors", "IDB", "successfactors_rmk", "https://jobs.iadb.org", extra=extra)
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    result = getattr(sync_module, sync_name)(source, db=db, policy=RobotsPolicy(honor_robots_txt=False), close_missing=False)

    assert requests == [RSS_URL]
    assert result.fetched == 0
    assert result.closed == result.missing == 0
    diagnostics = result.diagnostics
    stored = next(iter(db.iter_source_runs(source.id)))
    if public_widget:
        assert result.errors == ["idb_successfactors: pagination incomplete"]
        assert diagnostics.pagination_complete is False
        assert diagnostics.empty_reason == "rss_scope_unverified_public_widget"
        assert diagnostics.run_classification == "inconclusive"
        assert diagnostics.publishability_classification == "source_inconclusive"
        assert stored["errors"] == result.errors
    else:
        assert result.errors == []
        assert diagnostics.pagination_complete is True
        assert diagnostics.run_classification == "ok_empty"
        assert stored["errors"] == []
