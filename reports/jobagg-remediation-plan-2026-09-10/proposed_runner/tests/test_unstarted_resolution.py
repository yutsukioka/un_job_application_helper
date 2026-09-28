import importlib.util,json,hashlib
from pathlib import Path
import pytest
import test_runner as base


def module():
    spec=importlib.util.spec_from_file_location('triage_runner',base.RUNNER)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def write(p,v):p.write_text(json.dumps(v));return hashlib.sha256(p.read_bytes()).hexdigest()


def test_resolved_unstarted_intent_not_replayed_and_tampering_fails(tmp_path):
    m=module();run=tmp_path/'run';run.mkdir()
    out=run/'outcome.json';oh=write(out,{'run_id':'r','publication_status':'unknown_requires_review_no_replay'})
    rh=write(run/'publication_request.json',{'generated_at':'2026-09-17T05:30:00+00:00'})
    ih=write(run/'publication_intent.json',{'run_id':'r'})
    gate={'state':'complete','completed_at':'2026-09-17T05:11:00+00:00'}
    plan=run/'plan.json';ph=write(plan,{})
    result=run/'result.json';gh=write(result,gate)
    proof={'kind':'verified_unstarted_publication','generation_not_started':True,'prior_gate':gate,
           'prior_plan':{'path':str(plan),'sha256':ph},'prior_result':{'path':str(result),'sha256':gh}}
    resolution={'run_id':'r','previous_outcome_sha256':oh,'original_request_sha256':rh,'original_intent_sha256':ih,'proof':proof}
    write(run/'publication_no_write_resolution.json',resolution)
    assert m.unresolved_publications(tmp_path)==[]
    write(run/'publication_request.json',{'generated_at':'2026-09-17T04:00:00+00:00'})
    with pytest.raises(m.InvalidReport):m.unresolved_publications(tmp_path)
