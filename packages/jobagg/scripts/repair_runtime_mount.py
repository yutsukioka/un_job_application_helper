#!/usr/bin/env python3
"""Explicit live device-number renewal after a verified reboot.

Preserve transaction tokens, revisions, policy DB, holds, worker seal and original
publication proofs. Preparation fences the idle owner and saves an exact plan.
Execution changes only reviewed identity fields. A partial handoff keeps its
fence and resumes only the same plan. This is not a restore or hold-release tool.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import time
from types import SimpleNamespace
import uuid

from jobagg import runtime_authority as authority, runtime_source_head as heads
from jobagg import publication_fence as publication
from jobagg import remediation_worker as worker
from jobagg.policy_authority import PolicyAuthority, _schema_hash

LIVE = Path('/Users/yutsukioka2/git/un_job_application_helper/reports/jobagg-deterministic-fetch-2026-09-14/dispatcher.proposed.json')
CONFIG_SHA = '566e5a7233d0c6eb10c158235b297c81aa65ecc3cf627e59ae492a352933ecab'
SEAL = 'dba19225ae326c3cc00b655203f23f0af3b5eab2b093a0ab22302346df590e85'
BASE = Path('/Users/yutsukioka2/git/un_job_application_helper-releases/pr143-075cd7ac-dispatcher-timeouts/source/reports/jobagg-remediation-plan-2026-09-10/proposed_runner')
D = Path('/Volumes/MacbookAirM2/un_job_application_helper/jobagg-operations/mount-renewal-20261031')
RUN = 'f03a280d-b0bd-4f31-8076-dc00cbd0be2a'
DEADLINE = float('inf')
READ_BYTES = 0
MAX_READ_BYTES = 8 * 1024**3

def check():
    if time.monotonic() >= DEADLINE:
        raise TimeoutError('Repair deadline reached; retain exact journal')


def require(value, reason):
    if not value:
        raise ValueError(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def sha(path):
    global READ_BYTES
    check()
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            check(); READ_BYTES += len(block)
            require(READ_BYTES <= MAX_READ_BYTES, 'Repair read ceiling reached')
            h.update(block)
    return h.hexdigest()


def save(path, value):
    data = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()
    with Path(path).open('xb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    sync_parent(path)


def sync_parent(path):
    fd = os.open(Path(path).parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def atomic(path, value):
    from jobagg.atomic_files import atomic_write_text
    atomic_write_text(path, json.dumps(value, sort_keys=True)+'\n')


def renew(path, expected, actual, mount, old, new):
    """Reject any change other than the proved mount's device number."""
    path = Path(path)
    require(path.resolve() == path and path.is_relative_to(mount), 'Aliased or out-of-volume path')
    require(set(expected) == set(actual), 'Identity shape changed')
    require(expected.get('device') == old and actual.get('device') == new, 'Unexpected device transition')
    require({k:v for k,v in expected.items() if k != 'device'} ==
            {k:v for k,v in actual.items() if k != 'device'}, 'Non-device identity changed: '+str(path))
    return actual


def pinned_volume(config):
    guard = config['storage_guard']; mount = Path(guard['mount_root'])
    sentinel = Path(guard['sentinel_path'])
    require(sha(sentinel) == guard['sentinel_sha256'], 'Storage sentinel changed')
    expected = json.loads(sentinel.read_text())['volume_uuid']
    observed = plistlib.loads(subprocess.run(['/usr/sbin/diskutil','info','-plist',str(mount)],
                                            check=True,capture_output=True,timeout=10).stdout)
    require(observed.get('VolumeUUID') == expected and observed.get('MountPoint') == str(mount),
            'Persistent volume differs')
    return mount, expected


def bind(path):
    p = Path(path); s = p.stat()
    require(p.resolve() == p, 'Aliased path')
    return {'device':s.st_dev,'inode':s.st_ino}


def restricted(allowed, guard):
    def authorize(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_UPDATE:
            return sqlite3.SQLITE_OK if (database == 'main' and trigger is None and
                    column in allowed.get(table,set())) else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_TRANSACTION:
            if table == 'COMMIT':
                try: guard()
                except Exception: return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action in {sqlite3.SQLITE_READ,sqlite3.SQLITE_SELECT,sqlite3.SQLITE_FUNCTION}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and table in {'table_list','database_list'} and column is None:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return authorize


def protected(conn):
    tables = ('jobagg_dirty_current','jobagg_dirty_versions','jobagg_dirty_cohorts',
              'jobagg_dirty_transitions','jobagg_legacy_controls','jobagg_legacy_dependencies',
              'jobagg_legacy_review_holds','jobagg_dirty_state','jobagg_dirty_revalidation')
    result = {}
    for table in tables:
        check()
        h=hashlib.sha256(); n=0
        for row in conn.execute('SELECT * FROM '+table+' ORDER BY 1'):
            check(); h.update(json.dumps(row,default=str).encode()); n+=1
        result[table]=[n,h.hexdigest()]
    return result


def runner():
    spec=importlib.util.spec_from_file_location('mount_renewal_runner', BASE/'runner.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@contextmanager
def owner(config):
    path=Path(config['shared_lock_path'])
    with path.open('r+b') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        heads.owner(f.fileno(),path)
        yield f.fileno()


def prepare():
    require(not D.exists(), 'Operation already exists; inspect its exact journal')
    require(sha(LIVE)==CONFIG_SHA and worker.implementation_hash()==SEAL,'Config or worker seal changed')
    config=json.loads(LIVE.read_text()); mount,volume=pinned_volume(config)
    db=Path(config['publication_worker_database']); status=Path(config['attempt_state_dir'])
    policy=Path(config['policy_authority_location']['root']); fence=Path(config['maintenance_file'])
    out=Path(config['publication_argv'][config['publication_argv'].index('--output-dir')+1])
    from inspect_pending_publication import clone_file
    with owner(config) as fd:
        health=json.loads((status/'health.json').read_text()); gate=json.loads((out/'.jobagg-publication-state.json').read_text())
        require(not health.get('active_attempts') and health.get('unresolved_publication') is False,'Active or unresolved process')
        require(gate.get('state')=='complete' and gate.get('readback_verified') is True,'Publication incomplete')
        require(not fence.exists(),'Other maintenance fence exists')
        D.mkdir(mode=0o700)
        token={'purpose':'reviewed-device-number-renewal','token':uuid.uuid4().hex}
        save(fence,token); save(D/'fence.json',token)
        try:
            # Backups remain private and preserve each original family before any SQL write.
            def backup(path,name):
                for suffix in ('','-wal','-shm'):
                    source=Path(str(path)+suffix)
                    if source.exists(): clone_file(source,D/(name+suffix))
            backup(db,'worker.before.sqlite3'); backup(policy/'authority.sqlite3','policy.before.sqlite3')
            with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
                conn.execute('PRAGMA query_only=ON')
                state=authority.state(conn); bootstrap=json.loads(state['bootstrap_json']); control=json.loads(state['control_json'])
                require(state['phase']=='accepted' and state['run_id']==RUN and state['revision']==29821,'Active native state changed')
                require(state['config_sha256']==CONFIG_SHA and control['publication_sequence']==26,'Active binding changed')
                require(bootstrap['schema_sha256']==authority._schema_hash(conn),'Worker schema changed')
                require(json.loads(state['tables_json'])==authority._tables(conn),'Worker fenced tables changed')
                require(not control.get('reusable_projection'),'Existing reusable projection requires separate review')
                controls=Path(control['publication_control_dir'])
                require(controls.resolve()==controls and (not controls.exists() or not any(controls.iterdir())),
                        'Next batch already contains work')
                require(not (Path(config['state_dir'])/'pending_accepted_work.json').exists(),'Existing pending marker requires separate review')
                source_path=Path(bootstrap['restore_head_path']); source=json.loads(source_path.read_text())
                require(source['transaction_id']==state['source_transaction_id'] and source['minimum_revision']<=state['revision']
                        and source['epoch']==state['epoch'],'Uncertain transaction; device repair refused')
                require(source['owner_identity']==bootstrap['owner_identity'],'Owner records disagree')
                old=source['database']['device']; new=db.stat().st_dev
                require(old!=new and new==mount.stat().st_dev,'No device-only transition')
                require(source['database']=={'path':str(db),'device':old,'inode':db.stat().st_ino},'Worker database identity changed')
                new_boot=deepcopy(bootstrap)
                new_boot['owner_identity']=renew(config['shared_lock_path'],bootstrap['owner_identity'],bind(config['shared_lock_path']),mount,old,new)
                require(not bootstrap.get('restore_head_root'),'External head-root variant requires separate review')
                source_after=deepcopy(source); source_after['database']['device']=new; source_after['owner_identity']['device']=new
                boundary=control['publication_cursor']; require(boundary['sha256']==digest({k:v for k,v in boundary.items() if k!='sha256'}),'Cursor digest changed')
                require(gate['publication_boundary']==boundary,'Completed gate and native cursor differ')
                renewed=deepcopy(boundary); destinations=[]
                for i,item in enumerate(boundary['databases']):
                    path=Path(item['path']); require(path.parent==out,'Destination outside output')
                    actual=publication.binding(path)
                    renewed['databases'][i]=renew(path,item,actual,mount,old,new)
                    backup(path,'destination-%03d.before.sqlite3'%i)
                    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as target:
                        row=publication._validate(target)
                    require(row['owner_path']==config['shared_lock_path'] and row['owner_device']==old
                            and row['owner_inode']==os.fstat(fd).st_ino,'Destination owner differs')
                    destinations.append({'path':str(path),'before':row,'binding':actual})
                read_bytes=0
                for i,item in enumerate(boundary['exports']):
                    path=Path(item['path']); actual=publication._file_identity(path)
                    renew(path,{k:v for k,v in item.items() if k!='sha256'},actual,mount,old,new)
                    require(sha(path)==item['sha256'] and publication._file_identity(path)==actual,'Export bytes changed')
                    renewed['exports'][i]={**actual,'sha256':item['sha256']}; read_bytes+=actual['size']
                renewed['sha256']=digest({k:v for k,v in renewed.items() if k!='sha256'})
                new_control={**control,'publication_cursor':renewed}
                after={**state,'bootstrap_json':json.dumps(new_boot,sort_keys=True),'control_json':json.dumps(new_control,sort_keys=True)}
                kept=protected(conn)
            head_path=policy/'authority.json'; head=json.loads(head_path.read_text()); new_head=deepcopy(head)
            require(head['pending'] is False,'Pending policy transaction')
            for key,path in [('database_identity',policy/'authority.sqlite3'),('owner_identity',Path(config['shared_lock_path']))]:
                actual=bind(path); renew(path,dict(zip(('device','inode'),head[key])),actual,mount,old,new)
                new_head[key]=[actual['device'],actual['inode']]
            for path,expected in head['immutable_controls'].items():
                new_head['immutable_controls'][path]=renew(path,expected,publication._file_identity(path),mount,old,new)
            for name,expected in head['legacy_boundaries'].items():
                path=policy/name;s=path.stat();keys=('device','inode','mtime_ns','ctime_ns')
                actual=dict(zip(keys,[s.st_dev,s.st_ino,s.st_mtime_ns,s.st_ctime_ns]))
                renew(path,dict(zip(keys,expected)),actual,mount,old,new)
                new_head['legacy_boundaries'][name]=list(actual.values())
            with closing(sqlite3.connect((policy/'authority.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=1)) as c:
                c.row_factory=sqlite3.Row; row=dict(c.execute('SELECT * FROM authority WHERE id=1').fetchone())
                require(_schema_hash(c)==head['schema_sha256'] and all(row[k]==head[k] for k in ('epoch','revision','transaction_id')),'Policy transaction differs')
            proof_paths=[Path(config['state_dir'])/'runs'/RUN/n for n in ('request.json','acceptance.json','worker_result.json')]
            proof_paths+=[Path(control['previous_publication_control_dir'])/n for n in ('outcome.json','publication_result.json','publication_request.json')]
            proof_paths+=[Path(p) for p in head['immutable_controls']]
            proof_paths += [status/'failure_condition.json',out/'.jobagg-publication-state.json',policy/'authority.sqlite3']
            hashes={str(p):sha(p) for p in proof_paths}
            require(hashes[str(Path(control['previous_publication_control_dir'])/'outcome.json')]==control['previous_outcome_sha256'],'Predecessor outcome changed')
            require(hashes[str(Path(config['state_dir'])/'runs'/RUN/'worker_result.json')]==control['accepted_worker_result_sha256'],'Accepted proof changed')
            run=runner(); cfg,_=run.load_config(LIVE); contract=run.runtime_publication_contract(cfg)
            run.former_processes_absent(json.loads((Path(control['previous_publication_control_dir'])/'state.json').read_text()))
            require(contract['sha256']=='26d8ebf259393db015e7aeda5cadfc0289ff86d170f7e420c9068a540f97e7f5','Runtime contract changed')
            plan={'schema':'jobagg-live-mount-renewal-1','prepared_at':datetime.now(timezone.utc).isoformat(),
                  'volume_uuid':volume,'old_device':old,'new_device':new,'config_sha256':CONFIG_SHA,'worker_seal':SEAL,
                  'script_sha256':sha(__file__),'contract':contract,'database':str(db),'owner':config['shared_lock_path'],
                  'fence':str(fence),'fence_value':token,'state_before':state,'state_after':after,'protected':kept,
                  'source_path':str(source_path),'source_before':source,'source_after':source_after,
                  'policy_path':str(head_path),'policy_before':head,'policy_after':new_head,
                  'destinations':destinations,'preserved_hashes':hashes,'export_bytes_verified':read_bytes,
                  'scope':'Device bindings only; retain all debt, holds, charges, tokens, revisions and accepted proofs'}
            plan['sha256']=digest(plan);save(D/'plan.json',plan)
            save(D/'source-head.before.json',source_path.read_bytes());save(D/'policy-head.before.json',head_path.read_bytes())
            save(D/'preview.json',{'plan_sha256':plan['sha256'],'destinations':len(destinations),'exports':len(boundary['exports']),
                                  'export_bytes_verified':read_bytes,'production_data_changed':False,'maintenance_fence_held':True})
            print(json.dumps(json.loads((D/'preview.json').read_text())))
        except BaseException:
            if not (D/'plan.json').exists() and fence.exists() and json.loads(fence.read_text())==token:
                fence.unlink();sync_parent(fence)
            raise


def execute(plan_sha):
    plan=json.loads((D/'plan.json').read_text())
    require(plan['sha256']==plan_sha==digest({k:v for k,v in plan.items() if k!='sha256'}),'Exact reviewed plan required')
    require(plan['script_sha256']==sha(__file__),'Repair implementation changed since preview')
    config=json.loads(LIVE.read_text()); mount,volume=pinned_volume(config)
    require(volume==plan['volume_uuid'] and mount.stat().st_dev==plan['new_device'],'Mount changed again')
    with owner(config) as fd:
        fence=Path(plan['fence'])
        def guard():
            check()
            heads.owner(fd,plan['owner'])
            require(json.loads(fence.read_text())==plan['fence_value'],'Maintenance ownership changed')
            require(sha(LIVE)==CONFIG_SHA and bind(plan['database'])=={'device':plan['new_device'],'inode':plan['source_before']['database']['inode']},'Live binding changed')
        guard()
        require(worker.implementation_hash()==SEAL,'Worker seal changed')
        for path,expected in plan['preserved_hashes'].items(): require(sha(path)==expected,'Protected file changed: '+path)
        with closing(sqlite3.connect(Path(plan['database']).as_uri()+'?mode=rw',uri=True,timeout=1)) as conn:
            require(authority.state(conn) in (plan['state_before'],plan['state_after']),'Native state changed outside handoff')
            require(protected(conn)==plan['protected'],'Debt or holds changed')
        for field in ('source','policy'):
            require(json.loads(Path(plan[field+'_path']).read_text()) in (plan[field+'_before'],plan[field+'_after']),'Head changed outside handoff')
        if not (D/'intent.json').exists(): save(D/'intent.json',{'plan_sha256':plan_sha})
        require(json.loads((D/'intent.json').read_text())=={'plan_sha256':plan_sha},'Other intent exists')
        for item in plan['destinations']:
            guard();path=Path(item['path']); before=item['before']; after={**before,'owner_device':plan['new_device']}
            require(publication.binding(path)==item['binding'],'Destination changed before renewal')
            with closing(sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=1,cached_statements=0)) as c:
                require(publication._validate(c) in (before,after),'Destination state changed')
                c.create_function('jobagg_publication_control_allowed',0,lambda:guard() or 1)
                c.set_authorizer(restricted({publication.TABLE:{'owner_device'}},guard))
                with c:
                    c.execute('UPDATE '+publication.TABLE+' SET owner_device=? WHERE id=1 AND owner_device=?',(plan['new_device'],plan['old_device']))
                    require(publication._validate(c)==after,'Destination update differs')
            require(publication.binding(path)==item['binding'],'Destination revision changed')
        guard();atomic(Path(plan['policy_path']),plan['policy_after'])
        native=PolicyAuthority(SimpleNamespace(root=Path(plan['policy_path']).parent,owner=Path(plan['owner'])))
        require(native.revision()==plan['policy_before']['revision'],'Policy revision changed')
        with closing(sqlite3.connect(Path(plan['database']).as_uri()+'?mode=rw',uri=True,timeout=1,cached_statements=0)) as conn:
            conn.create_function('jobagg_runtime_control_allowed',0,lambda:guard() or 1)
            conn.set_authorizer(restricted({'jobagg_runtime_state':{'bootstrap_json','control_json'}},guard))
            with conn:
                require(authority.state(conn) in (plan['state_before'],plan['state_after']),'Runtime state changed')
                if authority.state(conn)==plan['state_before']:
                    conn.execute('UPDATE jobagg_runtime_state SET bootstrap_json=?,control_json=? WHERE id=1',
                                 (plan['state_after']['bootstrap_json'],plan['state_after']['control_json']))
                guard();atomic(Path(plan['source_path']),plan['source_after'])
                require(authority.validate(conn)==plan['state_after'],'Native readback differs')
                require(protected(conn)==plan['protected'],'Protected native data changed')
        run=runner();cfg,_=run.load_config(LIVE)
        require(run.runtime_publication_contract(cfg)==plan['contract'],'Runtime contract changed')
        require(run.runtime_active(cfg)==plan['state_after'],'Native admission preflight failed')
        boundary=json.loads(plan['state_after']['control_json'])['publication_cursor']
        require(publication.verify_boundary(boundary,expected_scope=run.runtime_publication_scope(cfg)),'Publication boundary failed')
        for path,expected in plan['preserved_hashes'].items(): require(sha(path)==expected,'Protected file changed after repair: '+path)
        require(worker.implementation_hash()==SEAL,'Worker seal changed')
        guard()
        if not (D/'activation-verified.json').exists():
            save(D/'activation-verified.json',{'completed_at':datetime.now(timezone.utc).isoformat(),'plan_sha256':plan_sha,
                 'config_sha256':CONFIG_SHA,'worker_seal':SEAL,'revision':plan['state_after']['revision'],
                 'run_id':RUN,'phase':'accepted','policy_revision':native.revision(),'protected_unchanged':True,
                 'publication_boundary_verified':True,'native_admission_verified':True,'fresh_fetch_verified':False})
        fence.unlink();sync_parent(fence)
        print((D/'activation-verified.json').read_text())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare',action='store_true');mode.add_argument('--execute-plan-sha256')
    parser.add_argument('--max-seconds',type=float,default=600)
    args=parser.parse_args()
    require(0 < args.max_seconds <= 900, 'Bounded repair duration required')
    DEADLINE=time.monotonic()+args.max_seconds
    if args.prepare: prepare()
    else: execute(args.execute_plan_sha256)
