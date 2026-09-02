import base64
import copy
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from vaultsync.authenticated_state_view import (
    AuthenticatedHistory,
    StateViewError,
    registry_root,
    sign_state_view,
)

ROOT = Path(__file__).resolve().parents[3]
V = json.loads(
    (
        ROOT / "contracts/sync/test_vectors/atlasvault_authenticated_state_view_vectors_v2.json"
    ).read_text()
)
P = V["packages"]
PUBLIC = base64.b64decode(V["signing_public_b64"])


def client(path):
    return AuthenticatedHistory(
        path,
        encryption_key=bytes(range(32)),
        account_id="account_c22",
        vault_id="vault_c22",
        collection_id="collection_c21",
        key_epoch=1,
        trusted_signer=PUBLIC,
    )


class MaliciousServer:
    def __init__(self, package):
        self.package = copy.deepcopy(package)

    def serve(self, target):
        p = self.package
        return target.observe(
            p["view"], p["registry"], p["collection"], base64.b64decode(p["opaque_b64"])
        )


def test_shared_roots_signatures_and_valid_transitions(tmp_path):
    c = client(tmp_path / "client")
    c.initialize()
    for name in ("one", "two", "three"):
        p = P[name]
        unsigned = {k: v for k, v in p["view"].items() if k not in ("root", "signature_b64")}
        assert (
            sign_state_view(unsigned, Ed25519PrivateKey.from_private_bytes(bytes(range(32))))
            == p["view"]
        )
        assert registry_root(p["registry"]) == p["view"]["registry_root"]
        assert registry_root(list(reversed(p["registry"]))) == registry_root(p["registry"])
        assert MaliciousServer(p).serve(c)
        assert not MaliciousServer(p).serve(c)
    assert client(tmp_path / "client").export_evidence() == [
        P[n]["view"] for n in ("one", "two", "three")
    ]


@pytest.mark.parametrize("fork", ["fork_two", "fork_state", "fork_three"])
def test_independent_clients_detect_equivocation_on_exchange(tmp_path, fork):
    a, b = client(tmp_path / "A" / "anchor"), client(tmp_path / "B" / "anchor")
    a.initialize()
    b.initialize()
    for c in (a, b):
        MaliciousServer(P["one"]).serve(c)
    MaliciousServer(P["two"]).serve(a)
    MaliciousServer(P["fork_two"] if fork == "fork_three" else P[fork]).serve(b)
    if fork == "fork_three":
        MaliciousServer(P[fork]).serve(b)
    a, b = client(tmp_path / "A" / "anchor"), client(tmp_path / "B" / "anchor")
    left, right = a.export_evidence(), b.export_evidence()
    assert left[1]["root"] != right[1]["root"]
    for c, evidence in ((a, right), (b, left)):
        with pytest.raises(StateViewError, match="^ATLAS_STATE_EQUIVOCATION$"):
            c.compare_evidence(evidence)
    with pytest.raises(StateViewError, match="^ATLAS_STATE_EQUIVOCATION$"):
        MaliciousServer(P["three"]).serve(client(tmp_path / "A" / "anchor"))


@pytest.mark.parametrize("attack", ["substituted", "added", "removed", "registry_rollback"])
def test_device_list_substitution_and_unsigned_changes(tmp_path, attack):
    a, b = client(tmp_path / "A"), client(tmp_path / "B")
    a.initialize()
    b.initialize()
    for c in (a, b):
        MaliciousServer(P["one"]).serve(c)
    MaliciousServer(P["two"]).serve(a)
    server = MaliciousServer(P["two"])
    if attack in ("removed", "registry_rollback"):
        server.package["registry"] = P["one"]["registry"]
    elif attack == "added":
        server.package["registry"] += [{"device_id": "f" * 64, "descriptor_sha256": "e" * 64}]
    else:
        server.package["registry"][0]["descriptor_sha256"] = "f" * 64
    with pytest.raises(StateViewError, match="^ATLAS_REGISTRY_SUBSTITUTION$"):
        server.serve(b)
    assert len(b.export_evidence()) == 1


@pytest.mark.parametrize("name", ["other_account", "other_vault", "other_epoch"])
def test_valid_signature_wrong_context_rejected(tmp_path, name):
    c = client(tmp_path / "c")
    c.initialize()
    with pytest.raises(StateViewError, match="^ATLAS_STATE_VIEW_REJECTED$"):
        MaliciousServer(P[name]).serve(c)


@pytest.mark.parametrize("field", list(P["one"]["view"]))
def test_each_authenticated_field_tampered(tmp_path, field):
    c = client(tmp_path / "c")
    c.initialize()
    server = MaliciousServer(P["one"])
    value = server.package["view"][field]
    server.package["view"][field] = value + 1 if type(value) is int else ("x" + value[1:])
    with pytest.raises(StateViewError):
        server.serve(c)
    assert c.export_evidence() == []


def test_rollback_chain_tamper_and_no_freshness_claim(tmp_path):
    a, b = client(tmp_path / "A"), client(tmp_path / "B")
    a.initialize()
    b.initialize()
    with pytest.raises(StateViewError, match="ATLAS_CHECKPOINT_REQUIRED"):
        a.compare_evidence([])
    for c in (a, b):
        MaliciousServer(P["one"]).serve(c)
    MaliciousServer(P["two"]).serve(a)
    assert a.compare_evidence(b.export_evidence()) == 1  # only consistent prefix, NOT freshness
    with pytest.raises(StateViewError, match="ATLAS_ROLLBACK_REJECTED"):
        MaliciousServer(P["one"]).serve(a)
    forged = copy.deepcopy(a.export_evidence())
    forged[1]["previous_root"] = "e" * 64
    with pytest.raises(StateViewError, match="ATLAS_STATE_VIEW_REJECTED"):
        b.compare_evidence(forged)
    assert len(b.export_evidence()) == 1
    with pytest.raises(StateViewError):
        client(tmp_path / "missing").export_evidence()
    (tmp_path / "B").write_bytes(b"corrupt")
    with pytest.raises(StateViewError):
        client(tmp_path / "B").export_evidence()
