import base64
import json
import multiprocessing
import time
from pathlib import Path

import pytest

from vaultsync.authenticated_state_view import StateViewError
from vaultsync.sync_recovery import GuardedSyncState

ROOT=Path(__file__).resolve().parents[3]
V=json.loads((ROOT/'contracts/sync/test_vectors/atlasvault_sync_recovery_vectors_v1.json').read_text())
P=V['packets']

def client(path):
    return GuardedSyncState(path,encryption_key=bytes(range(32)),account_id='account_c22',vault_id='vault_c22',collection_id='collection_c21',key_epoch=2,trusted_signer=base64.b64decode(V['signing_public_b64']))

class MaliciousServer:
    def __init__(self,packet): self.packet=packet
    def serve(self,c):
        p=self.packet
        return c.ingest(p['view'],p['registry'],p['collection'],base64.b64decode(p['opaque_b64']))

def ready(path):
    c=client(path);c.initialize()
    for n in ['one','two']: assert MaliciousServer(P[n]).serve(c)
    return c

@pytest.mark.parametrize('attack',V['attacks'],ids=lambda a:a['name'])
def test_hostile_delivery_fences_without_cursor_or_terminal_change(tmp_path,attack):
    a=ready(tmp_path/'A');b=ready(tmp_path/'B')
    before=b.checkpoint()
    with pytest.raises(StateViewError,match=attack['reason']): MaliciousServer(P[attack['packet']]).serve(b)
    b=client(tmp_path/'B')
    assert b.checkpoint()==before==a.checkpoint()
    assert b.recovery()['status']=='MANUAL_REQUIRED'
    assert b.recovery()['reason']==attack['reason']
    calls=[]
    with pytest.raises(StateViewError,match='ATLAS_RECOVERY_PENDING'): b.automatic_sync(lambda:calls.append(1))
    with pytest.raises(StateViewError,match='ATLAS_RECOVERY_PENDING'): MaliciousServer(P['three']).serve(b)
    assert not calls
    assert b.checkpoint()['records'][0]['tombstone'] is True
    text=json.dumps(b.recovery())
    for forbidden in ['ciphertext_b64','opaque_b64','passphrase','vault_key','access_token','nonce_b64']:
        assert forbidden not in text
    assert len(text)<160000

def test_manual_fork_preserves_both_branches_and_never_rewrites(tmp_path):
    a=ready(tmp_path/'A');b=client(tmp_path/'B');b.initialize()
    for n in ['one','fork_two']: MaliciousServer(P[n]).serve(b)
    left=a.export_evidence();right=b.export_evidence();before=a.checkpoint()
    with pytest.raises(StateViewError,match='ATLAS_STATE_EQUIVOCATION'): a.compare_evidence(right)
    evidence=a.evidence();assert evidence['local']==left and evidence['peer']==right
    ui=a.recovery()
    assert a.resolve('select_peer',ui['local'][-1]['root'],ui['peer'][-1]['root'])=='RECOVERY_PENDING'
    a=client(tmp_path/'A')
    assert a.evidence()==evidence
    assert a.recovery()['disposition']=='select_peer'
    assert a.checkpoint()==before
    with pytest.raises(StateViewError): a.automatic_sync(lambda:None)

def test_explicit_reject_known_replay_resumes_without_forgetting_evidence(tmp_path):
    c=ready(tmp_path/'C');before=c.checkpoint()
    with pytest.raises(StateViewError): MaliciousServer(P['one']).serve(c)
    ui=c.recovery()
    with pytest.raises(StateViewError): c.resolve('retain_accepted','f'*64,ui['peer'][-1]['root'])
    assert c.resolve('retain_accepted',ui['local'][-1]['root'],ui['peer'][-1]['root'])=='ACTIVE'
    assert c.checkpoint()==before and c.recovery()['disposition']=='retain_accepted'
    assert c.evidence()['peer']==[P['one']['view']]
    assert c.automatic_sync(lambda:7)==7
    assert MaliciousServer(P['three']).serve(c)
    before=c.checkpoint();assert not MaliciousServer(P['three']).serve(c);assert c.checkpoint()==before

def crash_after_alarm(path,signal):
    c=ready(Path(path))
    try: MaliciousServer(P['fork_two']).serve(c)
    except StateViewError: pass
    Path(signal).write_text('alarm durable')
    while True: time.sleep(60)

def test_alarm_survives_process_kill(tmp_path):
    signal=tmp_path/'ready';path=tmp_path/'child'
    child=multiprocessing.get_context('spawn').Process(target=crash_after_alarm,args=(str(path),str(signal)))
    child.start()
    try:
        deadline=time.monotonic()+20
        while not signal.exists() and time.monotonic()<deadline: time.sleep(.05)
        assert signal.exists()
        child.kill();child.join(10);assert child.exitcode is not None
        assert client(path).recovery()['status']=='MANUAL_REQUIRED'
        with pytest.raises(StateViewError): client(path).automatic_sync(lambda:None)
    finally:
        if child.is_alive(): child.kill();child.join(10)
