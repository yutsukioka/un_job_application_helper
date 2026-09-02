"""C25: hostile registry delivery and fresh, target-bound removal."""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vaultsync.revocation import (
    RevocationError, RevocationRegistry, RemovalController, registry_root,
    validate_rotation_plan, verify_transition,
)

VECTOR = Path(__file__).resolve().parents[3] / 'contracts/sync/test_vectors/atlasvault_revocation_v1.json'
V = json.loads(VECTOR.read_text())
A, B = [e['device_id'] for e in V['registry']]
KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


def test_native_removal_prompts_are_explicit_and_fail_closed():
    root = VECTOR.parents[3]
    android = (root / 'apps/atlas_flutter/android/app/src/main/kotlin/com/yutsukioka/jobagg/atlas/AtlasVaultAndroidStorage.kt').read_text()
    windows = (root / 'apps/atlas_flutter/windows/runner/atlas_vault_windows_storage.cpp').read_text()
    for source in (android, windows):
        assert 'authorizeDeviceRemoval' in source
        assert 'Authorize AtlasVault device removal' in source
    assert 'Activity.RESULT_OK' in android
    assert 'UserConsentVerificationResult::Verified' in windows


def store(path):
    return RevocationRegistry(path, bytes([7]) * 32, 'account-c25', 'vault-c25', 3,
                              V['registry'], 'ab' * 32)


def test_shared_signed_registry_and_epoch_contract(tmp_path):
    assert registry_root(V['registry']) == V['transition']['prior_registry_root']
    assert registry_root(V['revoked_registry']) == V['transition']['resulting_registry_root']
    verify_transition(V['transition'], V['registry'])
    validate_rotation_plan(V['rotation_plan'], V['transition'], V['revoked_registry'], 'ab' * 32)
    for name in ('A', 'B'):
        client = store(tmp_path / name)
        client.initialize()
        assert client.commit(V['transition']) is True
        assert client.commit(V['transition']) is False
        restarted = store(tmp_path / name)
        assert restarted.snapshot()['status'] == 'REVOCATION_PENDING'
        assert restarted.snapshot()['registry'][1]['state'] == 'REVOKED'
        with pytest.raises(RevocationError):
            restarted.prepare(A, B)


@pytest.mark.parametrize('field', list(V['transition']))
def test_every_signed_field_tamper_rejected(tmp_path, field):
    client = store(tmp_path / 'registry')
    client.initialize()
    altered = copy.deepcopy(V['transition'])
    altered[field] = altered[field] + 1 if type(altered[field]) is int else 'substitution'
    before = client.snapshot()
    with pytest.raises(RevocationError):
        client.commit(altered)
    assert client.snapshot() == before


@pytest.mark.parametrize('field', list(V['rotation_plan']))
def test_epoch_context_and_recipient_substitution_rejected(field):
    altered = copy.deepcopy(V['rotation_plan'])
    altered[field] = altered[field] + 1 if type(altered[field]) is int else 'substitution'
    with pytest.raises(RevocationError):
        validate_rotation_plan(altered, V['transition'], V['revoked_registry'], 'ab' * 32)


def test_no_recovery_stranding_and_no_unauthorized_signer(tmp_path):
    only = RevocationRegistry(tmp_path / 'solo', bytes([7]) * 32, 'account-c25', 'vault-c25', 3,
                              V['registry'][:1], 'ab' * 32)
    only.initialize()
    for target, signer in ((A, A), (A, B), (B, A)):
        with pytest.raises(RevocationError):
            only.prepare(target, signer)


@pytest.mark.parametrize('result', [False, None, 'true', 1, {}, RuntimeError('private sentinel')])
def test_authorization_failures_never_persist(tmp_path, result):
    async def run():
        client = store(tmp_path / 'registry')
        client.initialize()
        async def authorize():
            if isinstance(result, Exception):
                raise result
            return result
        async def sign(message):
            pytest.fail('signing reached without authorization')
        controller = RemovalController(client, A, authorize, sign)
        controller.select(B)
        before = (tmp_path / 'registry').read_bytes()
        with pytest.raises(RevocationError, match='ATLAS_REMOVAL_AUTHORIZATION') as error:
            await controller.remove(B)
        assert 'private sentinel' not in str(error.value)
        assert (tmp_path / 'registry').read_bytes() == before
    asyncio.run(run())


@pytest.mark.parametrize('attack', ['target', 'registry', 'cancel', 'timeout', 'wrong-confirmation', 'fork'])
def test_prompt_binding_and_recovery_fencing(tmp_path, attack):
    async def run():
        client = store(tmp_path / 'registry')
        client.initialize()
        ticks = [0.0]
        async def authorize():
            if attack == 'target':
                controller.select(A)
            elif attack == 'registry':
                client.commit(V['transition'])
            elif attack == 'cancel':
                controller.cancel()
            elif attack == 'timeout':
                ticks[0] = 61
            return True
        async def sign(message):
            pytest.fail('changed authorization context reached signing')
        controller = RemovalController(client, A, authorize, sign, clock=lambda: ticks[0])
        controller.select(B)
        if attack == 'fork':
            client.fence()
        with pytest.raises(RevocationError):
            await controller.remove(A if attack == 'wrong-confirmation' else B)
        if attack != 'registry':
            assert client.snapshot()['sequence'] == 0
    asyncio.run(run())


def test_fresh_prompt_per_attempt_and_exact_signed_output(tmp_path):
    async def run():
        client = store(tmp_path / 'registry')
        client.initialize()
        calls = []
        async def authorize():
            calls.append(1)
            return len(calls) == 2
        async def sign(message):
            return KEY.sign(message)
        controller = RemovalController(client, A, authorize, sign)
        controller.select(B)
        with pytest.raises(RevocationError):
            await controller.remove(B)
        assert await controller.remove(B) == V['transition']
        assert len(calls) == 2
        assert client.snapshot()['transition'] == V['transition']
    asyncio.run(run())


def test_independent_devices_survive_process_kill(tmp_path):
    # Each process owns separate encrypted registry state, then is killed after durable commit.
    code = '''
import json, pathlib, sys, time
from vaultsync.revocation import RevocationRegistry
v=json.loads(pathlib.Path(sys.argv[2]).read_text())
s=RevocationRegistry(pathlib.Path(sys.argv[1]),bytes([7])*32,'account-c25','vault-c25',3,v['registry'],'ab'*32)
s.initialize(); s.commit(v['transition'])
print('DURABLE',flush=True)
time.sleep(60)
'''
    for device in ('A', 'B'):
        path = tmp_path / device
        env = dict(os.environ, PYTHONPATH=str(VECTOR.parents[3] / 'packages/vaultsync'))
        child = subprocess.Popen([sys.executable, '-c', code, str(path), str(VECTOR)],
                                 env=env, stdout=subprocess.PIPE, text=True)
        try:
            assert child.stdout.readline().strip() == 'DURABLE'
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=10)
            assert store(path).snapshot()['registry'] == V['revoked_registry']
            assert store(path).commit(V['transition']) is False
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
