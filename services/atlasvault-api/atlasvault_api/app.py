"""AtlasVault account, device-registry, and opaque-storage endpoints."""

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
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, NoReturn

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Security,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from vaultsync.device_identity import (
    DeviceIdentityError,
    SignedDeviceDescriptor,
    verify_signed_device_descriptor,
)

from atlasvault_api.controls import (
    AbuseControlPolicy,
    AccountDeviceRateLimiter,
    RequestRateExceeded,
    SecretFreeTelemetry,
    StoragePrincipal,
)
from atlasvault_api.storage import (
    IDEMPOTENCY_KEY_MAX_LENGTH,
    MAX_PAGE_SIZE,
    OPAQUE_ID_MAX_LENGTH,
    REVISION_MAX_LENGTH,
    EncryptedVaultMetadataEnvelopeModel,
    InMemoryOpaqueStore,
    InvalidOpaqueStorageRequest,
    OpaqueCiphertextEnvelopeModel,
    OpaqueCiphertextPageModel,
    OpaqueStorageConflict,
    OpaqueStorageNotFound,
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
MAX_KEY_EPOCH = (1 << 63) - 1
_STRICT_MODEL = ConfigDict(extra="forbid", frozen=True, strict=True)


class DeviceDescriptorModel(BaseModel):
    model_config = _STRICT_MODEL

    format: Literal["atlasvault-device-descriptor"]
    version: Literal[1]
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    signing_public_key: str
    agreement_public_key: str
    created_at: str
    key_epoch: int = Field(
        ge=1,
        le=MAX_KEY_EPOCH,
    )

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: Any,
        handler: Any,
    ) -> dict[str, Any]:
        schema = handler(core_schema)
        schema["properties"]["key_epoch"]["maximum"] = MAX_KEY_EPOCH
        return schema


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
    used_revisions: set[str] = field(default_factory=set)


class AtlasVaultBackend:
    """Ephemeral account, public-device, and opaque ciphertext state.

    Session tokens are random bearer credentials and only their SHA-256
    digests are retained. Storage envelopes remain opaque to this service.
    """

    def __init__(
        self,
        *,
        entropy: Callable[[int], bytes] = secrets.token_bytes,
        monotonic: Callable[[], float] = time.monotonic,
        abuse_policy: AbuseControlPolicy | None = None,
        telemetry: SecretFreeTelemetry | None = None,
    ) -> None:
        self._entropy = entropy
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._accounts: dict[str, _Account] = {}
        self._challenges: dict[tuple[str, str], _Challenge] = {}
        self._sessions: dict[bytes, _Session] = {}
        self.storage = InMemoryOpaqueStore(entropy=entropy, monotonic=monotonic)
        self.abuse_policy = abuse_policy or AbuseControlPolicy()
        self._storage_limiter = AccountDeviceRateLimiter(
            self.abuse_policy,
            monotonic=monotonic,
        )
        self.telemetry = telemetry or SecretFreeTelemetry()

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
                used_revisions={transition.revision},
            )
            return self._registry_view(account_id)

    def issue_challenge(
        self,
        account_id: str,
        request: AuthenticationChallengeRequest,
    ) -> AuthenticationChallenge:
        with self._lock:
            now = self._monotonic()
            self._prune_expired_challenges(now)
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
                expires_at=now + CHALLENGE_LIFETIME_SECONDS,
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
            now = self._monotonic()
            challenge = self._challenges.pop(
                (account_id, request.challenge_id),
                None,
            )
            account = self._accounts.get(account_id)
            if (
                challenge is None
                or account is None
                or not hmac.compare_digest(challenge.device_id, request.device_id)
                or challenge.expires_at <= now
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
            self._prune_expired_sessions(now)
            token = _encode_token(_entropy_bytes(self._entropy, 32))
            digest = _token_digest(token)
            if digest in self._sessions:
                raise AuthorizationFailed
            self._sessions[digest] = _Session(
                account_id=account_id,
                device_id=request.device_id,
                expires_at=now + SESSION_LIFETIME_SECONDS,
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
            if transition.revision in account.used_revisions:
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
            account.used_revisions.add(transition.revision)
            return self._registry_view(account_id)

    def authorize_account(self, token: str) -> str:
        with self._lock:
            return self._authorize_token(token).account_id

    def authorize_storage(self, token: str) -> StoragePrincipal:
        with self._lock:
            session = self._authorize_token(token)
            principal = StoragePrincipal(
                account_id=session.account_id,
                device_id=session.device_id,
            )
            self._storage_limiter.consume(principal)
            return principal

    def _authorize(self, account_id: str, token: str) -> _Session:
        session = self._authorize_token(token)
        if not hmac.compare_digest(session.account_id, account_id):
            raise AuthorizationFailed
        return session

    def _authorize_token(self, token: str) -> _Session:
        digest = _token_digest(token)
        session = self._sessions.get(digest)
        if session is None or session.expires_at <= self._monotonic():
            self._sessions.pop(digest, None)
            raise AuthorizationFailed
        account = self._accounts.get(session.account_id)
        if account is None or session.device_id not in account.devices:
            raise AuthorizationFailed
        return session

    def _prune_expired_sessions(self, now: float) -> None:
        expired = [
            digest
            for digest, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for digest in expired:
            del self._sessions[digest]

    def _prune_expired_challenges(self, now: float) -> None:
        expired = [
            key
            for key, challenge in self._challenges.items()
            if challenge.expires_at <= now
        ]
        for key in expired:
            del self._challenges[key]

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
_BEARER_SECURITY = HTTPBearer(auto_error=False, scheme_name="bearerAuth")
BearerAuthorization = Annotated[
    HTTPAuthorizationCredentials | None,
    Security(_BEARER_SECURITY),
]
VaultPath = Annotated[
    str,
    Path(min_length=1, max_length=OPAQUE_ID_MAX_LENGTH),
]
ObjectPath = Annotated[
    str,
    Path(min_length=1, max_length=OPAQUE_ID_MAX_LENGTH),
]
IfMatchHeader = Annotated[
    str,
    Header(alias="If-Match", min_length=1, max_length=REVISION_MAX_LENGTH),
]
IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=IDEMPOTENCY_KEY_MAX_LENGTH,
    ),
]
CursorQuery = Annotated[
    str | None,
    Query(min_length=1, max_length=OPAQUE_ID_MAX_LENGTH),
]
PageSizeQuery = Annotated[int | None, Query(ge=1, le=MAX_PAGE_SIZE)]


class _RequestBodyTooLarge(ValueError):
    """Raised before an oversized body reaches a request model or handler."""


class _SecurityBoundaryMiddleware:
    def __init__(self, app: ASGIApp, *, backend: AtlasVaultBackend) -> None:
        self._app = app
        self._backend = backend

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        category = _request_category(scope)
        response_started = False
        response_status = 500

        async def observed_send(message: Message) -> None:
            nonlocal response_started, response_status
            if message["type"] == "http.response.start":
                response_started = True
                response_status = int(message["status"])
            await send(message)

        if category == "storage":
            try:
                authorization = _scope_header(scope, b"authorization")
                self._backend.authorize_storage(_bearer_token(authorization))
            except AuthorizationFailed:
                await _send_fixed_error(
                    observed_send,
                    401,
                    "Authorization failed.",
                )
                self._backend.telemetry.record(category, 401)
                return
            except RequestRateExceeded:
                await _send_fixed_error(
                    observed_send,
                    429,
                    "Request rate exceeded.",
                )
                self._backend.telemetry.record(category, 429)
                return

        try:
            declared_length = _declared_content_length(scope)
            if declared_length is not None and (
                declared_length > self._backend.abuse_policy.max_request_bytes
            ):
                raise _RequestBodyTooLarge
            received_bytes = 0

            async def limited_receive() -> Message:
                nonlocal received_bytes
                message = await receive()
                if message["type"] == "http.request":
                    received_bytes += len(message.get("body", b""))
                    if received_bytes > self._backend.abuse_policy.max_request_bytes:
                        raise _RequestBodyTooLarge
                return message

            await self._app(scope, limited_receive, observed_send)
        except _RequestBodyTooLarge:
            if response_started:
                raise
            await _send_fixed_error(
                observed_send,
                413,
                "Request body too large.",
            )
        except Exception:
            self._backend.telemetry.record(category, 500)
            raise
        self._backend.telemetry.record(category, response_status)


def create_app(backend: AtlasVaultBackend | None = None) -> FastAPI:
    service = backend or AtlasVaultBackend()
    app = FastAPI(
        title="AtlasVault Zero-Knowledge Sync API",
        version="1.2.0",
        description=(
            "Account authentication, signed public-device registry, opaque "
            "ciphertext storage, and C15 abuse and observability controls."
        ),
    )
    app.state.backend = service
    app.add_middleware(_SecurityBoundaryMiddleware, backend=service)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid request."},
        )

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
        authorization: BearerAuthorization = None,
    ) -> DeviceRegistryView:
        try:
            return service.list_devices(account_id, _credential_token(authorization))
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
        authorization: BearerAuthorization = None,
    ) -> DeviceRegistryView:
        try:
            return service.add_device(
                account_id,
                _credential_token(authorization),
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

    @app.put(
        "/v1/vaults/{vault_id}/metadata",
        response_model=EncryptedVaultMetadataEnvelopeModel,
        operation_id="putEncryptedVaultMetadata",
    )
    def put_encrypted_vault_metadata(
        vault_id: VaultPath,
        request: EncryptedVaultMetadataEnvelopeModel,
        if_match: IfMatchHeader,
        idempotency_key: IdempotencyKeyHeader,
        authorization: BearerAuthorization = None,
    ) -> EncryptedVaultMetadataEnvelopeModel:
        account_id = _authorized_storage_account(service, authorization)
        try:
            return service.storage.put_metadata(
                account_id,
                vault_id,
                request,
                expected_revision=if_match,
                idempotency_key=idempotency_key,
            )
        except _STORAGE_ERRORS as exc:
            _raise_storage_http_error(exc)

    @app.get(
        "/v1/vaults/{vault_id}/metadata",
        response_model=EncryptedVaultMetadataEnvelopeModel,
        operation_id="getEncryptedVaultMetadata",
    )
    def get_encrypted_vault_metadata(
        vault_id: VaultPath,
        authorization: BearerAuthorization = None,
    ) -> EncryptedVaultMetadataEnvelopeModel:
        account_id = _authorized_storage_account(service, authorization)
        try:
            return service.storage.get_metadata(account_id, vault_id)
        except _STORAGE_ERRORS as exc:
            _raise_storage_http_error(exc)

    @app.put(
        "/v1/vaults/{vault_id}/objects/{object_id}",
        response_model=OpaqueCiphertextEnvelopeModel,
        operation_id="putOpaqueCiphertextObject",
    )
    def put_opaque_ciphertext_object(
        vault_id: VaultPath,
        object_id: ObjectPath,
        request: OpaqueCiphertextEnvelopeModel,
        if_match: IfMatchHeader,
        idempotency_key: IdempotencyKeyHeader,
        authorization: BearerAuthorization = None,
    ) -> OpaqueCiphertextEnvelopeModel:
        account_id = _authorized_storage_account(service, authorization)
        try:
            return service.storage.put_object(
                account_id,
                vault_id,
                object_id,
                request,
                expected_revision=if_match,
                idempotency_key=idempotency_key,
            )
        except _STORAGE_ERRORS as exc:
            _raise_storage_http_error(exc)

    @app.get(
        "/v1/vaults/{vault_id}/objects/{object_id}",
        response_model=OpaqueCiphertextEnvelopeModel,
        operation_id="getOpaqueCiphertextObject",
    )
    def get_opaque_ciphertext_object(
        vault_id: VaultPath,
        object_id: ObjectPath,
        authorization: BearerAuthorization = None,
    ) -> OpaqueCiphertextEnvelopeModel:
        account_id = _authorized_storage_account(service, authorization)
        try:
            return service.storage.get_object(account_id, vault_id, object_id)
        except _STORAGE_ERRORS as exc:
            _raise_storage_http_error(exc)

    @app.post(
        "/v1/vaults/{vault_id}/patches",
        response_model=OpaqueCiphertextEnvelopeModel,
        status_code=status.HTTP_201_CREATED,
        operation_id="appendEncryptedPatch",
    )
    def append_encrypted_patch(
        vault_id: VaultPath,
        request: OpaqueCiphertextEnvelopeModel,
        if_match: IfMatchHeader,
        idempotency_key: IdempotencyKeyHeader,
        authorization: BearerAuthorization = None,
    ) -> OpaqueCiphertextEnvelopeModel:
        account_id = _authorized_storage_account(service, authorization)
        try:
            return service.storage.append_patch(
                account_id,
                vault_id,
                request,
                expected_revision=if_match,
                idempotency_key=idempotency_key,
            )
        except _STORAGE_ERRORS as exc:
            _raise_storage_http_error(exc)

    @app.get(
        "/v1/vaults/{vault_id}/patches",
        response_model=OpaqueCiphertextPageModel,
        operation_id="listEncryptedPatches",
    )
    def list_encrypted_patches(
        vault_id: VaultPath,
        authorization: BearerAuthorization = None,
        cursor: CursorQuery = None,
        page_size: PageSizeQuery = None,
    ) -> OpaqueCiphertextPageModel:
        account_id = _authorized_storage_account(service, authorization)
        try:
            return service.storage.list_patches(
                account_id,
                vault_id,
                cursor=cursor,
                page_size=page_size,
            )
        except _STORAGE_ERRORS as exc:
            _raise_storage_http_error(exc)

    @app.put(
        "/v1/vaults/{vault_id}/snapshots",
        response_model=OpaqueCiphertextEnvelopeModel,
        operation_id="putEncryptedSnapshot",
    )
    def put_encrypted_snapshot(
        vault_id: VaultPath,
        request: OpaqueCiphertextEnvelopeModel,
        if_match: IfMatchHeader,
        idempotency_key: IdempotencyKeyHeader,
        authorization: BearerAuthorization = None,
    ) -> OpaqueCiphertextEnvelopeModel:
        account_id = _authorized_storage_account(service, authorization)
        try:
            return service.storage.put_snapshot(
                account_id,
                vault_id,
                request,
                expected_revision=if_match,
                idempotency_key=idempotency_key,
            )
        except _STORAGE_ERRORS as exc:
            _raise_storage_http_error(exc)

    @app.get(
        "/v1/vaults/{vault_id}/snapshots",
        response_model=OpaqueCiphertextEnvelopeModel,
        operation_id="getEncryptedSnapshot",
    )
    def get_encrypted_snapshot(
        vault_id: VaultPath,
        authorization: BearerAuthorization = None,
    ) -> OpaqueCiphertextEnvelopeModel:
        account_id = _authorized_storage_account(service, authorization)
        try:
            return service.storage.get_snapshot(account_id, vault_id)
        except _STORAGE_ERRORS as exc:
            _raise_storage_http_error(exc)

    served_schema = app.openapi()
    served_schema["components"]["schemas"]["DeviceDescriptorModel"]["properties"][
        "key_epoch"
    ]["maximum"] = MAX_KEY_EPOCH
    app.openapi_schema = served_schema
    return app


_STORAGE_ERRORS = (
    InvalidOpaqueStorageRequest,
    OpaqueStorageConflict,
    OpaqueStorageNotFound,
)


def _authorized_storage_account(
    backend: AtlasVaultBackend,
    authorization: HTTPAuthorizationCredentials | None,
) -> str:
    try:
        return backend.authorize_account(_credential_token(authorization))
    except AuthorizationFailed as exc:
        raise HTTPException(
            status_code=401,
            detail="Authorization failed.",
        ) from exc


def _request_category(scope: Scope) -> str:
    path = str(scope.get("path", ""))
    if path.startswith("/v1/vaults/"):
        return "storage"
    if path.startswith("/v1/accounts/"):
        return "account"
    return "other"


def _scope_header(scope: Scope, name: bytes) -> str | None:
    values = [
        value
        for header_name, value in scope.get("headers", ())
        if header_name.lower() == name
    ]
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _declared_content_length(scope: Scope) -> int | None:
    values = [
        value
        for header_name, value in scope.get("headers", ())
        if header_name.lower() == b"content-length"
    ]
    if not values:
        return None
    if len(values) != 1:
        raise _RequestBodyTooLarge
    try:
        length = int(values[0].decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _RequestBodyTooLarge from exc
    if length < 0:
        raise _RequestBodyTooLarge
    return length


async def _send_fixed_error(
    send: Send,
    status_code: int,
    detail: str,
) -> None:
    body = json.dumps(
        {"detail": detail},
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-length", str(len(body)).encode("ascii")),
                (b"content-type", b"application/json"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _raise_storage_http_error(error: ValueError) -> NoReturn:
    if isinstance(error, OpaqueStorageNotFound):
        raise HTTPException(status_code=404, detail="Opaque resource not found.")
    if isinstance(error, OpaqueStorageConflict):
        raise HTTPException(status_code=409, detail="Opaque revision conflict.")
    raise HTTPException(status_code=400, detail="Invalid opaque storage request.")


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
    if authorization is None:
        raise AuthorizationFailed
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "bearer":
        raise AuthorizationFailed
    if not token or any(character.isspace() for character in token):
        raise AuthorizationFailed
    return token


def _credential_token(
    authorization: HTTPAuthorizationCredentials | None,
) -> str:
    if authorization is None:
        raise AuthorizationFailed
    return _bearer_token(f"{authorization.scheme} {authorization.credentials}")
