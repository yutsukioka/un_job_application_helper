"""Closed, ciphertext-safe D087 wire models. No raw key or auth material."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .commitments import Counter, Digest, Identifier

B32 = Annotated[str, Field(min_length=44, max_length=44)]
B64 = Annotated[str, Field(min_length=88, max_length=88)]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RegistryEntry(Closed):
    device_id: Identifier
    signing_public_b64: B32
    agreement_public_b64: B32
    state: Literal["ACTIVE", "REVOKED"]


class RevocationTransition(Closed):
    format: Literal["atlasvault-device-revocation"]
    version: Literal[1]
    account_id: Identifier
    vault_id: Identifier
    target_device_id: Identifier
    initiator_device_id: Identifier
    prior_registry_root: Digest
    resulting_registry_root: Digest
    key_epoch: Counter
    sequence: Counter
    authorization_category: Literal["DEVICE_PRESENCE"]
    root: Digest
    signature_b64: B64


class RotationPlan(Closed):
    format: Literal["atlasvault-rotation-plan"]
    version: Literal[1]
    account_id: Identifier
    vault_id: Identifier
    previous_epoch: Counter
    new_epoch: Counter
    prior_registry_root: Digest
    resulting_registry_root: Digest
    state_root: Digest
    initiator_device_id: Identifier
    revocation_root: Digest
    recipients: Annotated[list[Identifier], Field(min_length=1, max_length=256)]


class EpochDelivery(Closed):
    device_id: Identifier
    key_epoch: Counter
    encapsulated_key_b64: B32
    ciphertext_b64: Annotated[str, Field(min_length=64, max_length=64)]


class EpochRotationProof(Closed):
    format: Literal["atlasvault-epoch-rotation"]
    version: Literal[1]
    plan: RotationPlan
    revocation: RevocationTransition
    registry: Annotated[list[RegistryEntry], Field(min_length=1, max_length=256)]
    rotation_signer_device_id: Identifier
    deliveries: Annotated[list[EpochDelivery], Field(min_length=1, max_length=256)]
    root: Digest
    signature_b64: B64


class ActivationRecord(Closed):
    format: Literal["atlasvault-activation-record"]
    version: Literal[1]
    status: Literal["ACTIVATION_ACCEPTED"]
    transition_id: Digest
    proof: EpochRotationProof
