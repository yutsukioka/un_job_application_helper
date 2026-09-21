"""IMO refreshes the existing public API and selects exactly one full vacancy."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.imo import IMOAPIAdapter, _PUBLIC_DETAIL_FIELDS
from jobagg.http import HttpResponse, JobAggHTTPClient
from jobagg.models import OrganizationSource


API = "https://recruit.imo.org/api/CurrentJobVacancies"


def adapter(payload):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(json=lambda: deepcopy(payload))

    source = OrganizationSource(
        id="imo_api", name="IMO", ats_family="imo_api", base_url="https://recruit.imo.org"
    )
    return IMOAPIAdapter(AdapterContext(source=source, http=SimpleNamespace(get=get))), calls


def vacancy():
    return {
        **dict.fromkeys(_PUBLIC_DETAIL_FIELDS, ""),
        "jobVacancyId": 940,
        "title": "Officer",
        "jobDescription": "<p>Manage programme delivery.</p>",
        "maindutiesandresponsibilities": "<p>Manage programme delivery.</p>",
        "education": "<p>Advanced degree required.</p>",
        "essentialCompetencies": "<p>Demonstrated integrity.</p>",
        "desiredCompetencies": "<p>Additional language desirable.</p>",
        "competencyQuestions": ["Describe your oversight experience.", None],
        "backgroundQuestions": [None, "Are you available for travel?"],
    }


def test_fresh_public_response_replaces_stale_listing_body():
    fresh = vacancy()
    fresh["purposeforthepost"] = "New published purpose."
    value, calls = adapter([fresh, {**vacancy(), "jobVacancyId": 941}])
    stale = vacancy()
    stale["purposeforthepost"] = "Old stale purpose."
    original = deepcopy(stale)
    result = value.fetch_detail_for_listing_item(stale)
    assert calls == [(API, {"headers": {"Accept": "application/json"}})]
    assert result.external_id == "940" and result.raw["imo_detail_response_verified"] is True
    assert result.raw["imo_detail_fetch_kind"] == "fresh_current_vacancies_exact_id"
    assert (
        "New published purpose." in result.description
        and "Old stale purpose." not in result.description
    )
    assert stale == original
    assert result.description.count("Manage programme delivery.") == 1
    for text in [
        "Advanced degree required.",
        "Demonstrated integrity.",
        "Additional language desirable.",
        "Describe your oversight experience.",
        "Are you available for travel?",
    ]:
        assert text in result.description


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{**vacancy(), "jobVacancyId": 941}],
        [{"jobVacancyId": 940, "title": "Officer", "jobDescription": "Summary. " * 30}],
        [{**dict.fromkeys(_PUBLIC_DETAIL_FIELDS, ""), "jobVacancyId": 940, "title": "Officer"}],
    ],
)
def test_absent_or_partial_fresh_detail_never_falls_back_to_old_full_object(payload):
    value, calls = adapter(payload)
    assert value.fetch_detail_for_listing_item(vacancy()) is None
    assert len(calls) == 1


def test_duplicate_fresh_identity_is_rejected():
    value, calls = adapter([vacancy(), vacancy()])
    with pytest.raises(ValueError, match="duplicated"):
        value.fetch_detail_for_listing_item(vacancy())
    assert len(calls) == 1


@pytest.mark.parametrize("identity", [None, True, ""])
def test_invalid_listing_identity_never_dispatches(identity):
    value, calls = adapter([vacancy()])
    assert value.fetch_detail_for_listing_item({"jobVacancyId": identity}) is None
    assert calls == []


def test_unexpected_payload_contract_is_rejected():
    value, _ = adapter({"items": [vacancy()]})
    with pytest.raises(ValueError, match="not a vacancy list"):
        value.fetch_detail_for_listing_item(vacancy())


def test_existing_robots_gate_still_precedes_refresh_get():
    value, calls = adapter([vacancy()])
    value.context.robots = SimpleNamespace(allowed=lambda url: False)
    with pytest.raises(PermissionError):
        value.fetch_detail_for_listing_item(vacancy())
    assert not calls


def test_worker_makes_current_detail_capture_and_stores_new_response(tmp_path, monkeypatch):
    from datetime import datetime, UTC
    from jobagg.remediation_worker import Worker
    from jobagg.pipelines import http_checkpoint

    monkeypatch.setattr(http_checkpoint.time, "sleep", lambda seconds: None)
    registry = tmp_path / "sources.yaml"
    registry.write_text("""sources:
  - id: imo_api
    name: IMO
    ats_family: imo_api
    base_url: https://recruit.imo.org
    extra:
      api_url: https://recruit.imo.org/api/CurrentJobVacancies
""")
    robots = tmp_path / "robots.yaml"
    robots.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    owner = tmp_path / "owner.lock"
    bootstrap = tmp_path / "bootstrap.json"
    bootstrap.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "shared_lock": str(owner),
                "reviewed_at": datetime.now(UTC).isoformat(),
                "prior_writers_reviewed": True,
                "no_unmigrated_policy_state": True,
                "scope_source_ids": ["imo_api"],
                "evidence": [],
                "detail_attempts": [],
                "host_states": {},
                "source_holds": {},
                "review_note": "Temporary isolated test",
            }
        )
    )
    calls = []

    class Client(JobAggHTTPClient):
        def __init__(self):
            super().__init__(min_delay_seconds=0, max_retries=0)

        def _request(self, url, **kwargs):
            calls.append(url)
            item = vacancy()
            item["purposeforthepost"] = (
                "<p>"
                + ("Fresh detail duty. " if len(calls) > 1 else "Earlier listing duty. ") * 40
                + "</p>"
            )
            content = json.dumps([item]).encode()
            return HttpResponse(
                url, 200, {"Content-Type": "application/json"}, content.decode(), content
            )

    worker = Worker(
        registry=registry,
        robots=robots,
        workspace=tmp_path / "worker",
        shared_lock=owner,
        max_tasks=2,
        client_factory=lambda source, policy: Client(),
        policy_bootstrap=bootstrap,
    )
    result = worker.tick(execute=True)
    assert calls == [API, API]
    row = worker.db.get_job("imo_api:940")
    assert (
        "Fresh detail duty." in row["description"]
        and "Earlier listing duty." not in row["description"]
    )
    metas = [json.loads(p.read_text()) for p in worker.workspace.glob("captures/*/http/*.json")]
    detail = [m for m in metas if m["phase"]["kind"] == "detail"]
    assert len(detail) == 1 and detail[0]["phase"]["job_id"] == "940"
    assert detail[0]["status_code"] == 200 and detail[0]["body_captured"] is True
    assert result["completeness_certified"] is False
    with worker.db.connect() as conn:
        assert (
            conn.execute("SELECT status FROM remediation_tasks WHERE kind='detail'").fetchone()[0]
            == "done"
        )
