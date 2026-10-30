#!/usr/bin/env python3
"""One reviewed production handoff for independent policy evidence audit.

Preserves quota charges, holds and due work. Any failure retains the fence.
Do not repeat after an intent exists without inspecting the journal.
"""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid
from contextlib import closing

from jobagg import runtime_authority as authority, runtime_source_head as heads
from jobagg import runtime_containment as containment, publication_work as work
from jobagg import pending_publication as pending, remediation_worker as worker
from jobagg.publication_fence import verify_boundary

D=Path('/Volumes/MacbookAirM2/un_job_application_helper/jobagg-operations/audit-independent-20261030')
LIVE=Path('/Users/yutsukioka2/git/un_job_application_helper/reports/jobagg-deterministic-fetch-2026-09-14/dispatcher.proposed.json')
RUN='31fe9c77-9fc6-4d97-a937-8ac1d42111b7'

def require(ok,message):
    if not ok: raise ValueError(message)

def digest(data): return hashlib.sha256(data).hexdigest()
def encode(value): return (json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
def save(path,value):
    data=value if isinstance(value,bytes) else encode(value)
    with path.open('xb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())

def check_vector(vector,approved,boundary):
    require(approved and boundary,'Containment or publication boundary invalid')
    require(vector['pending'] is None and vector.get('unscoped_held') is None,'Publication requires reconciliation')
    require(vector['revalidation_reasons'] in ([],['time_transition_due']),'Unexpected revalidation debt')
    require(vector['held'] is not None,'Scoped holds not established')

def protected(conn):
    result={}
    for table in ('jobagg_dirty_current','jobagg_dirty_versions','jobagg_dirty_cohorts','jobagg_dirty_transitions','jobagg_legacy_controls','jobagg_legacy_dependencies'):
        h=hashlib.sha256();n=0
        for row in conn.execute('SELECT * FROM '+table+' ORDER BY 1'):
            h.update(json.dumps(row,default=str).encode());n+=1
        result[table]=[n,h.hexdigest()]
    h=hashlib.sha256()
    for row in conn.execute('SELECT * FROM jobagg_legacy_review_holds WHERE run_id!=? ORDER BY run_id',(containment.KEY,)):
        h.update(json.dumps(row,default=str).encode())
    result['historical_holds_sha256']=h.hexdigest()
    result['next_transition_at']=conn.execute('SELECT next_transition_at FROM jobagg_dirty_state WHERE id=1').fetchone()[0]
    return result

def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');a=p.parse_args()
    old_bytes=(D/'dispatcher.before.json').read_bytes();new_bytes=(D/'dispatcher.after.json').read_bytes()
    old=json.loads(old_bytes);new=json.loads(new_bytes)
    before=json.loads((D/'contract.before.json').read_text());after=json.loads((D/'contract.after.json').read_text())
    require(digest(old_bytes)==before['config_sha256'] and digest(new_bytes)==after['config_sha256'],'Config binding differs')
    require(LIVE.read_bytes()==old_bytes,'Live config changed')
    require(not (D/'binding-intent.json').exists(),'Previous intent exists; inspect it before retry')
    require(worker.implementation_hash()==after['jobagg_implementation_sha256'],'Candidate seal differs')
    delta={k for k in before if before[k]!=after[k]}
    require(delta=={'config_sha256','jobagg_implementation_sha256','sha256'},'Contract scope differs')
    if not a.execute:
        print(json.dumps({'staged':True,'old':before['jobagg_implementation_sha256'],'new':after['jobagg_implementation_sha256']}));return
    from inspect_pending_publication import clone_file
    lock_path=Path(old['shared_lock_path']);fence=Path(old['maintenance_file'])
    workspace=Path(old['worker_argv'][old['worker_argv'].index('--workspace')+1]);marker=workspace/'worker_workspace.json'
    old_marker_bytes=marker.read_bytes();old_marker=json.loads(old_marker_bytes)
    require(old_marker['implementation_sha256']==before['jobagg_implementation_sha256'],'Workspace marker differs')
    new_marker={**old_marker,'implementation_sha256':after['jobagg_implementation_sha256']}
    args=new['worker_argv'];arg=lambda name:args[args.index(name)+1]
    require(worker.sha(arg('--registry'))==old_marker['registry_sha256'] and worker.sha(arg('--robots'))==old_marker['robots_sha256'], 'Registry or robots policy differs')
    require(str(Path(arg('--shared-lock')).absolute())==old_marker['shared_lock'], 'Shared owner differs')
    run=Path(old['state_dir'])/'runs'/RUN
    outcome=json.loads((run/'outcome.json').read_text());failure=json.loads((run/'stdout.log').read_text())
    require(outcome['run_id']==RUN and outcome['worker_exit_code']==3 and outcome['publication_status']=='not_started','Unexpected failed run')
    require(failure.get('reason')=='policy_audit_overdue_explicit_repair_required','Unexpected failure')
    require(not (run/'acceptance.json').exists() and not (run/'worker_result.json').exists(),'Acceptance exists')
    output=Path(old['publication_argv'][old['publication_argv'].index('--output-dir')+1]);health_path=Path(old['attempt_state_dir'])/'health.json'
    token=encode({'purpose':'policy-audit-independent-handoff','operator':'Codex for yutsukioka2','created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'token':uuid.uuid4().hex})
    with lock_path.open('r+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        health=json.loads(health_path.read_text());gate=json.loads((output/'.jobagg-publication-state.json').read_text())
        require(health['active_attempts']=={} and health['unresolved_publication'] is False,'Runtime not idle')
        require(gate['state']=='complete' and gate['readback_verified'] is True,'Publication boundary incomplete')
        require(pending.inspect(Path(old['state_dir'])) is None,'Pending publication intent')
        require(LIVE.read_bytes()==old_bytes and marker.read_bytes()==old_marker_bytes,'Config or marker changed')
        for pid in (outcome['worker_pid'],outcome['wrapper_pid']):
            try: os.kill(pid,0)
            except ProcessLookupError: continue
            raise ValueError('Recorded process is live')
        database=Path(old['publication_worker_database'])
        require(D.stat().st_dev==database.stat().st_dev,'Backups must be on same volume')
        require(not fence.exists(),'An operator maintenance fence exists')
        save(fence,token);save(D/'fence-owned.json',token)
        for suffix in ('','-wal','-shm'):
            source=Path(str(database)+suffix)
            if source.exists(): clone_file(source,D/('worker.before.sqlite3'+suffix))
        save(D/'workspace-marker.before.json',old_marker_bytes);save(D/'workspace-marker.after.json',new_marker)
        for name in ('request.json','outcome.json','state.json','stdout.log'): save(D/('failed-'+name),(run/name).read_bytes())
        save(D/'health.before.json',health_path.read_bytes())
        with closing(sqlite3.connect(database,timeout=1)) as conn:
            work.configure(conn);state=authority.validate(conn)
            require(state['run_id']==RUN and state['phase']=='worker' and state['revision']==state['start_revision'],'Failed admission changed')
            require(state['config_sha256']==before['config_sha256'],'Native config differs')
            require(not conn.execute('SELECT 1 FROM jobagg_accepted_events WHERE run_id=? LIMIT 1',(RUN,)).fetchone(),'Accepted events exist')
            control=json.loads(state['control_json']);vector=work.vector(conn,contract_sha256=before['sha256'],now=time.time())
            check_vector(vector,containment.approved(conn,config_sha256=before['config_sha256'],implementation_sha256=before['jobagg_implementation_sha256'],policy_location=old['policy_authority_location']),
               control.get('publication_scope') is not None and verify_boundary(control.get('publication_cursor'),expected_scope=control['publication_scope']))
            require(conn.execute('SELECT contract_sha256 FROM jobagg_dirty_state WHERE id=1').fetchone()[0]==before['sha256'],'Dirty contract differs')
            require(conn.execute('SELECT complete FROM jobagg_dirty_revalidation WHERE id=1 AND contract_sha256=?',(before['sha256'],)).fetchone()==(1,),'Revalidation incomplete')
            review_text=conn.execute('SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?',(containment.KEY,)).fetchone()[0];review=json.loads(review_text)
            baseline=protected(conn);head=Path(json.loads(state['bootstrap_json'])['restore_head_path']);save(D/'source-head.before.json',head.read_bytes())
            save(D/'binding-intent.json',{'native_before':state,'protected_before':baseline,'old_contract':before['sha256'],'new_contract':after['sha256'],'failed_run':RUN,'worker_success_credit':False,'audit_separate_operator_authorized':True})
            receipt=digest((D/'binding-intent.json').read_bytes())
            review_after={**review,'config_sha256':after['config_sha256'],'implementation_sha256':after['jobagg_implementation_sha256'],'audit_independent_handoff':{'receipt':str(D/'binding-intent.json'),'sha256':receipt}}
            statements=[('UPDATE jobagg_runtime_state SET config_sha256=? WHERE id=1 AND config_sha256=?',(after['config_sha256'],before['config_sha256'])),
              ('UPDATE jobagg_dirty_state SET contract_sha256=? WHERE id=1 AND contract_sha256=?',(after['sha256'],before['sha256'])),
              ('UPDATE jobagg_dirty_revalidation SET contract_sha256=?,receipt_sha256=? WHERE id=1 AND complete=1',(after['sha256'],receipt)),
              ('UPDATE jobagg_legacy_review_holds SET review_json=? WHERE run_id=? AND review_json=?',(json.dumps(review_after,sort_keys=True),containment.KEY,review_text))]
            allowed={'jobagg_runtime_state':{'phase','config_sha256','source_transaction_id'},'jobagg_dirty_state':{'contract_sha256'},'jobagg_dirty_revalidation':{'contract_sha256','receipt_sha256'},'jobagg_legacy_review_holds':{'review_json'}}
            def authorize(action,table,column,db,trigger):
                if action in {sqlite3.SQLITE_INSERT,sqlite3.SQLITE_DELETE}:return sqlite3.SQLITE_DENY
                if action==sqlite3.SQLITE_UPDATE and column not in allowed.get(table,set()):return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            with heads.grant(conn,owner_fd=lock.fileno(),control=True,authorizer=authorize):
                for sql,args in statements+[('UPDATE jobagg_runtime_state SET phase=? WHERE id=1',('idle',))]:conn.execute('EXPLAIN '+sql,args).fetchall()
                with conn:
                    authority.finish_publication(conn,RUN)
                    for sql,args in statements:require(conn.execute(sql,args).rowcount==1,'Metadata update missed row')
                    current=authority.validate(conn)
                    require(current['phase']=='idle' and current['revision']==state['revision'],'Authority closure differs')
                    require(protected(conn)==baseline,'Protected holds or work changed')
                    require(containment.approved(conn,config_sha256=after['config_sha256'],implementation_sha256=after['jobagg_implementation_sha256'],policy_location=old['policy_authority_location']),'New containment binding invalid')
            save(D/'binding-sql-complete.json',{'phase':current['phase'],'revision':current['revision'],'protected_unchanged':True,'audit_completed':False,'worker_success_credit':False})
        require(fence.read_bytes()==token,'Fence changed')
        temp=marker.with_name('.audit-independent-marker.tmp');save(temp,new_marker);os.chmod(temp,marker.stat().st_mode&0o777);temp.replace(marker)
        args=new['worker_argv'];arg=lambda name:args[args.index(name)+1]
        instance=worker.Worker(registry=arg('--registry'),robots=arg('--robots'),workspace=workspace,shared_lock=lock_path,policy_authority_location=new['policy_authority_location'])
        preview=instance.preview();require(preview['binding']==new_marker and preview['network_requests']==0 and preview['database_writes']==0,'Worker preview failed');save(D/'worker-preview.json',preview)
        tmp=LIVE.with_name('.audit-independent-config.tmp');save(tmp,new_bytes);tmp.replace(LIVE)
        require(LIVE.read_bytes()==new_bytes and marker.read_text() and worker.implementation_hash()==after['jobagg_implementation_sha256'],'Activation check failed')
        require(fence.read_bytes()==token,'Fence changed')
        save(D/'activation-verified.json',{'activated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'config_sha256':after['config_sha256'],'contract_sha256':after['sha256'],'worker_seal':after['jobagg_implementation_sha256'],'holds_preserved':True,'audit_completed':False,'cron_schedule_unchanged':True})
        fence.unlink();print(json.dumps({'activated':True,'audit_completed':False,'evidence':str(D)}))
if __name__=='__main__':main()
