import copy,json,hashlib
from datetime import datetime,UTC,timedelta
import pytest
from test_remediation_worker import setup
from test_worker_timeout_recovery import task_row
from jobagg.http_safe import SSRFProtectionError
from jobagg.pipelines.reviewed_text_changes import reviewed_text_change,REVIEWS
from jobagg.publication_snapshot import capture_unstarted_publication,_recovery_identity


def test_url_policy_is_dead_letter_and_does_not_poison_host(setup):
    worker,replies,calls,_=setup;worker.max_tasks=1;worker.tick(execute=True)
    url=next(url for url in replies if not url.endswith('/jobs'))
    replies[url]=SSRFProtectionError('URL host is not in the organization allowlist')
    worker.tick(execute=True);row=task_row(worker)
    assert row['status']=='dead_letter' and row['attempts']==1
    assert not worker.host_state('demo.example').get('stopped')
    worker.tick(execute=True)
    assert task_row(worker)['attempts']==1


def journal(tmp_path):
    output=tmp_path/'output';output.mkdir();state=tmp_path/'state';generation='a'*32
    root=state/'generations'/generation;root.mkdir(parents=True)
    plan=root/'plan.json';plan.write_text('{}')
    old=datetime.now(UTC)-timedelta(hours=1)
    gate={'generation_id':generation,'state':'complete','status':'published','database_transactions_complete':True,
          'completed_at':old.isoformat(),'plan_path':str(plan),'plan_sha256':hashlib.sha256(plan.read_bytes()).hexdigest()}
    (root/'result.json').write_text(json.dumps(gate));(output/'.jobagg-publication-state.json').write_text(json.dumps(gate))
    request={'generated_at':datetime.now(UTC).isoformat()}
    return output,state,request,gate,root


def test_unstarted_intent_proven_by_preceding_complete_journal(tmp_path):
    output,state,request,_,_=journal(tmp_path)
    proof=capture_unstarted_publication(request,output,state,owner_held=True)
    assert proof['generation_not_started'] and proof['kind']=='verified_unstarted_publication'


@pytest.mark.parametrize('mutation',['incomplete','later','same_owner','changed_result','no_owner'])
def test_no_write_proof_rejects_unsafe_or_unbound_journal(tmp_path,mutation):
    output,state,request,gate,root=journal(tmp_path)
    if mutation=='incomplete':gate['state']='exporting'
    if mutation=='later':gate['completed_at']=(datetime.now(UTC)+timedelta(hours=1)).isoformat()
    if mutation=='same_owner':gate['request_identity_sha256']=_recovery_identity(request)
    if mutation=='changed_result':(root/'result.json').write_text('{}')
    (output/'.jobagg-publication-state.json').write_text(json.dumps(gate))
    with pytest.raises(ValueError):capture_unstarted_publication(request,output,state,owner_held=mutation!='no_owner')


def test_text_review_is_not_a_generic_shorter_text_bypass():
    a={'source_id':'unwomen_oracle_hcm','external_id':'36553','description':'New truncated content'}
    b={**a,'description':'Older content with important responsibilities and qualifications'}
    assert reviewed_text_change(b,a,{'captures':REVIEWS[-1]['captures']})['accepted'] is False


def test_exact_text_review_rejects_any_unreviewed_body_or_capture(monkeypatch):
    import jobagg.pipelines.reviewed_text_changes as mod
    old={'source_id':'demo','external_id':'1','description':'retained text'}
    new={**old,'description':'new text'};captures=[{'path':'capture.json','sha256':'a'*64}]
    before=hashlib.sha256(old['description'].encode()).hexdigest();after=hashlib.sha256(new['description'].encode()).hexdigest()
    monkeypatch.setattr(mod,'REVIEWS',[{'source_id':'demo','external_id':'1','before_sha256':before,
        'incoming_sha256':after,'captures':captures,'reason':'reviewed exact change'}])
    proof={'parsed_source_text_sha256':after,'captures':captures}
    assert mod.reviewed_text_change(old,new,proof)['accepted']
    assert not mod.reviewed_text_change(old,{**new,'description':'new text truncated'},proof)['accepted']
    assert not mod.reviewed_text_change(old,new,{**proof,'captures':[]})['accepted']
    assert not mod.reviewed_text_change(old,{**new,'external_id':'2'},proof)['accepted']
