import copy
import hashlib
import json
import time
from pathlib import Path

import pytest

from jobagg.pipelines.host_recovery import authorize_configuration_probe, host_eligibility
from jobagg.pipelines.sync_source import load_sources


def inputs(source_id):
    source = next(s for s in load_sources(Path(__file__).parents[1] / "config/organizations.yaml")
                  if s.id == source_id)
    worldbank = source_id == "worldbank_csod"
    evidence = {"url": source.extra["api_url"] if worldbank else
                "https://vacancies.osce.org/styles/core.css?v=1",
                "status_code": 401 if worldbank else 403,
                "error": "no Authorization header found" if worldbank else "Access Forbidden",
                "method": "POST" if worldbank else "GET", "phase": {"kind": "listing"},
                "error_type": "HTTPError", "failure_category": "access_denied"}
    state = {"stopped": True, "failure_category": "access_denied", "evidence": "old.json",
             "last_request_at": 100, "eligible_at": 150, "consecutive_transport_failures": 2}
    return state, evidence, source


@pytest.mark.parametrize("source_id", ["worldbank_csod", "osce_custom_html"])
def test_only_one_owner_can_probe_without_clearing_hold_or_counters(source_id):
    state, evidence, source = inputs(source_id)
    before = copy.deepcopy(state)
    leased = authorize_configuration_probe(state, evidence, source, owner="reviewer", now=200, expires_at=700)
    assert state == before
    assert all(leased[key] == value for key, value in before.items())
    assert host_eligibility(leased, 201, probe_owner="reviewer")["allowed"]
    assert not host_eligibility(leased, 201, probe_owner="worker")["allowed"]
    assert not host_eligibility(leased, 701, probe_owner="reviewer")["allowed"]
    assert not host_eligibility({**leased, "evidence": "new-denial.json"}, 201, probe_owner="reviewer")["allowed"]


@pytest.mark.parametrize("source_id", ["worldbank_csod", "osce_custom_html"])
@pytest.mark.parametrize("patch", [
    {"status_code": 429}, {"phase": {"kind": "detail"}},
    {"url": "https://vacancies.osce.org/jobs/search/"}, {"error_type": "TimeoutError"},
])
def test_unrelated_failure_cannot_authorize_probe(source_id, patch):
    state, evidence, source = inputs(source_id)
    evidence.update(patch)
    with pytest.raises(ValueError):
        authorize_configuration_probe(state, evidence, source, owner="reviewer", now=200, expires_at=700)


@pytest.mark.parametrize("source_id", ["worldbank_csod", "osce_custom_html"])
def test_unrepaired_config_cannot_authorize_probe(source_id):
    state, evidence, source = inputs(source_id)
    if source_id == "worldbank_csod":
        source.extra["requires_bearer_token"] = False
    else:
        source.extra["browser_render"]["load_stylesheets"] = True
    with pytest.raises(ValueError):
        authorize_configuration_probe(state, evidence, source, owner="reviewer", now=200, expires_at=700)


def test_guarded_transport_new_denial_invalidates_configuration_probe(tmp_path, monkeypatch):
    from urllib.error import HTTPError
    from jobagg.pipelines.http_checkpoint import HostIneligible
    from test_host_recovery import make_capture

    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    state, evidence, source = inputs("osce_custom_html")
    calls = []

    def deny(url, **kwargs):
        calls.append(url)
        raise HTTPError(url, 403, "Denied", {}, None)

    capture = make_capture(tmp_path, "capture", deny)
    now = time.time()
    state = authorize_configuration_probe(state, evidence, source, owner=capture.probe_owner,
                                         now=now, expires_at=now + 500)
    path = tmp_path / "hosts" / ("host-" + hashlib.sha256(b"vacancies.osce.org").hexdigest()[:24] + ".json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(state))
    with pytest.raises(HTTPError):
        capture.request("https://vacancies.osce.org/jobs/search/")
    with pytest.raises(HostIneligible):
        capture.request("https://vacancies.osce.org/jobs/search/")
    latest = json.loads(path.read_text())
    assert latest["stopped"] and latest["evidence"] != "old.json"
    assert len(calls) == 1
    assert not host_eligibility(latest, now + 1, probe_owner=capture.probe_owner)["allowed"]


@pytest.mark.parametrize("complete", [True, False])
def test_command_releases_only_after_independent_verification(tmp_path, monkeypatch, complete):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from jobagg import recover_public_sources as command
    from jobagg.models import SourceRunDiagnostics

    state, evidence, source = inputs("worldbank_csod")
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence))
    state["evidence"] = str(evidence_path)
    root = tmp_path / "policy"
    (root / "hosts").mkdir(parents=True)
    hold = root / "hosts" / ("host-" + hashlib.sha256(b"us.api.csod.com").hexdigest()[:24] + ".json")
    hold.write_text(json.dumps(state))
    finishes = []
    policy = SimpleNamespace(root=root, source_hold=lambda _: False,
                             reserve=lambda *args: None, finish=lambda *args: finishes.append(args))
    adapter = SimpleNamespace(fetch_jobs=lambda: [], run_diagnostics=SourceRunDiagnostics(
        source_id=source.id, pagination_complete=True))
    worker = SimpleNamespace(by_id={source.id: source}, shared_policy=policy,
                             shared_lock=tmp_path / "owner.lock", workspace=tmp_path / "workspace",
                             binding={"implementation_sha256": "fixture"},
                             policy_due=lambda *args: 0,
                             context=lambda *args: (adapter, None, SimpleNamespace(probe_owner="reviewer")))
    monkeypatch.setattr(command, "Worker", lambda **kwargs: worker)
    monkeypatch.setattr(command, "shared_owner", lambda _: nullcontext())
    monkeypatch.setattr(command, "verify_listing", lambda *args: {"complete": complete})
    argv = ["--registry", "unused", "--robots", "unused", "--workspace", str(worker.workspace),
            "--shared-lock", str(worker.shared_lock), "--output-dir", str(tmp_path / "out"),
            "--source", source.id, "--execute"]
    if complete:
        assert command.main(argv) == 0
    else:
        with pytest.raises(ValueError, match="reconciliation"):
            command.main(argv)
    latest = json.loads(hold.read_text())
    assert latest["stopped"] is (not complete)
    assert "reviewed_configuration_probe" not in latest
    for key in ["last_request_at", "eligible_at", "consecutive_transport_failures"]:
        assert latest[key] == state[key]
    assert finishes[0][1] == ("complete_listing_verified" if complete else "failed")
