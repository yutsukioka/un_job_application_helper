"""Private API bearer-token loading and verification."""

from __future__ import annotations

import os
import re
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

TOKEN_ENVIRONMENT = "ATLAS_PRIVATE_API_TOKEN"
TOKEN_FILE_ENVIRONMENT = "ATLAS_PRIVATE_API_TOKEN_FILE"
MINIMUM_TOKEN_BYTES = 32
MAXIMUM_TOKEN_FILE_BYTES = 4096

_TOKEN_PATTERN = re.compile(rb"[A-Za-z0-9._~+/-]+={0,2}\Z")
_AUTHORIZATION_PATTERN = re.compile(r"Bearer +([^\s]+)\Z", re.IGNORECASE | re.ASCII)
_CONFIGURATION_ERROR = "Invalid private API configuration."


@dataclass(frozen=True, slots=True)
class PrivateApiToken:
    """An opaque ASCII bearer token retained only in process memory."""

    _value: bytes = field(repr=False)

    @classmethod
    def parse(cls, value: str | bytes) -> PrivateApiToken:
        try:
            encoded = value.encode("ascii") if isinstance(value, str) else bytes(value)
        except UnicodeEncodeError:
            raise ValueError(_CONFIGURATION_ERROR) from None
        if (
            len(encoded) < MINIMUM_TOKEN_BYTES
            or len(encoded) > MAXIMUM_TOKEN_FILE_BYTES
            or not _TOKEN_PATTERN.fullmatch(encoded)
        ):
            raise ValueError(_CONFIGURATION_ERROR)
        return cls(encoded)

    def matches_authorization(self, header: str | None) -> bool:
        if header is None:
            return False
        match = _AUTHORIZATION_PATTERN.fullmatch(header)
        if match is None:
            return False
        candidate = match.group(1)
        try:
            candidate_bytes = candidate.encode("ascii")
        except UnicodeEncodeError:
            return False
        return secrets.compare_digest(candidate_bytes, self._value)


def load_private_api_token(
    environment: Mapping[str, str] | None = None,
) -> PrivateApiToken:
    environment = os.environ if environment is None else environment
    direct_value = environment.get(TOKEN_ENVIRONMENT)
    file_value = environment.get(TOKEN_FILE_ENVIRONMENT)
    if direct_value is not None and file_value is not None:
        raise ValueError(_CONFIGURATION_ERROR)
    if direct_value is not None:
        return PrivateApiToken.parse(direct_value)
    if file_value is not None:
        return PrivateApiToken.parse(_read_token_file(file_value))
    raise ValueError(_CONFIGURATION_ERROR)


def token_source_is_configured(environment: Mapping[str, str]) -> bool:
    return (
        environment.get(TOKEN_ENVIRONMENT) is not None
        or environment.get(TOKEN_FILE_ENVIRONMENT) is not None
    )


def _read_token_file(raw_path: str) -> bytes:
    if not raw_path or raw_path != raw_path.strip() or "\x00" in raw_path:
        raise ValueError(_CONFIGURATION_ERROR)
    path = Path(raw_path)
    try:
        path_stat = path.lstat()
        if not stat.S_ISREG(path_stat.st_mode):
            raise ValueError(_CONFIGURATION_ERROR)
        if os.name == "posix" and path_stat.st_mode & 0o077:
            raise ValueError(_CONFIGURATION_ERROR)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened_stat.st_mode):
                raise ValueError(_CONFIGURATION_ERROR)
            if os.name == "posix" and opened_stat.st_mode & 0o077:
                raise ValueError(_CONFIGURATION_ERROR)
            if (
                opened_stat.st_size <= 0
                or opened_stat.st_size > MAXIMUM_TOKEN_FILE_BYTES
            ):
                raise ValueError(_CONFIGURATION_ERROR)
            contents = handle.read(MAXIMUM_TOKEN_FILE_BYTES + 1)
    except (OSError, ValueError):
        raise ValueError(_CONFIGURATION_ERROR) from None
    if len(contents) != opened_stat.st_size:
        raise ValueError(_CONFIGURATION_ERROR)
    return contents
