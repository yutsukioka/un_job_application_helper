import base64
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vaultsync.sync_queue import RollbackTracker, SignedStateCommitment

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads((ROOT / 'contracts/sync/test_vectors/atlasvault_state_commitment_vectors_v1.json').read_text())
STATES = VECTORS['states']
PUBLIC = base64.b64decode(VECTORS['signing_public_b64'])


def tracker(path, key=bytes(range(32))):
    return RollbackTracker(path, encryption_key=key, collection_id='collection_c21', trusted_signer=PUBLIC)


class HostileServer:
    def __init__(self, state, *, omit_bytes=False):
        self.commitment = copy.deepcopy(state['commitment'])
        self.body = base64.b64decode(state['opaque_b64'])
        if omit_bytes:
            self.body = self.body[:-1]

    def serve(self, client):
        return client.accept(self.commitment, self.body)


def test_shared_roots_and_signatures():
    for state in STATES:
        c = state['commitment']
        signed = SignedStateCommitment.sign(
            base64.b64decode(state['opaque_b64']), collection_id=c['collection_id'],
            sequence=c['sequence'], previous_root=c['previous_root'],
            signing_key=Ed25519PrivateKey.from_private_bytes(bytes(range(32))),
        )
        assert signed.to_dict() == c


@pytest.mark.parametrize('attack', ['replay', 'old_snapshot', 'omission', 'gap', 'non_chaining', 'same_sequence'])
def test_malicious_server_is_rejected_after_durable_observation(tmp_path, attack):
    path = tmp_path / 'anchor'
    client = tracker(path)
    client.initialize()
    HostileServer(STATES[0]).serve(client)
    if attack != 'gap':
        HostileServer(STATES[1]).serve(client)
    before = path.read_bytes()
    client = tracker(path)  # A fresh client must reload, not reset, its high-water mark.
    target = STATES[0] if attack in ('replay', 'old_snapshot') else STATES[2]
    server = HostileServer(target, omit_bytes=attack == 'omission')
    if attack in ('non_chaining', 'same_sequence'):
        server.commitment = SignedStateCommitment.sign(
            server.body, collection_id='collection_c21',
            sequence=3 if attack == 'non_chaining' else 2,
            previous_root='f' * 64,
            signing_key=Ed25519PrivateKey.from_private_bytes(bytes(range(32))),
        ).to_dict()
    with pytest.raises(ValueError):
        server.serve(client)
    assert path.read_bytes() == before
    assert client.checkpoint()['sequence'] == (1 if attack == 'gap' else 2)


def test_duplicate_is_idempotent_and_anchor_encrypted(tmp_path):
    path = tmp_path / 'anchor'
    client = tracker(path)
    client.initialize()
    for state in STATES:
        assert HostileServer(state).serve(client)
    before = path.read_bytes()
    assert not HostileServer(STATES[2]).serve(tracker(path))
    assert path.read_bytes() == before
    assert STATES[2]['commitment']['root'].encode() not in before
    assert b'collection_c21' not in before
    with pytest.raises(ValueError):
        client.initialize()


def test_missing_corrupt_wrong_key_or_scope_anchor_fails_closed(tmp_path):
    path = tmp_path / 'anchor'
    client = tracker(path)
    with pytest.raises(ValueError):
        HostileServer(STATES[0]).serve(client)
    client.initialize()
    for bad in (tracker(path, bytes([99]) * 32), RollbackTracker(path, encryption_key=bytes(range(32)), collection_id='other', trusted_signer=PUBLIC)):
        with pytest.raises(ValueError):
            HostileServer(STATES[0]).serve(bad)
    path.write_bytes(b'corrupt')
    with pytest.raises(ValueError):
        HostileServer(STATES[0]).serve(client)
    path.unlink()
    with pytest.raises(ValueError):
        client.checkpoint()


@pytest.mark.parametrize('field,value', [('sequence', True), ('sequence', 0), ('sequence', 9007199254740992), ('sequence', 1.0), ('previous_root', 'F'*64), ('signature_b64', 'AA=='), ('collection_id', 'other'), ('plaintext', 'forbidden')])
def test_malformed_or_wrong_scope_is_rejected_without_write(tmp_path, field, value):
    client = tracker(tmp_path / 'anchor')
    client.initialize()
    server = HostileServer(STATES[0])
    server.commitment[field] = value
    with pytest.raises(ValueError):
        server.serve(client)
    assert client.checkpoint()['sequence'] == 0


def test_wrong_signer_and_tampered_signature_rejected(tmp_path):
    client = tracker(tmp_path / 'anchor')
    client.initialize()
    server = HostileServer(STATES[0])
    server.commitment['signature_b64'] = base64.b64encode(bytes(64)).decode()
    with pytest.raises(ValueError):
        server.serve(client)
    other = RollbackTracker(tmp_path / 'other', encryption_key=bytes(range(32)), collection_id='collection_c21', trusted_signer=bytes(32))
    other.initialize()
    with pytest.raises(ValueError):
        HostileServer(STATES[0]).serve(other)


def test_swift_randomized_signatures_share_commitment_identity(tmp_path):
    client = tracker(tmp_path / 'anchor')
    client.initialize()
    for state in STATES:
        server = HostileServer(state)
        server.commitment['signature_b64'] = state['swift_signature_b64']
        assert server.serve(client)
        assert not HostileServer(state).serve(client)


def test_process_kill_after_observation_preserves_root(tmp_path):
    path = tmp_path / 'anchor'
    worker = '''
import base64, json, os, signal
from pathlib import Path
from vaultsync.sync_queue import RollbackTracker
v = json.loads(Path(sys.argv[2]).read_text())
c = RollbackTracker(Path(sys.argv[1]), encryption_key=bytes(range(32)), collection_id='collection_c21', trusted_signer=base64.b64decode(v['signing_public_b64']))
c.initialize()
for state in v['states'][:2]:
    c.accept(state['commitment'], base64.b64decode(state['opaque_b64']))
os.kill(os.getpid(), signal.SIGKILL)
'''
    bootstrap = 'import sys; sys.path[:] = ' + repr(sys.path) + '\n' + worker
    result = subprocess.run([sys.executable, '-S', '-c', bootstrap, str(path), str(ROOT / 'contracts/sync/test_vectors/atlasvault_state_commitment_vectors_v1.json')], timeout=20, capture_output=True)
    assert result.returncode == -9
    restarted = tracker(path)
    assert restarted.checkpoint()['sequence'] == 2
    with pytest.raises(ValueError):
        HostileServer(STATES[0]).serve(restarted)


def test_interrupted_anchor_replace_keeps_prior_root(tmp_path, monkeypatch):
    import vaultsync.sync_queue as module
    path = tmp_path / 'anchor'
    client = tracker(path)
    client.initialize()
    HostileServer(STATES[0]).serve(client)
    before = path.read_bytes()
    original = module._EncryptedQueueFile.write

    def interrupted(store, value):
        def fail():
            raise OSError('injected before replace')
        return original(store, value, before_replace=fail)

    monkeypatch.setattr(module._EncryptedQueueFile, 'write', interrupted)
    with pytest.raises(ValueError):
        HostileServer(STATES[1]).serve(client)
    assert path.read_bytes() == before
    assert tracker(path).checkpoint()['sequence'] == 1
