"""D089 closed selective-delivery models; no server-side signing or plaintext."""

from typing import Annotated, Literal
from pydantic import Field
from .activations import (
    B64,
    Closed,
    EpochDelivery,
    RegistryEntry,
    RevocationTransition,
    RotationPlan,
)
from .commitments import Counter, Digest, Identifier


class DeviceDeliveryProof(Closed):
    format: Literal["atlasvault-device-delivery-proof"]
    version: Literal[2]
    activation_id: Digest
    plan: RotationPlan
    revocation: RevocationTransition
    registry: Annotated[list[RegistryEntry], Field(min_length=1, max_length=256)]
    recipient_device_id: Identifier
    recipient_agreement_sha256: Digest
    wrapper_sha256: Digest
    registry_generation: Counter
    issuer_device_id: Identifier
    rotation_signer_device_id: Identifier
    hpke_suite: Literal["0x0020/0x0001/0x0002"]
    hpke_version: Literal[2]
    signature_algorithm: Literal["Ed25519"]
    signature_version: Literal[1]
    root: Digest
    signature_b64: B64


class DeviceDeliveryPacket(Closed):
    proof: DeviceDeliveryProof
    wrapper: EpochDelivery


class ActivationReceipt(Closed):
    format: Literal['atlasvault-activation-receipt'] = 'atlasvault-activation-receipt'
    version: Literal[2] = 2
    status: Literal['ACTIVATION_ACCEPTED'] = 'ACTIVATION_ACCEPTED'
    transition_id: Digest
    key_epoch: Counter
