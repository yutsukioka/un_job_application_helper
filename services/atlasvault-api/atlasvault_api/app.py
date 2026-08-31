"""C13 account authentication and signed device-registry endpoints."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, Header, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from vaultsync.device_identity import (
    DeviceIdentityError,
    SignedDeviceDescriptor,
    verify_signed_device_descriptor,
)

ACCOUNT_SESSION_PROOF_DOMAIN = b"atlasvault-account-session-proof-v1:"
DEVICE_REGISTRY_TRANSITION_DOMAIN = b"atlasvault-device-registry-transition-v1:"
ACCOUNT_ID_PATTERN = r"^ava1-[0-9a-f]{64}$"
DEVICE_ID_PATTERN = r"^avd1-[0-9a-f]{64}$"
CHALLENGE_ID_PATTERN = r"^avc1-[0-9a-f]{32}$"
CHALLENGE_BYTES = 32
SIGNATURE_BYTES = 64
CHALLENGE_LIFETIME_SECONDS = 120
SESSION_LIFETIME_SECONDS = 900
_STRICT_MODEL = ConfigDict(extra="forbid", frozen=True)


class DeviceDescriptorModel(BaseModel):
    model_config = _STRICT_MODEL

    format: Literal["atlasvault-device-descriptor"]
    version: Literal[1]
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    signing_public_key: str
    agreement_public_key: str
    created_at: str
    key_epoch: int = Field(ge=1, le=(1 << 63) - 1)


class SignedDeviceDescriptorModel(BaseModel):
    model_config = _STRICT_MODEL

    format: Literal["atlasvault-signed-device-descriptor"]
    version: Literal[1]
    descriptor: DeviceDescriptorModel
    signature: str

    def verified(self) -> SignedDeviceDescriptor:
        try:
            signed = SignedDeviceDescriptor.from_dict(self.model_dump(mode="json"))
            verify_signed_device_descriptor(signed)
            return signed
        except DeviceIdentityError as exc:
            raise InvalidRegistryTransition from exc


class DeviceRegistryTransitionModel(BaseModel):
    model_config = _STRICT_MODEL

    format: Literal["atlasvault-device-registry-transition"]
    version: Literal[1]
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    revision: str
    parent_revision: str | None
    operation: Literal["add"]
    device: SignedDeviceDescriptorModel
    signer_device_id: str = Field(pattern=DEVICE_ID_PATTERN)


class SignedDeviceRegistryTransitionModel(BaseModel):
    model_config = _STRICT_MODEL

    format: Literal["atlasvault-signed-device-registry-transition"]
    version: Literal[1]
    transition: DeviceRegistryTransitionModel
    signature: str


class AuthenticationChallengeRequest(BaseModel):
    model_config = _STRICT_MODEL

    device_id: str = Field(pattern=DEVICE_ID_PATTERN)


class AuthenticationChallenge(BaseModel):
    model_config = _STRICT_MODEL

    challenge_id: str = Field(pattern=CHALLENGE_ID_PATTERN)
    challenge: str
    expires_in_seconds: Literal[CHALLENGE_LIFETIME_SECONDS]


class SessionProofRequest(BaseModel):
    model_config = _STRICT_MODEL

    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    challenge_id: str = Field(pattern=CHALLENGE_ID_PATTERN)
    signature: str


class SessionGrant(BaseModel):
    model_config = _STRICT_MODEL

    token_type: Literal["Bearer"]
    access_token: str
    expires_in_seconds: Literal[SESSION_LIFETIME_SECONDS]


class DeviceRegistryView(BaseModel):
    model_config = _STRICT_MODEL

    format: Literal["atlasvault-server-device-registry"]
    version: Literal[1]
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    revision: str
    devices: tuple[SignedDeviceDescriptorModel, ...]


class InvalidRegistryTransition(ValueError):
    """Raised when a signed device-registry transition is invalid."""


class AuthorizationFailed(ValueError):
    """Raised when device authentication or session authorization fails."""


class RevisionConflict(ValueError):
    """Raised when a registry transition does not extend current state."""


@dataclass(frozen=True)
class _Challenge:
    device_id: str
    challenge: bytes
    expires_at: float


@dataclass(frozen=True)
class _Session:
    account_id: str
    device_id: str
    expires_at: float


@dataclass
class _Account:
    revision: str
    devices: dict[str, SignedDeviceDescriptorModel]


class AtlasVaultBackend:
    """Ephemeral C13 account/session/device-registry state.

    Ciphertext objects are intentionally absent until C14. Session tokens are
    random bearer credentials and only their SHA-256 digests are retained.
    """

    def __init__(
        self,
        *,
        entropy: Callable[[int], bytes] = secrets.token_bytes,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._entropy = entropy
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._accounts: dict[str, _Account] = {}
        self._challenges: dict[tuple[str, str], _Challenge] = {}
        self._sessions: dict[bytes, _Session] = {}

    def bootstrap_account(
        self,
        account_id: str,
        signed_transition: SignedDeviceRegistryTransitionModel,
    ) -> DeviceRegistryView:
        transition = signed_transition.transition
        _require_account_match(account_id, transition.account_id)
        _require_uuid(transition.revision)
        if transition.parent_revision is not None:
            raise InvalidRegistryTransition
        descriptor = transition.device.verified()
        if not hmac.compare_digest(
            transition.signer_device_id,
            descriptor.descriptor.device_id,
        ):
            raise InvalidRegistryTransition
        _verify_transition_signature(
            signed_transition,
            descriptor.descriptor.signing_public_key,
        )
        with self._lock:
            if account_id in self._accounts:
                raise RevisionConflict
            self._accounts[account_id] = _Account(
                revision=transition.revision,
                devices={descriptor.descriptor.device_id: transition.device},
            )
            return self._registry_view(account_id)

    def issue_challenge(
        self,
        account_id: str,
        request: AuthenticationChallengeRequest,
    ) -> AuthenticationChallenge:
        with self._lock:
            account = self._accounts.get(account_id)
            if account is None or request.device_id not in account.devices:
                raise AuthorizationFailed
            challenge_id = f"avc1-{_entropy_bytes(self._entropy, 16).hex()}"
            key = (account_id, challenge_id)
            if key in self._challenges:
                raise AuthorizationFailed
            challenge = _entropy_bytes(self._entropy, CHALLENGE_BYTES)
            self._challenges[key] = _Challenge(
                device_id=request.device_id,
                challenge=challenge,
                expires_at=self._monotonic() + CHALLENGE_LIFETIME_SECONDS,
            )
            return AuthenticationChallenge(
                challenge_id=challenge_id,
                challenge=_encode_base64(challenge),
                expires_in_seconds=CHALLENGE_LIFETIME_SECONDS,
            )

    def create_session(
        self,
        account_id: str,
        request: SessionProofRequest,
    ) -> SessionGrant:
        with self._lock:
            challenge = self._challenges.pop(
                (account_id, request.challenge_id),
                None,
            )
            account = self._accounts.get(account_id)
            if (
                challenge is None
                or account is None
                or not hmac.compare_digest(challenge.device_id, request.device_id)
                or challenge.expires_at <= self._monotonic()
            ):
                raise AuthorizationFailed
            signed_descriptor = account.devices.get(request.device_id)
            if signed_descriptor is None:
                raise AuthorizationFailed
            descriptor = signed_descriptor.verified().descriptor
            proof_payload = {
                "format": "atlasvault-account-session-proof",
                "version": 1,
                "account_id": account_id,
                "device_id": request.device_id,
                "challenge_id": request.challenge_id,
                "challenge": _encode_base64(challenge.challenge),
            }
            try:
                Ed25519PublicKey.from_public_bytes(
                    descriptor.signing_public_key
                ).verify(
                    _decode_base64(request.signature, SIGNATURE_BYTES),
                    ACCOUNT_SESSION_PROOF_DOMAIN + _canonical_bytes(proof_payload),
                )
            except (InvalidSignature, TypeError, ValueError) as exc:
                raise AuthorizationFailed from exc
            token = _encode_token(_entropy_bytes(self._entropy, 32))
            digest = _token_digest(token)
            if digest in self._sessions:
                raise AuthorizationFailed
            self._sessions[digest] = _Session(
                account_id=account_id,
                device_id=request.device_id,
                expires_at=self._monotonic() + SESSION_LIFETIME_SECONDS,
            )
            return SessionGrant(
                token_type="Bearer",
                access_token=token,
                expires_in_seconds=SESSION_LIFETIME_SECONDS,
            )

    def list_devices(self, account_id: str, token: str) -> DeviceRegistryView:
        with self._lock:
            self._authorize(account_id, token)
            return self._registry_view(account_id)

    def add_device(
        self,
        account_id: str,
        token: str,
        signed_transition: SignedDeviceRegistryTransitionModel,
    ) -> DeviceRegistryView:
        transition = signed_transition.transition
        _require_account_match(account_id, transition.account_id)
        _require_uuid(transition.revision)
        if transition.parent_revision is None:
            raise InvalidRegistryTransition
        with self._lock:
            session = self._authorize(account_id, token)
            account = self._accounts[account_id]
            if not hmac.compare_digest(
                session.device_id,
                transition.signer_device_id,
            ):
                raise AuthorizationFailed
            if not hmac.compare_digest(account.revision, transition.parent_revision):
                raise RevisionConflict
            signer = account.devices.get(transition.signer_device_id)
            if signer is None:
                raise AuthorizationFailed
            signer_descriptor = signer.verified().descriptor
            _verify_transition_signature(
                signed_transition,
                signer_descriptor.signing_public_key,
            )
            target = transition.device.verified()
            if target.descriptor.device_id in account.devices:
                raise RevisionConflict
            account.devices[target.descriptor.device_id] = transition.device
            account.revision = transition.revision
            return self._registry_view(account_id)

    def _authorize(self, account_id: str, token: str) -> _Session:
        digest = _token_digest(token)
        session = self._sessions.get(digest)
        if session is None or session.expires_at <= self._monotonic():
            self._sessions.pop(digest, None)
            raise AuthorizationFailed
        if not hmac.compare_digest(session.account_id, account_id):
            raise AuthorizationFailed
        account = self._accounts.get(account_id)
        if account is None or session.device_id not in account.devices:
            raise AuthorizationFailed
        return session

    def _registry_view(self, account_id: str) -> DeviceRegistryView:
        account = self._accounts[account_id]
        return DeviceRegistryView(
            format="atlasvault-server-device-registry",
            version=1,
            account_id=account_id,
            revision=account.revision,
            devices=tuple(account.devices[key] for key in sorted(account.devices)),
        )


AccountPath = Annotated[str, Path(pattern=ACCOUNT_ID_PATTERN)]
AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]


def create_app(backend: AtlasVaultBackend | None = None) -> FastAPI:
    service = backend or AtlasVaultBackend()
    app = FastAPI(
        title="AtlasVault Zero-Knowledge Sync API",
        version="1.0.0",
        description=(
            "C13 account authentication and signed public-device registry. "
            "Ciphertext storage is reserved for C14."
        ),
    )
    app.state.backend = service

    @app.post(
        "/v1/accounts/{account_id}/devices/bootstrap",
        response_model=DeviceRegistryView,
        status_code=status.HTTP_201_CREATED,
        operation_id="bootstrapAccountDevice",
    )
    def bootstrap_account_device(
        account_id: AccountPath,
        request: SignedDeviceRegistryTransitionModel,
    ) -> DeviceRegistryView:
        try:
            return service.bootstrap_account(account_id, request)
        except InvalidRegistryTransition as exc:
            raise HTTPException(
                status_code=400, detail="Invalid registry transition."
            ) from exc
        except RevisionConflict as exc:
            raise HTTPException(
                status_code=409, detail="Account already exists."
            ) from exc

    @app.post(
        "/v1/accounts/{account_id}/auth/challenges",
        response_model=AuthenticationChallenge,
        status_code=status.HTTP_201_CREATED,
        operation_id="createAccountChallenge",
    )
    def create_account_challenge(
        account_id: AccountPath,
        request: AuthenticationChallengeRequest,
    ) -> AuthenticationChallenge:
        try:
            return service.issue_challenge(account_id, request)
        except AuthorizationFailed as exc:
            raise HTTPException(
                status_code=401, detail="Authentication failed."
            ) from exc

    @app.post(
        "/v1/accounts/{account_id}/sessions",
        response_model=SessionGrant,
        status_code=status.HTTP_201_CREATED,
        operation_id="createAccountSession",
    )
    def create_account_session(
        account_id: AccountPath,
        request: SessionProofRequest,
    ) -> SessionGrant:
        try:
            return service.create_session(account_id, request)
        except AuthorizationFailed as exc:
            raise HTTPException(
                status_code=401, detail="Authentication failed."
            ) from exc

    @app.get(
        "/v1/accounts/{account_id}/devices",
        response_model=DeviceRegistryView,
        operation_id="listAccountDevices",
    )
    def list_account_devices(
        account_id: AccountPath,
        authorization: AuthorizationHeader = None,
    ) -> DeviceRegistryView:
        try:
            return service.list_devices(account_id, _bearer_token(authorization))
        except AuthorizationFailed as exc:
            raise HTTPException(
                status_code=401, detail="Authorization failed."
            ) from exc

    @app.post(
        "/v1/accounts/{account_id}/devices",
        response_model=DeviceRegistryView,
        operation_id="addAccountDevice",
    )
    def add_account_device(
        account_id: AccountPath,
        request: SignedDeviceRegistryTransitionModel,
        authorization: AuthorizationHeader = None,
    ) -> DeviceRegistryView:
        try:
            return service.add_device(
                account_id,
                _bearer_token(authorization),
                request,
            )
        except AuthorizationFailed as exc:
            raise HTTPException(
                status_code=401, detail="Authorization failed."
            ) from exc
        except InvalidRegistryTransition as exc:
            raise HTTPException(
                status_code=400, detail="Invalid registry transition."
            ) from exc
        except RevisionConflict as exc:
            raise HTTPException(
                status_code=409, detail="Registry revision conflict."
            ) from exc

    return app


def _require_account_match(path_account_id: str, body_account_id: str) -> None:
    if not hmac.compare_digest(path_account_id, body_account_id):
        raise InvalidRegistryTransition


def _require_uuid(value: str) -> None:
    try:
        if str(uuid.UUID(value)) != value:
            raise InvalidRegistryTransition
    except (AttributeError, TypeError, ValueError) as exc:
        raise InvalidRegistryTransition from exc


def _verify_transition_signature(
    signed: SignedDeviceRegistryTransitionModel,
    signing_public_key: bytes,
) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(signing_public_key).verify(
            _decode_base64(signed.signature, SIGNATURE_BYTES),
            DEVICE_REGISTRY_TRANSITION_DOMAIN
            + _canonical_bytes(signed.transition.model_dump(mode="json")),
        )
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise InvalidRegistryTransition from exc


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _decode_base64(value: str, length: int) -> bytes:
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(encoded, validate=True)
    except (AttributeError, UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ValueError from exc
    if len(decoded) != length or not hmac.compare_digest(
        base64.b64encode(decoded),
        encoded,
    ):
        raise ValueError
    return decoded


def _encode_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _encode_token(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _token_digest(token: str) -> bytes:
    if not isinstance(token, str) or len(token) < 32 or not token.isascii():
        raise AuthorizationFailed
    return hashlib.sha256(token.encode("ascii")).digest()


def _entropy_bytes(entropy: Callable[[int], bytes], length: int) -> bytes:
    result = entropy(length)
    if not isinstance(result, bytes) or len(result) != length:
        raise RuntimeError("entropy source returned an invalid result")
    return result


def _bearer_token(authorization: str | None) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AuthorizationFailed
    token = authorization.removeprefix("Bearer ")
    if not token or any(character.isspace() for character in token):
        raise AuthorizationFailed
    return token
