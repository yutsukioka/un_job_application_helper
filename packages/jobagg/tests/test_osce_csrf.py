import copy
import hashlib
import json

import pytest

from jobagg.osce_fragments import request_url, verify_captures
from jobagg.pipelines.host_recovery import authorize_osce_csrf_probe, host_eligibility
from test_osce_native_browser import inputs as native_inputs


def inputs():
    source, state, evidence, review = native_inputs()
    evidence.update(
        url=request_url("123", "default2673", 2, 1),
        method="POST",
        transport="chromium_cdp_native_v1",
        transport_diagnostics={"request_body_bytes": 0},
    )
    review = {
        "observation": "reviewed_missing_csrf_header",
        "observed_at": 150,
        "evidence_sha256": hashlib.sha256(
            json.dumps(evidence, sort_keys=True).encode()
        ).hexdigest(),
        "provider_csrf_rule_verified": True,
        "current_document_token_present": True,
    }
    return source, state, evidence, review


def test_csrf_probe_keeps_hold_and_history_and_allows_one_owner_once():
    source, state, evidence, review = inputs()
    before = copy.deepcopy(state)
    kwargs = dict(owner="reviewer", now=200, expires_at=700, access_review=review)
    leased = authorize_osce_csrf_probe(state, evidence, source, **kwargs)
    assert state == before and all(leased[k] == v for k, v in state.items())
    assert host_eligibility(leased, 201, probe_owner="reviewer")["allowed"]
    assert not host_eligibility(leased, 201, probe_owner="worker")["allowed"]
    assert not host_eligibility(leased, 701, probe_owner="reviewer")["allowed"]
    assert not host_eligibility({**leased, "evidence": "new-denial"}, 201, probe_owner="reviewer")[
        "allowed"
    ]
    leased.pop("reviewed_osce_csrf_probe")
    with pytest.raises(ValueError, match="already attempted"):
        authorize_osce_csrf_probe(leased, evidence, source, **kwargs)


@pytest.mark.parametrize(
    "patch",
    [
        {"url": "https://vacancies.osce.org/jobs/search/"},
        {"url": request_url("123", "default2673", 3, 1)},
        {"method": "GET"},
        {"status_code": 429},
        {"phase": {"kind": "detail"}},
        {"transport_diagnostics": {"request_body_bytes": 1}},
        {"transport_diagnostics": {"request_body_bytes": 0, "csrf_observation": {"present": True}}},
    ],
)
def test_csrf_recovery_rejects_other_or_corrected_denials(patch):
    source, state, evidence, review = inputs()
    evidence.update(patch)
    review["evidence_sha256"] = hashlib.sha256(
        json.dumps(evidence, sort_keys=True).encode()
    ).hexdigest()
    with pytest.raises(ValueError):
        authorize_osce_csrf_probe(
            state, evidence, source, owner="reviewer", now=200, expires_at=700, access_review=review
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"observed_at": -90000},
        {"observed_at": 250},
        {"evidence_sha256": "different"},
        {"provider_csrf_rule_verified": False},
        {"current_document_token_present": False},
    ],
)
def test_csrf_recovery_requires_bound_recent_review(patch):
    source, state, evidence, review = inputs()
    with pytest.raises(ValueError):
        authorize_osce_csrf_probe(
            state,
            evidence,
            source,
            owner="reviewer",
            now=200,
            expires_at=700,
            access_review={**review, **patch},
        )


def test_inventory_verification_rejects_missing_or_mismatched_csrf_diagnostics(tmp_path):
    from test_osce_fragments import capture_bundle, bundle

    pages, paths = capture_bundle(tmp_path)
    for csrf in [{}, {"available": True, "present": True, "matches_document": False}]:
        meta = json.loads(paths[1].read_text())
        meta["transport_diagnostics"]["csrf_observation"] = csrf
        paths[1].write_text(json.dumps(meta))
        with pytest.raises(ValueError, match="CSRF"):
            verify_captures(bundle(pages), paths)
