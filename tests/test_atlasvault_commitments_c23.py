import base64
import copy
import hashlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'services/atlasvault-api'),str(ROOT/'packages/vaultsync')]
from atlasvault_api.app import AtlasVaultBackend, create_app
from atlasvault_api.commitments import CommitmentConflict, CommitmentLog
from test_atlasvault_backend_c13 import ACCOUNT_A, _bootstrap, _identities, _session
from vaultsync.authenticated_state_view import EMPTY_REGISTRY, _message, _root, registry_root

DEVICE=_identities()[0]
DESCRIPTOR=DEVICE.sign_descriptor().to_dict()['descriptor']
PUBLIC=base64.b64decode(DESCRIPTOR['signing_public_key'])
REGISTRY=registry_root([{'device_id':hashlib.sha256(DEVICE.device_id.encode()).hexdigest(),'descriptor_sha256':hashlib.sha256(json.dumps(DESCRIPTOR,sort_keys=True,separators=(',',':')).encode()).hexdigest()}])
VAULT='vault_c23'

def signed(sequence,previous=None,**changes):
    v={'format':'atlasvault-authenticated-state-view','version':2,'account_id':ACCOUNT_A,'vault_id':VAULT,'sequence':sequence,'previous_root':previous['root'] if previous else '0'*64,'collection_root':hashlib.sha256(f'ciphertext-state-{sequence}'.encode()).hexdigest(),'registry_root':REGISTRY,'previous_registry_root':previous['registry_root'] if previous else EMPTY_REGISTRY,'key_epoch':DESCRIPTOR['key_epoch']}
    v.update(changes);r=_root(v);return dict(v,root=r,signature_b64=base64.b64encode(DEVICE.sign(_message(r))).decode())

def append(log,v):return log.append(ACCOUNT_A,VAULT,v,REGISTRY,PUBLIC,DESCRIPTOR['key_epoch'])

def test_backend_atomic_conflicting_children_exact_retry_and_reopen(tmp_path):
    path=tmp_path/'commitments.sqlite';a=CommitmentLog(path);b=CommitmentLog(path)
    one=signed(1);assert append(a,one)
    barrier=threading.Barrier(2)
    def race(log,child):
        barrier.wait()
        try:return append(log,child)
        except CommitmentConflict:return False
    left=signed(2,one);right=signed(2,one,collection_root='f'*64)
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(race,a,left),pool.submit(race,b,right)]
        assert sorted(f.result() for f in futures)==[False,True]
    stored=a.read(ACCOUNT_A,VAULT);assert len(stored)==2
    assert not append(b,stored[-1]);assert not append(b,one)
    assert CommitmentLog(path).read(ACCOUNT_A,VAULT)==stored

@pytest.mark.parametrize('attack',['regression','same_sequence','predecessor','registry','registry_parent','epoch','account','vault','identifier'])
def test_backend_rejects_hostile_append_without_altering_log(tmp_path,attack):
    log=CommitmentLog(tmp_path/'log.sqlite');one=signed(1);two=signed(2,one);append(log,one);append(log,two)
    v=signed(3,two)
    if attack=='regression':v=signed(1,collection_root='f'*64)
    elif attack=='same_sequence':v=signed(2,one,collection_root='f'*64)
    elif attack=='predecessor':v=signed(3,two,previous_root='f'*64)
    elif attack=='registry':v=signed(3,two,registry_root='f'*64)
    elif attack=='registry_parent':v=signed(3,two,previous_registry_root='f'*64)
    elif attack=='epoch':v=signed(3,two,key_epoch=DESCRIPTOR['key_epoch']+1)
    elif attack=='account':v=signed(3,two,account_id='other')
    elif attack=='vault':v=signed(3,two,vault_id='other')
    else:v=dict(two,signature_b64=base64.b64encode(b'\0'*64).decode())
    before=log.read(ACCOUNT_A,VAULT)
    with pytest.raises(CommitmentConflict):append(log,v)
    assert log.read(ACCOUNT_A,VAULT)==before

def test_real_auth_http_commitment_path_and_secret_free_errors(caplog):
    backend=AtlasVaultBackend();c=TestClient(create_app(backend));_bootstrap(c,DEVICE)
    token,_=_session(c,DEVICE);headers={'Authorization':f'Bearer {token}'};path=f'/v1/vaults/{VAULT}/commitments'
    one=signed(1)
    assert c.post(path,json=one).status_code==401
    assert c.post(path,json=one,headers=headers).json()['appended'] is True
    assert c.post(path,json=one,headers=headers).json()['appended'] is False
    bad=signed(2,one,registry_root='f'*64)
    r=c.post(path,json=bad,headers=headers);assert r.status_code==409;assert r.json()=={'detail':'Commitment conflict.'}
    malformed=dict(one,plaintext='forbidden-field-sentinel')
    r=c.post(path,json=malformed,headers=headers);assert r.status_code==422
    assert c.get(path,headers=headers).json()==[one]
    assert token not in caplog.text and 'forbidden-field-sentinel' not in caplog.text+r.text
    assert backend.telemetry.snapshot()
