from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from vaultsync.key_delivery import (
    PairingBootstrap,
    PairingKeyDeliveryError,
    SignedPairingAcknowledgement,
    SignedPairingKeyRequest,
    SignedVaultKeyDelivery,
)
from vaultsync.pairing import (
    PairingError,
    SignedPairingAcceptance,
    SignedPairingOffer,
)
from vaultsync.protected_state_bounds import (
    require_staged_pairing_artifact_byte_counts,
)


PAIRING_ARTIFACT_FORMAT = "atlasvault-pairing-artifact"
PAIRING_ARTIFACT_VERSION = 1
PAIRING_PROOF_BYTES = 32


class PairingArtifactError(ValueError):
    """Raised when a manual pairing artifact is invalid."""


def _invalid_artifact() -> PairingArtifactError:
    return PairingArtifactError("pairing artifact is invalid")


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid_artifact()
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise _invalid_artifact()


def _integer(value: Any) -> int:
    if type(value) is not int:
        raise _invalid_artifact()
    return value


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_base64(value: Any, length: int) -> bytes:
    if not isinstance(value, str):
        raise _invalid_artifact()
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise _invalid_artifact() from exc
    if len(result) != length or _base64(result) != value:
        raise _invalid_artifact()
    return result


class PairingArtifactKind(str, Enum):
    offer = "offer"
    acceptance = "acceptance"
    delivery = "delivery"
    acknowledgement = "acknowledgement"


@dataclass(frozen=True)
class PairingArtifact:
    kind: PairingArtifactKind
    payload: Mapping[str, Any]
    format: str = PAIRING_ARTIFACT_FORMAT
    version: int = PAIRING_ARTIFACT_VERSION

    def __post_init__(self) -> None:
        try:
            if (
                self.format != PAIRING_ARTIFACT_FORMAT
                or _integer(self.version) != PAIRING_ARTIFACT_VERSION
                or not isinstance(self.kind, PairingArtifactKind)
            ):
                raise _invalid_artifact()
            payload = dict(_mapping(self.payload))
            self._validate_payload(self.kind, payload)
            object.__setattr__(self, "payload", _freeze_json(payload))
        except PairingArtifactError:
            raise
        except (PairingError, PairingKeyDeliveryError, Exception) as exc:
            raise _invalid_artifact() from exc

    @staticmethod
    def _validate_payload(kind: PairingArtifactKind, payload: Mapping[str, Any]) -> None:
        if kind is PairingArtifactKind.offer:
            _exact_keys(payload, {"signed_offer"})
            SignedPairingOffer.from_dict(_mapping(payload.get("signed_offer")))
            return
        if kind is PairingArtifactKind.acceptance:
            _exact_keys(
                payload,
                {"signed_acceptance", "signed_key_request", "invitee_proof"},
            )
            SignedPairingAcceptance.from_dict(_mapping(payload.get("signed_acceptance")))
            SignedPairingKeyRequest.from_dict(_mapping(payload.get("signed_key_request")))
            _decode_base64(payload.get("invitee_proof"), PAIRING_PROOF_BYTES)
            return
        if kind is PairingArtifactKind.delivery:
            _exact_keys(payload, {"signed_delivery", "bootstrap", "inviter_proof"})
            SignedVaultKeyDelivery.from_dict(_mapping(payload.get("signed_delivery")))
            PairingBootstrap.from_dict(_mapping(payload.get("bootstrap")))
            _decode_base64(payload.get("inviter_proof"), PAIRING_PROOF_BYTES)
            return
        _exact_keys(payload, {"signed_acknowledgement"})
        SignedPairingAcknowledgement.from_dict(_mapping(payload.get("signed_acknowledgement")))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PairingArtifact:
        try:
            obj = _mapping(data)
            _exact_keys(obj, {"format", "version", "kind", "payload"})
            kind_value = obj.get("kind")
            if not isinstance(kind_value, str):
                raise _invalid_artifact()
            return cls(
                format=obj.get("format"),
                version=_integer(obj.get("version")),
                kind=PairingArtifactKind(kind_value),
                payload=_mapping(obj.get("payload")),
            )
        except PairingArtifactError:
            raise
        except Exception as exc:
            raise _invalid_artifact() from exc

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> PairingArtifact:
        try:
            if not isinstance(data, bytes) or not data:
                raise _invalid_artifact()
            require_staged_pairing_artifact_byte_counts((len(data),))
            obj = _mapping(json.loads(data.decode("utf-8")))
            if not hmac.compare_digest(_canonical_bytes(obj), data):
                raise _invalid_artifact()
            return cls.from_dict(obj)
        except PairingArtifactError:
            raise
        except Exception as exc:
            raise _invalid_artifact() from exc

    @classmethod
    def offer(cls, signed_offer: SignedPairingOffer) -> PairingArtifact:
        return cls(
            kind=PairingArtifactKind.offer,
            payload={"signed_offer": signed_offer.to_dict()},
        )

    @classmethod
    def acceptance(
        cls,
        signed_acceptance: SignedPairingAcceptance,
        signed_key_request: SignedPairingKeyRequest,
        invitee_proof: bytes,
    ) -> PairingArtifact:
        return cls(
            kind=PairingArtifactKind.acceptance,
            payload={
                "signed_acceptance": signed_acceptance.to_dict(),
                "signed_key_request": signed_key_request.to_dict(),
                "invitee_proof": _base64(_decode_or_bytes(invitee_proof, PAIRING_PROOF_BYTES)),
            },
        )

    @classmethod
    def delivery(
        cls,
        signed_delivery: SignedVaultKeyDelivery,
        bootstrap: PairingBootstrap,
        inviter_proof: bytes,
    ) -> PairingArtifact:
        return cls(
            kind=PairingArtifactKind.delivery,
            payload={
                "signed_delivery": signed_delivery.to_dict(),
                "bootstrap": bootstrap.to_dict(),
                "inviter_proof": _base64(_decode_or_bytes(inviter_proof, PAIRING_PROOF_BYTES)),
            },
        )

    @classmethod
    def acknowledgement(
        cls,
        signed_acknowledgement: SignedPairingAcknowledgement,
    ) -> PairingArtifact:
        return cls(
            kind=PairingArtifactKind.acknowledgement,
            payload={"signed_acknowledgement": signed_acknowledgement.to_dict()},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "kind": self.kind.value,
            "payload": _thaw_json(self.payload),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def sha256_hex(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _decode_or_bytes(value: Any, length: int) -> bytes:
    if not isinstance(value, bytes) or len(value) != length:
        raise _invalid_artifact()
    return bytes(value)
