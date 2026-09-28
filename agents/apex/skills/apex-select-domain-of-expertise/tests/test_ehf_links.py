import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_ehf_links.py"
spec = importlib.util.spec_from_file_location("ehf_links", SCRIPT)
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)


@pytest.fixture
def pair(tmp_path):
    source = tmp_path / "history.md"
    source.write_text("Synthetic roles A and B with separately attributable work.")
    roles = [{"role_id": f"role-{n}", "job_title": f"Role {n}", "employer": "Example organization",
              "from_date": f"01/01/202{n}", "to_date": f"31/12/202{n}"} for n in (0, 1)]
    ledger = {"schema_version": 1, "application_id": "example",
              "source_manifest": [{"source_id": "history", "path": str(source), "sha256": links.digest(source)}],
              "roles": roles, "domains": []}
    jobs = [{**{k: role[k] for k in links.ROLE_FIELDS},
             "responsibilities": [f"Maintained records in role {n}."],
             "achievements": [f"Produced a report in role {n}."]} for n, role in enumerate(roles)]
    crosswalk = {"schema_version": 1, "application_id": "example", "links": [], "held": []}
    for n in (0, 1):
        evidence = {"evidence_id": f"e-{n}", "role_id": f"role-{n}", "role_relevance": 2,
                    "assertion_status": "SUPPORTED", "integration_policy": "OK_TO_INTEGRATE",
                    "source_refs": [{"source_id": "history", "locator": f"role {n}"}],
                    "action": jobs[n]["responsibilities"][0], "result": None,
                    "practice_periods": [], "period_note": "Activity dates unresolved; ownership clear."}
        ledger["domains"].append({"area_id": "area", "subarea_id": f"sub-{n}", "evidence": [evidence]})
        crosswalk["links"].append({"evidence_id": f"e-{n}", "targets": [
            {"job_index": n, "section": "responsibilities", "paragraph_index": 0,
             "text": jobs[n]["responsibilities"][0]}]})
    return ledger, {"jobs": jobs}, crosswalk


def test_unknown_practice_dates_do_not_block_supported_narrative(pair):
    result = links.validate(*pair)
    assert result["eligible_evidence"] == result["linked_targets"] == 2
    assert result["held_evidence"] == 0


def test_same_employer_does_not_allow_evidence_in_the_wrong_position(pair):
    ledger, ehf, crosswalk = pair
    target = crosswalk["links"][0]["targets"][0]
    target.update(job_index=1, text=ehf["jobs"][1]["responsibilities"][0])
    with pytest.raises(ValueError, match="different role"):
        links.validate(ledger, ehf, crosswalk)


def test_missing_evidence_link_is_detected(pair):
    pair[2]["links"].pop()
    with pytest.raises(ValueError, match="every eligible"):
        links.validate(*pair)


def test_changed_paragraph_text_is_detected(pair):
    pair[1]["jobs"][0]["responsibilities"][0] = "Edited wording without updating its crosswalk."
    with pytest.raises(ValueError, match="Stale or changed narrative"):
        links.validate(*pair)


@pytest.mark.parametrize("status,policy", [
    ("AMBIGUOUS", "HOLD_AS_PLACEHOLDER"),
    ("CONFLICTING", "DO_NOT_INTEGRATE_UNTIL_RESOLVED"),
    ("UNSUPPORTED_BUT_PLAUSIBLE", "INTEGRATE_WITH_CONFIRM_TAG"),
])
def test_unresolved_assertions_cannot_enter_clean_narrative(pair, status, policy):
    evidence = pair[0]["domains"][0]["evidence"][0]
    evidence.update(assertion_status=status, integration_policy=policy)
    with pytest.raises(ValueError, match="held/unknown"):
        links.validate(*pair)
    pair[2]["links"].pop(0)
    pair[2]["held"].append({"evidence_id": "e-0", "reason": "The specific assertion is unresolved."})
    assert links.validate(*pair)["held_evidence"] == 1


def test_sequence_evidence_stays_unallocated_while_other_roles_proceed(pair):
    evidence = pair[0]["domains"][0]["evidence"][0]
    evidence.update(role_id=None, role_relevance=None)
    pair[2]["links"].pop(0)
    pair[2]["held"].append({"evidence_id": "e-0", "reason": "Individual appointment is unknown."})
    result = links.validate(*pair)
    assert result["eligible_evidence"] == result["held_evidence"] == 1


def test_one_paragraph_may_evidence_multiple_domains(pair):
    domain = copy.deepcopy(pair[0]["domains"][0])
    domain["subarea_id"] = "another-subarea"
    domain["evidence"][0]["evidence_id"] = "e-another"
    pair[0]["domains"].append(domain)
    target = copy.deepcopy(pair[2]["links"][0])
    target["evidence_id"] = "e-another"
    pair[2]["links"].append(target)
    assert links.validate(*pair)["eligible_evidence"] == 3


def test_source_change_is_detected(pair):
    Path(pair[0]["source_manifest"][0]["path"]).write_text("Changed source responsibilities.")
    with pytest.raises(ValueError, match="Stale source"):
        links.validate(*pair)


def test_duplicate_evidence_cannot_hide_a_coverage_gap(pair):
    pair[0]["domains"][1]["evidence"][0]["evidence_id"] = "e-0"
    with pytest.raises(ValueError, match="Duplicate evidence"):
        links.validate(*pair)


def test_role_metadata_conflict_is_detected(pair):
    pair[1]["jobs"][0]["to_date"] = "31/12/2099"
    with pytest.raises(ValueError, match="role identity"):
        links.validate(*pair)


def test_cli_validates_file_revisions_and_never_writes_inputs(pair, tmp_path):
    ledger, ehf, crosswalk = pair
    paths = [tmp_path / name for name in ("ledger.json", "ehf.json", "crosswalk.json")]
    paths[0].write_text(json.dumps(ledger))
    paths[1].write_text(json.dumps(ehf))
    crosswalk.update(ledger_sha256=links.digest(paths[0]), ehf_input_sha256=links.digest(paths[1]))
    paths[2].write_text(json.dumps(crosswalk))
    command = [sys.executable, str(SCRIPT), "--ledger", str(paths[0]), "--ehf-input", str(paths[1]),
               "--crosswalk", str(paths[2])]
    before = [p.read_bytes() for p in paths]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "PASS"
    assert before == [p.read_bytes() for p in paths]
    paths[1].write_text(json.dumps(ehf, indent=2))
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Stale EHF revision" in json.loads(result.stdout)["error"]
