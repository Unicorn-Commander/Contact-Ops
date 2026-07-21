"""Fail-closed connector payload encryption with an ordered Fernet key ring."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from contact_ops.core.config import Settings


class ConnectorKeyringError(RuntimeError):
    """Base error whose messages never contain keys, tokens, or plaintext."""


class ConnectorKeyringConfigurationError(ConnectorKeyringError):
    """Raised when connector encryption has no usable configured key."""


class ConnectorPayloadError(ConnectorKeyringError):
    """Raised when a stored connector payload cannot be authenticated and decoded."""


@dataclass(frozen=True)
class ConnectorCipher:
    """Encrypt with the first key and decrypt with every key in order.

    ``MultiFernet`` already defines the desired rotation semantics: encryption
    always uses the first Fernet instance, while decryption tries each instance
    in order. This wrapper additionally enforces that connector plaintext is a
    JSON object and converts every failure into a non-secret-bearing error.
    """

    _primary: Fernet
    _keyring: MultiFernet

    @classmethod
    def from_settings(cls, settings: Settings) -> ConnectorCipher:
        return cls.from_keys(settings.connector_encryption_keyring)

    @classmethod
    def from_keys(cls, keys: tuple[str, ...]) -> ConnectorCipher:
        if not keys:
            raise ConnectorKeyringConfigurationError(
                "connector encryption requires a configured key"
            )
        try:
            fernets = tuple(Fernet(key.encode("ascii")) for key in keys)
        except (UnicodeEncodeError, ValueError) as exc:
            raise ConnectorKeyringConfigurationError(
                "connector encryption key configuration is invalid"
            ) from exc
        return cls(_primary=fernets[0], _keyring=MultiFernet(list(fernets)))

    def encrypt_payload(self, payload: dict[str, Any]) -> bytes:
        try:
            encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            return self._keyring.encrypt(encoded)
        except (TypeError, ValueError) as exc:
            raise ConnectorPayloadError("connector payload could not be encoded") from exc

    def decrypt_payload(self, value: bytes) -> dict[str, Any]:
        return self._decode(self._decrypt(self._keyring, value))

    def decrypt_payload_with_primary(self, value: bytes) -> dict[str, Any]:
        """Decrypt using only the primary key (the post-rewrap verification gate)."""

        return self._decode(self._decrypt(self._primary, value))

    @staticmethod
    def _decrypt(cipher: Fernet | MultiFernet, value: bytes) -> bytes:
        try:
            return cipher.decrypt(bytes(value))
        except (InvalidToken, TypeError, ValueError) as exc:
            raise ConnectorPayloadError("connector payload could not be authenticated") from exc

    @staticmethod
    def _decode(value: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ConnectorPayloadError("connector payload could not be decoded") from exc
        if not isinstance(decoded, dict):
            raise ConnectorPayloadError("connector payload is not an object")
        return decoded
