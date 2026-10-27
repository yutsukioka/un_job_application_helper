import pytest
from jobagg.pipelines.worker_policy import SharedPolicy
from test_policy_authority import native, activate  # noqa: F401

# ruff: noqa: F811


def test_explicit_existing_native_location_preserves_epoch_charges_and_no_legacy_fallback(native):
    policy, owner = native
    authority = activate(policy, owner)
    selected = SharedPolicy(
        policy.owner, policy.source_ids, native_location={"root": str(policy.root), "epoch": "test"}
    )
    assert selected.inspect()["authority"] == "indexed_sql"
    assert selected.native_authority().revision() == authority.revision() == 0
    original = authority.head.read_bytes()
    with pytest.raises(ValueError, match="revision_or_restore"):
        SharedPolicy(
            policy.owner,
            policy.source_ids,
            native_location={"root": str(policy.root), "epoch": "wrong"},
        ).inspect()
    assert authority.head.read_bytes() == original
    authority.head.unlink()
    with pytest.raises(ValueError, match="head_missing"):
        selected.native_authority()
    assert not selected.marker.exists()


@pytest.mark.parametrize("change", ["missing", "alias", "bootstrap", "owner"])
def test_native_policy_location_cannot_initialize_or_adopt_another_owner(native, change, tmp_path):
    policy, owner = native
    authority = activate(policy, owner)
    root = policy.root
    bootstrap = None
    owner_path = policy.owner
    if change == "missing":
        root = tmp_path / "missing"
    if change == "alias":
        alias = tmp_path / "alias"
        alias.symlink_to(root)
        root = alias
    if change == "bootstrap":
        bootstrap = policy.bootstrap
    if change == "owner":
        owner_path = tmp_path / "another-owner"
        owner_path.write_text("")
    with pytest.raises(ValueError):
        SharedPolicy(
            owner_path,
            policy.source_ids,
            bootstrap,
            native_location={"root": str(root), "epoch": "test"},
        ).inspect()
    assert authority.revision() == 0
